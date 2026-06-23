"""Tests for Elixir extractor."""

import os
import pytest
from tws_graph.indexer.extractors.elixir_extractor import extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "elixir")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("elixir")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.ex"):
    parser = get_parser("elixir")
    tree = parser.parse(source_str)
    return extract(source_str.encode("utf-8"), tree, file_path)


class TestElixirExtractor:
    """Unit tests for Elixir AST extraction."""

    def test_defmodule(self):
        """defmodule should produce CONTAINS edge."""
        edges = _parse("defmodule MyApp do end")
        mod_edges = [e for e in edges if e["kind"] == "contains"
                      and e["target_text"] == "MyApp"]
        assert len(mod_edges) == 1

    def test_def_function(self):
        """def should produce CONTAINS edge."""
        edges = _parse("defmodule M do def greet do end end")
        def_edges = [e for e in edges if e["kind"] == "contains"
                      and e["target_text"] == "greet"]
        assert len(def_edges) == 1

    def test_defp_private_function(self):
        """defp should produce CONTAINS edge."""
        edges = _parse("defmodule M do defp format(x) do end end")
        defp_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"] == "format"]
        assert len(defp_edges) == 1

    def test_import_statement(self):
        """import should produce IMPORTS edge."""
        edges = _parse("import Logger")
        import_edges = [e for e in edges if e["kind"] == "imports"
                        and e["target_text"] == "Logger"]
        assert len(import_edges) == 1

    def test_alias_statement(self):
        """alias should produce IMPORTS edge."""
        edges = _parse("alias MyApp.Helper")
        alias_edges = [e for e in edges if e["kind"] == "imports"
                        and e["target_text"] == "MyApp.Helper"]
        assert len(alias_edges) == 1

    def test_use_macro(self):
        """use should produce IMPORTS edge."""
        edges = _parse("use GenServer")
        use_edges = [e for e in edges if e["kind"] == "imports"
                      and e["target_text"] == "GenServer"]
        assert len(use_edges) == 1

    def test_require_statement(self):
        """require should produce IMPORTS edge."""
        edges = _parse("require Integer")
        req_edges = [e for e in edges if e["kind"] == "imports"
                      and e["target_text"] == "Integer"]
        assert len(req_edges) == 1

    def test_dot_function_call(self):
        """Module.function calls should produce CALLS edges."""
        edges = _parse("defmodule M do def f do Logger.info(\"hi\") end end")
        call_edges = [e for e in edges if e["kind"] == "calls"
                      and e["target_text"] == "Logger.info"]
        assert len(call_edges) == 1

    def test_local_function_call(self):
        """Local function calls should produce CALLS edges."""
        edges = _parse("defmodule M do def f do local_fn() end end")
        call_edges = [e for e in edges if e["kind"] == "calls"
                      and e["target_text"] == "local_fn"]
        assert len(call_edges) == 1

    def test_no_false_calls_for_def_name(self):
        """def function name should NOT generate a CALLS edge."""
        edges = _parse("defmodule M do def greet do :ok end end")
        call_edges = [e for e in edges if e["kind"] == "calls"
                      and e["target_text"] == "greet"]
        assert len(call_edges) == 0

    def test_full_fixture_file(self):
        """Full fixture file should produce a variety of edge types."""
        source, tree = _parse_file("Sample.ex")
        edges = extract(source, tree, "Sample.ex")

        assert len(edges) > 0

        kinds = {e["kind"] for e in edges}
        assert "contains" in kinds
        assert "calls" in kinds
        assert "imports" in kinds

        # Check module
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "SampleApp" in contains_targets
        assert "start_link" in contains_targets
        assert "handle_call" in contains_targets
        assert "handle_cast" in contains_targets
        assert "validate" in contains_targets

        # Check imports
        import_texts = {e["target_text"] for e in edges if e["kind"] == "imports"}
        assert "Logger" in import_texts
        assert "SampleApp.Helper" in import_texts
        assert "GenServer" in import_texts
        assert "Integer" in import_texts

        # Check calls
        call_texts = {e["target_text"] for e in edges if e["kind"] == "calls"}
        assert "Logger.info" in call_texts
        assert "String.valid?" in call_texts

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("Sample.ex")
        edges = extract(source, tree, "Sample.ex")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Same symbol name should produce consistent hash IDs."""
        edges = _parse("defmodule M do def f do :a end; def f(x) do :b end end")
        f_edges = [e for e in edges if e["target_text"] == "f"
                   and e["kind"] == "contains"]
        assert len(f_edges) == 2
        for edge in f_edges:
            assert len(edge["source"]) == 32

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("Sample.ex")
        edges = extract(source, tree, "Sample.ex")

        for edge in edges:
            assert "Sample.ex:" in edge["source_loc"]

    def test_empty_file(self):
        """Empty file should produce no edges."""
        edges = _parse("# just a comment\n")
        assert len(edges) == 0
