"""Tests for Ruby extractor."""

import os
import pytest
from tws_graph.indexer.parser import extract_from_source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_rb_src():
    """Return the sample.rb fixture content."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures", "ruby")
    with open(os.path.join(fixtures_dir, "sample.rb"), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def result(sample_rb_src):
    return extract_from_source("sample.rb", sample_rb_src, "ruby")


# ===========================================================================
# Class/module extraction
# ===========================================================================

class TestRubyTypeExtraction:
    """Tests for Ruby class/module extraction."""

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "OrderRepository" in names
        assert "Item" in names
        assert "OrderService" in names
        assert "PaymentHandler" in names

    def test_extracts_modules(self, result):
        modules = [n for n in result.nodes if n["kind"] == "module"]
        names = {n["name"] for n in modules}
        assert "SampleApp" in names


# ===========================================================================
# Method extraction
# ===========================================================================

class TestRubyMethodExtraction:
    """Tests for Ruby method extraction."""

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "find_by_id" in names
        assert "save" in names
        assert "initialize" in names
        assert "create_order" in names
        assert "build_order" in names
        assert "validate_input" in names
        assert "process" in names
        assert "calculate_total" in names

    def test_class_method(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        validate = [n for n in methods if n["name"] == "validate_input"]
        assert len(validate) >= 1

    def test_top_level_method(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "top_level_helper" in names


# ===========================================================================
# Attribute extraction (attr_accessor/reader/writer)
# ===========================================================================

class TestRubyAttributeExtraction:
    """Tests for Ruby attr_* extraction."""

    def test_extracts_attributes(self, result):
        attrs = [n for n in result.nodes if n["kind"] == "attribute"]
        names = {n["name"] for n in attrs}
        assert "items" in names
        assert "name" in names
        assert "logger" in names


# ===========================================================================
# Constant extraction
# ===========================================================================

class TestRubyConstantExtraction:
    """Tests for Ruby constant extraction."""

    def test_extracts_constants(self, result):
        consts = [n for n in result.nodes if n["kind"] == "constant"]
        names = {n["name"] for n in consts}
        assert "DEFAULT_TIMEOUT" in names
        assert "API_URL" in names


# ===========================================================================
# Edge extraction
# ===========================================================================

class TestRubyEdges:
    """Tests for Ruby edge extraction."""

    def test_contains_edges(self, result):
        contains = [e for e in result.edges if e["kind"] == "contains"]
        assert len(contains) > 0

    def test_call_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        assert len(calls) > 0

    def test_import_edges(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        assert len(imports) > 0

    def test_extends_edges(self, result):
        extends = [e for e in result.edges if e["kind"] in ("extends", "implements")]
        # Enumerable and Forwardable are Ruby stdlib — skipped for dangling prevention.
        # The fixture no longer produces extends edges.
        assert len(extends) == 0

    def test_call_target_text(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e.get("target_text", "") for e in calls}
        assert any("create_order" in t for t in targets)


# ===========================================================================
# Edge cases
# ===========================================================================

class TestRubyEdgeCases:
    """Edge case tests for Ruby extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.rb", "", "ruby")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.rb", "class Broken {", "ruby")
        assert isinstance(result.errors, list)

    def test_same_file_extends_produces_edge(self):
        """Module defined in same file that is extended should produce extends edge."""
        code = (
            "module Helper\n  def help; end\nend\n\n"
            "class Service\n  extend Helper\nend\n"
        )
        result = extract_from_source("test.rb", code, "ruby")
        extends = [e for e in result.edges if e["kind"] == "extends"]
        assert len(extends) == 1, f"Same-file extend should produce 1 edge, got {len(extends)}"
        assert extends[0]["target"] != "", "Same-file target should be non-empty"
        assert "Helper" in extends[0].get("target_text", "")

    def test_same_file_include_produces_implements_edge(self):
        """Module defined in same file that is included should produce implements edge."""
        code = (
            "module Mixin\n  def mix; end\nend\n\n"
            "class Worker\n  include Mixin\nend\n"
        )
        result = extract_from_source("test.rb", code, "ruby")
        implements = [e for e in result.edges if e["kind"] == "implements"]
        assert len(implements) == 1, f"Same-file include should produce 1 implements edge, got {len(implements)}"
        assert implements[0]["target"] != "", "Same-file target should be non-empty"
        assert "Mixin" in implements[0].get("target_text", "")

    def test_nodes_have_proper_ids(self, result):
        for node in result.nodes:
            assert "id" in node
            assert len(node["id"]) > 0
            assert node["language"] == "ruby"

    def test_nodes_have_qualified_names(self, result):
        for node in result.nodes:
            assert "qualified_name" in node
            assert "sample.rb::" in node["qualified_name"]

    def test_edges_have_source_loc(self, result):
        for edge in result.edges:
            assert "source_loc" in edge
            assert edge["source_loc"].startswith("sample.rb:")

    def test_frozen_string_literal_comment_does_not_break(self, result):
        """Ensure the frozen_string_literal magic comment does not break parsing."""
        # If we got here with nodes, parsing succeeded
        assert len(result.nodes) > 0
