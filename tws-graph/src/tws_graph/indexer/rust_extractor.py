"""Rust source extractor — walks tree-sitter CST for symbols and edges.

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


def visit_rust(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Rust source file."""
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
            "language": "rust",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _rust_visibility(node, src_bytes, simple_name),
            "is_abstract": 0,
            "is_exported": 0,
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

        if kind == "function_item":
            _visit_function(node)
        elif kind == "struct_item":
            _visit_struct(node)
        elif kind == "impl_item":
            _visit_impl(node)
        elif kind == "trait_item":
            _visit_trait(node)
        elif kind == "enum_item":
            _visit_enum(node)

        # Recurse (but not into function/struct/impl bodies directly)
        if kind not in ("function_item", "struct_item", "impl_item",
                        "trait_item", "enum_item", "block"):
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

        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_struct(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        nid = add_node("class", name, node)

        # Find fields
        body = _find_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "field_declaration":
                    fname = _find_named_child(child, "field_identifier")
                    if fname:
                        fn = _node_text(fname, src_bytes)
                        fid = add_node("property", fn, child)
                        add_edge(nid, fid, "contains", child.start_position().row + 1)
            name_stack.pop()
            node_stack.pop()

    def _visit_impl(node):
        # impl Foo { fn method() ... }
        type_name = None
        type_node = _find_named_child(node, "type_identifier")
        if type_node:
            type_name = _node_text(type_node, src_bytes)

        # trait impl
        trait_node = _find_named_child(node, "trait_type")
        if not trait_node:
            trait_node = _find_named_child(node, "scoped_type_identifier")

        body = _find_child(node, "declaration_list")
        if not body:
            return

        if type_name:
            name_stack.append(type_name)
            # Find or create parent struct id
            parent_q = f"{file_path}::{type_name}"
            parent_id = _hash_id(parent_q, file_path)
            node_stack.append(parent_id)

        for child in _named_children(body):
            if child.kind() == "function_item":
                _visit_function(child)

        if type_name:
            name_stack.pop()
            node_stack.pop()

    def _visit_trait(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("interface", name, node)

        body = _find_child(node, "declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "function_item":
                    _visit_function(child)
            name_stack.pop()
            node_stack.pop()

    def _visit_enum(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        add_node("enum", name, node)

    def _extract_calls(body, caller_id, src_bytes):
        _walk_calls(body, caller_id, src_bytes)

    def _walk_calls(node, caller_id, src_bytes):
        if node.kind() == "call_expression":
            # function() or obj.method()
            fn = _find_named_child(node, "identifier")
            if not fn:
                fn = _find_named_child(node, "field_expression")
                if fn:
                    # field_expression has value + field_identifier
                    fn = _find_named_child(fn, "field_identifier")
            if not fn:
                fn = _find_named_child(node, "scoped_identifier")
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)

        for child in _named_children(node):
            _walk_calls(child, caller_id, src_bytes)

    for child in _named_children(tree.root_node()):
        _visit(child)

    return result


def _rust_visibility(node, src_bytes, name: str) -> str:
    """Rust visibility: pub → public, pub(crate) → internal, else private."""
    vis_node = _find_child(node, "visibility_modifier")
    if vis_node:
        vis_text = _node_text(vis_node, src_bytes)
        if "crate" in vis_text:
            return "internal"
        return "public"
    return "private"


def _build_sig(node, src_bytes) -> str:
    """Build a simple signature from parameters."""
    params = _find_child(node, "parameters")
    if not params:
        return "()"
    sig_parts = []
    for child in _named_children(params):
        if child.kind() == "parameter":
            pat = _find_named_child(child, "identifier")
            typ = _find_named_child(child, "type_identifier")
            if pat and typ:
                sig_parts.append(
                    f"{_node_text(pat, src_bytes)}: {_node_text(typ, src_bytes)}"
                )
            elif pat:
                sig_parts.append(_node_text(pat, src_bytes))
    return f"({', '.join(sig_parts)})"
