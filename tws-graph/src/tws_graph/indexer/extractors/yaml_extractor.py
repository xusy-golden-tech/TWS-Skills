"""YAML extractor — standalone (does not inherit BaseExtractor).

Produces:
- Top-level mapping keys → CONTAINS edges
- Nested mapping keys → CONTAINS edges (target_text = parent.key)
- Sequence entries → CONTAINS edges (target_text = key[index])
- !!include / !include tags → IMPORTS edges
- String values matching ${VAR} → REFERENCES edges (environment variable references)
- provenance = "tree-sitter"

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("yaml")
    tree = parser.parse(content_str)
    edges = extract(content_str.encode("utf-8"), tree, "config.yaml")
"""

from __future__ import annotations

import re
from tws_graph.indexer.base import hash_id, BaseExtractor

# Regex for environment variable references: ${VAR_NAME} or ${VAR:-default}
_ENV_VAR_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}')


def _node_text(node, source: bytes) -> str:
    """Decode source bytes spanned by a tree-sitter node."""
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    """Generator over named children only."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    """Create an edge dict in the standard format."""
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _find_scalar_text(node, source: bytes) -> str | None:
    """Recursively find and extract scalar text from a node.

    Walks through flow_node → plain_scalar → string_scalar chain.
    Returns the text of the first string_scalar found, or None.
    """
    kind = node.kind()
    # string_scalar is the innermost text-bearing node
    if kind == "string_scalar":
        return _node_text(node, source)
    # Quoted scalars contain the text directly (no string_scalar child)
    if kind in ("double_quote_scalar", "single_quote_scalar"):
        text = _node_text(node, source)
        # Strip surrounding quotes
        if text and len(text) >= 2:
            if (text[0] == '"' and text[-1] == '"') or \
               (text[0] == "'" and text[-1] == "'"):
                text = text[1:-1]
        return text
    # Non-string scalar types (integer, float, boolean, null) represent their value directly
    if kind in ("integer_scalar", "float_scalar", "boolean_scalar", "null_scalar"):
        return _node_text(node, source)
    for child in _named_children(node):
        result = _find_scalar_text(child, source)
        if result is not None:
            return result
    return None


def _check_env_references(text: str) -> list[str]:
    """Find all ${VAR} references in a string value."""
    return [m.group(0) for m in _ENV_VAR_RE.finditer(text)]


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from a YAML CST.

    Args:
        source: Raw file bytes (UTF-8 encoded).
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    # Top-level source ID for the whole file
    file_id = hash_id(f"{file_path}::__yaml__", file_path)

    def find_tag_in_flow(flow_node) -> str | None:
        """Find a tag node inside a flow_node, return tag text without !! prefix."""
        for child in _named_children(flow_node):
            if child.kind() == "tag":
                text = _node_text(child, source).strip()
                if text.startswith("!!"):
                    text = text[2:]
                elif text.startswith("!"):
                    text = text[1:]
                return text
        return None

    def walk_block_mapping(block_node, parent_key_path: str, parent_source_id: str):
        """Walk inside a block_mapping (or similar), handling pairs."""
        # Find the actual mapping inside block_node
        mapping_node = None
        for child in _named_children(block_node):
            if child.kind() in ("block_mapping", "flow_mapping"):
                mapping_node = child
                break
        if mapping_node is None:
            # block_node might directly contain pairs (when the block_node wraps a mapping)
            mapping_node = block_node

        for pair in _named_children(mapping_node):
            if pair.kind() != "block_mapping_pair":
                continue

            named = list(_named_children(pair))
            if len(named) < 1:
                continue

            # First named child is the key (flow_node)
            key_node = named[0]
            key_text = _find_scalar_text(key_node, source)
            if key_text is None:
                continue

            # Build qualified key path
            key_path = f"{parent_key_path}.{key_text}" if parent_key_path else key_text
            source_id = hash_id(f"{file_path}::{key_path}", file_path)
            line = pair.start_position().row + 1

            # CONTAINS edge for this key
            edges.append(_make_edge(
                parent_source_id if parent_source_id else file_id,
                key_path, "contains", file_path, line,
            ))

            # Second named child (if any) is the value
            if len(named) >= 2:
                value_node = named[1]
                _handle_value(value_node, key_path, source_id, file_path)
            else:
                # No explicit value (null/empty)
                pass

    def _handle_value(value_node, key_path: str, source_id: str, file_path: str):
        """Handle the value part of a mapping pair."""
        if value_node.kind() == "block_node":
            # Could be block_mapping or block_sequence
            for child in _named_children(value_node):
                if child.kind() == "block_mapping":
                    walk_block_mapping(child, key_path, source_id)
                elif child.kind() == "block_sequence":
                    walk_block_sequence(child, key_path, source_id)

        elif value_node.kind() == "flow_node":
            # Could be scalar, or mapping/sequence in flow style
            line = value_node.start_position().row + 1
            tag_text = find_tag_in_flow(value_node)

            for child in _named_children(value_node):
                if child.kind() in ("flow_mapping",):
                    walk_block_mapping(child, key_path, source_id)
                elif child.kind() in ("flow_sequence",):
                    walk_block_sequence(child, key_path, source_id)
                elif child.kind() in ("plain_scalar", "double_quote_scalar", "single_quote_scalar",
                                       "integer_scalar", "float_scalar", "boolean_scalar", "null_scalar"):
                    scalar_text = _find_scalar_text(child, source)
                    if scalar_text is None:
                        continue
                    if tag_text and "include" in tag_text.lower():
                        # !!include / !include → IMPORTS edge
                        edges.append(_make_edge(
                            source_id, scalar_text, "imports",
                            file_path, line,
                        ))
                    else:
                        # Check for ${VAR} references
                        env_refs = _check_env_references(scalar_text)
                        for ref in env_refs:
                            edges.append(_make_edge(
                                source_id, ref, "references",
                                file_path, line,
                            ))

        elif value_node.kind() in ("flow_mapping", "block_mapping"):
            walk_block_mapping(value_node, key_path, source_id)
        elif value_node.kind() in ("flow_sequence", "block_sequence"):
            walk_block_sequence(value_node, key_path, source_id)

    def walk_block_sequence(seq_node, parent_key_path: str, parent_source_id: str):
        """Walk inside a block_sequence, handling items."""
        # seq_node may be a block_sequence or nested inside a block_node
        actual_seq = seq_node
        if seq_node.kind() not in ("block_sequence", "flow_sequence"):
            for child in _named_children(seq_node):
                if child.kind() in ("block_sequence", "flow_sequence"):
                    actual_seq = child
                    break

        idx = 0
        for item in _named_children(actual_seq):
            if item.kind() != "block_sequence_item":
                continue

            item_key = f"{parent_key_path}[{idx}]"
            source_id = hash_id(f"{file_path}::{item_key}", file_path)
            line = item.start_position().row + 1

            edges.append(_make_edge(
                parent_source_id if parent_source_id else file_id,
                item_key, "contains", file_path, line,
            ))

            # Process item content
            named_items = list(_named_children(item))
            if named_items:
                first_val = named_items[0]
                if first_val.kind() == "flow_node":
                    # Check for scalar or nested content
                    tag_text = find_tag_in_flow(first_val)
                    has_complex = False
                    for child in _named_children(first_val):
                        if child.kind() in ("flow_mapping",):
                            has_complex = True
                            walk_block_mapping(child, item_key, source_id)
                        elif child.kind() in ("flow_sequence",):
                            has_complex = True
                            walk_block_sequence(child, item_key, source_id)
                        elif child.kind() in ("plain_scalar", "double_quote_scalar", "single_quote_scalar",
                                               "integer_scalar", "float_scalar", "boolean_scalar", "null_scalar"):
                            scalar_text = _find_scalar_text(child, source)
                            if scalar_text and not has_complex:
                                if tag_text and "include" in tag_text.lower():
                                    edges.append(_make_edge(
                                        source_id, scalar_text, "imports",
                                        file_path, line,
                                    ))
                                else:
                                    env_refs = _check_env_references(scalar_text)
                                    for ref in env_refs:
                                        edges.append(_make_edge(
                                            source_id, ref, "references",
                                            file_path, line,
                                        ))
                elif first_val.kind() == "block_node":
                    for child in _named_children(first_val):
                        if child.kind() == "block_mapping":
                            walk_block_mapping(child, item_key, source_id)
                        elif child.kind() == "block_sequence":
                            walk_block_sequence(child, item_key, source_id)

            idx += 1

    # Start walking from root
    root_node = tree.root_node()
    for doc_node in _named_children(root_node):
        if doc_node.kind() != "document":
            continue
        # Inside document: block_node → block_mapping
        for bn in _named_children(doc_node):
            if bn.kind() == "block_node":
                walk_block_mapping(bn, "", file_id)

    return edges


class YamlExtractor(BaseExtractor):
    extensions = [".yaml", ".yml"]
    tree_sitter_languages = ["yaml"]

    def extract(self, source, tree, ctx) -> None:
        result_edges = extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(result_edges)
