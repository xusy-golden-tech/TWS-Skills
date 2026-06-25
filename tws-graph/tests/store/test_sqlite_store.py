"""Tests for store/sqlite_store.py — SqliteStore SQLite-persistent graph implementation.

Covers:
    1. Initialization & Store ABC compliance
    2. Node CRUD (insert, lookup, batch, delete, iter)
    3. Edge CRUD (insert, lookup, batch, update, delete)
    4. Graph traversal (neighbors, batch neighbors, BFS paths)
    5. File management
    6. Search (FTS, def index, field-qualified)
    7. Unresolved references
    8. Transaction management
    9. Flush / batch operations
    10. Maintenance / statistics
    11. Write buffer auto-flush
    12. Edge cases & error handling
"""

import json
import hashlib
import time
import sqlite3
import pytest
from pathlib import Path

from tws_graph.store.interface import Store
from tws_graph.store.sqlite_store import SqliteStore
from tws_graph.store.exceptions import (
    StoreClosedError,
    TransactionError,
    EdgeNotFoundError,
    StoreError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _make_node(file_path="src/test.py", kind="function", name="test_func", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    qname = overrides.get("qualified_name", f"{file_path}::{name}")
    node = {
        "id": _hash_id(qname, file_path),
        "kind": kind,
        "name": name,
        "qualified_name": qname,
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 10,
        "signature": "def test_func()",
        "docstring": "A test function.",
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 1,
        "decorators": None,
        "framework": None,
        "properties": None,
        "updated_at": int(time.time() * 1000),
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
        "source_loc": "src/test.py:3:5",
        "provenance": "tree-sitter",
        "properties": None,
    }
    edge.update(overrides)
    return edge


def _init_full_schema(conn: sqlite3.Connection):
    """Create the complete database schema for tests."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            checksum TEXT
        );
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            language TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            signature TEXT,
            docstring TEXT,
            visibility TEXT,
            is_abstract INTEGER DEFAULT 0,
            is_exported INTEGER DEFAULT 0,
            decorators TEXT,
            framework TEXT,
            properties TEXT DEFAULT '{}',
            body TEXT,
            body_hash TEXT,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            target TEXT NOT NULL,
            target_text TEXT,
            kind TEXT NOT NULL,
            source_loc TEXT,
            provenance TEXT DEFAULT 'tree-sitter',
            properties TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            language TEXT NOT NULL,
            node_count INTEGER DEFAULT 0,
            indexed_at INTEGER NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            modified_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS unresolved_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            reference_name TEXT NOT NULL,
            reference_kind TEXT NOT NULL DEFAULT 'call',
            line INTEGER NOT NULL DEFAULT 0,
            col INTEGER NOT NULL DEFAULT 0,
            candidates TEXT,
            source TEXT NOT NULL DEFAULT 'tree-sitter',
            file_path TEXT NOT NULL,
            language TEXT NOT NULL,
            is_external INTEGER NOT NULL DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
            id, name, qualified_name, docstring, signature,
            content='nodes', content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
            VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
            VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
            VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
            INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
            VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
        END;
        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
        CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source, target);
        CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_urefs_from_node ON unresolved_refs(from_node_id);
        CREATE INDEX IF NOT EXISTS idx_urefs_file ON unresolved_refs(file_path);
        INSERT INTO schema_versions (version, applied_at, description) VALUES (1, 0, 'v1');
        INSERT INTO schema_versions (version, applied_at, description) VALUES (2, 0, 'v2');
        INSERT INTO schema_versions (version, applied_at, description) VALUES (3, 0, 'v3');
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    db_dir = tmp_path / "sqlite_store_test"
    db_dir.mkdir()
    db_file = db_dir / "test.db"
    # Pre-create DB with schema
    conn = sqlite3.connect(str(db_file))
    _init_full_schema(conn)
    conn.close()
    return str(db_file)


@pytest.fixture
def store(db_path: str):
    """Return a fresh SqliteStore connected to a temp database."""
    s = SqliteStore(db_path, auto_flush_size=100)
    yield s
    try:
        s.close()
    except StoreClosedError:
        pass


@pytest.fixture
def populated_store(store: SqliteStore):
    """Return a SqliteStore with 3 nodes and 3 edges already flushed."""
    n1 = _make_node(kind="function", name="func_one")
    n2 = _make_node(kind="class", name="MyClass")
    n3 = _make_node(kind="method", name="do_stuff")
    store.insert_node(n1)
    store.insert_node(n2)
    store.insert_node(n3)
    store.insert_edge(_make_edge(n1["id"], n2["id"], kind="calls"))
    store.insert_edge(_make_edge(n2["id"], n3["id"], kind="contains"))
    store.insert_edge(_make_edge(n1["id"], n3["id"], kind="calls"))
    store.upsert_file("src/test.py", "abc123", "python", node_count=3, size=100, modified_at=1000)
    store.flush()
    return store


# =============================================================================
# 1. Initialization & Store ABC compliance
# =============================================================================

class TestInitialization:
    """Verify SqliteStore is a valid Store implementation."""

    def test_is_store_instance(self, store):
        assert isinstance(store, Store)

    def test_can_instantiate(self, db_path):
        s = SqliteStore(db_path)
        assert s is not None
        s.close()

    def test_initial_state_empty(self, store):
        assert store.count_nodes() == 0
        assert store.count_edges() == 0
        stats_ = store.stats()
        assert stats_["node_count"] == 0
        assert stats_["edge_count"] == 0
        assert stats_["file_count"] == 0
        assert stats_["unresolved_count"] == 0

    def test_initial_not_closed(self, store):
        store.count_nodes()

    def test_auto_flush_size_default(self):
        s = SqliteStore(":memory:")
        assert s._auto_flush_size == 10000
        s.close()


# =============================================================================
# 2. Node CRUD
# =============================================================================

class TestNodeCRUD:
    """Insert, lookup, batch, delete, iter, update nodes."""

    def test_insert_and_get_single_node(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        result = store.get_node_by_id(node["id"])
        assert result is not None
        assert result["name"] == node["name"]
        assert result["kind"] == "function"

    def test_get_node_by_id_not_found(self, store):
        assert store.get_node_by_id("nonexistent") is None

    def test_insert_node_missing_required(self, store):
        with pytest.raises(ValueError):
            store.insert_node({"id": "n1"})  # missing kind, name, etc.

    def test_insert_nodes_batch(self, store):
        n1 = _make_node(name="a")
        n2 = _make_node(name="b")
        store.insert_nodes([n1, n2])
        store.flush()
        assert store.count_nodes() == 2
        assert store.get_node_by_id(n1["id"]) is not None
        assert store.get_node_by_id(n2["id"]) is not None

    def test_insert_nodes_batch_with_invalid(self, store):
        with pytest.raises(ValueError):
            store.insert_nodes([_make_node(), {"id": "bad"}])

    def test_get_nodes_by_ids(self, store):
        n1 = _make_node(name="a")
        n2 = _make_node(name="b")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        result = store.get_nodes_by_ids([n1["id"], n2["id"], "ghost"])
        assert len(result) == 2
        assert n1["id"] in result
        assert n2["id"] in result

    def test_get_nodes_by_ids_empty(self, store):
        assert store.get_nodes_by_ids([]) == {}

    def test_delete_nodes_by_file(self, store):
        n1 = _make_node(file_path="src/a.py", name="a")
        n2 = _make_node(file_path="src/b.py", name="b")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        store.delete_nodes_by_file("src/a.py")
        assert store.get_node_by_id(n1["id"]) is None
        assert store.get_node_by_id(n2["id"]) is not None

    def test_delete_nodes_by_file_with_kind(self, store):
        n1 = _make_node(file_path="src/a.py", kind="function", name="a")
        n2 = _make_node(file_path="src/a.py", kind="class", name="b")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        store.delete_nodes_by_file("src/a.py", kind="function")
        assert store.get_node_by_id(n1["id"]) is None
        assert store.get_node_by_id(n2["id"]) is not None

    def test_delete_nodes_by_file_nonexistent(self, store):
        store.delete_nodes_by_file("nonexistent.py")  # no-op, no exception

    def test_update_node_property_merge(self, store):
        node = _make_node(properties='{"a": 1}')
        store.insert_node(node)
        store.flush()
        store.update_node_property(node["id"], {"b": 2})
        updated = store.get_node_by_id(node["id"])
        props = json.loads(updated["properties"])
        assert props["a"] == 1
        assert props["b"] == 2

    def test_update_node_property_not_found(self, store):
        with pytest.raises(KeyError):
            store.update_node_property("ghost", {"x": 1})

    def test_iter_nodes_by_kind(self, store):
        store.insert_node(_make_node(kind="function", name="f1"))
        store.insert_node(_make_node(kind="class", name="c1"))
        store.insert_node(_make_node(kind="function", name="f2"))
        store.flush()
        funcs = list(store.iter_nodes_by_kind("function"))
        assert len(funcs) == 2
        assert all(n["kind"] == "function" for n in funcs)

    def test_iter_all_nodes(self, store):
        for i in range(5):
            store.insert_node(_make_node(name=f"n{i}"))
        store.flush()
        all_nodes = list(store.iter_all_nodes(batch_size=2))
        assert len(all_nodes) == 5

    def test_count_nodes(self, store):
        assert store.count_nodes() == 0
        store.insert_node(_make_node())
        store.flush()
        assert store.count_nodes() == 1

    def test_iter_nodes_by_file(self, store):
        n1 = _make_node(file_path="src/a.py", name="a")
        n2 = _make_node(file_path="src/b.py", name="b")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        result = list(store.iter_nodes_by_file("src/a.py"))
        assert len(result) == 1
        assert result[0]["name"] == "a"

    def test_iter_nodes_by_file_empty_path(self, store):
        with pytest.raises(ValueError):
            list(store.iter_nodes_by_file(""))


# =============================================================================
# 3. Edge CRUD
# =============================================================================

class TestEdgeCRUD:
    """Insert, lookup, batch, update, delete edges."""

    @pytest.fixture
    def edge_store(self, store):
        """Store with 2 nodes for edge tests."""
        self.n_a = _make_node(name="a")
        self.n_b = _make_node(name="b")
        store.insert_node(self.n_a)
        store.insert_node(self.n_b)
        store.flush()
        return store

    def test_insert_and_get_outgoing(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        out = edge_store.get_outgoing_edges(self.n_a["id"])
        assert len(out) == 1
        assert out[0]["kind"] == "calls"

    def test_get_incoming_edges(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        incoming = edge_store.get_incoming_edges(self.n_b["id"])
        assert len(incoming) == 1
        assert incoming[0]["kind"] == "calls"

    def test_get_outgoing_with_kind_filter(self, edge_store):
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="calls"))
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="references"))
        edge_store.flush()
        calls = edge_store.get_outgoing_edges(self.n_a["id"], kinds=["calls"])
        assert len(calls) == 1
        assert calls[0]["kind"] == "calls"

    def test_get_edges_between(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        result = edge_store.get_edges_between(self.n_a["id"], self.n_b["id"])
        assert len(result) == 1

    def test_get_edges_between_none(self, edge_store):
        result = edge_store.get_edges_between(self.n_a["id"], self.n_b["id"])
        assert result == []

    def test_insert_edges_batch(self, edge_store):
        edges = [
            _make_edge(self.n_a["id"], self.n_b["id"], kind="calls"),
            _make_edge(self.n_b["id"], self.n_a["id"], kind="references"),
        ]
        edge_store.insert_edges(edges)
        edge_store.flush()
        assert edge_store.count_edges() == 2

    def test_insert_edge_missing_required(self, store):
        with pytest.raises(ValueError):
            store.insert_edge({"kind": "calls"})  # missing source

    def test_update_edge_target(self, edge_store):
        n_c = _make_node(name="c")
        edge_store.insert_node(n_c)
        edge_store.flush()
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        edges = edge_store.get_outgoing_edges(self.n_a["id"])
        assert len(edges) == 1
        edge_id = edges[0]["id"]
        edge_store.update_edge_target(edge_id, n_c["id"], provenance="resolved")
        updated = edge_store.get_outgoing_edges(self.n_a["id"])
        assert updated[0]["target"] == n_c["id"]
        assert updated[0]["provenance"] == "resolved"

    def test_update_edge_target_not_found(self, edge_store):
        with pytest.raises(EdgeNotFoundError):
            edge_store.update_edge_target(99999, self.n_b["id"])

    def test_update_edge_provenance(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls", provenance="tree-sitter")
        edge_store.insert_edge(e)
        edge_store.flush()
        edges = edge_store.get_outgoing_edges(self.n_a["id"])
        edge_id = edges[0]["id"]
        edge_store.update_edge_provenance(edge_id, "ambiguous")
        updated = edge_store.get_outgoing_edges(self.n_a["id"])
        assert updated[0]["provenance"] == "ambiguous"

    def test_update_edge_provenance_not_found(self, edge_store):
        with pytest.raises(EdgeNotFoundError):
            edge_store.update_edge_provenance(99999, "resolved")

    def test_delete_edges_by_source(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        assert edge_store.count_edges() == 1
        edge_store.delete_edges_by_source(self.n_a["id"])
        assert edge_store.count_edges() == 0

    def test_delete_edges_by_source_nonexistent(self, edge_store):
        edge_store.delete_edges_by_source("ghost")  # no-op

    def test_count_edges(self, edge_store):
        assert edge_store.count_edges() == 0
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"]))
        edge_store.flush()
        assert edge_store.count_edges() == 1

    def test_iter_all_edges(self, edge_store):
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="calls"))
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="references"))
        edge_store.flush()
        all_edges = list(edge_store.iter_all_edges(batch_size=1))
        assert len(all_edges) == 2

    def test_get_dangling_edges(self, edge_store):
        e = _make_edge(self.n_a["id"], "nonexistent_target", kind="calls",
                       target_text="some_function")
        edge_store.insert_edge(e)
        edge_store.flush()
        dangling = edge_store.get_dangling_edges()
        assert len(dangling) == 1
        assert dangling[0]["target_text"] == "some_function"

    def test_get_dangling_edges_with_kind_filter(self, edge_store):
        e1 = _make_edge(self.n_a["id"], "ghost1", kind="calls", target_text="fn1")
        e2 = _make_edge(self.n_a["id"], "ghost2", kind="imports", target_text="fn2")
        edge_store.insert_edge(e1)
        edge_store.insert_edge(e2)
        edge_store.flush()
        dangling_calls = edge_store.get_dangling_edges(kind="calls")
        assert len(dangling_calls) == 1
        assert dangling_calls[0]["kind"] == "calls"

    def test_iter_edges_with_kind(self, edge_store):
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="calls"))
        edge_store.insert_edge(_make_edge(self.n_a["id"], self.n_b["id"], kind="references"))
        edge_store.flush()
        results = list(edge_store.iter_edges(kind="calls"))
        assert len(results) == 1
        assert results[0]["kind"] == "calls"

    def test_iter_edges_with_source_info(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        results = list(edge_store.iter_edges(with_source_info=True))
        assert len(results) == 1
        assert results[0]["source_file"] == self.n_a["file_path"]
        assert results[0]["source_language"] == self.n_a["language"]

    def test_iter_edges_from_out(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        results = list(edge_store.iter_edges_from(self.n_a["id"], direction="out"))
        assert len(results) == 1

    def test_iter_edges_from_in(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        results = list(edge_store.iter_edges_from(self.n_b["id"], direction="in"))
        assert len(results) == 1

    def test_iter_edges_from_both(self, edge_store):
        e = _make_edge(self.n_a["id"], self.n_b["id"], kind="calls")
        edge_store.insert_edge(e)
        edge_store.flush()
        out = list(edge_store.iter_edges_from(self.n_a["id"], direction="both"))
        assert len(out) == 1


# =============================================================================
# 4. Graph traversal
# =============================================================================

class TestGraphTraversal:
    """Neighbors, batch neighbors, BFS paths."""

    @pytest.fixture
    def graph_store(self, store):
        """Store with 4 nodes forming a small call graph."""
        self.n1 = _make_node(name="main")
        self.n2 = _make_node(name="parse")
        self.n3 = _make_node(name="validate")
        self.n4 = _make_node(name="log")
        for n in [self.n1, self.n2, self.n3, self.n4]:
            store.insert_node(n)
        store.insert_edge(_make_edge(self.n1["id"], self.n2["id"], kind="calls"))
        store.insert_edge(_make_edge(self.n1["id"], self.n3["id"], kind="calls"))
        store.insert_edge(_make_edge(self.n2["id"], self.n4["id"], kind="calls"))
        store.flush()
        return store

    def test_get_neighbors_out(self, graph_store):
        neighbors = graph_store.get_neighbors(self.n1["id"], direction="out")
        assert len(neighbors) == 2  # parse, validate

    def test_get_neighbors_in(self, graph_store):
        neighbors = graph_store.get_neighbors(self.n4["id"], direction="in")
        assert len(neighbors) == 1  # parse -> log

    def test_get_neighbors_both(self, graph_store):
        neighbors = graph_store.get_neighbors(self.n2["id"], direction="both")
        assert len(neighbors) == 2  # main (in) + log (out)

    def test_get_neighbors_with_kinds(self, graph_store):
        neighbors = graph_store.get_neighbors(
            self.n1["id"], kinds=["calls"], direction="out"
        )
        assert len(neighbors) == 2

    def test_get_neighbors_dedup(self, graph_store):
        """Same neighbor via multiple edges should appear once."""
        graph_store.insert_edge(_make_edge(self.n1["id"], self.n2["id"], kind="references"))
        graph_store.flush()
        neighbors = graph_store.get_neighbors(self.n1["id"], direction="out")
        n2_count = sum(1 for nb in neighbors if nb["node"]["id"] == self.n2["id"])
        assert n2_count == 1

    def test_get_neighbors_batch(self, graph_store):
        result = graph_store.get_neighbors_batch(
            [self.n1["id"], self.n2["id"]], direction="out"
        )
        assert len(result[self.n1["id"]]) == 2
        assert len(result[self.n2["id"]]) == 1

    def test_get_neighbors_batch_nonexistent(self, graph_store):
        result = graph_store.get_neighbors_batch(["ghost"], direction="both")
        assert result["ghost"] == []

    def test_find_paths_direct(self, graph_store):
        path = graph_store.find_paths(self.n1["id"], self.n2["id"], max_depth=3)
        assert path is not None
        assert len(path) == 2  # main -> parse

    def test_find_paths_two_hops(self, graph_store):
        path = graph_store.find_paths(self.n1["id"], self.n4["id"], max_depth=3)
        assert path is not None
        assert len(path) == 3  # main -> parse -> log

    def test_find_paths_unreachable(self, graph_store):
        path = graph_store.find_paths(self.n3["id"], self.n4["id"], max_depth=3)
        assert path is None

    def test_find_paths_same_node(self, graph_store):
        path = graph_store.find_paths(self.n1["id"], self.n1["id"])
        assert path is not None
        assert len(path) == 1

    def test_find_paths_nonexistent(self, graph_store):
        path = graph_store.find_paths(self.n1["id"], "ghost")
        assert path is None


# =============================================================================
# 5. File management
# =============================================================================

class TestFileManagement:
    """Upsert, get, list, stats, delete files."""

    def test_upsert_and_get(self, store):
        store.upsert_file("src/main.py", "hash123", "python", node_count=5, size=200, modified_at=1000)
        f = store.get_file("src/main.py")
        assert f is not None
        assert f["content_hash"] == "hash123"
        assert f["node_count"] == 5

    def test_get_file_not_found(self, store):
        assert store.get_file("nonexistent.py") is None

    def test_upsert_updates_existing(self, store):
        store.upsert_file("src/a.py", "hash1", "python")
        store.upsert_file("src/a.py", "hash2", "python", node_count=10)
        f = store.get_file("src/a.py")
        assert f["content_hash"] == "hash2"
        assert f["node_count"] == 10

    def test_get_all_files(self, store):
        store.upsert_file("src/a.py", "h1", "python")
        store.upsert_file("src/b.py", "h2", "typescript")
        files = store.get_all_files()
        assert len(files) == 2
        assert files[0]["path"] <= files[1]["path"]  # sorted

    def test_get_file_stats(self, store):
        store.upsert_file("src/a.py", "h1", "python", size=100, modified_at=1000)
        store.upsert_file("src/b.py", "h2", "python", size=200, modified_at=2000)
        stats = store.get_file_stats()
        assert stats["src/a.py"] == (100, 1000)
        assert stats["src/b.py"] == (200, 2000)

    def test_delete_file(self, store):
        node = _make_node(file_path="src/a.py")
        store.insert_node(node)
        store.flush()
        store.upsert_file("src/a.py", "h1", "python", node_count=1)
        store.delete_file("src/a.py")
        assert store.get_node_by_id(node["id"]) is None
        assert store.get_file("src/a.py") is None

    def test_delete_file_nonexistent(self, store):
        store.delete_file("nonexistent.py")  # no-op


# =============================================================================
# 6. Search
# =============================================================================

class TestSearch:
    """FTS, def index, field-qualified search."""

    def test_fts_search_by_name(self, store):
        node = _make_node(name="calculateTotal", docstring="Calculate the total sum")
        store.insert_node(node)
        store.flush()
        store.rebuild_fts()
        results = store.fts_search("calculate")
        assert len(results) >= 1
        assert results[0]["name"] == "calculateTotal"

    def test_fts_search_no_results(self, store):
        results = store.fts_search("zzznotfound")
        assert results == []

    def test_fts_search_with_kind_filter(self, store):
        store.insert_node(_make_node(name="MyClass", kind="class"))
        store.insert_node(_make_node(name="my_func", kind="function"))
        store.flush()
        store.rebuild_fts()
        results = store.fts_search("my", kind_filter="class")
        assert len(results) >= 1
        assert all(r["kind"] == "class" for r in results)

    def test_fts_search_with_language_filter(self, store):
        store.insert_node(_make_node(name="foo_py", language="python"))
        store.insert_node(_make_node(name="foo_ts", language="typescript"))
        store.flush()
        store.rebuild_fts()
        results = store.fts_search("foo", language_filter="python")
        assert len(results) >= 1
        assert all(r["language"] == "python" for r in results)

    def test_search_by_def_index(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        found = store.search_by_def_index(node["qualified_name"])
        assert found == node["id"]

    def test_search_by_def_index_not_found(self, store):
        assert store.search_by_def_index("nonexistent::func") is None

    def test_build_def_index(self, store):
        n1 = _make_node(name="a")
        n2 = _make_node(name="b")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        index = store.build_def_index()
        assert len(index) == 2
        assert index[n1["qualified_name"]] == n1["id"]

    def test_search_by_field_qualified(self, store):
        store.insert_node(_make_node(kind="function", name="calc"))
        store.insert_node(_make_node(kind="class", name="Calculator"))
        store.flush()
        store.rebuild_fts()
        results = store.search_by_field_qualified("kind:function calc")
        assert len(results) >= 1
        assert all(r["kind"] == "function" for r in results)


# =============================================================================
# 7. Unresolved references
# =============================================================================

class TestUnresolvedRefs:
    """Insert, query, clear unresolved references."""

    def _make_ref(self, from_node_id, ref_name="unknown_fn", file_path="src/test.py", **overrides):
        ref = {
            "from_node_id": from_node_id,
            "reference_name": ref_name,
            "reference_kind": "call",
            "line": 10,
            "col": 5,
            "candidates": None,
            "file_path": file_path,
            "language": "python",
            "is_external": 0,
        }
        ref.update(overrides)
        return ref

    def test_insert_and_get(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        ref = self._make_ref(node["id"])
        store.insert_unresolved_ref(ref)
        store.flush()
        all_refs = store.get_unresolved_refs()
        assert len(all_refs) == 1
        assert all_refs[0]["reference_name"] == "unknown_fn"

    def test_get_by_file(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        store.insert_unresolved_ref(self._make_ref(node["id"], file_path="src/a.py"))
        store.insert_unresolved_ref(self._make_ref(node["id"], file_path="src/b.py"))
        store.flush()
        a_refs = store.get_unresolved_refs(file_path="src/a.py")
        assert len(a_refs) == 1

    def test_clear(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        store.insert_unresolved_ref(self._make_ref(node["id"]))
        store.flush()
        store.clear_unresolved_refs()
        assert store.get_unresolved_refs() == []

    def test_insert_refs_batch(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        refs = [
            self._make_ref(node["id"], "fn1"),
            self._make_ref(node["id"], "fn2"),
        ]
        store.insert_unresolved_refs(refs)
        store.flush()
        assert len(store.get_unresolved_refs()) == 2


# =============================================================================
# 8. Transaction management
# =============================================================================

class TestTransaction:
    """Begin, commit, rollback with single-level semantics."""

    def test_begin_commit(self, store):
        store.begin()
        node = _make_node()
        store.insert_node(node)
        store.commit()
        assert store.get_node_by_id(node["id"]) is not None

    def test_begin_rollback(self, store):
        store.begin()
        node = _make_node()
        store.insert_node(node)
        store.rollback()
        assert store.get_node_by_id(node["id"]) is None

    def test_nested_begin_raises(self, store):
        store.begin()
        try:
            with pytest.raises(TransactionError):
                store.begin()
        finally:
            store.rollback()

    def test_commit_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.commit()

    def test_rollback_without_begin_raises(self, store):
        with pytest.raises(TransactionError):
            store.rollback()

    def test_commit_flushes_buffers(self, store):
        """Commit should auto-flush buffered writes."""
        store.begin()
        node = _make_node()
        store.insert_node(node)  # buffered, not yet written
        # Before commit, node not visible in DB
        # (but visible in buffer via get_node_by_id)
        store.commit()
        # After commit, node is persisted
        assert store.get_node_by_id(node["id"]) is not None
        assert store.count_nodes() == 1

    def test_rollback_clears_buffers(self, store):
        store.begin()
        node = _make_node()
        store.insert_node(node)  # buffered
        store.insert_edge(_make_edge(node["id"], "some_target"))
        store.insert_unresolved_ref({
            "from_node_id": node["id"],
            "reference_name": "x",
            "file_path": "src/test.py",
            "language": "python",
        })
        store.rollback()
        # Buffers should be cleared
        store.flush()  # should be no-op
        assert store.count_nodes() == 0


# =============================================================================
# 9. Flush / batch operations
# =============================================================================

class TestFlush:
    """Flush buffer to persistent storage."""

    def test_flush_persists_nodes(self, store):
        node = _make_node()
        store.insert_node(node)  # buffered
        assert store.count_nodes() == 0  # not in DB yet
        store.flush()
        assert store.count_nodes() == 1  # now in DB

    def test_flush_empty_buffers(self, store):
        store.flush()  # no-op, should not raise

    def test_flush_auto_on_threshold(self, db_path):
        s = SqliteStore(db_path, auto_flush_size=2)
        try:
            s.insert_node(_make_node(name="n1"))
            s.insert_node(_make_node(name="n2"))  # triggers auto-flush
            # Should have been flushed automatically
            assert s.count_nodes() == 2
        finally:
            s.close()

    def test_flush_persists_edges(self, store):
        n1 = _make_node()
        n2 = _make_node()
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        store.insert_edge(_make_edge(n1["id"], n2["id"]))
        store.flush()
        assert store.count_edges() == 1

    def test_flush_persists_refs(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        store.insert_unresolved_ref({
            "from_node_id": node["id"],
            "reference_name": "missing_fn",
            "file_path": "src/test.py",
            "language": "python",
        })
        store.flush()
        assert len(store.get_unresolved_refs()) == 1


# =============================================================================
# 10. Maintenance / statistics
# =============================================================================

class TestMaintenance:
    """Optimize, clear, rebuild FTS, stats, def index."""

    def test_optimize(self, store):
        store.optimize()  # should not raise

    def test_clear(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        store.upsert_file("src/a.py", "hash", "python")
        store.clear()
        assert store.count_nodes() == 0
        assert store.count_edges() == 0
        assert store.get_all_files() == []

    def test_rebuild_fts(self, store):
        store.insert_node(_make_node(name="hello_world"))
        store.flush()
        store.rebuild_fts()  # should not raise
        results = store.fts_search("hello")
        assert len(results) >= 1

    def test_stats(self, store):
        node = _make_node()
        store.insert_node(node)
        store.flush()
        store.upsert_file("src/a.py", "hash", "python", node_count=1)
        s = store.stats()
        assert s["node_count"] == 1
        assert s["file_count"] == 1
        assert "db_size_bytes" in s

    def test_stats_on_memory_db(self):
        s = SqliteStore(":memory:")
        _init_full_schema(s._conn_mgr.conn)
        try:
            st = s.stats()
            assert st["node_count"] == 0
            assert st["edge_count"] == 0
            assert st["file_count"] == 0
            assert st["unresolved_count"] == 0
        finally:
            s.close()


# =============================================================================
# 11. Edge cases & error handling
# =============================================================================

class TestEdgeCases:
    """Closed store, empty inputs, edge cases."""

    def test_closed_raises_store_closed(self, store):
        store.close()
        with pytest.raises(StoreClosedError):
            store.count_nodes()
        with pytest.raises(StoreClosedError):
            store.insert_node(_make_node())
        with pytest.raises(StoreClosedError):
            store.commit()

    def test_close_idempotent(self, store):
        store.close()
        store.close()  # should not raise

    def test_close_flushes_buffered_edges(self, store):
        """close() must flush buffered writes before clearing buffers."""
        # Set auto_flush high so edges stay buffered
        store._auto_flush_size = 10000

        # Insert nodes first (required for edge FK validation)
        n1 = _make_node(kind="function", name="test_func")
        n2 = _make_node(kind="function", name="source_func")
        store.insert_node(n1)
        store.insert_node(n2)
        store.flush()
        assert store.get_node_by_id(n1["id"]) is not None
        assert store.get_node_by_id(n2["id"]) is not None

        # Insert edges below auto_flush threshold — purely buffered
        edges = [
            _make_edge(n1["id"], n2["id"], kind="test_edge",
                       source_loc="test.py",
                       target_text="source_func",
                       provenance="analysis",
                       properties='{"confidence":0.9}')
            for _ in range(50)
        ]
        store.insert_edges(edges)
        # Before close, edges are only in buffer (not yet flushed to DB)
        # Note: count_edges reads from DB, not buffer

        # close() must flush before clearing buffers
        store.close()

        # Reopen with fresh connection to verify persistence
        import sqlite3
        conn = sqlite3.connect(store._conn_mgr.db_path)
        try:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM edges WHERE kind='test_edge'")
            count = c.fetchone()[0]
            assert count == 50, f"Expected 50 edges after close, got {count}"
        finally:
            conn.close()

    def test_get_outgoing_empty_source(self, store):
        with pytest.raises(ValueError):
            store.get_outgoing_edges("")

    def test_get_incoming_empty_target(self, store):
        with pytest.raises(ValueError):
            store.get_incoming_edges("")

    def test_get_edges_between_empty_param(self, store):
        with pytest.raises(ValueError):
            store.get_edges_between("", "tgt")
        with pytest.raises(ValueError):
            store.get_edges_between("src", "")

    def test_find_paths_empty_param(self, store):
        with pytest.raises(ValueError):
            store.find_paths("", "tgt")
        with pytest.raises(ValueError):
            store.find_paths("src", "")

    def test_fts_search_empty_query(self, store):
        with pytest.raises(ValueError):
            store.fts_search("")

    def test_search_by_def_index_empty(self, store):
        with pytest.raises(ValueError):
            store.search_by_def_index("")

    def test_search_by_field_qualified_empty(self, store):
        with pytest.raises(ValueError):
            store.search_by_field_qualified("")

    def test_iter_nodes_by_file_empty_path(self, store):
        with pytest.raises(ValueError):
            list(store.iter_nodes_by_file(""))

    def test_upsert_file_empty_fields(self, store):
        with pytest.raises(ValueError):
            store.upsert_file("", "hash", "python")

    def test_def_index_cache_invalidated_on_write(self, store):
        store.insert_node(_make_node(name="a"))
        store.flush()
        idx1 = store.build_def_index()
        assert len(idx1) == 1
        store.insert_node(_make_node(name="b"))
        store.flush()
        idx2 = store.build_def_index()
        assert len(idx2) == 2  # cache was invalidated

    def test_insert_replace_existing_node(self, store):
        """Inserting a node with same id should REPLACE."""
        node = _make_node(name="original")
        store.insert_node(node)
        store.flush()
        node_v2 = _make_node(name="updated", id=node["id"], qualified_name=node["qualified_name"])
        store.insert_node(node_v2)
        store.flush()
        result = store.get_node_by_id(node["id"])
        assert result["name"] == "updated"

    def test_memory_db(self):
        """SqliteStore should work with :memory: database."""
        s = SqliteStore(":memory:")
        _init_full_schema(s._conn_mgr.conn)
        try:
            node = _make_node()
            s.insert_node(node)
            s.flush()
            assert s.count_nodes() == 1
            assert s.get_node_by_id(node["id"]) is not None
        finally:
            s.close()

    def test_get_neighbors_empty_node_id(self, store):
        with pytest.raises(ValueError):
            store.get_neighbors("")

    def test_get_neighbors_batch_empty_node_ids(self, store):
        with pytest.raises(ValueError):
            store.get_neighbors_batch([])

    def test_iter_edges_from_empty_node_id(self, store):
        with pytest.raises(ValueError):
            list(store.iter_edges_from(""))

    def test_update_edge_provenance_empty(self, store):
        with pytest.raises(ValueError):
            store.update_edge_provenance(1, "")

    def test_update_edge_target_empty(self, store):
        with pytest.raises(ValueError):
            store.update_edge_target(1, "")
