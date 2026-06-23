"""Tests for analysis/dead_code.py — DeadCodeDetector.

Covers:
    1. Normal nodes with incoming edges → not dead code
    2. Isolated nodes with no incoming edges → dead code candidate
    3. Entry points (no incoming edges but marked as entry) → not dead code
    4. Method nodes (not just functions) → checked
    5. Mutual recursion pair → known limitation (not detected)
    6. Edge cases: empty store, no function/method nodes, reference edges
    7. Candidate field correctness
"""

import pytest

from tws_graph.store.memory_store import MemoryStore
from tws_graph.analysis.dead_code import DeadCodeDetector, DeadCodeCandidate


# ---------------------------------------------------------------------------
# Helpers (mirror patterns from test_memory_store.py)
# ---------------------------------------------------------------------------

def _make_node(node_id, file_path="src/test.py", kind="function", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    node = {
        "id": node_id,
        "kind": kind,
        "name": f"name_of_{node_id}",
        "qualified_name": f"{file_path}::{kind}.name_of_{node_id}",
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_store():
    """Return a fresh empty MemoryStore."""
    return MemoryStore()


@pytest.fixture
def graph_store():
    """Return a MemoryStore with a non-trivial call graph.

    Graph topology:
        main (function)       ← entry point, calls helper, process
        helper (function)     ← called by main, calls format
        format (function)     ← called by helper, no outgoing calls
        process (function)    ← called by main
        dead_func (function)  ← isolated: not called by anyone, not an entry point
        dead_method (method)  ← isolated method on some class
        log (function)        ← called by main (via references edge, not calls)
        orphan (function)     ← no incoming edges at all

    Additionally:
        mutual_a (function) ↔ mutual_b (function)  ← mutual recursion, no external callers
        entry_dead (function) ← no incoming edges but marked as entry point
    """
    s = MemoryStore()

    # Nodes
    s.insert_node(_make_node("main", kind="function"))
    s.insert_node(_make_node("helper", kind="function"))
    s.insert_node(_make_node("format", kind="function"))
    s.insert_node(_make_node("process", kind="function"))
    s.insert_node(_make_node("dead_func", kind="function"))
    s.insert_node(_make_node("dead_method", kind="method"))
    s.insert_node(_make_node("log", kind="function"))
    s.insert_node(_make_node("orphan", kind="function"))
    s.insert_node(_make_node("mutual_a", kind="function"))
    s.insert_node(_make_node("mutual_b", kind="function"))
    s.insert_node(_make_node("entry_dead", kind="function"))

    # Edges: main → helper, main → process
    s.insert_edge(_make_edge("main", "helper", kind="calls"))
    s.insert_edge(_make_edge("main", "process", kind="calls"))

    # Edges: helper → format
    s.insert_edge(_make_edge("helper", "format", kind="calls"))

    # Edge: main → log (references, not calls)
    s.insert_edge(_make_edge("main", "log", kind="references"))

    # Mutual recursion: mutual_a ↔ mutual_b
    s.insert_edge(_make_edge("mutual_a", "mutual_b", kind="calls"))
    s.insert_edge(_make_edge("mutual_b", "mutual_a", kind="calls"))

    return s


# =============================================================================
# Tests
# =============================================================================

class TestDeadCodeDetector:
    """Core detection logic."""

    def test_normal_node_not_dead(self, graph_store):
        """A function with incoming edges should NOT be flagged as dead code."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        dead_ids = {c.node_id for c in results}
        assert "helper" not in dead_ids
        assert "format" not in dead_ids
        assert "process" not in dead_ids
        assert "log" not in dead_ids  # has incoming reference edge

    def test_isolated_function_is_dead(self, graph_store):
        """A function with zero in-degree and not an entry point IS dead code."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        dead_ids = {c.node_id for c in results}
        assert "dead_func" in dead_ids
        assert "orphan" in dead_ids

    def test_isolated_method_is_dead(self, graph_store):
        """A METHOD with zero in-degree IS dead code (both functions and methods checked)."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        dead_ids = {c.node_id for c in results}
        assert "dead_method" in dead_ids

    def test_entry_point_excluded(self, graph_store):
        """Node with no incoming edges BUT in entry_points should NOT be flagged."""
        detector = DeadCodeDetector()
        results = detector.detect(
            graph_store,
            entry_points={"entry_dead"}
        )
        dead_ids = {c.node_id for c in results}
        assert "entry_dead" not in dead_ids

    def test_entry_point_excluded_still_flags_others(self, graph_store):
        """Providing entry_points only excludes those; other dead nodes still flagged."""
        detector = DeadCodeDetector()
        results = detector.detect(
            graph_store,
            entry_points={"entry_dead"}
        )
        dead_ids = {c.node_id for c in results}
        assert "dead_func" in dead_ids
        assert "orphan" in dead_ids

    def test_mutual_recursion_not_detected(self, graph_store):
        """Known limitation: mutual recursion pair with no external callers
        is NOT flagged by the simple degree-based detector.
        Each node has in_degree >= 1 (from the other).
        """
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        dead_ids = {c.node_id for c in results}
        assert "mutual_a" not in dead_ids
        assert "mutual_b" not in dead_ids

    def test_candidate_fields(self, graph_store):
        """DeadCodeCandidate should carry all expected fields."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        for c in results:
            assert c.node_id
            assert c.qualified_name
            assert c.kind
            assert c.file_path
            assert c.language
            assert isinstance(c.in_degree, int)
            assert isinstance(c.out_degree, int)
            assert isinstance(c.is_entry_point, bool)
            assert c.in_degree == 0  # dead code by definition

    def test_in_degree_is_correct(self, graph_store):
        """The in_degree field should reflect actual incoming CALLS + REFERENCES count."""
        detector = DeadCodeDetector()
        # Detect without custom entry_points so we get all nodes
        results = detector.detect(graph_store)
        results_by_id = {c.node_id: c for c in results}

        # dead_func has no incoming edges → in_degree == 0
        assert results_by_id["dead_func"].in_degree == 0

        # helper has 1 incoming edge (from main: calls)
        # But helper won't be in results because it HAS incoming edges
        # Verify in_degree for a dead node: orphan has 0
        assert results_by_id["orphan"].in_degree == 0

    def test_out_degree_is_correct(self, graph_store):
        """The out_degree field should reflect actual outgoing CALLS + REFERENCES count."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store)
        results_by_id = {c.node_id: c for c in results}

        # main has outgoing edges to helper, process, log → not dead, not in results
        # orphan has no outgoing edges → out_degree == 0
        assert results_by_id["orphan"].out_degree == 0

    def test_entry_points_none_default(self, graph_store):
        """When entry_points is None, all nodes with in_degree==0 should be dead."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store, entry_points=None)
        dead_ids = {c.node_id for c in results}
        # entry_dead has no incoming edges, no entry_points provided → dead
        assert "entry_dead" in dead_ids

    def test_entry_points_empty_set(self, graph_store):
        """When entry_points is an empty set, behaves same as None."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store, entry_points=set())
        dead_ids = {c.node_id for c in results}
        assert "entry_dead" in dead_ids


class TestEdgeCases:
    """Boundary conditions."""

    def test_empty_store_returns_empty(self, empty_store):
        """An empty store should yield no dead code candidates."""
        detector = DeadCodeDetector()
        results = detector.detect(empty_store)
        assert results == []

    def test_no_function_nodes_returns_empty(self):
        """Store with only class/interface nodes should yield no results."""
        s = MemoryStore()
        s.insert_node(_make_node("c1", kind="class"))
        s.insert_node(_make_node("c2", kind="interface"))
        detector = DeadCodeDetector()
        results = detector.detect(s)
        assert results == []

    def test_only_entry_points_no_dead_code(self):
        """If all zero-in-degree nodes are entry points, no dead code reported."""
        s = MemoryStore()
        s.insert_node(_make_node("a", kind="function"))
        s.insert_node(_make_node("b", kind="function"))
        detector = DeadCodeDetector()
        results = detector.detect(s, entry_points={"a", "b"})
        assert results == []

    def test_use_centrality_flag_accepted(self, graph_store):
        """use_centrality flag should be accepted without error (even if not implemented)."""
        detector = DeadCodeDetector()
        results = detector.detect(graph_store, use_centrality=True)
        # For now centrality is optional; just ensure no crash
        assert isinstance(results, list)

    def test_reference_edge_counts_as_incoming(self):
        """A node with only a 'references' incoming edge (not 'calls') should NOT be dead."""
        s = MemoryStore()
        s.insert_node(_make_node("caller", kind="function"))
        s.insert_node(_make_node("target", kind="function"))
        s.insert_edge(_make_edge("caller", "target", kind="references"))
        detector = DeadCodeDetector()
        results = detector.detect(s)
        dead_ids = {c.node_id for c in results}
        assert "target" not in dead_ids

    def test_calls_edge_counts_as_incoming(self):
        """A node with only a 'calls' incoming edge should NOT be dead."""
        s = MemoryStore()
        s.insert_node(_make_node("caller", kind="function"))
        s.insert_node(_make_node("target", kind="function"))
        s.insert_edge(_make_edge("caller", "target", kind="calls"))
        detector = DeadCodeDetector()
        results = detector.detect(s)
        dead_ids = {c.node_id for c in results}
        assert "target" not in dead_ids

    def test_imports_edge_does_not_count_as_incoming(self):
        """An 'imports' edge should NOT prevent a node from being dead code
        (only calls and references edges are relevant for dead code detection)."""
        s = MemoryStore()
        s.insert_node(_make_node("mod", kind="function"))
        s.insert_node(_make_node("unused_func", kind="function"))
        s.insert_edge(_make_edge("mod", "unused_func", kind="imports"))
        detector = DeadCodeDetector()
        results = detector.detect(s)
        dead_ids = {c.node_id for c in results}
        assert "unused_func" in dead_ids
