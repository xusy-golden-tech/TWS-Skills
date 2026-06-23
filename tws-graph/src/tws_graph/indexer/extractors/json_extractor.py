"""JSON extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: top-level keys, nested keys (via dotted paths)
- IMPORTS edges for: package.json dependencies/devDependencies/peerDependencies
- CONTAINS edges for: package.json scripts

package.json special handling (heuristic based on file path):
  - dependencies/devDependencies/peerDependencies keys → IMPORTS edges
  - scripts → CONTAINS edges

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("json")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "package.json")
"""

from __future__ import annotations

import os

from tws_graph.indexer.base import hash_id, BaseExtractor, ExtractionContext, make_structural_node


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _get_string_content(node, source: bytes) -> str:
    """Extract string_content from a string/string_content node."""
    if node.kind() == "string_content":
        return _node_text(node, source)
    for child in _named_children(node):
        if child.kind() == "string_content":
            return _node_text(child, source)
    return _node_text(node, source)


def _get_pair_key(pair_node, source: bytes) -> str | None:
    """Extract the key from a pair node."""
    for child in _named_children(pair_node):
        if child.kind() == "string":
            return _get_string_content(child, source)
    return None


def _get_pair_value(pair_node, source: bytes):
    """Get the value node (object, array, string, number, etc.) from a pair."""
    found_string = False
    for child in _named_children(pair_node):
        if child.kind() == "string":
            if not found_string:
                found_string = True  # first string is the key
                continue
            return child  # second string is the value
        elif found_string or child.kind() in ("object", "array", "number", "true", "false", "null"):
            return child
    return None


def _walk_object(obj_node, source: bytes, file_path: str, prefix: str,
                 edges: list[dict], is_package_json: bool, add_node):
    """Walk an object node, extracting keys and their values."""
    for child in _named_children(obj_node):
        if child.kind() != "pair":
            continue

        key = _get_pair_key(child, source)
        if key is None:
            continue

        line = child.start_position().row + 1
        key_path = f"{prefix}.{key}" if prefix else key
        source_id = hash_id(f"{file_path}::{key_path}", file_path)
        add_node(source_id, key_path, "json_key", line)

        # Emit CONTAINS edge for this key
        edges.append(_make_edge(source_id, key_path, "contains", file_path, line))

        value_node = _get_pair_value(child, source)
        if value_node is None:
            continue

        # package.json special handling — skip default recurse for handled containers
        skip_default_recurse = False
        if is_package_json:
            if key in ("dependencies", "devDependencies", "peerDependencies"):
                if value_node.kind() == "object":
                    _extract_dependency_imports(value_node, source, file_path, source_id, edges)
                    skip_default_recurse = True
            elif key == "scripts":
                if value_node.kind() == "object":
                    _extract_scripts(value_node, source, file_path, source_id, edges, add_node)
                    skip_default_recurse = True

        # Recurse into nested objects (unless handled by special extractors)
        if not skip_default_recurse and value_node.kind() == "object":
            _walk_object(value_node, source, file_path, key_path, edges, is_package_json, add_node)

        # Recurse into arrays
        elif value_node.kind() == "array":
            _walk_array(value_node, source, file_path, key_path, edges, is_package_json, add_node)


def _walk_array(arr_node, source: bytes, file_path: str, prefix: str,
                edges: list[dict], is_package_json: bool, add_node):
    """Walk an array node, extracting objects within."""
    for child in _named_children(arr_node):
        if child.kind() == "object":
            _walk_object(child, source, file_path, prefix, edges, is_package_json, add_node)
        elif child.kind() == "array":
            _walk_array(child, source, file_path, prefix, edges, is_package_json, add_node)


def _extract_dependency_imports(deps_obj, source: bytes, file_path: str,
                                 parent_id: str, edges: list[dict]):
    """Extract package names as IMPORTS edges from a dependencies object."""
    for child in _named_children(deps_obj):
        if child.kind() != "pair":
            continue
        key = _get_pair_key(child, source)
        if key:
            line = child.start_position().row + 1
            edges.append(_make_edge(parent_id, key, "imports", file_path, line))


def _extract_scripts(scripts_obj, source: bytes, file_path: str,
                      parent_id: str, edges: list[dict], add_node):
    """Extract script names as CONTAINS edges from a scripts object."""
    for child in _named_children(scripts_obj):
        if child.kind() != "pair":
            continue
        key = _get_pair_key(child, source)
        if key:
            line = child.start_position().row + 1
            key_path = f"scripts.{key}"
            source_id = hash_id(f"{file_path}::{key_path}", file_path)
            add_node(source_id, key_path, "json_key", line)
            edges.append(_make_edge(source_id, key_path, "contains", file_path, line))


def _is_package_json(file_path: str) -> bool:
    """Check if the file is a package.json file."""
    basename = os.path.basename(file_path)
    return basename.lower() == "package.json"


def json_extract(source: bytes, tree, file_path: str) -> tuple[list[dict], list[dict]]:
    """Extract CONTAINS and IMPORTS nodes and edges from a JSON CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path (used in source IDs and locs).

    Returns:
        Tuple of (node dicts, edge dicts).
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def _add_node(source_id: str, name: str, kind: str, line: int):
        if source_id not in seen:
            seen.add(source_id)
            nodes.append(make_structural_node(source_id, name, kind, file_path, line, "json"))

    root = tree.root_node()
    is_pkg_json = _is_package_json(file_path)

    # Find the document/object/array root
    for child in _named_children(root):
        if child.kind() == "document":
            for doc_child in _named_children(child):
                if doc_child.kind() == "object":
                    _walk_object(doc_child, source, file_path, "", edges, is_pkg_json, _add_node)
                    break
                elif doc_child.kind() == "array":
                    _walk_array(doc_child, source, file_path, "", edges, is_pkg_json, _add_node)
                    break
            break
        elif child.kind() == "object":
            _walk_object(child, source, file_path, "", edges, is_pkg_json, _add_node)
            break
        elif child.kind() == "array":
            _walk_array(child, source, file_path, "", edges, is_pkg_json, _add_node)
            break

    return nodes, edges


class JsonExtractor(BaseExtractor):
    """BaseExtractor wrapper for the standalone json_extract function."""

    extensions = [".json"]
    tree_sitter_languages = ["json"]
    language_name = "json"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        result_nodes, result_edges = json_extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result_nodes)
        ctx.result.edges.extend(result_edges)
