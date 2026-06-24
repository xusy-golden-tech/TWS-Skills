"""Python source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API:
  node.kind()  (not node.type)
  child_count(), child(i)  (not node.children)
  start_position().row  (not start_point[0])
  start_byte(), end_byte()  (methods, not attributes)
"""

import hashlib
from functools import lru_cache

from .parser import ExtractionResult


@lru_cache(maxsize=4096)
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


def _get_docstring(body_node, source: bytes) -> str:
    """Extract docstring from the first string expression in a block."""
    for child in _children(body_node):
        if child.kind() == "expression_statement":
            # Look for a string literal inside
            for c in _named_children(child):
                if c.kind() == "string":
                    text = _node_text(c, source)
                    return text[:200]
        break
    return ""


def _build_signature(func_node, source: bytes, name: str) -> str:
    """Build a human-readable function signature."""
    params = ""
    params_node = func_node.child_by_field_name("parameters")
    if params_node:
        params = _node_text(params_node, source)

    returns = ""
    return_type = func_node.child_by_field_name("return_type")
    if return_type:
        returns = f" -> {_node_text(return_type, source)}"

    return f"{name}{params}{returns}"


def _extract_decorators(decorated_node, source: bytes) -> list[str]:
    """Extract decorator strings from a decorated_definition."""
    decs = []
    for child in _children(decorated_node):
        if child.kind() == "decorator":
            decs.append(_node_text(child, source))
    return decs


# Built-in types that should NOT produce type_ref edges
_BUILTIN_TYPES = frozenset({
    "int", "str", "float", "bool", "list", "dict", "tuple", "set",
    "bytes", "complex", "type", "object", "None", "NoneType",
    "Any", "Optional", "Union", "Callable", "Iterable", "Iterator",
    "Generator", "Coroutine", "Awaitable", "Protocol", "TypedDict",
    "Literal", "Final", "ClassVar", "TypeVar", "TypeGuard",
    "Self", "NoReturn", "Never", "TypeAlias",
    # TypeScript/JavaScript
    "number", "string", "boolean", "void", "undefined", "null",
    "any", "unknown", "never", "object", "Array", "Map", "Set",
    "Promise", "Record", "Partial", "Required", "Readonly",
})


def _extract_type_names(type_node, source: bytes) -> list[str]:
    """Extract type names from a type annotation node, filtering built-ins.

    Handles:
      - ``type`` wrapper node (Python tree-sitter wraps annotations)
      - Simple identifier types: ``int``, ``MyClass``
      - Subscript types: ``List[str]`` → ``List``
      - Attribute types: ``module.Type`` → ``module.Type``
      - Generic types: ``Optional[str]`` → ``Optional``
      - Union types: ``int | str`` → each part
    """
    if type_node is None:
        return []

    kind = type_node.kind()

    # Python tree-sitter wraps type annotations in a ``type`` node
    if kind == "type":
        names = []
        for child in _children(type_node):
            if child.is_named():
                names.extend(_extract_type_names(child, source))
        return names

    if kind == "identifier":
        name = _node_text(type_node, source)
        return [] if name in _BUILTIN_TYPES else [name]

    if kind == "attribute":
        return [_node_text(type_node, source)]

    if kind in ("subscript", "generic_type"):
        # Extract the base type (first child)
        for child in _children(type_node):
            if child.is_named():
                return _extract_type_names(child, source)
        return []

    if kind in ("union_type", "binary_operator"):
        names = []
        for child in _children(type_node):
            if child.is_named():
                names.extend(_extract_type_names(child, source))
        return names

    if kind == "splat_type":
        for child in _children(type_node):
            if child.is_named():
                return _extract_type_names(child, source)
        return []

    return []


def _visibility_from_name(name: str) -> str:
    """Heuristic: names starting with _ are private, __ are name-mangled."""
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


def visit_python(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a Python CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    # Stack for building qualified names and contains edges
    name_stack: list[str] = []   # [ClassName, NestedClass, ...]
    node_stack: list[str] = []   # node IDs for contains edges
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
            "language": "python",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _visibility_from_name(simple_name),
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
        """Recursively walk the CST, extracting symbols and edges."""
        node_kind = node.kind()

        # --- Symbol definitions ---
        if node_kind == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                # Check if it's a method (parent is class body)
                p = node.parent()
                is_method = p and p.parent() and p.parent().kind() == "class_definition"
                kind = "method" if is_method else "function"
                sig = _build_signature(node, source, name)
                body_node = node.child_by_field_name("body")
                doc = _get_docstring(body_node, source) if body_node else ""

                body_text = _node_text(body_node, source) if body_node else ""
                nid = add_node(kind, name, node,
                               signature=sig, docstring=doc,
                               body=body_text)

                # Type annotation references (P26d)
                param_node = node.child_by_field_name("parameters")
                if param_node:
                    for child in _children(param_node):
                        if child.kind() in ("typed_parameter", "typed_default_parameter",
                                             "list_splat_pattern", "dictionary_splat_pattern"):
                            type_node = child.child_by_field_name("type")
                            for type_name in _extract_type_names(type_node, source):
                                add_edge(nid, _hash_id(type_name, file_path),
                                         "type_ref", node.start_position().row + 1,
                                         target_text=type_name)
                # Return type
                return_type_node = node.child_by_field_name("return_type")
                for type_name in _extract_type_names(return_type_node, source):
                    add_edge(nid, _hash_id(type_name, file_path),
                             "type_ref", node.start_position().row + 1,
                             target_text=type_name)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Push for nested definitions, then recurse body
                name_stack.append(name)
                node_stack.append(nid)
                _walk_children(node)
                node_stack.pop()
                name_stack.pop()
                return

        elif node_kind == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(name_node, source)
                # Check for abstractmethod decorator
                p = node.parent()
                is_abstract = False
                if p:
                    for c in _children(p):
                        if c.kind() == "decorator" and "abstractmethod" in _node_text(c, source):
                            is_abstract = True
                            break

                nid = add_node("class", name, node, is_abstract=int(is_abstract))

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Extends / implements edges
                bases_node = node.child_by_field_name("superclasses")
                if bases_node:
                    for child in _children(bases_node):
                        if child.is_named():
                            base = _node_text(child, source)
                            base_qname = f"{file_path}::{base}"
                            base_id = _hash_id(base_qname, file_path)
                            # Determine edge kind: ABC/Protocol → implements
                            edge_kind = "extends"
                            if (base == "ABC" or base == "ABCMeta"
                                    or base.endswith("Protocol")
                                    or base in ("Interface", "AbstractBase")):
                                edge_kind = "implements"
                            add_edge(nid, base_id, edge_kind,
                                     node.start_position().row + 1,
                                     target_text=base_qname)

                name_stack.append(name)
                node_stack.append(nid)
                _walk_children(node)
                node_stack.pop()
                name_stack.pop()
                return

        elif node_kind == "decorated_definition":
            decs = _extract_decorators(node, source)
            # Find and walk the actual definition child
            for child in _children(node):
                if child.kind() in ("function_definition", "class_definition"):
                    walk(child)
                    # Tag the last added node with these decorators
                    if result.nodes and decs:
                        result.nodes[-1]["decorators"] = decs
                        decorated_id = result.nodes[-1]["id"]
                        # Create decorates edges for each decorator
                        for dec in decs:
                            add_edge(decorated_id,
                                     _hash_id(dec, file_path),
                                     "decorates",
                                     node.start_position().row + 1,
                                     target_text=dec)
                        # Check for event listener decorators (e.g. @receiver, @on_click)
                        from .event_detect import is_python_listen_decorator
                        for dec in decs:
                            if is_python_listen_decorator(dec):
                                add_edge(decorated_id,
                                         _hash_id(dec, file_path),
                                         "listens_on",
                                         node.start_position().row + 1,
                                         target_text=dec)
                                break
                    return

        # --- Subscript expressions (for os.environ['KEY']) ---
        elif node_kind == "subscript":
            value_node = node.child_by_field_name("value")
            # Fast-path: only inspect attribute subscripts (os.environ[...])
            if value_node and value_node.kind() == "attribute" and node_stack:
                subscript_node = node.child_by_field_name("subscript")
                if subscript_node:
                    env_var = _detect_environ_subscript(value_node, subscript_node, source)
                    if env_var:
                        caller_id = node_stack[-1]
                        add_edge(caller_id, _hash_id(env_var, file_path),
                                 "env_accesses", node.start_position().row + 1,
                                 target_text=env_var)

        # --- Module-level variable assignments (for config_link detection) ---
        elif node_kind == "assignment":
            # Only process at module level (outside classes/functions)
            if len(name_stack) == 0:
                lhs = node.child_by_field_name("left")
                if lhs and lhs.kind() == "identifier":
                    var_name = _node_text(lhs, source)
                    if var_name and var_name.upper() == var_name and any(c.isalpha() for c in var_name):
                        add_node("variable", var_name, node,
                                 is_const=True, properties='{"is_const":true}')

        # --- Call expressions ---
        elif node_kind == "call":
            func_node = node.child_by_field_name("function")
            if func_node and node_stack:
                callee_name = _resolve_call_target(func_node, source)
                if callee_name:
                    caller_id = node_stack[-1]
                    target_qname = _resolve_qualified_target(callee_name, file_path)
                    target_id = _hash_id(target_qname, file_path)
                    add_edge(caller_id, target_id, "calls", node.start_position().row + 1,
                             target_text=target_qname)

                    # HTTP call detection
                    http_info = _detect_http_call(callee_name, node, source)
                    if http_info:
                        http_target = f"{http_info['library']}.{http_info['method']}"
                        add_edge(caller_id, _hash_id(http_target, file_path),
                                 "http_calls", node.start_position().row + 1,
                                 target_text=http_target)

                    # Env access detection
                    env_info = _detect_env_access(callee_name, node, source)
                    if env_info:
                        add_edge(caller_id, _hash_id(env_info, file_path),
                                 "env_accesses", node.start_position().row + 1,
                                 target_text=env_info)

                    # gRPC detection
                    grpc_info = _detect_grpc(callee_name, "python")
                    if grpc_info:
                        kind, svc_name = grpc_info
                        grpc_target = svc_name or callee_name
                        add_edge(caller_id, _hash_id(grpc_target, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=grpc_target)

                    # Event emit detection
                    emit_info = _detect_emit(callee_name, "python")
                    if emit_info:
                        kind, event_name = emit_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

                    # Event listen detection
                    listen_info = _detect_listen(callee_name, "python")
                    if listen_info:
                        kind, event_name = listen_info
                        add_edge(caller_id, _hash_id(event_name, file_path),
                                 kind, node.start_position().row + 1,
                                 target_text=event_name)

        # --- Import statements ---
        elif node_kind == "import_statement":
            for child in _children(node):
                if child.kind() == "dotted_name":
                    module = _node_text(child, source)
                    if node_stack:
                        target_id = _hash_id(f"{file_path}::{module}", file_path)
                        add_edge(node_stack[-1], target_id, "imports", node.start_position().row + 1,
                                 target_text=module)

        elif node_kind == "import_from_statement":
            module_name = None
            module_node = node.child_by_field_name("module_name")
            if module_node:
                module_name = _node_text(module_node, source)

            for child in _children(node):
                imported_name = None
                if child.kind() == "dotted_name":
                    # Skip if this is the module_name node (the "from" part)
                    if module_name and _node_text(child, source) == module_name:
                        continue
                    imported_name = _node_text(child, source)
                elif child.kind() == "aliased_import":
                    name_child = child.child_by_field_name("name")
                    if name_child:
                        imported_name = _node_text(name_child, source)
                elif child.kind() == "wildcard_import":
                    imported_name = "*"

                if not imported_name:
                    continue

                full_name = f"{module_name}.{imported_name}" if module_name else imported_name
                if node_stack:
                    target_id = _hash_id(f"{file_path}::{full_name}", file_path)
                    add_edge(node_stack[-1], target_id, "imports", node.start_position().row + 1,
                             target_text=full_name)

        # --- Recurse into children ---
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result


def _resolve_call_target(func_node, source: bytes) -> str | None:
    """Resolve the target name of a call expression (best effort)."""
    if func_node.kind() == "identifier":
        return _node_text(func_node, source)
    elif func_node.kind() == "attribute":
        # e.g., self.method() or obj.method()
        parts = []
        for child in _children(func_node):
            if child.is_named():
                parts.append(_node_text(child, source))
        return ".".join(parts) if parts else None
    return None


# ---------------------------------------------------------------------------
# Env access detection
# ---------------------------------------------------------------------------

_KNOWN_ENV_FUNCTIONS = {
    "os.getenv",
    "os.environ.get",
    "os.environ.__getitem__",
}


def _detect_env_access(callee_name: str, call_node, source: bytes) -> str | None:
    """Detect env-var access via os.getenv / os.environ.get and return the var name."""
    if callee_name in _KNOWN_ENV_FUNCTIONS:
        # Try to extract the first string argument
        args_node = call_node.child_by_field_name("arguments")
        if args_node:
            for child in _children(args_node):
                if child.kind() == "string":
                    text = _node_text(child, source)
                    # Strip quotes
                    return text[1:-1] if len(text) >= 2 else text
    return None


def _detect_environ_subscript(value_node, subscript_node, source: bytes) -> str | None:
    """Detect os.environ['KEY'] style access."""
    # value_node should be an attribute: os.environ
    if value_node.kind() == "attribute":
        parts = []
        for child in _children(value_node):
            if child.is_named():
                parts.append(_node_text(child, source))
        if ".".join(parts) == "os.environ":
            # subscript_node is the key: 'KEY' or "KEY"
            if subscript_node.kind() == "string":
                text = _node_text(subscript_node, source)
                return text[1:-1] if len(text) >= 2 else text
    return None


def _resolve_qualified_target(callee_name: str, file_path: str) -> str:
    """Best-effort qualified name for a call target."""
    if "." in callee_name:
        return callee_name.replace(".", "::")
    return f"{file_path}::{callee_name}"


# ---------------------------------------------------------------------------
# HTTP call detection
# ---------------------------------------------------------------------------

_HTTP_LIBRARIES: dict[str, set[str]] = {
    "requests": {"get", "post", "put", "delete", "patch", "head", "options", "request"},
    "httpx": {"get", "post", "put", "delete", "patch", "head", "options", "request", "stream"},
    "urllib.request": {"urlopen"},
    "aiohttp": {},  # ClientSession methods are harder to detect statically
}

# Pre-computed set of all known HTTP callable names for O(1) lookup
_KNOWN_HTTP_CALLABLES: frozenset[str] = frozenset(
    f"{lib}.{method}"
    for lib, methods in _HTTP_LIBRARIES.items()
    for method in (methods if methods else ["__any__"])
)


def _detect_grpc(callee_name: str, language: str) -> tuple[str, str] | None:
    """Detect gRPC call patterns. Returns (edge_kind, service_name) or None."""
    from .grpc_detect import detect_python_grpc, detect_typescript_grpc, detect_java_grpc, detect_go_grpc
    if language == "python":
        return detect_python_grpc(callee_name)
    elif language in ("typescript", "tsx"):
        return detect_typescript_grpc(callee_name)
    elif language == "java":
        return detect_java_grpc(callee_name)
    elif language == "go":
        return detect_go_grpc(callee_name)
    return None


def _detect_emit(callee_name: str, language: str) -> tuple[str, str] | None:
    """Detect event emit patterns. Returns (edge_kind, event_name) or None."""
    from .event_detect import detect_python_emit, detect_ts_emit
    if language == "python":
        return detect_python_emit(callee_name)
    elif language in ("typescript", "tsx"):
        return detect_ts_emit(callee_name)
    return None


def _detect_listen(callee_name: str, language: str) -> tuple[str, str] | None:
    """Detect event listener registration. Returns (edge_kind, event_name) or None."""
    from .event_detect import detect_python_listen, detect_ts_listen
    if language == "python":
        return detect_python_listen(callee_name)
    elif language in ("typescript", "tsx"):
        return detect_ts_listen(callee_name)
    return None


def _detect_http_call(callee_name: str, call_node, source: bytes) -> dict | None:
    """Detect if a call expression is an HTTP library call.

    Returns dict with 'library' and 'method' keys, or None.
    """
    if "." not in callee_name:
        return None

    # Fast O(1) lookup against pre-computed set
    if callee_name in _KNOWN_HTTP_CALLABLES:
        lib, method = callee_name.split(".", 1)
        return {"library": lib, "method": method}

    # Check aiohttp (has arbitrary method names on ClientSession)
    if callee_name.startswith("aiohttp."):
        lib, method = callee_name.split(".", 1)
        return {"library": lib, "method": method}

    return None
