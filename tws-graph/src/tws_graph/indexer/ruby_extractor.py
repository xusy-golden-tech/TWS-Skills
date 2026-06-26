"""Ruby source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.
Extracts class/module definitions, methods, calls, require/include statements,
attr_accessor/reader/writer, and constant assignments.
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




def _find_named_child(node, kind: str):
    """Find the first named child with the given kind."""
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _find_all_named_children(node, kind: str) -> list:
    """Find all named children with the given kind."""
    results = []
    for child in _named_children(node):
        if child.kind() == kind:
            results.append(child)
    return results


def _get_constant_name(node, source: bytes) -> str | None:
    """Get the constant name (for class/module names)."""
    const_node = _find_named_child(node, "constant")
    if const_node:
        return _node_text(const_node, source)
    return None


def _get_identifier_name(node, source: bytes) -> str | None:
    """Get an identifier name."""
    ident_node = _find_named_child(node, "identifier")
    if ident_node:
        return _node_text(ident_node, source)
    return None


def _resolve_method_name(method_node, source: bytes) -> str | None:
    """Resolve the actual method name, skipping 'self.' prefix if present."""
    parts = []
    for child in _named_children(method_node):
        if child.kind() == "identifier":
            parts.append(_node_text(child, source))
    if parts:
        if parts[0] == "self" and len(parts) > 1:
            return parts[1]
        return parts[0]
    return None


def visit_ruby(file_path: str, content: str, tree) -> ExtractionResult:
    """Traverse a Ruby CST and collect all symbol nodes + relationship edges."""
    source = content.encode("utf-8")
    result = ExtractionResult()
    root = tree.root_node()

    name_stack: list[str] = []
    node_stack: list[str] = []
    node_id_set: set[str] = set()  # all node IDs created so far — O(1) lookup
    scope_kinds: list[str] = []
    file_id = ""  # Pre-declared; assigned after add_node is defined

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
            "language": "ruby",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            **extra,
        })
        node_id_set.add(nid)
        return nid

    def add_edge(source_id: str, target: str, kind: str, line: int, target_text: str | None = None):
        edge = {
            "source": source_id,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # Create a file-level node, after add_node is defined, for top-level
    # require/include calls and top-level methods to have a parent scope.
    if root.named_child_count() > 0:
        file_id = add_node("file", file_path, root)

    def _get_source_id():
        """Get the current scope's source ID for edges."""
        return node_stack[-1] if node_stack else (file_id or "")

    def _handle_call(call_node):
        """Unified handler for all Ruby call expressions."""
        ident = _find_named_child(call_node, "identifier")
        if not ident:
            _walk_children(call_node)
            return

        ident_text = _node_text(ident, source)
        source_id = _get_source_id()

        # --- require / require_relative / load ---
        if ident_text in ("require", "require_relative", "load"):
            arg_list = _find_named_child(call_node, "argument_list")
            if arg_list and source_id:
                str_node = _find_named_child(arg_list, "string")
                if str_node:
                    module_name = _node_text(str_node, source).strip("'\"")
                    target_id = _hash_id(f"{file_path}::{module_name}", file_path)
                    add_edge(source_id, target_id, "imports",
                             call_node.start_position().row + 1,
                             target_text=module_name)
            _walk_children(call_node)
            return

        # --- include / extend ---
        if ident_text in ("include", "extend"):
            arg_list = _find_named_child(call_node, "argument_list")
            if arg_list and source_id:
                const_node = _find_named_child(arg_list, "constant")
                if const_node:
                    const_name = _node_text(const_node, source)
                    kind = "implements" if ident_text == "include" else "extends"
                    target_qname = f"{file_path}::{const_name}"
                    target_id = _hash_id(target_qname, file_path)
                    if target_id in node_id_set:
                        add_edge(source_id, target_id, kind,
                                 call_node.start_position().row + 1,
                                 target_text=target_qname)
            _walk_children(call_node)
            return

        # --- attr_accessor / attr_reader / attr_writer ---
        if ident_text in ("attr_accessor", "attr_reader", "attr_writer"):
            arg_list = _find_named_child(call_node, "argument_list")
            if arg_list:
                for child in _named_children(arg_list):
                    if child.kind() == "simple_symbol":
                        symbol_name = _node_text(child, source)
                        attr_name = symbol_name.lstrip(":")
                        if source_id:
                            nid = add_node("attribute", attr_name, call_node)
                            add_edge(source_id, nid, "contains",
                                     call_node.start_position().row + 1)
            _walk_children(call_node)
            return

        # --- regular method call ---
        if source_id:
            callee_name = _resolve_call_identifier(call_node, source)
            if callee_name:
                target_qname = f"{file_path}::{callee_name}"
                target_id = _hash_id(target_qname, file_path)
                add_edge(source_id, target_id, "calls",
                         call_node.start_position().row + 1,
                         target_text=target_qname)

        _walk_children(call_node)

    def walk(node):
        nonlocal node_stack, name_stack, scope_kinds, file_id
        node_kind = node.kind()

        # -- class --
        if node_kind == "class":
            name = _get_constant_name(node, source)
            if name:
                nid = add_node("class", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("class")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- module --
        elif node_kind == "module":
            name = _get_constant_name(node, source)
            if name:
                nid = add_node("module", name, node)

                if node_stack:
                    add_edge(node_stack[-1], nid, "contains", node.start_position().row + 1)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("module")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- method / singleton_method --
        elif node_kind in ("method", "singleton_method"):
            if node_kind == "singleton_method":
                # singleton_method: self | identifier | method_parameters | body_statement
                # The identifier is the method name
                name = _get_identifier_name(node, source)
                is_class_method = True
            else:
                name = _resolve_method_name(node, source)
                is_class_method = False

            if name:
                nid = add_node("method", name, node,
                               decorators=["class_method"] if is_class_method else [])

                source_id = node_stack[-1] if node_stack else (file_id or "")
                if source_id:
                    add_edge(source_id, nid, "contains", node.start_position().row + 1,
                             target_text=f"{name_stack[-1]}::{name}" if name_stack else name)

                name_stack.append(name)
                node_stack.append(nid)
                scope_kinds.append("method")
                _walk_children(node)
                scope_kinds.pop()
                node_stack.pop()
                name_stack.pop()
                return

        # -- call (unified handler for all call types) --
        elif node_kind == "call":
            _handle_call(node)
            return

        # -- assignment (constant assignments: CONST = value) --
        elif node_kind == "assignment":
            const_node = _find_named_child(node, "constant")
            if const_node:
                const_name = _node_text(const_node, source)
                nid = add_node("constant", const_name, node)
                source_id = node_stack[-1] if node_stack else (file_id or "")
                if source_id:
                    add_edge(source_id, nid, "contains", node.start_position().row + 1)

        # Recurse into children
        _walk_children(node)

    def _walk_children(node):
        for child in _children(node):
            walk(child)

    walk(root)
    return result


def _resolve_call_identifier(call_node, source: bytes) -> str | None:
    """Resolve the target name of a Ruby call expression (regular method call).

    For calls with multiple identifiers (e.g., ``service.create_order``),
    the last identifier is the method name and earlier ones are receivers.
    For instance_variable/constant + identifier (e.g., ``@items.find``),
    the identifier is the method and the other is the receiver.
    """
    # Collect all identifier parts (preserving order)
    identifiers = []
    receiver = None
    for child in _named_children(call_node):
        if child.kind() == "identifier":
            identifiers.append(_node_text(child, source))
        elif child.kind() == "instance_variable":
            receiver = _node_text(child, source)
        elif child.kind() == "constant":
            receiver = _node_text(child, source)

    if not identifiers:
        return None

    # The last identifier is the method name
    method_name = identifiers[-1]

    # If there are multiple identifiers, earlier ones are receivers
    if len(identifiers) > 1:
        receiver = identifiers[0]

    if receiver:
        return f"{receiver}.{method_name}"
    return method_name
