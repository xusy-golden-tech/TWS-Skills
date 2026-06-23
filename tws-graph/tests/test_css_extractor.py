"""Tests for CSS extractor."""

import os
import pytest
from tree_sitter_language_pack import get_parser
from tws_graph.indexer.extractors.css_extractor import extract as css_extract


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "css")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _parse_and_extract(name: str) -> list[dict]:
    source = _read_fixture(name)
    parser = get_parser("css")
    tree = parser.parse(source)
    return css_extract(source.encode(), tree, name)


class TestCssExtractor:
    """Unit tests for CSS AST extraction."""

    # ---- references edges (selectors) ----

    def test_extracts_class_selector_references(self):
        edges = _parse_and_extract("basic.css")
        refs = [e for e in edges if e["kind"] == "references"]
        target_texts = {e["target_text"] for e in refs}
        assert ".container" in target_texts

    def test_extracts_id_selector_references(self):
        edges = _parse_and_extract("basic.css")
        refs = [e for e in edges if e["kind"] == "references"]
        target_texts = {e["target_text"] for e in refs}
        assert "#header" in target_texts

    def test_extracts_element_selector_references(self):
        edges = _parse_and_extract("basic.css")
        refs = [e for e in edges if e["kind"] == "references"]
        target_texts = {e["target_text"] for e in refs}
        assert any("p" in t for t in target_texts)

    def test_extracts_combinator_selector(self):
        edges = _parse_and_extract("basic.css")
        refs = [e for e in edges if e["kind"] == "references"]
        target_texts = {e["target_text"] for e in refs}
        assert any("div > span" in t for t in target_texts)

    def test_extracts_compound_selector(self):
        edges = _parse_and_extract("basic.css")
        refs = [e for e in edges if e["kind"] == "references"]
        target_texts = {e["target_text"] for e in refs}
        assert any(".class1.class2" in t for t in target_texts)

    # ---- imports edges (@import) ----

    def test_extracts_import_statements(self):
        edges = _parse_and_extract("imports.css")
        imports = [e for e in edges if e["kind"] == "imports"]
        assert len(imports) >= 2
        urls = {e["target_text"] for e in imports}
        assert "reset.css" in urls
        assert "theme.css" in urls

    # ---- contains edges (CSS variables) ----

    def test_extracts_variable_contains(self):
        edges = _parse_and_extract("variables.css")
        contains = [e for e in edges if e["kind"] == "contains"]
        target_texts = {e["target_text"] for e in contains}
        assert "--main-color" in target_texts
        assert "--bg-color" in target_texts
        assert "--font-size" in target_texts
        assert "--spacing" in target_texts

    # ---- contains edges (@keyframes) ----

    def test_extracts_keyframes_contains(self):
        edges = _parse_and_extract("keyframes.css")
        contains = [e for e in edges if e["kind"] == "contains"]
        target_texts = {e["target_text"] for e in contains}
        assert "fadeIn" in target_texts
        assert "slideUp" in target_texts

    # ---- contains edges (@media) ----

    def test_extracts_media_contains(self):
        edges = _parse_and_extract("media.css")
        contains = [e for e in edges if e["kind"] == "contains"]
        target_texts = {e["target_text"] for e in contains}
        assert any("max-width: 600px" in t for t in target_texts)
        assert any("min-width" in t for t in target_texts)
        assert any("print" in t for t in target_texts)

    # ---- edge cases ----

    def test_empty_file(self):
        edges = _parse_and_extract("empty.css")
        assert isinstance(edges, list)
        assert len(edges) == 0

    def test_all_edges_have_required_fields(self):
        edges = _parse_and_extract("basic.css")
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert edge["target"] == ""
            assert "kind" in edge
            assert "target_text" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["provenance"] == "tree-sitter"
            assert len(edge["source"]) == 32

    def test_all_edges_valid_kinds(self):
        edges = _parse_and_extract("variables.css")
        for edge in edges:
            assert edge["kind"] in ("contains", "imports", "references")
