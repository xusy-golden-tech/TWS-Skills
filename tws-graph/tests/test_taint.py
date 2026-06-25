"""TDD tests for P31 v5.3.0 — security taint analysis.

Source→sink path tracking through data_flows edges.
"""

import pytest
import hashlib

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
# P31a: Source detection
# ---------------------------------------------------------------------------

class TestSourceDetection:
    """Identify sensitive data sources (env vars, file reads, user input)."""

    def test_env_get_is_source(self, queries):
        """os.environ.get() should be detected as source."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("os::environ::get") is True

    def test_os_getenv_is_source(self, queries):
        """os.getenv() should be detected as source."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("os::getenv") is True

    def test_input_is_source(self, queries):
        """input() should be detected as source."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("input") is True

    def test_process_env_is_source(self, queries):
        """process.env should be detected as source (TypeScript)."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("process::env") is True

    def test_ordinary_function_not_source(self, queries):
        """Regular function like 'calculate' is NOT a source."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("my_package::calculate") is False

    def test_open_is_source(self, queries):
        """open() for reading is a source (file reads)."""
        from tws_graph.analysis.taint import is_source_node

        assert is_source_node("open") is True


# ---------------------------------------------------------------------------
# P31b: Sink detection
# ---------------------------------------------------------------------------

class TestSinkDetection:
    """Identify dangerous sinks (command exec, SQL, code injection, file write)."""

    def test_os_system_is_sink(self, queries):
        """os.system() should be detected as sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("os::system") is True

    def test_subprocess_run_is_sink(self, queries):
        """subprocess.run() should be detected as sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("subprocess::run") is True

    def test_eval_is_sink(self, queries):
        """eval() should be detected as sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("eval") is True

    def test_exec_is_sink(self, queries):
        """exec() should be detected as sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("exec") is True

    def test_sql_execute_is_sink(self, queries):
        """cursor.execute() should be detected as sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("cursor::execute") is True
        assert is_sink_node("Cursor::execute") is True

    def test_ordinary_function_not_sink(self, queries):
        """Regular function is NOT a sink."""
        from tws_graph.analysis.taint import is_sink_node

        assert is_sink_node("my_package::helper") is False


# ---------------------------------------------------------------------------
# P31c: BFS path finding
# ---------------------------------------------------------------------------

class TestTaintPathFinding:
    """BFS traversal from source to sink through data_flows edges."""

    def test_direct_source_to_sink_path(self, queries):
        """Source → Sink via direct data_flow edge."""
        src_id = _make_id("main.py::get_input", "main.py")
        sink_id = _make_id("main.py::dangerous_cmd", "main.py")

        for nid, kind, name, qname, fpath in [
            (src_id, "function", "get_input", "main.py::get_input", "main.py"),
            (sink_id, "function", "dangerous_cmd", "main.py::dangerous_cmd", "main.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # Data flows from source to sink
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'data_flows','main.py:1','tree-sitter')", (src_id, sink_id))
        queries.conn.commit()

        from tws_graph.analysis.taint import find_taint_paths, is_source_node, is_sink_node

        # Override for testing
        source_fn = lambda qn: "get_input" in str(qn) or "input" in str(qn)
        sink_fn = lambda qn: "dangerous" in str(qn) or "cmd" in str(qn) or "exec" in str(qn)

        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=5)

        assert len(paths) >= 1, f"Should find at least one path, got {paths}"

    def test_multi_hop_taint_path(self, queries):
        """Source → Intermediate → Sink (2-hop data_flow chain)."""
        src_id = _make_id("main.py::get_env", "main.py")
        mid_id = _make_id("main.py::process", "main.py")
        sink_id = _make_id("main.py::run_cmd", "main.py")

        for nid, kind, name, qname, fpath in [
            (src_id, "function", "get_env", "main.py::get_env", "main.py"),
            (mid_id, "function", "process", "main.py::process", "main.py"),
            (sink_id, "function", "run_cmd", "main.py::run_cmd", "main.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # Source → Intermediate → Sink
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'data_flows','main.py:1','tree-sitter')", (src_id, mid_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'data_flows','main.py:2','tree-sitter')", (mid_id, sink_id))
        queries.conn.commit()

        from tws_graph.analysis.taint import find_taint_paths

        source_fn = lambda qn: "env" in str(qn)
        sink_fn = lambda qn: "cmd" in str(qn)

        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=5)
        assert len(paths) >= 1

        # Path should have 3 nodes (source → mid → sink)
        path = paths[0]
        assert len(path["path"]) >= 2

    def test_no_path_when_disconnected(self, queries):
        """Source and sink in disconnected components: no path."""
        src_id = _make_id("a.py::read_file", "a.py")
        sink_id = _make_id("b.py::exec_sql", "b.py")

        for nid, kind, name, qname, fpath in [
            (src_id, "function", "read_file", "a.py::read_file", "a.py"),
            (sink_id, "function", "exec_sql", "b.py::exec_sql", "b.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # No data_flow edge connecting them
        queries.conn.commit()

        from tws_graph.analysis.taint import find_taint_paths

        source_fn = lambda qn: "read" in str(qn)
        sink_fn = lambda qn: "sql" in str(qn)

        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=5)
        assert len(paths) == 0

    def test_depth_limit_respected(self, queries):
        """BFS should respect max_depth."""
        ids: list[str] = []
        for i in range(8):
            nid = _make_id(f"mod.py::step_{i}", "mod.py")
            ids.append(nid)
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, "function", f"step_{i}", f"mod.py::step_{i}", "mod.py"))

        # Chain: 0→1→2→3→4→5→6→7
        for i in range(7):
            queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                           "VALUES (?,?,'data_flows','mod.py:1','tree-sitter')",
                           (ids[i], ids[i+1]))
        queries.conn.commit()

        from tws_graph.analysis.taint import find_taint_paths

        source_fn = lambda qn: "step_0" in str(qn)
        sink_fn = lambda qn: "step_7" in str(qn)

        # depth=2: can't reach step_7 from step_0 in 2 hops
        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=2)
        assert len(paths) == 0, f"With depth=2, should not reach step_7 from step_0"

        # depth=7: should reach
        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=7)
        assert len(paths) >= 1, f"With depth=7, should reach step_7 from step_0"

    def test_path_has_node_details(self, queries):
        """Each path entry should include node name, file_path, and qualified_name."""
        src_id = _make_id("main.py::source_fn", "main.py")
        sink_id = _make_id("main.py::sink_fn", "main.py")

        for nid, kind, name, qname, fpath in [
            (src_id, "function", "source_fn", "main.py::source_fn", "main.py"),
            (sink_id, "function", "sink_fn", "main.py::sink_fn", "main.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'data_flows','main.py:1','tree-sitter')", (src_id, sink_id))
        queries.conn.commit()

        from tws_graph.analysis.taint import find_taint_paths

        source_fn = lambda qn: "source" in str(qn)
        sink_fn = lambda qn: "sink" in str(qn)

        paths = find_taint_paths(queries, source_fn, sink_fn, max_depth=5)
        assert len(paths) >= 1

        path = paths[0]
        assert "path" in path, "Path should have 'path' key with node details"
        for node in path["path"]:
            assert "name" in node, f"Path node missing 'name': {node}"
            assert "file_path" in node, f"Path node missing 'file_path': {node}"

    def test_empty_graph_no_error(self, queries):
        """Empty graph should return empty paths."""
        from tws_graph.analysis.taint import find_taint_paths

        paths = find_taint_paths(queries,
                                 lambda qn: True,
                                 lambda qn: True,
                                 max_depth=5)
        assert paths == []
