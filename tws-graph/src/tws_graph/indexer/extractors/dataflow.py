"""DataFlowExtractor --- analyse call-site argument-to-parameter mappings.

Analyses intra-file call sites: for each call to a function whose definition
is resolvable within the same file, produces DATA_FLOWS edges that map each
call argument (positional or keyword) to its corresponding formal parameter name.

Not a BaseExtractor subclass.  Called directly by DataFlowPass.
Currently supports Python and TypeScript.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Tree-sitter navigation helpers (tree-sitter >= 0.25 method-based API)
# ---------------------------------------------------------------------------

def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _children(node):
    """Yield every child (named + unnamed) of *node*."""
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    """Yield only named children of *node*."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


# ---------------------------------------------------------------------------
# Parameter extraction helpers
# ---------------------------------------------------------------------------

def _find_param_name(child, source: bytes) -> str | None:
    """Recursively locate the parameter *identifier* inside a parameter node.

    Handles Python variants::
        identifier
        default_parameter  {name: identifier}
        typed_parameter    {identifier, type}
        typed_default_parameter {name: identifier, type, value}

    as well as TypeScript variants::
        required_parameter  {pattern: identifier}
        optional_parameter  {pattern: identifier}
    """
    if child.kind() == "identifier":
        return _node_text(child, source)

    # field-aware: many TS + Py variants expose a 'name' or 'pattern' field
    for field_name in ("name", "pattern"):
        field_node = child.child_by_field_name(field_name)
        if field_node is not None and field_node.kind() == "identifier":
            return _node_text(field_node, source)

    # generic fallback: depth-first search for the first identifier
    for c in _named_children(child):
        result = _find_param_name(c, source)
        if result is not None:
            return result
    return None


def _extract_params(func_def_node, source: bytes, language: str) -> list[str]:
    """Return ordered list of formal-parameter *names* for a function definition node."""
    params_node = func_def_node.child_by_field_name("parameters")
    if params_node is None:
        return []

    params: list[str] = []
    # Python skippable param kinds
    _py_skip = {"list_splat_pattern", "dictionary_splat_pattern",
                "keyword_separator"}
    # TS skippable param kinds
    _ts_skip = {"rest_parameter"}

    for child in _named_children(params_node):
        if language == "python" and child.kind() in _py_skip:
            continue
        if language == "typescript" and child.kind() in _ts_skip:
            continue
        name = _find_param_name(child, source)
        if name is not None:
            params.append(name)
    return params


# ---------------------------------------------------------------------------
# Call-argument extraction helpers
# ---------------------------------------------------------------------------

def _extract_py_args(call_node, source: bytes) -> list[tuple[int | str, bool]]:
    """Parse Python call arguments.

    Returns: list of (index_or_name, is_keyword) tuples.
        - positional arg  → (positional_index, False)
        - keyword arg     → (keyword_name, True)
    """
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    args: list[tuple[int | str, bool]] = []
    pos_idx = 0
    for child in _named_children(args_node):
        if child.kind() == "keyword_argument":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                args.append((_node_text(name_node, source), True))
        else:
            args.append((pos_idx, False))
            pos_idx += 1
    return args


def _extract_ts_args(call_node, source: bytes) -> list[tuple[int | str, bool]]:
    """Parse TypeScript call arguments (positional only)."""
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    args: list[tuple[int | str, bool]] = []
    pos_idx = 0
    for child in _named_children(args_node):
        args.append((pos_idx, False))
        pos_idx += 1
    return args


# ---------------------------------------------------------------------------
# Callee-name resolution
# ---------------------------------------------------------------------------

def _is_self_this_call(func_node, source: bytes) -> bool:
    """Return True when the call target is ``self.method()`` or ``this.method()``."""
    for ch in _children(func_node):
        if ch.is_named():
            return _node_text(ch, source) in ("self", "this")
    return False


def _resolve_callee_qname(
    func_node,
    source: bytes,
    file_path: str,
    class_stack: list[str],
    language: str,
) -> str | None:
    """Build the qualified name of the function being called.

    *class_stack* contains only the class names enclosing the call site
    (outermost first).  Used to resolve ``self.method()`` / ``this.method()``.

    Returns ``file_path::<...>::name`` suitable for lookup in *func_node_ids*.
    """
    # -- simple identifier call:  foo()  --
    if func_node.kind() == "identifier":
        return f"{file_path}::{_node_text(func_node, source)}"

    # -- attribute / member-expression call:  obj.method() or self.method() --
    parts: list[str] = []
    for child in _children(func_node):
        if child.is_named():
            parts.append(_node_text(child, source))

    if len(parts) >= 2:
        obj_name = parts[0]
        method_name = parts[1]
        if obj_name in ("self", "this") and class_stack:
            return f"{file_path}::{class_stack[-1]}::{method_name}"
        # Fallback: treat as top-level method name
        return f"{file_path}::{parts[-1]}"

    if parts:
        return f"{file_path}::{parts[-1]}"
    return None


# ===================================================================
# DataFlowExtractor
# ===================================================================

class DataFlowExtractor:
    """Analyse call-site arg→param mappings for intra-file function calls.

    Only produces edges when the callee's function definition can be resolved
    within the same source file.  Not a ``BaseExtractor`` subclass --- this is a
    standalone analysis module invoked by ``DataFlowPass``.
    """

    # Public API ----------------------------------------------------------

    def extract(
        self,
        source: bytes,
        tree,
        func_node_ids: dict[str, str],
        file_path: str,
        language: str,
    ) -> list[dict]:
        """Return **DATA_FLOWS** edges for every resolvable intra-file call.

        Each edge dict::

            {
                "source":      str  — caller function node_id,
                "target":      str  — callee function node_id,
                "kind":        str  — "data_flows",
                "target_text": str  — "arg:<N>->param:<name>" or "arg:<name>->param:<name>",
                "source_loc":  str  — "file_path:line",
                "provenance":  str  — "tree-sitter",
            }
        """
        root = tree.root_node()

        # --- Phase 1: collect every function-definition node ----------
        func_def_nodes: dict[str, object] = {}  # qname → AST node
        self._collect_all_defs(
            root, source, file_path, language, [], func_def_nodes,
        )

        # --- Phase 2: walk the tree and process calls ----------------
        edges: list[dict] = []
        name_stack: list[str] = []       # combined class + function names (for qname building)
        class_stack: list[str] = []      # only class names (for self.method resolution)
        func_stack: list[str] = []       # stacked function qnames
        self._walk_calls(
            root, source, file_path, language,
            name_stack, class_stack, func_stack,
            func_node_ids, func_def_nodes, edges,
        )
        return edges

    # -- Internal: call walk -----------------------------------------------

    def _walk_calls(
        self, node, source, file_path, language,
        name_stack, class_stack, func_stack,
        func_node_ids, func_def_nodes, edges,
    ):
        """Dispatch call-walk to the language-specific handler."""
        if language == "python":
            self._walk_py_calls(
                node, source, file_path,
                name_stack, class_stack, func_stack,
                func_node_ids, func_def_nodes, edges,
            )
        elif language == "typescript":
            self._walk_ts_calls(
                node, source, file_path,
                name_stack, class_stack, func_stack,
                func_node_ids, func_def_nodes, edges,
            )

    # -- Python call walker -----------------------------------------------

    def _walk_py_calls(
        self, node, source, file_path,
        name_stack, class_stack, func_stack,
        func_node_ids, func_def_nodes, edges,
    ):
        kind = node.kind()

        if kind == "function_definition":
            fn_node = node.child_by_field_name("name")
            if fn_node is not None and func_node_ids:
                name = _node_text(fn_node, source)
                qname = file_path + "::" + "::".join(name_stack + [name])
                if qname in func_node_ids:
                    func_stack.append(qname)
                    name_stack.append(name)
                    body = node.child_by_field_name("body")
                    if body:
                        for child in _named_children(body):
                            self._walk_py_calls(
                                child, source, file_path,
                                name_stack, class_stack, func_stack,
                                func_node_ids, func_def_nodes, edges,
                            )
                    name_stack.pop()
                    func_stack.pop()
                    return  # body already walked
            return  # untracked → fall through to generic recursion

        elif kind == "class_definition":
            cn_node = node.child_by_field_name("name")
            if cn_node is not None:
                name = _node_text(cn_node, source)
                name_stack.append(name)
                class_stack.append(name)
                body = node.child_by_field_name("body")
                if body:
                    for child in _named_children(body):
                        self._walk_py_calls(
                            child, source, file_path,
                            name_stack, class_stack, func_stack,
                            func_node_ids, func_def_nodes, edges,
                        )
                class_stack.pop()
                name_stack.pop()
                return  # body already walked

        elif kind == "decorated_definition":
            for child in _named_children(node):
                if child.kind() in ("function_definition", "class_definition"):
                    self._walk_py_calls(
                        child, source, file_path,
                        name_stack, class_stack, func_stack,
                        func_node_ids, func_def_nodes, edges,
                    )
            return

        elif kind == "call":
            if func_stack:
                self._process_py_call(
                    node, source, file_path,
                    class_stack, func_stack,
                    func_node_ids, func_def_nodes, edges,
                )

        # Default: recurse into all named children
        for child in _named_children(node):
            self._walk_py_calls(
                child, source, file_path,
                name_stack, class_stack, func_stack,
                func_node_ids, func_def_nodes, edges,
            )

    def _process_py_call(
        self, node, source, file_path,
        class_stack, func_stack,
        func_node_ids, func_def_nodes, edges,
    ):
        """Process a single Python ``call`` node."""
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        # --- caller ---
        caller_qname = func_stack[-1] if func_stack else None
        if caller_qname is None:
            return
        caller_id = func_node_ids.get(caller_qname)
        if caller_id is None:
            return

        # --- callee ---
        callee_qname = _resolve_callee_qname(
            func_node, source, file_path, class_stack, "python",
        )
        if callee_qname is None:
            return
        callee_id = func_node_ids.get(callee_qname)
        if callee_id is None:
            return

        # --- callee parameters ---
        callee_def = func_def_nodes.get(callee_qname)
        if callee_def is None:
            return
        params = _extract_params(callee_def, source, "python")

        # --- call arguments ---
        args = _extract_py_args(node, source)
        if not args or not params:
            return

        # --- self.method() → skip explicit ``self`` param ---
        is_self = _is_self_this_call(func_node, source)
        if is_self and params and params[0] in ("self",):
            params = params[1:]

        # --- map ---
        line = node.start_position().row + 1
        mapped = _map_args_to_params(args, params)
        for target_text in mapped:
            edges.append({
                "source": caller_id,
                "target": callee_id,
                "kind": "data_flows",
                "target_text": target_text,
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })

    # -- TypeScript call walker -------------------------------------------

    def _walk_ts_calls(
        self, node, source, file_path,
        name_stack, class_stack, func_stack,
        func_node_ids, func_def_nodes, edges,
    ):
        kind = node.kind()

        if kind == "function_declaration":
            fn_node = node.child_by_field_name("name")
            if fn_node is not None and func_node_ids:
                # Skip if inside a class body (handled as method_definition)
                p = node.parent()
                if not (p is not None and p.kind() in ("class_body", "object")):
                    name = _node_text(fn_node, source)
                    qname = file_path + "::" + "::".join(name_stack + [name])
                    if qname in func_node_ids:
                        func_stack.append(qname)
                        name_stack.append(name)
                        body = node.child_by_field_name("body")
                        if body:
                            for child in _named_children(body):
                                self._walk_ts_calls(
                                    child, source, file_path,
                                    name_stack, class_stack, func_stack,
                                    func_node_ids, func_def_nodes, edges,
                                )
                        name_stack.pop()
                        func_stack.pop()
                        return  # body already walked
            return

        elif kind == "method_definition":
            fn_node = node.child_by_field_name("name")
            if fn_node is not None and func_node_ids:
                name = _node_text(fn_node, source)
                qname = file_path + "::" + "::".join(name_stack + [name])
                if qname in func_node_ids:
                    func_stack.append(qname)
                    name_stack.append(name)
                    body = node.child_by_field_name("body")
                    if body:
                        for child in _named_children(body):
                            self._walk_ts_calls(
                                child, source, file_path,
                                name_stack, class_stack, func_stack,
                                func_node_ids, func_def_nodes, edges,
                            )
                    name_stack.pop()
                    func_stack.pop()
                    return  # body already walked
            return

        elif kind == "class_declaration":
            cn_node = node.child_by_field_name("name")
            if cn_node is not None:
                name = _node_text(cn_node, source)
                name_stack.append(name)
                class_stack.append(name)
                body = node.child_by_field_name("body")
                if body:
                    for child in _named_children(body):
                        self._walk_ts_calls(
                            child, source, file_path,
                            name_stack, class_stack, func_stack,
                            func_node_ids, func_def_nodes, edges,
                        )
                class_stack.pop()
                name_stack.pop()
                return  # body already walked

        elif kind == "call_expression":
            if func_stack:
                self._process_ts_call(
                    node, source, file_path,
                    class_stack, func_stack,
                    func_node_ids, func_def_nodes, edges,
                )

        # Default: recurse into named children
        for child in _named_children(node):
            self._walk_ts_calls(
                child, source, file_path,
                name_stack, class_stack, func_stack,
                func_node_ids, func_def_nodes, edges,
            )

    def _process_ts_call(
        self, node, source, file_path,
        class_stack, func_stack,
        func_node_ids, func_def_nodes, edges,
    ):
        """Process a single TypeScript ``call_expression`` node."""
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return

        # --- caller ---
        caller_qname = func_stack[-1] if func_stack else None
        if caller_qname is None:
            return
        caller_id = func_node_ids.get(caller_qname)
        if caller_id is None:
            return

        # --- callee ---
        callee_qname = _resolve_callee_qname(
            func_node, source, file_path, class_stack, "typescript",
        )
        if callee_qname is None:
            return
        callee_id = func_node_ids.get(callee_qname)
        if callee_id is None:
            return

        # --- callee parameters ---
        callee_def = func_def_nodes.get(callee_qname)
        if callee_def is None:
            return
        params = _extract_params(callee_def, source, "typescript")

        # --- call arguments ---
        args = _extract_ts_args(node, source)
        if not args or not params:
            return

        # NOTE: TypeScript methods do NOT explicitly declare ``this`` as a
        # formal parameter, so we do *not* skip the first param.
        # ``this.method(42)`` with ``method(x)`` → arg:0→param:x.

        # --- map ---
        line = node.start_position().row + 1
        mapped = _map_args_to_params(args, params)
        for target_text in mapped:
            edges.append({
                "source": caller_id,
                "target": callee_id,
                "kind": "data_flows",
                "target_text": target_text,
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })

    # -- Internal: collect function-definition nodes -----------------------

    def _collect_all_defs(
        self, node, source, file_path, language,
        name_stack, func_def_nodes,
    ):
        """Dispatch to language-specific collector."""
        if language == "python":
            self._collect_py_defs(node, source, file_path, name_stack, func_def_nodes)
        elif language == "typescript":
            self._collect_ts_defs(node, source, file_path, name_stack, func_def_nodes)

    def _collect_py_defs(self, node, source, file_path, name_stack, func_def_nodes):
        """Collect Python function_definition nodes into *func_def_nodes*."""
        kind = node.kind()

        if kind == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source)
                qname = file_path + "::" + "::".join(name_stack + [name])
                func_def_nodes[qname] = node
                name_stack.append(name)
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in _named_children(body):
                        self._collect_py_defs(child, source, file_path, name_stack, func_def_nodes)
                name_stack.pop()
                return

        elif kind == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source)
                name_stack.append(name)
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in _named_children(body):
                        self._collect_py_defs(child, source, file_path, name_stack, func_def_nodes)
                name_stack.pop()
                return

        elif kind == "decorated_definition":
            for child in _named_children(node):
                if child.kind() in ("function_definition", "class_definition"):
                    self._collect_py_defs(child, source, file_path, name_stack, func_def_nodes)
            return

        for child in _named_children(node):
            self._collect_py_defs(child, source, file_path, name_stack, func_def_nodes)

    def _collect_ts_defs(self, node, source, file_path, name_stack, func_def_nodes):
        """Collect TypeScript function_declaration / method_definition nodes."""
        kind = node.kind()

        if kind == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                p = node.parent()
                is_method = p is not None and p.kind() in ("class_body", "object")
                if not is_method:
                    name = _node_text(name_node, source)
                    qname = file_path + "::" + "::".join(name_stack + [name])
                    func_def_nodes[qname] = node
                    name_stack.append(name)
                    body = node.child_by_field_name("body")
                    if body is not None:
                        for child in _named_children(body):
                            self._collect_ts_defs(child, source, file_path, name_stack, func_def_nodes)
                    name_stack.pop()
                    return

        elif kind == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source)
                qname = file_path + "::" + "::".join(name_stack + [name])
                func_def_nodes[qname] = node
                name_stack.append(name)
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in _named_children(body):
                        self._collect_ts_defs(child, source, file_path, name_stack, func_def_nodes)
                name_stack.pop()
                return

        elif kind == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = _node_text(name_node, source)
                name_stack.append(name)
                body = node.child_by_field_name("body")
                if body is not None:
                    for child in _named_children(body):
                        self._collect_ts_defs(child, source, file_path, name_stack, func_def_nodes)
                name_stack.pop()
                return

        for child in _named_children(node):
            self._collect_ts_defs(child, source, file_path, name_stack, func_def_nodes)


# ---------------------------------------------------------------------------
# Argument-to-parameter mapping
# ---------------------------------------------------------------------------

def _map_args_to_params(
    args: list[tuple[int | str, bool]],
    params: list[str],
) -> list[str]:
    """Map call arguments to callee formal-parameter names.

    Returns a list of ``target_text`` strings such as
    ``"arg:0->param:x"`` or ``"arg:keyword->param:keyword"``.
    """
    if not args or not params:
        return []

    mapped: list[str] = []

    # -- positional args → positional params
    pos_args = [(idx, val) for idx, (val, is_kw) in enumerate(args) if not is_kw]
    for pos_idx, _pos_arg in enumerate(pos_args):
        if pos_idx < len(params):
            mapped.append(f"arg:{pos_idx}->param:{params[pos_idx]}")

    # -- keyword args → matched by name
    kw_args = [(val, True) for val, is_kw in args if is_kw]
    for kw_name, _ in kw_args:
        if isinstance(kw_name, str) and kw_name in params:
            mapped.append(f"arg:{kw_name}->param:{kw_name}")

    return mapped
