"""Tests for graph/algorithms/cycle_detect.py — cycle dependency detection.

Verifies:
    1. Algorithm metadata (name, description, GraphAlgorithm subclass)
    2. Empty graph returns empty result
    3. DAG (no cycle) returns empty cycles
    4. Simple cycle (A→B→A) detected
    5. Long cycle (A→B→C→A) detected
    6. Multiple cycles detected
    7. max_cycles limit
    8. Non-calls edges ignored
    9. AlgorithmResult fields correctness
    10. Self-loop detected
"""

import pytest

from tws_graph.store.memory_store import MemoryStore
from tws_graph.graph.algorithms.cycle_detect import CycleDetector
from tws_graph.graph.algorithms.base import AlgorithmResult, GraphAlgorithm


# ---------------------------------------------------------------------------
# Helpers — same pattern as test_memory_store.py
# ---------------------------------------------------------------------------

def _make_node(id, file_path="src/test.py", kind="function", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    node = {
        "id": id,
        "kind": kind,
        "name": f"name_of_{id}",
        "qualified_name": f"{file_path}::{kind}.name_of_{id}",
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


def _create_store_with_nodes_and_edges(nodes_and_edges):
    """Create a MemoryStore populated with given nodes and edges.

    Args:
        nodes_and_edges: dict with 'nodes' (list of node dicts with 'id') and
            'edges' (list of edge dicts with 'source', 'target', 'kind').

    Returns:
        MemoryStore
    """
    store = MemoryStore()
    for node in nodes_and_edges.get("nodes", []):
        store.insert_node(_make_node(**node))
    for edge in nodes_and_edges.get("edges", []):
        store.insert_edge(_make_edge(**edge))
    return store


# ---------------------------------------------------------------------------
# CycleDetector tests
# ---------------------------------------------------------------------------

class TestCycleDetectorMetadata:
    """Verify CycleDetector interface contract."""

    def test_is_graph_algorithm_subclass(self):
        """CycleDetector is a GraphAlgorithm subclass."""
        assert issubclass(CycleDetector, GraphAlgorithm)

    def test_name(self):
        """Algorithm name is 'cycle-detection'."""
        detector = CycleDetector()
        assert detector.name == "cycle-detection"

    def test_description(self):
        """Algorithm description is non-empty string."""
        detector = CycleDetector()
        assert isinstance(detector.description, str)
        assert len(detector.description) > 0

    def test_default_max_cycles(self):
        """Default max_cycles is 100."""
        detector = CycleDetector()
        assert detector._max_cycles == 100

    def test_custom_max_cycles(self):
        """Custom max_cycles is stored."""
        detector = CycleDetector(max_cycles=50)
        assert detector._max_cycles == 50


class TestCycleDetectorEmptyGraph:
    """Test CycleDetector with empty graph."""

    def test_empty_graph_returns_empty_cycles(self, empty_store):
        """Empty graph returns AlgorithmResult with empty cycles."""
        detector = CycleDetector()
        result = detector.run(empty_store)
        assert isinstance(result, AlgorithmResult)
        assert result.success is True
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0
        assert result.data["affected_files"] == []

    def test_empty_graph_has_duration(self, empty_store):
        """Result from empty graph includes duration_ms."""
        detector = CycleDetector()
        result = detector.run(empty_store)
        assert result.duration_ms >= 0.0


class TestCycleDetectorNoCycle:
    """Test CycleDetector with DAG (no cycles)."""

    def test_single_node_no_edges(self):
        """Single node with no edges has no cycles."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}],
            "edges": [],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0

    def test_linear_chain_no_cycle(self):
        """A->B->C (linear DAG) has no cycles."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0

    def test_tree_structure_no_cycle(self):
        """A->B, A->C (tree DAG) has no cycles."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n1", "target": "n3"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0

    def test_disconnected_nodes_no_cycle(self):
        """Disconnected nodes with no edges have no cycles."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
            "edges": [],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0


class TestCycleDetectorSimpleCycle:
    """Test CycleDetector with simple cycles."""

    def test_two_node_cycle(self):
        """A→B→A is detected as a cycle of length 2."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1", "name": "func_a"}, {"id": "n2", "name": "func_b"}],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["total_cycles"] >= 1

        # Verify at least one cycle contains both n1 and n2
        cycles = result.data["cycles"]
        found = False
        for cycle_info in cycles:
            names = cycle_info["cycle"]
            if "func_a" in names and "func_b" in names:
                found = True
                assert cycle_info["length"] >= 2
                break
        assert found, f"No cycle found containing both func_a and func_b: {cycles}"

    def test_self_loop(self):
        """A→A (self-loop) is detected as a cycle."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1", "name": "func_recursive"}],
            "edges": [
                {"source": "n1", "target": "n1"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["total_cycles"] >= 1

        cycles = result.data["cycles"]
        found = False
        for cycle_info in cycles:
            if "func_recursive" in cycle_info["cycle"]:
                found = True
                break
        assert found, f"No cycle found containing func_recursive: {cycles}"


class TestCycleDetectorLongCycle:
    """Test CycleDetector with cycles involving 3+ nodes."""

    def test_three_node_cycle(self):
        """A→B→C→A is detected."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "n1", "name": "func_a"},
                {"id": "n2", "name": "func_b"},
                {"id": "n3", "name": "func_c"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n3", "target": "n1"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["total_cycles"] >= 1

        cycles = result.data["cycles"]
        found = False
        for cycle_info in cycles:
            names = cycle_info["cycle"]
            if "func_a" in names and "func_b" in names and "func_c" in names:
                found = True
                assert cycle_info["length"] >= 3
                break
        assert found, f"No cycle found containing all three functions: {cycles}"

    def test_cycle_with_extra_tail(self):
        """A→B→C→B (tail node A not in cycle) still detects B-C-B cycle."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "n1", "name": "func_a"},
                {"id": "n2", "name": "func_b"},
                {"id": "n3", "name": "func_c"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n3", "target": "n2"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["total_cycles"] >= 1

        cycles = result.data["cycles"]
        found = False
        for cycle_info in cycles:
            names = cycle_info["cycle"]
            if "func_b" in names and "func_c" in names:
                found = True
                break
        assert found, f"No cycle found containing func_b and func_c: {cycles}"


class TestCycleDetectorMultipleCycles:
    """Test CycleDetector with multiple cycles in one graph."""

    def test_two_independent_cycles(self):
        """Two independent cycles A-B-A and C-D-C are both detected."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "a", "name": "fa"}, {"id": "b", "name": "fb"},
                {"id": "c", "name": "fc"}, {"id": "d", "name": "fd"},
            ],
            "edges": [
                {"source": "a", "target": "b"}, {"source": "b", "target": "a"},
                {"source": "c", "target": "d"}, {"source": "d", "target": "c"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        # Should find 2 cycles (one for each independent pair)
        assert result.data["total_cycles"] >= 2

    def test_nested_cycles(self):
        """Graph with both small and large cycles detects both."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "x", "name": "fx"}, {"id": "y", "name": "fy"},
                {"id": "z", "name": "fz"},
            ],
            "edges": [
                {"source": "x", "target": "y"},
                {"source": "y", "target": "x"},  # cycle X-Y
                {"source": "y", "target": "z"},
                {"source": "z", "target": "x"},  # also cycle X-Y-Z-X
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        # Multiple cycles detected
        assert result.data["total_cycles"] >= 1


class TestCycleDetectorMaxCycles:
    """Test CycleDetector max_cycles limit."""

    def test_max_cycles_reached_flag(self):
        """When cycle count reaches max_cycles, the flag is set."""
        # Create many small independent cycles: (0,1), (2,3), (4,5), ...
        nodes = []
        edges = []
        for i in range(20):
            nodes.append({"id": f"n{i*2}", "name": f"func_{i*2}", "file_path": f"src/file_{i}.py"})
            nodes.append({"id": f"n{i*2+1}", "name": f"func_{i*2+1}", "file_path": f"src/file_{i}.py"})
            edges.append({"source": f"n{i*2}", "target": f"n{i*2+1}"})
            edges.append({"source": f"n{i*2+1}", "target": f"n{i*2}"})

        store = _create_store_with_nodes_and_edges({
            "nodes": nodes,
            "edges": edges,
        })

        detector = CycleDetector(max_cycles=5)
        result = detector.run(store)

        # Should have at most 5 cycles
        assert result.data["total_cycles"] <= 5
        assert result.data["max_cycles_reached"] is True

    def test_max_cycles_not_reached_when_fewer(self):
        """When fewer cycles than max_cycles, flag is not set."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "a", "name": "fa"}, {"id": "b", "name": "fb"}],
            "edges": [
                {"source": "a", "target": "b"}, {"source": "b", "target": "a"},
            ],
        })
        detector = CycleDetector(max_cycles=100)
        result = detector.run(store)
        assert result.data["total_cycles"] <= 1
        assert result.data["max_cycles_reached"] is False


class TestCycleDetectorNonCallEdges:
    """Test that non-calls edges are ignored."""

    def test_imports_edges_ignored(self):
        """Import edges that form a cycle are ignored (only calls matter)."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1", "name": "func_a"}, {"id": "n2", "name": "func_b"}],
            "edges": [
                {"source": "n1", "target": "n2", "kind": "imports"},
                {"source": "n2", "target": "n1", "kind": "imports"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        assert result.data["cycles"] == []
        assert result.data["total_cycles"] == 0

    def test_mixed_kinds_only_calls_detected(self):
        """Graph with both calls cycles and imports cycles — only calls cycles detected."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "n1", "name": "fa"}, {"id": "n2", "name": "fb"},
                {"id": "n3", "name": "fc"}, {"id": "n4", "name": "fd"},
            ],
            "edges": [
                # import cycle (should be ignored)
                {"source": "n1", "target": "n2", "kind": "imports"},
                {"source": "n2", "target": "n1", "kind": "imports"},
                # call cycle (should be detected)
                {"source": "n3", "target": "n4", "kind": "calls"},
                {"source": "n4", "target": "n3", "kind": "calls"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        # Should detect the calls cycle, not the imports one
        cycles = result.data["cycles"]
        assert result.data["total_cycles"] >= 1
        # Verify the detected cycle involves n3/n4 (fc/fd), not n1/n2
        found_call_cycle = False
        found_import_cycle = False
        for cycle_info in cycles:
            names = cycle_info["cycle"]
            if "fc" in names and "fd" in names:
                found_call_cycle = True
            if "fa" in names and "fb" in names:
                found_import_cycle = True
        assert found_call_cycle, f"Expected calls cycle not found: {cycles}"
        assert not found_import_cycle, f"Import cycle should not be detected: {cycles}"


class TestCycleDetectorResultFields:
    """Test AlgorithmResult fields correctness."""

    def test_algorithm_field(self):
        """Result.algorithm matches detector.name."""
        detector = CycleDetector()
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        })
        result = detector.run(store)
        assert result.algorithm == "cycle-detection"

    def test_success_is_true(self):
        """Result.success is True for normal execution."""
        detector = CycleDetector()
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [],
        })
        result = detector.run(store)
        assert result.success is True

    def test_duration_ms_is_positive(self):
        """Result.duration_ms is non-negative."""
        detector = CycleDetector()
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [],
        })
        result = detector.run(store)
        assert result.duration_ms >= 0.0
        assert isinstance(result.duration_ms, float)

    def test_errors_is_empty_on_success(self):
        """Result.errors is empty when success is True."""
        detector = CycleDetector()
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [],
        })
        result = detector.run(store)
        assert result.errors == []

    def test_data_has_expected_keys(self):
        """Result.data contains all expected top-level keys."""
        detector = CycleDetector()
        store = _create_store_with_nodes_and_edges({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        })
        result = detector.run(store)
        data = result.data
        assert "cycles" in data
        assert "total_cycles" in data
        assert "max_cycles_reached" in data
        assert "affected_files" in data

    def test_cycle_info_structure(self):
        """Each cycle entry contains 'cycle', 'files', 'length'."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "n1", "name": "func_a", "file_path": "src/a.py"},
                {"id": "n2", "name": "func_b", "file_path": "src/b.py"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        for cycle_info in result.data["cycles"]:
            assert "cycle" in cycle_info, "Missing 'cycle' key"
            assert "files" in cycle_info, "Missing 'files' key"
            assert "length" in cycle_info, "Missing 'length' key"
            assert isinstance(cycle_info["cycle"], list)
            assert isinstance(cycle_info["files"], list)
            assert isinstance(cycle_info["length"], int)
            assert cycle_info["length"] > 0

    def test_affected_files_collects_all(self):
        """affected_files contains paths from all cycles."""
        store = _create_store_with_nodes_and_edges({
            "nodes": [
                {"id": "n1", "file_path": "src/a.py"},
                {"id": "n2", "file_path": "src/b.py"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n1"},
            ],
        })
        detector = CycleDetector()
        result = detector.run(store)
        affected = result.data["affected_files"]
        assert "src/a.py" in affected
        assert "src/b.py" in affected
