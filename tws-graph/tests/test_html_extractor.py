"""Tests for HTML extractor."""

import os
import pytest
from tree_sitter_language_pack import get_parser
from tws_graph.indexer.extractors.html_extractor import extract as html_extract


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "html")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _parse_and_extract(name: str) -> "tuple[list[dict], list[dict]]":
    source = _read_fixture(name)
    parser = get_parser("html")
    tree = parser.parse(source)
    return html_extract(source.encode(), tree, name)


class TestHtmlExtractor:
    """Unit tests for HTML AST extraction."""

    # ---- contains edges (id attributes) ----

    def test_extracts_id_contains_edges(self):
        nodes, edges = _parse_and_extract("basic.html")
        contains = [e for e in edges if e["kind"] == "contains"]
        assert len(contains) >= 1
        target_texts = {e["target_text"] for e in contains}
        assert any("main" in t for t in target_texts)

    def test_multiple_id_elements(self):
        nodes, edges = _parse_and_extract("multiple_ids.html")
        contains = [e for e in edges if e["kind"] == "contains"]
        # header, nav, content, section1, section2, footer
        assert len(contains) >= 6
        target_texts = {e["target_text"] for e in contains}
        assert any("header" in t for t in target_texts)
        assert any("nav" in t for t in target_texts)
        assert any("content" in t for t in target_texts)
        assert any("section1" in t for t in target_texts)
        assert any("section2" in t for t in target_texts)
        assert any("footer" in t for t in target_texts)

    def test_no_contains_for_elements_without_id(self):
        nodes, edges = _parse_and_extract("no_id_elements.html")
        contains = [e for e in edges if e["kind"] == "contains"]
        # No elements have id attributes
        assert len(contains) == 0

    # ---- imports edges (script src, link href) ----

    def test_extracts_script_imports(self):
        nodes, edges = _parse_and_extract("basic.html")
        imports = [e for e in edges if e["kind"] == "imports"]
        assert any("app.js" in e["target_text"] for e in imports)

    def test_extracts_link_imports(self):
        nodes, edges = _parse_and_extract("basic.html")
        imports = [e for e in edges if e["kind"] == "imports"]
        assert any("style.css" in e["target_text"] for e in imports)

    def test_multiple_imports(self):
        nodes, edges = _parse_and_extract("imports.html")
        imports = [e for e in edges if e["kind"] == "imports"]
        assert len(imports) >= 4
        urls = {e["target_text"] for e in imports}
        assert "reset.css" in urls
        assert "theme.css" in urls
        assert "jquery.js" in urls
        assert "app.bundle.js" in urls

    # ---- references edges (a href, form action) ----

    def test_extracts_anchor_references(self):
        nodes, edges = _parse_and_extract("forms_and_links.html")
        refs = [e for e in edges if e["kind"] == "references"]
        anchor_urls = {e["target_text"] for e in refs}
        assert "https://external.com" in anchor_urls
        assert "/internal/page" in anchor_urls
        assert "#section" in anchor_urls

    def test_extracts_form_action_references(self):
        nodes, edges = _parse_and_extract("forms_and_links.html")
        refs = [e for e in edges if e["kind"] == "references"]
        form_urls = {e["target_text"] for e in refs}
        assert "/api/login" in form_urls
        assert "/api/register" in form_urls

    def test_all_edges_have_provenance(self):
        nodes, edges = _parse_and_extract("basic.html")
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"

    def test_all_edges_have_source_loc(self):
        nodes, edges = _parse_and_extract("basic.html")
        for edge in edges:
            assert "source_loc" in edge
            assert ":" in edge["source_loc"]

    # ---- edge cases ----

    def test_empty_file(self):
        nodes, edges = _parse_and_extract("empty.html")
        # Empty file should not crash; it has some html/head/body elements but no id/import/ref
        assert isinstance(edges, list)

    def test_basic_edges_structure(self):
        nodes, edges = _parse_and_extract("basic.html")
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
            assert "target_text" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["kind"] in ("contains", "imports", "references")
            assert len(edge["source"]) == 32  # hash_id produces 32-char hex

    def test_anchor_mailto(self):
        nodes, edges = _parse_and_extract("forms_and_links.html")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "mailto:test@example.com" in urls
