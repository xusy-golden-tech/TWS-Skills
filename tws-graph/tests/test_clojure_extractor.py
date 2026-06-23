"""Tests for Clojure extractor."""

import os
import pytest
from tws_graph.indexer.extractors.clojure_extractor import clojure_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "clojure")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("clojure")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.clj"):
    parser = get_parser("clojure")
    tree = parser.parse(source_str)
    return clojure_extract(source_str.encode("utf-8"), tree, file_path)


class TestClojureExtractor:
    """Unit tests for Clojure AST extraction."""

    def test_ns_declaration(self):
        """ns should produce CONTAINS edge for the namespace."""
        edges = _parse("(ns myapp.core)")
        ns_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "myapp.core"]
        assert len(ns_edges) == 1

    def test_def_declaration(self):
        """def should produce CONTAINS edge."""
        edges = _parse("(def x 42)")
        def_edges = [e for e in edges if e["kind"] == "contains"
                      and e["target_text"] == "x"]
        assert len(def_edges) == 1

    def test_defn_declaration(self):
        """defn should produce CONTAINS edge."""
        edges = _parse("(defn greet [name] (str \"Hi\" name))")
        defn_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "greet"]
        assert len(defn_edges) == 1

    def test_defmacro_declaration(self):
        """defmacro should produce CONTAINS edge."""
        edges = _parse("(defmacro when-pos [n body] `(when (pos? ~n) ~body))")
        macro_edges = [e for e in edges if e["kind"] == "contains"
                        and e["target_text"] == "when-pos"]
        assert len(macro_edges) == 1

    def test_ns_require_imports(self):
        """require inside ns should produce IMPORTS edges."""
        edges = _parse("(ns myapp (:require [clojure.string :as str]))")
        import_edges = [e for e in edges if e["kind"] == "imports"
                        and e["target_text"] == "clojure.string"]
        assert len(import_edges) >= 1

    def test_ns_import_java(self):
        """import inside ns should produce IMPORTS edges."""
        edges = _parse("(ns myapp (:import [java.util Date]))")
        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) >= 1
        import_targets = {e["target_text"] for e in import_edges}
        assert "java.util" in import_targets

    def test_function_call(self):
        """Function calls should produce CALLS edges."""
        edges = _parse("(println \"Hello\")")
        call_edges = [e for e in edges if e["kind"] == "calls"
                       and e["target_text"] == "println"]
        assert len(call_edges) == 1

    def test_nested_function_call(self):
        """Nested function calls should produce CALLS edges."""
        edges = _parse("(str (upper-case \"hello\"))")
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "str" in call_texts
        assert "upper-case" in call_texts

    def test_full_fixture_file(self):
        """Full fixture file should produce a variety of edge types."""
        source, tree = _parse_file("Sample.clj")
        edges = clojure_extract(source, tree, "Sample.clj")

        assert len(edges) > 0

        kinds = {e["kind"] for e in edges}
        assert "contains" in kinds
        assert "calls" in kinds
        assert "imports" in kinds

        # Check namespace
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "sample.core" in contains_targets
        assert "app-name" in contains_targets
        assert "greet" in contains_targets
        assert "process-data" in contains_targets
        assert "log-and-do" in contains_targets
        assert "-main" in contains_targets

        # Check imports
        import_texts = {e["target_text"] for e in edges if e["kind"] == "imports"}
        assert "clojure.string" in import_texts
        assert "clojure.set" in import_texts
        assert "java.util" in import_texts
        assert "java.io" in import_texts

        # Check calls
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "str" in call_texts
        assert "println" in call_texts

    def test_edge_provenance(self):
        """All edges should have provenance='heuristic'."""
        source, tree = _parse_file("Sample.clj")
        edges = clojure_extract(source, tree, "Sample.clj")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "heuristic"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Same symbol name should produce consistent hash IDs."""
        edges = _parse("(ns test)\n(def x 1)\n(def y 2)")
        for edge in edges:
            assert len(edge["source"]) == 32

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("Sample.clj")
        edges = clojure_extract(source, tree, "Sample.clj")

        for edge in edges:
            assert "Sample.clj:" in edge["source_loc"]

    def test_empty_file(self):
        """Empty file should produce no edges."""
        edges = _parse("; just a comment\n")
        assert len(edges) == 0

    def test_multiple_def_forms(self):
        """Multiple def forms should each produce a CONTAINS edge."""
        edges = _parse("(def x 1)\n(def y 2)")
        def_contains = [e for e in edges if e["kind"] == "contains"]
        targets = {e["target_text"] for e in def_contains}
        assert "x" in targets
        assert "y" in targets
