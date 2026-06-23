"""Haskell extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: data/newtype/type definitions, function bindings (top-level),
  module declarations, class/instance definitions
- CALLS edges for: function calls (apply nodes)
- IMPORTS edges for: import statements

All edges use provenance = "heuristic" because Haskell syntax is complex
and tree-sitter cannot fully resolve dynamic dispatch / typeclass resolution.

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("haskell")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "test.hs")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id

PROVENANCE = "heuristic"


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


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": PROVENANCE,
    }


def _line(node) -> int:
    return node.start_position().row + 1


def _extract_module_name(node, source: bytes) -> str | None:
    """Extract module name from a header or import module node."""
    mod_node = _find_named_child(node, "module")
    if not mod_node:
        return None
    # module contains module_id children
    ids = _find_all_named_children(mod_node, "module_id")
    if ids:
        return ".".join(_node_text(i, source) for i in ids)
    return _node_text(mod_node, source)


def _extract_name(node, source: bytes) -> str | None:
    """Extract the name from a node that has a 'name' or 'variable' child."""
    n = _find_named_child(node, "name")
    if n:
        return _node_text(n, source)
    v = _find_named_child(node, "variable")
    if v:
        return _node_text(v, source)
    return None


def _extract_apply_target(node, source: bytes) -> str | None:
    """Extract the function/constructor name from an apply node."""
    for child in _named_children(node):
        if child.kind() in ("variable", "constructor", "name"):
            return _node_text(child, source)
    return None


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from a Haskell source CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    file_id = hash_id(f"{file_path}::__haskell_file__", file_path)

    # Container kinds that we should recurse into for finding more definitions/calls
    _CONTAINER_KINDS = {
        "haskell", "declarations", "class_declarations", "instance_declarations",
        "imports", "header", "data_type", "newtype", "type_synomym",
        "class", "instance", "data_constructors", "parens",
        "infix", "match", "where", "deriving",
        "function", "bind",   # recurse to find apply calls inside function bodies
        "apply",              # recurse to find nested function applications
        "do", "exp", "let",   # do-notation, expression statements, let bindings
    }

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = _line(child)

            # --- data/newtype/type definitions → CONTAINS ---
            if kind in ("data_type", "newtype", "type_synomym"):
                name_text = _extract_name(child, source)
                if name_text:
                    edges.append(_make_edge(
                        hash_id(f"{file_path}::{name_text}", file_path),
                        name_text, "contains", file_path, line,
                    ))

            # --- class/instance definitions → CONTAINS ---
            elif kind in ("class", "instance"):
                name_text = _extract_name(child, source)
                if name_text:
                    edges.append(_make_edge(
                        hash_id(f"{file_path}::{name_text}", file_path),
                        name_text, "contains", file_path, line,
                    ))

            # --- function bindings (top-level) → CONTAINS ---
            elif kind in ("function", "bind"):
                var = _find_named_child(child, "variable")
                if var:
                    name_text = _node_text(var, source)
                    # Only treat as definition if the name starts with lowercase
                    # (PascalCase names in function position are type constructors)
                    if name_text and name_text[0].islower():
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))

            # --- signature (type annotation for function) → CONTAINS ---
            elif kind == "signature":
                var = _find_named_child(child, "variable")
                if var:
                    name_text = _node_text(var, source)
                    if name_text and name_text[0].islower():
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))
                continue  # Don't recurse into type expressions

            # --- apply (function application) → CALLS ---
            elif kind == "apply":
                target = _extract_apply_target(child, source)
                if target:
                    if target and (target[0].isalpha() or target[0] == '_'):
                        edges.append(_make_edge(
                            file_id, target, "calls", file_path, line,
                        ))

            # --- import statement → IMPORTS ---
            elif kind == "import":
                mod_name = _extract_module_name(child, source)
                if mod_name:
                    edges.append(_make_edge(
                        file_id, mod_name, "imports", file_path, line,
                    ))

            # --- module declaration → CONTAINS ---
            elif kind == "header":
                mod_name = _extract_module_name(child, source)
                if mod_name:
                    edges.append(_make_edge(
                        hash_id(f"{file_path}::{mod_name}", file_path),
                        mod_name, "contains", file_path, line,
                    ))

            # Recurse into container nodes only (to find nested definitions/calls)
            if kind in _CONTAINER_KINDS:
                walk(child)

    walk(root)
    return edges
