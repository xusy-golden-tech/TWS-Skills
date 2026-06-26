"""Swift source extractor — walks tree-sitter CST for symbols and edges.

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


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


# ---------------------------------------------------------------------------
# Helpers to identify declaration type
# ---------------------------------------------------------------------------

def _declaration_keyword(node, source: bytes) -> str:
    """Return the keyword of a class_declaration: class, struct, enum, extension."""
    for child in _children(node):
        if not child.is_named():
            kw = _node_text(child, source)
            if kw in ("class", "struct", "enum", "extension"):
                return kw
    return "class"


def _extract_identifiers(node, source: bytes) -> list[str]:
    """Recursively extract all simple_identifier names from a node."""
    names: list[str] = []
    kind = node.kind()

    if kind == "simple_identifier":
        name = _node_text(node, source)
        if name and name != "_":
            names.append(name)
    else:
        for child in _named_children(node):
            names.extend(_extract_identifiers(child, source))
    return names


def visit_swift(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Swift source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []
    scope_kinds: list[str] = []  # track whether we're in class/struct/enum/extension/protocol

    # File-level ID for imports edges
    _file_id = _hash_id(file_path, file_path)

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
            "language": "swift",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": "internal",
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

    # -------------------------------------------------------------------
    # Visibility extraction
    # -------------------------------------------------------------------

    def _extract_visibility(node) -> str:
        """Extract visibility from modifiers."""
        mods = _find_named_child(node, "modifiers")
        if mods:
            vm = _find_named_child(mods, "visibility_modifier")
            if vm:
                vis_text = _node_text(vm, src_bytes)
                return vis_text
        return "internal"

    # -------------------------------------------------------------------
    # Import handling
    # -------------------------------------------------------------------

    def _visit_import_decl(node):
        # Extract the full import path from identifier children
        parts: list[str] = []
        _collect_import_path(node, parts)
        if parts:
            path_text = ".".join(parts)
            target_id = _hash_id(f"{file_path}::{path_text}", file_path)
            add_edge(_file_id, target_id, "imports",
                     node.start_position().row + 1, target_text=path_text)

    def _collect_import_path(node, parts: list[str]):
        """Recursively collect import path parts from identifier chain."""
        for child in _named_children(node):
            if child.kind() == "identifier":
                for sub in _named_children(child):
                    if sub.kind() == "simple_identifier":
                        parts.append(_node_text(sub, src_bytes))
            elif child.kind() == "simple_identifier":
                parts.append(_node_text(child, src_bytes))
            elif child.kind() == "navigation_expression":
                _collect_import_path(child, parts)

    # -------------------------------------------------------------------
    # Inheritance / conformance extraction
    # -------------------------------------------------------------------

    def _extract_inheritance(node, decl_node_id, decl_kw: str, base_line: int):
        """Extract extends/implements edges from inheritance_specifier children.

        For class declarations: first spec = extends, rest = implements.
        For struct/enum declarations: all specs = implements.
        For protocol declarations: all specs = extends.
        """
        specs = _find_all_named_children(node, "inheritance_specifier")
        if not specs:
            return

        for i, spec in enumerate(specs):
            ut = _find_named_child(spec, "user_type")
            if ut:
                tid = _find_named_child(ut, "type_identifier")
                if tid:
                    type_name = _node_text(tid, src_bytes)
                    target = f"{file_path}::{type_name}"

                    if decl_kw == "protocol":
                        edge_kind = "extends"
                    elif decl_kw == "class":
                        edge_kind = "extends" if i == 0 else "implements"
                    else:
                        # struct, enum, extension
                        edge_kind = "implements"

                    add_edge(decl_node_id, _hash_id(target, file_path),
                             edge_kind, base_line, target_text=type_name)

    # -------------------------------------------------------------------
    # Call expression extraction
    # -------------------------------------------------------------------

    def _extract_calls(body, caller_id):
        _walk_calls(body, caller_id)

    def _walk_calls(node, caller_id):
        if node.kind() == "call_expression":
            # Get the function name from simple_identifier
            fn = _find_named_child(node, "simple_identifier")
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target_text=target)
            else:
                # navigation_expression: obj.method()
                nav = _find_named_child(node, "navigation_expression")
                if nav:
                    # Get the last part (method name) from navigation_suffix
                    suffix = _find_named_child(nav, "navigation_suffix")
                    if suffix:
                        sid = _find_named_child(suffix, "simple_identifier")
                        if sid:
                            callee = _node_text(sid, src_bytes)
                            target = f"{file_path}::{callee}"
                            add_edge(caller_id, _hash_id(target, file_path),
                                     "calls", node.start_position().row + 1,
                                     target_text=target)

        for child in _named_children(node):
            _walk_calls(child, caller_id)

    # -------------------------------------------------------------------
    # Reads / writes extraction
    # -------------------------------------------------------------------

    def _extract_reads_writes(body_node, caller_id):
        _walk_reads_writes(body_node, caller_id)

    def _walk_reads_writes(node, caller_id):
        kind = node.kind()

        if kind == "assignment":
            left = _find_named_child(node, "directly_assignable_expression")
            right = _find_named_child(node, "additive_expression")

            # Check for compound assignment (+=, -=, etc.)
            is_compound = False
            for child in _children(node):
                if not child.is_named():
                    op = _node_text(child, src_bytes).strip()
                    if op in ("+=", "-=", "*=", "/=", "%="):
                        is_compound = True

            if left:
                for var_name in _extract_identifiers(left, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)
                    if is_compound:
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{var_name}", file_path),
                                 "reads", node.start_position().row + 1,
                                 target_text=var_name)
            if right:
                for var_name in _extract_identifiers(right, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "property_declaration":
            # let/var declarations: writes to the variable
            pattern = _find_named_child(node, "pattern")
            if pattern:
                for var_name in _extract_identifiers(pattern, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)
            # Read the right-hand side values
            value = _find_named_child(node, "call_expression")
            if value:
                for var_name in _extract_identifiers(value, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "control_transfer_statement":
            # return statements: reading the returned variable
            for child in _named_children(node):
                for var_name in _extract_identifiers(child, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "navigation_expression":
            # Reading obj.field: read both obj and field
            for var_name in _extract_identifiers(node, src_bytes):
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{var_name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=var_name)

        elif kind == "call_expression":
            # Reading function name and arguments
            fn = _find_named_child(node, "simple_identifier")
            args = _find_named_child(node, "call_suffix")
            if fn:
                for var_name in _extract_identifiers(fn, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)
            if args:
                for var_name in _extract_identifiers(args, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "simple_identifier":
            # Standalone identifier in expression context → read
            name = _node_text(node, src_bytes)
            if name and name != "_":
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=name)

        # Recurse into children
        for child in _named_children(node):
            _walk_reads_writes(child, caller_id)

    # -------------------------------------------------------------------
    # Main visitor
    # -------------------------------------------------------------------

    def _visit(node):
        kind = node.kind()

        if kind == "import_declaration":
            _visit_import_decl(node)
            return
        elif kind == "class_declaration":
            _visit_class_decl(node)
            return
        elif kind == "protocol_declaration":
            _visit_protocol_decl(node)
            return
        elif kind == "function_declaration":
            _visit_function_decl(node)
            return

        # Recurse into children
        for child in _named_children(node):
            _visit(child)

    def _visit_class_decl(node):
        # Determine the declaration keyword
        decl_kw = _declaration_keyword(node, src_bytes)
        base_line = node.start_position().row + 1

        # For extension: name comes from user_type
        if decl_kw == "extension":
            ut = _find_named_child(node, "user_type")
            if ut:
                tid = _find_named_child(ut, "type_identifier")
                if tid:
                    name = _node_text(tid, src_bytes)
                else:
                    return
            else:
                return
            tkind = "class"
        else:
            tid = _find_named_child(node, "type_identifier")
            if not tid:
                return
            name = _node_text(tid, src_bytes)

            if decl_kw == "enum":
                tkind = "enum"
            else:
                tkind = "class"  # class or struct

        vis = _extract_visibility(node)
        nid = add_node(tkind, name, node, visibility=vis)

        # Extract inheritance / protocol conformance
        _extract_inheritance(node, nid, decl_kw, base_line)

        # Find the body
        body = _find_named_child(node, "class_body")
        if not body:
            body = _find_named_child(node, "enum_class_body")

        if body:
            name_stack.append(name)
            node_stack.append(nid)
            scope_kinds.append(decl_kw)

            _visit_body(body, nid)

            scope_kinds.pop()
            node_stack.pop()
            name_stack.pop()

    def _visit_protocol_decl(node):
        tid = _find_named_child(node, "type_identifier")
        if not tid:
            return
        name = _node_text(tid, src_bytes)

        vis = _extract_visibility(node)
        nid = add_node("interface", name, node, visibility=vis)

        # Protocol inheritance
        _extract_inheritance(node, nid, "protocol", node.start_position().row + 1)

        body = _find_named_child(node, "protocol_body")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            scope_kinds.append("protocol")

            _visit_body(body, nid)

            scope_kinds.pop()
            node_stack.pop()
            name_stack.pop()

    def _visit_function_decl(node):
        name_node = _find_named_child(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        vis = _extract_visibility(node)

        # Determine if method or standalone function
        if scope_kinds:
            fkind = "method"
        else:
            fkind = "function"

        sig = _build_sig(node)
        nid = add_node(fkind, name, node, signature=sig, visibility=vis)

        # Edge from containing scope to this function
        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        body = _find_named_child(node, "function_body")
        if body:
            _extract_calls(body, nid)
            _extract_reads_writes(body, nid)

        name_stack.pop()
        node_stack.pop()

    def _visit_body(body, parent_id):
        """Visit the body of a class/struct/enum/extension/protocol."""
        for child in _named_children(body):
            kind = child.kind()
            if kind == "function_declaration":
                _visit_function_decl(child)
            elif kind == "protocol_function_declaration":
                _visit_protocol_function_decl(child)
            elif kind == "property_declaration":
                _visit_property_decl(child)
            elif kind == "enum_entry":
                _visit_enum_entry(child)
            elif kind == "statements":
                _visit_statements(child, parent_id)
            elif kind in ("class_declaration", "protocol_declaration"):
                _visit(child)

    def _visit_protocol_function_decl(node):
        name_node = _find_named_child(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        vis = _extract_visibility(node)
        sig = _build_sig(node)
        nid = add_node("method", name, node, signature=sig, visibility=vis)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _visit_property_decl(node):
        pattern = _find_named_child(node, "pattern")
        if not pattern:
            return
        name_node = _find_named_child(pattern, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        # Skip computed properties at the class level (they may have function_body)
        # Only create property node for stored properties or simple computed properties
        vis = _extract_visibility(node)
        nid = add_node("property", name, node, visibility=vis)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _visit_enum_entry(node):
        name_node = _find_named_child(node, "simple_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("enum_constant", name, node)
        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _visit_statements(statements_node, caller_id):
        """Visit statements in a function body (non class-level)."""
        for child in _named_children(statements_node):
            _extract_calls(child, caller_id)
            _extract_reads_writes(child, caller_id)

    # -------------------------------------------------------------------
    # Signature builder
    # -------------------------------------------------------------------

    def _build_sig(node) -> str:
        """Build a simple signature string from function parameters."""
        params = _find_all_named_children(node, "parameter")
        sig_parts = []
        for p in params:
            pname_node = _find_named_child(p, "simple_identifier")
            ptype_node = _find_named_child(p, "user_type")
            if pname_node and ptype_node:
                sig_parts.append(
                    f"{_node_text(pname_node, src_bytes)}: {_node_text(ptype_node, src_bytes)}"
                )
            elif pname_node:
                sig_parts.append(_node_text(pname_node, src_bytes))
            elif ptype_node:
                sig_parts.append(f"_: {_node_text(ptype_node, src_bytes)}")
        return f"({', '.join(sig_parts)})"

    # -------------------------------------------------------------------
    # Walk the tree
    # -------------------------------------------------------------------

    for child in _named_children(tree.root_node()):
        _visit(child)

    return result
