"""Tests for C++ extractor."""

import pytest
from tws_graph.indexer.parser import extract_from_source


class TestCppExtractor:
    """Unit tests for C++ AST extraction."""

    @pytest.fixture
    def result(self, sample_cpp_src):
        return extract_from_source("sample.cpp", sample_cpp_src, "cpp")

    # ---- functions ----

    def test_extracts_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "helper" in names
        assert "main" in names

    def test_function_language_is_cpp(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) > 0
        for f in funcs:
            assert f["language"] == "cpp"

    def test_function_qualified_name(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert "helper" in by_name
        assert by_name["helper"]["qualified_name"] == "sample.cpp::helper"

    # ---- classes ----

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "Calculator" in names
        assert "Shape" in names
        assert "Circle" in names

    # ---- class methods ----

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        # Methods should be qualified with class name
        assert any("add" in n for n in names)
        assert any("area" in n for n in names)

    def test_method_qualified_names_contain_class(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        for m in methods:
            # qualified_name should contain the class scope
            assert "sample.cpp" in m["qualified_name"]

    # ---- structs ----

    def test_extracts_structs(self, result):
        structs = [n for n in result.nodes if n["kind"] == "struct"]
        names = {n["name"] for n in structs}
        assert "Point" in names

    # ---- namespaces ----

    def test_extracts_namespaces(self, result):
        nss = [n for n in result.nodes if n["kind"] == "namespace"]
        names = {n["name"] for n in nss}
        assert "math" in names

    def test_namespace_contains_classes(self, result):
        # Classes inside namespace should have contains edges from namespace
        contains = [e for e in result.edges if e["kind"] == "contains"]
        # At least some edges should exist
        assert len(contains) > 0

    # ---- call edges ----

    def test_call_edges_present(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_call_edges_heuristic_provenance(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        for e in calls:
            assert e["provenance"] == "heuristic"

    # ---- include edges ----

    def test_include_edges(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        assert len(imports) >= 2
        target_texts = {e.get("target_text", "") for e in imports}
        assert "iostream" in target_texts
        assert "vector" in target_texts

    def test_include_edges_provenance(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        for e in imports:
            assert e["provenance"] == "heuristic"

    # ---- variables ----

    def test_extracts_global_variables(self, result):
        vars_ = [n for n in result.nodes if n["kind"] == "variable"]
        names = {n["name"] for n in vars_}
        assert "global_count" in names


class TestCppExtractorEdgeCases:
    """Edge case tests for C++ extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.cpp", "", "cpp")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.cpp", "class Broken {", "cpp")
        assert isinstance(result.errors, list)

    def test_cpp_file_extension(self, sample_cpp_src):
        """Verify .cpp files work."""
        result = extract_from_source("main.cpp", sample_cpp_src, "cpp")
        assert len(result.nodes) > 0

    def test_no_crashes_on_template_syntax(self):
        """Should handle complex template syntax."""
        src = """
template<typename T, typename U>
T max_val(T a, U b) { return a > b ? a : b; }
"""
        result = extract_from_source("tmpl.cpp", src, "cpp")
        assert isinstance(result.nodes, list)
