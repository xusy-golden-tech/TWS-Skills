"""Kustomize overlay extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Detects kustomization.yaml files and extracts:
- CONTAINS edges for: bases, resources, namespace, configMapGenerator,
  secretGenerator, commonLabels, commonAnnotations
- REFERENCES edges for: patches (target_text = patch path)
- IMPORTS edges for: images (newName/newTag)

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("yaml")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "kustomization.yaml")
"""

from __future__ import annotations

import os

from tws_graph.indexer.base import hash_id, BaseExtractor, ExtractionContext


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


def _get_scalar_text(node, source: bytes) -> str:
    """Extract the text content from a YAML scalar node.

    Handles plain_scalar, double_quote_scalar, single_quote_scalar,
    and their nested string_scalar children.
    """
    # Direct string_scalar child
    for child in _named_children(node):
        if child.kind() == "string_scalar":
            return _node_text(child, source)

    # scalar types that have string_scalar inside
    if node.kind() in ("plain_scalar", "double_quote_scalar", "single_quote_scalar"):
        for child in _named_children(node):
            if child.kind() == "string_scalar":
                return _node_text(child, source)
        return _node_text(node, source)

    # flow_node wrapping a scalar
    if node.kind() == "flow_node":
        for child in _named_children(node):
            text = _get_scalar_text(child, source)
            if text:
                return text

    return ""


def _get_flow_node_text(flow_node, source: bytes) -> str:
    """Extract string content from a flow_node."""
    for child in _named_children(flow_node):
        text = _get_scalar_text(child, source)
        if text:
            return text
    return _node_text(flow_node, source)


def _is_kustomization(file_path: str) -> bool:
    """Check if the file is a kustomization.yaml/yml file."""
    basename = os.path.basename(file_path)
    return basename.lower() in ("kustomization.yaml", "kustomization.yml")


def _walk_block_mapping(block_mapping, source: bytes, file_path: str, edges: list[dict]):
    """Walk a YAML block_mapping node and process kustomization-specific keys."""
    for child in _named_children(block_mapping):
        if child.kind() != "block_mapping_pair":
            continue

        # Find key
        key_node = None
        value_node = None
        for pair_child in _named_children(child):
            if key_node is None:
                key_node = pair_child
            else:
                value_node = pair_child
                break

        if key_node is None:
            continue

        key_text = _get_flow_node_text(key_node, source)
        if not key_text:
            continue

        line = child.start_position().row + 1
        section_id = hash_id(f"{file_path}::{key_text}", file_path)

        # bases/resources/namespace → CONTAINS
        if key_text in ("bases", "resources"):
            if value_node and value_node.kind() == "block_node":
                _extract_list_items_as_contains(value_node, source, file_path,
                                                key_text, section_id, edges)
        elif key_text == "namespace":
            if value_node:
                val_text = _get_flow_node_text(value_node, source)
                if val_text:
                    content_id = hash_id(f"{file_path}::namespace/{val_text}", file_path)
                    edges.append(_make_edge(content_id, val_text, "contains",
                                           file_path, line))
            edges.append(_make_edge(section_id, key_text, "contains", file_path, line))

        # patches → REFERENCES
        elif key_text == "patches":
            if value_node and value_node.kind() == "block_node":
                _extract_patch_references(value_node, source, file_path,
                                         section_id, edges)
            else:
                edges.append(_make_edge(section_id, key_text, "references", file_path, line))

        # images → IMPORTS
        elif key_text == "images":
            if value_node and value_node.kind() == "block_node":
                _extract_image_imports(value_node, source, file_path,
                                      section_id, edges)

        # configMapGenerator / secretGenerator → CONTAINS
        elif key_text in ("configMapGenerator", "secretGenerator"):
            edges.append(_make_edge(section_id, key_text, "contains", file_path, line))

        # commonLabels / commonAnnotations → CONTAINS
        elif key_text in ("commonLabels", "commonAnnotations"):
            edges.append(_make_edge(section_id, key_text, "contains", file_path, line))


def _extract_list_items_as_contains(value_node, source: bytes, file_path: str,
                                     section: str, section_id: str, edges: list[dict]):
    """Extract string items from a YAML list (block_sequence) as CONTAINS edges."""
    for child in _named_children(value_node):
        if child.kind() == "block_sequence":
            for seq_child in _named_children(child):
                if seq_child.kind() != "block_sequence_item":
                    continue
                line = seq_child.start_position().row + 1
                for item_child in _named_children(seq_child):
                    if item_child.kind() == "flow_node":
                        item_text = _get_flow_node_text(item_child, source)
                        if item_text:
                            source_id = hash_id(f"{file_path}::{section}/{item_text}", file_path)
                            edges.append(_make_edge(source_id, item_text, "contains",
                                                    file_path, line))
                        break  # one item per sequence item
                else:
                    # fallback: use the full item text
                    item_text = _node_text(seq_child, source).strip()
                    if item_text:
                        source_id = hash_id(f"{file_path}::{section}/{item_text}", file_path)
                        edges.append(_make_edge(source_id, item_text, "contains",
                                               file_path, line))
            break  # found block_sequence, done


def _extract_patch_references(value_node, source: bytes, file_path: str,
                               section_id: str, edges: list[dict]):
    """Extract patch references from a patches list."""
    for child in _named_children(value_node):
        if child.kind() == "block_sequence":
            for seq_child in _named_children(child):
                if seq_child.kind() != "block_sequence_item":
                    continue
                line = seq_child.start_position().row + 1
                # Patches can be simple strings or objects with path field
                found = False
                for item_child in _named_children(seq_child):
                    if item_child.kind() == "flow_node":
                        item_text = _get_flow_node_text(item_child, source)
                        if item_text:
                            edges.append(_make_edge(section_id, item_text, "references",
                                                   file_path, line))
                        found = True
                        break
                    elif item_child.kind() == "block_node":
                        # Object patch: extract 'path' field
                        _extract_patch_path_from_block(item_child, source, file_path,
                                                       section_id, line, edges)
                        found = True
                        break
            break


def _extract_patch_path_from_block(block_node, source: bytes, file_path: str,
                                    section_id: str, line: int, edges: list[dict]):
    """Extract the 'path' value from a patch object block."""
    for child in _named_children(block_node):
        if child.kind() == "block_mapping":
            for pair_child in _named_children(child):
                if pair_child.kind() != "block_mapping_pair":
                    continue
                pair_key = None
                pair_value = None
                for pc in _named_children(pair_child):
                    if pair_key is None:
                        pair_key = pc
                    else:
                        pair_value = pc
                        break
                if pair_key is None:
                    continue
                key_text = _get_flow_node_text(pair_key, source)
                if key_text == "path" and pair_value:
                    path_text = _get_flow_node_text(pair_value, source)
                    if path_text:
                        edges.append(_make_edge(section_id, path_text, "references",
                                               file_path, line))
                    break
            break


def _extract_image_imports(value_node, source: bytes, file_path: str,
                            section_id: str, edges: list[dict]):
    """Extract image imports from an images list.

    Each image entry may have name, newName, newTag, digest.
    We extract name as IMPORTS and newName/newTag as imports targets.
    """
    for child in _named_children(value_node):
        if child.kind() == "block_sequence":
            for seq_child in _named_children(child):
                if seq_child.kind() != "block_sequence_item":
                    continue
                line = seq_child.start_position().row + 1
                # Simple string image reference
                for item_child in _named_children(seq_child):
                    if item_child.kind() == "flow_node":
                        item_text = _get_flow_node_text(item_child, source)
                        if item_text:
                            edges.append(_make_edge(section_id, item_text, "imports",
                                                   file_path, line))
                        break
                    elif item_child.kind() == "block_node":
                        # Object image: extract name/newName/newTag
                        _extract_image_from_block(item_child, source, file_path,
                                                  section_id, line, edges)
            break


def _extract_image_from_block(block_node, source: bytes, file_path: str,
                               section_id: str, line: int, edges: list[dict]):
    """Extract image name/newName/newTag from a block_mapping in an images list."""
    for child in _named_children(block_node):
        if child.kind() != "block_mapping":
            continue
        for pair_child in _named_children(child):
            if pair_child.kind() != "block_mapping_pair":
                continue
            pair_key = None
            pair_value = None
            for pc in _named_children(pair_child):
                if pair_key is None:
                    pair_key = pc
                else:
                    pair_value = pc
                    break
            if pair_key is None:
                continue
            key_text = _get_flow_node_text(pair_key, source)
            if key_text in ("name", "newName", "newTag") and pair_value:
                val_text = _get_flow_node_text(pair_value, source)
                # Strip surrounding quotes that YAML may include in scalar text
                val_text = val_text.strip("'\"")
                if val_text:
                    edges.append(_make_edge(section_id, val_text, "imports",
                                           file_path, line))


def kustomize_extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract CONTAINS, REFERENCES, and IMPORTS edges from a kustomization YAML CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path (used in source IDs and locs).

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []

    if not _is_kustomization(file_path):
        return edges

    root = tree.root_node()

    # Walk the YAML document structure
    for child in _named_children(root):
        if child.kind() == "document":
            for doc_child in _named_children(child):
                if doc_child.kind() == "block_node":
                    for bc in _named_children(doc_child):
                        if bc.kind() == "block_mapping":
                            _walk_block_mapping(bc, source, file_path, edges)
                            break
                    break
            break

    return edges


class KustomizeExtractor(BaseExtractor):
    """BaseExtractor wrapper for the standalone kustomize_extract function."""

    extensions = [".yaml", ".yml"]
    tree_sitter_languages = ["yaml"]
    language_name = "yaml"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        edges = kustomize_extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(edges)
