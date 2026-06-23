"""CSS extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- REFERENCES edges for: rule set selectors (class, id, element)
- IMPORTS edges for: @import statements
- CONTAINS edges for: CSS variable definitions, @keyframes, @media queries

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("css")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "style.css")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor, make_structural_node


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


def _extract_selector_text(selectors_node, source: bytes) -> str:
    """Extract the full selector text from a selectors node."""
    return _node_text(selectors_node, source).strip()


def _extract_import_url(import_node, source: bytes) -> str | None:
    """Extract the URL from an @import statement."""
    call_expr = _find_named_child(import_node, "call_expression")
    if call_expr is None:
        return None
    args = _find_named_child(call_expr, "arguments")
    if args is None:
        # Some tree-sitter versions use function_arguments
        for child in _named_children(call_expr):
            if child.kind() in ("arguments", "function_arguments"):
                args = child
                break
    if args is None:
        return None
    str_val = _find_named_child(args, "string_value")
    if str_val:
        content = _find_named_child(str_val, "string_content")
        if content:
            return _node_text(content, source)
    return None


def _extract_keyframes_name(node, source: bytes) -> str | None:
    """Extract the animation name from @keyframes."""
    name_node = _find_named_child(node, "keyframes_name")
    if name_node:
        return _node_text(name_node, source).strip()
    return None


def extract(source: bytes, tree, file_path: str) -> tuple[list[dict], list[dict]]:
    """Extract nodes and edges from a CSS CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        Tuple of (node dicts, edge dicts).
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def _add_node(source_id: str, name: str, kind: str, line: int):
        if source_id not in seen:
            seen.add(source_id)
            nodes.append(make_structural_node(source_id, name, kind, file_path, line, "css"))

    root = tree.root_node()

    # State for counting unnamed selectors
    state: dict[str, int] = {}

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = child.start_position().row + 1

            if kind == "rule_set":
                selectors = _find_named_child(child, "selectors")
                if selectors:
                    sel_text = _extract_selector_text(selectors, source)
                    # Build a unique identifier for this rule_set
                    state_key = f"rule_{sel_text}"
                    state[state_key] = state.get(state_key, 0) + 1
                    idx = state[state_key]
                    ident = f"rule:{sel_text}" if idx == 1 else f"rule:{sel_text}__{idx}"
                    source_id = hash_id(f"{file_path}::{ident}", file_path)
                    _add_node(source_id, ident, "css_rule", line)

                    edges.append(_make_edge(
                        source_id, sel_text, "references",
                        file_path, line,
                    ))

                    # Check for CSS variable definitions inside block
                    block = _find_named_child(child, "block")
                    if block:
                        _extract_variables(block, source_id, file_path, source, edges)

            elif kind == "import_statement":
                url = _extract_import_url(child, source)
                if url:
                    ident = f"@import::{url}"
                    source_id = hash_id(f"{file_path}::{ident}", file_path)
                    _add_node(source_id, ident, "css_import", line)
                    edges.append(_make_edge(
                        source_id, url, "imports",
                        file_path, line,
                    ))

            elif kind == "keyframes_statement":
                anim_name = _extract_keyframes_name(child, source)
                if anim_name:
                    ident = f"@keyframes::{anim_name}"
                    source_id = hash_id(f"{file_path}::{ident}", file_path)
                    _add_node(source_id, ident, "css_keyframes", line)
                    edges.append(_make_edge(
                        source_id, anim_name, "contains",
                        file_path, line,
                    ))

            elif kind == "media_statement":
                feature = _find_named_child(child, "feature_query")
                if feature:
                    feature_text = _node_text(feature, source).strip()
                else:
                    binary = _find_named_child(child, "binary_query")
                    if binary:
                        feature_text = _node_text(binary, source).strip()
                    else:
                        keyword = _find_named_child(child, "keyword_query")
                        if keyword:
                            feature_text = _node_text(keyword, source).strip()
                        else:
                            feature_text = "media"
                ident = f"@media::{feature_text}"
                source_id = hash_id(f"{file_path}::{ident}", file_path)
                _add_node(source_id, ident, "css_media", line)
                edges.append(_make_edge(
                    source_id, feature_text, "contains",
                    file_path, line,
                ))

            # Recurse into children
            walk(child)

    def _extract_variables(block_node, parent_id: str, file_path: str,
                           source: bytes, edges: list[dict]):
        """Extract CSS custom property (variable) declarations from a block."""
        for child in _named_children(block_node):
            if child.kind() == "declaration":
                prop_name = _find_named_child(child, "property_name")
                if prop_name:
                    pname = _node_text(prop_name, source).strip()
                    if pname.startswith("--"):
                        edges.append(_make_edge(
                            parent_id, pname, "contains",
                            file_path, child.start_position().row + 1,
                        ))

    walk(root)
    return nodes, edges


class CssExtractor(BaseExtractor):
    extensions = [".css"]
    tree_sitter_languages = ["css"]
    language_name = "css"

    def extract(self, source, tree, ctx) -> None:
        result_nodes, result_edges = extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result_nodes)
        ctx.result.edges.extend(result_edges)
