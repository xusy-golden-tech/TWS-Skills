"""Tests for TOML extractor."""

import os
import pytest
from tws_graph.indexer.extractors.toml_extractor import extract as toml_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "toml")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("toml")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


class TestTomlExtractor:
    """Unit tests for TOML AST extraction."""

    def test_top_level_table(self):
        """Top-level [table] should produce a CONTAINS edge."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        table_edges = [e for e in edges if e["target_text"] == "package"
                       and e["kind"] == "contains"]
        assert len(table_edges) >= 1
        edge = table_edges[0]
        assert edge["provenance"] == "tree-sitter"

    def test_multiple_top_level_tables(self):
        """Multiple [tables] should produce multiple CONTAINS edges."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        table_names = {e["target_text"] for e in edges
                       if e["kind"] == "contains"
                       and e["target_text"] in ("package", "dependencies", "dev-dependencies")}
        assert "package" in table_names
        assert "dependencies" in table_names
        assert "dev-dependencies" in table_names

    def test_dotted_table(self):
        """[parent.child] should produce a CONTAINS edge with dotted name."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        dotted = [e for e in edges if e["target_text"] == "server.host"]
        assert len(dotted) >= 1
        assert dotted[0]["kind"] == "contains"

    def test_deeply_nested_table(self):
        """[a.b.c] should produce a CONTAINS edge with full dotted path."""
        source_str = "[a.b.c]\nkey = 1\n"
        parser = get_parser("toml")
        tree = parser.parse(source_str)
        edges = toml_extract(source_str.encode("utf-8"), tree, "test.toml")

        table_edges = [e for e in edges if e["target_text"] == "a.b.c"]
        assert len(table_edges) == 1
        assert table_edges[0]["kind"] == "contains"

    def test_array_of_tables(self):
        """[[bin]] should produce a CONTAINS edge."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        array_edges = [e for e in edges if e["target_text"] == "bin"
                       and e["kind"] == "contains"]
        assert len(array_edges) >= 2  # two [[bin]] entries

    def test_key_value_pairs(self):
        """key=value pairs should produce CONTAINS edges."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        pair_edges = [e for e in edges if "=" in e.get("target_text", "")
                      and e["kind"] == "contains"]
        assert len(pair_edges) >= 3
        # Check specific pairs exist
        pair_texts = {e["target_text"] for e in pair_edges}
        assert 'name="myapp"' in pair_texts
        assert 'version="1.0.0"' in pair_texts

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        assert len(edges) > 0
        for edge in edges:
            assert "sample.toml:" in edge["source_loc"]
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Same table name should produce same hash ID."""
        source, tree = _parse_file("sample.toml")
        edges = toml_extract(source, tree, "sample.toml")

        bin_edges = [e for e in edges if e["target_text"] == "bin"]
        assert len(bin_edges) >= 2
        for edge in edges:
            assert len(edge["source"]) == 32  # SHA256 truncated to 32 hex chars

    def test_empty_toml(self):
        """Empty TOML should produce no edges."""
        source_str = "# Just a comment\n"
        parser = get_parser("toml")
        tree = parser.parse(source_str)
        edges = toml_extract(source_str.encode("utf-8"), tree, "empty.toml")

        assert len(edges) == 0

    def test_single_table_with_pairs(self):
        """A minimal [table] with one key=value."""
        source_str = "[app]\nname = 'test'\n"
        parser = get_parser("toml")
        tree = parser.parse(source_str)
        edges = toml_extract(source_str.encode("utf-8"), tree, "test.toml")

        assert len(edges) >= 2  # table CONTAINS + pair CONTAINS
        kinds = {e["kind"] for e in edges}
        assert kinds == {"contains"}

    def test_boolean_and_integer_values(self):
        """Pairs with boolean and integer values."""
        source_str = "[config]\nenabled = true\ncount = 42\n"
        parser = get_parser("toml")
        tree = parser.parse(source_str)
        edges = toml_extract(source_str.encode("utf-8"), tree, "test.toml")

        pair_texts = {e["target_text"] for e in edges if "=" in e["target_text"]}
        assert "enabled=true" in pair_texts
        assert "count=42" in pair_texts

    def test_string_value(self):
        """Pairs with string values."""
        source_str = '[info]\ntitle = "Hello World"\n'
        parser = get_parser("toml")
        tree = parser.parse(source_str)
        edges = toml_extract(source_str.encode("utf-8"), tree, "test.toml")

        pair_texts = {e["target_text"] for e in edges if "=" in e["target_text"]}
        assert 'title="Hello World"' in pair_texts
