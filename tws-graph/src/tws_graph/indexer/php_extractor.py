"""PHP source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
Extracts class/interface/trait definitions, functions, methods, calls,
require/include statements, namespaces, use imports, PHP 8 attributes,
trait usage, and type annotations.
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
    """Get the name from a declaration node."""
    name_node = _find_named_child(node, "name")
    if name_node:
        return _node_text(name_node, source)
    return None


def _get_visibility_from_modifiers(node, source: bytes) -> str:
    """Extract visibility from visibility_modifier children."""
    for child in _named_children(node):
        if child.kind() == "visibility_modifier":
            return _node_text(child, source)
    return "public"


def _is_static_from_node(node, source: bytes) -> bool:
    """Check if a node has static_modifier."""
    for child in _named_children(node):
        if child.kind() == "static_modifier":
            return True
    return False


def visit_php(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a PHP CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    name_stack: list[str] = []
    node_stack: list[str] = []
    scope_kinds: list[str] = []
    file_id = ""  # Pre-declared; assigned after add_node is defined

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
            "language": "php",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            **extra,
        })
        return nid

    def add_edge(source_id: str, target: str, kind: str, line: int,
                 target_text: str | None = None, provenance: str = "tree-sitter"):
        edge = {
            "source": source_id,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": provenance,
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # Create a file-level node, after add_node is defined, for top-level
    # use declarations and require/include to have a parent scope.
    if root.named_child_count() > 0:
        file_id = add_node("file", file_path, root)

    def walk(node):
        nonlocal node_stack, name_stack, scope_kinds, file_id
        node_kind = node.kind()

        # -- class_declaration --
        if node_kind == "class_declaration":
            name = _get_name(node, source)
            if name:
                visibility = _get_visibility_from_modifiers(node, source)
                is_abstract = int(_find_named_child(node, "abstract_modifier") is not None)

                nid = add_node("class", name, node,
                               visibility=visibility,
                               is_abstract=is_abstract)

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

        # -- interface_declaration --
        elif node_kind == "interface_declaration":
            name = _get_name(node, source)
            if name:
                nid = add_node("interface", name, node)

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

        # -- trait_declaration --
        elif node_kind == "trait_declaration":
            name = _get_name(node, source)
            if name:
                nid = add_node("trait", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("trait")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- namespace_definition --
        elif node_kind == "namespace_definition":
            ns_name_node = _find_named_child(node, "namespace_name")
            if ns_name_node:
                ns_name = _node_text(ns_name_node, source)
                nid = add_node("namespace", ns_name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(ns_name)
                node_stack.append(nid)
                scope_kinds.append("namespace")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- function_definition (top-level function) --
        elif node_kind == "function_definition":
            name = _get_name(node, source)
            if name:
                # Check if parent is declaration_list inside a class
                p = node.parent()
                grand = p.parent() if p else None
                is_method = grand and grand.kind() in ("class_declaration", "interface_declaration", "trait_declaration")

                if not is_method:
                    nid = add_node("function", name, node)

                    if node_stack:
                        add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                    name_stack.append(name)
                    node_stack.append(nid)
                    scope_kinds.append("function")
                    _walk_children(node)
                    scope_kinds.pop()
                    node_stack.pop()
                    name_stack.pop()
                    return

        # -- method_declaration --
        elif node_kind == "method_declaration":
            name = _get_name(node, source)
            if name:
                visibility = _get_visibility_from_modifiers(node, source)
                is_static = int(_is_static_from_node(node, source))

                nid = add_node("method", name, node,
                               visibility=visibility,
                               decorators=["static"] if is_static else [])

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1,
                             target_text=f"{name_stack[-1]}::{name}" if name_stack else name)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("method")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- property_declaration --
        elif node_kind == "property_declaration":
            prop_elem = _find_named_child(node, "property_element")
            if prop_elem:
                var_name_node = _find_named_child(prop_elem, "variable_name")
                if var_name_node:
                    name_node = _find_named_child(var_name_node, "name")
                    if name_node:
                        prop_name = _node_text(name_node, source)
                        visibility = _get_visibility_from_modifiers(node, source)
                        is_static = int(_is_static_from_node(node, source))

                        nid = add_node("property", prop_name, node,
                                       visibility=visibility,
                                       decorators=["static"] if is_static else [])

                        if node_stack:
                            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                        # Don't push scope for properties, just walk children
                        _walk_children(node)
                        return

        # -- namespace_use_declaration (use Namespace\Class) --
        elif node_kind == "namespace_use_declaration":
            # Get the first use_clause
            use_clause = _find_named_child(node, "namespace_use_clause")
            if use_clause:
                qname_node = _find_named_child(use_clause, "qualified_name")
                if qname_node:
                    module_name = _node_text(qname_node, source)
                else:
                    name_node = _find_named_child(use_clause, "name")
                    if name_node:
                        module_name = _node_text(name_node, source)
                    else:
                        module_name = None

                if module_name:
                    target_id = _hash_id(f"{file_path}::{module_name}", file_path)
                    source_id = node_stack[-1] if node_stack else (file_id or "")
                    if source_id:
                        add_edge(source_id, target_id, "imports",
                                 node.start_position().row + 1,
                                 target_text=module_name)

        # -- use_declaration (trait usage inside class body) --
        elif node_kind == "use_declaration":
            # Only handle trait use when inside a class/trait scope
            source_id = node_stack[-1] if node_stack else (file_id or "")
            if source_id and scope_kinds and scope_kinds[-1] in ("class", "trait"):
                for child in _named_children(node):
                    if child.kind() == "name":
                        trait_name = _node_text(child, source)
                        if trait_name:
                            # Compute target qualified name at enclosing scope
                            # (trait defined at same level as the class)
                            parts = [file_path] + name_stack[:-1] + [trait_name]
                            trait_qname = "::".join(parts)
                            target_id = _hash_id(trait_qname, file_path)
                            add_edge(source_id, target_id, "implements",
                                     node.start_position().row + 1,
                                     target_text=trait_name,
                                     provenance="heuristic")
            _walk_children(node)
            return

        # -- attribute_list (PHP 8 #[...] attributes) --
        elif node_kind == "attribute_list":
            decorated_id = node_stack[-1] if node_stack else ""
            if decorated_id:
                for ac in _children(node):
                    if ac.kind() == "attribute_group":
                        attr_node = _find_named_child(ac, "attribute")
                        if attr_node:
                            attr_name_node = _find_named_child(attr_node, "name")
                            if attr_name_node:
                                attr_name = _node_text(attr_name_node, source)
                                if attr_name:
                                    add_edge(decorated_id,
                                             _hash_id(f"#{attr_name}", file_path),
                                             "decorates",
                                             node.start_position().row + 1,
                                             target_text=f"#{attr_name}",
                                             provenance="heuristic")
            _walk_children(node)
            return

        # -- named_type (type annotations like UserRequest, Cache) --
        elif node_kind == "named_type":
            source_id = node_stack[-1] if node_stack else ""
            if source_id:
                type_name_node = _find_named_child(node, "name")
                if not type_name_node:
                    type_name_node = _find_named_child(node, "qualified_name")
                if type_name_node:
                    type_name = _node_text(type_name_node, source)
                    if type_name:
                        add_edge(source_id,
                                 _hash_id(type_name, file_path),
                                 "type_ref",
                                 node.start_position().row + 1,
                                 target_text=type_name,
                                 provenance="heuristic")
            _walk_children(node)
            return

        # -- require_once_expression / require_expression / include_expression / include_once_expression --
        elif node_kind in ("require_once_expression", "require_expression",
                           "include_expression", "include_once_expression"):
            # Get the string argument
            str_node = _find_named_child(node, "string")
            if str_node:
                module_name = _node_text(str_node, source).strip('"\'')
                target_id = _hash_id(f"{file_path}::{module_name}", file_path)
                source_id = node_stack[-1] if node_stack else (file_id or "")
                if source_id:
                    add_edge(source_id, target_id, "imports",
                             node.start_position().row + 1,
                             target_text=module_name)

        # -- function_call_expression / member_call_expression --
        elif node_kind in ("function_call_expression", "member_call_expression"):
            callee_name = None
            source_id = node_stack[-1] if node_stack else (file_id or "")
            if not source_id:
                _walk_children(node)
                return

            if node_kind == "member_call_expression":
                # member_call_expression: variable_name, name, arguments
                obj_node = _find_named_child(node, "variable_name")
                method_name_node = _find_named_child(node, "name")
                if method_name_node:
                    method_name = _node_text(method_name_node, source)
                    obj_name = _node_text(obj_node, source) if obj_node else ""
                    callee_name = f"{obj_name}->{method_name}" if obj_name else method_name
            else:
                # function_call_expression: name or qualified_name, arguments
                # Check for member_access_expression (object->method)
                mae = _find_named_child(node, "member_access_expression")
                if mae:
                    obj_node = _find_named_child(mae, "variable_name")
                    method_name_node = _find_named_child(mae, "name")
                    if method_name_node:
                        method_name = _node_text(method_name_node, source)
                        obj_name = _node_text(obj_node, source) if obj_node else ""
                        callee_name = f"{obj_name}->{method_name}" if obj_name else method_name
                else:
                    # Simple function call
                    for child in _named_children(node):
                        if child.kind() in ("argument_list", "arguments"):
                            continue
                        if child.kind() == "name":
                            callee_name = _node_text(child, source)
                            break
                        elif child.kind() == "qualified_name":
                            callee_name = _node_text(child, source)
                            break

            if callee_name and source_id:
                target_qname = f"{file_path}::{callee_name}"
                target_id = _hash_id(target_qname, file_path)
                add_edge(source_id, target_id, "calls",
                         node.start_position().row + 1,
                         target_text=target_qname)

        # -- name_relative (parent class reference for use) --
        elif node_kind == "name":
            # Check if this is a standalone expression like trait use
            p = node.parent()
            if p and p.kind() == "namespace_use_clause":
                # Already handled above
                pass

        # Recurse into children
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result
