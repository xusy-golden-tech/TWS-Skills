"""Tests for cypher/executor.py — LogicalPlan to ResultSet execution."""

from __future__ import annotations

import pytest

from tws_graph.cypher.ast import (
    BinaryOp,
    FunctionCall,
    Identifier,
    ListLiteral,
    Literal,
    PropertyAccess,
    ReturnItem,
    OrderByItem,
    Span,
    UnaryOp,
    Parameter,
    StarExpression,
    InExpression,
)
from tws_graph.cypher.errors import CypherExecutionError

# Operators from planner
from tws_graph.cypher.planner import (
    LogicalPlan,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    SortOperator,
    LimitOperator,
    DistinctOperator,
)

# Will be filled by implementation
from tws_graph.cypher.executor import Executor, Row, ResultSet

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
    """Create a minimal node record for tests."""
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
    """Create a minimal edge record for tests."""
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
    }
    edge.update(kwargs)
    return edge


def _build_empty_store() -> MemoryStore:
    """Create an empty MemoryStore."""
    return MemoryStore()


def _build_simple_store() -> MemoryStore:
    """Create a MemoryStore with 3 nodes (functions)."""
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main"),
        _make_node("n2", "function", "helper"),
        _make_node("n3", "function", "dispatch"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_labeled_store() -> MemoryStore:
    """Create a MemoryStore with mixed kind nodes."""
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main"),
        _make_node("n2", "class", "MyClass"),
        _make_node("n3", "function", "helper"),
        _make_node("n4", "class", "YourClass"),
        _make_node("n5", "module", "utils"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_graph_store() -> MemoryStore:
    """Create a MemoryStore with nodes and edges for edge-expand tests."""
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
        _make_edge(3, "c", "a", "calls"),       # incoming edge
        _make_edge(4, "a", "d", "imports"),
        _make_edge(5, "b", "a", "references"),   # incoming edge
    ]
    store.insert_edges(edges)
    return store


def _row(**kwargs) -> Row:
    """Create a Row with the given data."""
    return Row(data=dict(kwargs))


# ============================================================================
# Row and ResultSet
# ============================================================================

class TestRow:

    def test_construct_empty(self):
        r = Row()
        assert r.data == {}

    def test_construct_with_data(self):
        r = Row(data={"n": {"name": "main"}})
        assert r.data["n"] == {"name": "main"}

    def test_getitem(self):
        r = Row(data={"n": {"name": "main"}})
        assert r["n"] == {"name": "main"}

    def test_setitem(self):
        r = Row(data={"n": {"name": "main"}})
        r["m"] = {"name": "helper"}
        assert r["m"] == {"name": "helper"}

    def test_get_with_default(self):
        r = Row(data={"n": {"name": "main"}})
        assert r.get("n") == {"name": "main"}
        assert r.get("missing") is None
        assert r.get("missing", "default") == "default"

    def test_missing_key_raises_keyerror(self):
        r = Row(data={"n": {"name": "main"}})
        with pytest.raises(KeyError):
            _ = r["missing"]


class TestResultSet:

    def test_construct_empty(self):
        rs = ResultSet()
        assert rs.columns == []
        assert rs.rows == []
        assert rs.total_count == 0
        assert len(rs) == 0

    def test_construct_with_data(self):
        rows = [Row(data={"n": {"name": "main"}})]
        rs = ResultSet(columns=["n"], rows=rows, total_count=1)
        assert rs.columns == ["n"]
        assert len(rs) == 1
        assert rs.total_count == 1

    def test_len(self):
        rs = ResultSet(rows=[Row(), Row(), Row()], total_count=3)
        assert len(rs) == 3

    def test_iter(self):
        r1, r2 = Row(data={"x": 1}), Row(data={"x": 2})
        rs = ResultSet(rows=[r1, r2])
        items = list(rs)
        assert len(items) == 2
        assert items[0] is r1
        assert items[1] is r2

    def test_getitem(self):
        r1, r2 = Row(data={"x": 1}), Row(data={"x": 2})
        rs = ResultSet(rows=[r1, r2])
        assert rs[0] is r1
        assert rs[1] is r2


# ============================================================================
# ScanOperator
# ============================================================================

class TestScanOperator:

    def test_scan_all_nodes(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n"))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_scan_with_label_function(self):
        store = _build_labeled_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n", label="function"))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_scan_with_label_class(self):
        store = _build_labeled_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n", label="class"))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"MyClass", "YourClass"}

    def test_scan_with_label_no_match(self):
        store = _build_labeled_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n", label="decorator"))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_scan_empty_store(self):
        store = _build_empty_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n"))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_scan_total_count(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n"))
        result = exec_.execute(plan)

        assert result.total_count == 3

    def test_scan_columns(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ScanOperator(variable="n"))
        result = exec_.execute(plan)

        assert "n" in result.columns


# ============================================================================
# FilterOperator
# ============================================================================

class TestFilterOperator:

    def test_filter_equals(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # WHERE n.name = 'main'
        predicate = BinaryOp(
            op="=",
            left=_prop_access("n", "name"),
            right=_literal("main"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "main"

    def test_filter_not_equal(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="<>",
            left=_prop_access("n", "name"),
            right=_literal("main"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert "main" not in names

    def test_filter_less_than(self):
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "a", start_line=5),
            _make_node("n2", "function", "b", start_line=10),
            _make_node("n3", "function", "c", start_line=3),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)
        # WHERE n.start_line < 8
        predicate = BinaryOp(
            op="<",
            left=_prop_access("n", "start_line"),
            right=_literal(8),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"a", "c"}

    def test_filter_greater_than(self):
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "a", start_line=5),
            _make_node("n2", "function", "b", start_line=10),
            _make_node("n3", "function", "c", start_line=3),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)
        predicate = BinaryOp(
            op=">",
            left=_prop_access("n", "start_line"),
            right=_literal(5),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "b"

    def test_filter_and(self):
        store = _build_labeled_store()
        exec_ = Executor(store)
        # WHERE n.kind = 'function' AND n.name = 'main'
        predicate = BinaryOp(
            op="AND",
            left=BinaryOp(
                op="=",
                left=_prop_access("n", "kind"),
                right=_literal("function"),
                span=_span(),
            ),
            right=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("main"),
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
        assert result.rows[0]["n"]["name"] == "main"

    def test_filter_or(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # WHERE n.name = 'main' OR n.name = 'dispatch'
        predicate = BinaryOp(
            op="OR",
            left=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("main"),
                span=_span(),
            ),
            right=BinaryOp(
                op="=",
                left=_prop_access("n", "name"),
                right=_literal("dispatch"),
                span=_span(),
            ),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "dispatch"}

    def test_filter_all_rejected(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="=",
            left=_prop_access("n", "name"),
            right=_literal("nonexistent"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0


# ============================================================================
# ProjectOperator
# ============================================================================

class TestProjectOperator:

    def test_project_single_identifier(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN n
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_ident("n"))],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        assert "n" in result.columns
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_project_with_alias(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN n.name AS func_name
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=_prop_access("n", "name"),
                alias="func_name",
            )],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        assert "func_name" in result.columns
        names = {row["func_name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_project_property_access(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN n.name
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "name"))],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_project_multiple_items(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN n.name, n.kind
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[
                ReturnItem(expression=_prop_access("n", "name"), alias="name"),
                ReturnItem(expression=_prop_access("n", "kind"), alias="kind"),
            ],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        assert result.columns == ["name", "kind"]
        for row in result.rows:
            assert "name" in row.data
            assert "kind" in row.data
            assert row["kind"] == "function"

    def test_project_literal(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN 42 AS answer
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_literal(42), alias="answer")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        for row in result.rows:
            assert row["answer"] == 42

    def test_project_deep_property_access(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN n.file_path
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "file_path"))],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        paths = {row["n.file_path"] for row in result.rows}
        assert paths == {"test/main.py", "test/helper.py", "test/dispatch.py"}


# ============================================================================
# SortOperator
# ============================================================================

class TestSortOperator:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        nodes = [
            _make_node("n1", "function", "c_main"),
            _make_node("n2", "function", "a_helper"),
            _make_node("n3", "function", "b_dispatch"),
        ]
        s.insert_nodes(nodes)
        return s

    def test_sort_asc_single_field(self, store):
        exec_ = Executor(store)
        # RETURN n ORDER BY n.name ASC
        plan = LogicalPlan(root=SortOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_ident("n"))],
            ),
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        ))
        result = exec_.execute(plan)

        names = [row["n"]["name"] for row in result.rows]
        assert names == sorted(names)

    def test_sort_desc_single_field(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=SortOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_ident("n"))],
            ),
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="DESC")],
        ))
        result = exec_.execute(plan)

        names = [row["n"]["name"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    def test_sort_multi_field(self, store):
        exec_ = Executor(store)
        # Same start_line for all, sort by name
        plan = LogicalPlan(root=SortOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_ident("n"))],
            ),
            items=[
                OrderByItem(expression=_prop_access("n", "name"), direction="ASC"),
            ],
        ))
        result = exec_.execute(plan)

        names = [row["n"]["name"] for row in result.rows]
        assert names == ["a_helper", "b_dispatch", "c_main"]

    def test_sort_empty_result(self):
        store = _build_empty_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=SortOperator(
            source=ScanOperator(variable="n"),
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_sort_preserves_row_count(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=SortOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_ident("n"))],
            ),
            items=[OrderByItem(expression=_prop_access("n", "name"), direction="ASC")],
        ))
        result = exec_.execute(plan)

        assert result.total_count == 3


# ============================================================================
# LimitOperator
# ============================================================================

class TestLimitOperator:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        nodes = [
            _make_node("n1", "function", "a"),
            _make_node("n2", "function", "b"),
            _make_node("n3", "function", "c"),
            _make_node("n4", "function", "d"),
            _make_node("n5", "function", "e"),
        ]
        s.insert_nodes(nodes)
        return s

    def test_limit_only(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
            skip=0,
            limit=2,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2

    def test_skip_only(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
            skip=3,
            limit=None,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2  # 5 - 3 = 2

    def test_skip_and_limit(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
            skip=2,
            limit=2,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2

    def test_skip_exceeds_count(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
            skip=10,
            limit=None,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_limit_zero(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
            skip=0,
            limit=0,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_no_skip_no_limit(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=ScanOperator(variable="n"),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 5


# ============================================================================
# DistinctOperator
# ============================================================================

class TestDistinctOperator:

    def test_distinct_removes_duplicates(self):
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
            _make_node("n2", "function", "main"),  # duplicate kind
            _make_node("n3", "function", "helper"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)
        # RETURN DISTINCT n.kind
        plan = LogicalPlan(root=DistinctOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_prop_access("n", "kind"))],
            ),
        ))
        result = exec_.execute(plan)

        # All nodes have kind="function" → dedup to 1
        assert len(result.rows) == 1
        assert result.rows[0]["n.kind"] == "function"

    def test_distinct_no_duplicates(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=DistinctOperator(
            source=ScanOperator(variable="n"),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3  # all different

    def test_distinct_empty(self):
        store = _build_empty_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=DistinctOperator(
            source=ScanOperator(variable="n"),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_distinct_on_projected_column(self):
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main", file_path="a.py"),
            _make_node("n2", "function", "main", file_path="a.py"),  # same
            _make_node("n3", "function", "helper", file_path="b.py"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)
        # RETURN DISTINCT n.name
        plan = LogicalPlan(root=DistinctOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_prop_access("n", "name"))],
            ),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2  # "main" and "helper"
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper"}


# ============================================================================
# EdgeExpandOperator
# ============================================================================

class TestEdgeExpandOperator:

    @pytest.fixture
    def store(self):
        return _build_graph_store()

    def test_edge_expand_out(self, store):
        exec_ = Executor(store)
        # MATCH (a)-[:calls]->(b) RETURN b -- scan all 4 nodes, expand
        # outgoing calls edges from each. Total: a→b, a→c, c→a = 3.
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        targets = {row["b"]["name"] for row in result.rows}
        assert targets == {"callee1", "callee2", "caller"}

    def test_edge_expand_in(self, store):
        exec_ = Executor(store)
        # MATCH (a)<-[:calls]-(b) -- scan all nodes.
        # Incoming calls: a from c, b from a, c from a = 3.
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="in",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3

    def test_edge_expand_both(self, store):
        exec_ = Executor(store)
        # MATCH (a)-[:calls]-(b) both directions.
        # a: 2out+1in, b: 0out+1in, c: 1out+1in, d: 0 = 6.
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="both",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 6

    def test_edge_expand_with_edge_variable(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="out",
            edge_variable="r",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        for row in result.rows:
            assert "r" in row.data
            assert row["r"]["kind"] == "calls"

    def test_edge_expand_no_matching_edges(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["contains"],
            direction="out",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_edge_expand_from_node_without_edges(self, store):
        exec_ = Executor(store)
        # Scan all nodes, outgoing calls: a→b, a→c, c→a = 3.
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="b"),
            edge_types=["calls"],
            direction="out",
            target_variable="target",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3

    def test_edge_expand_no_type_filter(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=[],  # all types
            direction="out",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        # All outgoing from each node: a:3, b:1, c:1, d:0 = 5
        assert len(result.rows) == 5

    def test_edge_expand_preserves_existing_variables(self, store):
        exec_ = Executor(store)
        # Scan-only then expand
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="n"),
            edge_types=["calls"],
            direction="out",
            target_variable="m",
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert "n" in row.data  # source variable
            assert "m" in row.data  # target variable

    def test_edge_expand_with_filtered_source(self, store):
        exec_ = Executor(store)
        # Only scan 'b' node, expand incoming
        plan = LogicalPlan(root=EdgeExpandOperator(
            source=FilterOperator(
                source=ScanOperator(variable="n"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("n", "name"),
                    right=_literal("callee1"),
                    span=_span(),
                ),
            ),
            edge_types=["references"],
            direction="in",
            target_variable="source",
        ))
        result = exec_.execute(plan)

        # b has an incoming references edge from... actually b has outgoing references to a
        # Let me check: edge 5 is b→a references, so incoming from b means we're looking
        # for edges where target=b and source is something else.
        # Since b is the source of that edge, not target, incoming refs to b = 0
        assert len(result.rows) == 0


# ============================================================================
# Full chain tests
# ============================================================================

class TestFullChain:

    def test_scan_filter_project(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # MATCH (n) WHERE n.name = 'main' RETURN n.name AS name
        plan = LogicalPlan(root=ProjectOperator(
            source=FilterOperator(
                source=ScanOperator(variable="n"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("n", "name"),
                    right=_literal("main"),
                    span=_span(),
                ),
            ),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["name"] == "main"
        assert result.columns == ["name"]

    def test_scan_filter_project_sort_limit(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # Sort by name ASC, skip 0, limit 2
        plan = LogicalPlan(root=LimitOperator(
            source=SortOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
                ),
                items=[OrderByItem(expression=_ident("name"), direction="ASC")],
            ),
            skip=0,
            limit=2,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)[:2]

    def test_scan_filter_project_sort_limit_with_skip(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=LimitOperator(
            source=SortOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
                ),
                items=[OrderByItem(expression=_ident("name"), direction="ASC")],
            ),
            skip=1,
            limit=1,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        # skip the first (alphabetically = "dispatch"), keep 1 = "helper"
        assert result.rows[0]["name"] in {"dispatch", "helper"}

    def test_full_chain_verify_operator_order(self):
        """Verify 5-operator chain: Scan → Filter → Project → Sort → Limit."""
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "zulu"),
            _make_node("n2", "class", "alpha"),
            _make_node("n3", "function", "beta"),
            _make_node("n4", "class", "gamma"),
            _make_node("n5", "function", "delta"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)

        plan = LogicalPlan(root=LimitOperator(
            source=SortOperator(
                source=ProjectOperator(
                    source=FilterOperator(
                        source=ScanOperator(variable="n"),
                        predicate=BinaryOp(
                            op="=",
                            left=_prop_access("n", "kind"),
                            right=_literal("function"),
                            span=_span(),
                        ),
                    ),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="func_name")],
                ),
                items=[OrderByItem(expression=_ident("func_name"), direction="ASC")],
            ),
            skip=0,
            limit=2,
        ))
        result = exec_.execute(plan)

        # Should only return function nodes (zulu, beta, delta), sorted ASC, top 2
        assert len(result.rows) == 2
        names = [row["func_name"] for row in result.rows]
        assert names == ["beta", "delta"]

    def test_full_chain_with_distinct(self):
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
            _make_node("n2", "function", "main"),  # duplicate name
            _make_node("n3", "function", "helper"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)

        # MATCH (n) RETURN DISTINCT n.name ORDER BY n.name
        plan = LogicalPlan(root=SortOperator(
            source=DistinctOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
                ),
            ),
            items=[OrderByItem(expression=_ident("name"), direction="ASC")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = [row["name"] for row in result.rows]
        assert names == ["helper", "main"]


# ============================================================================
# Expression Evaluator — Identifier / Literal / PropertyAccess
# ============================================================================

class TestExpressionEvaluator:

    def test_evaluate_identifier(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_ident("n"))],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        for row in result.rows:
            assert isinstance(row["n"], dict)
            assert "name" in row["n"]

    def test_evaluate_property_access(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "kind"))],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["n.kind"] == "function"

    def test_evaluate_chained_property_access(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "file_path"))],
        ))
        result = exec_.execute(plan)

        paths = {row["n.file_path"] for row in result.rows}
        assert len(paths) == 3

    def test_evaluate_literal_int(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_literal(42), alias="val")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["val"] == 42

    def test_evaluate_literal_str(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_literal("hello"), alias="val")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["val"] == "hello"

    def test_evaluate_literal_bool(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_literal(True), alias="val")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["val"] is True

    def test_evaluate_literal_none(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_literal(None), alias="val")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["val"] is None

    def test_evaluate_undefined_identifier(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        # RETURN m where m was never defined
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_ident("m"))],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)

    def test_evaluate_parameter_unsupported(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        param = Parameter(name="$param", span=_span())
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=param)],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)


# ============================================================================
# Expression Evaluator — Binary Operators
# ============================================================================

class TestBinaryOperators:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        nodes = [
            _make_node("n1", "function", "alpha", start_line=10),
            _make_node("n2", "function", "beta", start_line=20),
            _make_node("n3", "function", "gamma", start_line=30),
        ]
        s.insert_nodes(nodes)
        return s

    def test_eq_true(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="=",
            left=_prop_access("n", "name"),
            right=_literal("beta"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "beta"

    def test_neq(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="<>",
            left=_prop_access("n", "name"),
            right=_literal("beta"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        names = {row["n"]["name"] for row in result.rows}
        assert "beta" not in names

    def test_lt(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="<",
            left=_prop_access("n", "start_line"),
            right=_literal(25),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"alpha", "beta"}

    def test_gt(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op=">",
            left=_prop_access("n", "start_line"),
            right=_literal(25),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"gamma"}

    def test_lte(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op="<=",
            left=_prop_access("n", "start_line"),
            right=_literal(20),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"alpha", "beta"}  # 10 <= 20, 20 <= 20

    def test_gte(self, store):
        exec_ = Executor(store)
        predicate = BinaryOp(
            op=">=",
            left=_prop_access("n", "start_line"),
            right=_literal(20),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"beta", "gamma"}

    def test_add(self, store):
        exec_ = Executor(store)
        # RETURN n.start_line + 5
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="+",
                    left=_prop_access("n", "start_line"),
                    right=_literal(5),
                    span=_span(),
                ),
                alias="offset",
            )],
        ))
        result = exec_.execute(plan)

        values = {row["offset"] for row in result.rows}
        assert values == {15, 25, 35}

    def test_sub(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="-",
                    left=_prop_access("n", "start_line"),
                    right=_literal(5),
                    span=_span(),
                ),
                alias="offset",
            )],
        ))
        result = exec_.execute(plan)

        values = {row["offset"] for row in result.rows}
        assert values == {5, 15, 25}

    def test_mul(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="*",
                    left=_prop_access("n", "start_line"),
                    right=_literal(2),
                    span=_span(),
                ),
                alias="doubled",
            )],
        ))
        result = exec_.execute(plan)

        values = {row["doubled"] for row in result.rows}
        assert values == {20, 40, 60}

    def test_div(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="/",
                    left=_prop_access("n", "start_line"),
                    right=_literal(10),
                    span=_span(),
                ),
                alias="div_result",
            )],
        ))
        result = exec_.execute(plan)

        values = {row["div_result"] for row in result.rows}
        assert values == {1.0, 2.0, 3.0}

    def test_mod(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="%",
                    left=_prop_access("n", "start_line"),
                    right=_literal(7),
                    span=_span(),
                ),
                alias="mod_result",
            )],
        ))
        result = exec_.execute(plan)

        values = {row["mod_result"] for row in result.rows}
        assert values == {3, 6, 2}  # 10%7=3, 20%7=6, 30%7=2

    def test_pow(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="^",
                    left=_literal(3),
                    right=_literal(2),
                    span=_span(),
                ),
                alias="pow_result",
            )],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["pow_result"] == 9  # 3^2 = 9

    def test_and_short_circuit(self, store):
        exec_ = Executor(store)
        # true AND false → false
        predicate = BinaryOp(
            op="AND",
            left=_literal(True),
            right=_literal(False),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_or_short_circuit(self, store):
        exec_ = Executor(store)
        # true OR false → true
        predicate = BinaryOp(
            op="OR",
            left=_literal(True),
            right=_literal(False),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 3

    def test_regex_match(self, store):
        exec_ = Executor(store)
        # WHERE n.name =~ 'alpha|beta'
        predicate = BinaryOp(
            op="=~",
            left=_prop_access("n", "name"),
            right=_literal(r"alpha|beta"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"alpha", "beta"}

    def test_regex_match_case_sensitive(self, store):
        exec_ = Executor(store)
        # WHERE n.name =~ 'ALPHA' (alpha does not match ALPHA case-sensitive)
        predicate = BinaryOp(
            op="=~",
            left=_prop_access("n", "name"),
            right=_literal("ALPHA"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_in_expression(self, store):
        exec_ = Executor(store)
        # WHERE n.name IN ['alpha', 'beta']
        predicate = BinaryOp(
            op="IN",
            left=_prop_access("n", "name"),
            right=ListLiteral(
                elements=[_literal("alpha"), _literal("beta")],
                span=_span(),
            ),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)

        names = {row["n"]["name"] for row in result.rows}
        assert names == {"alpha", "beta"}

    def test_null_comparison_always_false(self, store):
        exec_ = Executor(store)
        # WHERE None = 'something' (in real use: where property_that_is_null = 'x')
        predicate = BinaryOp(
            op="=",
            left=_literal(None),
            right=_literal("something"),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0


# ============================================================================
# Expression Evaluator — Unary Operators
# ============================================================================

class TestUnaryOperators:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        s.insert_nodes([_make_node("n1", "function", "test")])
        return s

    def test_unary_not_true(self, store):
        exec_ = Executor(store)
        # WHERE NOT (1 = 2) → true
        predicate = UnaryOp(
            op="NOT",
            operand=BinaryOp(
                op="=",
                left=_literal(1),
                right=_literal(2),
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

    def test_unary_not_false(self, store):
        exec_ = Executor(store)
        # WHERE NOT (1 = 1) → false
        predicate = UnaryOp(
            op="NOT",
            operand=BinaryOp(
                op="=",
                left=_literal(1),
                right=_literal(1),
                span=_span(),
            ),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_unary_negate(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=UnaryOp(op="-", operand=_literal(5), span=_span()),
                alias="neg",
            )],
        ))
        result = exec_.execute(plan)
        assert result.rows[0]["neg"] == -5

    def test_is_null_true(self, store):
        exec_ = Executor(store)
        predicate = UnaryOp(
            op="IS NULL",
            operand=_literal(None),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1  # all pass

    def test_is_null_false(self, store):
        exec_ = Executor(store)
        predicate = UnaryOp(
            op="IS NULL",
            operand=_literal(42),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0

    def test_is_not_null_true(self, store):
        exec_ = Executor(store)
        predicate = UnaryOp(
            op="IS NOT NULL",
            operand=_literal(42),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 1

    def test_is_not_null_false(self, store):
        exec_ = Executor(store)
        predicate = UnaryOp(
            op="IS NOT NULL",
            operand=_literal(None),
            span=_span(),
        )
        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=predicate,
        ))
        result = exec_.execute(plan)
        assert len(result.rows) == 0


# ============================================================================
# Built-in Function Calls
# ============================================================================

class TestFunctionCalls:

    @pytest.fixture
    def store(self):
        s = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
            _make_node("n2", "function", "Helper"),
            _make_node("n3", "function", "dispatch"),
        ]
        s.insert_nodes(nodes)
        return s

    def test_to_upper(self, store):
        exec_ = Executor(store)
        # RETURN toUpper(n.name)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="toUpper",
                    args=[_prop_access("n", "name")],
                    span=_span(),
                ),
                alias="upper_name",
            )],
        ))
        result = exec_.execute(plan)

        names = {row["upper_name"] for row in result.rows}
        assert names == {"MAIN", "HELPER", "DISPATCH"}

    def test_to_lower(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="toLower",
                    args=[_prop_access("n", "name")],
                    span=_span(),
                ),
                alias="lower_name",
            )],
        ))
        result = exec_.execute(plan)

        names = {row["lower_name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_to_string(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="toString",
                    args=[_prop_access("n", "start_line")],
                    span=_span(),
                ),
                alias="str_val",
            )],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert isinstance(row["str_val"], str)

    def test_coalesce_first_not_null(self, store):
        exec_ = Executor(store)
        # coalesce(n.name, 'default') → always n.name
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="coalesce",
                    args=[_prop_access("n", "name"), _literal("default")],
                    span=_span(),
                ),
                alias="resolved",
            )],
        ))
        result = exec_.execute(plan)

        names = {row["resolved"] for row in result.rows}
        assert names == {"main", "Helper", "dispatch"}

    def test_coalesce_all_null(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="coalesce",
                    args=[_literal(None), _literal(None)],
                    span=_span(),
                ),
                alias="resolved",
            )],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["resolved"] is None

    def test_type_function(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="type",
                    args=[_prop_access("n", "name")],
                    span=_span(),
                ),
                alias="type_val",
            )],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["type_val"] == "str"

    def test_unknown_function(self, store):
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="unknownFunc",
                    args=[_ident("n")],
                    span=_span(),
                ),
                alias="bad",
            )],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)


# ============================================================================
# Error Handling
# ============================================================================

class TestErrorHandling:

    def test_parameter_evaluation(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        param = Parameter(name="$p", span=_span())
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=param)],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)

    def test_star_expression_evaluation(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        star = StarExpression(span=_span())
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=star)],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)

    def test_division_by_zero(self):
        store = _build_simple_store()
        exec_ = Executor(store)
        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=BinaryOp(
                    op="/",
                    left=_literal(1),
                    right=_literal(0),
                    span=_span(),
                ),
                alias="result",
            )],
        ))
        with pytest.raises(CypherExecutionError):
            exec_.execute(plan)
