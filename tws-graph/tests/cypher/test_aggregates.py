"""Tests for cypher/aggregates.py — aggregate function registry."""

import pytest

from tws_graph.cypher.aggregates import (
    register_aggregate,
    get_aggregate,
    list_aggregates,
)


# ---------------------------------------------------------------------------
# Helper: simulate the Executor calling an aggregate over a row stream
# ---------------------------------------------------------------------------

def simulate_aggregate(agg_func, values, initial, finalize=None):
    """Call agg_func(acc, val) for each val, return final accumulator.

    If finalize is given, it transforms the accumulator into the result
    (e.g. AVG divides sum by count).
    """
    acc = initial
    for v in values:
        acc = agg_func(acc, v)
    return finalize(acc) if finalize else acc


# ---------------------------------------------------------------------------
# Registry mechanism
# ---------------------------------------------------------------------------

class TestRegistry:
    """Tests for register_aggregate / get_aggregate / list_aggregates."""

    def test_register_and_get(self):
        """Registered function is retrievable by the exact name."""
        func = lambda: 99
        register_aggregate("my_count", func)
        assert get_aggregate("my_count") is func

    def test_case_insensitive_get(self):
        """get_aggregate normalises name to uppercase before lookup."""
        func = lambda: 42
        register_aggregate("MyAgg", func)
        assert get_aggregate("myagg") is func
        assert get_aggregate("MYAGG") is func
        assert get_aggregate("MyAgg") is func

    def test_list_returns_sorted_copy(self):
        """list_aggregates returns a sorted list of names."""
        names = list_aggregates()
        assert isinstance(names, list)
        assert names == sorted(names)
        # built-in aggregates should already be present
        assert len(names) >= 6  # COUNT, SUM, AVG, MIN, MAX, COLLECT

    def test_duplicate_raises_value_error(self):
        """Registering the same name (any casing) raises ValueError."""
        register_aggregate("unique_test_agg", lambda: 1)
        with pytest.raises(ValueError, match="already registered"):
            register_aggregate("unique_test_agg", lambda: 2)
        with pytest.raises(ValueError, match="already registered"):
            register_aggregate("UNIQUE_TEST_AGG", lambda: 3)

    def test_get_nonexistent_raises_key_error(self):
        """Getting an unregistered name raises KeyError."""
        with pytest.raises(KeyError):
            get_aggregate("no_such_aggregate_xyz")


# ---------------------------------------------------------------------------
# Built-in aggregate function behaviours
# ---------------------------------------------------------------------------

class TestBuiltinCountStar:
    """COUNT(*) — closure returning 1 per row (no accumulator)."""

    def test_count_star_returns_one(self):
        func = get_aggregate("COUNT")
        assert func() == 1  # each call = 1 row

    def test_count_star_idempotent(self):
        func = get_aggregate("COUNT")
        for _ in range(5):
            assert func() == 1


class TestBuiltinSum:
    """SUM — accumulator-based addition."""

    def test_sum_positive(self):
        func = get_aggregate("SUM")
        result = simulate_aggregate(func, [10, 20, 30], 0)
        assert result == 60

    def test_sum_single(self):
        func = get_aggregate("SUM")
        result = simulate_aggregate(func, [7], 0)
        assert result == 7

    def test_sum_empty(self):
        func = get_aggregate("SUM")
        result = simulate_aggregate(func, [], 0)
        assert result == 0

    def test_sum_negative(self):
        func = get_aggregate("SUM")
        result = simulate_aggregate(func, [-5, 10, -2], 0)
        assert result == 3


class TestBuiltinAvg:
    """AVG — uses (sum, count) accumulator tuple."""

    def test_avg_positive(self):
        func = get_aggregate("AVG")
        result = simulate_aggregate(
            func, [10, 20, 30], (0, 0),
            finalize=lambda acc: acc[0] / acc[1] if acc[1] else None,
        )
        assert result == 20.0

    def test_avg_single(self):
        func = get_aggregate("AVG")
        result = simulate_aggregate(
            func, [42], (0, 0),
            finalize=lambda acc: acc[0] / acc[1] if acc[1] else None,
        )
        assert result == 42.0

    def test_avg_empty_no_rows(self):
        func = get_aggregate("AVG")
        result = simulate_aggregate(
            func, [], (0, 0),
            finalize=lambda acc: acc[0] / acc[1] if acc[1] else None,
        )
        assert result is None  # no rows → no average


class TestBuiltinMin:
    """MIN — accumulator tracks smallest value."""

    def test_min_basic(self):
        func = get_aggregate("MIN")
        result = simulate_aggregate(func, [5, 3, 9, 7], None)
        assert result == 3

    def test_min_single(self):
        func = get_aggregate("MIN")
        result = simulate_aggregate(func, [42], None)
        assert result == 42

    def test_min_empty(self):
        func = get_aggregate("MIN")
        result = simulate_aggregate(func, [], None)
        assert result is None

    def test_min_negative(self):
        func = get_aggregate("MIN")
        result = simulate_aggregate(func, [-1, 0, 1], None)
        assert result == -1


class TestBuiltinMax:
    """MAX — accumulator tracks largest value."""

    def test_max_basic(self):
        func = get_aggregate("MAX")
        result = simulate_aggregate(func, [5, 3, 9, 7], None)
        assert result == 9

    def test_max_single(self):
        func = get_aggregate("MAX")
        result = simulate_aggregate(func, [42], None)
        assert result == 42

    def test_max_empty(self):
        func = get_aggregate("MAX")
        result = simulate_aggregate(func, [], None)
        assert result is None

    def test_max_negative(self):
        func = get_aggregate("MAX")
        result = simulate_aggregate(func, [-1, 0, 1], None)
        assert result == 1


class TestBuiltinCollect:
    """COLLECT — accumulator builds a list."""

    def test_collect_basic(self):
        func = get_aggregate("COLLECT")
        result = simulate_aggregate(func, ["a", "b", "c"], [])
        assert result == ["a", "b", "c"]

    def test_collect_single(self):
        func = get_aggregate("COLLECT")
        result = simulate_aggregate(func, [99], [])
        assert result == [99]

    def test_collect_empty(self):
        func = get_aggregate("COLLECT")
        result = simulate_aggregate(func, [], [])
        assert result == []
