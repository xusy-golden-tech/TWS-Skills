"""Go source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
from .base import ExtractionResult


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


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


def visit_go(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Go source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []

    def make_qualified(simple_name: str) -> str:
        parts = [file_path] + name_stack + [simple_name]
        return "::".join(parts)

    def add_node(kind: str, simple_name: str, node, **extra):
        qname = make_qualified(simple_name)
        nid = _hash_id(qname, file_path)
        sp = node.start_position()
        ep = node.end_position()
        record = {
            "id": nid,
            "kind": kind,
            "name": simple_name,
            "qualified_name": qname,
            "file_path": file_path,
            "language": "go",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _go_visibility(simple_name),
            "is_abstract": 0,
            "is_exported": 0 if simple_name[0].islower() else 0 if simple_name else 0,
        }
        record.update(extra)
        result.nodes.append(record)
        return nid

    def add_edge(source: str, target: str, kind: str, line: int, target_text: str | None = None):
        edge = {
            "source": source, "target": target, "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    def _visit(node):
        kind = node.kind()

        if kind == "function_declaration":
            _visit_function(node)
        elif kind == "method_declaration":
            _visit_method(node)
        elif kind == "type_declaration":
            _visit_type_decl(node)

        # Recurse into children
        if kind not in ("function_declaration", "method_declaration",
                        "type_declaration", "block", "field_declaration_list"):
            for child in _named_children(node):
                _visit(child)

    def _visit_function(node):
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        sig = _build_sig(node, src_bytes)
        nid = add_node("function", name, node, signature=sig)

        name_stack.append(name)
        node_stack.append(nid)

        # Walk body for calls
        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_method(node):
        # Method: field_name, identifier, parameters, result, block?
        name_node = _find_named_child(node, "field_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        sig = _build_sig(node, src_bytes)
        nid = add_node("method", name, node, signature=sig)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_type_decl(node):
        # type Foo struct { ... } or type Foo interface { ... }
        spec = _find_child(node, "type_spec")
        if not spec:
            return
        name_node = _find_named_child(spec, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        # Determine if struct or interface
        body = _find_child(spec, "struct_type")
        if body:
            tkind = "class"
        else:
            body = _find_child(spec, "interface_type")
            tkind = "interface" if body else "class"

        nid = add_node(tkind, name, node)

        # Find methods inside struct/interface
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() in ("field_declaration", "field_declaration_list"):
                    for fc in _named_children(child):
                        if fc.kind() == "field_declaration":
                            fname = _find_named_child(fc, "field_identifier")
                            if fname:
                                fn = _node_text(fname, src_bytes)
                                add_node("property", fn, fc)
                                if node_stack:
                                    add_edge(node_stack[-1],
                                             _hash_id(make_qualified(fn), file_path),
                                             "contains", fc.start_position().row + 1)
            # Methods in source file
            name_stack.pop()
            node_stack.pop()

    def _extract_calls(body, caller_id, src_bytes):
        _walk_calls(body, caller_id, src_bytes)

    def _walk_calls(node, caller_id, src_bytes):
        if node.kind() == "call_expression":
            # Get function name
            fn = _find_child(node, "identifier")
            if not fn:
                # selector_expression: obj.method()
                sel = _find_child(node, "selector_expression")
                if sel:
                    fn = _find_named_child(sel, "field_identifier")
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)

        for child in _named_children(node):
            _walk_calls(child, caller_id, src_bytes)

    # Walk the tree
    for child in _named_children(tree.root_node()):
        _visit(child)

    return result


def _go_visibility(name: str) -> str:
    """Go visibility: uppercase first char → exported/public, lowercase → private."""
    if name and name[0].isupper():
        return "public"
    return "private"


def _build_sig(node, src_bytes) -> str:
    """Build a simple signature from parameter list."""
    params = _find_child(node, "parameter_list")
    if not params:
        return "()"
    sig_parts = []
    for child in _named_children(params):
        if child.kind() == "parameter_declaration":
            name_node = _find_named_child(child, "identifier")
            type_node = _find_named_child(child, "type_identifier")
            if name_node and type_node:
                sig_parts.append(
                    f"{_node_text(name_node, src_bytes)} {_node_text(type_node, src_bytes)}"
                )
            elif name_node:
                sig_parts.append(_node_text(name_node, src_bytes))
    return f"({', '.join(sig_parts)})"
