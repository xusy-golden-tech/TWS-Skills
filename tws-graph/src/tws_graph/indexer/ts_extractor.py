"""TypeScript / TSX source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
from .parser import ExtractionResult


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _children(node):
    """Generator over all children of a node."""
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    """Generator over named children only."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _build_signature(func_node, source: bytes, name: str) -> str:
    """Build a human-readable function signature."""
    params_node = func_node.child_by_field_name("parameters")
    params = _node_text(params_node, source) if params_node else "()"

    returns = ""
    return_type = func_node.child_by_field_name("return_type")
    if return_type:
        type_text = _node_text(return_type, source)
        returns = f": {type_text}"

    return f"{name}{params}{returns}"


def _is_exported(node):
    """Check if a node is wrapped in an export_statement."""
    parent = node.parent()
    while parent:
        if parent.kind() == "export_statement":
            return True
        parent = parent.parent()
    return False


def _visibility_from_modifiers(node, source: bytes) -> str:
    """Extract visibility from TypeScript modifiers."""
    p = node.parent()
    if not p:
        return "public"
    for child in _children(p):
        if child.kind() in ("public", "private", "protected", "readonly"):
            text = _node_text(child, source)
            if text in ("public", "private", "protected"):
                return text
    return "public"


def visit_typescript(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a TypeScript/TSX CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    # Stack for building qualified names and contains edges
    name_stack: list[str] = []
    node_stack: list[str] = []
    node_id_set: set[str] = set()  # all node IDs created so far — O(1) lookup

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
            "language": "typescript",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _visibility_from_modifiers(node, source),
            "is_exported": int(_is_exported(node)),
            **extra,
        })
        node_id_set.add(nid)
        return nid

    def add_edge(source: str, target: str, kind: str, line: int, target_text: str | None = None):
        edge = {
            "source": source,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    def walk(node):
        node_kind = node.kind()

        # --- Function declarations ---
        if node_kind == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                sig = _build_signature(node, source, name)

                # Skip if it's inside a class body (handled as method_definition)
                p = node.parent()
                is_method = p and p.kind() in ("class_body", "object")

                if not is_method:
                    body_node = node.child_by_field_name("body")
                    body_text = _node_text(body_node, source) if body_node else ""
                    nid = add_node("function", name, node, signature=sig,
                                   body=body_text)

                    if node_stack:
                        add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                    name_stack.append(name)
                    node_stack.append(nid)
                    _walk_children(node)
                    node_stack.pop()
                    name_stack.pop()
                    return

        # --- Arrow functions assigned to variables ---
        elif node_kind == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node and value_node:
                if value_node.kind() in ("arrow_function", "function_expression"):
                    name = _node_text(name_node, source)
                    sig = _build_signature(value_node, source, name)

                    nid = add_node("function", name, value_node, signature=sig,
                                   body=_node_text(value_node, source))

                    if node_stack:
                        add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Module-level const/let/var declarations → variable nodes for config_link
                elif len(name_stack) == 0:
                    p = node.parent()
                    if p and p.kind() in ("lexical_declaration", "variable_declaration"):
                        name = _node_text(name_node, source)
                        # Only index uppercase or ALL_CAPS identifiers as config candidates
                        is_const = any(
                            c.kind() == "const" for c in _children(p)
                        )
                        add_node("variable", name, node,
                                 is_const=int(is_const),
                                 properties=f'{{"is_const":{str(is_const).lower()}}}')

        # --- Method definitions ---
        elif node_kind == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                sig = _build_signature(node, source, name)

                body_node = node.child_by_field_name("body")
                body_text = _node_text(body_node, source) if body_node else ""
                nid = add_node("method", name, node, signature=sig,
                               body=body_text)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                _walk_children(node)
                node_stack.pop()
                name_stack.pop()
                return

        # --- Class declarations ---
        elif node_kind == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                is_abstract = any(c.kind() == "abstract" for c in _children(node))

                nid = add_node("class", name, node, is_abstract=int(is_abstract))

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Extends / implements edges
                for child in _children(node):
                    if child.kind() in ("extends_clause", "implements_clause"):
                        for c in _children(child):
                            if c.is_named():
                                base_name = _node_text(c, source)
                                base_qname = f"{file_path}::{base_name}"
                                base_id = _hash_id(base_qname, file_path)
                                edge_kind = "extends" if child.kind() == "extends_clause" else "implements"
                                # Only emit edge if base type is defined in this file
                                add_edge(nid, base_id, edge_kind,
                                         node.start_position().row + 1,
                                         target_text=base_qname)

                name_stack.append(name)
                node_stack.append(nid)
                _walk_children(node)
                node_stack.pop()
                name_stack.pop()
                return

        # --- Enum declarations ---
        elif node_kind == "enum_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                nid = add_node("enum", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Extract enum members
                enum_body = node.child_by_field_name("body")
                if enum_body:
                    for child in _children(enum_body):
                        if child.kind() == "enum_member":
                            member_name_node = child.child_by_field_name("name")
                            if member_name_node:
                                member_name = _node_text(member_name_node, source)
                                name_stack.append(name)
                                node_stack.append(nid)
                                member_nid = add_node("enum_member", member_name, child)
                                add_edge(nid, member_nid, "contains", child.start_position().row + 1)
                                node_stack.pop()
                                name_stack.pop()

        # --- Interface declarations ---
        elif node_kind == "interface_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                nid = add_node("interface", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

        # --- Call expressions ---
        elif node_kind == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and node_stack:
                callee = _resolve_call_target(func_node, source)
                if callee:
                    caller_id = node_stack[-1]
                    target_qname = _resolve_qualified_target(callee, file_path)
                    target_id = _hash_id(target_qname, file_path)
                    add_edge(caller_id, target_id, "calls", node.start_position().row + 1,
                             target_text=target_qname)

                    # HTTP call detection (fetch, axios, etc.)
                    http_info = _detect_http_call_ts(callee)
                    if http_info:
                        http_target = f"{http_info['library']}.{http_info['method']}"
                        add_edge(caller_id, _hash_id(http_target, file_path),
                                 "http_calls", node.start_position().row + 1,
                                 target_text=http_target)

                    # gRPC detection
                    grpc_info = _detect_grpc_ts(callee)
                    if grpc_info:
                        kind, svc_name = grpc_info
                        grpc_target = svc_name or callee
                        add_edge(caller_id, _hash_id(grpc_target, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=grpc_target)

                    # Event emit detection
                    emit_info = _detect_emit_ts(callee)
                    if emit_info:
                        kind, event_name = emit_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

                    # Event listen detection
                    listen_info = _detect_listen_ts(callee)
                    if listen_info:
                        kind, event_name = listen_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

        # --- Env access (process.env.KEY) ---
        elif node_kind == "member_expression":
            if node_stack:
                # Fast-path: only inspect if object might start with "process"
                obj_node = node.child_by_field_name("object")
                if obj_node and obj_node.is_named():
                    env_var = _detect_env_access_ts(node, source)
                    if env_var:
                        caller_id = node_stack[-1]
                        add_edge(caller_id, _hash_id(env_var, file_path),
                                 "env_accesses", node.start_position().row + 1,
                                 target_text=env_var)

        # --- New expressions ---
        elif node_kind == "new_expression":
            func_node = node.child_by_field_name("constructor")
            if func_node and node_stack:
                callee = _resolve_call_target(func_node, source)
                if callee:
                    caller_id = node_stack[-1]
                    target_qname = _resolve_qualified_target(callee, file_path)
                    target_id = _hash_id(target_qname, file_path)
                    add_edge(caller_id, target_id, "calls", node.start_position().row + 1,
                             target_text=target_qname)

                    # gRPC detection (new expressions like `new GreeterClient()`)
                    grpc_info = _detect_grpc_ts(callee)
                    if grpc_info:
                        kind, svc_name = grpc_info
                        grpc_target = svc_name or callee
                        add_edge(caller_id, _hash_id(grpc_target, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=grpc_target)

                    # Event emit/listen detection
                    emit_info = _detect_emit_ts(callee)
                    if emit_info:
                        kind, event_name = emit_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

                    listen_info = _detect_listen_ts(callee)
                    if listen_info:
                        kind, event_name = listen_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

        # --- Recurse into children ---
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result


def _resolve_call_target(func_node, source: bytes) -> str | None:
    """Resolve the target name of a call/new expression."""
    if func_node.kind() == "identifier":
        return _node_text(func_node, source)
    elif func_node.kind() in ("member_expression", "property_access_expression"):
        parts = []
        for child in _children(func_node):
            if child.is_named() and child.kind() != "arguments":
                parts.append(_node_text(child, source))
        return ".".join(parts) if parts else None
    elif func_node.kind() == "call_expression":
        inner = func_node.child_by_field_name("function")
        if inner:
            inner_name = _resolve_call_target(inner, source)
            return f"{inner_name}()" if inner_name else None
    return None


# ---------------------------------------------------------------------------
# Env access detection (TypeScript/JavaScript)
# ---------------------------------------------------------------------------

def _detect_env_access_ts(node, source: bytes) -> str | None:
    """Detect process.env.KEY member expression and return the env var name."""
    obj = node.child_by_field_name("object")
    if not obj:
        return None
    # Fast string check: only member_expression or subscript_expression
    obj_kind = obj.kind()
    if obj_kind == "member_expression":
        # Quick check: obj's first child should be "process"
        first_child = obj.child_by_field_name("object")
        if not first_child or _node_text(first_child, source) != "process":
            return None
        prop = node.child_by_field_name("property")
        if prop:
            return _node_text(prop, source)
    elif obj_kind == "subscript_expression":
        obj_text = _node_text(obj, source)
        if obj_text == "process.env":
            prop = node.child_by_field_name("property")
            if prop:
                return _node_text(prop, source)
    return None


def _resolve_qualified_target(callee_name: str, file_path: str) -> str:
    """Best-effort qualified name for a call target."""
    if "." in callee_name:
        return callee_name.replace(".", "::")
    return f"{file_path}::{callee_name}"


# ---------------------------------------------------------------------------
# gRPC call detection (TypeScript/JavaScript)
# ---------------------------------------------------------------------------

def _detect_grpc_ts(callee_name: str) -> tuple[str, str] | None:
    """Detect gRPC call patterns in TS/JS. Returns (edge_kind, service_name) or None."""
    from .grpc_detect import detect_typescript_grpc
    return detect_typescript_grpc(callee_name)


def _detect_emit_ts(callee_name: str) -> tuple[str, str] | None:
    """Detect event emit patterns in TS/JS. Returns (edge_kind, event_name) or None."""
    from .event_detect import detect_ts_emit
    return detect_ts_emit(callee_name)


def _detect_listen_ts(callee_name: str) -> tuple[str, str] | None:
    """Detect event listener patterns in TS/JS. Returns (edge_kind, event_name) or None."""
    from .event_detect import detect_ts_listen
    return detect_ts_listen(callee_name)


# HTTP call detection (TypeScript/JavaScript)
# ---------------------------------------------------------------------------

_TS_HTTP_METHODS: dict[str, set[str]] = {
    "fetch": set(),  # global fetch(url), matches any call named "fetch"
    "axios": {"get", "post", "put", "delete", "patch", "head", "options", "request"},
    "got": {"get", "post", "put", "delete", "patch", "head"},
    "node-fetch": set(),  # import as 'fetch' — caught by 'fetch'
    "superagent": {"get", "post", "put", "delete", "patch", "head"},
}

# Pre-computed set for O(1) lookup
_KNOWN_TS_HTTP_CALLABLES: frozenset[str] = frozenset(
    f"{lib}.{method}"
    for lib, methods in _TS_HTTP_METHODS.items()
    for method in (methods if methods else ["__any__"])
)


def _detect_http_call_ts(callee_name: str) -> dict | None:
    """Detect if a TS/JS call expression is an HTTP library call."""
    # Global fetch
    if callee_name == "fetch":
        return {"library": "fetch", "method": "fetch"}

    if "." not in callee_name:
        return None

    # Fast O(1) lookup
    if callee_name in _KNOWN_TS_HTTP_CALLABLES:
        lib, method = callee_name.split(".", 1)
        return {"library": lib, "method": method}

    return None
