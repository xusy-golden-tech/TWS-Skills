"""Tests for PHP extractor."""

import os
import pytest
from tws_graph.indexer.parser import extract_from_source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_php_src():
    """Return the sample.php fixture content."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures", "php")
    with open(os.path.join(fixtures_dir, "sample.php"), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def result(sample_php_src):
    return extract_from_source("sample.php", sample_php_src, "php")


# ===========================================================================
# Class/interface/trait extraction
# ===========================================================================

class TestPhpTypeExtraction:
    """Tests for PHP class/interface/trait extraction."""

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "OrderService" in names
        assert "PaymentHandler" in names

    def test_extracts_interfaces(self, result):
        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        names = {n["name"] for n in interfaces}
        assert "OrderRepositoryInterface" in names

    def test_extracts_traits(self, result):
        traits = [n for n in result.nodes if n["kind"] == "trait"]
        names = {n["name"] for n in traits}
        assert "LoggerTrait" in names


# ===========================================================================
# Method extraction
# ===========================================================================

class TestPhpMethodExtraction:
    """Tests for PHP method extraction."""

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "createOrder" in names
        assert "buildOrder" in names
        assert "validateInput" in names
        assert "process" in names
        assert "calculateTotal" in names
        assert "log" in names

    def test_method_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "method"}
        assert by_name["createOrder"]["visibility"] == "public"
        assert by_name["buildOrder"]["visibility"] == "private"


# ===========================================================================
# Function extraction
# ===========================================================================

class TestPhpFunctionExtraction:
    """Tests for PHP function extraction."""

    def test_extracts_top_level_functions(self, result):
        functions = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in functions}
        assert "calculate_discount" in names


# ===========================================================================
# Namespace extraction
# ===========================================================================

class TestPhpNamespaceExtraction:
    """Tests for PHP namespace extraction."""

    def test_extracts_namespace(self, result):
        namespaces = [n for n in result.nodes if n["kind"] == "namespace"]
        assert any(n["name"] == "SampleApp" for n in namespaces)


# ===========================================================================
# Property extraction
# ===========================================================================

class TestPhpPropertyExtraction:
    """Tests for PHP property extraction."""

    def test_extracts_properties(self, result):
        props = [n for n in result.nodes if n["kind"] == "property"]
        names = {n["name"] for n in props}
        assert "repo" in names
        assert "apiUrl" in names


# ===========================================================================
# Edge extraction
# ===========================================================================

class TestPhpEdges:
    """Tests for PHP edge extraction."""

    def test_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        assert len(contains) > 0

    def test_call_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_import_edges(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        assert len(imports) > 0

    def test_call_target_text(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e.get("target_text", "") for e in calls}
        assert any("createOrder" in t for t in targets)


# ===========================================================================
# Edge cases
# ===========================================================================

class TestPhpEdgeCases:
    """Edge case tests for PHP extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.php", "", "php")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.php", "<?php class Broken {", "php")
        assert isinstance(result.errors, list)

    def test_nodes_have_proper_ids(self, result):
        for node in result.nodes:
            assert "id" in node
            assert len(node["id"]) > 0
            assert node["language"] == "php"

    def test_nodes_have_qualified_names(self, result):
        for node in result.nodes:
            assert "qualified_name" in node
            assert "sample.php::" in node["qualified_name"]

    def test_edges_have_source_loc(self, result):
        for edge in result.edges:
            assert "source_loc" in edge
            assert edge["source_loc"].startswith("sample.php:")
