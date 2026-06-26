"""Tests for C# extractor."""

import os
import pytest
from tws_graph.indexer.parser import extract_from_source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_cs_src():
    """Return the sample.cs fixture content."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures", "csharp")
    with open(os.path.join(fixtures_dir, "sample.cs"), "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def result(sample_cs_src):
    return extract_from_source("sample.cs", sample_cs_src, "csharp")


# ===========================================================================
# Class extraction
# ===========================================================================

class TestCSharpClassExtraction:
    """Tests for C# class/struct/interface extraction."""

    def test_extracts_classes(self, result):
        classes = [n for n in result.nodes if n["kind"] == "class"]
        names = {n["name"] for n in classes}
        assert "OrderService" in names
        assert "PaymentHandler" in names
        assert "Config" in names

    def test_extracts_interfaces(self, result):
        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        names = {n["name"] for n in interfaces}
        assert "IOrderRepository" in names

    def test_extracts_structs(self, result):
        structs = [n for n in result.nodes if n["kind"] == "struct"]
        names = {n["name"] for n in structs}
        assert "Item" in names

    def test_class_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "class"}
        assert by_name["OrderService"]["visibility"] == "public"
        assert by_name["Config"]["visibility"] == "public"


# ===========================================================================
# Method extraction
# ===========================================================================

class TestCSharpMethodExtraction:
    """Tests for C# method extraction."""

    def test_extracts_methods(self, result):
        methods = [n for n in result.nodes if n["kind"] == "method"]
        names = {n["name"] for n in methods}
        assert "GetHighValueOrders" in names
        assert "GetTotalsByCustomer" in names
        assert "HasOrdersForCustomer" in names
        assert "BuildOrder" in names
        assert "ValidateInput" in names
        assert "Process" in names
        assert "CalculateTotal" in names

    def test_extracts_constructors(self, result):
        ctors = [n for n in result.nodes if n["kind"] == "constructor"]
        assert len(ctors) >= 1

    def test_method_visibility(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "method"}
        assert by_name["GetHighValueOrders"]["visibility"] == "public"
        assert by_name["BuildOrder"]["visibility"] == "private"

    def test_static_method(self, result):
        by_name = {n["name"]: n for n in result.nodes if n["kind"] == "method"}
        validate = by_name.get("ValidateInput")
        assert validate is not None
        # ValidateInput is private static
        assert "static" in (validate.get("decorators", []) or [])


# ===========================================================================
# Namespace extraction
# ===========================================================================

class TestCSharpNamespaceExtraction:
    """Tests for C# namespace extraction."""

    def test_extracts_namespace(self, result):
        namespaces = [n for n in result.nodes if n["kind"] == "namespace"]
        assert any(n["name"] == "SampleApp" for n in namespaces)


# ===========================================================================
# Field extraction
# ===========================================================================

class TestCSharpFieldExtraction:
    """Tests for C# field/property extraction."""

    def test_extracts_fields(self, result):
        fields = [n for n in result.nodes if n["kind"] == "field"]
        names = {n["name"] for n in fields}
        assert "_repo" in names
        assert "_apiUrl" in names or "ApiUrl" in names

    def test_field_visibility(self, result):
        fields = {n["name"]: n for n in result.nodes if n["kind"] == "field"}
        repo = fields.get("_repo")
        if repo:
            assert repo["visibility"] == "private"


# ===========================================================================
# Edge extraction
# ===========================================================================

class TestCSharpEdges:
    """Tests for C# edge extraction."""

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
        assert any("CalculateTotal" in t for t in targets)

    def test_import_target_text(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        targets = {e.get("target_text", "") for e in imports}
        assert any("System" in t for t in targets)


# ===========================================================================
# Edge cases
# ===========================================================================

class TestCSharpEdgeCases:
    """Edge case tests for C# extractor."""

    def test_empty_file(self):
        result = extract_from_source("empty.cs", "", "csharp")
        assert len(result.nodes) == 0

    def test_syntax_error(self):
        result = extract_from_source("bad.cs", "class Broken {", "csharp")
        assert isinstance(result.errors, list)

    def test_nodes_have_proper_ids(self, result):
        for node in result.nodes:
            assert "id" in node
            assert len(node["id"]) > 0
            assert node["language"] == "csharp"

    def test_nodes_have_qualified_names(self, result):
        for node in result.nodes:
            assert "qualified_name" in node
            assert "sample.cs::" in node["qualified_name"]

    def test_edges_have_source_loc(self, result):
        for edge in result.edges:
            assert "source_loc" in edge
            assert edge["source_loc"].startswith("sample.cs:")


# ===========================================================================
# P51: Attribute extraction — decorates edges
# ===========================================================================

class TestCSharpP51Attribute:
    """P51: [JsonPropertyName] attributes -> decorates edges."""

    def test_decorates_edges_exist(self, result):
        decorates = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(decorates) >= 1, "Expected decorates edges from attributes"

    def test_decorates_target_text_has_attribute_name(self, result):
        decorates = [e for e in result.edges if e["kind"] == "decorates"]
        targets = {e.get("target_text", "") for e in decorates}
        assert any("JsonPropertyName" in t for t in targets), \
            "target_text should contain attribute name JsonPropertyName"

    def test_decorates_target_text_has_param(self, result):
        decorates = [e for e in result.edges if e["kind"] == "decorates"]
        targets = {e.get("target_text", "") for e in decorates}
        assert any("order_id" in t for t in targets), \
            "target_text should contain attribute param order_id"


# ===========================================================================
# P51: Generic type extraction — type_ref edges
# ===========================================================================

class TestCSharpP51Generic:
    """P51: GenericRepository<T> / List<T> -> type_ref edges."""

    def test_type_ref_edges_exist(self, result):
        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_refs) >= 1, "Expected type_ref edges from generics"

    def test_type_ref_has_T_param(self, result):
        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        targets = {e.get("target_text", "") for e in type_refs}
        assert any("T" in t for t in targets), \
            "Expected generic param T in type_ref target_text"

    def test_type_ref_has_concrete_type(self, result):
        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        targets = {e.get("target_text", "") for e in type_refs}
        assert any("OrderDto" in t or "OrderItem" in t for t in targets), \
            "Expected concrete type like OrderDto/OrderItem in type_ref"


# ===========================================================================
# P51: Property accessor extraction — reads/writes edges
# ===========================================================================

class TestCSharpP51Property:
    """P51: property accessor get/set -> reads/writes edges."""

    def test_reads_edges_exist(self, result):
        reads = [e for e in result.edges if e["kind"] == "reads"]
        assert len(reads) >= 1, "Expected reads edges from property getters"

    def test_writes_edges_exist(self, result):
        writes = [e for e in result.edges if e["kind"] == "writes"]
        assert len(writes) >= 1, "Expected writes edges from property setters"


# ===========================================================================
# P51: Regression verification
# ===========================================================================

class TestCSharpP51Regression:
    """P51: verify no regressions in previously passing tests."""

    def test_existing_node_kinds_preserved(self, result):
        kinds = {n["kind"] for n in result.nodes}
        for expected in ("class", "method", "interface", "struct",
                         "field", "namespace", "file", "constructor"):
            assert expected in kinds, f"Existing node kind '{expected}' missing"

    def test_existing_edge_kinds_preserved(self, result):
        edge_kinds = {e["kind"] for e in result.edges}
        for expected in ("calls", "imports", "contains"):
            assert expected in edge_kinds, f"Existing edge kind '{expected}' missing"

    def test_node_count_not_decreased(self, result):
        """Regression: we should have at least as many nodes as before."""
        node_count = len(result.nodes)
        assert node_count >= 23, f"Expected >=23 nodes, got {node_count}"

    def test_edge_count_not_decreased(self, result):
        """Regression: we should have at least as many edges as before."""
        edge_count = len(result.edges)
        assert edge_count >= 30, f"Expected >=30 edges, got {edge_count}"
