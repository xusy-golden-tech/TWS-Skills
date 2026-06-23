"""Tests for C extractor."""

import pytest
from tws_graph.indexer.parser import extract_from_source


class TestCExtractor:
    """Unit tests for C AST extraction."""

    @pytest.fixture
    def result(self, sample_c_src):
        return extract_from_source("sample.c", sample_c_src, "c")

    # ---- functions ----

    def test_extracts_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "add" in names
        assert "print_point" in names
        assert "calculate_total" in names
        assert "main" in names

    def test_function_language_is_c(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) > 0
        for f in funcs:
            assert f["language"] == "c"

    def test_function_qualified_name(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["add"]["qualified_name"] == "sample.c::add"
        assert by_name["main"]["qualified_name"] == "sample.c::main"

    # ---- structs ----

    def test_extracts_struct(self, result):
        structs = [n for n in result.nodes if n["kind"] == "struct"]
        names = {n["name"] for n in structs}
        assert "Point" in names

    def test_struct_fields(self, result):
        """Verify struct field extraction."""
        fields = [n for n in result.nodes if n["kind"] == "field"]
        names = {n["name"] for n in fields}
        assert "x" in names
        assert "y" in names

    def test_struct_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        # Fields should have contains edges from their parent struct
        assert len(contains) > 0

    # ---- unions ----

    def test_extracts_union(self, result):
        unions = [n for n in result.nodes if n["kind"] == "union"]
        names = {n["name"] for n in unions}
        assert "Data" in names

    # ---- enums ----

    def test_extracts_enum(self, result):
        enums = [n for n in result.nodes if n["kind"] == "enum"]
        names = {n["name"] for n in enums}
        assert "Color" in names

    # ---- variables ----

    def test_extracts_global_variables(self, result):
        vars_ = [n for n in result.nodes if n["kind"] == "variable"]
        names = {n["name"] for n in vars_}
        # global_counter and hidden_counter should be extracted
        assert "global_counter" in names

    # ---- call edges ----

    def test_call_edges_present(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_call_targets(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e.get("target_text", "") for e in calls}
        assert "print_point" in targets or "printf" in targets

    def test_call_edges_have_heuristic_provenance(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        for e in calls:
            assert e["provenance"] == "heuristic"

    # ---- include edges ----

    def test_include_edges(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        assert len(imports) >= 2  # <stdio.h>, <stdlib.h>, "mylib.h"
        target_texts = {e.get("target_text", "") for e in imports}
        assert "stdio.h" in target_texts
        assert "stdlib.h" in target_texts

    def test_include_edges_provenance(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        for e in imports:
            assert e["provenance"] == "heuristic"


class TestCExtractorEdgeCases:
    """Edge case tests for C extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.c", "", "c")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.c", "int broken {", "c")
        # Should not crash; may produce errors
        assert isinstance(result.errors, list)

    def test_c_file_extension(self, sample_c_src):
        """Verify .c files work."""
        result = extract_from_source("main.c", sample_c_src, "c")
        assert len(result.nodes) > 0

    def test_no_crashes_on_malformed_includes(self):
        """Should not crash on unusual preprocessor directives."""
        src = '#include INVALID\nint x;\n'
        result = extract_from_source("weird.c", src, "c")
        assert isinstance(result.nodes, list)
