"""Scala extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: class/object/trait definitions, def method definitions,
  val/var declarations, package declarations
- CALLS edges for: function/method calls
- IMPORTS edges for: import statements

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("scala")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "test.scala")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_named_child(node, kind: str):
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int,
               provenance: str = "tree-sitter") -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": provenance,
    }


def _line(node) -> int:
    return node.start_position().row + 1


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from a Scala source CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    # File-level source ID
    file_id = hash_id(f"{file_path}::__scala_file__", file_path)

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = _line(child)

            # --- class/object/trait definitions → CONTAINS ---
            if kind in ("class_definition", "object_definition", "trait_definition"):
                name = _find_named_child(child, "identifier")
                if name:
                    name_text = _node_text(name, source)
                    edges.append(_make_edge(
                        hash_id(f"{file_path}::{name_text}", file_path),
                        name_text, "contains", file_path, line,
                    ))

            # --- def method definitions → CONTAINS ---
            elif kind in ("function_definition", "function_declaration"):
                name = _find_named_child(child, "identifier")
                if name:
                    name_text = _node_text(name, source)
                    edges.append(_make_edge(
                        hash_id(f"{file_path}::{name_text}", file_path),
                        name_text, "contains", file_path, line,
                    ))

            # --- val/var declarations → CONTAINS ---
            elif kind in ("val_definition", "var_definition"):
                pat_def = _find_named_child(child, "val_pattern")
                if pat_def:
                    name = _find_named_child(pat_def, "identifier")
                    if name:
                        name_text = _node_text(name, source)
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))
                # Simple val/var: val x = ... or var x = ...
                else:
                    name = _find_named_child(child, "identifier")
                    if name:
                        name_text = _node_text(name, source)
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))

            # --- function/method calls → CALLS ---
            elif kind == "call_expression":
                func = _find_named_child(child, "identifier")
                if func:
                    func_text = _node_text(func, source)
                    edges.append(_make_edge(
                        file_id, func_text, "calls", file_path, line,
                    ))

            # --- import statements → IMPORTS ---
            elif kind == "import_declaration":
                identifiers = []
                for c in _named_children(child):
                    if c.kind() == "identifier":
                        identifiers.append(_node_text(c, source))
                if identifiers:
                    import_path = ".".join(identifiers)
                else:
                    import_path = _node_text(child, source).strip()
                edges.append(_make_edge(
                    file_id, import_path, "imports", file_path, line,
                ))

            # --- package declaration → CONTAINS ---
            elif kind == "package_clause":
                pkg_id = _find_named_child(child, "package_identifier")
                if pkg_id:
                    pkg_text = _node_text(pkg_id, source)
                else:
                    pkg_text = _node_text(child, source).strip()
                edges.append(_make_edge(
                    hash_id(f"{file_path}::{pkg_text}", file_path),
                    pkg_text, "contains", file_path, line,
                ))

            # Recurse
            walk(child)

    walk(root)
    return edges
