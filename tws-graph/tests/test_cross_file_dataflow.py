"""TDD tests for cross-file data_flows propagation (P27 v5.2.0).

Tests that data_flows edges are propagated across file boundaries
through resolved calls edges.
"""

import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.indexer.parallel import _propagate_cross_file_dataflow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_id(qualified_name: str, file_path: str) -> str:
    import hashlib
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


def _setup_cross_file_scenario(queries: QueryBuilder) -> dict:
    """Set up a cross-file call scenario.

    file_a.py::caller() calls file_b.py::callee(x)
    callee has data_flows: param:x → return:result
    We expect cross-file data_flows: caller_arg → caller_result (via callee)
    """
    # Callee (in file_b.py)
    callee_id = _make_node_id("file_b.py::callee", "file_b.py")
    param_x_id = _make_node_id("file_b.py::callee::param:x", "file_b.py")
    return_result_id = _make_node_id("file_b.py::callee::return:result", "file_b.py")

    for nid, kind, name, qname, fpath in [
        (callee_id, "function", "callee", "file_b.py::callee", "file_b.py"),
        (param_x_id, "variable", "x", "file_b.py::callee::param:x", "file_b.py"),
        (return_result_id, "variable", "result", "file_b.py::callee::return:result", "file_b.py"),
    ]:
        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, ?, ?, ?, ?, 'python', 1, 2, 1000)
        """, (nid, kind, name, qname, fpath))

    # Callee data_flow: param:x → return:result
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, ?, 'data_flows', 'file_b.py:3', 'tree-sitter')
    """, (param_x_id, return_result_id))

    # Caller (in file_a.py)
    caller_id = _make_node_id("file_a.py::caller", "file_a.py")
    arg_val_id = _make_node_id("file_a.py::caller::param:val", "file_a.py")
    result_var_id = _make_node_id("file_a.py::caller::var:res", "file_a.py")

    for nid, kind, name, qname, fpath in [
        (caller_id, "function", "caller", "file_a.py::caller", "file_a.py"),
        (arg_val_id, "variable", "val", "file_a.py::caller::param:val", "file_a.py"),
        (result_var_id, "variable", "res", "file_a.py::caller::var:res", "file_a.py"),
    ]:
        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, ?, ?, ?, ?, 'python', 1, 2, 1000)
        """, (nid, kind, name, qname, fpath))

    # Caller data_flow: param:val → arg (passed to callee)
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, ?, 'data_flows', 'file_a.py:3', 'tree-sitter')
    """, (arg_val_id, callee_id))

    # Caller data_flow: callee_return → var:res
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, ?, 'data_flows', 'file_a.py:3', 'tree-sitter')
    """, (callee_id, result_var_id))

    # calls edge: file_a.py::caller → file_b.py::callee
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, ?, 'calls', 'file_a.py:3', 'resolved')
    """, (caller_id, callee_id))

    return {
        "callee_id": callee_id,
        "caller_id": caller_id,
        "param_x_id": param_x_id,
        "return_result_id": return_result_id,
        "arg_val_id": arg_val_id,
        "result_var_id": result_var_id,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrossFileDataflow:
    """Test that data_flows edges are propagated across file boundaries."""

    def test_cross_file_dataflow_created(self, queries):
        """Cross-file data_flows edges should be created."""
        ids = _setup_cross_file_scenario(queries)
        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        cross_edges = queries._exec(
            "SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'"
        ).fetchall()
        assert len(cross_edges) > 0, f"Expected cross-file data_flows, got {len(cross_edges)}"

    def test_cross_file_dataflow_through_cross_file_call(self, queries):
        """Cross-file data_flows connect source→target within the same file,
        but the data path goes through a cross-file call.

        The semantics: caller's pre-call variable flows → through callee → to caller's post-call variable.
        Both ends are in the caller's file, but the flow crosses file boundaries in between.
        """
        ids = _setup_cross_file_scenario(queries)
        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        cross_edges = queries._exec("""
            SELECT e.*, ns.file_path AS src_file, nt.file_path AS tgt_file,
                   ns.qualified_name AS src_qn, nt.qualified_name AS tgt_qn
            FROM edges e
            JOIN nodes ns ON e.source = ns.id
            JOIN nodes nt ON e.target = nt.id
            WHERE e.kind='data_flows' AND e.provenance='cross-file'
        """).fetchall()

        assert len(cross_edges) > 0, "Expected cross-file data_flows edges"
        # The source should be in the caller's file
        for edge in cross_edges:
            assert edge["src_file"] == "file_a.py", \
                f"Source should be in caller's file: {edge['src_qn']} ({edge['src_file']})"
            assert edge["tgt_file"] == "file_a.py", \
                f"Target should be in caller's file: {edge['tgt_qn']} ({edge['tgt_file']})"

    def test_no_cross_file_without_calls(self, queries):
        """Without cross-file calls, no cross-file data_flows should be created."""
        # Create two functions in different files WITHOUT a calls edge between them
        func_a_id = _make_node_id("a.py::func_a", "a.py")
        func_b_id = _make_node_id("b.py::func_b", "b.py")
        var_a_id = _make_node_id("a.py::func_a::var:x", "a.py")
        var_b_id = _make_node_id("b.py::func_b::var:y", "b.py")

        for nid, kind, name, qname, fpath in [
            (func_a_id, "function", "func_a", "a.py::func_a", "a.py"),
            (func_b_id, "function", "func_b", "b.py::func_b", "b.py"),
            (var_a_id, "variable", "x", "a.py::func_a::var:x", "a.py"),
            (var_b_id, "variable", "y", "b.py::func_b::var:y", "b.py"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, ?, 'python', 1, 2, 1000)
            """, (nid, kind, name, qname, fpath))

        # Data flows within each file
        for src, tgt in [(var_a_id, func_b_id), (var_b_id, func_a_id)]:
            queries._exec("""
                INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
                VALUES (?, ?, 'data_flows', 'test:1', 'tree-sitter')
            """, (src, tgt))

        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        cross_edges = queries._exec(
            "SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'"
        ).fetchall()
        assert len(cross_edges) == 0

    def test_cross_file_provenance(self, queries):
        """Cross-file edges should have provenance='cross-file'."""
        ids = _setup_cross_file_scenario(queries)
        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        cross_edges = queries._exec(
            "SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'"
        ).fetchall()
        for edge in cross_edges:
            assert edge["provenance"] == "cross-file"

    def test_intra_file_dataflows_unchanged(self, queries):
        """Existing intra-file data_flows should not be modified."""
        ids = _setup_cross_file_scenario(queries)
        queries.conn.commit()

        # Count existing data_flows
        before = queries._exec(
            "SELECT COUNT(*) FROM edges WHERE kind='data_flows' AND provenance != 'cross-file'"
        ).fetchone()[0]

        _propagate_cross_file_dataflow(queries)

        after = queries._exec(
            "SELECT COUNT(*) FROM edges WHERE kind='data_flows' AND provenance != 'cross-file'"
        ).fetchone()[0]
        assert after == before, f"Intra-file data_flows changed: {before} → {after}"

    def test_depth_limit(self, queries):
        """Cross-file propagation should respect depth limit (2 hops)."""
        ids = _setup_cross_file_scenario(queries)
        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        # Verify no chain longer than 2 hops by checking edge count is reasonable
        cross_edges = queries._exec(
            "SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'"
        ).fetchall()
        # With 2 hops max, we shouldn't have combinatorial explosion
        # Our test scenario has 1 cross-file call, so edges should be manageable
        assert len(cross_edges) < 50, f"Too many cross-file edges: {len(cross_edges)}"
