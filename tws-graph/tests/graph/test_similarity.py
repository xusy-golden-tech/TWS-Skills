"""Tests for graph/algorithms/similarity.py — CloneDetector.

Verifies:
    1. Empty body nodes don't produce false positives
    2. Identical body produces 1.0 similarity
    3. Modified identifiers produce similarity > 0.8
    4. Completely different body doesn't pass threshold
    5. Empty function list returns empty result
    6. AlgorithmResult fields are correct
    7. Method nodes are also checked
    8. Threshold boundary is respected
"""

from __future__ import annotations

import pytest

from tws_graph.graph.algorithms.similarity import CloneDetector
from tws_graph.graph.algorithms.base import AlgorithmResult
from tws_graph.store.memory_store import MemoryStore


def _make_node(
    nid: str,
    kind: str,
    name: str,
    body: str = "",
    **kwargs,
) -> dict:
    """Helper to create a minimal node dict for MemoryStore testing."""
    return {
        "id": nid,
        "kind": kind,
        "name": name,
        "qualified_name": f"test.{name}",
        "file_path": f"/test/{name}.py",
        "language": "python",
        "start_line": 1,
        "end_line": 10,
        "body": body,
        **kwargs,
    }


class TestCloneDetector:
    """Tests for CloneDetector graph algorithm."""

    # ------------------------------------------------------------------
    # Acceptance criteria
    # ------------------------------------------------------------------

    def test_empty_body_no_false_positive(self):
        """Two nodes with empty body should not produce a clone pair."""
        store = MemoryStore()
        store.insert_node(_make_node("a", "function", "func_a", body=""))
        store.insert_node(_make_node("b", "function", "func_b", body=""))

        detector = CloneDetector(threshold=0.8)
        result = detector.run(store)

        assert result.success is True
        assert result.data["similar_pairs"] == []

    def test_identical_body_similarity_one(self):
        """Two functions with identical body should have 1.0 similarity."""
        store = MemoryStore()
        body = "def add(x, y):\n    return x + y"
        store.insert_node(_make_node("a", "function", "add", body=body))
        store.insert_node(_make_node("b", "function", "add_v2", body=body))

        detector = CloneDetector(threshold=0.5)
        result = detector.run(store)

        pairs = result.data["similar_pairs"]
        assert len(pairs) == 1
        assert pairs[0]["similarity"] == 1.0
        assert set([pairs[0]["node_a"], pairs[0]["node_b"]]) == {"a", "b"}

    def test_modified_identifiers_similarity_above_threshold(self):
        """Functions with only identifier differences should have high similarity.

        After AST token normalization, identifiers map to 'I', so the two
        token sequences are identical, yielding Jaccard = 1.0.
        """
        store = MemoryStore()
        body_a = (
            "def calculate_total(items):\n"
            "    result = 0\n"
            "    for i in items:\n"
            "        result += i\n"
            "    return result"
        )
        body_b = (
            "def compute_sum(values):\n"
            "    result = 0\n"
            "    for x in values:\n"
            "        result += x\n"
            "    return result"
        )

        store.insert_node(
            _make_node("a", "function", "calculate_total", body=body_a)
        )
        store.insert_node(
            _make_node("b", "function", "compute_sum", body=body_b)
        )

        detector = CloneDetector(threshold=0.8)
        result = detector.run(store)

        pairs = result.data["similar_pairs"]
        assert len(pairs) == 1
        assert pairs[0]["similarity"] >= 0.8
        assert pairs[0]["name_a"] == "calculate_total"
        assert pairs[0]["name_b"] == "compute_sum"

    def test_completely_different_body_below_threshold(self):
        """Completely different bodies should not pass the threshold."""
        store = MemoryStore()
        body_a = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    return fibonacci(n-1) + fibonacci(n-2)"
        )
        body_b = (
            "def quicksort(arr):\n"
            "    if len(arr) <= 1:\n"
            "        return arr\n"
            "    pivot = arr[0]\n"
            "    left = [x for x in arr[1:] if x <= pivot]\n"
            "    right = [x for x in arr[1:] if x > pivot]\n"
            "    return quicksort(left) + [pivot] + quicksort(right)"
        )

        store.insert_node(
            _make_node("a", "function", "fibonacci", body=body_a)
        )
        store.insert_node(
            _make_node("b", "function", "quicksort", body=body_b)
        )

        detector = CloneDetector(threshold=0.8)
        result = detector.run(store)

        pairs = result.data["similar_pairs"]
        assert len(pairs) == 0

    def test_empty_function_list(self):
        """Empty store with no function/method nodes returns empty result."""
        store = MemoryStore()
        # Insert a non-function node (class) — should be excluded
        store.insert_node(
            _make_node(
                "cls1", "class", "MyClass",
                body="class MyClass:\n    pass",
            )
        )

        detector = CloneDetector(threshold=0.8)
        result = detector.run(store)

        assert result.data["similar_pairs"] == []
        assert result.data["total_functions_checked"] == 0
        assert result.data["total_comparisons"] == 0
        assert result.data["threshold"] == 0.8

    def test_algorithm_result_fields_correct(self):
        """AlgorithmResult fields are properly set."""
        store = MemoryStore()
        store.insert_node(
            _make_node("a", "function", "func_a",
                       body="def foo():\n    return 1")
        )
        store.insert_node(
            _make_node("b", "function", "func_b",
                       body="def foo():\n    return 1")
        )

        detector = CloneDetector(
            threshold=0.5, num_perm=64, bands=8, rows=8,
        )
        result = detector.run(store)

        assert isinstance(result, AlgorithmResult)
        assert result.algorithm == "clone-detection"
        assert result.success is True
        assert result.duration_ms >= 0
        assert "similar_pairs" in result.data
        assert "total_functions_checked" in result.data
        assert "total_comparisons" in result.data
        assert result.data["threshold"] == 0.5
        assert result.data["total_functions_checked"] == 2

    # ------------------------------------------------------------------
    # Additional coverage
    # ------------------------------------------------------------------

    def test_method_nodes_also_checked(self):
        """Method nodes (kind='method') should also be checked."""
        store = MemoryStore()
        body = "def process(self):\n    return self.data"
        store.insert_node(
            _make_node("a", "function", "util_process", body=body)
        )
        store.insert_node(
            _make_node("b", "method", "process", body=body)
        )

        detector = CloneDetector(threshold=0.5)
        result = detector.run(store)

        assert result.data["total_functions_checked"] == 2
        pairs = result.data["similar_pairs"]
        assert len(pairs) == 1
        assert pairs[0]["similarity"] == 1.0

    def test_respects_threshold_boundary(self):
        """Pairs at exactly threshold value are included."""
        store = MemoryStore()
        body = "def a():\n    x = 1\n    y = 2\n    return x + y"
        store.insert_node(_make_node("a", "function", "a", body=body))
        store.insert_node(_make_node("b", "function", "b", body=body))

        # Threshold = 1.0, identical bodies produce Jaccard = 1.0 >= 1.0
        detector = CloneDetector(threshold=1.0)
        result = detector.run(store)
        assert len(result.data["similar_pairs"]) == 1

    def test_single_node_no_pairs(self):
        """A single function node produces no pairs."""
        store = MemoryStore()
        store.insert_node(
            _make_node("a", "function", "lonely", body="def lonley():\n    pass")
        )

        detector = CloneDetector()
        result = detector.run(store)

        assert result.data["similar_pairs"] == []
        assert result.data["total_functions_checked"] == 1
        assert result.data["total_comparisons"] == 0

    def test_name_fields_populated(self):
        """Each pair entry includes name_a and name_b from the node."""
        store = MemoryStore()
        body = "def hello():\n    print('hello')"
        store.insert_node(
            _make_node("n1", "function", "greet_zh", body=body)
        )
        store.insert_node(
            _make_node("n2", "function", "greet_en", body=body)
        )

        detector = CloneDetector(threshold=0.5)
        result = detector.run(store)

        pair = result.data["similar_pairs"][0]
        assert pair["name_a"] == "greet_zh"
        assert pair["name_b"] == "greet_en"

    def test_default_parameters(self):
        """CloneDetector defaults to threshold=0.8, num_perm=128,
        bands=16, rows=8."""
        detector = CloneDetector()
        assert detector._threshold == 0.8
        assert detector._num_perm == 128
        assert detector._bands == 16
        assert detector._rows == 8
