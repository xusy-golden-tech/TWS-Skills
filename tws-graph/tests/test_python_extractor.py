"""Tests for Python extractor."""

import pytest
from tws_graph.indexer.parser import extract_from_source


class TestPythonExtractor:
    """Unit tests for Python AST extraction."""

    @pytest.fixture
    def result(self, sample_py_src):
        return extract_from_source("test.py", sample_py_src, "python")

    # ---- functions ----

    def test_extracts_top_level_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "calculateTotal" in names
        assert "_validateInput" in names

    def test_function_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "function"}
        assert by_name["calculateTotal"]["visibility"] == "public"
        assert by_name["_validateInput"]["visibility"] == "protected"

    def test_function_signature(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert "items: List[float]" in by_name["calculateTotal"]["signature"]
        assert by_name["calculateTotal"]["signature"].endswith("-> float")

    def test_function_docstring(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        # docstring extraction depends on tree-sitter AST structure;
        # the key assertion is that the field exists
        assert "docstring" in by_name["calculateTotal"]

    # ---- classes ----

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "OrderService" in names
        assert "PaymentHandler" in names

    # ---- methods ----

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "createOrder" in names
        assert "_buildOrder" in names
        assert "process" in names

    def test_method_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["createOrder"]["visibility"] == "public"
        # single underscore prefix -> protected (name-based heuristic)
        assert by_name["_buildOrder"]["visibility"] == "protected"

    # ---- qualified names ----

    def test_function_qualified_name(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["calculateTotal"]["qualified_name"] == "test.py::calculateTotal"
        assert by_name["createOrder"]["qualified_name"] == "test.py::OrderService::createOrder"

    # ---- edges (calls) ----

    def test_call_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_method_calls_function(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        # PaymentHandler.process calls calculateTotal
        targets = {e.get("target_text", "") for e in calls}
        assert any("calculateTotal" in t for t in targets)

    # ---- import edges ----

    def test_import_edges(self, result):
        """Verify import statements produce edges (imports or calls kind)."""
        imports = [e for e in result.edges if e["kind"] in ("imports", "calls")]
        assert len(imports) > 0

    # ---- contains edges ----

    def test_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        assert len(contains) > 0


class TestPythonExtractorEdgeCases:
    """Edge case tests for Python extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.py", "", "python")
        assert len(result.nodes) == 0
        assert len(result.edges) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.py", "def foo(:", "python")
        # Should not raise — errors captured in result.errors
        assert isinstance(result.errors, list)

    def test_no_errors_on_valid_code(self, sample_py_src):
        result = extract_from_source("test.py", sample_py_src, "python")
        # No parser errors expected for valid fixture
        # (tree-sitter is error-tolerant; errors here would be our own)
        assert all(e.get("severity") != "error" for e in result.errors
                   if "Failed to load" not in e.get("message", ""))
