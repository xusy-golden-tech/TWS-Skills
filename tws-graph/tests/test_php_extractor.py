"""Tests for PHP extractor."""

import hashlib
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


# ===========================================================================
# P51: Deep imports — alias handling
# ===========================================================================

class TestPhpP51Use:
    """Tests for PHP use statement imports edge depth (P51)."""

    def test_imports_edge_with_alias(self, result):
        """use statement with 'as' alias should produce imports edge with target_text."""
        imports = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = {e.get("target_text", "") for e in imports}
        # The fixture has use System\Collections\Generic\List as GenericList
        assert any("System\\Collections\\Generic\\List" in t for t in target_texts), \
            f"Expected imports edge for List alias, got: {target_texts}"

    def test_imports_edge_count_increased(self, result):
        """Adding new use statements should increase imports edges."""
        imports = [e for e in result.edges if e["kind"] == "imports"]
        # At minimum we have 2 use statements + require/include
        assert len(imports) >= 3, \
            f"Expected at least 3 imports edges, got {len(imports)}"


# ===========================================================================
# P51: Trait usage — implements edges
# ===========================================================================

class TestPhpP51Trait:
    """Tests for PHP trait usage producing implements edges (P51)."""

    def test_trait_usage_produces_implements_edge(self, result):
        """use CacheableTrait / use LoggerTrait inside class should produce implements edge."""
        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        assert len(impl_edges) >= 2, \
            f"Expected >=2 implements edges (LoggerTrait + CacheableTrait), got {len(impl_edges)}"

        target_texts = {e.get("target_text", "") for e in impl_edges}
        assert "LoggerTrait" in target_texts, \
            f"'LoggerTrait' not found in implements target_texts: {target_texts}"
        assert "CacheableTrait" in target_texts, \
            f"'CacheableTrait' not found in implements target_texts: {target_texts}"

    def test_trait_implements_source_is_class(self, result):
        """The source of an implements edge should be the class using the trait."""
        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        class_nodes = {n["name"]: n["id"] for n in result.nodes if n["kind"] == "class"}
        trait_nodes = {n["name"]: n["id"] for n in result.nodes if n["kind"] == "trait"}

        assert "PaymentHandler" in class_nodes, "PaymentHandler class not found"
        assert "CacheableTrait" in trait_nodes, "CacheableTrait node not found"

        # PaymentHandler uses LoggerTrait
        payment_id = class_nodes["PaymentHandler"]
        logger_id = trait_nodes["LoggerTrait"]
        found = any(
            e["source"] == payment_id and e["target"] == logger_id
            for e in impl_edges
        )
        assert found, f"PaymentHandler should implement LoggerTrait. edges={impl_edges}"

        # CachedOrderService uses CacheableTrait
        if "CachedOrderService" in class_nodes:
            cached_id = class_nodes["CachedOrderService"]
            cache_trait_id = trait_nodes["CacheableTrait"]
            found = any(
                e["source"] == cached_id and e["target"] == cache_trait_id
                for e in impl_edges
            )
            assert found, f"CachedOrderService should implement CacheableTrait"


# ===========================================================================
# P51: PHP 8 Attributes — decorates edges
# ===========================================================================

class TestPhpP51Annotation:
    """Tests for PHP 8 attribute annotations producing decorates edges (P51)."""

    def test_attribute_produces_decorates_edge(self, result):
        """#[Route(...)] on methods should produce decorates edges."""
        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) >= 3, \
            f"Expected >=3 decorates edges (1 class + 2 methods), got {len(dec_edges)}"

        target_texts = {e.get("target_text", "") for e in dec_edges}
        assert any("Route" in t for t in target_texts), \
            f"'Route' not found in decorates target_texts: {target_texts}"
        assert any("Attribute" in t for t in target_texts), \
            f"'Attribute' not found in decorates target_texts: {target_texts}"

    def test_decorates_source_is_decorated_node(self, result):
        """The source of a decorates edge should be the decorated method/class."""
        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        method_nodes = {n["name"]: n["id"] for n in result.nodes if n["kind"] == "method"}
        class_nodes = {n["name"]: n["id"] for n in result.nodes if n["kind"] == "class"}

        # The index method should be decorated by Route
        if "index" in method_nodes:
            index_id = method_nodes["index"]
            found = any(
                e["source"] == index_id and "Route" in e.get("target_text", "")
                for e in dec_edges
            )
            assert found, f"index() should have a Route decorates edge"

        # The store method should be decorated by Route
        if "store" in method_nodes:
            store_id = method_nodes["store"]
            found = any(
                e["source"] == store_id and "Route" in e.get("target_text", "")
                for e in dec_edges
            )
            assert found, f"store() should have a Route decorates edge"

        # The Route class itself has #[Attribute]
        if "Route" in class_nodes:
            route_id = class_nodes["Route"]
            found = any(
                e["source"] == route_id and "Attribute" in e.get("target_text", "")
                for e in dec_edges
            )
            assert found, f"Route class should have an Attribute decorates edge"


# ===========================================================================
# P51: Type hints — type_ref edges
# ===========================================================================

class TestPhpP51TypeHint:
    """Tests for PHP type hints producing type_ref edges (P51)."""

    def test_type_hint_produces_type_ref_edge(self, result):
        """Type hints like UserRequest, UserResponse, Cache should produce type_ref edges."""
        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_refs) > 0, \
            f"Expected type_ref edges for PHP type hints, got 0"

        target_texts = {e.get("target_text", "") for e in type_refs}
        assert any("UserRequest" in t for t in target_texts), \
            f"'UserRequest' not found in type_refs: {target_texts}"
        assert any("UserResponse" in t for t in target_texts), \
            f"'UserResponse' not found in type_refs: {target_texts}"
        assert any("Cache" in t for t in target_texts), \
            f"'Cache' not found in type_refs: {target_texts}"
        assert any("OrderRepositoryInterface" in t for t in target_texts), \
            f"'OrderRepositoryInterface' not found in type_refs: {target_texts}"


# ===========================================================================
# P51: Regression — ensure existing tests still pass
# ===========================================================================

class TestPhpP51Regression:
    """Regression tests to ensure P51 changes don't break existing functionality."""

    def test_all_original_15_tests_still_coherent(self, result):
        """Sanity check: existing node types still extracted correctly."""
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        # Original classes must still be there
        for cls_name in ["OrderService", "PaymentHandler"]:
            assert cls_name in names, f"Original class {cls_name} missing after P51"

        # Original interfaces
        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        iface_names = {n["name"] for n in interfaces}
        assert "OrderRepositoryInterface" in iface_names

        # Original traits
        traits = [n for n in result.nodes if n["kind"] == "trait"]
        trait_names = {n["name"] for n in traits}
        for t in ["LoggerTrait", "CacheableTrait"]:
            assert t in trait_names, f"Trait {t} missing after P51"

    def test_original_edge_types_still_present(self, result):
        """contains, calls, imports edges must still exist."""
        edge_kinds = {e["kind"] for e in result.edges}
        for kind in ["contains", "calls", "imports"]:
            assert kind in edge_kinds, f"Edge kind '{kind}' missing after P51"

    def test_new_nodes_still_have_proper_ids(self, result):
        """All nodes (including new ones) must have proper IDs and structure."""
        new_class_names = {"Route", "UserController", "CachedOrderService",
                          "UserRequest", "UserResponse", "Cache", "Repository"}
        for node in result.nodes:
            if node["name"] in new_class_names and node["kind"] in ("class", "trait"):
                assert "id" in node
                assert len(node["id"]) > 0
                assert node["language"] == "php"
