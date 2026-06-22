"""LogicalPlan to ResultSet executor.

Walks a LogicalPlan tree bottom-up, executing each logical operator
against the Store interface. Includes a built-in expression evaluator
for Filter, Project, and Sort operations.

P2 scope: all seven operators (Scan, Filter, EdgeExpand, Project,
Sort, Limit, Distinct) plus a full expression evaluator supporting
binary/unary operators, built-in function calls, and property access.

Design reference: design-cypher-engine.md sections 6-7.
"""

from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass, field
from typing import Optional

from tws_graph.cypher.aggregates import get_aggregate
from tws_graph.cypher.ast import (
    BinaryOp,
    FunctionCall,
    Identifier,
    InExpression,
    ListLiteral,
    Literal,
    Parameter,
    PropertyAccess,
    StarExpression,
    UnaryOp,
    CaseExpression,
    Expression,
    ReturnItem,
)
from tws_graph.cypher.errors import CypherExecutionError
from tws_graph.cypher.functions import get_function
from tws_graph.cypher.planner import (
    LogicalPlan,
    LogicalOperator,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    SortOperator,
    LimitOperator,
    DistinctOperator,
    AggregateOperator,
    UnionOperator,
    UnwindOperator,
    SubqueryFilterOperator,
)
from tws_graph.store.interface import Store


# ============================================================================
# Data classes
# ============================================================================


@dataclass
class Row:
    """A single row in the result set.

    Maps variable names to their bound values (node dicts, scalars, etc.).
    Supports dict-style access via ``row["key"]`` and ``row.get("key", default)``.
    """

    data: dict[str, object] = field(default_factory=dict)

    def __getitem__(self, key: str) -> object:
        return self.data[key]

    def __setitem__(self, key: str, value: object) -> None:
        self.data[key] = value

    def get(self, key: str, default: object = None) -> object:
        """Return the value for *key*, or *default* if the key is missing."""
        return self.data.get(key, default)


@dataclass
class ResultSet:
    """A collection of result rows with column metadata.

    Attributes:
        columns: Ordered list of column names.
        rows: List of Row objects.
        total_count: Total number of rows.
    """

    columns: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    total_count: int = 0

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, idx: int) -> Row:
        return self.rows[idx]


# ============================================================================
# Executor
# ============================================================================


class Executor:
    """Execute a LogicalPlan against a Store, producing a ResultSet.

    Walks the operator tree bottom-up: recursion descends from root to
    leaf, and each operator materialises its upstream rows before
    transforming them.

    Usage::

        executor = Executor(store)
        result = executor.execute(logical_plan)
        for row in result:
            print(row["n"])
    """

    def __init__(self, store: Store) -> None:
        """Initialise the executor with a Store instance.

        The Store is used through its public interface only — no backend-
        specific coupling.
        """
        self._store = store
        self._result_columns: list[str] = []
        self._correlation_row: Optional[Row] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, plan: LogicalPlan) -> ResultSet:
        """Execute *plan* and return the result set.

        The column names are derived from the outermost
        ProjectOperator (or the Scan variable if no projection is present).
        """
        self._result_columns = []
        rows = self._execute_operator(plan.root)
        return ResultSet(
            columns=list(self._result_columns),
            rows=rows,
            total_count=len(rows),
        )

    # ------------------------------------------------------------------
    # Operator dispatch
    # ------------------------------------------------------------------

    def _execute_operator(self, op: LogicalOperator) -> list[Row]:
        """Dispatch *op* to the correct execution method."""
        if isinstance(op, ScanOperator):
            return self._execute_scan(op)
        elif isinstance(op, FilterOperator):
            return self._execute_filter(op)
        elif isinstance(op, EdgeExpandOperator):
            return self._execute_edge_expand(op)
        elif isinstance(op, ProjectOperator):
            return self._execute_project(op)
        elif isinstance(op, SortOperator):
            return self._execute_sort(op)
        elif isinstance(op, LimitOperator):
            return self._execute_limit(op)
        elif isinstance(op, DistinctOperator):
            return self._execute_distinct(op)
        elif isinstance(op, AggregateOperator):
            return self._execute_aggregate(op)
        elif isinstance(op, UnionOperator):
            return self._execute_union(op)
        elif isinstance(op, UnwindOperator):
            return self._execute_unwind(op)
        elif isinstance(op, SubqueryFilterOperator):
            return self._execute_subquery_filter(op)
        else:
            raise CypherExecutionError(
                f"Unknown operator type: {type(op).__name__}"
            )

    # ------------------------------------------------------------------
    # ScanOperator
    # ------------------------------------------------------------------

    def _execute_scan(self, op: ScanOperator) -> list[Row]:
        """Iterate all nodes, optionally filtering by label (kind).

        Virtual scan (variable="" and label=None) produces a single
        empty row — used as the source for UNWIND or WITH without MATCH.

        Correlated scan: if a correlation row is set and the scan variable
        matches, use the correlation row's value instead of scanning.
        """
        # Virtual scan for UNWIND/WITH without MATCH
        if not op.variable and op.label is None:
            return [Row()]

        # Correlated scan: use outer row's value for the variable
        if (
            self._correlation_row is not None
            and op.variable
            and op.variable in self._correlation_row.data
        ):
            val = self._correlation_row[op.variable]
            if isinstance(val, dict) and "id" in val:
                # Filter by label if needed
                if op.label and val.get("kind") != op.label:
                    return []
                return [Row(data={op.variable: val})]

        rows: list[Row] = []
        for node in self._store.iter_all_nodes():
            if op.label and node.get("kind") != op.label:
                continue
            rows.append(Row(data={op.variable: dict(node)}))
        if op.variable:
            self._result_columns = [op.variable]
        else:
            self._result_columns = []
        return rows

    # ------------------------------------------------------------------
    # FilterOperator
    # ------------------------------------------------------------------

    def _execute_filter(self, op: FilterOperator) -> list[Row]:
        """Evaluate the predicate for each upstream row; keep truthy ones."""
        source_rows = self._execute_operator(op.source)
        result: list[Row] = []
        for row in source_rows:
            if self._evaluate(op.predicate, row):
                result.append(row)
        return result

    # ------------------------------------------------------------------
    # EdgeExpandOperator
    # ------------------------------------------------------------------

    def _execute_edge_expand(self, op: EdgeExpandOperator) -> list[Row]:
        """Expand from each source row along matching edges.

        For each node dict in the source row, query the Store for adjacent
        edges (filtered by type and direction).  Each (source_row, edge)
        pair produces one output Row containing the original data plus the
        edge and target node.

        If *op.optional* is True (OPTIONAL MATCH semantics), a source row
        with no matching edges still produces one output Row with the target
        and edge variables set to None (LEFT OUTER JOIN).
        """
        source_rows = self._execute_operator(op.source)
        result: list[Row] = []
        kinds = op.edge_types if op.edge_types else None

        for row in source_rows:
            # Find node variables in the row (dicts with an "id" key).
            found_any = False
            for _var_name, var_value in row.data.items():
                if not (isinstance(var_value, dict) and "id" in var_value):
                    continue
                node_id = var_value["id"]
                for edge in self._store.iter_edges_from(
                    str(node_id),
                    kinds=kinds,
                    direction=op.direction,  # type: ignore[arg-type]
                ):
                    found_any = True
                    target_id = edge.get("target", "")
                    target_node = self._store.get_node_by_id(str(target_id))
                    if target_node is None:
                        continue
                    new_data = dict(row.data)
                    new_data[op.target_variable] = dict(target_node)
                    if op.edge_variable:
                        new_data[op.edge_variable] = dict(edge)
                    result.append(Row(data=new_data))

            # OPTIONAL MATCH: emit NULL row when no edges matched
            if op.optional and not found_any:
                new_data = dict(row.data)
                new_data[op.target_variable] = None
                if op.edge_variable:
                    new_data[op.edge_variable] = None
                result.append(Row(data=new_data))

        return result

    # ------------------------------------------------------------------
    # ProjectOperator
    # ------------------------------------------------------------------

    def _execute_project(self, op: ProjectOperator) -> list[Row]:
        """Evaluate projection expressions and produce a new Row per input."""
        source_rows = self._execute_operator(op.source)

        # Determine column names
        columns: list[str] = []
        for item in op.items:
            if item.alias:
                columns.append(item.alias)
            else:
                columns.append(self._expr_name(item.expression))
        self._result_columns = columns

        result: list[Row] = []
        for row in source_rows:
            new_data: dict[str, object] = {}
            for item in op.items:
                val = self._evaluate(item.expression, row)
                key = item.alias if item.alias else self._expr_name(item.expression)
                new_data[key] = val
            result.append(Row(data=new_data))
        return result

    # ------------------------------------------------------------------
    # SortOperator
    # ------------------------------------------------------------------

    def _execute_sort(self, op: SortOperator) -> list[Row]:
        """Collect all upstream rows and sort by the ORDER BY items.

        Multi-column sort: iterate items in reverse so the first item
        is the primary key (Python's sort is stable).
        """
        source_rows = self._execute_operator(op.source)
        if not source_rows:
            return []

        for item in reversed(op.items):
            reverse = item.direction.upper() == "DESC"

            # Capture item in closure default to avoid late-binding issues.
            def sort_key(r: Row, _item: object = item) -> object:
                val = self._evaluate(_item.expression, r)  # type: ignore[union-attr]
                return self._sortable_key(val)

            source_rows.sort(key=sort_key, reverse=reverse)

        return source_rows

    @staticmethod
    def _sortable_key(val: object) -> tuple[int, object]:
        """Convert a value to a sortable tuple.

        ``None`` sorts after all other values (ASC) or before (DESC, via
        reverse=True).
        """
        if val is None:
            return (1, 0)
        return (0, val)

    # ------------------------------------------------------------------
    # LimitOperator
    # ------------------------------------------------------------------

    def _execute_limit(self, op: LimitOperator) -> list[Row]:
        """Skip the first *skip* rows, then take at most *limit* rows."""
        source_rows = self._execute_operator(op.source)
        result = source_rows[op.skip:]
        if op.limit is not None:
            result = result[: op.limit]
        return result

    # ------------------------------------------------------------------
    # DistinctOperator
    # ------------------------------------------------------------------

    def _execute_distinct(self, op: DistinctOperator) -> list[Row]:
        """Deduplicate upstream rows (full-row comparison)."""
        source_rows = self._execute_operator(op.source)
        seen: set[str] = set()
        result: list[Row] = []
        for row in source_rows:
            key = json.dumps(row.data, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result

    # ------------------------------------------------------------------
    # AggregateOperator
    # ------------------------------------------------------------------

    def _execute_aggregate(self, op: AggregateOperator) -> list[Row]:
        """Group source rows by *group_by* and compute aggregate functions.

        Each aggregate is a tuple of ``(func_name, param_str, alias)``.
        The parameter string is evaluated as a property path against each
        row (e.g. ``"n.start_line"`` resolves to ``row["n"]["start_line"]``).
        For COUNT(\"*\"), the parameter is ``"*"`` and the closure version
        of the aggregate is used (no per-row value needed).
        """
        source_rows = self._execute_operator(op.source)
        if not source_rows:
            return []

        # ------------------------------------------------------------------
        # Group rows by group_by keys
        # ------------------------------------------------------------------
        groups: dict[tuple, list[Row]] = {}
        for row in source_rows:
            if op.group_by:
                key = tuple(self._resolve_path(gb, row) for gb in op.group_by)
            else:
                key = ()  # single group for full aggregation
            groups.setdefault(key, []).append(row)

        # ------------------------------------------------------------------
        # Compute aggregates per group
        # ------------------------------------------------------------------
        result: list[Row] = []
        for group_key, group_rows in groups.items():
            new_data: dict[str, object] = {}

            # Emit group_by columns
            for i, gb_col in enumerate(op.group_by):
                new_data[gb_col] = group_key[i] if isinstance(group_key, tuple) else group_key

            # Compute each aggregate
            for func_name, param_str, alias in op.aggregates:
                try:
                    agg_func = get_aggregate(func_name)
                except KeyError:
                    raise CypherExecutionError(
                        f"Unknown aggregate function: {func_name}"
                    )

                func_name_upper = func_name.upper()

                if func_name_upper == "COUNT" and param_str == "*":
                    # COUNT(*) — closure version (no per-row argument)
                    total = 0
                    for _ in group_rows:
                        total += agg_func()
                    new_data[alias] = total
                else:
                    # Accumulating aggregates: SUM, MIN, MAX, AVG, COLLECT
                    # Determine initial accumulator
                    if func_name_upper == "MIN" or func_name_upper == "MAX":
                        acc = None
                    elif func_name_upper == "COLLECT":
                        acc = []
                    elif func_name_upper == "AVG":
                        acc = (0, 0)  # (sum, count)
                    elif func_name_upper == "SUM":
                        acc = 0
                    else:
                        # Custom aggregate — try with None initial
                        acc = None

                    first_row = True
                    for r in group_rows:
                        val = self._resolve_path(param_str, r)
                        if first_row and acc is None and func_name_upper in ("MIN", "MAX"):
                            acc = val
                            first_row = False
                        else:
                            acc = agg_func(acc, val)

                    # Finalize
                    if func_name_upper == "AVG":
                        new_data[alias] = acc[0] / acc[1] if acc[1] else None
                    else:
                        new_data[alias] = acc

            result.append(Row(data=new_data))

        return result

    @staticmethod
    def _resolve_path(path: str, row: Row) -> object:
        """Resolve a dot-separated property path against a Row.

        Examples:
            ``"n"`` → ``row["n"]``
            ``"n.name"`` → ``row["n"]["name"]``
            ``"*"`` → ``"*"`` (pass-through for COUNT(*))
        """
        if not path or path == "*":
            return "*"
        parts = path.split(".")
        val: object = row[parts[0]]
        for part in parts[1:]:
            if isinstance(val, dict):
                val = val.get(part)
            elif val is None:
                return None
            else:
                val = getattr(val, part, None)
        return val

    # ------------------------------------------------------------------
    # Plan-level execution (for sub-plans)
    # ------------------------------------------------------------------

    def _execute_plan(self, plan: LogicalPlan) -> list[Row]:
        """Execute a full LogicalPlan and return its rows.

        Used by UnionOperator and SubqueryFilterOperator to execute
        independent sub-plans.
        """
        return self._execute_operator(plan.root)

    # ------------------------------------------------------------------
    # UnionOperator
    # ------------------------------------------------------------------

    def _execute_union(self, op: UnionOperator) -> list[Row]:
        """Execute UNION [ALL] by combining left and right plan results."""
        left_rows = self._execute_plan(op.left)
        right_rows = self._execute_plan(op.right)
        combined = left_rows + right_rows

        if op.all:
            # UNION ALL — keep all rows
            return combined

        # UNION — deduplicate by all column values
        seen: set[tuple] = set()
        result: list[Row] = []
        # Determine columns from the combined rows
        columns: list[str] = []
        if combined:
            columns = list(combined[0].data.keys())

        for row in combined:
            key = tuple(str(row.get(c, "")) for c in columns)
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result

    # ------------------------------------------------------------------
    # UnwindOperator
    # ------------------------------------------------------------------

    def _execute_unwind(self, op: UnwindOperator) -> list[Row]:
        """Unwind a list expression: expand each row's list into multiple rows."""
        source_rows = self._execute_operator(op.source)
        result: list[Row] = []
        for row in source_rows:
            val = self._evaluate(op.expression, row)
            if isinstance(val, list):
                for item in val:
                    new_row = Row(data={**row.data, op.variable: item})
                    result.append(new_row)
        return result

    # ------------------------------------------------------------------
    # SubqueryFilterOperator
    # ------------------------------------------------------------------

    def _execute_subquery_filter(self, op: SubqueryFilterOperator) -> list[Row]:
        """For each upstream row, execute subquery and filter by EXISTS/NOT EXISTS.

        Uses correlation: outer row data is available to the subquery so
        that variables like ``n`` in ``EXISTS { MATCH (n)-[:calls]->(m) }``
        refer to the outer ``n`` value, not a fresh scan.
        """
        source_rows = self._execute_operator(op.source)
        result: list[Row] = []
        for row in source_rows:
            # Set correlation context so subquery ScanOperator uses outer values
            old_correlation = self._correlation_row
            self._correlation_row = row
            try:
                sub_result = self._execute_plan(op.subquery_plan)
            finally:
                self._correlation_row = old_correlation
            has_match = len(sub_result) > 0
            if (op.exists and has_match) or (not op.exists and not has_match):
                result.append(row)
        return result

    # ==================================================================
    # Expression Evaluator
    # ==================================================================

    def _evaluate(self, expr: Expression, row: Row) -> object:
        """Evaluate *expr* in the context of *row*.

        Returns:
            The result of evaluating the expression.

        Raises:
            CypherExecutionError: If the expression cannot be evaluated
                (e.g. undefined variable, unsupported feature).
        """
        if isinstance(expr, Identifier):
            try:
                return row[expr.name]
            except KeyError:
                raise CypherExecutionError(
                    f"Variable '{expr.name}' not defined in current scope"
                )

        elif isinstance(expr, PropertyAccess):
            obj_val = self._evaluate(expr.obj, row)
            if isinstance(obj_val, dict):
                return obj_val.get(expr.key)
            elif obj_val is None:
                return None
            else:
                return getattr(obj_val, expr.key, None)

        elif isinstance(expr, Literal):
            return expr.value

        elif isinstance(expr, BinaryOp):
            return self._eval_binary_op(expr, row)

        elif isinstance(expr, UnaryOp):
            return self._eval_unary_op(expr, row)

        elif isinstance(expr, FunctionCall):
            return self._eval_function_call(expr, row)

        elif isinstance(expr, ListLiteral):
            return [self._evaluate(e, row) for e in expr.elements]

        elif isinstance(expr, InExpression):
            expr_val = self._evaluate(expr.expr, row)
            list_val = self._evaluate(expr.list, row)
            if expr_val is None:
                return False
            return expr_val in list_val

        elif isinstance(expr, CaseExpression):
            return self._evaluate_case(expr, row)

        elif isinstance(expr, Parameter):
            raise CypherExecutionError(
                "Parameterized queries are not supported in P2"
            )

        elif isinstance(expr, StarExpression):
            raise CypherExecutionError(
                "RETURN * is not supported in P2 executor"
            )

        else:
            raise CypherExecutionError(
                f"Unsupported expression type: {type(expr).__name__}"
            )

    # ------------------------------------------------------------------
    # Binary operators
    # ------------------------------------------------------------------

    def _eval_binary_op(self, expr: BinaryOp, row: Row) -> object:
        op = expr.op.upper()

        # Short-circuit boolean operators (evaluate lazily).
        if op == "AND":
            return self._evaluate(expr.left, row) and self._evaluate(
                expr.right, row
            )
        if op == "OR":
            return self._evaluate(expr.left, row) or self._evaluate(
                expr.right, row
            )

        left_val = self._evaluate(expr.left, row)
        right_val = self._evaluate(expr.right, row)

        # NULL semantics for comparisons and regex: NULL compared to
        # anything yields False (SQL-style, not Python-style).
        if op in ("=", "<>", "<", ">", "<=", ">=", "=~", "IN"):
            if left_val is None or right_val is None:
                return False

        if op == "=":
            return left_val == right_val
        elif op == "<>":
            return left_val != right_val
        elif op == "<":
            return left_val < right_val
        elif op == ">":
            return left_val > right_val
        elif op == "<=":
            return left_val <= right_val
        elif op == ">=":
            return left_val >= right_val
        elif op == "+":
            return self._safe_arith(lambda a, b: a + b, left_val, right_val)
        elif op == "-":
            return self._safe_arith(lambda a, b: a - b, left_val, right_val)
        elif op == "*":
            return self._safe_arith(lambda a, b: a * b, left_val, right_val)
        elif op == "/":
            return self._safe_arith(self._div, left_val, right_val)
        elif op == "%":
            return self._safe_arith(self._mod, left_val, right_val)
        elif op == "^":
            return self._safe_arith(lambda a, b: a**b, left_val, right_val)
        elif op == "=~":
            try:
                return bool(_re.search(str(right_val), str(left_val)))
            except _re.error:
                return False
        elif op == "IN":
            return left_val in right_val
        elif op == "XOR":
            # Logical XOR: (left and not right) or (not left and right)
            return (left_val and not right_val) or (not left_val and right_val)
        elif op == "STARTS WITH":
            if left_val is None or right_val is None:
                return False
            return str(left_val).startswith(str(right_val))
        elif op == "ENDS WITH":
            if left_val is None or right_val is None:
                return False
            return str(left_val).endswith(str(right_val))
        elif op == "CONTAINS":
            if left_val is None or right_val is None:
                return False
            return str(right_val) in str(left_val)
        else:
            raise CypherExecutionError(f"Unknown binary operator: {op}")

    @staticmethod
    def _safe_arith(op_fn, a: object, b: object) -> object:
        """Apply *op_fn* safely, propagating None."""
        if a is None or b is None:
            return None
        return op_fn(a, b)

    @staticmethod
    def _div(a: object, b: object) -> object:
        if b == 0:
            raise CypherExecutionError("Division by zero")
        return a / b  # type: ignore[operator]

    @staticmethod
    def _mod(a: object, b: object) -> object:
        if b == 0:
            raise CypherExecutionError("Modulo by zero")
        return a % b  # type: ignore[operator]

    # ------------------------------------------------------------------
    # Unary operators
    # ------------------------------------------------------------------

    def _eval_unary_op(self, expr: UnaryOp, row: Row) -> object:
        val = self._evaluate(expr.operand, row)
        op = expr.op.upper()

        if op == "NOT":
            return not val
        elif op == "-":
            if val is None:
                return None
            return -val  # type: ignore[operator]
        elif op == "IS NULL":
            return val is None
        elif op == "IS NOT NULL":
            return val is not None
        else:
            raise CypherExecutionError(f"Unknown unary operator: {op}")

    # ------------------------------------------------------------------
    # Function call
    # ------------------------------------------------------------------

    def _eval_function_call(self, expr: FunctionCall, row: Row) -> object:
        """Look up and invoke a registered scalar function."""
        try:
            func = get_function(expr.name)
        except KeyError:
            raise CypherExecutionError(
                f"Unknown function: {expr.name}"
            )
        args = [self._evaluate(a, row) for a in expr.args]
        return func(*args)

    # ------------------------------------------------------------------
    # CASE expression
    # ------------------------------------------------------------------

    def _evaluate_case(self, expr: CaseExpression, row: Row) -> object:
        """Evaluate a CASE expression.

        Two forms:
          - Simple CASE (expression is not None):
            CASE expr WHEN val1 THEN res1 WHEN val2 THEN res2 [ELSE default] END
          - Search CASE (expression is None):
            CASE WHEN cond1 THEN res1 WHEN cond2 THEN res2 [ELSE default] END
        """
        if expr.expression is not None:
            # Simple CASE: evaluate the test expression once
            test_val = self._evaluate(expr.expression, row)
            for when_expr, then_expr in expr.cases:
                when_val = self._evaluate(when_expr, row)
                if when_val == test_val:
                    return self._evaluate(then_expr, row)
        else:
            # Search CASE: evaluate each WHEN condition as a boolean
            for when_expr, then_expr in expr.cases:
                cond = self._evaluate(when_expr, row)
                if cond:
                    return self._evaluate(then_expr, row)

        # No match — return ELSE default or None
        if expr.default is not None:
            return self._evaluate(expr.default, row)
        return None

    # ------------------------------------------------------------------
    # Column-name derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _expr_name(expr: Expression) -> str:
        """Derive a human-readable column name from an expression."""
        if isinstance(expr, Identifier):
            return expr.name
        elif isinstance(expr, PropertyAccess):
            return f"{Executor._expr_name(expr.obj)}.{expr.key}"
        elif isinstance(expr, Literal):
            return str(expr.value)
        elif isinstance(expr, FunctionCall):
            return f"{expr.name}(...)"
        elif isinstance(expr, BinaryOp):
            return "expr"
        elif isinstance(expr, UnaryOp):
            return "expr"
        else:
            return "col"
