"""Tests for analysis/git_diff.py — GitDiffAnalyzer.

TDD step 1: Tests written before implementation.
Covers:
    1. Empty store → no results
    2. No changes between baseline and current → empty result
    3. Added node with no incoming edges → low risk, impact_radius=0
    4. Added node called by others → correct impact radius and risk level
    5. Modified node (signature change) → detected as "modified"
    6. Removed node → detected as "removed"
    7. Risk level classification for all 4 levels
    8. Multiple changes in one diff
    9. Impact radius with multi-hop call chains
"""

import pytest

from tws_graph.store.memory_store import MemoryStore
from tws_graph.analysis.git_diff import (
    GitDiffAnalyzer,
    DiffImpact,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Helpers (mirror patterns from test_dead_code.py)
# ---------------------------------------------------------------------------

def _make_node(node_id, file_path="src/test.py", kind="function", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    default_qname = f"{file_path}::{kind}.{node_id}"
    node = {
        "id": node_id,
        "kind": kind,
        "name": node_id,
        "qualified_name": default_qname,
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 10,
        "signature": "def foo():",
        "docstring": None,
        "visibility": "public",
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


def _setup_store_with_graph(nodes: list[dict], edges: list[dict]) -> MemoryStore:
    """Create a MemoryStore and populate with nodes + edges."""
    store = MemoryStore()
    for node in nodes:
        store.insert_node(node)
    for edge in edges:
        store.insert_edge(edge)
    return store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_analyzer():
    """Return a fresh GitDiffAnalyzer."""
    return GitDiffAnalyzer()


# ---------------------------------------------------------------------------
# Test: empty store
# ---------------------------------------------------------------------------

class TestEmptyStore:
    def test_empty_both_stores(self, empty_analyzer):
        """Both baseline and current are empty → no results."""
        baseline = MemoryStore()
        current = MemoryStore()
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert results == []

    def test_empty_current(self, empty_analyzer):
        """Current is empty, baseline has nodes → those nodes are 'removed'."""
        baseline = _setup_store_with_graph(
            [_make_node("a", qualified_name="src/test.py::function.a")],
            [],
        )
        current = MemoryStore()
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 1
        assert results[0].change_type == "removed"
        assert results[0].impact_radius == 0
        assert results[0].risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# Test: no changes
# ---------------------------------------------------------------------------

class TestNoChanges:
    def test_identical_stores(self, empty_analyzer):
        """Baseline and current are identical → no changes."""
        nodes = [
            _make_node("a", qualified_name="src/test.py::function.a"),
            _make_node("b", qualified_name="src/test.py::function.b"),
        ]
        edges = [_make_edge("a", "b")]
        baseline = _setup_store_with_graph(nodes, edges)
        current = _setup_store_with_graph(nodes, edges)
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert results == []


# ---------------------------------------------------------------------------
# Test: added node
# ---------------------------------------------------------------------------

class TestAddedNode:
    def test_added_node_no_impact(self, empty_analyzer):
        """Added node with no incoming edges → low risk, radius 0."""
        baseline = MemoryStore()
        nodes = [
            _make_node("a", qualified_name="src/test.py::function.a",
                       signature="def a(): pass"),
        ]
        current = _setup_store_with_graph(nodes, [])
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 1
        imp = results[0]
        assert imp.node_id == "a"
        assert imp.change_type == "added"
        assert imp.impact_radius == 0
        assert imp.risk_level == RiskLevel.LOW
        assert imp.affected_files == ["src/test.py"]
        assert imp.affected_nodes == []

    def test_added_node_with_callers(self, empty_analyzer):
        """Added node called by other nodes → impact radius reflects callers."""
        baseline = MemoryStore()
        nodes = [
            _make_node("a", qualified_name="src/test.py::function.a"),
            _make_node("b", qualified_name="src/test.py::function.b"),
            _make_node("c", qualified_name="src/test.py::function.c"),
        ]
        edges = [
            _make_edge("b", "a", kind="calls"),
            _make_edge("c", "a", kind="calls"),
        ]
        current = _setup_store_with_graph(nodes, edges)
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        # All 3 nodes are new, but only the one with callers has impact
        assert len(results) == 3

        # Find the node with callers
        node_a = [r for r in results if r.node_id == "a"][0]
        assert node_a.change_type == "added"
        assert node_a.impact_radius == 2  # b and c call a
        assert node_a.risk_level == RiskLevel.MEDIUM  # 2 falls in 1-5 range
        assert set(node_a.affected_nodes) == {"b", "c"}


# ---------------------------------------------------------------------------
# Test: modified node
# ---------------------------------------------------------------------------

class TestModifiedNode:
    def test_signature_change(self, empty_analyzer):
        """Node exists in both but signature differs → 'modified'."""
        nodes_before = [
            _make_node("a", qualified_name="src/test.py::function.a",
                       signature="def a(): pass"),
            _make_node("b", qualified_name="src/test.py::function.b",
                       signature="def b(): pass"),
        ]
        edges = [_make_edge("b", "a", kind="calls")]
        baseline = _setup_store_with_graph(nodes_before, edges)

        # Same nodes, but 'a' has a changed signature
        nodes_after = [
            _make_node("a", qualified_name="src/test.py::function.a",
                       signature="def a(x: int = 42): pass"),
            _make_node("b", qualified_name="src/test.py::function.b",
                       signature="def b(): pass"),
        ]
        current = _setup_store_with_graph(nodes_after, edges)

        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 1
        imp = results[0]
        assert imp.node_id == "a"
        assert imp.change_type == "modified"
        assert imp.impact_radius == 1  # b calls a
        assert imp.risk_level == RiskLevel.MEDIUM


# ---------------------------------------------------------------------------
# Test: removed node
# ---------------------------------------------------------------------------

class TestRemovedNode:
    def test_removed_node(self, empty_analyzer):
        """Node in baseline but not in current → 'removed'."""
        nodes_before = [
            _make_node("a", qualified_name="src/test.py::function.a"),
            _make_node("b", qualified_name="src/test.py::function.b"),
        ]
        edges = [_make_edge("b", "a", kind="calls")]
        baseline = _setup_store_with_graph(nodes_before, edges)

        # 'a' removed
        nodes_after = [
            _make_node("b", qualified_name="src/test.py::function.b"),
        ]
        current = _setup_store_with_graph(nodes_after, [])

        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 1
        imp = results[0]
        assert imp.node_id == "a"
        assert imp.change_type == "removed"
        # For removed nodes, impact_radius is 0 (not in current graph)
        assert imp.impact_radius == 0
        assert imp.risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# Test: risk level classification
# ---------------------------------------------------------------------------

class TestRiskClassification:
    """Verify all 4 risk levels based on impact radius thresholds."""

    def test_low_risk(self, empty_analyzer):
        """Impact radius 0 → low risk."""
        baseline = MemoryStore()
        nodes = [
            _make_node("isolated", qualified_name="src/test.py::function.isolated"),
        ]
        current = _setup_store_with_graph(nodes, [])
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 1
        assert results[0].impact_radius == 0
        assert results[0].risk_level == RiskLevel.LOW

    def test_medium_risk(self, empty_analyzer):
        """Impact radius 1-5 → medium risk."""
        baseline = MemoryStore()
        nodes = [_make_node(f"n{i}", qualified_name=f"src/test.py::function.n{i}")
                 for i in range(6)]  # n0..n5
        edges = [_make_edge(f"n{i}", "n0", kind="calls") for i in range(1, 6)]
        current = _setup_store_with_graph(nodes, edges)
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        # Find n0 (the one being called)
        node_0 = [r for r in results if r.node_id == "n0"][0]
        assert node_0.impact_radius == 5
        assert node_0.risk_level == RiskLevel.MEDIUM

    def test_high_risk(self, empty_analyzer):
        """Impact radius 6-15 → high risk."""
        baseline = MemoryStore()
        n_calls = 10
        nodes = [_make_node(f"n{i}", qualified_name=f"src/test.py::function.n{i}")
                 for i in range(n_calls + 1)]
        edges = [_make_edge(f"n{i}", "n0", kind="calls") for i in range(1, n_calls + 1)]
        current = _setup_store_with_graph(nodes, edges)
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        node_0 = [r for r in results if r.node_id == "n0"][0]
        assert node_0.impact_radius == 10
        assert node_0.risk_level == RiskLevel.HIGH

    def test_critical_risk(self, empty_analyzer):
        """Impact radius > 15 → critical risk."""
        baseline = MemoryStore()
        n_calls = 20
        nodes = [_make_node(f"n{i}", qualified_name=f"src/test.py::function.n{i}")
                 for i in range(n_calls + 1)]
        edges = [_make_edge(f"n{i}", "n0", kind="calls") for i in range(1, n_calls + 1)]
        current = _setup_store_with_graph(nodes, edges)
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        node_0 = [r for r in results if r.node_id == "n0"][0]
        assert node_0.impact_radius == 20
        assert node_0.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Test: multiple changes
# ---------------------------------------------------------------------------

class TestMultipleChanges:
    def test_added_modified_removed_together(self, empty_analyzer):
        """Simultaneous add, modify, and remove in one diff."""
        baseline = _setup_store_with_graph(
            [
                _make_node("old_a", qualified_name="src/test.py::function.old_a",
                           signature="def old_a(): pass"),
                _make_node("b", qualified_name="src/test.py::function.b",
                           signature="def b(): pass"),
            ],
            [_make_edge("b", "old_a", kind="calls")],
        )

        current = _setup_store_with_graph(
            [
                # old_a removed → new_a added in its place
                _make_node("new_a", qualified_name="src/test.py::function.new_a",
                           signature="def new_a(): pass"),
                # b modified (signature change)
                _make_node("b", qualified_name="src/test.py::function.b",
                           signature="def b(x: int): pass"),
                # c added
                _make_node("c", qualified_name="src/test.py::function.c"),
            ],
            [
                _make_edge("b", "new_a", kind="calls"),
                _make_edge("c", "new_a", kind="calls"),
            ],
        )

        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        # old_a removed, new_a added, b modified, c added = 4 changes
        assert len(results) == 4

        types = {r.change_type for r in results}
        assert types == {"added", "modified", "removed"}


# ---------------------------------------------------------------------------
# Test: multi-hop impact radius
# ---------------------------------------------------------------------------

class TestMultiHopImpact:
    def test_multi_hop_call_chain(self, empty_analyzer):
        """Impact radius reflects multi-hop call chains (BFS on incoming edges).

        Graph:
            n4 → n3 → n2 → n1 → n0
        Change to `n0` → impact includes n1, n2, n3 (radius=3) at max_depth=3.
        n4 is at depth 4, which exceeds max_depth=3, so not included.
        """
        # Baseline is empty — all nodes in current are "added"
        baseline = MemoryStore()

        current_nodes = [_make_node(f"n{i}", qualified_name=f"src/test.py::function.n{i}")
                         for i in range(5)]
        # Chain: n4 → n3 → n2 → n1 → n0 (edge direction: source→target means source calls target)
        current_edges = [_make_edge(f"n{i+1}", f"n{i}", kind="calls") for i in range(4)]
        current = _setup_store_with_graph(current_nodes, current_edges)

        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        # All 5 nodes are new (none in baseline). Find n0's impact.
        node_0 = [r for r in results if r.node_id == "n0"][0]
        # With default max_depth=3, impact_radius should include n1, n2, n3
        # (3 hops: n1→n0, n2→n1→n0, n3→n2→n1→n0)
        # n4 is at depth 4, which exceeds max_depth=3, so not included
        assert node_0.impact_radius == 3
        assert "n1" in node_0.affected_nodes
        assert "n2" in node_0.affected_nodes
        assert "n3" in node_0.affected_nodes
        assert "n4" not in node_0.affected_nodes


# ---------------------------------------------------------------------------
# Test: affected files collection
# ---------------------------------------------------------------------------

class TestAffectedFiles:
    def test_affected_files_from_nodes(self, empty_analyzer):
        """affected_files collects file_paths of changed and impacted nodes."""
        baseline = MemoryStore()
        current = _setup_store_with_graph(
            [
                _make_node("a", qualified_name="src/a.py::function.a",
                           file_path="src/a.py"),
                _make_node("b", qualified_name="src/b.py::function.b",
                           file_path="src/b.py"),
            ],
            [_make_edge("b", "a", kind="calls")],
        )
        results = empty_analyzer.analyze(
            current,
            baseline_store=baseline,
        )
        assert len(results) == 2

        node_a = [r for r in results if r.node_id == "a"][0]
        # affected_files should include a's own file + files of affected_nodes
        assert "src/a.py" in node_a.affected_files
        # File of node b (which calls a) should be in affected files
        # since b is in affected_nodes for a
        assert "src/b.py" in node_a.affected_files
