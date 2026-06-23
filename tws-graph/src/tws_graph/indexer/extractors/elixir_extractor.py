"""Elixir extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: defmodule definitions, def/defp function definitions
- CALLS edges for: function calls (dot calls and local calls)
- IMPORTS edges for: import, alias, require statements, use macro

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("elixir")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "test.ex")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor, ExtractionContext


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


def _get_call_name_text(child, source: bytes) -> str | None:
    """Extract module/function name from a call's arguments.

    For defmodule: arguments contain an alias.
    For def/defp: arguments contain a call node or identifier wrapping the function name.
    For import/alias/use/require: arguments contain alias(es).
    """
    arguments = _find_named_child(child, "arguments")
    if not arguments:
        return None
    for c in _named_children(arguments):
        if c.kind() == "alias":
            return _node_text(c, source)
        if c.kind() == "call":
            ident = _find_named_child(c, "identifier")
            if ident:
                return _node_text(ident, source)
        if c.kind() == "identifier":
            return _node_text(c, source)
    return None


def _get_import_target(child, source: bytes) -> str | None:
    """Extract target module from import/alias/use/require calls."""
    arguments = _find_named_child(child, "arguments")
    if not arguments:
        return None
    aliases = _find_all_named_children(arguments, "alias")
    if aliases:
        return _node_text(aliases[0], source)
    return None


def _is_local_name(identifier, source: bytes) -> bool:
    """Check if an identifier is a local name (starts with lowercase) vs module."""
    text = _node_text(identifier, source)
    return text and text[0].islower()


def elixir_extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from an Elixir source CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    file_id = hash_id(f"{file_path}::__elixir_file__", file_path)

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = _line(child)

            if kind == "call":
                identifier = _find_named_child(child, "identifier")
                if not identifier:
                    walk(child)
                    continue

                id_text = _node_text(identifier, source)

                # --- defmodule -> CONTAINS ---
                if id_text == "defmodule":
                    name_text = _get_call_name_text(child, source)
                    if name_text:
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))
                    # Only walk into do_block, skip arguments (avoids false CALLS)
                    do_block = _find_named_child(child, "do_block")
                    if do_block:
                        walk(do_block)
                    continue

                # --- def/defp -> CONTAINS ---
                if id_text in ("def", "defp"):
                    name_text = _get_call_name_text(child, source)
                    if name_text:
                        edges.append(_make_edge(
                            hash_id(f"{file_path}::{name_text}", file_path),
                            name_text, "contains", file_path, line,
                        ))
                    # Only walk into do_block, skip arguments
                    do_block = _find_named_child(child, "do_block")
                    if do_block:
                        walk(do_block)
                    continue

                # --- import/alias/require/use -> IMPORTS ---
                if id_text in ("import", "alias", "require", "use"):
                    target_text = _get_import_target(child, source)
                    if target_text:
                        edges.append(_make_edge(
                            file_id, target_text, "imports", file_path, line,
                        ))
                    continue  # no body to walk into

                # --- other calls (function calls) -> CALLS ---
                if _is_local_name(identifier, source):
                    edges.append(_make_edge(
                        file_id, id_text, "calls", file_path, line,
                    ))

            # --- dot calls (module.function) -> CALLS ---
            elif kind == "dot":
                parts = []
                for c in _named_children(child):
                    parts.append(_node_text(c, source))
                dot_text = ".".join(parts) if parts else _node_text(child, source)
                edges.append(_make_edge(
                    file_id, dot_text, "calls", file_path, line,
                ))

            # Recurse
            walk(child)

    walk(root)
    return edges


class ElixirExtractor(BaseExtractor):
    """BaseExtractor wrapper for the standalone elixir_extract function."""

    extensions = [".ex", ".exs"]
    tree_sitter_languages = ["elixir"]
    language_name = "elixir"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        edges = elixir_extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(edges)
