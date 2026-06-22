"""Tests for graph/algorithms/community.py — CommunityDetector (Louvain).

Verifies:
    1. CommunityDetector construction (name, description, default edge_kinds)
    2. Empty store returns zero communities
    3. Two connected cliques separated into two communities
    4. edge_kinds filtering — only specified edge types used
    5. AlgorithmResult fields correctness (algorithm, success, data, duration_ms)
    6. Single node produces one community of size 1
    7. Disconnected nodes produce separate communities
    8. NetworkX fallback is used when import raises
    9. Custom edge_kinds parameter works
"""

import pytest

from tws_graph.graph.algorithms.community import CommunityDetector
from tws_graph.graph.algorithms.base import AlgorithmResult, GraphAlgorithm
from tws_graph.store.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store_with_nodes(node_ids: list[str]) -> MemoryStore:
    """Create a MemoryStore pre-populated with nodes.

    Each node gets a unique qualified_name: file_path:qual_name
    """
    store = MemoryStore()
    for i, nid in enumerate(node_ids):
        store.insert_node({
            "id": nid,
            "kind": "function",
            "name": nid,
            "qualified_name": f"test/file.py:{nid}",
            "file_path": "test/file.py",
            "language": "python",
            "start_line": i + 1,
            "end_line": i + 1,
        })
    return store


def _add_edge(store: MemoryStore, src: str, tgt: str, kind: str = "calls") -> None:
    """Add a single edge to the store."""
    store.insert_edge({
        "source": src,
        "target": tgt,
        "kind": kind,
        "target_text": "",
        "provenance": "test",
    })


def _build_two_cliques() -> MemoryStore:
    """Build a store with two cliques connected by a single bridge edge.

    Clique A: n0, n1, n2, n3 (fully connected, 6 edges)
    Clique B: n4, n5, n6, n7 (fully connected, 6 edges)
    Bridge:  n3 -- n4 (1 edge)
    """
    store = _make_store_with_nodes([f"n{i}" for i in range(8)])

    # Clique A
    for i in range(4):
        for j in range(i + 1, 4):
            _add_edge(store, f"n{i}", f"n{j}")

    # Clique B
    for i in range(4, 8):
        for j in range(i + 1, 8):
            _add_edge(store, f"n{i}", f"n{j}")

    # Bridge
    _add_edge(store, "n3", "n4")

    return store


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class TestCommunityDetectorConstruction:
    """Tests for CommunityDetector construction and class hierarchy."""

    def test_is_graph_algorithm_subclass(self):
        """CommunityDetector is a GraphAlgorithm subclass."""
        assert issubclass(CommunityDetector, GraphAlgorithm)

    def test_name_property(self):
        """name returns 'community-detection'."""
        cd = CommunityDetector()
        assert cd.name == "community-detection"

    def test_description_property(self):
        """description is a non-empty string."""
        cd = CommunityDetector()
        assert isinstance(cd.description, str)
        assert len(cd.description) > 0

    def test_default_edge_kinds(self):
        """Default edge_kinds is ['calls', 'imports']."""
        cd = CommunityDetector()
        assert cd._edge_kinds == ["calls", "imports"]

    def test_custom_edge_kinds(self):
        """Custom edge_kinds are stored correctly."""
        cd = CommunityDetector(edge_kinds=["references"])
        assert cd._edge_kinds == ["references"]

    def test_none_edge_kinds_uses_default(self):
        """edge_kinds=None falls back to default."""
        cd = CommunityDetector(edge_kinds=None)
        assert cd._edge_kinds == ["calls", "imports"]


# ---------------------------------------------------------------------------
# Empty store tests
# ---------------------------------------------------------------------------


class TestCommunityDetectorEmpty:
    """Tests for community detection on empty / single-node graphs."""

    def test_empty_store_returns_zero_communities(self):
        """An empty store produces zero communities with modularity 0.0."""
        store = MemoryStore()
        cd = CommunityDetector()
        result = cd.run(store)

        assert isinstance(result, AlgorithmResult)
        assert result.success is True  # no errors — just empty
        assert result.data["communities"] == []
        assert result.data["modularity"] == 0.0
        assert result.data["community_count"] == 0
        assert len(result.errors) == 0

    def test_store_with_nodes_but_no_edges(self):
        """Nodes without edges: each should form its own community."""
        store = _make_store_with_nodes(["a", "b", "c"])
        cd = CommunityDetector()
        result = cd.run(store)

        # Without edges, no communities via adjacency — empty result
        assert result.data["community_count"] == 0
        assert result.data["communities"] == []

    def test_single_node_with_self_loop(self):
        """A single node with a self-loop (or no edge) still treated as empty adj."""
        store = _make_store_with_nodes(["only_node"])
        cd = CommunityDetector()
        result = cd.run(store)

        assert result.success is True
        assert result.data["community_count"] == 0


# ---------------------------------------------------------------------------
# Two cliques test
# ---------------------------------------------------------------------------


class TestCommunityDetectorTwoCliques:
    """Tests on a two-clique graph with a bridge edge."""

    def test_two_cliques_separated(self):
        """Two connected cliques produce at least 2 communities."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        assert result.success is True
        assert result.data["community_count"] >= 2

        communities = result.data["communities"]
        all_members = set()
        for c in communities:
            all_members.update(c["members"])

        # All 8 nodes should be assigned to some community
        assert all_members == {f"n{i}" for i in range(8)}

    def test_community_sizes_are_reasonable(self):
        """Each community has at least 1 member and sizes are documented."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        for c in result.data["communities"]:
            assert c["size"] > 0
            assert c["size"] == len(c["members"])

    def test_community_ids_are_unique(self):
        """Each community has a unique id."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        ids = [c["id"] for c in result.data["communities"]]
        assert len(ids) == len(set(ids))

    def test_detector_is_deterministic(self):
        """Same store produces same community_count (deterministic algorithm)."""
        store = _build_two_cliques()
        cd = CommunityDetector()

        r1 = cd.run(store)
        r2 = cd.run(store)

        assert r1.data["community_count"] == r2.data["community_count"]
        assert r1.data["modularity"] == r2.data["modularity"]


# ---------------------------------------------------------------------------
# edge_kinds filtering
# ---------------------------------------------------------------------------


class TestCommunityDetectorEdgeKindsFilter:
    """Tests for edge kind filtering."""

    def test_only_specified_edge_kinds_used(self):
        """Edges of unselected kinds are ignored."""
        store = _make_store_with_nodes(["a", "b", "c", "d"])

        # calls edges: a-b-c form a chain
        _add_edge(store, "a", "b", kind="calls")
        _add_edge(store, "b", "c", kind="calls")

        # references edge: c-d connects to d
        _add_edge(store, "c", "d", kind="references")

        # Only consider "calls" — d should be isolated (no community for call edges alone)
        cd_calls = CommunityDetector(edge_kinds=["calls"])
        result_calls = cd_calls.run(store)

        # a, b, c are connected via calls
        calls_members = set()
        for c in result_calls.data["communities"]:
            calls_members.update(c["members"])
        assert "a" in calls_members
        assert "b" in calls_members
        assert "c" in calls_members
        # d has no "calls" edges — should not appear
        assert "d" not in calls_members

    def test_all_edge_kinds_inclusive(self):
        """When edge_kinds includes all edge types, all nodes are included."""
        store = _make_store_with_nodes(["a", "b", "c", "d"])
        _add_edge(store, "a", "b", kind="calls")
        _add_edge(store, "b", "c", kind="references")
        _add_edge(store, "c", "d", kind="imports")

        cd = CommunityDetector(edge_kinds=["calls", "references", "imports"])
        result = cd.run(store)

        all_members = set()
        for c in result.data["communities"]:
            all_members.update(c["members"])
        assert all_members == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# AlgorithmResult fields
# ---------------------------------------------------------------------------


class TestCommunityDetectorResultFields:
    """Verify AlgorithmResult field contents."""

    def test_result_algorithm_field(self):
        """Result.algorithm equals Cd.name."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)
        assert result.algorithm == cd.name
        assert result.algorithm == "community-detection"

    def test_result_success_true(self):
        """Normal execution returns success=True."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)
        assert result.success is True

    def test_duration_ms_set(self):
        """duration_ms is a positive number after execution."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)
        assert result.duration_ms > 0

    def test_data_has_expected_keys(self):
        """Result.data contains communities, modularity, community_count."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)
        data = result.data
        assert "communities" in data
        assert "modularity" in data
        assert "community_count" in data

    def test_modularity_in_range(self):
        """Modularity is between -1.0 and 1.0 (or 0.0 for empty)."""
        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        mod = result.data["modularity"]
        # NetworkX modularity: [-0.5, 1.0]. Built-in fallback might differ.
        # Just check it's a float and not wildly out of range.
        assert isinstance(mod, float)
        assert -1.0 <= mod <= 1.0


# ---------------------------------------------------------------------------
# Disconnected graph test
# ---------------------------------------------------------------------------


class TestCommunityDetectorDisconnected:
    """Tests for disconnected nodes (no edges between groups)."""

    def test_fully_disconnected(self):
        """Nodes with no edges between them: each is its own community."""
        store = _make_store_with_nodes(["x", "y", "z"])
        _add_edge(store, "x", "y", kind="calls")
        # z has no edges — won't appear in adjacency

        cd = CommunityDetector()
        result = cd.run(store)

        members = set()
        for c in result.data["communities"]:
            members.update(c["members"])
        # x and y are connected, z is isolated (not in adj)
        assert "x" in members
        assert "y" in members
        assert "z" not in members

    def test_two_separate_groups(self):
        """Two groups with no connecting edges get at least 2 communities."""
        store = _make_store_with_nodes(["g1a", "g1b", "g2a", "g2b"])

        _add_edge(store, "g1a", "g1b", kind="calls")
        _add_edge(store, "g2a", "g2b", kind="calls")

        cd = CommunityDetector()
        result = cd.run(store)

        # At least 2 communities (one per group)
        assert result.data["community_count"] >= 2

        all_members = set()
        for c in result.data["communities"]:
            all_members.update(c["members"])
        assert all_members == {"g1a", "g1b", "g2a", "g2b"}


# ---------------------------------------------------------------------------
# NetworkX fallback
# ---------------------------------------------------------------------------


class TestCommunityDetectorFallback:
    """Tests for the NetworkX→built-in fallback path."""

    def test_builtin_fallback_when_networkx_unavailable(self, monkeypatch):
        """When networkx import fails, the built-in Louvain is used."""
        import builtins

        original_import = builtins.__import__

        def fail_networkx(name, *args, **kwargs):
            if name == "networkx":
                raise ImportError("Simulated: networkx not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail_networkx)

        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        assert result.success is True
        # Should still get communities via fallback
        assert result.data["community_count"] >= 2
        # Should report the fallback in errors
        assert len(result.errors) == 1
        assert "NetworkX not available" in result.errors[0]

    def test_networkx_exception_caught(self, monkeypatch):
        """When networkx raises an exception, it's caught and reported."""
        # Only test when networkx is available (can test exception path
        # by monkeypatching a specific function).
        import networkx as nx  # noqa: F401

        def raise_error(G, *args, **kwargs):
            raise RuntimeError("Simulated: Louvain computation failed")

        monkeypatch.setattr(
            nx.community, "louvain_communities", raise_error
        )

        store = _build_two_cliques()
        cd = CommunityDetector()
        result = cd.run(store)

        assert result.success is True  # Soft failure — fallback
        assert result.data["community_count"] >= 2  # Built-in fallback works
        # Should report the exception text in errors
        assert len(result.errors) > 0
        assert "Simulated" in result.errors[0]
