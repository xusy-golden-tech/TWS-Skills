"""Tests for graph/algorithms/base.py — GraphAlgorithm ABC + AlgorithmResult.

Verifies:
    1. AlgorithmResult construction with all fields
    2. AlgorithmResult default values
    3. GraphAlgorithm is an ABC — cannot instantiate directly
    4. Full subclass can be instantiated
    5. Partial subclass raises TypeError
"""

import inspect
from abc import ABC

import pytest


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from tws_graph.graph.algorithms.base import AlgorithmResult, GraphAlgorithm


# ---------------------------------------------------------------------------
# AlgorithmResult tests
# ---------------------------------------------------------------------------

class TestAlgorithmResult:
    """Tests for the AlgorithmResult dataclass."""

    def test_construct_all_fields(self):
        """AlgorithmResult can be constructed with all fields."""
        result = AlgorithmResult(
            algorithm="test_algo",
            success=True,
            data={"score": 0.95},
            errors=["warning: low confidence"],
            duration_ms=42.5,
        )
        assert result.algorithm == "test_algo"
        assert result.success is True
        assert result.data == {"score": 0.95}
        assert result.errors == ["warning: low confidence"]
        assert result.duration_ms == 42.5

    def test_default_values(self):
        """AlgorithmResult default values are set correctly."""
        result = AlgorithmResult(algorithm="minhash")
        assert result.algorithm == "minhash"
        assert result.success is True
        assert result.data == {}
        assert result.errors == []
        assert result.duration_ms == 0.0

    def test_failure_result(self):
        """AlgorithmResult with success=False carries error info."""
        result = AlgorithmResult(
            algorithm="broken_algo",
            success=False,
            data={},
            errors=["connection refused", "timeout"],
            duration_ms=3000.0,
        )
        assert result.success is False
        assert len(result.errors) == 2

    def test_duration_can_be_negative(self):
        """AlgorithmResult accepts negative duration (edge case: clock skew)."""
        result = AlgorithmResult(algorithm="timewarp", duration_ms=-1.0)
        assert result.duration_ms == -1.0

    def test_is_dataclass(self):
        """AlgorithmResult is a dataclass (not a plain class)."""
        from dataclasses import is_dataclass
        assert is_dataclass(AlgorithmResult)


# ---------------------------------------------------------------------------
# GraphAlgorithm ABC tests
# ---------------------------------------------------------------------------

class TestGraphAlgorithmABC:
    """Tests for the GraphAlgorithm abstract base class."""

    def test_is_abc(self):
        """GraphAlgorithm is an ABC subclass."""
        assert issubclass(GraphAlgorithm, ABC)

    def test_cannot_instantiate_directly(self):
        """Instantiating GraphAlgorithm directly raises TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            GraphAlgorithm()  # type: ignore[abstract]

    def test_full_subclass_can_instantiate(self):
        """A subclass implementing all abstract members can be instantiated."""

        class ConcreteAlgo(GraphAlgorithm):
            @property
            def name(self) -> str:
                return "concrete_test"

            @property
            def description(self) -> str:
                return "A test algorithm for ABC compliance."

            def run(self, store: "Store") -> AlgorithmResult:
                return AlgorithmResult(algorithm=self.name)

        instance = ConcreteAlgo()
        assert instance.name == "concrete_test"
        assert instance.description == "A test algorithm for ABC compliance."
        assert isinstance(instance.run(None), AlgorithmResult)

    def test_missing_name_raises(self):
        """Subclass without 'name' property cannot be instantiated."""

        class MissingName(GraphAlgorithm):
            @property
            def description(self) -> str:
                return "missing name"

            def run(self, store: "Store") -> AlgorithmResult:
                return AlgorithmResult(algorithm="bad")

        with pytest.raises(TypeError, match="abstract"):
            MissingName()  # type: ignore[abstract]

    def test_missing_description_raises(self):
        """Subclass without 'description' can still be considered abstract."""

        class MissingDesc(GraphAlgorithm):
            @property
            def name(self) -> str:
                return "missing_desc"

            def run(self, store: "Store") -> AlgorithmResult:
                return AlgorithmResult(algorithm=self.name)

        # Missing an abstract property — should still be abstract
        abstract_methods = MissingDesc.__abstractmethods__
        assert "description" in abstract_methods

    def test_missing_run_raises(self):
        """Subclass without 'run' method cannot be instantiated."""

        class MissingRun(GraphAlgorithm):
            @property
            def name(self) -> str:
                return "missing_run"

            @property
            def description(self) -> str:
                return "forgot to implement run"

        with pytest.raises(TypeError, match="abstract"):
            MissingRun()  # type: ignore[abstract]

    def test_abstract_properties_defined(self):
        """name and description are abstract properties (not plain methods)."""
        members = inspect.getmembers(
            GraphAlgorithm, predicate=lambda m: hasattr(m, "__isabstractmethod__")
        )
        abstract_names = {name for name, _ in members}
        assert "name" in abstract_names
        assert "description" in abstract_names
        assert "run" in abstract_names
