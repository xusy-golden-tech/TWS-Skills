"""Clojure extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: def/defn/defmacro definitions, ns declarations
- IMPORTS edges for: require/use/import (inside ns)
- CALLS edges for: function calls (first element of a list)

All edges use provenance = "heuristic" because Clojure Lisp macros prevent
reliable static analysis via tree-sitter alone.

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("clojure")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "test.clj")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor, ExtractionContext, make_structural_node

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


def _list_first_sym_text(list_node, source: bytes) -> str | None:
    """Get the text of the first symbol in a list_lit."""
    for child in _named_children(list_node):
        if child.kind() == "sym_lit":
            sym_name = _find_named_child(child, "sym_name")
            if sym_name:
                return _node_text(sym_name, source)
    return None


def _count_sym_lit_children(list_node) -> int:
    """Count how many sym_lit children a list has (before the first non-sym)."""
    count = 0
    for child in _named_children(list_node):
        if child.kind() == "sym_lit":
            count += 1
        else:
            break
    return count


def _sym_name_text(sym_node, source: bytes) -> str:
    """Extract the symbol name from a sym_lit node."""
    sym_name = _find_named_child(sym_node, "sym_name")
    if sym_name:
        return _node_text(sym_name, source)
    return _node_text(sym_node, source)


def _kwd_name_text(kwd_node, source: bytes) -> str:
    """Extract the keyword name (without colon) from a kwd_lit node."""
    kwd_name = _find_named_child(kwd_node, "kwd_name")
    if kwd_name:
        return _node_text(kwd_name, source)
    return _node_text(kwd_node, source)


def _extract_ns_imports(ns_list, source: bytes, file_path: str, file_id: str, edges: list):
    """Extract require/use/import declarations from inside an ns form."""
    # ns form structure: (ns name ...options...)
    # Options are list_lit starting with :require, :use, :import
    children = list(_named_children(ns_list))
    # Skip first two children (ns sym and namespace name)
    for child in children[2:]:
        if child.kind() == "list_lit":
            # Check if this list starts with a keyword (:require, :use, :import)
            kwd = _find_named_child(child, "kwd_lit")
            if not kwd:
                continue
            kwd_text = _kwd_name_text(kwd, source)
            if kwd_text in ("require", "use", "import"):
                # Extract module names from the vector(s)
                for c in _named_children(child):
                    if c.kind() == "vec_lit":
                        for item in _named_children(c):
                            if item.kind() == "sym_lit":
                                mod_name = _sym_name_text(item, source)
                                edges.append(_make_edge(
                                    file_id, mod_name, "imports",
                                    file_path, _line(child),
                                ))


def _is_definition_form(first_sym: str) -> bool:
    """Check if the form is a definition (def, defn, defmacro, etc.)."""
    return first_sym in ("def", "defn", "defmacro", "defn-", "defmacro-", "defonce")


def clojure_extract(source: bytes, tree, file_path: str) -> tuple[list[dict], list[dict]]:
    """Extract nodes and edges from a Clojure source CST.

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
            nodes.append(make_structural_node(source_id, name, kind, file_path, line, "clojure"))

    root = tree.root_node()

    file_id = hash_id(f"{file_path}::__clojure_file__", file_path)
    _add_node(file_id, "__clojure_file__", "clojure_file", 1)

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = _line(child)

            if kind == "list_lit":
                first_sym = _list_first_sym_text(child, source)
                if not first_sym:
                    # Empty list or first child is not a symbol — skip
                    walk(child)
                    continue

                # --- ns declaration → CONTAINS ---
                if first_sym == "ns":
                    for c in _named_children(child):
                        if c.kind() == "sym_lit":
                            ns_name = _sym_name_text(c, source)
                            if ns_name != "ns":
                                sid = hash_id(f"{file_path}::{ns_name}", file_path)
                                _add_node(sid, ns_name, "namespace", line)
                                edges.append(_make_edge(
                                    sid, ns_name, "contains", file_path, line,
                                ))
                                break
                    # Extract imports inside ns
                    _extract_ns_imports(child, source, file_path, file_id, edges)
                    continue  # ns is a top-level form, no need to recurse deeper

                # --- def/defn/defmacro → CONTAINS ---
                if _is_definition_form(first_sym):
                    sym_count = _count_sym_lit_children(child)
                    if sym_count >= 2:
                        # Second sym is the var name
                        syms = []
                        for c in _named_children(child):
                            if c.kind() == "sym_lit":
                                syms.append(c)
                        if len(syms) >= 2:
                            var_name = _sym_name_text(syms[1], source)
                            sid = hash_id(f"{file_path}::{var_name}", file_path)
                            _add_node(sid, var_name, "var_def", line)
                            edges.append(_make_edge(
                                sid, var_name, "contains", file_path, line,
                            ))

                # --- any other list → CALLS (first symbol is the function) ---
                else:
                    edges.append(_make_edge(
                        file_id, first_sym, "calls", file_path, line,
                    ))

            # Recurse
            walk(child)

    walk(root)
    return nodes, edges


class ClojureExtractor(BaseExtractor):
    """BaseExtractor wrapper for the standalone clojure_extract function."""

    extensions = [".clj", ".cljs", ".cljc", ".edn"]
    tree_sitter_languages = ["clojure"]
    language_name = "clojure"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        result_nodes, result_edges = clojure_extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result_nodes)
        ctx.result.edges.extend(result_edges)
