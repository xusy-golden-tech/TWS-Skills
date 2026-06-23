"""C# source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
Extracts class/struct/interface definitions, methods, calls, using directives,
namespaces, and property/field declarations.
"""

import hashlib
from .parser import ExtractionResult


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _children(node):
    """Generator over all children of a node (named and unnamed)."""
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    """Generator over named children only."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_named_child(node, kind: str):
    """Find the first named child with the given kind."""
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _find_all_named_children(node, kind: str) -> list:
    """Find all named children with the given kind."""
    results = []
    for child in _named_children(node):
        if child.kind() == kind:
            results.append(child)
    return results


def _get_name(node, source: bytes) -> str | None:
    """Get the identifier name from a type/method/namespace declaration."""
    name_node = _find_named_child(node, "identifier")
    if name_node:
        return _node_text(name_node, source)
    return None


def _get_modifier_text(modifiers_node, source: bytes) -> list[str]:
    """Extract modifier strings from a modifiers list."""
    if modifiers_node is None:
        return []
    return [_node_text(c, source) for c in _named_children(modifiers_node)]


def _visibility_from_modifiers(modifiers: list[str]) -> str:
    """Derive visibility from modifier keywords."""
    for m in ("private", "protected", "public", "internal"):
        if m in modifiers:
            return m
    return "public"


def visit_csharp(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a C# CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    name_stack: list[str] = []
    node_stack: list[str] = []
    scope_kinds: list[str] = []

    def make_qualified(simple_name: str) -> str:
        parts = [file_path] + name_stack + [simple_name]
        return "::".join(parts)

    def add_node(kind: str, simple_name: str, node, **extra) -> str:
        qname = make_qualified(simple_name)
        nid = _hash_id(qname, file_path)
        sp = node.start_position()
        ep = node.end_position()
        result.nodes.append({
            "id": nid,
            "kind": kind,
            "name": simple_name,
            "qualified_name": qname,
            "file_path": file_path,
            "language": "csharp",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            **extra,
        })
        return nid

    def add_edge(source_id: str, target: str, kind: str, line: int, target_text: str | None = None):
        edge = {
            "source": source_id,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # Create a file-level node for the compilation unit, so top-level
    # using directives and namespace declarations have a parent scope.
    # Only create if root has named children (non-empty file).
    if root.named_child_count() > 0:
        file_id = add_node("file", file_path, root)
    else:
        file_id = ""

    def walk(node):
        nonlocal node_stack, name_stack, scope_kinds, file_id
        node_kind = node.kind()

        # -- class_declaration --
        if node_kind == "class_declaration":
            name = _get_name(node, source)
            if name:
                modifiers_node = _find_named_child(node, "modifier")
                modifiers = [_node_text(c, source) for c in _named_children(node) if c.kind() == "modifier"]
                visibility = _visibility_from_modifiers(modifiers)
                is_abstract = int("abstract" in modifiers)
                is_static = int("static" in modifiers)

                nid = add_node("class", name, node,
                               visibility=visibility,
                               is_abstract=is_abstract,
                               decorators=["static"] if is_static else [])

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("class")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- struct_declaration --
        elif node_kind == "struct_declaration":
            name = _get_name(node, source)
            if name:
                modifiers = [_node_text(c, source) for c in _named_children(node) if c.kind() == "modifier"]
                visibility = _visibility_from_modifiers(modifiers)

                nid = add_node("struct", name, node, visibility=visibility)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("struct")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- interface_declaration --
        elif node_kind == "interface_declaration":
            name = _get_name(node, source)
            if name:
                modifiers = [_node_text(c, source) for c in _named_children(node) if c.kind() == "modifier"]
                visibility = _visibility_from_modifiers(modifiers)

                nid = add_node("interface", name, node, visibility=visibility)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("interface")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- namespace_declaration --
        elif node_kind == "namespace_declaration":
            name = _get_name(node, source)
            if name:
                nid = add_node("namespace", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("namespace")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- method_declaration / constructor_declaration --
        elif node_kind in ("method_declaration", "constructor_declaration"):
            name = _get_name(node, source)
            if name:
                modifiers = [_node_text(c, source) for c in _named_children(node) if c.kind() == "modifier"]
                visibility = _visibility_from_modifiers(modifiers)
                is_static = int("static" in modifiers)
                is_constructor = node_kind == "constructor_declaration"
                kind = "constructor" if is_constructor else "method"

                nid = add_node(kind, name, node,
                               visibility=visibility,
                               decorators=["static"] if is_static else [])

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1,
                             target_text=f"{name_stack[-1]}::{name}" if name_stack else name)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append(kind)
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- field_declaration --
        elif node_kind == "field_declaration":
            # Find variable_declarator to get the field name
            var_decl = _find_named_child(node, "variable_declaration")
            if var_decl:
                declarator = _find_named_child(var_decl, "variable_declarator")
                if declarator:
                    field_name_node = _find_named_child(declarator, "identifier")
                    if field_name_node:
                        field_name = _node_text(field_name_node, source)
                        modifiers = [_node_text(c, source) for c in _named_children(node) if c.kind() == "modifier"]
                        visibility = _visibility_from_modifiers(modifiers)
                        is_static = int("static" in modifiers)
                        is_const = int("const" in modifiers)

                        nid = add_node("field", field_name, node,
                                       visibility=visibility,
                                       decorators=["static"] if is_static else [],
                                       is_const=is_const)

                        if node_stack:
                            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        # -- using_directive --
        elif node_kind == "using_directive":
            # Get the full namespace name from qualified_name or identifier
            qname_node = _find_named_child(node, "qualified_name")
            ident_node = _find_named_child(node, "identifier")
            if qname_node:
                module_name = _node_text(qname_node, source)
            elif ident_node:
                module_name = _node_text(ident_node, source)
            else:
                module_name = None

            if module_name:
                target_id = _hash_id(f"{file_path}::{module_name}", file_path)
                source_id = node_stack[-1] if node_stack else (file_id or "")
                if source_id:
                    add_edge(source_id, target_id, "imports",
                             node.start_position().row + 1,
                             target_text=module_name)

        # -- invocation_expression (method calls) --
        elif node_kind == "invocation_expression":
            if node_stack:
                # The first named child is the function/method being called
                for child in _named_children(node):
                    # Skip argument_list
                    if child.kind() == "argument_list":
                        continue
                    callee_name = _resolve_call_target(child, source)
                    if callee_name:
                        caller_id = node_stack[-1]
                        target_qname = f"{file_path}::{callee_name}"
                        target_id = _hash_id(target_qname, file_path)
                        add_edge(caller_id, target_id, "calls",
                                 node.start_position().row + 1,
                                 target_text=target_qname)
                    break

        # Recurse into children
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result


def _resolve_call_target(func_node, source: bytes) -> str | None:
    """Resolve the target name of a call expression."""
    if func_node.kind() == "identifier":
        return _node_text(func_node, source)
    elif func_node.kind() == "member_access_expression":
        # e.g., obj.Method() or service.CreateOrder()
        parts = []
        for child in _named_children(func_node):
            if child.kind() == "identifier":
                parts.append(_node_text(child, source))
        return ".".join(parts) if parts else None
    elif func_node.kind() == "generic_name":
        # e.g., List<string>
        ident = _find_named_child(func_node, "identifier")
        if ident:
            return _node_text(ident, source)
    return None
