"""Tests for Lua extractor (P43)."""

import pytest
from tws_graph.indexer.parser import extract_from_source


SAMPLE_LUA_SRC = """\
-- Sample Lua script for testing

local function greet(name)
    print("Hello " .. name)
    return true
end

function global_helper()
    return 42
end

local x = 10
local y = "hello"

require("my_module")
require("another.module")

greet("World")
global_helper()
"""


@pytest.fixture(scope="module")
def result():
    return extract_from_source("sample.lua", SAMPLE_LUA_SRC, "lua")


class TestLuaExtractorFunctions:
    """Tests for function extraction from Lua scripts."""

    def test_extracts_local_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "greet" in names

    def test_extracts_global_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "global_helper" in names

    def test_function_count(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 2

    def test_function_has_line_numbers(self, result):
        for n in result.nodes:
            if n["kind"] == "function":
                assert n["start_line"] > 0
                assert n["end_line"] >= n["start_line"]

    def test_function_language_is_lua(self, result):
        for n in result.nodes:
            if n["kind"] == "function":
                assert n["language"] == "lua"

    def test_function_qualified_name(self, result):
        func = next(n for n in result.nodes if n["name"] == "greet")
        assert "sample.lua" in func["qualified_name"]
        assert "greet" in func["qualified_name"]

    def test_function_has_signature(self, result):
        func = next(n for n in result.nodes if n["name"] == "greet")
        assert "function greet" in func["signature"]


class TestLuaExtractorEdges:
    """Tests for edge extraction from Lua scripts."""

    def test_extracts_writes_edges(self, result):
        writes = [e for e in result.edges if e["kind"] == "writes"]
        targets = {e["target_text"] for e in writes}
        assert "x" in targets
        assert "y" in targets

    def test_extracts_calls_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e["target_text"] for e in calls}
        assert "print" in targets
        assert "greet" in targets or "global_helper" in targets

    def test_extracts_require_as_imports(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        targets = {e["target_text"] for e in imports}
        assert "my_module" in targets
        assert "another.module" in targets

    def test_all_edges_have_source_loc(self, result):
        for e in result.edges:
            assert "source_loc" in e
            assert "sample.lua" in e["source_loc"]

    def test_all_edges_have_provenance(self, result):
        for e in result.edges:
            assert e.get("provenance") == "tree-sitter"


class TestLuaExtractorEmptyInput:
    """Tests for edge cases."""

    def test_empty_script(self):
        result = extract_from_source("empty.lua", "", "lua")
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 0

    def test_comment_only_script(self):
        result = extract_from_source("comments.lua", "-- Just a comment\n--[[ block ]]\n", "lua")
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 0
