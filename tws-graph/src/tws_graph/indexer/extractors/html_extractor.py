"""HTML extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: elements with id attributes
- IMPORTS edges for: <script src="..."> and <link href="...">
- REFERENCES edges for: <a href="..."> and <form action="...">

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("html")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "index.html")
"""

from __future__ import annotations

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


def _get_attribute_value(element_node, attr_name: str, source: bytes) -> str | None:
    """Get the value of an attribute from an element's start_tag."""
    start_tag = _find_named_child(element_node, "start_tag")
    if start_tag is None:
        return None
    for child in _named_children(start_tag):
        if child.kind() == "attribute":
            name_node = _find_named_child(child, "attribute_name")
            if name_node and _node_text(name_node, source) == attr_name:
                val_node = _find_named_child(child, "quoted_attribute_value")
                if val_node:
                    attr_val = _find_named_child(val_node, "attribute_value")
                    if attr_val:
                        return _node_text(attr_val, source)
    return None


def _get_tag_name(element_node, source: bytes) -> str | None:
    """Get the tag name from an element's start_tag."""
    start_tag = _find_named_child(element_node, "start_tag")
    if start_tag is None:
        return None
    tag_name_node = _find_named_child(start_tag, "tag_name")
    if tag_name_node:
        return _node_text(tag_name_node, source)
    return None


def _get_element_id(element_node, source: bytes) -> str | None:
    """Get the id attribute value from an element."""
    return _get_attribute_value(element_node, "id", source)


def _count_elements_with_tag(root, tag: str) -> int:
    """Count elements with a given tag name in the tree (for auto-index numbering)."""
    count = 0

    def walk(node):
        nonlocal count
        for child in _named_children(node):
            if child.kind() == "element" or child.kind() == "script_element":
                tag_name = _get_tag_name(child, b"")  # we don't have source yet, just check kind
                if tag_name == tag:
                    count += 1
            walk(child)

    walk(root)
    return count


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from an HTML CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    def walk(node, state: dict):
        """Walk the AST and extract edges. state holds per-tag counters for index assignment."""
        for child in _named_children(node):
            kind = child.kind()

            if kind == "element":
                tag_name = _get_tag_name(child, source)
                element_id = _get_element_id(child, source)
                line = child.start_position().row + 1

                if tag_name:
                    # Track element index for this tag
                    state_key = f"count_{tag_name}"
                    state[state_key] = state.get(state_key, 0) + 1
                    idx = state[state_key]

                    # Build identifier
                    if element_id:
                        ident = f"{tag_name}#{element_id}"
                    else:
                        ident = f"{tag_name}#{idx}"

                    source_id = hash_id(f"{file_path}::{ident}", file_path)

                    # CONTAINS edge for elements with id
                    if element_id:
                        edges.append(_make_edge(
                            source_id, ident, "contains",
                            file_path, line,
                        ))

                    # Check for script src
                    if tag_name == "script":
                        src = _get_attribute_value(child, "src", source)
                        if src:
                            edges.append(_make_edge(
                                source_id, src, "imports",
                                file_path, line,
                            ))

                    # Check for link href
                    elif tag_name == "link":
                        href = _get_attribute_value(child, "href", source)
                        if href:
                            edges.append(_make_edge(
                                source_id, href, "imports",
                                file_path, line,
                            ))

                    # Check for anchor href
                    elif tag_name == "a":
                        href = _get_attribute_value(child, "href", source)
                        if href:
                            edges.append(_make_edge(
                                source_id, href, "references",
                                file_path, line,
                            ))

                    # Check for form action
                    elif tag_name == "form":
                        action = _get_attribute_value(child, "action", source)
                        if action:
                            edges.append(_make_edge(
                                source_id, action, "references",
                                file_path, line,
                            ))

                # Recurse into element children
                walk(child, state)

            elif kind == "script_element":
                tag_name = _get_tag_name(child, source)
                if tag_name is None:
                    tag_name = "script"
                line = child.start_position().row + 1

                state_key = f"count_{tag_name}"
                state[state_key] = state.get(state_key, 0) + 1
                idx = state[state_key]
                ident = f"{tag_name}#{idx}"
                source_id = hash_id(f"{file_path}::{ident}", file_path)

                src = _get_attribute_value(child, "src", source)
                if src:
                    edges.append(_make_edge(
                        source_id, src, "imports",
                        file_path, line,
                    ))

                walk(child, state)

            else:
                # Recurse into other nodes
                walk(child, state)

    walk(root, {})
    return edges


class HtmlExtractor(BaseExtractor):
    extensions = [".html", ".htm"]
    tree_sitter_languages = ["html"]
    language_name = "html"

    def extract(self, source, tree, ctx) -> None:
        result_edges = extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(result_edges)
