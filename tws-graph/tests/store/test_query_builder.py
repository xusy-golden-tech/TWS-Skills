"""Tests for store.query_builder — SQL builder for all graph operations."""

import sqlite3
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    db_dir = tmp_path / "qb_test"
    db_dir.mkdir()
    return str(db_dir / "test.db")


@pytest.fixture
def conn_mgr(temp_db_path: str):
    """Create a ConnectionManager with schema initialized."""
    from tws_graph.store.connection import ConnectionManager
    mgr = ConnectionManager(temp_db_path)
    # Minimal schema for query_builder tests
    _init_schema(mgr.conn)
    yield mgr
    mgr.close()


@pytest.fixture
def qb(conn_mgr):
    """Create a QueryBuilder from ConnectionManager."""
    from tws_graph.store.query_builder import QueryBuilder
    return QueryBuilder(conn_mgr)


# ---------------------------------------------------------------------------
# Schema initialisation helper
# ---------------------------------------------------------------------------

def _init_schema(conn: sqlite3.Connection):
    """Create minimal schema tables for testing query_builder."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL,
            description TEXT
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
    """)


# ---------------------------------------------------------------------------
# Helpers to create test data
# ---------------------------------------------------------------------------

def _make_node(**overrides) -> dict:
    import hashlib
    name = overrides.get("name", "test_func")
    fpath = overrides.get("file_path", "src/test.py")
    qname = overrides.get("qualified_name", f"{fpath}::{name}")
    raw = f"{fpath}:{qname}"
    node_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return {
        "id": node_id,
        "kind": "function",
        "name": name,
        "qualified_name": qname,
        "file_path": fpath,
        "language": "python",
        "start_line": 1,
        "end_line": 5,
        **{k: v for k, v in overrides.items() if k not in ("id", "qualified_name")},
    }


def _make_edge(source: str, target: str, kind: str = "calls", **overrides) -> dict:
    return {
        "source": source,
        "target": target,
        "kind": kind,
        "target_text": target,
        "source_loc": "src/test.py:3:5",
        "provenance": "tree-sitter",
        **overrides,
    }


# ===========================================================================
# Constructor
# ===========================================================================

class TestConstructor:
    """QueryBuilder constructor tests."""

    def test_accepts_connection_manager(self, conn_mgr):
        """QueryBuilder should accept ConnectionManager instance."""
        from tws_graph.store.query_builder import QueryBuilder
        qb = QueryBuilder(conn_mgr)
        assert qb._conn_mgr is conn_mgr
        assert qb._conn_mgr.conn is not None

    def test_conn_property_works(self, qb):
        """Internal conn access via conn_mgr should work."""
        result = qb._conn_mgr.conn.execute("SELECT 1 AS v").fetchone()
        assert result["v"] == 1

    def test_not_exported_from_store_init(self):
        """QueryBuilder should NOT be in store.__all__."""
        from tws_graph.store import __all__ as store_all
        assert "QueryBuilder" not in store_all


# ===========================================================================
# Node CRUD
# ===========================================================================

class TestNodeCRUD:
    """Tests for node insert/read/delete operations."""

    def test_insert_and_get_by_id(self, qb):
        node = _make_node()
        qb.insert_node(node)
        row = qb.get_node_by_id(node["id"])
        assert row is not None
        assert row["name"] == "test_func"

    def test_get_nonexistent_node(self, qb):
        assert qb.get_node_by_id("no_such_id") is None

    def test_insert_nodes_batch(self, qb):
        nodes = [_make_node(name=f"func_{i}") for i in range(5)]
        qb.insert_nodes(nodes)
        assert qb.get_node_count() == 5

    def test_get_nodes_by_ids(self, qb):
        nodes = [_make_node(name=f"func_{i}") for i in range(3)]
        qb.insert_nodes(nodes)
        ids = [n["id"] for n in nodes]
        result = qb.get_nodes_by_ids(ids)
        assert len(result) == 3
        for nid in ids:
            assert nid in result

    def test_get_nodes_by_ids_empty(self, qb):
        assert qb.get_nodes_by_ids([]) == {}

    def test_get_nodes_by_name(self, qb):
        qb.insert_node(_make_node(name="uniqueName"))
        rows = qb.get_nodes_by_name("uniqueName")
        assert len(rows) >= 1
        assert rows[0]["name"] == "uniqueName"

    def test_get_nodes_by_file(self, qb):
        qb.insert_node(_make_node(file_path="src/a.py"))
        qb.insert_node(_make_node(file_path="src/a.py", name="func2"))
        qb.insert_node(_make_node(file_path="src/b.py", name="func3"))
        rows = qb.get_nodes_by_file("src/a.py")
        assert len(rows) == 2

    def test_delete_nodes_by_file(self, qb):
        qb.insert_node(_make_node(file_path="src/delete_me.py"))
        assert qb.get_node_count() == 1
        qb.delete_nodes_by_file("src/delete_me.py")
        assert qb.get_node_count() == 0

    def test_get_node_count(self, qb):
        assert qb.get_node_count() == 0
        qb.insert_node(_make_node())
        assert qb.get_node_count() == 1

    def test_iterate_nodes_by_kind(self, qb):
        qb.insert_node(_make_node(kind="function", name="f1"))
        qb.insert_node(_make_node(kind="function", name="f2"))
        qb.insert_node(_make_node(kind="class", name="C1"))
        results = list(qb.iterate_nodes_by_kind("function"))
        assert len(results) == 2

    def test_search_nodes_fts(self, qb):
        qb.insert_node(_make_node(name="calculateTotal", qualified_name="src/calc.py::calculateTotal"))
        results = qb.search_nodes("calculate")
        assert len(results) >= 1

    def test_search_nodes_field_qualified(self, qb):
        qb.insert_node(_make_node(kind="function", language="python", name="my_func"))
        qb.insert_node(_make_node(kind="class", language="python", name="MyClass"))
        results = qb.search_nodes_field_qualified("kind:class lang:python my")
        assert len(results) >= 1
        assert any(r["kind"] == "class" for r in results)

    def test_search_nodes_field_qualified_filter_only(self, qb):
        qb.insert_node(_make_node(kind="class", language="python", name="MyClass"))
        results = qb.search_nodes_field_qualified("kind:class")
        assert len(results) >= 1


# ===========================================================================
# Edge CRUD
# ===========================================================================

class TestEdgeCRUD:
    """Tests for edge insert/read/update operations."""

    def test_insert_edge(self, qb):
        n1 = _make_node(name="caller")
        n2 = _make_node(name="callee")
        qb.insert_nodes([n1, n2])
        edge = _make_edge(n1["id"], n2["id"], "calls")
        qb.insert_edge(edge)
        assert qb.get_edge_count() == 1

    def test_insert_edges_batch(self, qb):
        n1 = _make_node(name="caller")
        n2 = _make_node(name="callee1")
        n3 = _make_node(name="callee2")
        qb.insert_nodes([n1, n2, n3])
        edges = [
            _make_edge(n1["id"], n2["id"], "calls"),
            _make_edge(n1["id"], n3["id"], "calls"),
        ]
        qb.insert_edges(edges)
        assert qb.get_edge_count() == 2

    def test_insert_edges_empty(self, qb):
        qb.insert_edges([])
        assert qb.get_edge_count() == 0

    def test_get_outgoing_edges(self, qb):
        n1 = _make_node(name="src")
        n2 = _make_node(name="tgt1")
        n3 = _make_node(name="tgt2")
        qb.insert_nodes([n1, n2, n3])
        qb.insert_edges([
            _make_edge(n1["id"], n2["id"], "calls"),
            _make_edge(n1["id"], n3["id"], "imports"),
        ])
        all_out = qb.get_outgoing_edges(n1["id"])
        assert len(all_out) == 2
        calls_only = qb.get_outgoing_edges(n1["id"], kinds=["calls"])
        assert len(calls_only) == 1

    def test_get_incoming_edges(self, qb):
        n1 = _make_node(name="caller1")
        n2 = _make_node(name="caller2")
        n3 = _make_node(name="target")
        qb.insert_nodes([n1, n2, n3])
        qb.insert_edges([
            _make_edge(n1["id"], n3["id"], "calls"),
            _make_edge(n2["id"], n3["id"], "calls"),
        ])
        in_edges = qb.get_incoming_edges(n3["id"])
        assert len(in_edges) == 2

    def test_get_edge_count(self, qb):
        assert qb.get_edge_count() == 0
        n1 = _make_node(name="a")
        n2 = _make_node(name="b")
        qb.insert_nodes([n1, n2])
        qb.insert_edge(_make_edge(n1["id"], n2["id"]))
        assert qb.get_edge_count() == 1

    def test_get_dangling_call_edges(self, qb):
        n1 = _make_node(name="caller")
        qb.insert_node(n1)
        # Direct insert to bypass source-existence check
        qb._exec(
            "INSERT INTO edges (source, target, target_text, kind) VALUES (?,?,?,?)",
            (n1["id"], "nonexistent_hash", "nonexistent_target", "calls"),
        )
        dangling = qb.get_dangling_call_edges()
        assert len(dangling) >= 1

    def test_get_all_callable_nodes(self, qb):
        qb.insert_node(_make_node(kind="function", name="f1"))
        qb.insert_node(_make_node(kind="class", name="C1"))
        qb.insert_node(_make_node(kind="variable", name="v1"))
        nodes = qb.get_all_callable_nodes()
        kinds = {n["kind"] for n in nodes}
        assert "function" in kinds
        assert "class" in kinds
        assert "variable" not in kinds

    def test_update_edge_target(self, qb):
        n1 = _make_node(name="caller")
        n2 = _make_node(name="old_target")
        n3 = _make_node(name="new_target")
        qb.insert_nodes([n1, n2, n3])
        qb.insert_edge(_make_edge(n1["id"], n2["id"]))
        rows = qb._exec("SELECT rowid AS e_rowid FROM edges WHERE source = ?", (n1["id"],)).fetchall()
        edge_rowid = rows[0]["e_rowid"]
        qb.update_edge_target(edge_rowid, n3["id"], "resolved")
        edge = qb.get_outgoing_edges(n1["id"])[0]
        assert edge["target"] == n3["id"]
        assert edge["provenance"] == "resolved"

    def test_mark_edge_provenance(self, qb):
        n1 = _make_node(name="caller")
        n2 = _make_node(name="target")
        qb.insert_nodes([n1, n2])
        qb.insert_edge(_make_edge(n1["id"], n2["id"]))
        rows = qb._exec("SELECT rowid AS e_rowid FROM edges WHERE source = ?", (n1["id"],)).fetchall()
        edge_rowid = rows[0]["e_rowid"]
        qb.mark_edge_provenance(edge_rowid, "ambiguous")
        edge = qb.get_outgoing_edges(n1["id"])[0]
        assert edge["provenance"] == "ambiguous"


# ===========================================================================
# File CRUD
# ===========================================================================

class TestFileCRUD:
    """Tests for file record operations."""

    def test_upsert_and_get_file(self, qb):
        qb.upsert_file("src/test.py", "abc123", "python", 5, 100, 1700000000)
        row = qb.get_file_by_path("src/test.py")
        assert row is not None
        assert row["content_hash"] == "abc123"
        assert row["node_count"] == 5

    def test_get_nonexistent_file(self, qb):
        assert qb.get_file_by_path("nonexistent.py") is None

    def test_get_all_files(self, qb):
        qb.upsert_file("src/a.py", "h1", "python")
        qb.upsert_file("src/b.py", "h2", "python")
        files = qb.get_all_files()
        assert len(files) == 2

    def test_get_file_stats(self, qb):
        qb.upsert_file("src/a.py", "h1", "python", size=100, modified_at=1000)
        qb.upsert_file("src/b.py", "h2", "python", size=200, modified_at=2000)
        stats = qb.get_file_stats()
        assert stats["src/a.py"] == (100, 1000)
        assert stats["src/b.py"] == (200, 2000)

    def test_delete_file(self, qb):
        qb.upsert_file("src/del.py", "h", "python")
        assert qb.get_file_count() == 1
        qb.delete_file("src/del.py")
        assert qb.get_file_count() == 0

    def test_get_file_count(self, qb):
        assert qb.get_file_count() == 0
        qb.upsert_file("src/a.py", "h1", "python")
        assert qb.get_file_count() == 1


# ===========================================================================
# Unresolved Refs CRUD
# ===========================================================================

class TestUnresolvedRefsCRUD:
    """Tests for unresolved reference operations."""

    def test_insert_and_get_by_file(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        ref = {
            "from_node_id": node["id"],
            "reference_name": "unknown_func",
            "reference_kind": "call",
            "line": 10,
            "col": 5,
            "file_path": "src/test.py",
            "language": "python",
        }
        qb.insert_unresolved_ref(ref)
        rows = qb.get_unresolved_refs_by_file("src/test.py")
        assert len(rows) == 1
        assert rows[0]["reference_name"] == "unknown_func"

    def test_insert_unresolved_refs_batch(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        refs = [
            {"from_node_id": node["id"], "reference_name": f"unknown_{i}",
             "file_path": "src/test.py", "language": "python", "line": i, "col": 0}
            for i in range(3)
        ]
        qb.insert_unresolved_refs(refs)
        assert qb.get_unresolved_ref_count() == 3

    def test_get_by_node(self, qb):
        n1 = _make_node(name="c1")
        n2 = _make_node(name="c2")
        qb.insert_nodes([n1, n2])
        qb.insert_unresolved_ref({"from_node_id": n1["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        qb.insert_unresolved_ref({"from_node_id": n2["id"], "reference_name": "u2",
                                   "file_path": "src/a.py", "language": "python"})
        rows = qb.get_unresolved_refs_by_node(n1["id"])
        assert len(rows) == 1
        assert rows[0]["reference_name"] == "u1"

    def test_get_all_unresolved_refs(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u2",
                                   "file_path": "src/b.py", "language": "python"})
        all_refs = qb.get_all_unresolved_refs()
        assert len(all_refs) == 2

    def test_get_unresolved_ref_count(self, qb):
        assert qb.get_unresolved_ref_count() == 0
        node = _make_node(name="caller")
        qb.insert_node(node)
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        assert qb.get_unresolved_ref_count() == 1

    def test_delete_unresolved_refs_by_file(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u2",
                                   "file_path": "src/b.py", "language": "python"})
        qb.delete_unresolved_refs_by_file("src/a.py")
        assert qb.get_unresolved_ref_count() == 1

    def test_update_unresolved_candidates(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        rows = qb.get_unresolved_refs_by_file("src/a.py")
        ref_id = rows[0]["id"]
        qb.update_unresolved_candidates(ref_id, ["cand1", "cand2"])
        updated = qb.get_unresolved_refs_by_file("src/a.py")[0]
        assert updated["candidates"] is not None

    def test_clear_unresolved_refs(self, qb):
        node = _make_node(name="caller")
        qb.insert_node(node)
        qb.insert_unresolved_ref({"from_node_id": node["id"], "reference_name": "u1",
                                   "file_path": "src/a.py", "language": "python"})
        qb.clear_unresolved_refs()
        assert qb.get_unresolved_ref_count() == 0


# ===========================================================================
# FTS Maintenance
# ===========================================================================

class TestFTS:
    """Tests for FTS maintenance operations."""

    def test_rebuild_fts(self, qb):
        """rebuild_fts should execute without error."""
        qb.insert_node(_make_node(name="test_func"))
        qb.rebuild_fts()
        # After rebuild, search should still work
        results = qb.search_nodes("test_func")
        assert len(results) >= 1


# ===========================================================================
# Maintenance / Clear
# ===========================================================================

class TestMaintenance:
    """Tests for maintenance operations."""

    def test_get_stats(self, qb):
        qb.insert_node(_make_node(name="f1"))
        qb.insert_node(_make_node(name="f2"))
        qb.upsert_file("src/test.py", "h1", "python")
        stats = qb.get_stats()
        assert stats["node_count"] == 2
        assert stats["file_count"] == 1

    def test_clear(self, qb):
        qb.insert_node(_make_node(name="f1"))
        qb.insert_node(_make_node(name="f2"))
        qb.upsert_file("src/test.py", "h1", "python")
        qb.clear()
        assert qb.get_node_count() == 0
        assert qb.get_file_count() == 0
        assert qb.get_edge_count() == 0


# ===========================================================================
# Module-level helpers
# ===========================================================================

class TestHelpers:
    """Tests for module-level helper functions in query_builder."""

    def test_hash_id_deterministic(self):
        from tws_graph.store.query_builder import _hash_id
        h1 = _hash_id("src/test.py::my_func", "src/test.py")
        h2 = _hash_id("src/test.py::my_func", "src/test.py")
        assert h1 == h2
        assert len(h1) == 32

    def test_hash_id_different_inputs(self):
        from tws_graph.store.query_builder import _hash_id
        h1 = _hash_id("src/a.py::f1", "src/a.py")
        h2 = _hash_id("src/b.py::f1", "src/b.py")
        assert h1 != h2

    def test_to_json(self):
        from tws_graph.store.query_builder import _to_json
        assert _to_json(None) is None
        assert _to_json(["a", "b"]) == '["a", "b"]'

    def test_edit_distance_identical(self):
        from tws_graph.store.query_builder import _edit_distance
        assert _edit_distance("abc", "abc") == 0

    def test_edit_distance_substitution(self):
        from tws_graph.store.query_builder import _edit_distance
        assert _edit_distance("abc", "abd") == 1

    def test_parse_field_qualifiers(self):
        from tws_graph.store.query_builder import _parse_field_qualifiers
        r = _parse_field_qualifiers("kind:function lang:python api")
        assert r["text"] == ["api"]
        assert "kind" in r["filters"]
        assert r["filters"]["kind"] == "function"

    def test_build_fts_query(self):
        from tws_graph.store.query_builder import QueryBuilder
        q = QueryBuilder._build_fts_query("hello world")
        assert "hello" in q
        assert "world" in q


# ===========================================================================
# Compat layer verification
# ===========================================================================

class TestCompatLayer:
    """Verify db/queries.py compat layer still works."""

    def test_import_from_db_queries(self):
        """from tws_graph.db.queries import QueryBuilder should work."""
        from tws_graph.db.queries import QueryBuilder as DBQueryBuilder
        from tws_graph.store.query_builder import QueryBuilder as StoreQueryBuilder
        assert DBQueryBuilder is StoreQueryBuilder

    def test_import_helper_from_db_queries(self):
        """Module-level helpers should be re-exported."""
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("test", "test") == 0

    def test_conftest_pattern_works(self, temp_db_path):
        """The existing conftest.py pattern should work with compat layer."""
        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder
        conn = DatabaseConnection.initialize(temp_db_path)
        try:
            qb = QueryBuilder(conn.conn)  # passing raw conn through compat
            qb.insert_node(_make_node(name="test"))
            assert qb.get_node_count() == 1
        finally:
            conn.close()
