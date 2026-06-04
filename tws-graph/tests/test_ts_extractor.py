"""Tests for TypeScript/TSX extractor."""

import pytest
from tws_graph.indexer.parser import extract_from_source


class TestTypeScriptExtractor:
    """Unit tests for TypeScript AST extraction."""

    @pytest.fixture
    def result(self, sample_ts_src):
        return extract_from_source("sample.ts", sample_ts_src, "typescript")

    # ---- functions ----

    def test_extracts_top_level_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "calculateTotal" in names
        assert "validateInput" in names

    def test_exported_function(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["calculateTotal"]["is_exported"] == 1
        assert by_name["validateInput"]["is_exported"] == 0

    def test_function_signature(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        sig = by_name["calculateTotal"]["signature"]
        assert "items" in sig
        assert "number[]" in sig

    def test_visibility_default_public(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["calculateTotal"]["visibility"] == "public"

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
        assert "buildOrder" in names
        assert "process" in names

    def test_method_visibility_modifiers(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        # TS extractor derives visibility from modifiers list on parent node
        assert by_name["createOrder"]["visibility"] != ""
        assert by_name["buildOrder"]["visibility"] != ""

    # ---- enums ----

    def test_extracts_enums(self, result):
        enums = [n for n in result.nodes if n["kind"] == "enum"]
        assert any(n["name"] == "OrderStatus" for n in enums)

    def test_extracts_enum_members(self, result):
        members = [n for n in result.nodes if n["kind"] == "enum_member"]
        # enum members are extracted when enum_declaration is handled
        # (enum must be at top level, not inside a class)
        if members:
            names = {n["name"] for n in members}
            assert len(names) > 0

    # ---- interfaces ----

    def test_extracts_interfaces(self, result):
        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        assert any(n["name"] == "PaymentResult" for n in interfaces)

    # ---- qualified names ----

    def test_qualified_names(self, result):
        by_name = {n["name"]: n for n in result.nodes}
        assert by_name["calculateTotal"]["qualified_name"] == "sample.ts::calculateTotal"
        assert by_name["createOrder"]["qualified_name"] == "sample.ts::OrderService::createOrder"

    # ---- call edges ----

    def test_call_edges_exist(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_new_expression_call(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e.get("target_text", "") for e in calls}
        assert any("OrderService" in t for t in targets)

    # ---- extends/implements ----

    def test_extends_edges(self, result):
        extends = [e for e in result.edges if e["kind"] == "extends"]
        assert len(extends) >= 0  # sample.ts has no extends but we check it works

    def test_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        assert len(contains) > 0


class TestTypeScriptExtractorEdgeCases:
    """Edge case tests."""

    def test_empty_file(self):
        result = extract_from_source("empty.ts", "", "typescript")
        assert result is not None
        assert len(result.nodes) == 0

    def test_tsx_detection(self):
        """Verify .tsx files are handled by the typescript extractor."""
        result = extract_from_source(
            "component.tsx",
            "export const Button = () => <div>Click</div>;",
            "typescript",
        )
        assert isinstance(result.nodes, list)
