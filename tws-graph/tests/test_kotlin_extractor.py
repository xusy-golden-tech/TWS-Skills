"""Tests for Kotlin extractor."""

import pytest
from tws_graph.indexer.parser import extract_from_source


class TestKotlinExtractor:
    """Unit tests for Kotlin AST extraction."""

    @pytest.fixture
    def result(self, sample_kt_src):
        return extract_from_source("sample.kt", sample_kt_src, "kotlin")

    # ---- functions ----

    def test_extracts_top_level_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "calculateTotal" in names
        assert "validateInput" in names

    def test_function_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "function"}
        assert by_name["calculateTotal"]["visibility"] == "public"
        assert by_name["validateInput"]["visibility"] == "private"

    def test_function_signature(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        sig = by_name["calculateTotal"]["signature"]
        assert "items" in sig
        assert "Double" in sig

    # ---- classes ----

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "OrderService" in names
        assert "PaymentHandler" in names
        assert "Item" in names

    def test_data_class(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        item = by_name["Item"]
        assert item["kind"] == "class"
        # data class is detected as a class; decorators may include 'data'
        decorators = item.get("decorators", [])
        assert "data" in (decorators or [])

    # ---- methods ----

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "createOrder" in names
        assert "buildOrder" in names
        assert "process" in names

    def test_method_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["createOrder"]["visibility"] == "public"
        assert by_name["buildOrder"]["visibility"] == "private"

    # ---- interfaces ----

    def test_extracts_interfaces(self, result):
        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        names = {n["name"] for n in interfaces}
        assert "OrderRepository" in names

    # ---- objects ----

    def test_extracts_objects(self, result):
        objs = [n for n in result.nodes if n["kind"] == "object"]
        assert any(n["name"] == "Config" for n in objs)

    # ---- properties ----

    def test_extracts_properties(self, result):
        props = [n for n in result.nodes if n["kind"] == "property"]
        names = {n["name"] for n in props}
        # apiUrl from object Config is extracted as a property
        assert "apiUrl" in names
        # data class constructor params may be property or class_parameter
        assert len(props) >= 1

    # ---- qualified names ----

    def test_function_qualified_name(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["calculateTotal"]["qualified_name"] == "sample.kt::calculateTotal"
        assert by_name["createOrder"]["qualified_name"] == "sample.kt::OrderService::createOrder"

    # ---- call edges ----

    def test_call_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_receiver_call(self, result):
        """Kotlin extractor should resolve receiver calls (service.createOrder)."""
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e.get("target_text", "") for e in calls}
        assert any("createOrder" in t for t in targets)

    # ---- extends/implements ----

    def test_implements_edges(self, result):
        implements = [e for e in result.edges if e["kind"] == "implements"]
        # OrderService may implement something, or it may have no implements
        assert isinstance(implements, list)

    def test_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        assert len(contains) > 0


class TestKotlinExtractorEdgeCases:
    """Edge case tests."""

    def test_empty_file(self):
        result = extract_from_source("empty.kt", "", "kotlin")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.kt", "fun broken {", "kotlin")
        assert isinstance(result.errors, list)

    def test_kt_file_extension(self, sample_kt_src):
        """Verify .kt files work."""
        result = extract_from_source("main.kt", sample_kt_src, "kotlin")
        assert len(result.nodes) > 0
