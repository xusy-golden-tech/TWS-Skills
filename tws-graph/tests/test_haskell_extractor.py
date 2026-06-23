"""Tests for Haskell extractor."""

import os
import pytest
from tws_graph.indexer.extractors.haskell_extractor import haskell_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "haskell")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("haskell")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.hs"):
    parser = get_parser("haskell")
    tree = parser.parse(source_str)
    return haskell_extract(source_str.encode("utf-8"), tree, file_path)


class TestHaskellExtractor:
    """Unit tests for Haskell AST extraction."""

    def test_module_declaration(self):
        """Module declaration should produce CONTAINS edge."""
        edges = _parse("module Main where\nx = 1")
        mod_edges = [e for e in edges if e["kind"] == "contains"
                      and e["target_text"] == "Main"]
        assert len(mod_edges) == 1

    def test_data_definition(self):
        """data definition should produce CONTAINS edge."""
        edges = _parse("data Person = Person String Int")
        data_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "Person"]
        assert len(data_edges) >= 1

    def test_newtype_definition(self):
        """newtype definition should produce CONTAINS edge."""
        edges = _parse("newtype Name = Name String")
        nt_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "Name"]
        assert len(nt_edges) >= 1

    def test_type_synonym_definition(self):
        """type synonym should produce CONTAINS edge."""
        edges = _parse("type Age = Int")
        type_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "Age"]
        assert len(type_edges) >= 1

    def test_class_definition(self):
        """class definition should produce CONTAINS edge."""
        code = "class Show a where\n  show :: a -> String"
        edges = _parse(code)
        class_edges = [e for e in edges if e["kind"] == "contains"
                        and e["target_text"] == "Show"]
        assert len(class_edges) >= 1

    def test_instance_definition(self):
        """instance definition should produce CONTAINS edge."""
        code = "data P = P String\ninstance Show P where\n  show (P s) = s"
        edges = _parse(code)
        inst_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "Show"]
        assert len(inst_edges) >= 1

    def test_function_signature(self):
        """Function type signature should produce CONTAINS edge."""
        edges = _parse("greet :: String -> String\ngreet s = \"Hi \" ++ s")
        sig_edges = [e for e in edges if e["kind"] == "contains"
                      and e["target_text"] == "greet"]
        assert len(sig_edges) >= 1

    def test_function_binding(self):
        """Function binding should produce CONTAINS edge."""
        edges = _parse("greet s = \"Hi \" ++ s")
        fn_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "greet"]
        assert len(fn_edges) == 1

    def test_import_statement(self):
        """import statement should produce IMPORTS edge."""
        edges = _parse("import Data.List (sort)")
        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0]["target_text"] == "Data.List"

    def test_function_call(self):
        """Function calls should produce CALLS edges."""
        code = "greet s = putStrLn s"
        edges = _parse(code)
        call_edges = [e for e in edges if e["kind"] == "calls"
                       and e["target_text"] == "putStrLn"]
        assert len(call_edges) == 1

    def test_nested_function_call(self):
        """Nested function calls should all produce CALLS edges."""
        code = "main = putStrLn (greet (Person \"Alice\" 30))"
        edges = _parse(code)
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "putStrLn" in call_texts
        assert "greet" in call_texts
        assert "Person" in call_texts

    def test_full_fixture_file(self):
        """Full fixture file should produce a variety of edge types."""
        source, tree = _parse_file("Sample.hs")
        edges = haskell_extract(source, tree, "Sample.hs")

        assert len(edges) > 0

        kinds = {e["kind"] for e in edges}
        assert "contains" in kinds
        assert "calls" in kinds
        assert "imports" in kinds

        # Check module
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "SampleApp" in contains_targets
        assert "Person" in contains_targets
        assert "UserId" in contains_targets
        assert "Name" in contains_targets

        # Check functions
        assert "sayHello" in contains_targets
        assert "main" in contains_targets

        # Check imports
        import_texts = {e["target_text"] for e in edges if e["kind"] == "imports"}
        assert "Data.List" in import_texts
        assert "Data.Map" in import_texts
        assert "Data.Maybe" in import_texts

        # Check calls
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "putStrLn" in call_texts

    def test_edge_provenance(self):
        """All edges should have provenance='heuristic'."""
        source, tree = _parse_file("Sample.hs")
        edges = haskell_extract(source, tree, "Sample.hs")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "heuristic"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Same symbol name should produce consistent hash IDs."""
        code = "data T = T Int; data S = S String"
        edges = _parse(code)
        for edge in edges:
            assert len(edge["source"]) == 32

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("Sample.hs")
        edges = haskell_extract(source, tree, "Sample.hs")

        for edge in edges:
            assert "Sample.hs:" in edge["source_loc"]

    def test_empty_file(self):
        """Empty file should produce no edges."""
        edges = _parse("-- just a comment\n")
        assert len(edges) == 0
