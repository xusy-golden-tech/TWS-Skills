"""TOML extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces CONTAINS edges for:
- Top-level tables: [name]
- Dotted tables: [parent.child]
- Array-of-tables: [[name]]
- Key-value pairs: key = value

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("toml")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "config.toml")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _children(node):
    for i in range(node.child_count()):
        yield node.child(i)


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _resolve_dotted_key(node, source: bytes) -> str:
    """Resolve a dotted_key or bare_key node to a dotted name string."""
    if node.kind() == "bare_key":
        return _node_text(node, source)
    if node.kind() == "dotted_key":
        parts = []
        for child in _children(node):
            if child.is_named and child.kind() in ("bare_key", "dotted_key"):
                parts.append(_resolve_dotted_key(child, source))
        return ".".join(parts)
    return ""


def _resolve_pair_text(node, source: bytes) -> str:
    """Resolve a pair node to 'key=value' text."""
    key = ""
    value = ""
    for child in _named_children(node):
        if child.kind() in ("bare_key", "dotted_key", "quoted_key"):
            if child.kind() == "quoted_key":
                key = _node_text(child, source)
            else:
                key = _resolve_dotted_key(child, source)
        elif key and child.kind() not in ("=",) and child.is_named:
            value = _node_text(child, source)
            break
    if key and value:
        return f"{key}={value}"
    return ""


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract CONTAINS edges from a TOML CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path (used in source IDs and locs).

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    def walk(node, current_section: str = ""):
        for child in _children(node):
            kind = child.kind()

            if kind == "table":
                # [name] or [dotted.key]
                key_node = None
                for c in _named_children(child):
                    if c.kind() in ("bare_key", "dotted_key"):
                        key_node = c
                        break
                if key_node:
                    section = _resolve_dotted_key(key_node, source)
                    source_id = hash_id(f"{file_path}::{section}", file_path)
                    edges.append(_make_edge(
                        source_id, section, "contains",
                        file_path, child.start_position().row + 1,
                    ))
                    # Recurse with new section context
                    for c in _named_children(child):
                        if c.kind() == "pair":
                            _handle_pair(c, source_id, file_path)
                    # continue walking into pairs
                    continue

            elif kind == "table_array_element":
                # [[name]]
                key_node = None
                for c in _named_children(child):
                    if c.kind() in ("bare_key", "dotted_key"):
                        key_node = c
                        break
                if key_node:
                    section = _resolve_dotted_key(key_node, source)
                    source_id = hash_id(f"{file_path}::{section}", file_path)
                    edges.append(_make_edge(
                        source_id, section, "contains",
                        file_path, child.start_position().row + 1,
                    ))
                    for c in _named_children(child):
                        if c.kind() == "pair":
                            _handle_pair(c, source_id, file_path)
                    continue

            # walk children recursively
            walk(child, current_section)

    def _handle_pair(pair_node, parent_id: str, file_path: str):
        """Handle a key=value pair."""
        text = _resolve_pair_text(pair_node, source)
        if text:
            edges.append(_make_edge(
                parent_id, text, "contains",
                file_path, pair_node.start_position().row + 1,
            ))

    walk(root)
    return edges


class TomlExtractor(BaseExtractor):
    extensions = [".toml"]
    tree_sitter_languages = ["toml"]

    def extract(self, source, tree, ctx) -> None:
        result_edges = extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(result_edges)
