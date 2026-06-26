"""Go source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
import re
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


# ---------------------------------------------------------------------------
# Go built-in types — filtered out from type_ref edges
# ---------------------------------------------------------------------------
_GO_BUILTIN_TYPES = frozenset({
    "bool", "byte", "complex64", "complex128", "error",
    "float32", "float64",
    "int", "int8", "int16", "int32", "int64",
    "rune", "string",
    "uint", "uint8", "uint16", "uint32", "uint64",
    "uintptr", "interface",
})


def _extract_type_names_from_go_type(type_node, source: bytes) -> list[str]:
    """Extract user-defined type names from a Go type node, filtering built-ins.

    Handles: type_identifier, qualified_type, pointer_type, slice_type,
    map_type, array_type, parameter_list (for multi-return).
    """
    if type_node is None:
        return []

    kind = type_node.kind()

    if kind == "type_identifier":
        name = _node_text(type_node, source)
        return [] if name in _GO_BUILTIN_TYPES else [name]

    if kind == "qualified_type":
        return [_node_text(type_node, source)]

    if kind == "pointer_type":
        for child in _named_children(type_node):
            names = _extract_type_names_from_go_type(child, source)
            if names:
                return names
        return []

    if kind in ("slice_type", "array_type", "map_type"):
        names: list[str] = []
        for child in _named_children(type_node):
            names.extend(_extract_type_names_from_go_type(child, source))
        return names

    if kind == "parameter_list":
        # Multi-return: (T1, T2) or single-return in parens
        names: list[str] = []
        for child in _named_children(type_node):
            if child.kind() == "parameter_declaration":
                for c in _named_children(child):
                    names.extend(_extract_type_names_from_go_type(c, source))
        return names

    return []


def _extract_type_names_from_param(param_node, source: bytes) -> list[str]:
    """Extract type names from a parameter_declaration node.

    The named children are [identifier (optional), type_node].
    We skip the identifier and process the type node.
    """
    names: list[str] = []
    for child in _named_children(param_node):
        if child.kind() in (
            "type_identifier", "pointer_type", "qualified_type",
            "slice_type", "map_type", "array_type", "channel_type",
            "function_type",
        ):
            names.extend(_extract_type_names_from_go_type(child, source))
    return names


def _extract_identifiers(node, source: bytes) -> list[str]:
    """Recursively extract all identifier names from an expression node."""
    names: list[str] = []
    kind = node.kind()

    if kind == "identifier":
        name = _node_text(node, source)
        if name and name != "_":
            names.append(name)
    elif kind == "field_identifier":
        name = _node_text(node, source)
        if name and name != "_":
            names.append(name)
    else:
        for child in _named_children(node):
            names.extend(_extract_identifiers(child, source))
    return names


def _extract_receiver_type(receiver_node, source: bytes) -> str | None:
    """Extract the struct name from a method receiver parameter_list."""
    for child in _named_children(receiver_node):
        if child.kind() == "parameter_declaration":
            for tc in _named_children(child):
                if tc.kind() == "type_identifier":
                    return _node_text(tc, source)
                elif tc.kind() == "pointer_type":
                    for ptc in _named_children(tc):
                        if ptc.kind() == "type_identifier":
                            return _node_text(ptc, source)
    return None


def visit_go(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Go source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []

    # File-level ID for imports edges
    _file_id = _hash_id(file_path, file_path)

    # Track interface/struct info for implements detection (post-processing)
    _interfaces: dict[str, tuple[str, set[str]]] = {}  # name → (node_id, method_names)
    _structs: dict[str, str] = {}  # name → node_id
    _struct_methods: dict[str, set[str]] = {}  # struct_name → method_names

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

    # -------------------------------------------------------------------
    # Import handling (P51a)
    # -------------------------------------------------------------------
    def _visit_import_decl(node):
        for child in _children(node):
            if child.kind() == "import_spec":
                _process_import_spec(child)
            elif child.kind() == "import_spec_list":
                for spec in _children(child):
                    if spec.kind() == "import_spec":
                        _process_import_spec(spec)

    def _process_import_spec(node):
        path_node = node.child_by_field_name("path")
        if not path_node:
            return
        path_text = _node_text(path_node, src_bytes)
        # Strip surrounding quotes or backticks
        if len(path_text) >= 2 and path_text[0] in ('"', '`'):
            path_text = path_text[1:-1]

        target_id = _hash_id(f"{file_path}::{path_text}", file_path)
        add_edge(_file_id, target_id, "imports",
                 node.start_position().row + 1, target_text=path_text)

    # -------------------------------------------------------------------
    # Struct tag handling (P51c)
    # -------------------------------------------------------------------
    def _extract_struct_tag(field_id, tag_node, fc, src_bytes):
        """Parse Go struct tags and produce decorates edges."""
        tag_text = _node_text(tag_node, src_bytes)
        # Strip surrounding backticks
        if len(tag_text) >= 2 and tag_text[0] == '`':
            tag_text = tag_text[1:-1]

        # Parse key:"value" pairs.  Use a regex that handles quoted values.
        for m in re.finditer(r'(\w+)\s*:\s*"((?:[^"\\]|\\.)*)"', tag_text):
            key = m.group(1)
            value = m.group(2)
            target_id = _hash_id(f"{file_path}::{key}", file_path)
            add_edge(field_id, target_id, "decorates",
                     fc.start_position().row + 1,
                     target_text=f'{key}:"{value}"')

    # -------------------------------------------------------------------
    # Type reference extraction (P51d)
    # -------------------------------------------------------------------
    def _extract_func_type_refs(func_node, nid, src_bytes):
        """Extract type_ref edges from function/method parameters and results."""
        params = func_node.child_by_field_name("parameters")
        if params:
            for child in _named_children(params):
                if child.kind() == "parameter_declaration":
                    for type_name in _extract_type_names_from_param(child, src_bytes):
                        add_edge(nid,
                                 _hash_id(f"{file_path}::{type_name}", file_path),
                                 "type_ref", func_node.start_position().row + 1,
                                 target_text=type_name)

        result_node = func_node.child_by_field_name("result")
        if result_node:
            for type_name in _extract_type_names_from_go_type(result_node, src_bytes):
                add_edge(nid,
                         _hash_id(f"{file_path}::{type_name}", file_path),
                         "type_ref", func_node.start_position().row + 1,
                         target_text=type_name)

    # -------------------------------------------------------------------
    # Variable reads / writes extraction (P51e)
    # -------------------------------------------------------------------
    def _extract_reads_writes(body_node, caller_id, src_bytes):
        _walk_reads_writes(body_node, caller_id, src_bytes)

    def _walk_reads_writes(node, caller_id, src_bytes):
        """Walk function/method body to detect reads and writes."""
        kind = node.kind()

        if kind == "assignment_statement":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            operator_node = node.child_by_field_name("operator")

            # Compound assignment like +=, -=, etc. → both read and write on left
            is_compound = False
            if operator_node:
                op_text = _node_text(operator_node, src_bytes).strip()
                if op_text in ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                               "<<=", ">>=", "&^="):
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

        elif kind == "short_var_declaration":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left:
                for var_name in _extract_identifiers(left, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)
            if right:
                for var_name in _extract_identifiers(right, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind in ("inc_statement", "dec_statement"):
            # x++ or x-- → both read and write to x
            for child in _named_children(node):
                for var_name in _extract_identifiers(child, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "return_statement":
            for child in _named_children(node):
                for var_name in _extract_identifiers(child, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "call_expression":
            # Reading function name and arguments
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
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

        elif kind == "selector_expression":
            # Reading a field/method: obj.field
            for var_name in _extract_identifiers(node, src_bytes):
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{var_name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=var_name)

        # Recurse into children (skip blocks to avoid double-walking via _visit)
        for child in _named_children(node):
            _walk_reads_writes(child, caller_id, src_bytes)

    # -------------------------------------------------------------------
    # Interface implementation detection (P51b)
    # -------------------------------------------------------------------
    def _detect_implements():
        """Post-processing: match struct methods against interface methods."""
        for struct_name, struct_methods in _struct_methods.items():
            struct_id = _structs.get(struct_name)
            if not struct_id or not struct_methods:
                continue

            # Check direct interfaces
            for iface_name, (iface_id, iface_methods) in _interfaces.items():
                if not iface_methods:
                    continue
                if iface_methods.issubset(struct_methods):
                    add_edge(struct_id, iface_id, "implements", 0,
                             target_text=f"{file_path}::{iface_name}")

            # Also handle embedded interfaces: a struct implements all
            # interfaces whose methods are a subset of the struct's methods
            # (covers both direct and embedded interface chains)
            for iface_name, (iface_id, iface_methods) in _interfaces.items():
                if iface_name in _structs:
                    continue  # skip structs
                if not iface_methods:
                    continue
                # Already processed above
                if iface_methods.issubset(struct_methods):
                    continue  # already added

    # -------------------------------------------------------------------
    # Main visitor
    # -------------------------------------------------------------------
    def _visit(node):
        kind = node.kind()

        if kind == "import_declaration":
            _visit_import_decl(node)
            return  # fully handled, don't recurse
        elif kind == "function_declaration":
            _visit_function(node)
        elif kind == "method_declaration":
            _visit_method(node)
        elif kind == "type_declaration":
            _visit_type_decl(node)

        # Recurse into children for unhandled node types
        if kind not in ("function_declaration", "method_declaration",
                        "type_declaration", "block", "field_declaration_list",
                        "import_declaration"):
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

        # Type references (P51d)
        _extract_func_type_refs(node, nid, src_bytes)

        # Walk body for calls
        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)
            _extract_reads_writes(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_method(node):
        # Method: receiver, field_identifier, parameters, result, block?
        name_node = _find_named_child(node, "field_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        sig = _build_sig(node, src_bytes)
        nid = add_node("method", name, node, signature=sig)

        # Track receiver → struct for implements detection (P51b)
        receiver = node.child_by_field_name("receiver")
        if receiver:
            recv_type = _extract_receiver_type(receiver, src_bytes)
            if recv_type:
                if recv_type not in _struct_methods:
                    _struct_methods[recv_type] = set()
                _struct_methods[recv_type].add(name)

        if node_stack:
            add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        # Type references (P51d)
        _extract_func_type_refs(node, nid, src_bytes)

        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)
            _extract_reads_writes(body, nid, src_bytes)

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

        # Track structs for implements detection
        if tkind == "class":
            _structs[name] = nid
        elif tkind == "interface":
            # Collect interface method names (P51b)
            methods: set[str] = set()
            if body:
                for child in _named_children(body):
                    if child.kind() == "method_spec":
                        mn = _find_named_child(child, "field_identifier")
                        if mn:
                            methods.add(_node_text(mn, src_bytes))
                    elif child.kind() == "method_elem":
                        # Method element inside interface (tree-sitter variant)
                        mn = _find_named_child(child, "field_identifier")
                        if mn:
                            methods.add(_node_text(mn, src_bytes))
            _interfaces[name] = (nid, methods)

        # Process struct/interface body
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() in ("field_declaration", "field_declaration_list"):
                    for fc in _named_children(child):
                        if fc.kind() == "field_declaration":
                            fname_node = _find_named_child(fc, "field_identifier")
                            fn_id = None
                            if fname_node:
                                fn = _node_text(fname_node, src_bytes)
                                fn_id = _hash_id(make_qualified(fn), file_path)
                                add_node("property", fn, fc)
                                if node_stack:
                                    add_edge(node_stack[-1], fn_id,
                                             "contains", fc.start_position().row + 1)

                            # Extract struct tags → decorates edges (P51c)
                            tag_node = fc.child_by_field_name("tag")
                            if tag_node and fn_id:
                                _extract_struct_tag(fn_id, tag_node, fc, src_bytes)
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

    # -------------------------------------------------------------------
    # Walk the tree
    # -------------------------------------------------------------------
    for child in _named_children(tree.root_node()):
        _visit(child)

    # Post-processing: detect interface implementations (P51b)
    _detect_implements()

    return result


def _go_visibility(name: str) -> str:
    """Go visibility: uppercase first char → exported/public, lowercase → private."""
    if name and name[0].isupper():
        return "public"
    return "private"


def _build_sig(node, src_bytes) -> str:
    """Build a simple signature from parameter list.

    For function_declaration the first parameter_list is the params;
    for method_declaration the first is the receiver — prefer field access
    when available, fall back to first parameter_list child.
    """
    # Try named field access first (tree-sitter >= 0.25)
    params = node.child_by_field_name("parameters")
    if not params:
        # Fallback: first parameter_list child
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
