"""Markdown extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: headings (#, ##, ...), fenced code blocks
- REFERENCES edges for: inline links [text](url), images ![alt](url), link reference definitions [id]: url

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("markdown")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "README.md")
"""

from __future__ import annotations

import re

from tws_graph.indexer.base import hash_id, BaseExtractor


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_named_child(node, kind: str):
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


# Regex patterns for inline markdown links and images
_LINK_RE = re.compile(r'(?<!\!)\[([^\]]*)\]\(([^)]+)\)')
_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def _extract_inline_links(text: str) -> list[tuple[str, str]]:
    """Extract [text](url) links from inline text. Returns list of (text, url)."""
    return _LINK_RE.findall(text)


def _extract_inline_images(text: str) -> list[tuple[str, str]]:
    """Extract ![alt](url) images from inline text. Returns list of (alt, url)."""
    return _IMAGE_RE.findall(text)


def _extract_heading_text(heading_node, source: bytes) -> str | None:
    """Extract the text content of a heading."""
    inline = _find_named_child(heading_node, "inline")
    if inline:
        return _node_text(inline, source).strip()
    return None


def _extract_code_lang(fenced_code_block_node, source: bytes) -> str | None:
    """Extract the language identifier from a fenced code block."""
    info_string = _find_named_child(fenced_code_block_node, "info_string")
    if info_string:
        lang = _find_named_child(info_string, "language")
        if lang:
            return _node_text(lang, source).strip()
        return _node_text(info_string, source).strip()
    return None


def _get_heading_level(heading_node) -> int:
    """Determine heading level from atx_h{n}_marker, default 1."""
    for child in _named_children(heading_node):
        kind = child.kind()
        if kind.startswith("atx_h") and kind.endswith("_marker"):
            # Try to extract number from e.g. "atx_h2_marker"
            try:
                parts = kind.split("_")
                for p in parts:
                    if p.startswith("h") and len(p) > 1:
                        num = p[1:]
                        if num.isdigit():
                            return int(num)
            except (ValueError, IndexError):
                pass
    return 1


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from a Markdown CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    heading_count: dict[int, int] = {}  # level -> count for indexing

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = child.start_position().row + 1

            if kind == "atx_heading":
                level = _get_heading_level(child)
                heading_text = _extract_heading_text(child, source)

                if heading_text:
                    heading_count[level] = heading_count.get(level, 0) + 1
                    idx = heading_count[level]
                    ident = f"h{level}#{idx}: {heading_text}"
                    source_id = hash_id(f"{file_path}::heading::{ident}", file_path)

                    edges.append(_make_edge(
                        source_id, heading_text, "contains",
                        file_path, line,
                    ))

            elif kind == "fenced_code_block":
                lang = _extract_code_lang(child, source) or ""
                code_fence = _find_named_child(child, "code_fence_content")
                if code_fence:
                    code_text = _node_text(code_fence, source).strip()
                else:
                    code_text = ""

                # Use a short snippet for target_text
                preview = code_text[:40].replace("\n", "\\n")
                ident = f"codeblock:{lang}" if lang else "codeblock"
                source_id = hash_id(f"{file_path}::codeblock::{ident}", file_path)

                edges.append(_make_edge(
                    source_id, f"lang:{lang} {preview}", "contains",
                    file_path, line,
                ))

            elif kind == "inline":
                text = _node_text(child, source)

                # Extract links [text](url)
                for link_text, url in _extract_inline_links(text):
                    source_id = hash_id(
                        f"{file_path}::link::{link_text}->{url}", file_path,
                    )
                    edges.append(_make_edge(
                        source_id, url, "references",
                        file_path, line,
                    ))

                # Extract images ![alt](url)
                for alt_text, url in _extract_inline_images(text):
                    source_id = hash_id(
                        f"{file_path}::image::{alt_text}->{url}", file_path,
                    )
                    edges.append(_make_edge(
                        source_id, url, "references",
                        file_path, line,
                    ))

            elif kind == "link_reference_definition":
                label = _find_named_child(child, "link_label")
                dest = _find_named_child(child, "link_destination")
                if label and dest:
                    label_text = _node_text(label, source).strip()
                    dest_text = _node_text(dest, source).strip()
                    source_id = hash_id(
                        f"{file_path}::refdef::{label_text}", file_path,
                    )
                    edges.append(_make_edge(
                        source_id, dest_text, "references",
                        file_path, line,
                    ))

            # Recurse into children
            walk(child)

    walk(root)
    return edges


class MarkdownExtractor(BaseExtractor):
    extensions = [".md", ".markdown"]
    tree_sitter_languages = ["markdown"]
    language_name = "markdown"

    def extract(self, source, tree, ctx) -> None:
        result_edges = extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(result_edges)
