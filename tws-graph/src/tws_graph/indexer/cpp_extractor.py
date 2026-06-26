"""C++ source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
provenance="heuristic": no macro expansion, no template instantiation.
"""

from .base import ExtractionResult, hash_id, children as _children, named_children as _named_children


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _node_has_keyword(node, src_bytes, keyword):
    """Check if keyword appears in the declaration part of a function/declaration node."""
    text = src_bytes[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")
    # Strip trailing body/terminator to check only the declaration part
    for term_char in ('{', ';'):
        idx = text.find(term_char)
        if idx != -1:
            text = text[:idx]
    return keyword in text


# _children imported from .base (P50: cached)




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


def _find_identifier_deep(node, src_bytes: bytes):
    """Walk down declarators to find the first identifier."""
    if node.kind() in ("identifier", "field_identifier"):
        return node
    for child in _named_children(node):
        result = _find_identifier_deep(child, src_bytes)
        if result:
            return result
    return None


def _get_func_name(node, src_bytes: bytes) -> str | None:
    """Extract function name from function_definition."""
    declarator = _find_named_child(node, "function_declarator")
    if not declarator:
        declarator = _find_named_child(node, "declarator")
    if not declarator:
        return None

    id_node = _find_identifier_deep(declarator, src_bytes)
    if id_node:
        return _node_text(id_node, src_bytes)
    return None


def _get_type_name(node, src_bytes: bytes) -> str | None:
    """Extract type name from class_specifier / struct_specifier / enum_specifier."""
    id_node = _find_named_child(node, "type_identifier")
    if not id_node:
        id_node = _find_named_child(node, "identifier")
    if id_node:
        return _node_text(id_node, src_bytes)
    return None


def visit_cpp(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a C++ source file."""
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
            "language": "cpp",
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
        elif kind == "class_specifier":
            _visit_class(node)
        elif kind == "struct_specifier":
            _visit_struct(node)
        elif kind == "namespace_definition":
            _visit_namespace(node)
        elif kind == "template_declaration":
            _visit_template(node)
        elif kind == "enum_specifier":
            _visit_enum(node)
        elif kind == "declaration":
            _visit_declaration(node)

        # Recurse into children
        if kind not in ("function_definition", "class_specifier",
                        "struct_specifier", "enum_specifier",
                        "namespace_definition", "template_declaration",
                        "compound_statement", "field_declaration_list",
                        "enumerator_list"):
            for child in _named_children(node):
                _visit(child)

    def _visit_function(node):
        name = _get_func_name(node, src_bytes)
        if not name:
            return

        # If inside a class, qualify as method
        if name_stack and (scope_stack and scope_stack[-1] in ("class", "struct")):
            nid = add_node("method", name, node)
            class_name = name_stack[-1]

            # --- Instantiates: constructor detection ---
            if name == class_name:
                # This method is a constructor (same name as containing class/struct)
                if class_name in _class_node_ids:
                    add_edge(nid, _class_node_ids[class_name], "instantiates",
                             node.start_position().row + 1)

            # --- Virtual method detection (classes only) ---
            if scope_stack[-1] == "class" and _node_has_keyword(node, src_bytes, "virtual"):
                if class_name not in _class_virtual_methods:
                    _class_virtual_methods[class_name] = {}
                _class_virtual_methods[class_name][name] = nid

            # --- Overrides detection ---
            if class_name in _class_bases:
                for base_name in _class_bases[class_name]:
                    if (base_name in _class_virtual_methods
                            and name in _class_virtual_methods[base_name]):
                        add_edge(nid, _class_virtual_methods[base_name][name],
                                 "overrides", node.start_position().row + 1)
                        break

        else:
            nid = add_node("function", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        body = _find_child(node, "compound_statement")
        if body:
            _extract_calls(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_include(node):
        path_node = _find_named_child(node, "system_lib_string")
        if not path_node:
            path_node = _find_named_child(node, "string_literal")
        if not path_node:
            return

        path_text = _node_text(path_node, src_bytes)
        header = path_text.strip('"').strip("<>").strip()

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

    def _visit_class(node):
        name = _get_type_name(node, src_bytes)
        if not name:
            return
        nid = add_node("class", name, node)
        _class_node_ids[name] = nid

        # Extract base classes for override tracking
        base_clause = _find_named_child(node, "base_class_clause")
        if base_clause:
            bases = []
            for child in _named_children(base_clause):
                if child.kind() == "type_identifier":
                    bases.append(_node_text(child, src_bytes))
            _class_bases[name] = bases

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        body = _find_named_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            scope_stack.append("class")
            for child in _named_children(body):
                ckind = child.kind()
                if ckind == "field_declaration":
                    # tree-sitter puts virtual/pure-virtual method declarations
                    # in field_declaration nodes (not declaration nodes)
                    if _find_named_child(child, "function_declarator"):
                        _visit_declaration_in_class(child, nid)
                    else:
                        _visit_field_in_type(child, nid)
                elif ckind == "function_definition":
                    _visit_function(child)
                elif ckind == "declaration":
                    # Could be a method declaration or field
                    _visit_declaration_in_class(child, nid)
            scope_stack.pop()
            name_stack.pop()
            node_stack.pop()

    def _visit_struct(node):
        name = _get_type_name(node, src_bytes)
        if not name:
            return
        nid = add_node("struct", name, node)
        _class_node_ids[name] = nid

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        body = _find_named_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            scope_stack.append("struct")
            for child in _named_children(body):
                ckind = child.kind()
                if ckind == "field_declaration":
                    # tree-sitter puts constructor/method decls in field_declaration nodes
                    if _find_named_child(child, "function_declarator"):
                        _visit_declaration_in_class(child, nid)
                    else:
                        _visit_field_in_type(child, nid)
                elif ckind == "function_definition":
                    _visit_function(child)
                elif ckind == "declaration":
                    _visit_declaration_in_class(child, nid)
            scope_stack.pop()
            name_stack.pop()
            node_stack.pop()

    def _visit_namespace(node):
        name_node = _find_named_child(node, "namespace_identifier")
        if not name_node:
            name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("namespace", name, node)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        body = _find_named_child(node, "declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            scope_stack.append("namespace")
            for child in _named_children(body):
                _visit(child)
            scope_stack.pop()
            name_stack.pop()
            node_stack.pop()

    def _visit_template(node):
        # Collect template parameter names from template_parameter_list
        param_list = _find_named_child(node, "template_parameter_list")
        params = []
        if param_list:
            for child in _named_children(param_list):
                if child.kind() == "type_parameter_declaration":
                    id_node = _find_named_child(child, "type_identifier")
                    if id_node:
                        params.append(_node_text(id_node, src_bytes))

        tname = "<" + ", ".join(params) + ">" if params else "template<>"
        nid = add_node("template", tname, node,
                       is_abstract=1,
                       visibility="public")

        if node_stack:
            add_edge(node_stack[-1], nid, "contains",
                     node.start_position().row + 1)

        # --- type_ref edges for template parameters ---
        for pname in params:
            add_edge(nid, "", "type_ref", node.start_position().row + 1, pname)

        # Visit children of template (class/function/struct/declaration)
        for child in _named_children(node):
            if child.kind() in ("class_specifier", "function_definition",
                               "struct_specifier", "declaration"):
                name_stack.append(tname)
                node_stack.append(nid)
                _visit(child)
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
        # Check for function declarator first (method declarations like
        # "virtual double area() const = 0;" — pure virtual, no body).
        func_decl = _find_named_child(field_node, "function_declarator")
        if func_decl:
            id_node = _find_identifier_deep(func_decl, src_bytes)
            if id_node:
                mname = _node_text(id_node, src_bytes)
                mid = add_node("method", mname, field_node)
                add_edge(parent_id, mid, "contains", field_node.start_position().row + 1)

                class_name = name_stack[-1] if name_stack else ""
                # --- Instantiates: constructor detection ---
                if mname == class_name and class_name in _class_node_ids:
                    add_edge(mid, _class_node_ids[class_name], "instantiates",
                             field_node.start_position().row + 1)

                # --- Virtual method detection ---
                if (scope_stack and scope_stack[-1] == "class" and class_name
                        and _node_has_keyword(field_node, src_bytes, "virtual")):
                    if class_name not in _class_virtual_methods:
                        _class_virtual_methods[class_name] = {}
                    _class_virtual_methods[class_name][mname] = mid

                # --- Overrides detection ---
                if class_name and class_name in _class_bases:
                    for base_name in _class_bases[class_name]:
                        if (base_name in _class_virtual_methods
                                and mname in _class_virtual_methods[base_name]):
                            add_edge(mid, _class_virtual_methods[base_name][mname],
                                     "overrides", field_node.start_position().row + 1)
                            break
            return

        declarator = _find_named_child(field_node, "field_identifier")
        if not declarator:
            declarator = _find_named_child(field_node, "identifier")
        if not declarator:
            return
        fname = _node_text(declarator, src_bytes)
        fid = add_node("field", fname, field_node)
        add_edge(parent_id, fid, "contains", field_node.start_position().row + 1)

    def _visit_declaration_in_class(decl_node, parent_id):
        """Handle member declarations inside class/struct body."""
        # Check for function declarator (method declaration)
        func_decl = _find_named_child(decl_node, "function_declarator")
        if func_decl:
            id_node = _find_identifier_deep(func_decl, src_bytes)
            if id_node:
                mname = _node_text(id_node, src_bytes)
                mid = add_node("method", mname, decl_node)
                add_edge(parent_id, mid, "contains", decl_node.start_position().row + 1)

                class_name = name_stack[-1] if name_stack else ""
                # --- Instantiates: constructor detection ---
                if mname == class_name and class_name in _class_node_ids:
                    add_edge(mid, _class_node_ids[class_name], "instantiates",
                             decl_node.start_position().row + 1)

                # --- Virtual method detection from declaration ---
                if (scope_stack and scope_stack[-1] == "class" and class_name
                        and _node_has_keyword(decl_node, src_bytes, "virtual")):
                    if class_name not in _class_virtual_methods:
                        _class_virtual_methods[class_name] = {}
                    _class_virtual_methods[class_name][mname] = mid

                # --- Overrides detection from declaration ---
                if class_name and class_name in _class_bases:
                    for base_name in _class_bases[class_name]:
                        if (base_name in _class_virtual_methods
                                and mname in _class_virtual_methods[base_name]):
                            add_edge(mid, _class_virtual_methods[base_name][mname],
                                     "overrides", decl_node.start_position().row + 1)
                            break
            return

        # Plain field declaration
        declarator = _find_named_child(decl_node, "field_declarator")
        if not declarator:
            declarator = _find_named_child(decl_node, "identifier")
        if declarator:
            fname = _node_text(declarator, src_bytes)
            fid = add_node("field", fname, decl_node)
            add_edge(parent_id, fid, "contains", decl_node.start_position().row + 1)

    def _visit_declaration(node):
        """Handle global variable declarations at file/namespace scope."""
        if name_stack and scope_stack and scope_stack[-1] not in ("namespace",):
            # Inside a class/struct, handled by _visit_declaration_in_class
            return

        declarator = _find_named_child(node, "init_declarator")
        if declarator:
            id_node = _find_identifier_deep(declarator, src_bytes)
        else:
            id_node = _find_identifier_deep(node, src_bytes)

        if not id_node:
            return

        if _find_child(node, "function_declarator"):
            return

        name = _node_text(id_node, src_bytes)
        nid = add_node("variable", name, node)
        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

    def _extract_calls(body, caller_id, src_bytes):
        _walk_calls(body, caller_id, src_bytes)

    def _walk_calls(node, caller_id, src_bytes):
        if node.kind() == "call_expression":
            fn = _find_named_child(node, "identifier")
            if not fn:
                fe = _find_named_child(node, "field_expression")
                if fe:
                    fn = _find_named_child(fe, "field_identifier")
            if not fn:
                # template_function: identifier <...>
                tfn = _find_named_child(node, "template_function")
                if tfn:
                    fn = _find_named_child(tfn, "identifier")
            if not fn:
                # qualified_identifier: ns::func
                qi = _find_named_child(node, "qualified_identifier")
                if qi:
                    fn = _find_named_child(qi, "identifier")
                    if not fn:
                        fn = qi  # use the whole qualified name
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, hash_id(target, file_path), "calls",
                         node.start_position().row + 1, callee)

        for child in _named_children(node):
            _walk_calls(child, caller_id, src_bytes)

    # Need scope_stack for C++
    scope_stack: list[str] = []

    # State for override/constructor/template tracking
    _class_bases: dict[str, list[str]] = {}           # class_name -> [base_class_name]
    _class_virtual_methods: dict[str, dict[str, str]] = {}  # class_name -> {method_name: node_id}
    _class_node_ids: dict[str, str] = {}               # class_name -> node_id

    # Walk the tree
    for child in _named_children(tree.root_node()):
        _visit(child)

    return result
