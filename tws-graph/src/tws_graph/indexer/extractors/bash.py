"""Bash/Shell extractor — P43 File Coverage Expansion.

Uses tree-sitter-bash via tree_sitter_language_pack (same as all other extractors).
Produces: function definitions, variable assignments, source/import commands.

Usage:
    parser = get_parser("bash")
    tree = parser.parse(source.encode())
    result = extract(source.encode(), tree, "script.sh")
"""

from __future__ import annotations

import re

from tws_graph.indexer.base import hash_id, BaseExtractor


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    """Iterate named children (API compat with tree-sitter < 0.22 and >= 0.22)."""
    try:
        return list(node.named_children)
    except AttributeError:
        return [node.named_child(i) for i in range(node.named_child_count())]


def extract(source: bytes, tree, file_path: str) -> dict:
    """Extract nodes and edges from a Bash script AST."""
    nodes = []
    edges = []
    file_node_id = f"file:{file_path}"

    root = tree.root_node()
    _walk(root, source, file_path, nodes, edges)

    return {"nodes": nodes, "edges": edges}


def _walk(node, source: bytes, file_path: str, nodes: list, edges: list):
    """Recursively walk the AST."""
    kind = _node_kind(node)

    if kind == "function_definition":
        _extract_function(node, source, file_path, nodes)
    elif kind == "variable_assignment":
        _extract_variable(node, source, file_path, edges)
    elif kind == "command":
        _extract_command(node, source, file_path, edges)

    for child in _named_children(node):
        _walk(child, source, file_path, nodes, edges)


def _node_kind(node) -> str:
    """Get node kind (API compat with old .kind() and new .type)."""
    try:
        return node.kind()
    except (TypeError, AttributeError):
        return node.type


def _extract_function(node, source: bytes, file_path: str, nodes: list):
    """Extract a function_definition node."""
    name = None
    body_text = ""
    for child in _named_children(node):
        ck = _node_kind(child)
        if ck == "word" and name is None:
            name = _node_text(child, source)
        elif ck in ("compound_statement", "body"):
            body_text = _node_text(child, source)

    if not name:
        return

    node_id = hash_id(f"function.{name}", file_path)
    nodes.append({
        "id": node_id,
        "kind": "function",
        "name": name,
        "qualified_name": f"{file_path}::function.{name}",
        "file_path": file_path,
        "language": "bash",
        "start_line": node.start_position().row + 1,
        "end_line": node.end_position().row + 1,
        "signature": f"function {name}()",
        "docstring": None,
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 1,
        "decorators": None,
        "framework": None,
        "properties": "{}",
        "body": body_text,
        "body_hash": hash_id(body_text, file_path) if body_text else None,
    })


def _extract_variable(node, source: bytes, file_path: str, edges: list):
    """Extract variable assignment as WRITES edge."""
    var_name = None
    for child in _named_children(node):
        ck = _node_kind(child)
        if ck == "variable_name":
            var_name = _node_text(child, source)
            break
        elif ck == "word" and var_name is None:
            var_name = _node_text(child, source)

    if var_name:
        edges.append({
            "source": f"file:{file_path}",
            "target": "",
            "kind": "writes",
            "target_text": var_name,
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "provenance": "tree-sitter",
        })


def _extract_command(node, source: bytes, file_path: str, edges: list):
    """Extract command calls (source, ., export, general commands)."""
    children = list(_named_children(node))
    if not children:
        return

    first = children[0]
    first_text = _node_text(first, source).strip()

    # source / . scripts → IMPORTS
    if first_text in ("source", "."):
        for child in children[1:]:
            arg_text = _node_text(child, source).strip().strip('"').strip("'")
            if arg_text and not arg_text.startswith("-"):
                edges.append({
                    "source": f"file:{file_path}",
                    "target": "",
                    "kind": "imports",
                    "target_text": arg_text,
                    "source_loc": f"{file_path}:{node.start_position().row + 1}",
                    "provenance": "tree-sitter",
                })
                break
    # export → ENV_ACCESSES
    elif first_text == "export":
        for child in children[1:]:
            child_text = _node_text(child, source).strip()
            if child_text and not child_text.startswith("-"):
                edges.append({
                    "source": f"file:{file_path}",
                    "target": "",
                    "kind": "env_accesses",
                    "target_text": child_text.split("=")[0],
                    "source_loc": f"{file_path}:{node.start_position().row + 1}",
                    "provenance": "tree-sitter",
                })
    else:
        # General command → CALLS
        cmd_text = first_text
        edges.append({
            "source": f"file:{file_path}",
            "target": "",
            "kind": "calls",
            "target_text": cmd_text,
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "provenance": "tree-sitter",
        })


class BashExtractor(BaseExtractor):
    """Bash/Shell extractor (P43)."""

    extensions = [".sh", ".bash", ".zsh", ".ksh"]
    language_name = "bash"
    tree_sitter_languages = ["bash"]

    def extract(self, source: bytes, tree, ctx) -> None:
        result = extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result["nodes"])
        ctx.result.edges.extend(result["edges"])
