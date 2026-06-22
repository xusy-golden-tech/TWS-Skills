"""Tests for store/memory_store.py — MemoryStore in-memory graph implementation.

Covers:
    1. Initialization & Store ABC compliance
    2. Node CRUD (insert, lookup, batch, delete, iter)
    3. Edge CRUD (insert, lookup, batch, update, delete, adjacency)
    4. Graph traversal (neighbors, batch neighbors, BFS paths)
    5. File management
    6. Search (FTS, def index, field-qualified)
    7. Unresolved references
    8. Transaction management (no-op semantics)
    9. Batch operations (flush)
    10. Maintenance/statistics
    11. load_from
    12. Edge cases & error handling
    13. Adjacency table integrity
"""

import pytest

from tws_graph.store.interface import Store
from tws_graph.store.memory_store import MemoryStore
from tws_graph.store.exceptions import (
    StoreClosedError,
    TransactionError,
    EdgeNotFoundError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_node(id, file_path="src/test.py", kind="function", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    node = {
        "id": id,
        "kind": kind,
        "name": f"name_of_{id}",
        "qualified_name": f"{file_path}::{kind}.name_of_{id}",
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 10,
        "signature": None,
        "docstring": None,
        "visibility": None,
        "is_abstract": 0,
        "is_exported": 0,
        "decorators": None,
        "framework": None,
        "properties": None,
        "updated_at": 0,
    }
    node.update(overrides)
    return node


def _make_edge(source, target, kind="calls", **overrides):
    """Create a minimal valid EdgeRecord for testing."""
    edge = {
        "source": source,
        "target": target,
        "target_text": None,
        "kind": kind,
        "source_loc": None,
        "provenance": "tree-sitter",
        "properties": None,
    }
    edge.update(overrides)
    return edge


@pytest.fixture
def store():
    """Return a fresh empty MemoryStore."""
    return MemoryStore()


@pytest.fixture
def populated_store():
    """Return a MemoryStore with 3 nodes and 3 edges."""
    s = MemoryStore()
    s.insert_node(_make_node("n1", kind="function"))
    s.insert_node(_make_node("n2", kind="class"))
    s.insert_node(_make_node("n3", kind="method"))
    s.insert_edge(_make_edge("n1", "n2", kind="calls"))
    s.insert_edge(_make_edge("n2", "n3", kind="contains"))
    s.insert_edge(_make_edge("n1", "n3", kind="calls"))
    s.upsert_file("src/test.py", "abc123", "python", node_count=3, size=100, modified_at=1000)
    return s


# =============================================================================
# 1. Initialization & Store ABC compliance
# =============================================================================

class TestInitialization:
    """Verify MemoryStore is a valid Store implementation."""

    def test_is_store_instance(self, store):
        assert isinstance(store, Store)

    def test_can_instantiate(self):
        s = MemoryStore()
        assert s is not None

    def test_initial_state_empty(self, store):
        assert store.count_nodes() == 0
        assert store.count_edges() == 0
        assert store.stats()["node_count"] == 0
        assert store.stats()["edge_count"] == 0
        assert store.stats()["file_count"] == 0
        assert store.stats()["unresolved_count"] == 0

    def test_initial_not_closed(self, store):
        # _closed is private; test via method call that should NOT raise
        store.count_nodes()  # should not raise StoreClosedError


# =============================================================================
# 2. Node CRUD
# =============================================================================

class TestNodeInsert:
    """Tests for insert_node and insert_nodes."""

    def test_insert_single_node(self, store):
        node = _make_node("n1")
        store.insert_node(node)
        assert store.count_nodes() == 1

    def test_insert_single_node_retrievable(self, store):
        node = _make_node("n1", name="myFunc")
        store.insert_node(node)
        retrieved = store.get_node_by_id("n1")
        assert retrieved is not None
        assert retrieved["name"] == "myFunc"
        assert retrieved["kind"] == "function"

    def test_insert_node_replace_on_duplicate_id(self, store):
        node1 = _make_node("n1", name="old_name")
        node2 = _make_node("n1", name="new_name")
        store.insert_node(node1)
        store.insert_node(node2)
        assert store.count_nodes() == 1
        assert store.get_node_by_id("n1")["name"] == "new_name"

    def test_insert_nodes_batch(self, store):
        nodes = [
            _make_node("n1"),
            _make_node("n2"),
            _make_node("n3"),
        ]
        store.insert_nodes(nodes)
        assert store.count_nodes() == 3

    def test_insert_node_missing_required_fields(self, store):
        with pytest.raises(ValueError):
            store.insert_node({"id": "n1"})  # missing name, kind, etc.

    def test_insert_nodes_with_invalid_node(self, store):
        with pytest.raises(ValueError):
            store.insert_nodes([
                _make_node("n1"),
                {"id": "bad"},  # missing required fields
            ])


class TestNodeLookup:
    """Tests for get_node_by_id and get_nodes_by_ids."""

    def test_get_existing_node(self, store):
        store.insert_node(_make_node("n1"))
        node = store.get_node_by_id("n1")
        assert node is not None
        assert node["id"] == "n1"

    def test_get_nonexistent_node(self, store):
        assert store.get_node_by_id("nonexistent") is None

    def test_get_nodes_by_ids_returns_mapping(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        result = store.get_nodes_by_ids(["n1", "n2", "n3"])
        assert "n1" in result
        assert "n2" in result
        assert "n3" not in result  # nonexistent omitted

    def test_get_nodes_by_ids_all_missing(self, store):
        result = store.get_nodes_by_ids(["nx", "ny"])
        assert result == {}


class TestNodeDeletion:
    """Tests for delete_nodes_by_file."""

    def test_delete_all_nodes_by_file(self, store):
        store.insert_node(_make_node("n1", file_path="src/a.py"))
        store.insert_node(_make_node("n2", file_path="src/a.py"))
        store.insert_node(_make_node("n3", file_path="src/b.py"))
        store.delete_nodes_by_file("src/a.py")
        assert store.count_nodes() == 1
        assert store.get_node_by_id("n1") is None
        assert store.get_node_by_id("n2") is None
        assert store.get_node_by_id("n3") is not None

    def test_delete_nodes_by_file_with_kind_filter(self, store):
        store.insert_node(_make_node("n1", file_path="src/a.py", kind="function"))
        store.insert_node(_make_node("n2", file_path="src/a.py", kind="class"))
        store.delete_nodes_by_file("src/a.py", kind="function")
        assert store.count_nodes() == 1
        assert store.get_node_by_id("n1") is None
        assert store.get_node_by_id("n2") is not None

    def test_delete_nodes_by_file_nonexistent(self, store):
        # No-op, no exception
        store.delete_nodes_by_file("nonexistent.py")
        assert store.count_nodes() == 0

    def test_delete_nodes_by_file_cascades_to_edges(self, store):
        store.insert_node(_make_node("n1", file_path="src/a.py"))
        store.insert_node(_make_node("n2", file_path="src/b.py"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.delete_nodes_by_file("src/a.py")
        assert store.count_edges() == 0


class TestNodeIteration:
    """Tests for iter_nodes_by_kind, iter_all_nodes, iter_nodes_by_file."""

    def test_iter_all_nodes(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        nodes = list(store.iter_all_nodes())
        assert len(nodes) == 2

    def test_iter_nodes_by_kind(self, store):
        store.insert_node(_make_node("n1", kind="function"))
        store.insert_node(_make_node("n2", kind="class"))
        store.insert_node(_make_node("n3", kind="function"))
        funcs = list(store.iter_nodes_by_kind("function"))
        assert len(funcs) == 2
        assert all(n["kind"] == "function" for n in funcs)

    def test_iter_nodes_by_kind_empty(self, store):
        result = list(store.iter_nodes_by_kind("nonexistent"))
        assert result == []

    def test_iter_nodes_by_file(self, store):
        store.insert_node(_make_node("n1", file_path="src/a.py"))
        store.insert_node(_make_node("n2", file_path="src/a.py"))
        store.insert_node(_make_node("n3", file_path="src/b.py"))
        nodes = list(store.iter_nodes_by_file("src/a.py"))
        assert len(nodes) == 2

    def test_iter_nodes_by_file_empty_raises(self, store):
        with pytest.raises(ValueError):
            list(store.iter_nodes_by_file(""))


class TestUpdateNodeProperty:
    """Tests for update_node_property."""

    def test_merge_update_properties(self, store):
        store.insert_node(_make_node("n1", properties='{"key1": "val1"}'))
        store.update_node_property("n1", {"key2": "val2", "key1": "new_val"})
        import json
        props = json.loads(store.get_node_by_id("n1")["properties"])
        assert props["key1"] == "new_val"
        assert props["key2"] == "val2"

    def test_update_node_property_nonexistent(self, store):
        with pytest.raises(KeyError):
            store.update_node_property("nonexistent", {"key": "val"})

    def test_update_node_property_no_existing_properties(self, store):
        store.insert_node(_make_node("n1"))
        store.update_node_property("n1", {"new_key": "new_val"})
        import json
        props = json.loads(store.get_node_by_id("n1")["properties"])
        assert props["new_key"] == "new_val"


# =============================================================================
# 3. Edge CRUD
# =============================================================================

class TestEdgeInsert:
    """Tests for insert_edge and insert_edges."""

    def test_insert_single_edge(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        assert store.count_edges() == 1

    def test_insert_edge_auto_assigns_id(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = store.get_outgoing_edges("n1")
        assert edges[0]["id"] == 1

    def test_insert_edge_id_increments(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.insert_edge(_make_edge("n2", "n3"))
        edges = store.get_outgoing_edges("n2")
        assert edges[0]["id"] == 2

    def test_insert_edges_batch(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        edges = [
            _make_edge("n1", "n2"),
            _make_edge("n2", "n3"),
        ]
        store.insert_edges(edges)
        assert store.count_edges() == 2

    def test_insert_edge_missing_required_fields(self, store):
        with pytest.raises(ValueError):
            store.insert_edge({"source": "n1"})  # missing target, kind

    def test_insert_edge_skip_nonexistent_source(self, store):
        """Edge with source not in _nodes should be skipped."""
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))  # source n1 doesn't exist
        assert store.count_edges() == 0

    def test_insert_edge_duplicate_is_ignored(self, store):
        """Duplicate (source, target, kind) triple should be skipped (INSERT OR IGNORE)."""
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))  # duplicate
        assert store.count_edges() == 1

    def test_insert_edge_duplicate_different_kind_stored(self, store):
        """Different kind means different edge."""
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n1", "n2", kind="references"))
        assert store.count_edges() == 2


class TestEdgeLookup:
    """Tests for get_outgoing_edges, get_incoming_edges, get_edges_between."""

    def test_get_outgoing_edges(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = store.get_outgoing_edges("n1")
        assert len(edges) == 1
        assert edges[0]["source"] == "n1"
        assert edges[0]["target"] == "n2"

    def test_get_outgoing_edges_empty(self, store):
        assert store.get_outgoing_edges("n1") == []

    def test_get_outgoing_edges_empty_source_raises(self, store):
        with pytest.raises(ValueError):
            store.get_outgoing_edges("")

    def test_get_outgoing_edges_with_kind_filter(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n1", "n3", kind="references"))
        edges = store.get_outgoing_edges("n1", kinds=["calls"])
        assert len(edges) == 1
        assert edges[0]["kind"] == "calls"

    def test_get_incoming_edges(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = store.get_incoming_edges("n2")
        assert len(edges) == 1
        assert edges[0]["source"] == "n1"
        assert edges[0]["target"] == "n2"

    def test_get_incoming_edges_empty_source_raises(self, store):
        with pytest.raises(ValueError):
            store.get_incoming_edges("")

    def test_get_incoming_edges_with_kind_filter(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n3", "n2", kind="references"))
        edges = store.get_incoming_edges("n2", kinds=["references"])
        assert len(edges) == 1
        assert edges[0]["kind"] == "references"

    def test_get_edges_between(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = store.get_edges_between("n1", "n2")
        assert len(edges) == 1

    def test_get_edges_between_empty(self, store):
        assert store.get_edges_between("n1", "n2") == []

    def test_get_edges_between_empty_params_raises(self, store):
        with pytest.raises(ValueError):
            store.get_edges_between("", "n2")
        with pytest.raises(ValueError):
            store.get_edges_between("n1", "")


class TestEdgeUpdate:
    """Tests for update_edge_target and update_edge_provenance."""

    def test_update_edge_target(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.update_edge_target(1, "n3", provenance="resolved")
        edges = store.get_outgoing_edges("n1")
        assert edges[0]["target"] == "n3"
        assert edges[0]["provenance"] == "resolved"

    def test_update_edge_target_nonexistent_edge(self, store):
        with pytest.raises(EdgeNotFoundError):
            store.update_edge_target(999, "n2")

    def test_update_edge_target_empty_target(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        with pytest.raises(ValueError):
            store.update_edge_target(1, "")

    def test_update_edge_provenance(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2", provenance="tree-sitter"))
        store.update_edge_provenance(1, "ambiguous")
        edges = store.get_outgoing_edges("n1")
        assert edges[0]["provenance"] == "ambiguous"
        assert edges[0]["target"] == "n2"  # target unchanged

    def test_update_edge_provenance_nonexistent_edge(self, store):
        with pytest.raises(EdgeNotFoundError):
            store.update_edge_provenance(999, "resolved")

    def test_update_edge_provenance_empty(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        with pytest.raises(ValueError):
            store.update_edge_provenance(1, "")


class TestEdgeDeletion:
    """Tests for delete_edges_by_source."""

    def test_delete_edges_by_source(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.insert_edge(_make_edge("n1", "n3"))
        store.delete_edges_by_source("n1")
        assert store.count_edges() == 0
        assert store.get_outgoing_edges("n1") == []

    def test_delete_edges_by_source_nonexistent(self, store):
        # No-op
        store.delete_edges_by_source("nonexistent")
        assert store.count_edges() == 0

    def test_delete_edges_by_source_cleans_adjacency(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.delete_edges_by_source("n1")
        # incoming should also be cleaned
        assert store.get_incoming_edges("n2") == []


class TestEdgeIteration:
    """Tests for iter_all_edges, iter_edges, iter_edges_from, get_dangling_edges."""

    def test_iter_all_edges(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = list(store.iter_all_edges())
        assert len(edges) == 1

    def test_iter_edges_with_kind_filter(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n2", "n3", kind="contains"))
        edges = list(store.iter_edges(kind="calls"))
        assert len(edges) == 1
        assert edges[0]["kind"] == "calls"

    def test_iter_edges_with_source_info(self, store):
        store.insert_node(_make_node("n1", kind="function", file_path="src/a.py", language="python"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        edges = list(store.iter_edges(kind="calls", with_source_info=True))
        assert len(edges) == 1
        assert edges[0]["source_file"] == "src/a.py"
        assert edges[0]["source_language"] == "python"

    def test_iter_edges_from_out(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = list(store.iter_edges_from("n1", direction="out"))
        assert len(edges) == 1
        assert edges[0]["source"] == "n1"

    def test_iter_edges_from_in(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = list(store.iter_edges_from("n2", direction="in"))
        assert len(edges) == 1
        assert edges[0]["target"] == "n2"

    def test_iter_edges_from_both(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.insert_edge(_make_edge("n3", "n1"))
        edges = list(store.iter_edges_from("n1", direction="both"))
        assert len(edges) == 2

    def test_iter_edges_from_empty_node(self, store):
        with pytest.raises(ValueError):
            list(store.iter_edges_from(""))

    def test_get_dangling_edges(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "", target_text="some_target"))
        store.insert_edge(_make_edge("n1", "n2"))  # normal edge
        dangling = store.get_dangling_edges()
        assert len(dangling) == 1
        assert dangling[0]["target_text"] == "some_target"

    def test_get_dangling_edges_with_kind(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "", kind="calls", target_text="foo"))
        store.insert_edge(_make_edge("n1", "", kind="references", target_text="bar"))
        dangling = store.get_dangling_edges(kind="calls")
        assert len(dangling) == 1
        assert dangling[0]["kind"] == "calls"


# =============================================================================
# 4. Graph traversal
# =============================================================================

class TestGetNeighbors:
    """Tests for get_neighbors."""

    def test_get_neighbors_out(self, populated_store):
        neighbors = populated_store.get_neighbors("n1", direction="out")
        assert len(neighbors) == 2  # n1 -> n2 (calls), n1 -> n3 (calls)

    def test_get_neighbors_in(self, populated_store):
        neighbors = populated_store.get_neighbors("n3", direction="in")
        assert len(neighbors) == 2  # n2 -> n3, n1 -> n3

    def test_get_neighbors_both(self, populated_store):
        neighbors = populated_store.get_neighbors("n2", direction="both")
        assert len(neighbors) == 2  # n1 -> n2 (in), n2 -> n3 (out)

    def test_get_neighbors_with_kind_filter(self, populated_store):
        neighbors = populated_store.get_neighbors("n1", kinds=["calls"], direction="out")
        assert len(neighbors) == 2

    def test_get_neighbors_empty_node_raises(self, store):
        with pytest.raises(ValueError):
            store.get_neighbors("")

    def test_get_neighbors_returns_correct_format(self, populated_store):
        neighbors = populated_store.get_neighbors("n1", direction="out")
        for n in neighbors:
            assert "node" in n
            assert "edge" in n
            assert "direction" in n
            assert n["direction"] in ("out", "in")

    def test_get_neighbors_nonexistent_node(self, store):
        result = store.get_neighbors("nonexistent")
        assert result == []


class TestGetNeighborsBatch:
    """Tests for get_neighbors_batch."""

    def test_get_neighbors_batch(self, populated_store):
        result = populated_store.get_neighbors_batch(["n1", "n2"])
        assert "n1" in result
        assert "n2" in result
        assert len(result["n1"]) == 2  # n1 -> n2, n1 -> n3

    def test_get_neighbors_batch_nonexistent(self, populated_store):
        result = populated_store.get_neighbors_batch(["n1", "nonexistent"])
        assert "n1" in result
        assert result["nonexistent"] == []

    def test_get_neighbors_batch_empty_list(self, store):
        with pytest.raises(ValueError):
            store.get_neighbors_batch([])


class TestFindPaths:
    """Tests for find_paths."""

    def test_find_paths_direct_connection(self, populated_store):
        path = populated_store.find_paths("n1", "n2")
        assert path is not None
        assert len(path) == 2  # n1 -> n2

    def test_find_paths_two_hops(self, populated_store):
        path = populated_store.find_paths("n1", "n3")
        assert path is not None
        # Path should be n1 -> n3 (direct) or n1 -> n2 -> n3
        # BFS finds shortest: n1 -> n3 (direct calls edge)
        assert len(path) >= 2

    def test_find_paths_unreachable(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        path = store.find_paths("n1", "n2")
        assert path is None

    def test_find_paths_endpoint_not_exist(self, store):
        store.insert_node(_make_node("n1"))
        path = store.find_paths("n1", "nonexistent")
        assert path is None

    def test_find_paths_max_depth_respected(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_node(_make_node("n4"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.insert_edge(_make_edge("n2", "n3"))
        store.insert_edge(_make_edge("n3", "n4"))
        path = store.find_paths("n1", "n4", max_depth=2)
        assert path is None  # depth 2 can't reach n4

    def test_find_paths_empty_params_raises(self, store):
        with pytest.raises(ValueError):
            store.find_paths("", "n2")
        with pytest.raises(ValueError):
            store.find_paths("n1", "")


# =============================================================================
# 5. File management
# =============================================================================

class TestFileManagement:
    """Tests for file CRUD operations."""

    def test_upsert_file(self, store):
        store.upsert_file("src/test.py", "abc123", "python", node_count=5, size=200, modified_at=1000)
        f = store.get_file("src/test.py")
        assert f is not None
        assert f["content_hash"] == "abc123"
        assert f["language"] == "python"
        assert f["node_count"] == 5
        assert f["size"] == 200
        assert f["modified_at"] == 1000

    def test_upsert_file_update_existing(self, store):
        store.upsert_file("src/test.py", "abc123", "python")
        store.upsert_file("src/test.py", "def456", "python", node_count=10)
        f = store.get_file("src/test.py")
        assert f["content_hash"] == "def456"
        assert f["node_count"] == 10

    def test_get_file_nonexistent(self, store):
        assert store.get_file("nonexistent.py") is None

    def test_get_all_files(self, store):
        store.upsert_file("src/a.py", "h1", "python")
        store.upsert_file("src/b.py", "h2", "python")
        files = store.get_all_files()
        assert len(files) == 2
        assert files[0]["path"] <= files[1]["path"]  # sorted

    def test_get_file_stats(self, store):
        store.upsert_file("src/a.py", "h1", "python", size=100, modified_at=1000)
        store.upsert_file("src/b.py", "h2", "python", size=200, modified_at=2000)
        stats = store.get_file_stats()
        assert stats == {
            "src/a.py": (100, 1000),
            "src/b.py": (200, 2000),
        }

    def test_delete_file(self, store):
        store.upsert_file("src/a.py", "h1", "python")
        store.insert_node(_make_node("n1", file_path="src/a.py"))
        store.delete_file("src/a.py")
        assert store.get_file("src/a.py") is None
        assert store.get_node_by_id("n1") is None

    def test_delete_file_nonexistent(self, store):
        # No-op
        store.delete_file("nonexistent.py")

    def test_upsert_file_empty_path(self, store):
        with pytest.raises(ValueError):
            store.upsert_file("", "h1", "python")

    def test_upsert_file_empty_hash(self, store):
        with pytest.raises(ValueError):
            store.upsert_file("src/test.py", "", "python")


# =============================================================================
# 6. Search
# =============================================================================

class TestFtsSearch:
    """Tests for fts_search."""

    def test_fts_search_basic(self, store):
        store.insert_node(_make_node("n1", name="calculateTotal"))
        store.insert_node(_make_node("n2", name="parseInput"))
        results = store.fts_search("calculate")
        assert len(results) == 1
        assert results[0]["name"] == "calculateTotal"

    def test_fts_search_case_insensitive(self, store):
        store.insert_node(_make_node("n1", name="MyFunction"))
        results = store.fts_search("myfunction")
        assert len(results) == 1

    def test_fts_search_no_match(self, store):
        store.insert_node(_make_node("n1", name="foo"))
        results = store.fts_search("nonexistent")
        assert results == []

    def test_fts_search_limit(self, store):
        for i in range(10):
            store.insert_node(_make_node(f"n{i}", name=f"func_{i}"))
        results = store.fts_search("func", limit=3)
        assert len(results) == 3

    def test_fts_search_empty_query_raises(self, store):
        with pytest.raises(ValueError):
            store.fts_search("")

    def test_fts_search_with_kind_filter(self, store):
        store.insert_node(_make_node("n1", name="func_a", kind="function"))
        store.insert_node(_make_node("n2", name="func_b", kind="class"))
        results = store.fts_search("func", kind_filter="class")
        assert len(results) == 1
        assert results[0]["kind"] == "class"

    def test_fts_search_with_language_filter(self, store):
        store.insert_node(_make_node("n1", name="func_a", language="python"))
        store.insert_node(_make_node("n2", name="func_b", language="typescript"))
        results = store.fts_search("func", language_filter="typescript")
        assert len(results) == 1

    def test_fts_search_with_path_filter(self, store):
        store.insert_node(_make_node("n1", name="func_a", file_path="src/a.py"))
        store.insert_node(_make_node("n2", name="func_b", file_path="src/b.py"))
        results = store.fts_search("func", path_filter="src/b.py")
        assert len(results) == 1

    def test_fts_search_result_format(self, store):
        store.insert_node(_make_node("n1", name="my_func"))
        results = store.fts_search("my_func")
        r = results[0]
        assert "id" in r
        assert "name" in r
        assert "qualified_name" in r
        assert "kind" in r
        assert "file_path" in r
        assert "language" in r


class TestSearchByDefIndex:
    """Tests for search_by_def_index."""

    def test_search_found(self, store):
        store.insert_node(_make_node("n1", qualified_name="src/a.py::func.method"))
        node_id = store.search_by_def_index("src/a.py::func.method")
        assert node_id == "n1"

    def test_search_not_found(self, store):
        assert store.search_by_def_index("nonexistent") is None

    def test_search_empty_name_raises(self, store):
        with pytest.raises(ValueError):
            store.search_by_def_index("")


class TestSearchByFieldQualified:
    """Tests for search_by_field_qualified."""

    def test_search_by_field_qualified(self, store):
        store.insert_node(_make_node("n1", name="my_func", kind="function", language="python"))
        results = store.search_by_field_qualified("kind:function lang:python my_func")
        assert len(results) == 1

    def test_search_by_field_qualified_no_match(self, store):
        store.insert_node(_make_node("n1", name="my_func", kind="function"))
        results = store.search_by_field_qualified("kind:class my_func")
        assert results == []

    def test_search_by_field_qualified_empty_raises(self, store):
        with pytest.raises(ValueError):
            store.search_by_field_qualified("")


# =============================================================================
# 7. Unresolved references
# =============================================================================

class TestUnresolvedRefs:
    """Tests for unresolved reference operations."""

    def _make_ref(self, from_node_id="n1", reference_name="target_func", **overrides):
        ref = {
            "from_node_id": from_node_id,
            "reference_name": reference_name,
            "reference_kind": "call",
            "file_path": "src/test.py",
            "language": "python",
            "is_external": 0,
            "line": 10,
            "col": 5,
            "candidates": None,
            "source": "tree-sitter",
        }
        ref.update(overrides)
        return ref

    def test_insert_unresolved_ref(self, store):
        store.insert_unresolved_ref(self._make_ref())
        refs = store.get_unresolved_refs()
        assert len(refs) == 1

    def test_insert_unresolved_refs_batch(self, store):
        refs = [self._make_ref(reference_name=f"ref_{i}") for i in range(3)]
        store.insert_unresolved_refs(refs)
        assert len(store.get_unresolved_refs()) == 3

    def test_get_unresolved_refs_by_file(self, store):
        store.insert_unresolved_ref(self._make_ref(file_path="src/a.py"))
        store.insert_unresolved_ref(self._make_ref(file_path="src/b.py"))
        refs = store.get_unresolved_refs(file_path="src/a.py")
        assert len(refs) == 1
        assert refs[0]["file_path"] == "src/a.py"

    def test_get_unresolved_refs_all(self, store):
        store.insert_unresolved_ref(self._make_ref(file_path="src/a.py"))
        store.insert_unresolved_ref(self._make_ref(file_path="src/b.py"))
        refs = store.get_unresolved_refs()
        assert len(refs) == 2

    def test_clear_unresolved_refs(self, store):
        store.insert_unresolved_ref(self._make_ref())
        store.clear_unresolved_refs()
        assert store.get_unresolved_refs() == []

    def test_insert_unresolved_ref_missing_fields(self, store):
        with pytest.raises(ValueError):
            store.insert_unresolved_ref({"from_node_id": "n1"})


# =============================================================================
# 8. Transaction management (no-op)
# =============================================================================

class TestTransaction:
    """Tests for transaction no-op semantics."""

    def test_begin_sets_flag(self, store):
        store.begin()
        # commit should succeed (in transaction)
        store.commit()

    def test_commit_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.commit()

    def test_rollback_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.rollback()

    def test_nested_begin_raises(self, store):
        store.begin()
        with pytest.raises(TransactionError):
            store.begin()
        store.rollback()  # clean up

    def test_commit_clears_flag(self, store):
        store.begin()
        store.commit()
        # second commit should fail
        with pytest.raises(TransactionError):
            store.commit()

    def test_rollback_clears_flag(self, store):
        store.begin()
        store.rollback()
        with pytest.raises(TransactionError):
            store.rollback()

    def test_transaction_does_not_isolate_writes(self, store):
        """MemoryStore transactions are no-ops: writes are immediately visible."""
        store.begin()
        store.insert_node(_make_node("n1"))
        # Should be visible immediately even before commit
        assert store.get_node_by_id("n1") is not None
        store.commit()


# =============================================================================
# 9. Batch operations
# =============================================================================

class TestFlush:
    """Tests for flush (no-op)."""

    def test_flush_is_noop(self, store):
        store.insert_node(_make_node("n1"))
        store.flush()
        # Data still present
        assert store.count_nodes() == 1


# =============================================================================
# 10. Maintenance / statistics
# =============================================================================

class TestMaintenance:
    """Tests for optimize, clear, rebuild_fts, stats, build_def_index."""

    def test_optimize_is_noop(self, store):
        store.optimize()  # should not raise

    def test_clear(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.upsert_file("src/test.py", "h1", "python")
        store.insert_unresolved_ref({
            "from_node_id": "n1", "reference_name": "x",
            "file_path": "src/test.py", "language": "python",
            "is_external": 0,
        })
        store.clear()
        assert store.count_nodes() == 0
        assert store.count_edges() == 0
        assert store.get_all_files() == []
        assert store.get_unresolved_refs() == []

    def test_clear_resets_edge_id_counter(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.clear()
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        edges = store.get_outgoing_edges("n1")
        assert edges[0]["id"] == 1  # counter reset

    def test_rebuild_fts_is_noop(self, store):
        store.rebuild_fts()  # should not raise

    def test_stats(self, populated_store):
        stats = populated_store.stats()
        assert stats["node_count"] == 3
        assert stats["edge_count"] == 3
        assert stats["file_count"] == 1
        assert stats["unresolved_count"] == 0

    def test_stats_memory_usage(self, store):
        stats = store.stats()
        assert "memory_usage_bytes" in stats

    def test_build_def_index(self, populated_store):
        index = populated_store.build_def_index()
        assert len(index) == 3
        assert "src/test.py::function.name_of_n1" in index
        assert index["src/test.py::function.name_of_n1"] == "n1"

    def test_build_def_index_empty(self, store):
        assert store.build_def_index() == {}


# =============================================================================
# 11. load_from
# =============================================================================

class TestLoadFrom:
    """Tests for MemoryStore.load_from()."""

    def test_load_from_copies_nodes(self):
        src = MemoryStore()
        src.insert_node(_make_node("n1"))
        src.insert_node(_make_node("n2"))

        dst = MemoryStore()
        dst.load_from(src)
        assert dst.count_nodes() == 2
        assert dst.get_node_by_id("n1") is not None
        assert dst.get_node_by_id("n2") is not None

    def test_load_from_copies_edges_and_adjacency(self):
        src = MemoryStore()
        src.insert_node(_make_node("n1"))
        src.insert_node(_make_node("n2"))
        src.insert_node(_make_node("n3"))
        src.insert_edge(_make_edge("n1", "n2", kind="calls"))
        src.insert_edge(_make_edge("n2", "n3", kind="contains"))

        dst = MemoryStore()
        dst.load_from(src)
        assert dst.count_edges() == 2
        # Verify adjacency is correct
        assert len(dst.get_outgoing_edges("n1")) == 1
        assert len(dst.get_incoming_edges("n2")) == 1

    def test_load_from_uses_public_iterators(self):
        """load_from should work via iter_all_nodes + iter_all_edges (public API)."""
        src = MemoryStore()
        src.insert_node(_make_node("n1"))
        src.insert_edge(_make_edge("n1", "n2"))  # n2 target not inserted — edge still stored

        dst = MemoryStore()
        dst.load_from(src)
        # Node n1 loaded; edge n1->n2 loaded since source n1 exists (target may be cross-file)
        assert dst.count_nodes() == 1
        assert dst.count_edges() == 1

    def test_load_from_idempotent_on_multiple_calls(self):
        src = MemoryStore()
        src.insert_node(_make_node("n1"))
        src.insert_node(_make_node("n2"))
        src.insert_edge(_make_edge("n1", "n2"))

        dst = MemoryStore()
        dst.load_from(src)
        dst.load_from(src)  # second call replaces data
        assert dst.count_nodes() == 2
        assert dst.count_edges() == 1

    def test_load_from_sets_edge_id_counter(self):
        src = MemoryStore()
        src.insert_node(_make_node("n1"))
        src.insert_node(_make_node("n2"))
        src.insert_edge(_make_edge("n1", "n2"))

        dst = MemoryStore()
        dst.load_from(src)
        # After load, inserting a new edge should continue from correct counter
        dst.insert_edge(_make_edge("n1", "n2", kind="references"))
        edges = dst.get_edges_between("n1", "n2")
        assert len(edges) == 2
        # First edge from load should have id=1, second should have id=2
        ids = sorted(e["id"] for e in edges)
        assert ids == [1, 2]

    def test_load_from_preserves_def_index(self):
        src = MemoryStore()
        src.insert_node(_make_node("n1", qualified_name="a.b.Foo"))
        src.insert_node(_make_node("n2", qualified_name="a.b.Bar"))

        dst = MemoryStore()
        dst.load_from(src)
        assert dst.search_by_def_index("a.b.Foo") == "n1"
        assert dst.search_by_def_index("a.b.Bar") == "n2"


# =============================================================================
# 12. Edge cases & error handling
# =============================================================================

class TestClose:
    """Tests for close and StoreClosedError."""

    def test_close_sets_closed(self, store):
        store.close()
        with pytest.raises(StoreClosedError):
            store.count_nodes()

    def test_close_idempotent(self, store):
        store.close()
        store.close()  # second close should not raise

    def test_close_all_methods_raise(self, store):
        """All mutating/reading methods should raise StoreClosedError after close."""
        store.close()
        methods_to_test = [
            lambda: store.insert_node(_make_node("n1")),
            lambda: store.insert_nodes([_make_node("n1")]),
            lambda: store.get_node_by_id("n1"),
            lambda: store.get_nodes_by_ids(["n1"]),
            lambda: store.delete_nodes_by_file("test.py"),
            lambda: store.update_node_property("n1", {}),
            lambda: list(store.iter_nodes_by_kind("function")),
            lambda: list(store.iter_all_nodes()),
            lambda: store.count_nodes(),
            lambda: store.insert_edge(_make_edge("n1", "n2")),
            lambda: store.insert_edges([_make_edge("n1", "n2")]),
            lambda: store.get_outgoing_edges("n1"),
            lambda: store.get_incoming_edges("n1"),
            lambda: store.get_edges_between("n1", "n2"),
            lambda: store.delete_edges_by_source("n1"),
            lambda: store.count_edges(),
            lambda: list(store.iter_all_edges()),
            lambda: store.get_neighbors("n1"),
            lambda: store.get_neighbors_batch(["n1"]),
            lambda: store.find_paths("n1", "n2"),
            lambda: store.upsert_file("test.py", "h", "python"),
            lambda: store.get_file("test.py"),
            lambda: store.get_all_files(),
            lambda: store.get_file_stats(),
            lambda: store.delete_file("test.py"),
            lambda: store.fts_search("test"),
            lambda: store.search_by_def_index("test"),
            lambda: store.search_by_field_qualified("test"),
            lambda: store.insert_unresolved_ref({"from_node_id": "n1", "reference_name": "x", "file_path": "x.py", "language": "python", "is_external": 0}),
            lambda: store.insert_unresolved_refs([{"from_node_id": "n1", "reference_name": "x", "file_path": "x.py", "language": "python", "is_external": 0}]),
            lambda: store.get_unresolved_refs(),
            lambda: store.clear_unresolved_refs(),
            lambda: store.flush(),
            lambda: store.clear(),
            lambda: store.stats(),
            lambda: store.build_def_index(),
            lambda: store.begin(),
            lambda: store.commit(),
            lambda: store.rollback(),
        ]
        for i, fn in enumerate(methods_to_test):
            with pytest.raises(StoreClosedError):
                fn()

    def test_rebuild_fts_raises_when_closed(self, store):
        store.close()
        with pytest.raises(StoreClosedError):
            store.rebuild_fts()

    def test_optimize_raises_when_closed(self, store):
        store.close()
        with pytest.raises(StoreClosedError):
            store.optimize()


# =============================================================================
# 13. Adjacency table integrity
# =============================================================================

class TestAdjacencyIntegrity:
    """Verify adjacency tables stay consistent through CRUD operations."""

    def test_outgoing_consistency(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.insert_edge(_make_edge("n1", "n3", kind="calls"))

        outgoing = store.get_outgoing_edges("n1")
        targets = {e["target"] for e in outgoing}
        assert targets == {"n2", "n3"}

    def test_incoming_consistency(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n3", kind="calls"))
        store.insert_edge(_make_edge("n2", "n3", kind="calls"))

        incoming = store.get_incoming_edges("n3")
        sources = {e["source"] for e in incoming}
        assert sources == {"n1", "n2"}

    def test_delete_edges_by_source_cleans_incoming(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.delete_edges_by_source("n1")
        assert store.get_incoming_edges("n2") == []
        assert store.get_outgoing_edges("n1") == []

    def test_delete_nodes_by_file_cleans_adjacency(self, store):
        store.insert_node(_make_node("n1", file_path="src/a.py"))
        store.insert_node(_make_node("n2", file_path="src/b.py"))
        store.insert_edge(_make_edge("n1", "n2"))
        store.delete_nodes_by_file("src/a.py")
        # n2 should have no incoming edges from n1
        assert store.get_incoming_edges("n2") == []
        # n1's outgoing should be empty
        assert store.get_outgoing_edges("n1") == []

    def test_update_edge_target_updates_adjacency(self, store):
        store.insert_node(_make_node("n1"))
        store.insert_node(_make_node("n2"))
        store.insert_node(_make_node("n3"))
        store.insert_edge(_make_edge("n1", "n2", kind="calls"))
        store.update_edge_target(1, "n3")

        # n2 should no longer have incoming from n1
        assert store.get_incoming_edges("n2") == []
        # n3 should now have incoming from n1
        incoming_n3 = store.get_incoming_edges("n3")
        assert len(incoming_n3) == 1
        assert incoming_n3[0]["source"] == "n1"
