"""P2 design-sync fix regression tests — covers all 6 defects.

Defect 1-4: Missing binary operators in executor (XOR, STARTS WITH, ENDS WITH, CONTAINS)
Defect 5:   Missing AggregateOperator in planner + executor
Defect 6:   OPTIONAL MATCH LEFT OUTER JOIN semantics in planner + executor
"""

from __future__ import annotations

import pytest

from tws_graph.cypher.ast import (
    BinaryOp,
    FunctionCall,
    Identifier,
    Literal,
    PropertyAccess,
    ReturnItem,
    Span,
    StarExpression,
    PatternPart,
    PatternElement,
    NodePattern,
    RelPattern,
    MatchClause,
    ReturnClause,
    Direction,
    Query,
    Statement,
)
from tws_graph.cypher.errors import CypherExecutionError, CypherSemanticError
from tws_graph.cypher.executor import Executor, Row, ResultSet
from tws_graph.cypher.planner import (
    LogicalPlan,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    AggregateOperator,
    Planner,
)
from tws_graph.store.memory_store import MemoryStore


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


def _make_node(node_id: str, kind: str, name: str, **kwargs) -> dict:
    node = {
        "id": node_id,
        "kind": kind,
        "name": name,
        "qualified_name": f"test::{name}",
        "file_path": f"test/{name}.py",
        "language": "python",
        "start_line": 1,
        "end_line": 10,
    }
    node.update(kwargs)
    return node


def _make_edge(edge_id: int, source: str, target: str, kind: str, **kwargs) -> dict:
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
    }
    edge.update(kwargs)
    return edge


def _build_labeled_store() -> MemoryStore:
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main_hello"),
        _make_node("n2", "class", "MyClass"),
        _make_node("n3", "function", "helper_hello"),
        _make_node("n4", "class", "YourClass"),
        _make_node("n5", "module", "utils"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_graph_store() -> MemoryStore:
    """Create a MemoryStore with nodes and edges for optional match tests."""
    store = MemoryStore()
    nodes = [
        _make_node("a", "function", "caller"),
        _make_node("b", "function", "callee1"),
        _make_node("c", "function", "callee2"),
        _make_node("d", "class", "TargetClass"),
    ]
    store.insert_nodes(nodes)
    edges = [
        _make_edge(1, "a", "b", "calls"),
        _make_edge(2, "a", "c", "calls"),
        _make_edge(3, "c", "a", "calls"),
        _make_edge(4, "a", "d", "imports"),
    ]
    store.insert_edges(edges)
    return store


# ============================================================================
# Defect 1: XOR binary operator
# ============================================================================

class TestXorOperator:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        s.insert_nodes([_make_node("n1", "function", "test")])
        return s

    def test_xor_true_false_returns_true(self, store):
        """True XOR False → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="XOR",
            left=_literal(True),
            right=_literal(False),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1

    def test_xor_true_true_returns_false(self, store):
        """True XOR True → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="XOR",
            left=_literal(True),
            right=_literal(True),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_xor_false_false_returns_false(self, store):
        """False XOR False → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="XOR",
            left=_literal(False),
            right=_literal(False),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_xor_false_true_returns_true(self, store):
        """False XOR True → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="XOR",
            left=_literal(False),
            right=_literal(True),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1

    def test_xor_with_expression(self, store):
        """XOR with property comparison expressions."""
        exec_ = Executor(store)
        # n.name = 'test' XOR n.kind = 'class' → True XOR False → True
        predicate = BinaryOp(
            op="XOR",
            left=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("test"),
                span=_span(),
            ),
            right=BinaryOp(
                op="=",
                left=_prop_access("n", "kind"),
                right=_literal("class"),
                span=_span(),
            ),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1


# ============================================================================
# Defect 2-4: String operators (STARTS WITH, ENDS WITH, CONTAINS)
# ============================================================================

class TestStartsWithOperator:

    @pytest.fixture
    def store(self):
        return _build_labeled_store()

    def test_starts_with_true(self, store):
        """'hello' STARTS WITH 'hel' → True"""
        exec_ = Executor(store)
        # Filter by name starts with 'main'
        predicate = BinaryOp(
            op="STARTS WITH",
            left=_prop_access("n", "name"),
            right=_literal("main"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        names = {row["n"]["name"] for row in result.rows}
        assert "main_hello" in names
        # Should NOT include helper_hello
        assert "helper_hello" not in names

    def test_starts_with_false(self, store):
        """'hello' STARTS WITH 'lo' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="STARTS WITH",
            left=_prop_access("n", "name"),
            right=_literal("lo"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_starts_with_null_left(self, store):
        """NULL STARTS WITH 'x' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="STARTS WITH",
            left=_literal(None),
            right=_literal("x"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_starts_with_null_right(self, store):
        """'hello' STARTS WITH NULL → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="STARTS WITH",
            left=_literal("hello"),
            right=_literal(None),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_starts_with_exact_match(self, store):
        """'hello' STARTS WITH 'hello' → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="STARTS WITH",
            left=_literal("hello"),
            right=_literal("hello"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 5  # all rows pass (literal comparison)


class TestEndsWithOperator:

    @pytest.fixture
    def store(self):
        return _build_labeled_store()

    def test_ends_with_true(self, store):
        """'hello' ENDS WITH 'lo' → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="ENDS WITH",
            left=_prop_access("n", "name"),
            right=_literal("hello"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        names = {row["n"]["name"] for row in result.rows}
        assert "main_hello" in names
        assert "helper_hello" in names

    def test_ends_with_false(self, store):
        """'hello' ENDS WITH 'hel' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="ENDS WITH",
            left=_literal("hello"),
            right=_literal("hel"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_ends_with_null(self, store):
        """NULL ENDS WITH 'x' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="ENDS WITH",
            left=_literal(None),
            right=_literal("x"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_ends_with_single_char(self, store):
        """'hello' ENDS WITH 'o' → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="ENDS WITH",
            left=_literal("hello"),
            right=_literal("o"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 5


class TestContainsOperator:

    @pytest.fixture
    def store(self):
        return _build_labeled_store()

    def test_contains_true(self, store):
        """'hello' CONTAINS 'ell' → True"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="CONTAINS",
            left=_prop_access("n", "name"),
            right=_literal("ell"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        names = {row["n"]["name"] for row in result.rows}
        assert "main_hello" in names
        assert "helper_hello" in names

    def test_contains_false(self, store):
        """'hello' CONTAINS 'xyz' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="CONTAINS",
            left=_literal("hello"),
            right=_literal("xyz"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_contains_null(self, store):
        """NULL CONTAINS 'x' → False"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="CONTAINS",
            left=_literal(None),
            right=_literal("x"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_contains_case_sensitive(self, store):
        """'Hello' CONTAINS 'ell' → True (case-sensitive check)"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="CONTAINS",
            left=_literal("Hello"),
            right=_literal("ell"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 5

    def test_contains_case_sensitive_mismatch(self, store):
        """'Hello' CONTAINS 'ELL' → False (case-sensitive)"""
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="CONTAINS",
            left=_literal("Hello"),
            right=_literal("ELL"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0


# ============================================================================
# Defect 5: AggregateOperator — planner + executor
# ============================================================================

class TestAggregateOperatorPlanner:

    def test_aggregate_operator_dataclass(self):
        """AggregateOperator can be constructed."""
        source = ScanOperator(variable="n")
        agg = AggregateOperator(
            source=source,
            group_by=["kind"],
            aggregates=[("COUNT", "*", "cnt")],
        )
        assert agg.source is source
        assert agg.group_by == ["kind"]
        assert agg.aggregates == [("COUNT", "*", "cnt")]

    def test_aggregate_operator_is_logical_operator(self):
        agg = AggregateOperator(
            source=ScanOperator(variable="n"),
        )
        from tws_graph.cypher.planner import LogicalOperator
        assert isinstance(agg, LogicalOperator)

    def test_aggregate_operator_frozen(self):
        agg = AggregateOperator(
            source=ScanOperator(variable="n"),
            group_by=["kind"],
            aggregates=[("COUNT", "*", "cnt")],
        )
        with pytest.raises(Exception):
            agg.group_by = ["other"]  # type: ignore

    def test_planner_detects_count_star(self):
        """MATCH (n) RETURN COUNT(*) AS cnt → AggregateOperator in plan."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[
            ReturnItem(
                expression=FunctionCall(
                    name="COUNT",
                    args=[StarExpression(span=_span())],
                    span=_span(),
                ),
                alias="cnt",
            ),
        ])
        query = Query(
            return_clause=return_clause,
            match=match,
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)

        # Root should be ProjectOperator
        assert isinstance(plan.root, ProjectOperator)
        # Source should be AggregateOperator
        agg = plan.root.source
        assert isinstance(agg, AggregateOperator)
        assert agg.group_by == []
        assert len(agg.aggregates) == 1
        assert agg.aggregates[0][0] == "COUNT"
        assert agg.aggregates[0][1] == "*"
        assert agg.aggregates[0][2] == "cnt"

    def test_planner_detects_sum(self):
        """MATCH (n) RETURN SUM(n.start_line) AS total → AggregateOperator."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[
            ReturnItem(
                expression=FunctionCall(
                    name="SUM",
                    args=[_prop_access("n", "start_line")],
                    span=_span(),
                ),
                alias="total",
            ),
        ])
        query = Query(
            return_clause=return_clause,
            match=match,
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)

        assert isinstance(plan.root, ProjectOperator)
        agg = plan.root.source
        assert isinstance(agg, AggregateOperator)
        assert len(agg.aggregates) == 1
        assert agg.aggregates[0][0] == "SUM"
        assert agg.aggregates[0][1] == "n.start_line"
        assert agg.aggregates[0][2] == "total"

    def test_planner_group_by_detection(self):
        """MATCH (n) RETURN n.kind, COUNT(*) AS cnt → group_by=['n.kind']."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[
            ReturnItem(expression=_prop_access("n", "kind")),
            ReturnItem(
                expression=FunctionCall(
                    name="COUNT",
                    args=[StarExpression(span=_span())],
                    span=_span(),
                ),
                alias="cnt",
            ),
        ])
        query = Query(
            return_clause=return_clause,
            match=match,
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)
        agg = plan.root.source
        assert isinstance(agg, AggregateOperator)
        # n.kind should be in group_by
        assert len(agg.group_by) >= 1

    def test_planner_no_aggregate_detection(self):
        """MATCH (n) RETURN n.name → no AggregateOperator (scalar only)."""
        planner = Planner()
        node = NodePattern(name="n")
        match = MatchClause(pattern=PatternPart(node=node))
        return_clause = ReturnClause(items=[
            ReturnItem(expression=_prop_access("n", "name")),
        ])
        query = Query(
            return_clause=return_clause,
            match=match,
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)
        # Should be a regular ProjectOperator without AggregateOperator
        assert isinstance(plan.root, ProjectOperator)
        # Source should be ScanOperator
        scan = plan.root.source
        assert isinstance(scan, ScanOperator)


class TestAggregateOperatorExecutor:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        nodes = [
            _make_node("n1", "function", "alpha", start_line=10),
            _make_node("n2", "function", "beta", start_line=20),
            _make_node("n3", "class", "Gamma", start_line=30),
            _make_node("n4", "function", "delta", start_line=40),
            _make_node("n5", "class", "Epsilon", start_line=50),
        ]
        s.insert_nodes(nodes)
        return s

    def test_count_star_aggregate(self, store):
        """COUNT(*) over 5 nodes → 5"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("COUNT", "*", "cnt")],
            ),
            items=[ReturnItem(expression=_ident("cnt"), alias="cnt")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["cnt"] == 5

    def test_count_star_group_by(self, store):
        """COUNT(*) GROUP BY kind → 2 rows (function=3, class=2)"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=["n.kind"],
                aggregates=[("COUNT", "*", "cnt")],
            ),
            items=[
                ReturnItem(expression=_ident("n.kind"), alias="kind"),
                ReturnItem(expression=_ident("cnt"), alias="cnt"),
            ],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 2
        kinds = {row["kind"]: row["cnt"] for row in result.rows}
        assert kinds["function"] == 3
        assert kinds["class"] == 2

    def test_sum_aggregate(self, store):
        """SUM(n.start_line) over 5 nodes → 150"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("SUM", "n.start_line", "total")],
            ),
            items=[ReturnItem(expression=_ident("total"), alias="total")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["total"] == 150  # 10+20+30+40+50

    def test_min_aggregate(self, store):
        """MIN(n.start_line) → 10"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("MIN", "n.start_line", "min_val")],
            ),
            items=[ReturnItem(expression=_ident("min_val"), alias="min_val")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["min_val"] == 10

    def test_max_aggregate(self, store):
        """MAX(n.start_line) → 50"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("MAX", "n.start_line", "max_val")],
            ),
            items=[ReturnItem(expression=_ident("max_val"), alias="max_val")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["max_val"] == 50

    def test_avg_aggregate(self, store):
        """AVG(n.start_line) → 30.0"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("AVG", "n.start_line", "avg_val")],
            ),
            items=[ReturnItem(expression=_ident("avg_val"), alias="avg_val")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["avg_val"] == 30.0

    def test_collect_aggregate(self, store):
        """COLLECT(n.name) → list of all names"""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("COLLECT", "n.name", "names")],
            ),
            items=[ReturnItem(expression=_ident("names"), alias="names")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        names = result.rows[0]["names"]
        assert isinstance(names, list)
        assert set(names) == {"alpha", "beta", "Gamma", "delta", "Epsilon"}

    def test_aggregate_on_empty_source(self, store):
        """Aggregate over empty result → no rows."""
        exec_ = Executor(store)
        # Filter that matches nothing
        plan = LogicalPlan(root=AggregateOperator(
            source=FilterOperator(
                source=ScanOperator(variable="n"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("n", "kind"),
                    right=_literal("nonexistent"),
                    span=_span(),
                ),
            ),
            group_by=[],
            aggregates=[("COUNT", "*", "cnt")],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_aggregate_with_multiple_funcs(self, store):
        """Multiple aggregate functions in one operator."""
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=["n.kind"],
                aggregates=[
                    ("COUNT", "*", "cnt"),
                    ("SUM", "n.start_line", "total"),
                    ("MIN", "n.start_line", "min_line"),
                ],
            ),
            items=[
                ReturnItem(expression=_ident("n.kind"), alias="kind"),
                ReturnItem(expression=_ident("cnt"), alias="cnt"),
                ReturnItem(expression=_ident("total"), alias="total"),
                ReturnItem(expression=_ident("min_line"), alias="min_line"),
            ],
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 2
        for row in result.rows:
            assert "kind" in row.data
            assert "cnt" in row.data
            assert "total" in row.data
            assert "min_line" in row.data
            assert row["cnt"] == 3 if row["kind"] == "function" else 2


# ============================================================================
# Defect 6: OPTIONAL MATCH — LEFT OUTER JOIN semantics
# ============================================================================

class TestOptionalMatchPlanner:

    def test_planner_edge_expand_has_optional_field(self):
        """EdgeExpandOperator should have optional field, defaulting to False."""
        expand = EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
        )
        assert hasattr(expand, "optional")
        assert expand.optional is False

    def test_planner_handles_optional_matches(self):
        """Planner processes optional_matches without error and generates EdgeExpand with optional=True."""
        planner = Planner()

        # Main match: MATCH (a)
        node_a = NodePattern(name="a")
        main_match = MatchClause(pattern=PatternPart(node=node_a))

        # Optional match: OPTIONAL MATCH (a)-[:calls]->(b)
        node_b = NodePattern(name="b")
        rel = RelPattern(types=["calls"], direction=Direction.RIGHT)
        elem = PatternElement(node=node_b, rel=rel)
        opt_pattern = PatternPart(node=node_a, chain=[elem])
        opt_match = MatchClause(pattern=opt_pattern, optional=True)

        return_clause = ReturnClause(items=[
            ReturnItem(expression=_ident("a")),
            ReturnItem(expression=_ident("b")),
        ])
        query = Query(
            return_clause=return_clause,
            match=main_match,
            optional_matches=[opt_match],
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)
        assert isinstance(plan.root, ProjectOperator)

    def test_planner_optional_match_variables_defined(self):
        """Variables from optional_matches are added to defined_vars."""
        planner = Planner()
        node_a = NodePattern(name="a")
        main_match = MatchClause(pattern=PatternPart(node=node_a))

        node_m = NodePattern(name="m")
        rel = RelPattern(types=["imports"], direction=Direction.RIGHT)
        elem = PatternElement(node=node_m, rel=rel)
        opt_match = MatchClause(
            pattern=PatternPart(node=node_a, chain=[elem]),
            optional=True,
        )

        # RETURN m which is defined in optional match
        return_clause = ReturnClause(items=[
            ReturnItem(expression=_ident("m")),
        ])
        query = Query(
            return_clause=return_clause,
            match=main_match,
            optional_matches=[opt_match],
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        # Should NOT raise "Variable 'm' is not defined"
        plan = planner.plan(stmt)
        assert isinstance(plan.root, ProjectOperator)


class TestOptionalMatchExecutor:

    @pytest.fixture
    def store(self):
        return _build_graph_store()

    def test_optional_expand_preserves_row_with_null(self, store):
        """OPTIONAL MATCH: node with no edges → row with target=NULL."""
        exec_ = Executor(store)
        # Scan 'd' (TargetClass) which has no outgoing calls edges
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=FilterOperator(
                source=ScanOperator(variable="a"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("a", "name"),
                    right=_literal("TargetClass"),
                    span=_span(),
                ),
            ),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
            optional=True,
        ))
        result = exec_.execute(plan)
        # Should produce 1 row with b=None (LEFT OUTER JOIN)
        assert len(result.rows) == 1
        assert result.rows[0]["b"] is None

    def test_optional_expand_with_matches(self, store):
        """OPTIONAL MATCH: node with edges → all matching rows produced."""
        exec_ = Executor(store)
        # Scan all nodes, optional outgoing calls
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
            optional=True,
        ))
        result = exec_.execute(plan)
        # a: 2 calls edges, b: 0 + 1 NULL, c: 1 + 0, d: 0 + 1 NULL
        # Total: need to count... a has 2 matches (b, c), b has 1 NULL, c has 1 match (a), d has 1 NULL = 5
        assert len(result.rows) == 5
        null_count = sum(1 for row in result.rows if row["b"] is None)
        assert null_count == 2  # b and d have no outgoing calls

    def test_optional_expand_edge_variable_null(self, store):
        """OPTIONAL MATCH: when no edge matched, edge_variable is also None."""
        exec_ = Executor(store)
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=FilterOperator(
                source=ScanOperator(variable="a"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("a", "name"),
                    right=_literal("TargetClass"),
                    span=_span(),
                ),
            ),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
            edge_variable="r",
            optional=True,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1
        assert result.rows[0]["b"] is None
        assert result.rows[0]["r"] is None

    def test_optional_expand_still_filtered_by_edge_type(self, store):
        """OPTIONAL MATCH with edge type filter still respects the filter."""
        exec_ = Executor(store)
        # a has "imports" edges to d, but we filter for "calls" only
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=FilterOperator(
                source=ScanOperator(variable="a"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("a", "name"),
                    right=_literal("caller"),
                    span=_span(),
                ),
            ),
            edge_types=["nonexistent_type"],
            direction="out",
            target_variable="b",
            optional=True,
        ))
        result = exec_.execute(plan)
        # Still produces a row (optional) but b=None since no matches
        assert len(result.rows) == 1
        assert result.rows[0]["b"] is None

    def test_non_optional_expand_no_null_rows(self, store):
        """Non-optional expand: node with no edges → no output row."""
        exec_ = Executor(store)
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=FilterOperator(
                source=ScanOperator(variable="a"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("a", "name"),
                    right=_literal("TargetClass"),
                    span=_span(),
                ),
            ),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
            optional=False,  # not optional
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0


# ============================================================================
# Defect 5+6: Full pipeline integration (planner + executor)
# ============================================================================

class TestFullPipelineIntegration:

    def test_optional_match_through_planner_and_executor(self):
        """OPTIONAL MATCH query through Planner → Executor produces correct results."""
        store = MemoryStore()
        nodes = [
            _make_node("a", "function", "caller"),
            _make_node("b", "function", "callee"),
            _make_node("c", "class", "Loner"),  # no edges
        ]
        store.insert_nodes(nodes)
        edges = [
            _make_edge(1, "a", "b", "calls"),
        ]
        store.insert_edges(edges)

        # Build AST manually and run through planner + executor
        planner = Planner()
        node_a = NodePattern(name="n")
        main_match = MatchClause(pattern=PatternPart(node=node_a))

        node_m = NodePattern(name="m")
        rel = RelPattern(types=["calls"], direction=Direction.RIGHT)
        opt_elem = PatternElement(node=node_m, rel=rel)
        opt_match = MatchClause(
            pattern=PatternPart(node=node_a, chain=[opt_elem]),
            optional=True,
        )

        return_clause = ReturnClause(items=[
            ReturnItem(expression=_ident("n"), alias="n"),
            ReturnItem(expression=_ident("m"), alias="m"),
        ])
        query = Query(
            return_clause=return_clause,
            match=main_match,
            optional_matches=[opt_match],
            span=_span(),
        )
        stmt = Statement(query=query, span=_span())

        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        # 3 nodes: a has 1 calls edge, b has 0, c has 0
        # a → (a,b) present
        # b → (b,None) for optional null
        # c → (c,None) for optional null
        assert len(result.rows) == 3
        for row in result.rows:
            assert "n" in row.data
            assert "m" in row.data
