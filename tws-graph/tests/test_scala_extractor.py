"""Tests for Scala extractor."""

import os
import pytest
from tws_graph.indexer.extractors.scala_extractor import scala_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "scala")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("scala")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.scala"):
    parser = get_parser("scala")
    tree = parser.parse(source_str)
    return scala_extract(source_str.encode("utf-8"), tree, file_path)


class TestScalaExtractor:
    """Unit tests for Scala AST extraction."""

    def test_object_definition(self):
        """Object definitions should produce CONTAINS edges."""
        edges = _parse("object Hello { }")
        obj_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "Hello"]
        assert len(obj_edges) == 1
        assert obj_edges[0]["source_loc"] == "test.scala:1"

    def test_class_definition(self):
        """Class definitions should produce CONTAINS edges."""
        edges = _parse("class Foo(x: Int) { def bar() = 42 }")
        class_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "Foo"]
        assert len(class_edges) == 1

    def test_trait_definition(self):
        """Trait definitions should produce CONTAINS edges."""
        edges = _parse("trait Baz { def qux: String }")
        trait_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "Baz"]
        assert len(trait_edges) == 1

    def test_def_method_definition(self):
        """Def method definitions should produce CONTAINS edges."""
        edges = _parse("object A { def foo(x: Int): Int = x + 1 }")
        def_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "foo"]
        assert len(def_edges) == 1

    def test_val_definition(self):
        """Val definitions should produce CONTAINS edges."""
        edges = _parse("object A { val x: Int = 42 }")
        val_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "x"]
        assert len(val_edges) == 1

    def test_var_definition(self):
        """Var definitions should produce CONTAINS edges."""
        edges = _parse("object A { var y = 0 }")
        var_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "y"]
        assert len(var_edges) == 1

    def test_function_call(self):
        """Function calls should produce CALLS edges."""
        edges = _parse("object A { def f() = println(\"hi\") }")
        call_edges = [e for e in edges if e["kind"] == "calls"
                      and e["target_text"] == "println"]
        assert len(call_edges) == 1

    def test_import_statement(self):
        """Import statements should produce IMPORTS edges."""
        edges = _parse("import scala.collection.mutable.ListBuffer")
        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0]["target_text"] == "scala.collection.mutable.ListBuffer"

    def test_package_declaration(self):
        """Package declarations should produce CONTAINS edges."""
        edges = _parse("package com.example.util")
        pkg_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "com.example.util"]
        assert len(pkg_edges) == 1

    def test_full_fixture_file(self):
        """Full fixture file should produce a variety of edge types."""
        source, tree = _parse_file("Sample.scala")
        edges = scala_extract(source, tree, "Sample.scala")

        assert len(edges) > 0

        kinds = {e["kind"] for e in edges}
        assert "contains" in kinds
        assert "calls" in kinds
        assert "imports" in kinds

        # Check object definitions
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "HelloWorld" in contains_targets
        assert "Greeter" in contains_targets
        assert "Loggable" in contains_targets
        assert "Logger" in contains_targets

        # Check method definitions
        assert "main" in contains_targets
        assert "greet" in contains_targets
        assert "log" in contains_targets

        # Check val/var
        assert "defaultLevel" in contains_targets
        assert "counter" in contains_targets

        # Check imports
        import_texts = {e["target_text"] for e in edges if e["kind"] == "imports"}
        assert any("scala.collection.mutable.ListBuffer" in t for t in import_texts)
        assert any("scala.util" in t for t in import_texts)

        # Check package
        assert "com.example.myapp" in contains_targets

        # Check calls exist
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "println" in call_texts

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("Sample.scala")
        edges = scala_extract(source, tree, "Sample.scala")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Same symbol name should produce consistent hash IDs."""
        edges = _parse("object A { def f() = 1; def f(x: Int) = 2 }")
        f_edges = [e for e in edges if e["target_text"] == "f"
                   and e["kind"] == "contains"]
        assert len(f_edges) == 2
        for edge in f_edges:
            assert len(edge["source"]) == 32

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("Sample.scala")
        edges = scala_extract(source, tree, "Sample.scala")

        for edge in edges:
            assert "Sample.scala:" in edge["source_loc"]

    def test_empty_file(self):
        """Empty file should produce no edges."""
        edges = _parse("// just a comment\n")
        assert len(edges) == 0
