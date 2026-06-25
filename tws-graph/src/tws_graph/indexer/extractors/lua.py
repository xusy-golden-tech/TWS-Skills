"""Lua extractor — P43 File Coverage Expansion.

Uses tree-sitter-lua via tree_sitter_language_pack.
Produces: function definitions, variable assignments, require() imports, function calls.
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    """Iterate named children."""
    return [node.named_child(i) for i in range(node.named_child_count())]


def extract(source: bytes, tree, file_path: str) -> dict:
    """Extract nodes and edges from a Lua AST."""
    nodes = []
    edges = []

    root = tree.root_node()
    _walk(root, source, file_path, nodes, edges)

    return {"nodes": nodes, "edges": edges}


def _walk(node, source: bytes, file_path: str, nodes: list, edges: list):
    kind = node.kind()

    if kind == "function_declaration":
        _extract_function(node, source, file_path, nodes)
    elif kind == "function_call":
        _extract_call(node, source, file_path, edges)
    elif kind in ("variable_declaration", "local_variable_declaration"):
        _extract_variable(node, source, file_path, edges)

    for child in _named_children(node):
        _walk(child, source, file_path, nodes, edges)


def _extract_function(node, source: bytes, file_path: str, nodes: list):
    name = None
    params = ""
    for child in _named_children(node):
        ck = child.kind()
        if ck == "identifier" and name is None:
            name = _node_text(child, source)
        elif ck == "parameters":
            params = _node_text(child, source)

    if not name:
        return

    node_id = hash_id(f"function.{name}", file_path)
    nodes.append({
        "id": node_id,
        "kind": "function",
        "name": name,
        "qualified_name": f"{file_path}::function.{name}",
        "file_path": file_path,
        "language": "lua",
        "start_line": node.start_position().row + 1,
        "end_line": node.end_position().row + 1,
        "signature": f"function {name}({params})",
        "docstring": None,
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 1,
        "decorators": None,
        "framework": None,
        "properties": "{}",
        "body": None,
        "body_hash": None,
    })


def _extract_variable(node, source: bytes, file_path: str, edges: list):
    for child in _named_children(node):
        ck = child.kind()
        if ck == "assignment_statement":
            for gc in _named_children(child):
                if gc.kind() == "variable_list":
                    for vc in _named_children(gc):
                        var_text = _node_text(vc, source)
                        if var_text:
                            edges.append({
                                "source": f"file:{file_path}",
                                "target": "",
                                "kind": "writes",
                                "target_text": var_text,
                                "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                "provenance": "tree-sitter",
                            })
            break


def _extract_call(node, source: bytes, file_path: str, edges: list):
    children = _named_children(node)
    if not children:
        return

    first = children[0]
    first_text = _node_text(first, source)

    # require("module") → IMPORTS
    if first_text == "identifier":
        prefix_text = _node_text(first, source)
    else:
        prefix_text = first_text

    # Look for the call name
    call_name = None
    for child in children:
        if child.kind() == "identifier":
            call_name = _node_text(child, source)
            break
        elif child.kind() == "dot_index_expression":
            call_name = _node_text(child, source)
            break

    if not call_name:
        return

    if call_name == "require":
        # Extract the module name from arguments
        for child in children:
            if child.kind() == "arguments":
                for ac in _named_children(child):
                    if ac.kind() == "string":
                        mod_name = _node_text(ac, source).strip('"').strip("'")
                        if mod_name:
                            edges.append({
                                "source": f"file:{file_path}",
                                "target": "",
                                "kind": "imports",
                                "target_text": mod_name,
                                "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                "provenance": "tree-sitter",
                            })
                        return
    else:
        edges.append({
            "source": f"file:{file_path}",
            "target": "",
            "kind": "calls",
            "target_text": call_name,
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "provenance": "tree-sitter",
        })


class LuaExtractor(BaseExtractor):
    """Lua extractor (P43)."""

    extensions = [".lua"]
    language_name = "lua"
    tree_sitter_languages = ["lua"]

    def extract(self, source: bytes, tree, ctx) -> None:
        result = extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result["nodes"])
        ctx.result.edges.extend(result["edges"])
