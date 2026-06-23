"""Tests for Markdown extractor."""

import os
import pytest
from tree_sitter_language_pack import get_parser
from tws_graph.indexer.extractors.markdown_extractor import extract as md_extract


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "markdown")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _parse_and_extract(name: str) -> "tuple[list[dict], list[dict]]":
    source = _read_fixture(name)
    parser = get_parser("markdown")
    tree = parser.parse(source)
    return md_extract(source.encode(), tree, name)


class TestMarkdownExtractor:
    """Unit tests for Markdown AST extraction."""

    # ---- contains edges (headings) ----

    def test_extracts_heading_contains(self):
        nodes, edges = _parse_and_extract("basic.md")
        contains = [e for e in edges if e["kind"] == "contains"]
        target_texts = {e["target_text"] for e in contains}
        assert "Project Title" in target_texts
        assert "Introduction" in target_texts
        assert "Usage" in target_texts
        assert "Sub Section" in target_texts
        assert "License" in target_texts

    def test_heading_count_matches(self):
        nodes, edges = _parse_and_extract("headings_only.md")
        contains = [e for e in edges if e["kind"] == "contains"]
        # H1: 1, H2: 2, H3: 3, H4: 1 => 7
        assert len(contains) == 7

    def test_headings_all_levels(self):
        nodes, edges = _parse_and_extract("headings_only.md")
        contains = [e for e in edges if e["kind"] == "contains"]
        # Check that all heading text is captured
        texts = {e["target_text"] for e in contains}
        assert "H1 Title" in texts
        assert "H2 First" in texts
        assert "H3 Sub A" in texts
        assert "H4 Detail" in texts

    # ---- contains edges (fenced code blocks) ----

    def test_extracts_code_block_contains(self):
        nodes, edges = _parse_and_extract("code_blocks.md")
        contains = [e for e in edges if e["kind"] == "contains"]
        code_blocks = [e for e in contains if "lang:" in e["target_text"]]
        assert len(code_blocks) >= 2
        lang_texts = {e["target_text"] for e in code_blocks}
        assert any("python" in t for t in lang_texts)
        assert any("javascript" in t for t in lang_texts)

    def test_code_block_with_no_language(self):
        nodes, edges = _parse_and_extract("code_blocks.md")
        contains = [e for e in edges if e["kind"] == "contains"]
        code_blocks = [e for e in contains if "lang:" in e["target_text"]]
        lang_texts = {e["target_text"] for e in code_blocks}
        # There should be at least one with empty/plain
        assert any(e["target_text"] == "lang: " or "lang: " in e["target_text"]
                   for e in code_blocks)

    # ---- references edges (inline links) ----

    def test_extracts_link_references(self):
        nodes, edges = _parse_and_extract("basic.md")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "https://example.com" in urls

    def test_extracts_image_references(self):
        nodes, edges = _parse_and_extract("basic.md")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "images/logo.png" in urls

    def test_multiple_links(self):
        nodes, edges = _parse_and_extract("links_and_refs.md")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "https://google.com" in urls
        assert "https://github.com" in urls

    def test_multiple_images(self):
        nodes, edges = _parse_and_extract("links_and_refs.md")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "images/screenshot.png" in urls

    # ---- references edges (link reference definitions) ----

    def test_extracts_ref_definitions(self):
        nodes, edges = _parse_and_extract("links_and_refs.md")
        refs = [e for e in edges if e["kind"] == "references"]
        urls = {e["target_text"] for e in refs}
        assert "https://ref1.example.com" in urls
        assert "https://ref2.example.com/page" in urls

    # ---- edge cases ----

    def test_empty_file(self):
        nodes, edges = _parse_and_extract("empty.md")
        assert isinstance(edges, list)
        assert len(edges) == 0

    def test_all_edges_have_required_fields(self):
        nodes, edges = _parse_and_extract("basic.md")
        assert len(edges) > 0
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
            assert "target_text" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["provenance"] == "tree-sitter"
            assert len(edge["source"]) == 32

    def test_all_edges_valid_kinds(self):
        nodes, edges = _parse_and_extract("links_and_refs.md")
        for edge in edges:
            assert edge["kind"] in ("contains", "references")
