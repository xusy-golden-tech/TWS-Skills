"""Kotlin source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
Kotlin grammar has only 4 field names (alternative, condition, consequence, None),
so we navigate by child kinds rather than field names.

Call resolution uses scoped type inference:
  - Parameters:        fun f(x: Foo)         →  x → Foo
  - Explicit types:    val x: Foo = ...      →  x → Foo
  - Constructor infer: val x = Foo(...)      →  x → Foo
  - Receiver:          class Foo { fun bar() { baz() } }  →  this → Foo → Foo::baz
"""

import hashlib
from .base import children as _children, named_children as _named_children
from .parser import ExtractionResult


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


# _children imported from .base (P50: cached)


def _named_children(node):
    """Generator over named children only."""
    for child in _children(node):
        if child.is_named():
            yield child


def _find_child(node, kind: str):
    """Find the first child (named or unnamed) with the given kind."""
    for child in _children(node):
        if child.kind() == kind:
            return child
    return None


def _find_named_child(node, kind: str):
    """Find the first named child with the given kind."""
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _has_child(node, kind: str) -> bool:
    """Check if node has a child (named or not) with the given kind."""
    return _find_child(node, kind) is not None


def _find_all_named_children(node, kind: str) -> list:
    """Find all named children with the given kind."""
    results = []
    for child in _named_children(node):
        if child.kind() == kind:
            results.append(child)
    return results


# ── Visibility helpers ────────────────────────────────────────────

def _visibility_from_modifiers(modifiers_node, source: bytes) -> str:
    """Extract visibility from a modifiers node (private/protected/internal/public)."""
    if modifiers_node is None:
        return "public"
    for child in _named_children(modifiers_node):
        if child.kind() == "visibility_modifier":
            text = _node_text(child, source)
            if text in ("private", "protected", "internal", "public"):
                return text
    return "public"


def _is_suspend(modifiers_node) -> bool:
    """Check if modifiers contain 'suspend'."""
    if modifiers_node is None:
        return False
    for child in _named_children(modifiers_node):
        if child.kind() == "suspend_modifier":
            return True
    return False


def _has_class_modifier(modifiers_node, modifier: str, source: bytes) -> bool:
    """Check if a class_modifier (data, abstract, sealed, etc.) is present."""
    if modifiers_node is None:
        return False
    for child in _named_children(modifiers_node):
        if child.kind() == "class_modifier":
            if _node_text(child, source) == modifier:
                return True
    return False


# ── Signature building ────────────────────────────────────────────

def _build_function_signature(func_node, source: bytes, name: str) -> str:
    """Build a human-readable function signature from a function_declaration."""
    params_node = _find_named_child(func_node, "function_value_parameters")
    params = _node_text(params_node, source) if params_node else "()"

    return_type_node = _find_named_child(func_node, "user_type")
    if return_type_node:
        returns = f": {_node_text(return_type_node, source)}"
    else:
        returns = ""

    return f"{name}{params}{returns}"


# ── Type resolution helpers ───────────────────────────────────────

def _extract_type_name(user_type_node, source: bytes) -> str | None:
    """Extract the simple type name from a user_type node."""
    ti = _find_named_child(user_type_node, "type_identifier")
    if ti:
        return _node_text(ti, source)
    return None


def _extract_variable_type_from_property(prop_node, source: bytes, func_return_types: dict[str, str] | None = None) -> str | None:
    """Extract the type of a variable from a property_declaration.

    Sources:
      1. Explicit annotation: val x: Foo = ... → user_type → type_identifier
      2. Constructor inference: val x = Foo(...) → call_expression → simple_identifier
      3. Function return type: val x = getFoo() → lookup getFoo's return type
    """
    # 1. Explicit type annotation on variable_declaration
    var_node = _find_named_child(prop_node, "variable_declaration")
    if var_node:
        ut = _find_named_child(var_node, "user_type")
        if ut:
            type_name = _extract_type_name(ut, source)
            if type_name:
                return type_name

    # 2. Constructor / function call inference: val x = Foo(...) / val x = getFoo()
    call = _find_named_child(prop_node, "call_expression")
    if call:
        for child in _named_children(call):
            if child.kind() == "call_suffix":
                continue
            if child.kind() == "simple_identifier":
                callee_name = _node_text(child, source)
                # 3. Try function return type lookup first
                if func_return_types:
                    for qname, ret_type in func_return_types.items():
                        if qname.endswith(f"::{callee_name}"):
                            return ret_type
                # Fall through to constructor inference (name is the type itself)
                return callee_name
            break

    # 3. Cast expression inference: val x = expr as Type
    as_expr = _find_named_child(prop_node, "as_expression")
    if as_expr:
        ut = _find_named_child(as_expr, "user_type")
        if ut:
            type_name = _extract_type_name(ut, source)
            if type_name:
                return type_name

    return None


def _extract_variable_name_from_property(prop_node, source: bytes) -> str | None:
    """Extract the variable name from a property_declaration."""
    var_node = _find_named_child(prop_node, "variable_declaration")
    if var_node:
        var_text = _node_text(var_node, source)
        return var_text.split(":")[0].strip()
    return None


def _extract_param_type(param_node, source: bytes) -> tuple[str | None, str | None]:
    """Extract (param_name, type_name) from a parameter node."""
    name = None
    type_name = None
    for child in _named_children(param_node):
        if child.kind() == "simple_identifier":
            name = _node_text(child, source)
    ut = _find_named_child(param_node, "user_type")
    if ut:
        type_name = _extract_type_name(ut, source)
    return name, type_name


# ── Main visitor ──────────────────────────────────────────────────

def visit_kotlin(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a Kotlin CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    # Scope stacks for qualified names
    name_stack: list[str] = []
    node_stack: list[str] = []
    node_id_set: set[str] = set()  # all node IDs created so far — O(1) lookup
    scope_kinds: list[str] = []

    # Type environment — scoped {variable_name: type_name}
    type_env: list[dict[str, str]] = [{}]

    # Current class name stack (for receiver resolution)
    class_name_stack: list[str] = []

    # Per-class set of method names (to distinguish this.method() from top-level calls)
    class_method_names: list[set[str]] = []

    def _collect_class_methods(class_body_node) -> set[str]:
        """Pre-scan a class_body to collect all method names defined in it."""
        names: set[str] = set()
        def _scan(n):
            if n.kind() == "function_declaration":
                name_n = _find_named_child(n, "simple_identifier")
                if name_n:
                    names.add(_node_text(name_n, source))
            for child in _children(n):
                _scan(child)
        _scan(class_body_node)
        return names

    # Function return types — {qualified_name: return_type_name} for same-file lookup
    func_return_types: dict[str, str] = {}

    # ── Type env helpers ───────────────────────────────────────

    def push_type_scope():
        type_env.append({})

    def pop_type_scope():
        type_env.pop()

    def add_local_type(var_name: str, type_name: str):
        type_env[-1][var_name] = type_name

    def resolve_type(var_name: str) -> str | None:
        """Walk type env from innermost to outermost scope."""
        for scope in reversed(type_env):
            if var_name in scope:
                return scope[var_name]
        return None

    # ── Core helpers ───────────────────────────────────────────

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
            "language": "kotlin",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
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

    def _collect_navigation_parts(nav_node, source: bytes) -> list[str]:
        """Recursively collect all identifiers from a navigation_expression chain.

        For appContainer.documentOpener.open():
          navigation_expression
            navigation_expression (appContainer.documentOpener)
              simple_identifier: "appContainer"
              navigation_suffix: ".documentOpener"
            navigation_suffix: ".open"
        Returns: ["appContainer", "documentOpener", "open"]
        """
        parts = []
        for child in _named_children(nav_node):
            if child.kind() == "simple_identifier":
                parts.append(_node_text(child, source))
            elif child.kind() == "navigation_expression":
                # Nested chain — recurse
                parts.extend(_collect_navigation_parts(child, source))
            elif child.kind() == "navigation_suffix":
                for c in _named_children(child):
                    if c.kind() == "simple_identifier":
                        parts.append(_node_text(c, source))
            elif child.kind() == "parenthesized_expression":
                # (expr as Type).method → extract Type from cast inside parens
                for c in _named_children(child):
                    if c.kind() == "as_expression":
                        ut = _find_named_child(c, "user_type")
                        if ut:
                            type_name = _extract_type_name(ut, source)
                            if type_name:
                                parts.append(type_name)
        return parts

    def resolve_call_target(callee_node, source: bytes) -> str | None:
        """Resolve a call target using type information.

        For navigation_expression (obj.method):
          - Extract receiver (obj) and method name
          - Look up receiver's type in type_env
          - If found: file_path::TypeName::method
          - If not found: fall back to qualified name heuristic

        For simple_identifier (bare function call):
          - If inside a class: try receiver resolution → ClassName::func
          - Otherwise: file_path::func
        """
        if callee_node.kind() == "simple_identifier":
            func_name = _node_text(callee_node, source)
            # If inside a class method, check if func_name is a known method of this class
            if class_name_stack and class_method_names:
                if func_name in class_method_names[-1]:
                    # It is a method of the current class → receiver call
                    receiver_type = class_name_stack[-1]
                    return f"{file_path}::{receiver_type}::{func_name}"
                else:
                    # Not a method of this class → top-level function or imported call
                    return f"{file_path}::{func_name}"
            return f"{file_path}::{func_name}"

        elif callee_node.kind() == "navigation_expression":
            # e.g., reader.read → receiver="reader", method="read"
            # Handle nested chains: appContainer.documentOpener.open()
            parts = _collect_navigation_parts(callee_node, source)

            if len(parts) >= 2:
                receiver = parts[0]
                method = parts[-1]  # only the last segment is the method name

                # Look up receiver type
                receiver_type = resolve_type(receiver)
                if receiver_type:
                    return f"{file_path}::{receiver_type}::{method}"
                else:
                    # Fallback: use variable name as-is
                    return f"{file_path}::{receiver}::{method}"

        return None

    # ── Walker ─────────────────────────────────────────────────

    def walk(node):
        nonlocal node_stack, name_stack, scope_kinds, type_env, class_name_stack, class_method_names

        node_kind = node.kind()

        # ── Function declarations ─────────────────────────────
        if node_kind == "function_declaration":
            name_node = _find_named_child(node, "simple_identifier")
            if name_node:
                name = _node_text(name_node, source)

                p = node.parent()
                is_method = p and p.kind() == "class_body"

                kind = "method" if is_method else "function"
                sig = _build_function_signature(node, source, name)

                modifiers_node = _find_named_child(node, "modifiers")
                visibility = _visibility_from_modifiers(modifiers_node, source)

                body_node = _find_named_child(node, "function_body")
                is_abstract = 0 if body_node else 1

                nid = add_node(kind, name, node,
                               signature=sig,
                               visibility=visibility,
                               is_abstract=is_abstract,
                               is_suspend=int(_is_suspend(modifiers_node)))

                # Record return type for same-file call resolution
                rt_node = _find_named_child(node, "user_type")
                if rt_node:
                    rt_name = _extract_type_name(rt_node, source)
                    if rt_name:
                        func_return_types[make_qualified(name)] = rt_name

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Push scope + type scope
                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append(kind)
                push_type_scope()

                # Extract parameter types into type env
                params_node = _find_named_child(node, "function_value_parameters")
                if params_node:
                    for param in _find_all_named_children(params_node, "parameter"):
                        pname, ptype = _extract_param_type(param, source)
                        if pname and ptype:
                            add_local_type(pname, ptype)

                # Add implicit 'this' → enclosing class for methods
                if is_method and class_name_stack:
                    add_local_type("this", class_name_stack[-1])

                _walk_children(node)

                pop_type_scope()
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # ── Class declarations ────────────────────────────────
        elif node_kind == "class_declaration":
            name_node = _find_named_child(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)

                has_interface = _has_child(node, "interface")
                kind = "interface" if has_interface else "class"

                modifiers_node = _find_named_child(node, "modifiers")
                visibility = _visibility_from_modifiers(modifiers_node, source)
                is_abstract = int(
                    _has_class_modifier(modifiers_node, "abstract", source) or
                    _has_class_modifier(modifiers_node, "sealed", source)
                )
                is_data = int(_has_class_modifier(modifiers_node, "data", source))

                nid = add_node(kind, name, node,
                               visibility=visibility,
                               is_abstract=is_abstract,
                               decorators=["data"] if is_data else [])

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Extends / implements edges
                for ds in _find_all_named_children(node, "delegation_specifier"):
                    ci = _find_named_child(ds, "constructor_invocation")
                    if ci:
                        ut = _find_named_child(ci, "user_type")
                        if ut:
                            base_name = _extract_type_name(ut, source)
                            if base_name:
                                base_qname = f"{file_path}::{base_name}"
                                base_id = _hash_id(base_qname, file_path)
                                if base_id in node_id_set:
                                    add_edge(nid, base_id, "extends",
                                             node.start_position().row + 1,
                                             target_text=base_qname)
                    else:
                        ut = _find_named_child(ds, "user_type")
                        if ut:
                            base_name = _extract_type_name(ut, source)
                            if base_name:
                                base_qname = f"{file_path}::{base_name}"
                                base_id = _hash_id(base_qname, file_path)
                                add_edge(nid, base_id, "implements",
                                         node.start_position().row + 1,
                                         target_text=base_qname)

                # Push scope
                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append(kind)
                class_name_stack.append(name)

                # Pre-scan class body for method names (to distinguish this.method() from top-level calls)
                body_node = _find_named_child(node, "class_body")
                if body_node:
                    class_method_names.append(_collect_class_methods(body_node))
                else:
                    class_method_names.append(set())

                push_type_scope()

                # Extract constructor parameter types into type env
                for child in _named_children(node):
                    if child.kind() == "primary_constructor":
                        for param in _find_all_named_children(child, "class_parameter"):
                            pname, ptype = _extract_param_type(param, source)
                            if pname and ptype:
                                add_local_type(pname, ptype)

                # Extract class-level property types for methods to resolve
                _walk_children(node)

                pop_type_scope()
                class_method_names.pop()
                class_name_stack.pop()
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # ── Object declarations ───────────────────────────────
        elif node_kind == "object_declaration":
            name_node = _find_named_child(node, "type_identifier")
            if name_node:
                name = _node_text(name_node, source)

                p = node.parent()
                is_companion = p is not None and p.kind() == "class_body"

                kind = "companion_object" if is_companion else "object"
                nid = add_node(kind, name, node,
                               visibility="public",
                               is_abstract=0)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append(kind)
                class_name_stack.append(name)

                # Pre-scan class body for method names
                body_node = _find_named_child(node, "class_body")
                if body_node:
                    class_method_names.append(_collect_class_methods(body_node))
                else:
                    class_method_names.append(set())

                push_type_scope()
                _walk_children(node)
                pop_type_scope()
                class_method_names.pop()
                class_name_stack.pop()
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # ── Property declarations ─────────────────────────────
        elif node_kind == "property_declaration":
            in_function = scope_kinds and scope_kinds[-1] in ("function", "method")

            if in_function:
                # Local variable — still extract type info for the type env
                var_name = _extract_variable_name_from_property(node, source)
                var_type = _extract_variable_type_from_property(node, source, func_return_types)
                if var_name and var_type:
                    add_local_type(var_name, var_type)

                # Walk children for call expressions inside the value
                _walk_children(node)
                return

            # Class-level / top-level property
            var_node = _find_named_child(node, "variable_declaration")
            if var_node:
                var_text = _node_text(var_node, source)
                prop_name = var_text.split(":")[0].strip()

                modifiers_node = _find_named_child(node, "modifiers")
                visibility = _visibility_from_modifiers(modifiers_node, source)
                is_const = (
                    modifiers_node is not None
                    and _has_child(modifiers_node, "property_modifier")
                    and any(
                        _node_text(c, source) == "const"
                        for c in _named_children(modifiers_node)
                        if c.kind() == "property_modifier"
                    )
                )

                val_var = _find_child(node, "binding_pattern_kind")
                is_mutable = val_var is not None and _node_text(val_var, source) == "var"

                nid = add_node("property", prop_name, node,
                               visibility=visibility,
                               is_const=int(is_const),
                               is_mutable=int(is_mutable))

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                # Register type for class-level properties (useful for resolving later)
                var_type = _extract_variable_type_from_property(node, source, func_return_types)
                if var_type:
                    add_local_type(prop_name, var_type)

                name_stack.append(prop_name)
                node_stack.append(nid)
                scope_kinds.append("property")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # ── Call expressions ──────────────────────────────────
        elif node_kind == "call_expression":
            if node_stack:
                for child in _named_children(node):
                    if child.kind() == "call_suffix":
                        continue

                    target_qname = resolve_call_target(child, source)
                    if target_qname:
                        caller_id = node_stack[-1]
                        target_id = _hash_id(target_qname, file_path)
                        add_edge(caller_id, target_id, "calls",
                                 node.start_position().row + 1,
                                 target_text=target_qname)
                    break  # First non-call_suffix child is the callee

        # ── Imports ───────────────────────────────────────────
        elif node_kind == "import_header":
            pass

        # ── Recurse into children ─────────────────────────────
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result
