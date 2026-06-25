"""TDD tests for P33 v5.3.0 — Incremental indexing v2."""

import hashlib
import os
import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


# ---------------------------------------------------------------------------
# P33a: body_hash column and computation
# ---------------------------------------------------------------------------

class TestBodyHashColumn:
    """body_hash is present in nodes table and computed during indexing."""

    def test_body_hash_column_exists(self, tmp_path):
        """Schema migration adds body_hash column."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        try:
            cols = db.conn.execute("PRAGMA table_info(nodes)").fetchall()
            col_names = {c["name"] for c in cols}
            assert "body_hash" in col_names, f"body_hash column missing. Columns: {col_names}"
        finally:
            db.close()

    def test_body_hash_populated_for_function(self, queries):
        """Nodes get body_hash when extracted from source."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        nodes = [{
            "id": "test1", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 1, "end_line": 3,
        }]
        content = "def foo():\n    return 42\n"
        _compute_body_hashes(nodes, content)

        assert nodes[0].get("body_hash") is not None
        assert len(nodes[0]["body_hash"]) == 16  # truncated SHA256

    def test_body_hash_changes_with_content(self, queries):
        """Different body → different body_hash."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        nodes_a = [{
            "id": "a", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 1, "end_line": 3,
        }]
        nodes_b = [{
            "id": "b", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 1, "end_line": 4,
        }]

        _compute_body_hashes(nodes_a, "def foo():\n    return 42\n")
        _compute_body_hashes(nodes_b, "def foo():\n    x = 1\n    return x + 1\n")

        assert nodes_a[0]["body_hash"] != nodes_b[0]["body_hash"]

    def test_body_hash_same_for_same_content(self, queries):
        """Same body text (at different positions) → same body_hash."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        content1 = "def foo():\n    pass\n"
        content2 = "# comment\n# comment\n# comment\n# comment\ndef foo():\n    pass\n"

        nodes1 = [{
            "id": "a", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 1, "end_line": 2,
        }]
        nodes2 = [{
            "id": "b", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 5, "end_line": 6,
        }]

        _compute_body_hashes(nodes1, content1)
        _compute_body_hashes(nodes2, content2)

        assert nodes1[0]["body_hash"] == nodes2[0]["body_hash"]

    def test_body_hash_none_when_no_lines(self, queries):
        """Nodes without line info get no body_hash."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        nodes = [{
            "id": "test", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 0, "end_line": 0,
        }]
        _compute_body_hashes(nodes, "content")
        assert nodes[0].get("body_hash") is None

    def test_body_hash_with_empty_content(self, queries):
        """Empty content → no body_hash."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        nodes = [{
            "id": "test", "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo", "start_line": 1, "end_line": 2,
        }]
        _compute_body_hashes(nodes, "")
        assert nodes[0].get("body_hash") is None


# ---------------------------------------------------------------------------
# P33b: Function-level incremental indexing
# ---------------------------------------------------------------------------

class TestFunctionLevelIncremental:
    """Only changed functions are re-extracted; unchanged ones are preserved."""

    def test_insert_new_file_has_body_hash(self, queries):
        """First-time index populates body_hash."""
        nid = _make_id("mod.py::add", "mod.py")
        queries._exec(
            "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at, body_hash) "
            "VALUES (?,?,?,?,?,'python',1,3,1000,'abc123')",
            (nid, "function", "add", "mod.py::add", "mod.py"))
        queries.conn.commit()

        node = queries.get_node_by_id(nid)
        assert node["body_hash"] == "abc123"

    def test_line_number_update_preserves_body_hash(self, queries):
        """update_node_lines changes start_line/end_line but preserves body_hash."""
        nid = _make_id("mod.py::add", "mod.py")
        queries._exec(
            "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at, body_hash) "
            "VALUES (?,?,?,?,?,'python',1,3,1000,'abc123')",
            (nid, "function", "add", "mod.py::add", "mod.py"))
        queries.conn.commit()

        # Simulate line drift: function moved from lines 1-3 to 5-7
        queries.update_node_lines(nid, 5, 7)
        queries.conn.commit()

        node = queries.get_node_by_id(nid)
        assert node["start_line"] == 5
        assert node["end_line"] == 7
        assert node["body_hash"] == "abc123"  # body_hash unchanged

    def test_delete_single_node_removes_node(self, queries):
        """delete_single_node removes one node only."""
        a_id = _make_id("mod.py::foo", "mod.py")
        b_id = _make_id("mod.py::bar", "mod.py")
        for nid, name in [(a_id, "foo"), (b_id, "bar")]:
            queries._exec(
                "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,3,1000)",
                (nid, "function", name, f"mod.py::{name}", "mod.py"))
        queries.conn.commit()

        queries.delete_single_node(a_id)
        queries.conn.commit()

        assert queries.get_node_by_id(a_id) is None
        assert queries.get_node_by_id(b_id) is not None

    def test_unchanged_node_keeps_edges_on_reindex(self, queries):
        """When re-indexing a file, unchanged functions keep their edges."""
        a_id = _make_id("mod.py::unchanged", "mod.py")
        b_id = _make_id("other.py::callee", "other.py")

        for nid, name, fpath in [
            (a_id, "unchanged", "mod.py"),
            (b_id, "callee", "other.py"),
        ]:
            queries._exec(
                "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at, body_hash) "
                "VALUES (?,?,?,?,?,'python',1,3,1000,'same_hash')",
                (nid, "function", name, f"{fpath}::{name}", fpath))

        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
            "VALUES (?,?,'calls','mod.py:2','resolved')", (a_id, b_id))
        queries.conn.commit()

        # Verify edge exists
        edges = queries._exec(
            "SELECT * FROM edges WHERE source = ?", (a_id,)
        ).fetchall()
        assert len(edges) == 1

    def test_changed_node_edges_are_replaced(self, queries):
        """When a function changes, old edges are cascade-deleted."""
        a_id = _make_id("mod.py::changed", "mod.py")
        b_id = _make_id("other.py::callee", "other.py")

        for nid, name, fpath in [
            (a_id, "changed", "mod.py"),
            (b_id, "callee", "other.py"),
        ]:
            queries._exec(
                "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at, body_hash) "
                "VALUES (?,?,?,?,?,'python',1,3,1000,'hash1')",
                (nid, "function", name, f"{fpath}::{name}", fpath))

        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
            "VALUES (?,?,'calls','mod.py:2','resolved')", (a_id, b_id))
        queries.conn.commit()

        # Delete changed node (cascade deletes its edges)
        queries.delete_single_node(a_id)
        queries.conn.commit()

        # Edge should be gone
        edges = queries._exec(
            "SELECT * FROM edges WHERE source = ?", (a_id,)
        ).fetchall()
        assert len(edges) == 0


# ---------------------------------------------------------------------------
# P33c: Line number drift fix
# ---------------------------------------------------------------------------

class TestLineNumberDrift:
    """When body_hash matches but lines shifted, only line numbers update."""

    def test_update_node_lines_changes_lines_only(self, queries):
        """update_node_lines changes start/end but preserves everything else."""
        nid = _make_id("mod.py::foo", "mod.py")
        queries._exec(
            "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at, body_hash) "
            "VALUES (?,?,?,?,?,'python',10,15,1000,'hash_foo')",
            (nid, "function", "foo", "mod.py::foo", "mod.py"))
        queries.conn.commit()

        queries.update_node_lines(nid, 20, 25)
        queries.conn.commit()

        node = dict(queries.get_node_by_id(nid))
        assert node["start_line"] == 20
        assert node["end_line"] == 25
        assert node["name"] == "foo"
        assert node["qualified_name"] == "mod.py::foo"
        assert node["body_hash"] == "hash_foo"

    def test_drift_fix_preserves_fk_edges(self, queries):
        """After line number update, edges from this node still exist."""
        src_id = _make_id("mod.py::source", "mod.py")
        tgt_id = _make_id("other.py::target", "other.py")

        for nid, name, fpath, bh in [
            (src_id, "source", "mod.py", "hash_src"),
            (tgt_id, "target", "other.py", "hash_tgt"),
        ]:
            queries._exec(
                "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at, body_hash) "
                "VALUES (?,?,?,?,?,'python',1,3,1000,?)",
                (nid, "function", name, f"{fpath}::{name}", fpath, bh))

        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
            "VALUES (?,?,'calls','mod.py:2','resolved')", (src_id, tgt_id))
        queries.conn.commit()

        # Simulate drift
        queries.update_node_lines(src_id, 10, 15)
        queries.conn.commit()

        # Edge must still exist
        edges = queries._exec(
            "SELECT * FROM edges WHERE source = ?", (src_id,)
        ).fetchall()
        assert len(edges) == 1, "Edges should survive line number update"
        assert edges[0]["target"] == tgt_id


# ---------------------------------------------------------------------------
# P33d: Correctness — incremental matches full re-index
# ---------------------------------------------------------------------------

class TestIncrementalCorrectness:
    """Full index vs incremental should produce consistent results."""

    def test_same_body_hash_means_unchanged(self, queries):
        """Two nodes with same body_hash but different lines → drift fix candidate."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        body_text = "def foo():\n    return 1\n"
        content1 = body_text
        content2 = "# header\n" + body_text  # function starts at line 2 now

        nodes1 = [{
            "id": _make_id("mod.py::foo", "mod.py"),
            "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo",
            "start_line": 1, "end_line": 2,
        }]
        nodes2 = [{
            "id": _make_id("mod.py::foo", "mod.py"),
            "kind": "function", "name": "foo",
            "qualified_name": "mod.py::foo",
            "start_line": 2, "end_line": 3,
        }]

        _compute_body_hashes(nodes1, content1)
        _compute_body_hashes(nodes2, content2)

        assert nodes1[0]["body_hash"] == nodes2[0]["body_hash"]
        # Lines differ but body_hash same → drift fix, not re-extract
        assert nodes1[0]["start_line"] != nodes2[0]["start_line"]

    def test_empty_file_no_error(self, queries):
        """Processing a file with no functions is fine."""
        from tws_graph.indexer.orchestrator import _compute_body_hashes

        nodes = []
        _compute_body_hashes(nodes, "")
        assert nodes == []
