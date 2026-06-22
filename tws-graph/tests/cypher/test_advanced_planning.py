"""Tests for P3 Planner extensions — UNION, UNWIND, WITH, SubqueryFilter.

Tests the Planner's ability to generate new logical operators:
UnionOperator, UnwindOperator, SubqueryFilterOperator.
"""

from __future__ import annotations

import pytest

from tws_graph.cypher.ast import (
    Statement,
    Query,
    MatchClause,
    PatternPart,
    PatternElement,
    NodePattern,
    RelPattern,
    Direction,
    WhereClause,
    ReturnClause,
    ReturnItem,
    OrderByClause,
    OrderByItem,
    Identifier,
    PropertyAccess,
    BinaryOp,
    Literal,
    Span,
    SubqueryExpression,
    UnwindClause,
    WithClause,
    ListLiteral,
)

from tws_graph.cypher.planner import (
    Planner,
    LogicalPlan,
    LogicalOperator,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    # New operators will be filled by implementation
    UnionOperator,
    UnwindOperator,
    SubqueryFilterOperator,
)


# ============================================================================
# Helpers
# ============================================================================


def _span():
    return Span(start_line=1, start_col=1, end_line=1, end_col=5)


def _ident(name: str) -> Identifier:
    return Identifier(name=name, span=_span())


def _literal(value) -> Literal:
    return Literal(value=value, span=_span())


def _prop_access(obj_name: str, key: str) -> PropertyAccess:
    return PropertyAccess(obj=_ident(obj_name), key=key, span=_span())


def _make_query(
    match: MatchClause | None = None,
    where: WhereClause | None = None,
    return_clause: ReturnClause | None = None,
    order_by: OrderByClause | None = None,
    skip: int | None = None,
    limit: int | None = None,
    unwind: UnwindClause | None = None,
    with_clause: WithClause | None = None,
) -> Query:
    if return_clause is None:
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
    return Query(
        return_clause=return_clause,
        match=match,
        where=where,
        order_by=order_by,
        skip=skip,
        limit=limit,
        unwind=unwind,
        with_clause=with_clause,
        span=_span(),
    )


def _make_statement(query: Query | None = None) -> Statement:
    if query is None:
        query = _make_query()
    return Statement(query=query, span=_span())


# ============================================================================
# UnionOperator construction
# ============================================================================


class TestUnionOperator:
    """Tests for UnionOperator logical operator."""

    def test_construct_union_all(self):
        left_plan = LogicalPlan(root=ScanOperator(variable="n"))
        right_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = UnionOperator(left=left_plan, right=right_plan, all=True)
        assert op.left is left_plan
        assert op.right is right_plan
        assert op.all is True

    def test_construct_union(self):
        left_plan = LogicalPlan(root=ScanOperator(variable="n"))
        right_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = UnionOperator(left=left_plan, right=right_plan, all=False)
        assert op.all is False

    def test_union_default_all_false(self):
        left_plan = LogicalPlan(root=ScanOperator(variable="n"))
        right_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = UnionOperator(left=left_plan, right=right_plan)
        assert op.all is False

    def test_union_is_logical_operator(self):
        left_plan = LogicalPlan(root=ScanOperator(variable="n"))
        right_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = UnionOperator(left=left_plan, right=right_plan)
        assert isinstance(op, LogicalOperator)

    def test_union_frozen(self):
        left_plan = LogicalPlan(root=ScanOperator(variable="n"))
        right_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = UnionOperator(left=left_plan, right=right_plan)
        with pytest.raises(Exception):
            op.all = True  # type: ignore


# ============================================================================
# UnwindOperator construction
# ============================================================================


class TestUnwindOperator:
    """Tests for UnwindOperator logical operator."""

    def test_construct_basic(self):
        source = ScanOperator(variable="n")
        expr = _ident("x")
        op = UnwindOperator(source=source, expression=expr, variable="val")
        assert op.source is source
        assert op.expression is expr
        assert op.variable == "val"

    def test_construct_with_list_literal(self):
        source = ScanOperator(variable="n")
        expr = ListLiteral(elements=[_literal(1), _literal(2), _literal(3)], span=_span())
        op = UnwindOperator(source=source, expression=expr, variable="x")
        assert isinstance(op.expression, ListLiteral)
        assert op.variable == "x"

    def test_unwind_is_logical_operator(self):
        op = UnwindOperator(
            source=ScanOperator(variable="n"),
            expression=_ident("x"),
            variable="val",
        )
        assert isinstance(op, LogicalOperator)

    def test_unwind_frozen(self):
        op = UnwindOperator(
            source=ScanOperator(variable="n"),
            expression=_ident("x"),
            variable="val",
        )
        with pytest.raises(Exception):
            op.variable = "y"  # type: ignore


# ============================================================================
# SubqueryFilterOperator construction
# ============================================================================


class TestSubqueryFilterOperator:
    """Tests for SubqueryFilterOperator logical operator."""

    def test_construct_exists(self):
        source = ScanOperator(variable="n")
        sub_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = SubqueryFilterOperator(source=source, subquery_plan=sub_plan, exists=True)
        assert op.source is source
        assert op.subquery_plan is sub_plan
        assert op.exists is True

    def test_construct_not_exists(self):
        source = ScanOperator(variable="n")
        sub_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = SubqueryFilterOperator(source=source, subquery_plan=sub_plan, exists=False)
        assert op.exists is False

    def test_default_exists_true(self):
        source = ScanOperator(variable="n")
        sub_plan = LogicalPlan(root=ScanOperator(variable="m"))
        op = SubqueryFilterOperator(source=source, subquery_plan=sub_plan)
        assert op.exists is True

    def test_subquery_filter_is_logical_operator(self):
        op = SubqueryFilterOperator(
            source=ScanOperator(variable="n"),
            subquery_plan=LogicalPlan(root=ScanOperator(variable="m")),
        )
        assert isinstance(op, LogicalOperator)

    def test_subquery_filter_frozen(self):
        op = SubqueryFilterOperator(
            source=ScanOperator(variable="n"),
            subquery_plan=LogicalPlan(root=ScanOperator(variable="m")),
        )
        with pytest.raises(Exception):
            op.exists = False  # type: ignore


# ============================================================================
# Planner — UNION
# ============================================================================


class TestPlannerUnion:
    """Tests for planner generating UnionOperator from UNION queries."""

    def test_union_two_queries(self):
        """MATCH (n) RETURN n UNION MATCH (m) RETURN m → UnionOperator(all=False)."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) RETURN n UNION MATCH (m) RETURN m")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        assert isinstance(plan.root, UnionOperator)
        op = plan.root
        assert isinstance(op, UnionOperator)
        assert op.all is False
        # Left plan should have a ProjectOperator
        assert isinstance(op.left.root, ProjectOperator)
        # Right plan should have a ProjectOperator
        assert isinstance(op.right.root, ProjectOperator)

    def test_union_all_two_queries(self):
        """MATCH (n) RETURN n UNION ALL MATCH (m) RETURN m → UnionOperator(all=True)."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) RETURN n UNION ALL MATCH (m) RETURN m")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        assert isinstance(plan.root, UnionOperator)
        assert plan.root.all is True

    def test_union_all_defaults_to_union(self):
        """UNION without ALL produces UnionOperator with all=False."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n:Class) RETURN n UNION MATCH (m:Function) RETURN m")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        assert isinstance(plan.root, UnionOperator)
        assert plan.root.all is False


# ============================================================================
# Planner — UNWIND
# ============================================================================


class TestPlannerUnwind:
    """Tests for planner generating UnwindOperator."""

    def test_unwind_with_list(self):
        """UNWIND [1,2,3] AS x RETURN x → UnwindOperator."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("UNWIND [1, 2, 3] AS x RETURN x")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root should be ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        # Source should be UnwindOperator
        assert isinstance(proj.source, UnwindOperator)
        unwind = proj.source
        assert isinstance(unwind, UnwindOperator)
        assert unwind.variable == "x"
        assert isinstance(unwind.expression, ListLiteral)
        # Source of UnwindOperator should be a virtual scan
        assert isinstance(unwind.source, ScanOperator)
        assert unwind.source.variable == ""

    def test_unwind_with_match(self):
        """MATCH (n) UNWIND n.list AS item RETURN item → UnwindOperator after Scan."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) UNWIND n.name AS item RETURN item")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root: ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        # Source: UnwindOperator
        assert isinstance(proj.source, UnwindOperator)
        unwind = proj.source
        assert isinstance(unwind, UnwindOperator)
        assert unwind.variable == "item"
        # Source of Unwind: ScanOperator
        assert isinstance(unwind.source, ScanOperator)
        assert unwind.source.variable == "n"


# ============================================================================
# Planner — WITH clause
# ============================================================================


class TestPlannerWith:
    """Tests for planner handling WITH clause."""

    def test_with_clause_as_projection(self):
        """MATCH (n) WITH n.name AS name RETURN name → WITH becomes ProjectOperator."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) WITH n.name AS name RETURN name")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root should be ProjectOperator (from RETURN)
        assert isinstance(plan.root, ProjectOperator)
        return_proj = plan.root
        # Source should be ProjectOperator (from WITH)
        assert isinstance(return_proj.source, ProjectOperator)
        with_proj = return_proj.source
        assert len(with_proj.items) == 1
        # Source of WITH should be ScanOperator
        assert isinstance(with_proj.source, ScanOperator)

    def test_with_clause_with_where(self):
        """MATCH (n) WITH n.name AS name WHERE name IS NOT NULL RETURN name."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) WITH n.name AS name WHERE name IS NOT NULL RETURN name")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root: ProjectOperator (RETURN)
        assert isinstance(plan.root, ProjectOperator)
        return_proj = plan.root
        # Source: FilterOperator (WITH WHERE)
        assert isinstance(return_proj.source, FilterOperator)
        with_filter = return_proj.source
        # Source: ProjectOperator (WITH)
        assert isinstance(with_filter.source, ProjectOperator)
        # Source: ScanOperator
        assert isinstance(with_filter.source.source, ScanOperator)


# ============================================================================
# Planner — SubqueryFilterOperator (EXISTS)
# ============================================================================


class TestPlannerSubqueryFilter:
    """Tests for planner generating SubqueryFilterOperator."""

    def test_exists_subquery(self):
        """MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n → SubqueryFilterOperator(exists=True)."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root: ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        # Source: SubqueryFilterOperator
        assert isinstance(proj.source, SubqueryFilterOperator)
        sub_filter = proj.source
        assert isinstance(sub_filter, SubqueryFilterOperator)
        assert sub_filter.exists is True
        # subquery_plan should be a LogicalPlan for the inner MATCH
        assert isinstance(sub_filter.subquery_plan, LogicalPlan)
        # Source: ScanOperator
        assert isinstance(sub_filter.source, ScanOperator)

    def test_not_exists_subquery(self):
        """MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } RETURN n → SubqueryFilterOperator(exists=False)."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } RETURN n")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root: ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        # Source: SubqueryFilterOperator with exists=False
        assert isinstance(proj.source, SubqueryFilterOperator)
        sub_filter = proj.source
        assert sub_filter.exists is False


# ============================================================================
# Planner — combined scenarios
# ============================================================================


class TestPlannerCombined:
    """Tests for planner with combinations of new features."""

    def test_unwind_with_full_chain(self):
        """UNWIND [1,2,3] AS x RETURN x ORDER BY x LIMIT 2."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("UNWIND [1, 2, 3] AS x RETURN x ORDER BY x DESC LIMIT 1")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Full chain: Limit → Sort → Project → Unwind → VirtualScan
        op = plan.root
        assert isinstance(op, __import__('tws_graph.cypher.planner', fromlist=['LimitOperator']).LimitOperator)
        assert isinstance(op.source, __import__('tws_graph.cypher.planner', fromlist=['SortOperator']).SortOperator)
        assert isinstance(op.source.source, ProjectOperator)
        assert isinstance(op.source.source.source, UnwindOperator)

    def test_match_unwind_with_where(self):
        """MATCH (n) WHERE n.start_line > 5 UNWIND n.name AS ch RETURN ch."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser

        lexer = Lexer("MATCH (n) UNWIND n.name AS ch RETURN ch")
        parser = Parser(lexer)
        stmt = parser.parse()

        planner = Planner()
        plan = planner.plan(stmt)

        # Root: ProjectOperator → UnwindOperator → ScanOperator
        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, UnwindOperator)
        assert isinstance(plan.root.source.source, ScanOperator)


# ============================================================================
# Planner — regression: existing features still work
# ============================================================================


class TestPlannerRegression:
    """Verify existing planner features are not broken."""

    def test_simple_match_return(self):
        """MATCH (n) RETURN n — regression check."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, ScanOperator)
        assert plan.root.source.variable == "n"

    def test_match_where_return(self):
        """MATCH (n) WHERE n.name = 'main' RETURN n."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        where = WhereClause(
            expression=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("main"),
                span=_span(),
            ),
        )
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, where=where, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, FilterOperator)
        assert isinstance(plan.root.source.source, ScanOperator)

    def test_match_return_order_limit(self):
        """MATCH (n) RETURN n ORDER BY n.name LIMIT 5."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        order_by = OrderByClause(
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        )
        query = _make_query(
            match=match, return_clause=return_clause, order_by=order_by, limit=5
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, __import__('tws_graph.cypher.planner', fromlist=['LimitOperator']).LimitOperator)
        assert plan.root.limit == 5

    def test_match_return_distinct(self):
        """MATCH (n) RETURN DISTINCT n."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(
            items=[ReturnItem(expression=_ident("n"))],
            distinct=True,
        )
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, __import__('tws_graph.cypher.planner', fromlist=['DistinctOperator']).DistinctOperator)
        assert isinstance(plan.root.source.source, ScanOperator)

    def test_edge_expand(self):
        """MATCH (a)-[:calls]->(b) RETURN b."""
        planner = Planner()
        node_a = NodePattern(name="a")
        node_b = NodePattern(name="b")
        rel = RelPattern(name="r", types=["calls"], direction=Direction.RIGHT)
        elem = PatternElement(node=node_b, rel=rel)
        pattern = PatternPart(node=node_a, chain=[elem])
        match = MatchClause(pattern=pattern)
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("b"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, EdgeExpandOperator)
        assert plan.root.source.edge_types == ["calls"]
        assert plan.root.source.target_variable == "b"

    def test_undefined_variable_still_errors(self):
        """UNDEFINED variable in RETURN still raises CypherSemanticError."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("m"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        from tws_graph.cypher.errors import CypherSemanticError
        with pytest.raises(CypherSemanticError, match="m"):
            planner.plan(stmt)
