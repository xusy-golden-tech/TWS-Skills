"""Nix source extractor — walks tree-sitter CST for symbols and edges.

Nix is a functional DSL.  Key concepts extracted:
  - let ... in ... blocks  →  bindings (variable-like)
  - { ... } attribute sets  →  kind="class" (struct-like data)
  - function expressions     →  kind="function"
  - inherit                 →  imports-like edges
  - import                  →  imports edges
  - rec { ... }             →  recursive attribute sets
  - with                    →  scope imports
  - apply_expression        →  calls edges
"""

import hashlib
from .base import ExtractionResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_named_child(node, kind: str):
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def visit_nix(file_path: str, source: str, tree) -> ExtractionResult:
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []
    file_id = _hash_id(file_path, file_path)

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
            "language": "nix",
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
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # -------------------------------------------------------------------
    # binding helpers
    # -------------------------------------------------------------------

    def _get_binding_name(binding_node) -> str | None:
        """Extract the name from a binding's attrpath."""
        attrpath = _find_named_child(binding_node, "attrpath")
        if not attrpath:
            return None
        id_nodes = [c for c in _named_children(attrpath) if c.kind() == "identifier"]
        if not id_nodes:
            return None
        return ".".join(_node_text(c, src_bytes) for c in id_nodes)

    def _get_binding_value(binding_node):
        """Return the value expression child of a binding (first non-attrpath named child)."""
        for child in _named_children(binding_node):
            if child.kind() not in ("attrpath", "comment"):
                return child
        return None

    # -------------------------------------------------------------------
    # import detection
    # -------------------------------------------------------------------

    def _detect_import_call(apply_node) -> str | None:
        """If this apply is an 'import <path>' or 'import ./file', return the path."""
        fn_node = None
        for child in _named_children(apply_node):
            fn_node = child
            break
        if not fn_node:
            return None
        if fn_node.kind() == "variable_expression":
            idn = _find_named_child(fn_node, "identifier")
            if idn and _node_text(idn, src_bytes) == "import":
                # Find the path argument
                found_fn = False
                for child in _named_children(apply_node):
                    if not found_fn:
                        found_fn = True
                        continue
                    if child.kind() in ("spath_expression", "path_expression"):
                        return _node_text(child, src_bytes)
                    if child.kind() in ("string_expression", "indented_string_expression"):
                        return _node_text(child, src_bytes)
        return None

    # -------------------------------------------------------------------
    # call extraction
    # -------------------------------------------------------------------

    def _extract_callee_name(apply_node) -> str | None:
        """Extract the callee name from an apply_expression node."""
        fn_node = None
        for child in _named_children(apply_node):
            fn_node = child
            break
        if not fn_node:
            return None

        if fn_node.kind() == "variable_expression":
            idn = _find_named_child(fn_node, "identifier")
            if idn:
                return _node_text(idn, src_bytes)
        elif fn_node.kind() == "select_expression":
            return _node_text(fn_node, src_bytes)
        elif fn_node.kind() == "apply_expression":
            # Nested apply — walk down to find the base function
            inner = fn_node
            while inner.kind() == "apply_expression":
                first = None
                for c in _named_children(inner):
                    first = c
                    break
                if not first:
                    break
                inner = first
            if inner.kind() == "variable_expression":
                idn = _find_named_child(inner, "identifier")
                if idn:
                    return _node_text(idn, src_bytes)
            elif inner.kind() == "select_expression":
                return _node_text(inner, src_bytes)

        return None

    def _walk_calls(node, caller_id):
        """Recursively find apply_expressions and emit calls edges."""
        if node.kind() == "apply_expression":
            # First check if this is an import call
            import_path = _detect_import_call(node)
            if import_path:
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{import_path}", file_path),
                         "imports", node.start_position().row + 1,
                         target_text=import_path)
            else:
                callee = _extract_callee_name(node)
                if callee:
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{callee}", file_path),
                             "calls", node.start_position().row + 1,
                             target_text=callee)

        for child in _named_children(node):
            _walk_calls(child, caller_id)

    # -------------------------------------------------------------------
    # structural visitors
    # -------------------------------------------------------------------

    def _walk_binding_set(binding_set_node, default_kind="variable"):
        """Process all bindings and inherits within a binding_set.

        default_kind: 'variable' for let bindings, 'property' for attrset fields.
        """
        for child in _named_children(binding_set_node):
            if child.kind() == "binding":
                _visit_binding(child, default_kind)
            elif child.kind() == "inherit":
                _visit_inherit(child)

    def _visit_binding(node, default_kind="variable"):
        """Process a binding node.

        default_kind: fallback kind for simple bindings ('variable' or 'property').
        """
        name = _get_binding_name(node)
        if not name:
            return
        value = _get_binding_value(node)

        if value and value.kind() == "function_expression":
            # Named function
            nid = add_node("function", name, node)
            if node_stack:
                add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

            name_stack.append(name)
            node_stack.append(nid)

            # Walk function body for calls
            _walk_function_body(value)

            node_stack.pop()
            name_stack.pop()

        elif value and value.kind() in ("attrset_expression", "rec_attrset_expression"):
            # Named attrset → class
            nid = add_node("class", name, node)
            if node_stack:
                add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

            name_stack.append(name)
            node_stack.append(nid)

            _walk_attrset_body(value)

            node_stack.pop()
            name_stack.pop()

        elif value and value.kind() == "let_expression":
            # Binding whose value is a let expression — treat as class
            nid = add_node("class", name, node)
            if node_stack:
                add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

            name_stack.append(name)
            node_stack.append(nid)

            _visit_let(value)

            node_stack.pop()
            name_stack.pop()

        else:
            # Simple binding — use the caller-specified default_kind
            nid = add_node(default_kind, name, node)
            if node_stack:
                add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

            # Visit value for nested expressions (calls, imports, etc.)
            if value:
                name_stack.append(name)
                node_stack.append(nid)
                _visit(value)
                node_stack.pop()
                name_stack.pop()

    def _visit_inherit(node):
        """inherit (scope) attr1 attr2 ...; → imports edges."""
        # Check for scope expression: inherit (scope) ...
        scope_node = None
        scope_text = None
        for child in _named_children(node):
            if child.kind() in ("variable_expression", "select_expression"):
                scope_node = child
                scope_text = _node_text(child, src_bytes)
                break

        inherited = _find_named_child(node, "inherited_attrs")
        if not inherited:
            return

        for idn in _named_children(inherited):
            if idn.kind() == "identifier":
                attr_name = _node_text(idn, src_bytes)
                target_text = f"{scope_text}.{attr_name}" if scope_text else attr_name
                source_id = node_stack[-1] if node_stack else file_id
                add_edge(source_id,
                         _hash_id(f"{file_path}::{target_text}", file_path),
                         "imports", node.start_position().row + 1,
                         target_text=target_text)

    def _visit_let(node, create_node=True):
        """Process a let_expression."""
        for child in _named_children(node):
            if child.kind() == "binding_set":
                _walk_binding_set(child)
            else:
                # Body expression
                _visit(child)

    def _walk_attrset_body(attrset_node):
        """Walk the body of an attrset_expression / rec_attrset_expression."""
        for child in _named_children(attrset_node):
            if child.kind() == "binding_set":
                _walk_binding_set(child, default_kind="property")
            else:
                _visit(child)

    def _visit_function(node, create_node=True):
        """Process a function_expression (anonymous or top-level)."""
        # Find parameter name for synthetic node
        param_name = "lambda"
        formals = _find_named_child(node, "formals")
        if formals:
            formal = _find_named_child(formals, "formal")
            if formal:
                idn = _find_named_child(formal, "identifier")
                if idn:
                    param_name = _node_text(idn, src_bytes)
        if param_name == "lambda":
            idn = _find_named_child(node, "identifier")
            if idn:
                param_name = _node_text(idn, src_bytes)

        if create_node and not node_stack:
            # Top-level anonymous function — create node
            nid = add_node("function", param_name, node)
            node_stack.append(nid)
            name_stack.append(param_name)
            _walk_function_body(node)
            name_stack.pop()
            node_stack.pop()
        elif create_node:
            nid = add_node("function", param_name, node)
            if node_stack:
                add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)
            node_stack.append(nid)
            name_stack.append(param_name)
            _walk_function_body(node)
            name_stack.pop()
            node_stack.pop()
        else:
            _walk_function_body(node)

    def _walk_function_body(func_node):
        """Visit the body expression of a function, skipping formals/identifier."""
        skipped = False
        for child in _named_children(func_node):
            if not skipped:
                if child.kind() in ("identifier", "formals", "formal"):
                    continue
                skipped = True
            _visit(child)
            break  # Only one body expression in a function

    def _visit_with(node):
        """Process a with expression: with scope; body."""
        for child in _named_children(node):
            if child.kind() in ("variable_expression", "select_expression"):
                scope_text = _node_text(child, src_bytes)
                source_id = node_stack[-1] if node_stack else file_id
                add_edge(source_id,
                         _hash_id(f"{file_path}::{scope_text}", file_path),
                         "imports", node.start_position().row + 1,
                         target_text=scope_text)
            else:
                _visit(child)

    # -------------------------------------------------------------------
    # main visitor dispatcher
    # -------------------------------------------------------------------

    def _visit(node):
        kind = node.kind()

        if kind in ("attrset_expression", "rec_attrset_expression"):
            # Top-level / anonymous attrset → class node
            if not node_stack:
                tag = "rec" if kind == "rec_attrset_expression" else "attrset"
                nid = add_node("class", tag, node)
                node_stack.append(nid)
                name_stack.append(tag)
                _walk_attrset_body(node)
                name_stack.pop()
                node_stack.pop()
            else:
                _walk_attrset_body(node)
            return

        elif kind == "function_expression":
            _visit_function(node, create_node=(len(node_stack) == 0))
            return

        elif kind == "let_expression":
            _visit_let(node)
            return

        elif kind == "with_expression":
            _visit_with(node)
            return

        elif kind == "binding":
            _visit_binding(node)
            return

        elif kind == "inherit":
            _visit_inherit(node)
            return

        elif kind == "apply_expression":
            # Calls at top level
            import_path = _detect_import_call(node)
            if import_path:
                source_id = node_stack[-1] if node_stack else file_id
                add_edge(source_id,
                         _hash_id(f"{file_path}::{import_path}", file_path),
                         "imports", node.start_position().row + 1,
                         target_text=import_path)
            else:
                callee = _extract_callee_name(node)
                if callee:
                    source_id = node_stack[-1] if node_stack else file_id
                    add_edge(source_id,
                             _hash_id(f"{file_path}::{callee}", file_path),
                             "calls", node.start_position().row + 1,
                             target_text=callee)

                # Recurse into children for nested calls
                for child in _named_children(node):
                    if child.kind() != "attrset_expression":
                        _visit(child)

        else:
            # Default: recurse into children
            for child in _named_children(node):
                _visit(child)

    # -------------------------------------------------------------------
    # walk the tree
    # -------------------------------------------------------------------
    for child in _named_children(tree.root_node()):
        _visit(child)

    return result
