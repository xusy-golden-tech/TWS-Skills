"""Tests for cypher/planner.py — AST to LogicalPlan conversion."""

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
)
from tws_graph.cypher.errors import CypherSemanticError

# Will be filled by implementation
from tws_graph.cypher.planner import (
    Planner,
    LogicalPlan,
    LogicalOperator,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    SortOperator,
    LimitOperator,
    DistinctOperator,
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
        span=_span(),
    )


def _make_statement(query: Query | None = None) -> Statement:
    if query is None:
        query = _make_query()
    return Statement(query=query, span=_span())


# ============================================================================
# LogicalPlan and LogicalOperator
# ============================================================================

class TestLogicalPlan:

    def test_construct_with_root(self):
        scan = ScanOperator(variable="n")
        plan = LogicalPlan(root=scan)
        assert plan.root is scan

    def test_plan_is_frozen(self):
        scan = ScanOperator(variable="n")
        plan = LogicalPlan(root=scan)
        # frozen dataclass should prevent mutation
        with pytest.raises(Exception):
            plan.root = ScanOperator(variable="m")  # type: ignore


class TestLogicalOperator:

    def test_is_abstract(self):
        # LogicalOperator is an ABC, cannot be instantiated directly
        with pytest.raises(TypeError):
            LogicalOperator()  # type: ignore

    def test_scan_operator_is_logical_operator(self):
        scan = ScanOperator(variable="n")
        assert isinstance(scan, LogicalOperator)


# ============================================================================
# ScanOperator
# ============================================================================

class TestScanOperator:

    def test_construct_basic(self):
        scan = ScanOperator(variable="n")
        assert scan.variable == "n"
        assert scan.label is None

    def test_construct_with_label(self):
        scan = ScanOperator(variable="n", label="Class")
        assert scan.variable == "n"
        assert scan.label == "Class"

    def test_frozen(self):
        scan = ScanOperator(variable="n")
        with pytest.raises(Exception):
            scan.variable = "m"  # type: ignore

    def test_no_source(self):
        scan = ScanOperator(variable="n")
        assert not hasattr(scan, "source")


# ============================================================================
# FilterOperator
# ============================================================================

class TestFilterOperator:

    @pytest.fixture
    def source(self):
        return ScanOperator(variable="n")

    @pytest.fixture
    def predicate(self):
        return BinaryOp(
            op="=",
            left=_prop_access("n", "name"),
            right=_literal("main"),
            span=_span(),
        )

    def test_construct(self, source, predicate):
        filt = FilterOperator(source=source, predicate=predicate)
        assert filt.source is source
        assert filt.predicate is predicate

    def test_frozen(self, source, predicate):
        filt = FilterOperator(source=source, predicate=predicate)
        with pytest.raises(Exception):
            filt.predicate = _literal(42)  # type: ignore

    def test_is_logical_operator(self, source, predicate):
        filt = FilterOperator(source=source, predicate=predicate)
        assert isinstance(filt, LogicalOperator)


# ============================================================================
# ProjectOperator
# ============================================================================

class TestProjectOperator:

    @pytest.fixture
    def source(self):
        return ScanOperator(variable="n")

    def test_construct(self, source):
        items = [ReturnItem(expression=_ident("n"))]
        proj = ProjectOperator(source=source, items=items)
        assert proj.source is source
        assert proj.items == items

    def test_multiple_items(self, source):
        items = [
            ReturnItem(expression=_ident("n")),
            ReturnItem(expression=_prop_access("n", "file_path")),
        ]
        proj = ProjectOperator(source=source, items=items)
        assert len(proj.items) == 2

    def test_frozen(self, source):
        proj = ProjectOperator(
            source=source,
            items=[ReturnItem(expression=_ident("n"))],
        )
        with pytest.raises(Exception):
            proj.items = []  # type: ignore


# ============================================================================
# SortOperator
# ============================================================================

class TestSortOperator:

    @pytest.fixture
    def source(self):
        return ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_ident("n"))],
        )

    def test_construct(self, source):
        items = [OrderByItem(expression=_ident("n"), direction="ASC")]
        sort = SortOperator(source=source, items=items)
        assert sort.source is source
        assert sort.items == items

    def test_desc_direction(self, source):
        items = [OrderByItem(expression=_ident("n"), direction="DESC")]
        sort = SortOperator(source=source, items=items)
        assert sort.items[0].direction == "DESC"

    def test_frozen(self, source):
        sort = SortOperator(
            source=source,
            items=[OrderByItem(expression=_ident("n"))],
        )
        with pytest.raises(Exception):
            sort.items = []  # type: ignore


# ============================================================================
# LimitOperator
# ============================================================================

class TestLimitOperator:

    @pytest.fixture
    def source(self):
        return ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_ident("n"))],
        )

    def test_construct_limit_only(self, source):
        lim = LimitOperator(source=source, skip=0, limit=10)
        assert lim.source is source
        assert lim.skip == 0
        assert lim.limit == 10

    def test_construct_skip_and_limit(self, source):
        lim = LimitOperator(source=source, skip=5, limit=10)
        assert lim.skip == 5
        assert lim.limit == 10

    def test_construct_skip_only(self, source):
        lim = LimitOperator(source=source, skip=5, limit=None)
        assert lim.skip == 5
        assert lim.limit is None

    def test_defaults(self, source):
        lim = LimitOperator(source=source)
        assert lim.skip == 0
        assert lim.limit is None

    def test_frozen(self, source):
        lim = LimitOperator(source=source, limit=10)
        with pytest.raises(Exception):
            lim.limit = 20  # type: ignore


# ============================================================================
# DistinctOperator
# ============================================================================

class TestDistinctOperator:

    def test_construct(self):
        source = ScanOperator(variable="n")
        dist = DistinctOperator(source=source)
        assert dist.source is source

    def test_frozen(self):
        source = ScanOperator(variable="n")
        dist = DistinctOperator(source=source)
        with pytest.raises(Exception):
            dist.source = ScanOperator(variable="m")  # type: ignore

    def test_is_logical_operator(self):
        dist = DistinctOperator(source=ScanOperator(variable="n"))
        assert isinstance(dist, LogicalOperator)


# ============================================================================
# EdgeExpandOperator
# ============================================================================

class TestEdgeExpandOperator:

    @pytest.fixture
    def source(self):
        return ScanOperator(variable="a")

    def test_construct_out(self, source):
        expand = EdgeExpandOperator(
            source=source,
            edge_types=["calls"],
            direction="out",
            target_variable="b",
        )
        assert expand.source is source
        assert expand.edge_types == ["calls"]
        assert expand.direction == "out"
        assert expand.target_variable == "b"
        assert expand.edge_variable is None

    def test_construct_with_edge_var(self, source):
        expand = EdgeExpandOperator(
            source=source,
            edge_types=["calls"],
            direction="out",
            edge_variable="r",
            target_variable="b",
        )
        assert expand.edge_variable == "r"

    def test_construct_in(self, source):
        expand = EdgeExpandOperator(
            source=source,
            edge_types=["imports"],
            direction="in",
            target_variable="b",
        )
        assert expand.direction == "in"

    def test_construct_both(self, source):
        expand = EdgeExpandOperator(
            source=source,
            edge_types=["references"],
            direction="both",
            target_variable="b",
        )
        assert expand.direction == "both"

    def test_frozen(self, source):
        expand = EdgeExpandOperator(
            source=source,
            edge_types=["calls"],
            direction="out",
            target_variable="b",
        )
        with pytest.raises(Exception):
            expand.edge_types = ["imports"]  # type: ignore


# ============================================================================
# Planner — basic MATCH-RETURN
# ============================================================================

class TestPlannerMatchReturn:

    def test_simple_match_return(self):
        """MATCH (n) RETURN n → ScanOperator + ProjectOperator."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(
            pattern=PatternPart(node=node, chain=[]),
        )
        return_clause = ReturnClause(
            items=[ReturnItem(expression=_ident("n"))],
        )
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Root should be ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        assert isinstance(proj, ProjectOperator)
        assert len(proj.items) == 1
        assert isinstance(proj.items[0].expression, Identifier)
        assert proj.items[0].expression.name == "n"

        # Source of Project should be ScanOperator
        scan = proj.source
        assert isinstance(scan, ScanOperator)
        assert scan.variable == "n"
        assert scan.label is None

    def test_match_with_label(self):
        """MATCH (n:Class) RETURN n → ScanOperator with label."""
        planner = Planner()
        node = NodePattern(name="n", labels=["Class"])
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        proj = plan.root
        assert isinstance(proj, ProjectOperator)
        scan = proj.source
        assert isinstance(scan, ScanOperator)
        assert scan.variable == "n"
        assert scan.label == "Class"

    def test_match_with_multiple_labels_uses_first(self):
        """MATCH (n:Class:Function) RETURN n → ScanOperator with first label."""
        planner = Planner()
        node = NodePattern(name="n", labels=["Class", "Function"])
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        scan = plan.root.source
        assert isinstance(scan, ScanOperator)
        assert scan.label == "Class"  # first label


# ============================================================================
# Planner — WHERE clause
# ============================================================================

class TestPlannerWhere:

    def test_where_clause(self):
        """MATCH (n) WHERE n.name = 'main' RETURN n → FilterOperator."""
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

        # Root: ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        # ProjectOperator.source → FilterOperator
        filt = plan.root.source
        assert isinstance(filt, FilterOperator)
        assert isinstance(filt.predicate, BinaryOp)
        assert filt.predicate.op == "="
        # FilterOperator.source → ScanOperator
        scan = filt.source
        assert isinstance(scan, ScanOperator)
        assert scan.variable == "n"


# ============================================================================
# Planner — ORDER BY
# ============================================================================

class TestPlannerOrderBy:

    def test_order_by_asc(self):
        """MATCH (n) RETURN n ORDER BY n.name ASC → SortOperator."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        order_by = OrderByClause(
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        )
        query = _make_query(
            match=match, return_clause=return_clause, order_by=order_by
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Root: SortOperator
        assert isinstance(plan.root, SortOperator)
        sort = plan.root
        assert len(sort.items) == 1
        assert sort.items[0].direction == "ASC"
        # SortOperator.source → ProjectOperator
        proj = sort.source
        assert isinstance(proj, ProjectOperator)
        # ProjectOperator.source → ScanOperator
        scan = proj.source
        assert isinstance(scan, ScanOperator)

    def test_order_by_desc(self):
        """MATCH (n) RETURN n ORDER BY n.name DESC → SortOperator DESC."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        order_by = OrderByClause(
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="DESC")],
        )
        query = _make_query(
            match=match, return_clause=return_clause, order_by=order_by
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, SortOperator)
        assert plan.root.items[0].direction == "DESC"


# ============================================================================
# Planner — SKIP / LIMIT
# ============================================================================

class TestPlannerSkipLimit:

    def test_limit(self):
        """MATCH (n) RETURN n LIMIT 10 → LimitOperator."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause, limit=10)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Root: LimitOperator
        assert isinstance(plan.root, LimitOperator)
        lim = plan.root
        assert lim.skip == 0
        assert lim.limit == 10
        # LimitOperator.source → ProjectOperator
        assert isinstance(lim.source, ProjectOperator)

    def test_skip(self):
        """MATCH (n) RETURN n SKIP 5 → LimitOperator with skip=5."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause, skip=5)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, LimitOperator)
        assert plan.root.skip == 5
        assert plan.root.limit is None

    def test_skip_and_limit(self):
        """MATCH (n) RETURN n SKIP 5 LIMIT 10 → LimitOperator(skip=5, limit=10)."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(
            match=match, return_clause=return_clause, skip=5, limit=10
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, LimitOperator)
        lim = plan.root
        assert lim.skip == 5
        assert lim.limit == 10


# ============================================================================
# Planner — RETURN DISTINCT
# ============================================================================

class TestPlannerDistinct:

    def test_return_distinct(self):
        """MATCH (n) RETURN DISTINCT n → DistinctOperator + ProjectOperator."""
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

        # Root: ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        proj = plan.root
        # ProjectOperator.source → DistinctOperator
        dist = proj.source
        assert isinstance(dist, DistinctOperator)
        # DistinctOperator.source → ScanOperator
        scan = dist.source
        assert isinstance(scan, ScanOperator)
        assert scan.variable == "n"


# ============================================================================
# Planner — full chain
# ============================================================================

class TestPlannerFullChain:

    def test_full_chain(self):
        """MATCH→WHERE→RETURN→ORDER BY→LIMIT complete chain."""
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
        order_by = OrderByClause(
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        )
        query = _make_query(
            match=match,
            where=where,
            return_clause=return_clause,
            order_by=order_by,
            skip=0,
            limit=10,
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Traverse from root to leaf: Limit → Sort → Project → Filter → Scan
        op = plan.root
        assert isinstance(op, LimitOperator)
        assert op.limit == 10

        op = op.source
        assert isinstance(op, SortOperator)
        assert len(op.items) == 1

        op = op.source
        assert isinstance(op, ProjectOperator)
        assert len(op.items) == 1

        op = op.source
        assert isinstance(op, FilterOperator)
        assert isinstance(op.predicate, BinaryOp)

        op = op.source
        assert isinstance(op, ScanOperator)
        assert op.variable == "n"

    def test_chain_with_skip(self):
        """MATCH→RETURN→SKIP complete chain."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause, skip=5)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        op = plan.root
        assert isinstance(op, LimitOperator)
        assert op.skip == 5
        assert isinstance(op.source, ProjectOperator)
        assert isinstance(op.source.source, ScanOperator)


# ============================================================================
# Planner — EdgeExpand
# ============================================================================

class TestPlannerEdgeExpand:

    def test_edge_expand_out(self):
        """MATCH (a)-[r:calls]->(b) RETURN b → EdgeExpandOperator."""
        planner = Planner()
        node_a = NodePattern(name="a")
        node_b = NodePattern(name="b")
        rel = RelPattern(
            name="r",
            types=["calls"],
            direction=Direction.RIGHT,
        )
        elem = PatternElement(node=node_b, rel=rel)
        pattern = PatternPart(node=node_a, chain=[elem])
        match = MatchClause(pattern=pattern)
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("b"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Root: ProjectOperator
        proj = plan.root
        assert isinstance(proj, ProjectOperator)
        # Source: EdgeExpandOperator
        expand = proj.source
        assert isinstance(expand, EdgeExpandOperator)
        assert expand.edge_types == ["calls"]
        assert expand.direction == "out"
        assert expand.target_variable == "b"
        assert expand.edge_variable == "r"
        # Source of EdgeExpand: ScanOperator for 'a'
        scan = expand.source
        assert isinstance(scan, ScanOperator)
        assert scan.variable == "a"

    def test_edge_expand_in(self):
        """MATCH (a)<-[r:imports]-(b) RETURN b → EdgeExpandOperator in direction."""
        planner = Planner()
        node_a = NodePattern(name="a")
        node_b = NodePattern(name="b")
        rel = RelPattern(
            name="r",
            types=["imports"],
            direction=Direction.LEFT,
        )
        elem = PatternElement(node=node_b, rel=rel)
        pattern = PatternPart(node=node_a, chain=[elem])
        match = MatchClause(pattern=pattern)
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("b"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        expand = plan.root.source
        assert isinstance(expand, EdgeExpandOperator)
        assert expand.direction == "in"

    def test_edge_expand_both(self):
        """MATCH (a)-[r:references]-(b) RETURN b → EdgeExpandOperator both direction."""
        planner = Planner()
        node_a = NodePattern(name="a")
        node_b = NodePattern(name="b")
        rel = RelPattern(
            name="r",
            types=["references"],
            direction=Direction.BOTH,
        )
        elem = PatternElement(node=node_b, rel=rel)
        pattern = PatternPart(node=node_a, chain=[elem])
        match = MatchClause(pattern=pattern)
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("b"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        expand = plan.root.source
        assert isinstance(expand, EdgeExpandOperator)
        assert expand.direction == "both"

    def test_edge_expand_no_edge_var(self):
        """MATCH (a)-[:calls]->(b) RETURN b → EdgeExpandOperator without edge variable."""
        planner = Planner()
        node_a = NodePattern(name="a")
        node_b = NodePattern(name="b")
        rel = RelPattern(types=["calls"], direction=Direction.RIGHT)
        elem = PatternElement(node=node_b, rel=rel)
        pattern = PatternPart(node=node_a, chain=[elem])
        match = MatchClause(pattern=pattern)
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("b"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        expand = plan.root.source
        assert isinstance(expand, EdgeExpandOperator)
        assert expand.edge_variable is None


# ============================================================================
# Planner — semantic checks
# ============================================================================

class TestPlannerSemanticErrors:

    def test_undefined_variable_in_return(self):
        """RETURN m when only n is defined → CypherSemanticError."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("m"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        with pytest.raises(CypherSemanticError, match="m"):
            planner.plan(stmt)

    def test_undefined_variable_in_where(self):
        """WHERE m.name = 'x' when only n is defined → CypherSemanticError."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        where = WhereClause(
            expression=BinaryOp(
                op="=",
                left=_prop_access("m", "name"),
                right=_literal("x"),
                span=_span(),
            ),
        )
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, where=where, return_clause=return_clause)
        stmt = _make_statement(query)

        with pytest.raises(CypherSemanticError, match="m"):
            planner.plan(stmt)

    def test_defined_variable_where(self):
        """WHERE n.name = 'x' when n is defined → no error."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        where = WhereClause(
            expression=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("x"),
                span=_span(),
            ),
        )
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, where=where, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)
        assert isinstance(plan.root, ProjectOperator)

    def test_no_match_clause(self):
        """Query without MATCH clause → CypherSemanticError."""
        planner = Planner()
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=None, return_clause=return_clause)
        stmt = _make_statement(query)

        with pytest.raises(CypherSemanticError, match="MATCH"):
            planner.plan(stmt)

    def test_no_return_clause(self):
        """Query without RETURN clause → error (defensive)."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        query = Query(
            return_clause=ReturnClause(items=[]),
            match=match,
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        with pytest.raises(CypherSemanticError):
            planner.plan(stmt)


# ============================================================================
# Planner — DAG traversal
# ============================================================================

class TestPlannerDagTraversal:

    def test_plan_can_be_traversed(self):
        """LogicalPlan DAG can be traversed from root to leaf."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Walk the chain from root to leaf
        visited = []
        op = plan.root
        while True:
            visited.append(type(op).__name__)
            if hasattr(op, "source"):
                op = op.source
            else:
                break

        assert visited == ["ProjectOperator", "ScanOperator"]

    def test_full_chain_traversal(self):
        """Full chain can be traversed top-down."""
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
        order_by = OrderByClause(
            items=[OrderByItem(expression=_prop_access("n", "name"))],
        )
        query = _make_query(
            match=match,
            where=where,
            return_clause=return_clause,
            order_by=order_by,
            skip=0,
            limit=10,
        )
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        visited = []
        op = plan.root
        while True:
            visited.append(type(op).__name__)
            if hasattr(op, "source"):
                op = op.source
            else:
                break

        assert visited == [
            "LimitOperator",
            "SortOperator",
            "ProjectOperator",
            "FilterOperator",
            "ScanOperator",
        ]


# ============================================================================
# Planner — return with star
# ============================================================================

class TestPlannerReturnStar:

    def test_return_star(self):
        """MATCH (n) RETURN * → ProjectOperator with StarExpression."""
        from tws_graph.cypher.ast import StarExpression

        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(
            items=[ReturnItem(expression=StarExpression(span=_span()))],
        )
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        assert len(plan.root.items) == 1
        assert isinstance(plan.root.items[0].expression, StarExpression)


# ============================================================================
# Planner — no optional extras (no WHERE, no ORDER BY, etc.)
# ============================================================================

class TestPlannerMinimal:

    def test_minimal_no_optional_clauses(self):
        """MATCH (n) RETURN n with no WHERE/ORDER BY/SKIP/LIMIT."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        # Only Project → Scan, no extra operators
        assert isinstance(plan.root, ProjectOperator)
        assert isinstance(plan.root.source, ScanOperator)
        assert not isinstance(plan.root.source, FilterOperator)

    def test_match_without_label(self):
        """MATCH (n) RETURN n → label is None."""
        planner = Planner()
        node = NodePattern(name="n", labels=[])
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[ReturnItem(expression=_ident("n"))])
        query = _make_query(match=match, return_clause=return_clause)
        stmt = _make_statement(query)

        plan = planner.plan(stmt)

        scan = plan.root.source
        assert isinstance(scan, ScanOperator)
        assert scan.label is None
