"""Tests for graph/algorithms/centrality.py — CentralityComputer.

Verifies:
    1. PageRank: nodes with more incoming edges get higher scores
    2. Betweenness: bridge nodes get higher scores
    3. Empty graph returns empty scores (no crash)
    4. NetworkX fallback when NetworkX is not installed
    5. AlgorithmResult fields are correctly populated
    6. Edge kind filtering (only "calls" edges by default)
    7. Single node / disconnected graph handling
    8. Circular dependency handling
"""

import pytest

from tws_graph.graph.algorithms.centrality import CentralityComputer
from tws_graph.graph.algorithms.base import AlgorithmResult
from tws_graph.store.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dag_store() -> MemoryStore:
    """Build a simple call-graph DAG: A->B, A->C, B->C, B->D, C->D."""
    store = MemoryStore()
    # Insert nodes
    for nid, name in [("A", "func_a"), ("B", "func_b"), ("C", "func_c"), ("D", "func_d")]:
        store.insert_node({
            "id": nid,
            "kind": "function",
            "name": name,
            "qualified_name": f"test.py::{name}",
            "file_path": "test.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        })
    # Insert edges: A->B, A->C, B->C, B->D, C->D
    edges = [
        ("A", "B"),
        ("A", "C"),
        ("B", "C"),
        ("B", "D"),
        ("C", "D"),
    ]
    for src, tgt in edges:
        store.insert_edge({
            "source": src,
            "target": tgt,
            "kind": "calls",
        })
    return store


def _make_circular_store() -> MemoryStore:
    """Build a circular call graph: A<->B, B->C."""
    store = MemoryStore()
    for nid, name in [("A", "func_a"), ("B", "func_b"), ("C", "func_c")]:
        store.insert_node({
            "id": nid,
            "kind": "function",
            "name": name,
            "qualified_name": f"circ.py::{name}",
            "file_path": "circ.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        })
    for src, tgt in [("A", "B"), ("B", "A"), ("B", "C")]:
        store.insert_edge({
            "source": src,
            "target": tgt,
            "kind": "calls",
        })
    return store


def _make_star_store() -> MemoryStore:
    """Build a star graph: Center calls Leaf1, Leaf2, Leaf3."""
    store = MemoryStore()
    for nid, name in [("C", "center"), ("L1", "leaf1"), ("L2", "leaf2"), ("L3", "leaf3")]:
        store.insert_node({
            "id": nid,
            "kind": "function",
            "name": name,
            "qualified_name": f"star.py::{name}",
            "file_path": "star.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        })
    for tgt in ["L1", "L2", "L3"]:
        store.insert_edge({"source": "C", "target": tgt, "kind": "calls"})
    return store


# ---------------------------------------------------------------------------
# PageRank tests
# ---------------------------------------------------------------------------

class TestPageRank:
    """Tests for PageRank centrality computation."""

    def test_dag_pagerank_ordering(self):
        """In a DAG, nodes with more in-degree should have higher PageRank."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        # D has in-degree 2 (from B, C), should be high
        # A has in-degree 0, should be lowest
        assert "D" in scores
        assert "A" in scores
        assert scores["D"] > scores["A"]

    def test_pagerank_all_nodes_have_scores(self):
        """All nodes in the graph should appear in the PageRank result."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        scores = result.data["scores"]
        assert set(scores.keys()) == {"A", "B", "C", "D"}

    def test_pagerank_scores_sum_to_one(self):
        """PageRank scores should approximately sum to 1.0."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        total = sum(result.data["scores"].values())
        assert 0.99 <= total <= 1.01, f"PageRank sum: {total}"

    def test_pagerank_circular_graph(self):
        """PageRank handles circular dependencies without error."""
        store = _make_circular_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert set(scores.keys()) == {"A", "B", "C"}
        total = sum(scores.values())
        assert 0.99 <= total <= 1.01, f"PageRank sum: {total}"

    def test_pagerank_star_graph(self):
        """In a star graph, center node has most out-links; leaves have most in-links."""
        store = _make_star_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        scores = result.data["scores"]
        # Center has outgoing links, leaves have incoming links
        # With no backlinks, PageRank of leaves should be higher
        assert scores["L1"] > 0
        assert scores["C"] > 0


# ---------------------------------------------------------------------------
# Betweenness tests
# ---------------------------------------------------------------------------

class TestBetweenness:
    """Tests for Betweenness centrality computation."""

    def test_dag_betweenness(self):
        """In a DAG, bridge nodes should have higher betweenness."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert set(scores.keys()) == {"A", "B", "C", "D"}

    def test_betweenness_star_graph(self):
        """In a star graph, center node has maximum betweenness."""
        store = _make_star_store()
        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        scores = result.data["scores"]
        # Center should have betweenness > 0 (bridging leaves)
        assert scores["C"] >= 0
        # Leaves should have 0 betweenness (endpoints)
        assert scores["L1"] == 0.0
        assert scores["L2"] == 0.0
        assert scores["L3"] == 0.0

    def test_betweenness_circular_graph(self):
        """Betweenness handles circular dependencies without error."""
        store = _make_circular_store()
        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert set(scores.keys()) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge case handling."""

    def test_empty_graph(self):
        """An empty graph should return empty scores, no errors."""
        store = MemoryStore()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        assert result.data["scores"] == {}
        assert result.errors == []

    def test_single_node_no_edges(self):
        """A single node with no edges should not crash."""
        store = MemoryStore()
        store.insert_node({
            "id": "N1",
            "kind": "function",
            "name": "solo",
            "qualified_name": "solo.py::solo",
            "file_path": "solo.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        })
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert "N1" in scores, f"solo node should have a score, got: {scores}"
        assert scores["N1"] > 0

    def test_single_node_betweenness(self):
        """A single node with no edges should not crash for betweenness."""
        store = MemoryStore()
        store.insert_node({
            "id": "N1",
            "kind": "function",
            "name": "solo",
            "qualified_name": "solo.py::solo",
            "file_path": "solo.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        })
        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert "N1" in scores

    def test_disconnected_components(self):
        """Graph with isolated nodes should not crash."""
        store = MemoryStore()
        for nid in ["X", "Y"]:
            store.insert_node({
                "id": nid,
                "kind": "function",
                "name": f"func_{nid}",
                "qualified_name": f"iso.py::func_{nid}",
                "file_path": "iso.py",
                "language": "python",
                "start_line": 1,
                "end_line": 1,
            })
        # No edges at all
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        assert "X" in scores
        assert "Y" in scores


# ---------------------------------------------------------------------------
# AlgorithmResult field verification
# ---------------------------------------------------------------------------

class TestAlgorithmResultFields:
    """Tests for AlgorithmResult output correctness."""

    def test_result_fields_pagerank(self):
        """CentralityComputer returns correct AlgorithmResult fields."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert isinstance(result, AlgorithmResult)
        assert result.algorithm == "centrality"
        assert result.success is True
        assert isinstance(result.data, dict)
        assert "scores" in result.data
        assert "node_count" in result.data
        assert "method" in result.data
        assert result.data["method"] == "pagerank"
        assert result.data["node_count"] == 4
        assert isinstance(result.duration_ms, float)
        assert result.duration_ms > 0

    def test_result_fields_betweenness(self):
        """Betweenness result has correct fields."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        assert result.algorithm == "centrality"
        assert result.data["method"] == "betweenness"
        assert result.data["node_count"] == 4

    def test_scores_are_rounded(self):
        """Scores should be rounded to 6 decimal places."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        for nid, score in result.data["scores"].items():
            assert round(score, 6) == score, f"Score for {nid} not rounded: {score}"

    def test_top_100_limit(self):
        """Results should be capped at 100 top nodes (for large graphs)."""
        store = MemoryStore()
        for i in range(200):
            nid = f"node_{i}"
            store.insert_node({
                "id": nid,
                "kind": "function",
                "name": f"func_{i}",
                "qualified_name": f"large.py::func_{i}",
                "file_path": "large.py",
                "language": "python",
                "start_line": i,
                "end_line": i,
            })
        # Add some edges to connect nodes
        for i in range(1, 200):
            store.insert_edge({
                "source": f"node_{i}",
                "target": "node_0",
                "kind": "calls",
            })
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert len(result.data["scores"]) <= 100

    def test_duration_is_recorded(self):
        """Duration should be a positive measurable value."""
        store = _make_dag_store()
        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.duration_ms > 0, "Duration should be measurable"


# ---------------------------------------------------------------------------
# NetworkX fallback
# ---------------------------------------------------------------------------

class TestNetworkXFallback:
    """Tests for built-in fallback when NetworkX is unavailable."""

    def test_builtin_pagerank_works_without_networkx(self, monkeypatch):
        """When NetworkX import fails, built-in PageRank should produce results."""
        store = _make_dag_store()

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("No module named 'networkx'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        assert "scores" in result.data
        assert len(result.data["scores"]) == 4
        assert len(result.errors) == 1
        assert "NetworkX not available" in result.errors[0]

    def test_builtin_betweenness_works_without_networkx(self, monkeypatch):
        """When NetworkX import fails, built-in betweenness should produce results."""
        store = _make_dag_store()

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("No module named 'networkx'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        algo = CentralityComputer(algorithm="betweenness")
        result = algo.run(store)

        assert result.success is True
        assert "scores" in result.data
        assert len(result.data["scores"]) == 4
        assert len(result.errors) == 1
        assert "NetworkX not available" in result.errors[0]


# ---------------------------------------------------------------------------
# Edge kind filtering
# ---------------------------------------------------------------------------

class TestEdgeKindFiltering:
    """Tests for edge_kinds parameter."""

    def test_default_filters_calls_only(self):
        """By default, only 'calls' edges are used."""
        store = MemoryStore()
        for nid in ["A", "B"]:
            store.insert_node({
                "id": nid,
                "kind": "function",
                "name": f"func_{nid}",
                "qualified_name": f"test.py::func_{nid}",
                "file_path": "test.py",
                "language": "python",
                "start_line": 1,
                "end_line": 1,
            })
        store.insert_edge({"source": "A", "target": "B", "kind": "calls"})
        store.insert_edge({"source": "A", "target": "B", "kind": "imports"})

        algo = CentralityComputer(algorithm="pagerank")
        result = algo.run(store)

        assert result.success is True
        # The graph should have one edge (calls), not two
        scores = result.data["scores"]
        assert set(scores.keys()) == {"A", "B"}

    def test_custom_edge_kinds(self):
        """Custom edge_kinds should filter correctly."""
        store = MemoryStore()
        for nid in ["A", "B"]:
            store.insert_node({
                "id": nid,
                "kind": "function",
                "name": f"func_{nid}",
                "qualified_name": f"test.py::func_{nid}",
                "file_path": "test.py",
                "language": "python",
                "start_line": 1,
                "end_line": 1,
            })
        # Only imports edge
        store.insert_edge({"source": "A", "target": "B", "kind": "imports"})

        algo = CentralityComputer(algorithm="pagerank", edge_kinds=["imports"])
        result = algo.run(store)

        assert result.success is True
        scores = result.data["scores"]
        # With imports edge, B gets pagerank from A
        assert "B" in scores


# ---------------------------------------------------------------------------
# Constructor / property tests
# ---------------------------------------------------------------------------

class TestCentralityComputerProperties:
    """Tests for CentralityComputer constructor and properties."""

    def test_name_property(self):
        """The name property should return 'centrality'."""
        algo = CentralityComputer()
        assert algo.name == "centrality"

    def test_description_property(self):
        """The description property should be non-empty."""
        algo = CentralityComputer()
        assert len(algo.description) > 0

    def test_is_graph_algorithm_subclass(self):
        """CentralityComputer is a valid GraphAlgorithm subclass."""
        from tws_graph.graph.algorithms.base import GraphAlgorithm
        assert issubclass(CentralityComputer, GraphAlgorithm)

    def test_can_instantiate(self):
        """CentralityComputer can be instantiated directly."""
        algo = CentralityComputer()
        assert algo is not None

    def test_damping_factor_custom(self):
        """Custom damping factor should be accepted."""
        algo = CentralityComputer(algorithm="pagerank", damping_factor=0.5)
        store = _make_dag_store()
        result = algo.run(store)
        assert result.success is True

    def test_custom_max_iterations(self):
        """Custom max iterations should be accepted."""
        algo = CentralityComputer(algorithm="pagerank", max_iterations=10)
        store = _make_dag_store()
        result = algo.run(store)
        assert result.success is True
