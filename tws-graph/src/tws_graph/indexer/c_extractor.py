"""C source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
provenance="heuristic": no preprocessor expansion, pure syntax-level extraction.
"""

from .base import ExtractionResult, hash_id


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _children(node):
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_child(node, kind: str):
    for child in _children(node):
        if child.kind() == kind:
            return child
    return None


def _find_named_child(node, kind: str):
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _get_func_name(node, src_bytes: bytes) -> str | None:
    """Extract function name from a function_definition node.

    function_definition
      type: ...
      function_declarator
        declarator: (identifier)  or  (pointer_declarator declarator: (identifier))
    """
    declarator = _find_named_child(node, "function_declarator")
    if not declarator:
        return None
    id_node = _find_identifier_deep(declarator, src_bytes)
    if id_node:
        return _node_text(id_node, src_bytes)
    return None


def _find_identifier_deep(node, src_bytes: bytes):
    """Walk down declarators to find the first identifier."""
    if node.kind() in ("identifier", "field_identifier"):
        return node
    if node.kind() == "declarator":
        # Direct identifier
        id_node = _find_named_child(node, "identifier")
        if id_node:
            return id_node
        # field_identifier
        id_node = _find_named_child(node, "field_identifier")
        if id_node:
            return id_node
    # Recurse into named children
    for child in _named_children(node):
        result = _find_identifier_deep(child, src_bytes)
        if result:
            return result
    return None


def _get_type_name(node, src_bytes: bytes) -> str | None:
    """Extract type name from struct_specifier / union_specifier / enum_specifier."""
    # type_identifier or identifier
    id_node = _find_named_child(node, "type_identifier")
    if not id_node:
        id_node = _find_named_child(node, "identifier")
    if id_node:
        return _node_text(id_node, src_bytes)
    return None


def visit_c(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a C source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []

    def make_qualified(simple_name: str) -> str:
        parts = [file_path] + name_stack + [simple_name]
        return "::".join(parts)

    def add_node(kind: str, simple_name: str, node, **extra):
        qname = make_qualified(simple_name)
        nid = hash_id(qname, file_path)
        sp = node.start_position()
        ep = node.end_position()
        record = {
            "id": nid,
            "kind": kind,
            "name": simple_name,
            "qualified_name": qname,
            "file_path": file_path,
            "language": "c",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": "public",
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        result.nodes.append(record)
        return nid

    def add_edge(source: str, target: str, kind: str, line: int, target_text: str | None = None):
        edge = {
            "source": source,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "heuristic",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    def _visit(node):
        kind = node.kind()

        if kind == "function_definition":
            _visit_function(node)
        elif kind == "preproc_include":
            _visit_include(node)
        elif kind == "struct_specifier":
            _visit_struct(node)
        elif kind == "union_specifier":
            _visit_union(node)
        elif kind == "enum_specifier":
            _visit_enum(node)
        elif kind == "declaration":
            _visit_declaration(node)

        # Recurse into children (but skip function bodies and type bodies that we handle)
        if kind not in ("function_definition", "struct_specifier",
                        "union_specifier", "enum_specifier", "compound_statement",
                        "field_declaration_list", "enumerator_list"):
            for child in _named_children(node):
                _visit(child)

    def _visit_function(node):
        name = _get_func_name(node, src_bytes)
        if not name:
            return
        nid = add_node("function", name, node)

        name_stack.append(name)
        node_stack.append(nid)

        # Walk body for calls
        body = _find_child(node, "compound_statement")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_include(node):
        """#include <stdio.h> or #include "mylib.h"."""
        # path is in the preproc_include node's text; extract from system_lib_string or string_literal
        path_node = _find_named_child(node, "system_lib_string")
        if not path_node:
            path_node = _find_named_child(node, "string_literal")
        if not path_node:
            path_node = _find_named_child(node, "identifier")
        if not path_node:
            return

        path_text = _node_text(path_node, src_bytes)
        # Strip quotes or angle brackets
        header = path_text.strip('"').strip("<>").strip()

        # Create a virtual include node
        qname = f"{file_path}::include::{header}"
        source_id = hash_id(qname, file_path)

        edge = {
            "source": source_id,
            "target": "",
            "kind": "imports",
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "target_text": header,
            "provenance": "heuristic",
        }
        result.edges.append(edge)

    def _visit_struct(node):
        name = _get_type_name(node, src_bytes)
        if not name:
            return
        nid = add_node("struct", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        body = _find_named_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "field_declaration":
                    _visit_field_in_type(child, nid)
            name_stack.pop()
            node_stack.pop()

    def _visit_union(node):
        name = _get_type_name(node, src_bytes)
        if not name:
            return
        nid = add_node("union", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        body = _find_named_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "field_declaration":
                    _visit_field_in_type(child, nid)
            name_stack.pop()
            node_stack.pop()

    def _visit_enum(node):
        name = _get_type_name(node, src_bytes)
        if not name:
            return
        nid = add_node("enum", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _visit_field_in_type(field_node, parent_id):
        """Extract field declarations inside struct/union bodies."""
        declarator = _find_named_child(field_node, "field_identifier")
        if not declarator:
            declarator = _find_named_child(field_node, "identifier")
        if not declarator:
            return
        fname = _node_text(declarator, src_bytes)
        fid = add_node("field", fname, field_node)
        add_edge(parent_id, fid, "contains", field_node.start_position().row + 1)

    def _visit_declaration(node):
        """Handle global variable declarations."""
        # Only process at file scope (not inside functions)
        if name_stack:
            return
        # Find declarator with identifier
        declarator = _find_named_child(node, "init_declarator")
        if declarator:
            id_node = _find_identifier_deep(declarator, src_bytes)
        else:
            id_node = _find_identifier_deep(node, src_bytes)

        if not id_node:
            return

        # Skip function declarators (only capture variable declarations)
        if _find_child(node, "function_declarator"):
            return

        name = _node_text(id_node, src_bytes)
        # Skip typedefs and simple type declarations
        storage = _find_named_child(node, "storage_class_specifier")
        storage_text = _node_text(storage, src_bytes) if storage else ""

        nid = add_node("variable", name, node)
        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _extract_calls(body, caller_id, src_bytes):
        _walk_calls(body, caller_id, src_bytes)

    def _walk_calls(node, caller_id, src_bytes):
        if node.kind() == "call_expression":
            fn = _find_named_child(node, "identifier")
            if not fn:
                # Check for field_expression (e.g., obj.method)
                fe = _find_named_child(node, "field_expression")
                if fe:
                    fn = _find_named_child(fe, "field_identifier")
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, hash_id(target, file_path), "calls",
                         node.start_position().row + 1, callee)

        for child in _named_children(node):
            _walk_calls(child, caller_id, src_bytes)

    # Walk the tree
    for child in _named_children(tree.root_node()):
        _visit(child)

    return result
