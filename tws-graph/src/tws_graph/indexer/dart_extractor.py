"""Dart source extractor — walks tree-sitter CST for symbols and edges.

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
# Dart built-in types — filtered out from type_ref edges
# ---------------------------------------------------------------------------
_DART_BUILTIN_TYPES = frozenset({
    "int", "double", "num", "bool", "String", "void", "dynamic",
    "Object", "Null", "Type", "Symbol", "Function", "Future",
    "Stream", "Iterable", "List", "Set", "Map", "Duration",
})


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def visit_dart(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Dart source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []

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
            "language": "dart",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _dart_visibility(simple_name),
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
    # Import handling
    # -------------------------------------------------------------------
    def _visit_import(node):
        """Process import_or_export → library_import → import_specification."""
        lib_import = _find_named_child(node, "library_import")
        if not lib_import:
            return
        spec = _find_named_child(lib_import, "import_specification")
        if not spec:
            return
        _process_import_spec(spec)

    def _process_import_spec(node):
        """Extract import path from import_specification."""
        config_uri = _find_named_child(node, "configurable_uri")
        if not config_uri:
            return
        uri_node = _find_named_child(config_uri, "uri")
        if not uri_node:
            return
        string_lit = _find_named_child(uri_node, "string_literal")
        if not string_lit:
            for kind in ("single_string_literal", "raw_string_literal", "string_literal"):
                string_lit = _find_child(uri_node, kind)
                if string_lit:
                    break
        if not string_lit:
            return

        path_text = _node_text(string_lit, src_bytes)
        if len(path_text) >= 2 and path_text[0] in ('"', "'"):
            path_text = path_text[1:-1]
        if len(path_text) >= 2 and path_text[0] in ('"', "'"):
            path_text = path_text[1:-1]

        target_id = _hash_id(f"{file_path}::{path_text}", file_path)
        add_edge(_file_id, target_id, "imports",
                 node.start_position().row + 1, target_text=path_text)

    # -------------------------------------------------------------------
    # Annotation handling
    # -------------------------------------------------------------------
    def _extract_annotations(node, target_nid: str):
        """Find annotation nodes and produce decorates edges."""
        for child in _children(node):
            if child.kind() == "annotation":
                _process_annotation(child, target_nid)

    def _process_annotation(anno_node, target_nid: str):
        """Process a single @name annotation."""
        name_node = _find_named_child(anno_node, "identifier")
        if not name_node:
            scoped = _find_named_child(anno_node, "scoped_identifier")
            if scoped:
                anno_name = _node_text(scoped, src_bytes)
            else:
                return
        else:
            anno_name = _node_text(name_node, src_bytes)

        target_text = f"@{anno_name}"
        add_edge(
            target_nid,
            _hash_id(target_text, file_path),
            "decorates",
            anno_node.start_position().row + 1,
            target_text=target_text,
        )

    # -------------------------------------------------------------------
    # Type reference extraction
    # -------------------------------------------------------------------
    def _extract_type_refs(node, caller_id: str):
        """Extract type_ref edges from type_identifier, type_arguments, type_parameters."""
        for child in _named_children(node):
            if child.kind() == "type_identifier":
                type_name = _node_text(child, src_bytes)
                if type_name not in _DART_BUILTIN_TYPES:
                    add_edge(
                        caller_id,
                        _hash_id(make_qualified(type_name), file_path),
                        "type_ref",
                        child.start_position().row + 1,
                        target_text=type_name,
                    )
            elif child.kind() == "type_arguments":
                for tc in _named_children(child):
                    if tc.kind() == "type_identifier":
                        type_name = _node_text(tc, src_bytes)
                        if type_name not in _DART_BUILTIN_TYPES:
                            add_edge(
                                caller_id,
                                _hash_id(make_qualified(type_name), file_path),
                                "type_ref",
                                tc.start_position().row + 1,
                                target_text=type_name,
                            )
            elif child.kind() == "type_parameters":
                for tp in _named_children(child):
                    if tp.kind() == "type_parameter":
                        for tpc in _named_children(tp):
                            if tpc.kind() == "type_identifier":
                                type_name = _node_text(tpc, src_bytes)
                                if type_name not in _DART_BUILTIN_TYPES:
                                    add_edge(
                                        caller_id,
                                        _hash_id(make_qualified(type_name), file_path),
                                        "type_ref",
                                        tpc.start_position().row + 1,
                                        target_text=type_name,
                                    )
            else:
                _extract_type_refs(child, caller_id)

    def _resolve_callee_name(node):
        """Resolve the call target name from a call expression node.

        Handles: simple calls init(), method calls obj.method(), constructors Foo().
        Returns the callee name or None.
        """
        ident = _find_named_child(node, "identifier")
        selectors = _find_all_named_children(node, "selector")

        if not selectors:
            return None

        # Find selector with argument_part
        has_call = False
        for sel in selectors:
            if _find_child(sel, "argument_part"):
                has_call = True
                break
        if not has_call:
            return None

        if not ident:
            return None

        # Check for method invocation (obj.method())
        for sel in selectors:
            uas = _find_named_child(sel, "unconditional_assignable_selector")
            if uas:
                field_id = _find_named_child(uas, "identifier")
                if field_id:
                    return _node_text(field_id, src_bytes)

        # Simple call or constructor: the first identifier is the callee
        return _node_text(ident, src_bytes)

    # -------------------------------------------------------------------
    # Call walking
    # -------------------------------------------------------------------
    def _walk_for_calls(node, caller_id):
        """Recursively walk a body node for call expressions."""
        if node.kind() == "expression_statement":
            callee = _resolve_callee_name(node)
            if callee:
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)
        for child in _named_children(node):
            _walk_for_calls(child, caller_id)

    # -------------------------------------------------------------------
    # Read/write walking
    # -------------------------------------------------------------------
    def _walk_for_reads_writes(node, caller_id):
        """Recursively walk a body node for reads and writes."""
        kind = node.kind()

        if kind == "assignment_expression":
            _handle_assignment(node, caller_id)

        elif kind == "local_variable_declaration":
            ivd = _find_named_child(node, "initialized_variable_definition")
            if ivd:
                var_id = _find_named_child(ivd, "identifier")
                if var_id:
                    var_name = _node_text(var_id, src_bytes)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)
                for child in _named_children(ivd):
                    if child.kind() not in ("identifier", "inferred_type"):
                        _extract_reads_from_expr(child, caller_id)

        elif kind == "return_statement":
            for child in _named_children(node):
                _extract_reads_from_expr(child, caller_id)

        elif kind == "expression_statement":
            for child in _named_children(node):
                if child.kind() == "identifier":
                    name = _node_text(child, src_bytes)
                    selectors = _find_all_named_children(node, "selector")
                    has_call = any(_find_child(s, "argument_part") for s in selectors)
                    if not has_call:
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{name}", file_path),
                                 "reads", child.start_position().row + 1,
                                 target_text=name)
                elif child.kind() == "assignment_expression":
                    _handle_assignment(child, caller_id)
                elif child.kind() == "selector":
                    # Check for method call and read the object
                    uas = _find_named_child(child, "unconditional_assignable_selector")
                    if uas and not _find_child(child, "argument_part"):
                        field_id = _find_named_child(uas, "identifier")
                        if field_id:
                            field_name = _node_text(field_id, src_bytes)
                            add_edge(caller_id,
                                     _hash_id(f"{file_path}::{field_name}", file_path),
                                     "reads", field_id.start_position().row + 1,
                                     target_text=field_name)
                else:
                    _extract_reads_from_expr(child, caller_id)

        elif kind == "unary_expression":
            for child in _named_children(node):
                if child.kind() == "identifier":
                    name = _node_text(child, src_bytes)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=name)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=name)
                elif child.kind() == "assignable_expression":
                    # postfix _value++ has assignable_expression with identifier
                    for gc in _named_children(child):
                        if gc.kind() == "identifier":
                            name = _node_text(gc, src_bytes)
                            add_edge(caller_id,
                                     _hash_id(f"{file_path}::{name}", file_path),
                                     "reads", gc.start_position().row + 1,
                                     target_text=name)
                else:
                    _extract_reads_from_expr(child, caller_id)

        elif kind == "unconditional_assignable_selector":
            # obj.field access (read of field)
            field_id = _find_named_child(node, "identifier")
            if field_id:
                field_name = _node_text(field_id, src_bytes)
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{field_name}", file_path),
                         "reads", field_id.start_position().row + 1,
                         target_text=field_name)

        elif kind == "identifier":
            name = _node_text(node, src_bytes)
            if name and name != "_":
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=name)

        for child in _named_children(node):
            _walk_for_reads_writes(child, caller_id)

    def _handle_assignment(node, caller_id):
        """Handle assignment expression → writes + reads edges."""
        ae = _find_named_child(node, "assignable_expression")
        if ae:
            for child in _named_children(ae):
                if child.kind() == "identifier":
                    var_name = _node_text(child, src_bytes)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", child.start_position().row + 1,
                             target_text=var_name)
                elif child.kind() == "unconditional_assignable_selector":
                    field_id = _find_named_child(child, "identifier")
                    if field_id:
                        field_name = _node_text(field_id, src_bytes)
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{field_name}", file_path),
                                 "writes", field_id.start_position().row + 1,
                                 target_text=field_name)
        named = list(_named_children(node))
        if len(named) >= 2:
            rhs = named[1]
            _extract_reads_from_expr(rhs, caller_id)

    def _extract_reads_from_expr(node, caller_id):
        """Recursively extract reads from an expression."""
        if node.kind() == "identifier":
            name = _node_text(node, src_bytes)
            if name and name != "_":
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=name)
        elif node.kind() == "unconditional_assignable_selector":
            field_id = _find_named_child(node, "identifier")
            if field_id:
                field_name = _node_text(field_id, src_bytes)
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{field_name}", file_path),
                         "reads", field_id.start_position().row + 1,
                         target_text=field_name)
        for child in _named_children(node):
            _extract_reads_from_expr(child, caller_id)

    # -------------------------------------------------------------------
    # Signature builder
    # -------------------------------------------------------------------
    def _build_sig(node) -> str:
        """Build a simple signature from formal_parameter_list."""
        params = _find_named_child(node, "formal_parameter_list")
        if not params:
            return "()"
        sig_parts = []
        for child in _named_children(params):
            if child.kind() == "formal_parameter":
                type_node = _find_named_child(child, "type_identifier")
                name_node = _find_named_child(child, "identifier")
                if type_node and name_node:
                    sig_parts.append(
                        f"{_node_text(type_node, src_bytes)} {_node_text(name_node, src_bytes)}"
                    )
                elif name_node:
                    sig_parts.append(_node_text(name_node, src_bytes))
            elif child.kind() == "this_initializer_parameter":
                name_node = _find_named_child(child, "identifier")
                if name_node:
                    sig_parts.append(f"this.{_node_text(name_node, src_bytes)}")
            elif child.kind() == "super_initializer_parameter":
                name_node = _find_named_child(child, "identifier")
                if name_node:
                    sig_parts.append(f"super.{_node_text(name_node, src_bytes)}")
        return f"({', '.join(sig_parts)})"

    # -------------------------------------------------------------------
    # Top-level function visitor
    # -------------------------------------------------------------------
    def _visit_top_function(func_sig, func_body):
        """Handle function_signature with optional function_body (sibling)."""
        name_node = _find_named_child(func_sig, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        sig = _build_sig(func_sig)
        nid = add_node("function", name, func_sig, signature=sig)

        _extract_type_refs(func_sig, nid)
        params = _find_named_child(func_sig, "formal_parameter_list")
        if params:
            _extract_type_refs(params, nid)

        name_stack.append(name)
        node_stack.append(nid)

        if func_body:
            _walk_for_calls(func_body, nid)
            _walk_for_reads_writes(func_body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Method visitor
    # -------------------------------------------------------------------
    def _visit_method(method_sig, class_nid, func_body):
        """Handle method_signature with optional function_body (sibling)."""
        # Try function_signature first
        func_sig = _find_named_child(method_sig, "function_signature")
        if func_sig:
            name_node = _find_named_child(func_sig, "identifier")
            if not name_node:
                return
            name = _node_text(name_node, src_bytes)
            sig = _build_sig(func_sig)
        else:
            # Check for getter_signature
            getter_sig = _find_named_child(method_sig, "getter_signature")
            if getter_sig:
                name_node = _find_named_child(getter_sig, "identifier")
                if not name_node:
                    return
                name = _node_text(name_node, src_bytes)
                sig = f"get {name}"
            else:
                # Check for setter_signature
                setter_sig = _find_named_child(method_sig, "setter_signature")
                if setter_sig:
                    name_node = _find_named_child(setter_sig, "identifier")
                    if not name_node:
                        return
                    name = _node_text(name_node, src_bytes)
                    sig = f"set {name}"
                else:
                    return

        is_abstract = 0 if func_body else 1

        nid = add_node("method", name, method_sig, signature=sig, is_abstract=is_abstract)

        if class_nid:
            add_edge(class_nid, nid, "contains", method_sig.start_position().row + 1)

        _extract_annotations(method_sig, nid)

        if func_sig:
            _extract_type_refs(func_sig, nid)
            params = _find_named_child(func_sig, "formal_parameter_list")
            if params:
                _extract_type_refs(params, nid)

        name_stack.append(name)
        node_stack.append(nid)

        if func_body and is_abstract == 0:
            _walk_for_calls(func_body, nid)
            _walk_for_reads_writes(func_body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Constructor visitor
    # -------------------------------------------------------------------
    def _visit_constructor(ctor_sig, class_nid, func_body):
        """Handle constructor_signature with optional function_body (sibling)."""
        name_node = _find_named_child(ctor_sig, "identifier")
        if not name_node:
            name = "new"
        else:
            name = _node_text(name_node, src_bytes)

        sig = _build_sig(ctor_sig)
        nid = add_node("constructor", name, ctor_sig, signature=sig)

        if class_nid:
            add_edge(class_nid, nid, "contains", ctor_sig.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        if func_body:
            _walk_for_calls(func_body, nid)
            _walk_for_reads_writes(func_body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Class visitor
    # -------------------------------------------------------------------
    def _visit_class(node):
        """Handle class_definition."""
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        is_abstract = 1 if _find_child(node, "abstract") else 0

        nid = add_node("class", name, node, is_abstract=is_abstract)
        name_stack.append(name)
        node_stack.append(nid)

        _extract_annotations(node, nid)
        _extract_type_refs(node, nid)

        # Extends + with (mixins)
        sc = _find_named_child(node, "superclass")
        if sc:
            for child in _named_children(sc):
                if child.kind() == "type_identifier":
                    super_name = _node_text(child, src_bytes)
                    add_edge(nid,
                             _hash_id(make_qualified(super_name), file_path),
                             "extends", child.start_position().row + 1,
                             target_text=super_name)
                elif child.kind() == "mixins":
                    for mc in _named_children(child):
                        if mc.kind() == "type_identifier":
                            mixin_name = _node_text(mc, src_bytes)
                            add_edge(nid,
                                     _hash_id(make_qualified(mixin_name), file_path),
                                     "implements", mc.start_position().row + 1,
                                     target_text=mixin_name)

        # Implements
        ifaces = _find_named_child(node, "interfaces")
        if ifaces:
            for child in _named_children(ifaces):
                if child.kind() == "type_identifier":
                    iface_name = _node_text(child, src_bytes)
                    add_edge(nid,
                             _hash_id(make_qualified(iface_name), file_path),
                             "implements", child.start_position().row + 1,
                             target_text=iface_name)

        # Body
        body = _find_named_child(node, "class_body")
        if body:
            _walk_body(body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Mixin visitor
    # -------------------------------------------------------------------
    def _visit_mixin(node):
        """Handle mixin_declaration."""
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        nid = add_node("class", name, node)
        name_stack.append(name)
        node_stack.append(nid)

        _extract_annotations(node, nid)

        body = _find_named_child(node, "class_body")
        if body:
            _walk_body(body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Enum visitor
    # -------------------------------------------------------------------
    def _visit_enum(node):
        """Handle enum_declaration."""
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        nid = add_node("enum", name, node)
        name_stack.append(name)
        node_stack.append(nid)

        body = _find_named_child(node, "enum_body")
        if body:
            for child in _named_children(body):
                if child.kind() == "enum_constant":
                    const_name_node = _find_named_child(child, "identifier")
                    if const_name_node:
                        const_name = _node_text(const_name_node, src_bytes)
                        const_nid = add_node("enum_constant", const_name, child)
                        add_edge(nid, const_nid, "contains",
                                 child.start_position().row + 1)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Extension visitor
    # -------------------------------------------------------------------
    def _visit_extension(node):
        """Handle extension_declaration."""
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        nid = add_node("class", name, node)
        name_stack.append(name)
        node_stack.append(nid)

        body = _find_named_child(node, "class_body")
        if body:
            _walk_body(body, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Field/Property visitor
    # -------------------------------------------------------------------
    def _visit_field(node, class_nid):
        """Handle declaration in body (field, abstract method, or top-level var)."""
        # Check if this is an abstract method (function_signature without method_signature)
        func_sig = _find_named_child(node, "function_signature")
        if func_sig:
            name_node = _find_named_child(func_sig, "identifier")
            if name_node:
                name = _node_text(name_node, src_bytes)
                sig = _build_sig(func_sig)
                nid = add_node("method", name, func_sig, signature=sig, is_abstract=1)
                if class_nid:
                    add_edge(class_nid, nid, "contains", node.start_position().row + 1)
                _extract_type_refs(func_sig, nid)
                params = _find_named_child(func_sig, "formal_parameter_list")
                if params:
                    _extract_type_refs(params, nid)
            return

        # Field declaration
        iil = _find_named_child(node, "initialized_identifier_list")
        if iil:
            for child in _named_children(iil):
                if child.kind() == "initialized_identifier":
                    fname_node = _find_named_child(child, "identifier")
                    if fname_node:
                        fname = _node_text(fname_node, src_bytes)
                        fnid = add_node("property", fname, child)
                        if class_nid:
                            add_edge(class_nid, fnid, "contains",
                                     child.start_position().row + 1)

    # -------------------------------------------------------------------
    # Body walker (handles sibling signature + function_body pairs)
    # -------------------------------------------------------------------
    def _walk_body(body_node, parent_nid):
        """Walk a body node (class_body, program level) handling signature+body pairs."""
        children = list(_named_children(body_node))
        i = 0
        while i < len(children):
            child = children[i]

            # Peek at next sibling for function_body
            func_body = None
            if i + 1 < len(children) and children[i + 1].kind() == "function_body":
                func_body = children[i + 1]

            cn = child.kind()

            if cn == "method_signature":
                _visit_method(child, parent_nid, func_body)
                if func_body:
                    i += 1
            elif cn == "constructor_signature":
                _visit_constructor(child, parent_nid, func_body)
                if func_body:
                    i += 1
            elif cn == "declaration":
                _visit_field(child, parent_nid)
            elif cn == "annotation":
                _process_annotation(child, parent_nid)
            elif cn == "class_definition":
                _visit_class(child)
            elif cn == "mixin_declaration":
                _visit_mixin(child)
            elif cn == "enum_declaration":
                _visit_enum(child)
            elif cn == "extension_declaration":
                _visit_extension(child)
            elif cn == "function_signature":
                _visit_top_function(child, func_body)
                if func_body:
                    i += 1
            elif cn == "import_or_export":
                _visit_import(child)
            # Other nodes (like function_body, block, etc.) are handled by their consumers

            i += 1

    # -------------------------------------------------------------------
    # Walk the program root
    # -------------------------------------------------------------------
    root = tree.root_node()
    _walk_body(root, None)

    return result


def _dart_visibility(name: str) -> str:
    """Dart visibility: _ prefix → private, otherwise public."""
    if name and name.startswith("_"):
        return "private"
    return "public"
