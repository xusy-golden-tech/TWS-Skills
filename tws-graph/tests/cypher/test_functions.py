"""Tests for cypher/functions.py — scalar function registry."""

import math
import pytest

from tws_graph.cypher.functions import (
    register_function,
    get_function,
    list_functions,
)


# ---------------------------------------------------------------------------
# Registry mechanism
# ---------------------------------------------------------------------------

class TestRegistry:
    """Tests for register_function / get_function / list_functions."""

    def test_register_and_get(self):
        """Registered function is retrievable by the exact name."""
        func = lambda x: x * 2
        register_function("double", func)
        assert get_function("double") is func

    def test_case_insensitive_get(self):
        """get_function normalises name to lowercase before lookup."""
        func = lambda x: x.upper()
        register_function("Shout", func)
        assert get_function("shout") is func
        assert get_function("SHOUT") is func
        assert get_function("Shout") is func

    def test_list_returns_sorted_copy(self):
        """list_functions returns a sorted list of names."""
        names = list_functions()
        assert isinstance(names, list)
        assert names == sorted(names)
        # built-in functions should already be present
        assert len(names) >= 5  # toUpper, toLower, toString, coalesce, type

    def test_duplicate_raises_value_error(self):
        """Registering the same name (any casing) raises ValueError."""
        register_function("unique_test_func", lambda x: x)
        with pytest.raises(ValueError, match="already registered"):
            register_function("unique_test_func", lambda x: x + 1)
        with pytest.raises(ValueError, match="already registered"):
            register_function("UNIQUE_TEST_FUNC", lambda x: x + 2)

    def test_get_nonexistent_raises_key_error(self):
        """Getting an unregistered name raises KeyError."""
        with pytest.raises(KeyError):
            get_function("no_such_function_xyz")


# ---------------------------------------------------------------------------
# Built-in scalar function behaviours
# ---------------------------------------------------------------------------

class TestToUpper:
    """toUpper — convert string to uppercase."""

    def test_basic(self):
        func = get_function("toUpper")
        assert func("abc") == "ABC"
        assert func("HeLLo") == "HELLO"

    def test_empty_string(self):
        func = get_function("toUpper")
        assert func("") == ""

    def test_none(self):
        func = get_function("toUpper")
        assert func(None) is None

    def test_non_string_coerced(self):
        func = get_function("toUpper")
        # non-string values are stringified first
        assert func(123) == "123"


class TestToLower:
    """toLower — convert string to lowercase."""

    def test_basic(self):
        func = get_function("toLower")
        assert func("ABC") == "abc"
        assert func("HeLLo") == "hello"

    def test_empty_string(self):
        func = get_function("toLower")
        assert func("") == ""

    def test_none(self):
        func = get_function("toLower")
        assert func(None) is None


class TestToString:
    """toString — convert value to string representation."""

    def test_int(self):
        func = get_function("toString")
        assert func(123) == "123"

    def test_float(self):
        func = get_function("toString")
        assert func(3.14) == "3.14"

    def test_string(self):
        func = get_function("toString")
        assert func("hello") == "hello"

    def test_none(self):
        func = get_function("toString")
        assert func(None) is None

    def test_bool(self):
        func = get_function("toString")
        assert func(True) == "True"


class TestCoalesce:
    """coalesce — return first non-None argument."""

    def test_first_non_none(self):
        func = get_function("coalesce")
        assert func(None, "b") == "b"

    def test_third_non_none(self):
        func = get_function("coalesce")
        assert func(None, None, "c") == "c"

    def test_first_already_non_none(self):
        func = get_function("coalesce")
        assert func("a", "b") == "a"

    def test_all_none(self):
        func = get_function("coalesce")
        assert func(None, None) is None

    def test_single_arg(self):
        func = get_function("coalesce")
        assert func(None) is None
        assert func("only") == "only"


class TestType:
    """type — return the type name of a value."""

    def test_int(self):
        func = get_function("type")
        assert func(42) == "int"

    def test_str(self):
        func = get_function("type")
        assert func("hello") == "str"

    def test_float(self):
        func = get_function("type")
        assert func(3.14) == "float"

    def test_bool(self):
        func = get_function("type")
        assert func(True) == "bool"

    def test_list(self):
        func = get_function("type")
        assert func([1, 2, 3]) == "list"

    def test_none(self):
        func = get_function("type")
        assert func(None) is None
