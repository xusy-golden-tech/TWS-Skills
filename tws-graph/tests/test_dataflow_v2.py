"""TDD tests for P37 v5.4.0 — Data flow depth v2 (field-level + through-struct)."""

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
    """Simple flow chain: source → intermediate → sink via data_flows."""
    src_id = _make_id("mod.py::source", "mod.py")
    mid_id = _make_id("mod.py::intermediate", "mod.py")
    sink_id = _make_id("mod.py::sink", "mod.py")

    for nid, kind, name, qname in [
        (src_id, "function", "source", "mod.py::source"),
        (mid_id, "function", "intermediate", "mod.py::intermediate"),
        (sink_id, "function", "sink", "mod.py::sink"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',1,2,1000)",
            (nid, kind, name, qname, "mod.py"))

    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'data_flows','mod.py:1','tree-sitter')", (src_id, mid_id))
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'data_flows','mod.py:2','tree-sitter')", (mid_id, sink_id))
    queries.conn.commit()
    return queries


# ---------------------------------------------------------------------------
# P37a: Field-level data_flows
# ---------------------------------------------------------------------------

class TestFieldDataFlows:
    """Data flows through struct/object fields."""

    def test_through_struct_transitive_closure(self, populated_queries):
        """A → B → C: transitive closure finds A → C."""
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        new_edges = compute_transitive_dataflows(populated_queries)

        # Should find transitive edges: A → C via B
        src_id = _make_id("mod.py::source", "mod.py")
        sink_id = _make_id("mod.py::sink", "mod.py")

        found_transitive = any(
            e["source"] == src_id and e["target"] == sink_id
            for e in new_edges
        )
        assert found_transitive, "Transitive data_flow should be found"

    def test_transitive_edges_have_field_path(self, populated_queries):
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        new_edges = compute_transitive_dataflows(populated_queries)

        src_id = _make_id("mod.py::source", "mod.py")
        sink_id = _make_id("mod.py::sink", "mod.py")

        for e in new_edges:
            if e["source"] == src_id and e["target"] == sink_id:
                assert "path" in e or "transitive" in str(e), \
                    "Transitive edge should have path info"
                break

    def test_transitive_depth_limited(self, populated_queries):
        """Transitive closure respects max_depth."""
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        # max_depth=1: only direct edges, no transitive
        new_edges = compute_transitive_dataflows(populated_queries, max_depth=1)

        src_id = _make_id("mod.py::source", "mod.py")
        sink_id = _make_id("mod.py::sink", "mod.py")

        found = any(
            e["source"] == src_id and e["target"] == sink_id
            for e in new_edges
        )
        assert not found, "With max_depth=1, no transitive edge should exist"

    def test_empty_graph(self, queries):
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        new_edges = compute_transitive_dataflows(queries)
        assert new_edges == []


# ---------------------------------------------------------------------------
# P37b: Through-struct propagation
# ---------------------------------------------------------------------------

class TestThroughStructPropagation:
    """A → struct.field → B produces A → B."""

    def test_find_dataflow_chains(self, populated_queries):
        from tws_graph.analysis.dataflow_v2 import find_dataflow_chains

        chains = find_dataflow_chains(populated_queries)
        assert len(chains) >= 1, f"Should find at least 1 chain, got {len(chains)}"

    def test_chain_has_length(self, populated_queries):
        from tws_graph.analysis.dataflow_v2 import find_dataflow_chains

        chains = find_dataflow_chains(populated_queries)

        # 3 nodes: source → intermediate → sink = 1 chain of length 3
        long_enough = [c for c in chains if c["length"] >= 3]
        assert len(long_enough) >= 1, f"Should find chain of length >= 3"

    def test_chain_nodes_in_order(self, populated_queries):
        from tws_graph.analysis.dataflow_v2 import find_dataflow_chains

        chains = find_dataflow_chains(populated_queries)

        src_id = _make_id("mod.py::source", "mod.py")
        sink_id = _make_id("mod.py::sink", "mod.py")

        for chain in chains:
            nodes = chain.get("nodes", [])
            if len(nodes) >= 3:
                # First should be source, last should be sink
                assert nodes[0] == src_id
                assert nodes[-1] == sink_id


# ---------------------------------------------------------------------------
# P37c: Enhanced taint integration
# ---------------------------------------------------------------------------

class TestEnhancedTaintIntegration:
    """Field-level data flows improve taint path precision."""

    def test_transitive_improves_taint_paths(self, populated_queries):
        """With transitive data_flows, taint finds longer paths."""
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        # Count original data_flows edges
        orig_count = len(populated_queries._exec(
            "SELECT * FROM edges WHERE kind = 'data_flows'"
        ).fetchall())

        new_edges = compute_transitive_dataflows(populated_queries, max_depth=3)

        # Should find at least one transitive edge
        assert len(new_edges) > 0, "Transitive closure should produce edges"

    def test_no_duplicate_direct_edges(self, populated_queries):
        """Transitive closure should not duplicate existing direct edges."""
        from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows

        new_edges = compute_transitive_dataflows(populated_queries, max_depth=2)

        mid_id = _make_id("mod.py::intermediate", "mod.py")
        src_id = _make_id("mod.py::source", "mod.py")

        # No duplicate of source→intermediate
        dupes = [e for e in new_edges
                 if e["source"] == src_id and e["target"] == mid_id]
        assert len(dupes) == 0, "Should not duplicate existing direct edges"

    def test_empty_graph_no_error(self, queries):
        from tws_graph.analysis.dataflow_v2 import (
            compute_transitive_dataflows, find_dataflow_chains
        )

        new = compute_transitive_dataflows(queries)
        chains = find_dataflow_chains(queries)
        assert new == []
        assert chains == []
