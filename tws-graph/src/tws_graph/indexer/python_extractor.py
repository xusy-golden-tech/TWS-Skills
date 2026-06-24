"""Python source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API:
  node.kind()  (not node.type)
  child_count(), child(i)  (not node.children)
  start_position().row  (not start_point[0])
  start_byte(), end_byte()  (methods, not attributes)
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

                nid = add_node(kind, name, node,
                               signature=sig, docstring=doc)

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

                # Extends edges
                bases_node = node.child_by_field_name("superclasses")
                if bases_node:
                    for child in _children(bases_node):
                        if child.is_named():
                            base = _node_text(child, source)
                            base_qname = f"{file_path}::{base}"
                            base_id = _hash_id(base_qname, file_path)
                            # Only emit extends edge if base class is in same file
                            # (node_id_set tracks all nodes defined in this file).
                            # Cross-file / external bases are skipped — they would
                            # produce dangling edges that can't be resolved statically.
                            if base_id in node_id_set:
                                add_edge(nid, base_id, "extends",
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
                    if result.nodes:
                        result.nodes[-1]["decorators"] = decs
                    return

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


def _resolve_qualified_target(callee_name: str, file_path: str) -> str:
    """Best-effort qualified name for a call target."""
    if "." in callee_name:
        return callee_name.replace(".", "::")
    return f"{file_path}::{callee_name}"
