"""Java source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
from typing import Optional
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


def visit_java(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Java source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []
    node_id_set: set[str] = set()  # all node IDs created so far — O(1) lookup

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
            "language": "java",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _visibility_from_modifiers(node, src_bytes),
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        result.nodes.append(record)
        node_id_set.add(nid)
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

    def _visit_node(node, depth: int = 0):
        kind = node.kind()

        if kind == "class_declaration":
            _visit_class(node, is_interface=False)
        elif kind == "interface_declaration":
            _visit_class(node, is_interface=True)
        elif kind == "method_declaration":
            _visit_method(node, is_constructor=False)
        elif kind == "constructor_declaration":
            _visit_method(node, is_constructor=True)
        elif kind == "field_declaration":
            _visit_field(node)

        # Recurse into children (but not into nested classes' internals via _visit_class)
        if kind not in ("class_declaration", "interface_declaration",
                        "method_declaration", "constructor_declaration"):
            for child in _named_children(node):
                _visit_node(child, depth + 1)

    def _visit_class(node, is_interface: bool = False):
        name_node = _find_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        cls_kind = "interface" if is_interface else "class"

        # Check for abstract
        is_abstract = 1 if any(
            c.kind() == "abstract" for c in _children(node)
        ) else 0

        nid = add_node(cls_kind, name, node, is_abstract=is_abstract)
        name_stack.append(name)
        node_stack.append(nid)

        # Extends
        sc = _find_child(node, "superclass")
        if sc:
            for child in _named_children(sc):
                if child.kind() == "type_identifier":
                    super_name = _node_text(child, src_bytes)
                    super_qname = f"{file_path}::{super_name}"
                    super_id = _hash_id(super_qname, file_path)
                    # Only emit edge if superclass is defined in this file
                    if super_id in node_id_set:
                        add_edge(nid, super_id, "extends",
                                 child.start_position().row + 1,
                                 super_qname)

        # Implements
        si = _find_child(node, "super_interfaces")
        if si:
            for child in _named_children(si):
                if child.kind() == "type_list":
                    for tc in _named_children(child):
                        if tc.kind() == "type_identifier":
                            iface_name = _node_text(tc, src_bytes)
                            iface_qname = f"{file_path}::{iface_name}"
                            iface_id = _hash_id(iface_qname, file_path)
                            if iface_id in node_id_set:
                                add_edge(nid, iface_id, "implements",
                                         tc.start_position().row + 1,
                                         iface_qname)

        # Contains edge for body
        body = _find_child(node, "class_body") or _find_child(node, "interface_body")
        if body:
            for child in _named_children(body):
                _visit_node(child, 0)
                # Add contains edge for methods/fields
                cn = child.kind()
                if cn in ("method_declaration", "constructor_declaration", "field_declaration"):
                    child_name_node = _find_child(child, "identifier")
                    if child_name_node:
                        child_name = _node_text(child_name_node, src_bytes)
                        child_q = make_qualified(child_name)
                        child_id = _hash_id(child_q, file_path)
                        add_edge(nid, child_id, "contains",
                                 child.start_position().row + 1)

        name_stack.pop()
        node_stack.pop()

    def _visit_method(node, is_constructor: bool = False):
        if is_constructor:
            name_node = _find_child(node, "identifier")
            if not name_node:
                return
            name = _node_text(name_node, src_bytes)
        else:
            # Method: find name via "identifier" child of declarator
            name = _extract_method_name(node, src_bytes)
            if not name:
                return

        # Build signature
        sig = _build_method_sig(node, src_bytes)
        mkind = "method"

        is_abstract = 1 if any(
            c.kind() == "abstract" for c in _children(node)
        ) else 0

        nid = add_node(mkind, name, node, signature=sig, is_abstract=is_abstract)

        # contains edge from parent class
        if node_stack:
            parent_id = node_stack[-1]
            add_edge(parent_id, nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        # Walk body for calls
        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_field(node):
        decl = _find_child(node, "variable_declarator")
        if not decl:
            return
        name_node = _find_child(decl, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("property", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _extract_calls(body, caller_id, src_bytes):
        """Find method_invocation nodes and record call edges."""
        _walk_for_calls(body, caller_id, src_bytes)

    def _walk_for_calls(node, caller_id, src_bytes):
        if node.kind() == "method_invocation":
            # Try to get method name
            name_node = _find_child(node, "identifier")
            # Might be chained; try field_access
            obj = _find_child(node, "field_access")
            if obj:
                # obj.method() → extract method name from field_access
                name_node = _find_child(obj, "identifier")
            if name_node:
                callee = _node_text(name_node, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)

        for child in _named_children(node):
            _walk_for_calls(child, caller_id, src_bytes)

    # Walk the tree
    root = tree.root_node()
    for child in _named_children(root):
        _visit_node(child, 0)

    return result


def _visibility_from_modifiers(node, src_bytes) -> str:
    """Determine Java visibility from modifiers."""
    # Look for 'modifiers' child node
    modifiers = _find_child(node, "modifiers")
    search_in = modifiers if modifiers else node
    for child in _children(search_in):
        kind = child.kind()
        if kind == "private":
            return "private"
        if kind == "protected":
            return "protected"
        if kind == "public":
            return "public"
    return "package-private"


def _extract_method_name(node, src_bytes) -> Optional[str]:
    """Extract method name: find identifier child that is not a type."""
    for child in _named_children(node):
        if child.kind() == "identifier":
            return _node_text(child, src_bytes)
    return None


def _build_method_sig(node, src_bytes) -> str:
    """Build a simple method signature from the parameter list."""
    params = _find_child(node, "formal_parameters")
    if not params:
        return "()"
    sig_parts = []
    for child in _named_children(params):
        if child.kind() == "formal_parameter":
            type_node = _find_child(child, "type_identifier")
            name_node = _find_child(child, "identifier")
            if type_node and name_node:
                sig_parts.append(
                    f"{_node_text(type_node, src_bytes)} {_node_text(name_node, src_bytes)}"
                )
    return f"({' ,'.join(sig_parts)})"
