"""TDD tests for P36 v5.4.0 — Dead code detection v2 (cross-file)."""

import hashlib
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


@pytest.fixture
def populated_queries(queries):
    """Graph with:
    main → helper → util (all live)
    orphan — no callers, not an entry point (dead)
    test_main — test file, ignored
    """
    main_id = _make_id("src/main.py::main", "src/main.py")
    helper_id = _make_id("src/lib.py::helper", "src/lib.py")
    util_id = _make_id("src/lib.py::util", "src/lib.py")
    orphan_id = _make_id("src/dead.py::orphan", "src/dead.py")
    test_id = _make_id("tests/test_main.py::test_main", "tests/test_main.py")

    for nid, kind, name, qname, fpath in [
        (main_id, "function", "main", "src/main.py::main", "src/main.py"),
        (helper_id, "function", "helper", "src/lib.py::helper", "src/lib.py"),
        (util_id, "function", "util", "src/lib.py::util", "src/lib.py"),
        (orphan_id, "function", "orphan", "src/dead.py::orphan", "src/dead.py"),
        (test_id, "function", "test_main", "tests/test_main.py::test_main", "tests/test_main.py"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',1,2,1000)",
            (nid, kind, name, qname, fpath))

    # Call chain: main → helper → util
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','main.py:2','resolved')", (main_id, helper_id))
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','lib.py:2','resolved')", (helper_id, util_id))
    # test_main → main (test calling production code)
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','test_main.py:2','resolved')", (test_id, main_id))
    queries.conn.commit()
    return queries


# ---------------------------------------------------------------------------
# P36a: Reachability analysis
# ---------------------------------------------------------------------------

class TestReachability:
    """BFS from entry points to find reachable code."""

    def test_entry_points_detected(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import find_entry_points

        entries = find_entry_points(populated_queries)
        main_id = _make_id("src/main.py::main", "src/main.py")
        assert main_id in entries, f"main should be entry point, got {list(entries)[:5]}"

    def test_reachable_nodes_identified(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import find_reachable

        entries = [_make_id("src/main.py::main", "src/main.py")]
        reachable = find_reachable(populated_queries, entries)

        helper_id = _make_id("src/lib.py::helper", "src/lib.py")
        util_id = _make_id("src/lib.py::util", "src/lib.py")
        assert helper_id in reachable, "helper should be reachable from main"
        assert util_id in reachable, "util should be reachable from main→helper→util"

    def test_orphan_not_reachable(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import find_reachable

        entries = [_make_id("src/main.py::main", "src/main.py")]
        reachable = find_reachable(populated_queries, entries)

        orphan_id = _make_id("src/dead.py::orphan", "src/dead.py")
        assert orphan_id not in reachable, "orphan should not be reachable"

    def test_deep_chain_reachable(self, queries):
        """A→B→C→D: all reachable from A."""
        from tws_graph.analysis.dead_code_v2 import find_reachable

        ids = []
        for i, name in enumerate(["a", "b", "c", "d"]):
            nid = _make_id(f"mod.py::{name}", "mod.py")
            ids.append(nid)
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, "function", name, f"mod.py::{name}", "mod.py"))

        for i in range(3):
            queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                           "VALUES (?,?,'calls','mod.py:1','resolved')", (ids[i], ids[i+1]))
        queries.conn.commit()

        reachable = find_reachable(queries, {ids[0]})
        assert all(nid in reachable for nid in ids), "All nodes in chain should be reachable"

    def test_empty_graph(self, queries):
        from tws_graph.analysis.dead_code_v2 import find_reachable, find_entry_points

        entries = find_entry_points(queries)
        reachable = find_reachable(queries, entries)
        assert reachable == set()


# ---------------------------------------------------------------------------
# P36b: Dead code classification
# ---------------------------------------------------------------------------

class TestDeadCodeClassification:
    """Classify unreachable vs unused."""

    def test_dead_code_detected(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        orphan_id = _make_id("src/dead.py::orphan", "src/dead.py")
        assert orphan_id in result["dead_nodes"], "orphan should be dead"

    def test_live_code_not_dead(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        main_id = _make_id("src/main.py::main", "src/main.py")
        assert main_id not in result["dead_nodes"]

    def test_test_files_excluded(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        test_id = _make_id("tests/test_main.py::test_main", "tests/test_main.py")
        assert test_id not in result["dead_nodes"], \
            "test functions should be excluded from dead code"

    def test_dead_code_has_details(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        for detail in result.get("dead_details", []):
            assert "name" in detail
            assert "file_path" in detail
            assert "reason" in detail

    def test_empty_graph(self, queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(queries)
        assert result["dead_nodes"] == []
        assert result["live_count"] == 0


# ---------------------------------------------------------------------------
# P36c: Report generation
# ---------------------------------------------------------------------------

class TestDeadCodeReport:
    """Per-file dead code report."""

    def test_report_groups_by_file(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        assert "by_file" in result
        # orphan is in src/dead.py
        orphan_found = False
        for fpath, nodes in result["by_file"].items():
            if "src/dead.py" in fpath:
                orphan_found = True
                assert len(nodes) >= 1
        assert orphan_found, "src/dead.py should be in by_file report"

    def test_report_has_counts(self, populated_queries):
        from tws_graph.analysis.dead_code_v2 import detect_dead_code

        result = detect_dead_code(populated_queries)

        assert "live_count" in result
        assert "dead_count" in result
        assert "total_analyzed" in result
        assert result["dead_count"] >= 1
        assert result["live_count"] >= 3  # main, helper, util
