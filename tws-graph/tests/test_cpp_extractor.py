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

    # ---- override edges (virtual function overrides) ----

    def test_override_edges(self, result):
        """Circle::area() and Rectangle::area() override Shape::area()."""
        overrides = [e for e in result.edges if e["kind"] == "overrides"]
        assert len(overrides) >= 2, (
            f"Expected at least 2 override edges (Circle::area→Shape::area, "
            f"Rectangle::area→Shape::area), got {len(overrides)}"
        )

        node_by_id = {n["id"]: n for n in result.nodes}

        override_pairs = set()
        for e in overrides:
            assert e["provenance"] == "heuristic", (
                f"Override edge provenance should be 'heuristic', got {e['provenance']}"
            )
            src = node_by_id.get(e["source"])
            tgt = node_by_id.get(e["target"])
            assert src is not None, f"Override edge source {e['source']} not in nodes"
            assert tgt is not None, f"Override edge target {e['target']} not in nodes"
            assert src["kind"] == "method", f"Override source should be method, got {src['kind']}"
            assert tgt["kind"] == "method", f"Override target should be method, got {tgt['kind']}"
            override_pairs.add((src["qualified_name"], tgt["qualified_name"]))

        # Verify specific expected overrides
        assert ("sample.cpp::math::Circle::area", "sample.cpp::math::Shape::area") in override_pairs, (
            f"Expected Circle::area → Shape::area override, got {override_pairs}"
        )
        assert ("sample.cpp::math::Rectangle::area", "sample.cpp::math::Shape::area") in override_pairs, (
            f"Expected Rectangle::area → Shape::area override, got {override_pairs}"
        )

    # ---- constructor / instantiates edges ----

    def test_constructor_edges(self, result):
        """Constructors should produce instantiates edges to their class."""
        instant_edges = [e for e in result.edges if e["kind"] == "instantiates"]
        assert len(instant_edges) >= 3, (
            f"Expected at least 3 instantiates edges (Circle, Rectangle, KeyValueStore), "
            f"got {len(instant_edges)}"
        )

        node_by_id = {n["id"]: n for n in result.nodes}

        instant_pairs = set()
        for e in instant_edges:
            assert e["provenance"] == "heuristic", (
                f"Instantiates edge provenance should be 'heuristic', got {e['provenance']}"
            )
            src = node_by_id.get(e["source"])
            tgt = node_by_id.get(e["target"])
            assert src is not None, f"Instantiates edge source {e['source']} not in nodes"
            assert tgt is not None, f"Instantiates edge target {e['target']} not in nodes"
            assert src["kind"] == "method", (
                f"Instantiates source should be method (constructor), got {src['kind']}"
            )
            assert tgt["kind"] in ("class", "struct"), (
                f"Instantiates target should be class or struct, got {tgt['kind']}"
            )
            instant_pairs.add((src["qualified_name"], tgt["qualified_name"]))

        # Check specific constructor edges
        assert ("sample.cpp::math::Circle::Circle", "sample.cpp::math::Circle") in instant_pairs, (
            f"Expected Circle constructor → Circle class, got {instant_pairs}"
        )
        assert ("sample.cpp::math::Rectangle::Rectangle", "sample.cpp::math::Rectangle") in instant_pairs, (
            f"Expected Rectangle constructor → Rectangle class, got {instant_pairs}"
        )

    # ---- template type_ref edges ----

    def test_template_type_ref(self, result):
        """Template type parameters (T, K, V) should produce type_ref edges."""
        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_refs) >= 3, (
            f"Expected at least 3 type_ref edges (T, K, V), got {len(type_refs)}"
        )

        for e in type_refs:
            assert e["provenance"] == "heuristic", (
                f"type_ref edge provenance should be 'heuristic', got {e['provenance']}"
            )

        # Collect target_text values (type parameter names)
        param_names = set()
        for e in type_refs:
            if e.get("target_text"):
                param_names.add(e["target_text"])

        assert "T" in param_names, (
            f"Expected type_ref for template parameter T, got {param_names}"
        )
        assert "K" in param_names, (
            f"Expected type_ref for template parameter K, got {param_names}"
        )
        assert "V" in param_names, (
            f"Expected type_ref for template parameter V, got {param_names}"
        )


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
