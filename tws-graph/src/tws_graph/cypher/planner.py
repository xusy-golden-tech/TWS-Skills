"""AST to LogicalPlan conversion — Planner and logical operators.

P2 scope: Converts a parsed Cypher AST (Statement/Query) into a LogicalPlan
composed of logical operators (Scan, Filter, EdgeExpand, Project, Sort,
Limit, Distinct). No query optimization in P2 — naive translation only.

Design reference: design-cypher-engine.md sections 6-7.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from tws_graph.cypher.ast import (
    Statement,
    Query,
    MatchClause,
    PatternPart,
    PatternElement,
    NodePattern,
    RelPattern,
    Direction,
    Expression,
    ReturnItem,
    OrderByItem,
    Identifier,
    PropertyAccess,
    BinaryOp,
    UnaryOp,
    FunctionCall,
    InExpression,
    ListLiteral,
    StarExpression,
    Literal,
    Parameter,
    Span,
)
from tws_graph.cypher.errors import CypherSemanticError


# ============================================================================
# Logical Operators
# ============================================================================


@dataclass(frozen=True)
class LogicalPlan:
    """Logical plan: a directed acyclic graph (DAG) of operator nodes.

    The Executor walks from the root down through ``source`` references
    to build a bottom-up execution pipeline.
    """

    root: LogicalOperator


class LogicalOperator(ABC):
    """Abstract base class for all logical operators."""

    @abstractmethod
    def __init__(self) -> None:
        ...


@dataclass(frozen=True)
class ScanOperator(LogicalOperator):
    """Full scan: iterate over all nodes.

    Attributes:
        label: Optional node-label filter (e.g. ``MATCH (n:Class)`` → ``"Class"``).
        variable: The variable name bound to each scanned node.
    """

    label: Optional[str] = None
    variable: str = ""


@dataclass(frozen=True)
class FilterOperator(LogicalOperator):
    """Filter: apply a WHERE-condition predicate.

    Attributes:
        source: Upstream operator whose rows are filtered.
        predicate: AST expression from the WHERE clause.
    """

    source: LogicalOperator
    predicate: Expression


@dataclass(frozen=True)
class EdgeExpandOperator(LogicalOperator):
    """Edge expansion: traverse along edges to neighbouring nodes.

    Attributes:
        source: Upstream operator providing source-node rows.
        edge_types: Edge-type filter (e.g. ``["calls", "imports"]``).
        direction: ``"out"``, ``"in"``, or ``"both"``.
        edge_variable: Optional variable bound to the traversed edge.
        target_variable: Variable bound to the target node.
        optional: If True, use LEFT OUTER JOIN semantics (OPTIONAL MATCH).
    """

    source: LogicalOperator
    edge_types: list[str] = field(default_factory=list)
    direction: str = "out"
    edge_variable: Optional[str] = None
    target_variable: str = ""
    optional: bool = False


@dataclass(frozen=True)
class ProjectOperator(LogicalOperator):
    """Projection: select which fields / expressions to return.

    Attributes:
        source: Upstream operator whose columns are projected.
        items: RETURN-clause items (expressions with optional aliases).
    """

    source: LogicalOperator
    items: list[ReturnItem] = field(default_factory=list)


@dataclass(frozen=True)
class SortOperator(LogicalOperator):
    """Sort: order rows by a list of expressions.

    Attributes:
        source: Upstream operator whose rows are sorted.
        items: ORDER BY items (expressions with ASC / DESC).
    """

    source: LogicalOperator
    items: list[OrderByItem] = field(default_factory=list)


@dataclass(frozen=True)
class LimitOperator(LogicalOperator):
    """Row limiting: skip the first N rows and / or return at most M rows.

    Attributes:
        source: Upstream operator whose row stream is truncated.
        skip: Number of rows to skip (0 = no skip).
        limit: Maximum number of rows to return (None = no limit).
    """

    source: LogicalOperator
    skip: int = 0
    limit: Optional[int] = None


@dataclass(frozen=True)
class DistinctOperator(LogicalOperator):
    """Deduplication: remove duplicate rows.

    Attributes:
        source: Upstream operator whose rows are deduplicated.
    """

    source: LogicalOperator


@dataclass(frozen=True)
class AggregateOperator(LogicalOperator):
    """Aggregation: GROUP BY + aggregate functions.

    Attributes:
        source: Upstream operator whose rows are aggregated.
        group_by: GROUP BY field names (variable/property paths).
                  An empty list means aggregate all rows into one group.
        aggregates: List of ``(func_name, param_str, alias)`` tuples.
                    func_name e.g. "COUNT", "SUM"; param_str e.g.
                    ``"*"`` or ``"n.start_line"``; alias e.g. "cnt".
    """

    source: LogicalOperator
    group_by: list[str] = field(default_factory=list)
    aggregates: list[tuple[str, str, str]] = field(default_factory=list)


# ============================================================================
# Planner
# ============================================================================


class Planner:
    """Convert an AST Statement into an executable LogicalPlan.

    P2 scope — naive translation without query optimisation::

        planner = Planner()
        plan   = planner.plan(parsed_statement)
        # plan.root is the outermost logical operator

    Semantic checks:
    * Query must have a MATCH clause.
    * Return-clause must have at least one item.
    * Every variable referenced in RETURN / WHERE must be defined in MATCH.
    """

    def plan(self, statement: Statement) -> LogicalPlan:
        """Build a LogicalPlan from *statement*.

        Returns:
            LogicalPlan whose ``root`` is the outermost operator.
        Raises:
            CypherSemanticError: If the query is semantically invalid.
        """
        from tws_graph.cypher.aggregates import list_aggregates

        query = statement.query

        # ── Guard clauses ───────────────────────────────────────────
        if query.match is None:
            raise CypherSemanticError(
                "Query must have a MATCH clause"
            )

        if not query.return_clause.items:
            raise CypherSemanticError(
                "Query must have at least one RETURN item"
            )

        # ── Collect defined variables ───────────────────────────────
        defined_vars = self._collect_match_variables(query.match)
        for om in query.optional_matches:
            defined_vars |= self._collect_match_variables(om)

        # ── Semantic checks: variable references ────────────────────
        if query.where is not None:
            self._check_variables(query.where.expression, defined_vars)

        for item in query.return_clause.items:
            self._check_variables(item.expression, defined_vars)

        # ── Build operator chain bottom-up ──────────────────────────
        root: LogicalOperator = self._build_match_scan(query.match)

        # WHERE → FilterOperator
        if query.where is not None:
            root = FilterOperator(source=root, predicate=query.where.expression)

        # OPTIONAL MATCH → EdgeExpandOperator with optional=True
        for om in query.optional_matches:
            root = self._build_match_scan_optional(om, root)

        # ── Aggregate detection ─────────────────────────────────────
        agg_names = set(list_aggregates())
        has_aggregate, agg_items, non_agg_return_items = self._classify_return_items(
            query.return_clause.items, agg_names
        )

        if has_aggregate:
            # Extract group_by from non-aggregate RETURN items.
            # Use the full expression path (e.g. "n.kind") not just
            # the root identifier, so the executor can resolve it
            # correctly against rows.
            group_by: list[str] = []
            for ri in non_agg_return_items:
                gb_path = self._expr_to_group_path(ri.expression)
                if gb_path and gb_path not in group_by:
                    group_by.append(gb_path)

            # Build aggregate tuples
            agg_tuples: list[tuple[str, str, str]] = []
            for ri in agg_items:
                fc = ri.expression
                alias = ri.alias if ri.alias else self._expr_name_str(fc)
                param_str = self._agg_param_str(fc)
                agg_tuples.append((fc.name, param_str, alias))

            root = AggregateOperator(
                source=root,
                group_by=group_by,
                aggregates=agg_tuples,
            )

            # Build ProjectOperator with aggregate result references
            proj_items: list[ReturnItem] = []
            for gb in group_by:
                proj_items.append(ReturnItem(
                    expression=Identifier(name=gb, span=Span(0, 0, 0, 0)),
                    alias=gb,
                ))
            for _fn, _ps, alias in agg_tuples:
                proj_items.append(ReturnItem(
                    expression=Identifier(name=alias, span=Span(0, 0, 0, 0)),
                    alias=alias,
                ))
            root = ProjectOperator(source=root, items=proj_items)
        else:
            # RETURN DISTINCT → DistinctOperator (before projection)
            if query.return_clause.distinct:
                root = DistinctOperator(source=root)

            # RETURN → ProjectOperator
            root = ProjectOperator(
                source=root,
                items=list(query.return_clause.items),
            )

        # ORDER BY → SortOperator
        if query.order_by is not None:
            root = SortOperator(source=root, items=list(query.order_by.items))

        # SKIP / LIMIT → LimitOperator
        if query.skip is not None or query.limit is not None:
            root = LimitOperator(
                source=root,
                skip=query.skip if query.skip is not None else 0,
                limit=query.limit,
            )

        return LogicalPlan(root=root)

    # -----------------------------------------------------------------
    # MATCH → Scan / EdgeExpand chain
    # -----------------------------------------------------------------

    def _build_match_scan(self, match: MatchClause) -> LogicalOperator:
        """Build a Scan → (EdgeExpand → EdgeExpand → ...) chain for *match*."""
        pattern = match.pattern
        start = pattern.node

        # Entry node → ScanOperator
        label = start.labels[0] if start.labels else None
        root: LogicalOperator = ScanOperator(
            variable=start.name or "",
            label=label,
        )

        # Chained pattern elements → EdgeExpandOperator chain
        for elem in pattern.chain:
            root = self._build_edge_expand(root, elem)

        return root

    def _build_edge_expand(
        self,
        source: LogicalOperator,
        elem: PatternElement,
    ) -> EdgeExpandOperator:
        """Wrap *source* with an EdgeExpandOperator for *elem*."""
        rel = elem.rel

        if rel is not None:
            direction = {
                Direction.RIGHT: "out",
                Direction.LEFT: "in",
                Direction.BOTH: "both",
            }.get(rel.direction, "out")

            return EdgeExpandOperator(
                source=source,
                edge_types=list(rel.types) if rel.types else [],
                direction=direction,
                edge_variable=rel.name,
                target_variable=elem.node.name or "",
            )
        else:
            # No relationship pattern — treat target node as a standalone scan
            # (should not normally happen in well-formed MATCH patterns)
            target_label = (
                elem.node.labels[0] if elem.node.labels else None
            )
            return ScanOperator(
                variable=elem.node.name or "",
                label=target_label,
            )

    # -----------------------------------------------------------------
    # Variable collection
    # -----------------------------------------------------------------

    def _collect_match_variables(self, match: MatchClause) -> set[str]:
        """Return the set of variable names defined in *match*'s pattern."""
        vars_: set[str] = set()
        pattern = match.pattern

        if pattern.node.name:
            vars_.add(pattern.node.name)

        for elem in pattern.chain:
            if elem.node.name:
                vars_.add(elem.node.name)
            if elem.rel is not None and elem.rel.name:
                vars_.add(elem.rel.name)

        return vars_

    # -----------------------------------------------------------------
    # Variable-use checking
    # -----------------------------------------------------------------

    def _check_variables(
        self,
        expr: Expression,
        defined_vars: set[str],
    ) -> None:
        """Raise CypherSemanticError if *expr* references an undefined variable."""
        used = self._collect_identifiers(expr)
        undefined = used - defined_vars
        if undefined:
            name = next(iter(undefined))
            raise CypherSemanticError(
                f"Variable '{name}' is not defined in MATCH clause"
            )

    def _collect_identifiers(self, expr: Expression) -> set[str]:
        """Recursively collect all identifier names referenced in *expr*."""
        if isinstance(expr, Identifier):
            return {expr.name} if expr.name else set()
        elif isinstance(expr, PropertyAccess):
            return self._collect_identifiers(expr.obj)
        elif isinstance(expr, BinaryOp):
            return self._collect_identifiers(expr.left) | self._collect_identifiers(expr.right)
        elif isinstance(expr, UnaryOp):
            return self._collect_identifiers(expr.operand)
        elif isinstance(expr, FunctionCall):
            result: set[str] = set()
            for arg in expr.args:
                result |= self._collect_identifiers(arg)
            return result
        elif isinstance(expr, InExpression):
            return self._collect_identifiers(expr.expr) | self._collect_identifiers(expr.list)
        elif isinstance(expr, ListLiteral):
            result = set()
            for elem in expr.elements:
                result |= self._collect_identifiers(elem)
            return result
        elif isinstance(expr, (StarExpression, Literal, Parameter)):
            return set()
        return set()

    # -----------------------------------------------------------------
    # OPTIONAL MATCH helper
    # -----------------------------------------------------------------

    def _build_match_scan_optional(
        self,
        match: MatchClause,
        source: LogicalOperator,
    ) -> LogicalOperator:
        """Build an EdgeExpandOperator chain for *match*, wrapping *source*.

        Each EdgeExpandOperator is marked ``optional=True`` to produce
        LEFT OUTER JOIN semantics.
        """
        # Build the entry ScanOperator for the optional pattern's start node
        pattern = match.pattern
        start = pattern.node
        label = start.labels[0] if start.labels else None

        # For OPTIONAL MATCH, scan the start node
        opt_root: LogicalOperator = ScanOperator(
            variable=start.name or "",
            label=label,
        )

        # Chain through pattern elements — each marked optional
        for elem in pattern.chain:
            expand = self._build_edge_expand(opt_root, elem)
            # Mark as optional
            opt_root = EdgeExpandOperator(
                source=expand.source,
                edge_types=list(expand.edge_types),
                direction=expand.direction,
                edge_variable=expand.edge_variable,
                target_variable=expand.target_variable,
                optional=True,
            )

        # We cannot easily express a cross-join with optional semantics
        # in the current operator model. For now, produce the OPTIONAL
        # MATCH chain independently, linked to the source via the optional
        # flag on EdgeExpandOperator built from the source.

        # Build EdgeExpandOperator from *source* for each pattern element
        for elem in pattern.chain:
            source = EdgeExpandOperator(
                source=source,
                edge_types=list(elem.rel.types) if elem.rel and elem.rel.types else [],
                direction={
                    Direction.RIGHT: "out",
                    Direction.LEFT: "in",
                    Direction.BOTH: "both",
                }.get(elem.rel.direction if elem.rel else Direction.RIGHT, "out"),
                edge_variable=elem.rel.name if elem.rel else None,
                target_variable=elem.node.name or "",
                optional=True,
            )

        return source

    # -----------------------------------------------------------------
    # Aggregate detection helpers
    # -----------------------------------------------------------------

    def _classify_return_items(
        self,
        items: list[ReturnItem],
        agg_names: set[str],
    ) -> tuple:
        """Separate RETURN items into aggregate and non-aggregate.

        Returns:
            (has_aggregate: bool, agg_items: list[ReturnItem], non_agg: list[ReturnItem])
        """
        agg_items: list[ReturnItem] = []
        non_agg: list[ReturnItem] = []
        for item in items:
            if self._is_aggregate_expr(item.expression, agg_names):
                agg_items.append(item)
            else:
                non_agg.append(item)
        return bool(agg_items), agg_items, non_agg

    def _is_aggregate_expr(self, expr: Expression, agg_names: set[str]) -> bool:
        """Return True if *expr* is (or contains) an aggregate FunctionCall."""
        if isinstance(expr, FunctionCall):
            return expr.name.upper() in agg_names
        return False

    def _agg_param_str(self, fc: FunctionCall) -> str:
        """Convert a FunctionCall's first argument to its string representation.

        Examples:
            COUNT(*) → "*"
            SUM(n.start_line) → "n.start_line"
            MIN(n.name) → "n.name"
        """
        if not fc.args:
            return "*"
        arg = fc.args[0]
        if isinstance(arg, StarExpression):
            return "*"
        if isinstance(arg, PropertyAccess):
            return f"{self._expr_name_str(arg.obj)}.{arg.key}"
        if isinstance(arg, Identifier):
            return arg.name
        if isinstance(arg, Literal):
            return str(arg.value)
        return "*"

    def _expr_name_str(self, expr: Expression) -> str:
        """Derive a string name from an expression (for alias generation)."""
        if isinstance(expr, Identifier):
            return expr.name
        elif isinstance(expr, PropertyAccess):
            return f"{self._expr_name_str(expr.obj)}.{expr.key}"
        elif isinstance(expr, Literal):
            return str(expr.value)
        elif isinstance(expr, FunctionCall):
            args_str = ", ".join(
                self._expr_name_str(a) for a in expr.args
            ) if expr.args else "*"
            return f"{expr.name}({args_str})"
        elif isinstance(expr, BinaryOp):
            return "expr"
        elif isinstance(expr, UnaryOp):
            return "expr"
        return "col"

    def _expr_to_group_path(self, expr: Expression) -> str:
        """Convert an expression to a resolvable property path for GROUP BY.

        Examples:
            Identifier("n") → "n"
            PropertyAccess(obj=Identifier("n"), key="kind") → "n.kind"
        """
        if isinstance(expr, Identifier):
            return expr.name
        elif isinstance(expr, PropertyAccess):
            return f"{self._expr_to_group_path(expr.obj)}.{expr.key}"
        # For anything else, derive a name
        return self._expr_name_str(expr)
