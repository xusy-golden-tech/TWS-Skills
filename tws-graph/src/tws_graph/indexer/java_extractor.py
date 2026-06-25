"""Java source extractor — walks tree-sitter CST for symbols and edges.

v5.7.0 (P45): Production-grade Java support with:
  P45a: Package & import system
  P45b: Annotation handling (decorates edges)
  P45c: Generic type handling (type_ref edges)
  P45d: Method call depth (chain, static, instantiates)
  P45e: Modern Java (record, enum, lambda, interface default)
  P45f: Variable-level read/write tracking (reads, writes, data_flows)

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
from functools import lru_cache
from typing import Optional

from .parser import ExtractionResult


@lru_cache(maxsize=4096)
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


# ============================================================================
# Main entry point
# ============================================================================


def visit_java(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Java source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    # ── State ────────────────────────────────────────────────────
    name_stack: list[str] = []
    node_stack: list[str] = []
    node_id_set: set[str] = set()
    package_name: str = ""  # P45a: current file's package
    import_map: dict[str, str] = {}  # P45a: short_name → full_name

    # ── Helpers ──────────────────────────────────────────────────

    def make_qualified(simple_name: str) -> str:
        """Build qualified_name: package::Class::member (P45a format)."""
        if package_name:
            parts = [package_name] + name_stack + [simple_name]
        else:
            # No package declaration — use file_path as namespace
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
            "language": "java",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _visibility_from_modifiers(node, src_bytes),
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        result.nodes.append(record)
        node_id_set.add(nid)
        return nid

    def add_edge(source: str, target: str, kind: str, line: int,
                 target_text: str | None = None, provenance: str = "tree-sitter"):
        edge = {
            "source": source,
            "target": target,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": provenance,
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # ── P45a: Package & Import Parsing ───────────────────────────

    def _parse_package(root_node):
        """Extract package declaration."""
        nonlocal package_name
        for child in _named_children(root_node):
            if child.kind() == "package_declaration":
                scoped = _find_named_child(child, "scoped_identifier")
                if scoped:
                    package_name = _node_text(scoped, src_bytes)
                    # P45a: produce a module/package-level node
                    pkg_id = _hash_id(package_name, file_path)
                    pkg_node = {
                        "id": pkg_id,
                        "kind": "package",
                        "name": package_name,
                        "qualified_name": package_name,
                        "file_path": file_path,
                        "language": "java",
                        "start_line": child.start_position().row + 1,
                        "end_line": child.end_position().row + 1,
                        "visibility": "public",
                        "is_abstract": 0,
                        "is_exported": 0,
                    }
                    result.nodes.append(pkg_node)
                    node_id_set.add(pkg_id)
                return

    def _parse_imports(root_node):
        """Extract import declarations and produce imports edges."""
        for child in _named_children(root_node):
            if child.kind() == "import_declaration":
                is_static = any(
                    c.kind() == "static" for c in _children(child)
                )
                is_wildcard = any(
                    c.kind() == "asterisk" for c in _children(child)
                )

                # Get the imported name
                scoped = _find_named_child(child, "scoped_identifier")
                if scoped:
                    full_name = _node_text(scoped, src_bytes)
                else:
                    # Simple identifier import: import Foo;
                    ident = _find_named_child(child, "identifier")
                    if ident:
                        full_name = _node_text(ident, src_bytes)
                    else:
                        continue

                target_text = full_name
                if is_static:
                    target_text = f"static {full_name}"
                if is_wildcard:
                    target_text = f"{full_name}.*" if not is_static else f"static {full_name}.*"

                # Build imports edge from file-level package node
                file_qname = package_name or file_path
                file_nid = _hash_id(file_qname, file_path)

                add_edge(
                    file_nid,
                    _hash_id(target_text, file_path),
                    "imports",
                    child.start_position().row + 1,
                    target_text=target_text,
                )

    # ── P45b: Annotation Parsing ─────────────────────────────────

    def _extract_annotations(node, target_nid: str):
        """Extract modifier annotations and produce decorates edges."""
        modifiers = _find_child(node, "modifiers")
        search_in = modifiers if modifiers else node
        if not search_in:
            return

        for child in _children(search_in):
            if child.kind() in ("annotation", "marker_annotation"):
                _process_annotation(child, target_nid)

    def _process_annotation(anno_node, target_nid: str):
        """Process a single annotation node."""
        # Get annotation name
        name_node = _find_named_child(anno_node, "identifier")
        if not name_node:
            # Try scoped_identifier for fully qualified annotations
            scoped = _find_named_child(anno_node, "scoped_identifier")
            if scoped:
                anno_name = _node_text(scoped, src_bytes)
            else:
                return
        else:
            anno_name = _node_text(name_node, src_bytes)

        target_text = f"@{anno_name}"

        # Get annotation arguments if present
        args_node = _find_child(anno_node, "annotation_argument_list")
        if args_node:
            for arg_child in _named_children(args_node):
                if arg_child.kind() == "element_value_pair":
                    key_node = _find_named_child(arg_child, "identifier")
                    if key_node:
                        key = _node_text(key_node, src_bytes)
                        val = ""
                        # Try to get value
                        for vc in _named_children(arg_child):
                            if vc.kind() != "identifier":
                                val = _node_text(vc, src_bytes)
                                break
                        target_text = f"@{anno_name}({key}={val})"

        add_edge(
            target_nid,
            _hash_id(target_text, file_path),
            "decorates",
            anno_node.start_position().row + 1,
            target_text=target_text,
        )

    # ── P45c: Generic Type Handling ──────────────────────────────

    def _extract_type_refs(node, caller_id: str):
        """Extract type references from type_arguments and type_identifier nodes."""
        for child in _named_children(node):
            if child.kind() == "type_arguments":
                for tc in _named_children(child):
                    _extract_type_ref_node(tc, caller_id)
            elif child.kind() == "type_identifier":
                type_name = _node_text(child, src_bytes)
                # Filter out Java built-in types
                if type_name not in _JAVA_BUILTIN_TYPES:
                    add_edge(
                        caller_id,
                        _hash_id(type_name, file_path),
                        "type_ref",
                        child.start_position().row + 1,
                        target_text=type_name,
                    )

    def _extract_type_ref_node(node, caller_id: str):
        """Recursively extract type references from type argument nodes."""
        if node.kind() == "type_identifier":
            type_name = _node_text(node, src_bytes)
            if type_name not in _JAVA_BUILTIN_TYPES:
                add_edge(
                    caller_id,
                    _hash_id(type_name, file_path),
                    "type_ref",
                    node.start_position().row + 1,
                    target_text=type_name,
                )
        elif node.kind() == "generic_type":
            # e.g. List<T> or ArrayList<String>
            base = _find_named_child(node, "type_identifier")
            if base:
                base_name = _node_text(base, src_bytes)
                if base_name not in _JAVA_BUILTIN_TYPES:
                    add_edge(
                        caller_id,
                        _hash_id(base_name, file_path),
                        "type_ref",
                        base.start_position().row + 1,
                        target_text=base_name,
                    )
            type_args = _find_child(node, "type_arguments")
            if type_args:
                for tc in _named_children(type_args):
                    _extract_type_ref_node(tc, caller_id)
        elif node.kind() == "wildcard":
            # ? extends Foo or ? super Bar
            for wc in _named_children(node):
                if wc.kind() == "type_identifier":
                    type_name = _node_text(wc, src_bytes)
                    if type_name not in _JAVA_BUILTIN_TYPES:
                        add_edge(
                            caller_id,
                            _hash_id(type_name, file_path),
                            "type_ref",
                            wc.start_position().row + 1,
                            target_text=type_name,
                        )
        # Recurse
        for child in _named_children(node):
            if child != node:
                _extract_type_ref_node(child, caller_id)

    def _extract_type_refs_from_type_param(node, caller_id: str):
        """Extract type_ref from a type_parameter like 'T extends Comparable<T>'."""
        # Get the type parameter name
        name_node = _find_named_child(node, "identifier")
        if name_node:
            type_name = _node_text(name_node, src_bytes)
            if type_name not in _JAVA_BUILTIN_TYPES:
                add_edge(
                    caller_id,
                    _hash_id(type_name, file_path),
                    "type_ref",
                    name_node.start_position().row + 1,
                    target_text=type_name,
                )
        # Handle bounds: "extends Comparable<T>"
        for child in _named_children(node):
            if child.kind() == "type_bound":
                _extract_type_ref_node(child, caller_id)

    def _extract_type_refs_from_node(node, caller_id: str):
        """Recursively scan a node for type references in type annotations."""
        for child in _named_children(node):
            if child.kind() == "generic_type":
                _extract_type_ref_node(child, caller_id)
            elif child.kind() == "type_identifier":
                type_name = _node_text(child, src_bytes)
                if type_name not in _JAVA_BUILTIN_TYPES:
                    add_edge(
                        caller_id,
                        _hash_id(type_name, file_path),
                        "type_ref",
                        child.start_position().row + 1,
                        target_text=type_name,
                    )
            elif child.kind() == "type_arguments":
                for tc in _named_children(child):
                    _extract_type_ref_node(tc, caller_id)
            else:
                # Recurse into other children that might contain type refs
                _extract_type_refs_from_node(child, caller_id)

    # ── Main Visitor ─────────────────────────────────────────────

    def _visit_node(node, depth: int = 0):
        kind = node.kind()

        if kind == "class_declaration":
            _visit_class(node, is_interface=False)
        elif kind == "interface_declaration":
            _visit_class(node, is_interface=True)
        elif kind == "method_declaration":
            _visit_method(node, is_constructor=False)
        elif kind == "constructor_declaration":
            _visit_method(node, is_constructor=True)
        elif kind == "field_declaration":
            _visit_field(node)
        elif kind == "record_declaration":  # P45e
            _visit_record(node)
        elif kind == "enum_declaration":    # P45e
            _visit_enum(node)

        # Recurse into children (but not into nested class bodies)
        if kind not in ("class_declaration", "interface_declaration",
                        "record_declaration", "enum_declaration",
                        "method_declaration", "constructor_declaration"):
            for child in _named_children(node):
                _visit_node(child, depth + 1)

    # ── Class / Interface ────────────────────────────────────────

    def _visit_class(node, is_interface: bool = False):
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        cls_kind = "interface" if is_interface else "class"

        is_abstract = 1 if any(
            c.kind() == "abstract" for c in _children(node)
        ) else 0

        nid = add_node(cls_kind, name, node, is_abstract=is_abstract)
        name_stack.append(name)
        node_stack.append(nid)

        # P45b: Extract annotations on class
        _extract_annotations(node, nid)

        # P45c: Extract type parameters from class declaration
        type_params = _find_child(node, "type_parameters")
        if type_params:
            for tp in _named_children(type_params):
                if tp.kind() == "type_parameter":
                    _extract_type_refs_from_type_param(tp, nid)

        # Extends
        sc = _find_child(node, "superclass")
        if sc:
            for child in _named_children(sc):
                if child.kind() == "type_identifier":
                    super_name = _node_text(child, src_bytes)
                    super_qname = _make_cross_ref(super_name)
                    super_id = _hash_id(super_qname, file_path)
                    add_edge(nid, super_id, "extends",
                             child.start_position().row + 1,
                             super_qname)

        # Implements
        si = _find_child(node, "super_interfaces")
        if si:
            for child in _named_children(si):
                if child.kind() == "type_list":
                    for tc in _named_children(child):
                        if tc.kind() == "type_identifier":
                            iface_name = _node_text(tc, src_bytes)
                            iface_qname = _make_cross_ref(iface_name)
                            iface_id = _hash_id(iface_qname, file_path)
                            add_edge(nid, iface_id, "implements",
                                     tc.start_position().row + 1,
                                     iface_qname)

        # Body
        body = _find_child(node, "class_body") or _find_child(node, "interface_body")
        if body:
            for child in _named_children(body):
                _visit_node(child, 0)
                cn = child.kind()
                if cn in ("method_declaration", "constructor_declaration",
                          "field_declaration"):
                    child_name_node = _find_named_child(child, "identifier")
                    if child_name_node:
                        child_name = _node_text(child_name_node, src_bytes)
                        child_q = make_qualified(child_name)
                        child_id = _hash_id(child_q, file_path)
                        add_edge(nid, child_id, "contains",
                                 child.start_position().row + 1)

        name_stack.pop()
        node_stack.pop()

    # ── Method / Constructor ─────────────────────────────────────

    def _visit_method(node, is_constructor: bool = False):
        if is_constructor:
            name_node = _find_named_child(node, "identifier")
            if not name_node:
                return
            name = _node_text(name_node, src_bytes)
            mkind = "constructor"
        else:
            name = _extract_method_name(node, src_bytes)
            if not name:
                return
            mkind = "method"

        sig = _build_method_sig(node, src_bytes)
        is_abstract = 1 if any(
            c.kind() == "abstract" for c in _children(node)
        ) else 0

        body_node = _find_child(node, "block")
        body_text = _node_text(body_node, src_bytes) if body_node else ""

        nid = add_node(mkind, name, node, signature=sig, is_abstract=is_abstract,
                       body=body_text)

        # P45b: Annotations on method
        _extract_annotations(node, nid)

        # P45c: Type ref from method return type and parameter types
        _extract_type_refs_from_node(node, nid)

        # Contains edge from parent class
        if node_stack:
            parent_id = node_stack[-1]
            add_edge(parent_id, nid, "contains", node.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        # Walk body
        if body_node:
            _walk_method_body(body_node, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    # ── Field ────────────────────────────────────────────────────

    def _visit_field(node):
        _extract_annotations(node, node_stack[-1] if node_stack else "")

        decl = _find_child(node, "variable_declarator")
        if not decl:
            return
        name_node = _find_named_child(decl, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("property", name, node)

        # P45c: Type ref from field type annotation
        _extract_type_refs_from_node(node, nid)

        # P45e: Check for enum constant (use node_stack parent)
        if node_stack:
            parent_id = node_stack[-1]
            add_edge(parent_id, nid, "contains", node.start_position().row + 1)

    # ── P45e: Record ─────────────────────────────────────────────

    def _visit_record(node):
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("class", name, node, is_abstract=0)
        name_stack.append(name)
        node_stack.append(nid)

        _extract_annotations(node, nid)

        # Record components (fields)
        body = _find_child(node, "class_body")
        if body:
            for child in _named_children(body):
                _visit_node(child, 0)
                cn = child.kind()
                if cn in ("method_declaration", "constructor_declaration"):
                    child_name_node = _find_named_child(child, "identifier")
                    if child_name_node:
                        child_name = _node_text(child_name_node, src_bytes)
                        child_q = make_qualified(child_name)
                        child_id = _hash_id(child_q, file_path)
                        add_edge(nid, child_id, "contains",
                                 child.start_position().row + 1)

        name_stack.pop()
        node_stack.pop()

    # ── P45e: Enum ───────────────────────────────────────────────

    def _visit_enum(node):
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("class", name, node, is_abstract=0)
        name_stack.append(name)
        node_stack.append(nid)

        _extract_annotations(node, nid)

        body = _find_child(node, "enum_body")
        if body:
            # Enum constants
            for child in _named_children(body):
                if child.kind() == "enum_constant":
                    const_name_node = _find_named_child(child, "identifier")
                    if const_name_node:
                        const_name = _node_text(const_name_node, src_bytes)
                        const_qname = make_qualified(const_name)
                        const_id = add_node("enum_constant", const_name, child)
                        add_edge(nid, const_id, "contains",
                                 child.start_position().row + 1)

                        # Enum constant annotations
                        _extract_annotations(child, const_id)

                        # Enum constant class body
                        const_body = _find_child(child, "class_body")
                        if const_body:
                            for cb in _named_children(const_body):
                                _visit_node(cb, 0)

                elif child.kind() in ("method_declaration", "constructor_declaration",
                                      "field_declaration"):
                    _visit_node(child, 0)

        name_stack.pop()
        node_stack.pop()

    # ── P45d: Method Call Depth ──────────────────────────────────

    def _walk_method_body(body_node, caller_id: str, src_bytes):
        """Walk method body for calls, reads, writes, lambdas."""
        _walk_body_recursive(body_node, caller_id, src_bytes)

    def _walk_body_recursive(node, caller_id: str, src_bytes):
        kind = node.kind()

        # P45d: Method invocation (calls + chain)
        if kind == "method_invocation":
            _handle_method_invocation(node, caller_id, src_bytes)

        # P45d: Object creation (instantiates)
        elif kind == "object_creation_expression":
            _handle_object_creation(node, caller_id, src_bytes)

        # P45e: Lambda expression
        elif kind == "lambda_expression":
            _handle_lambda(node, caller_id, src_bytes)

        # P45f: Variable declaration (writes)
        elif kind == "variable_declaration":
            _handle_variable_declaration(node, caller_id, src_bytes)

        # P45f: Assignment expression (writes)
        elif kind == "assignment_expression":
            _handle_assignment(node, caller_id, src_bytes)

        # P45f: Field access (reads)
        elif kind == "field_access":
            _handle_field_access(node, caller_id, src_bytes)

        # P45f: Identifier (reads)
        elif kind == "identifier":
            # Only track bare identifiers in expressions (not declarations)
            if not _is_declaration_context(node):
                name = _node_text(node, src_bytes)
                if name not in _JAVA_KEYWORDS:
                    add_edge(
                        caller_id,
                        _hash_id(f"var::{name}", file_path),
                        "reads",
                        node.start_position().row + 1,
                        target_text=name,
                    )

        # Recurse into children
        for child in _named_children(node):
            _walk_body_recursive(child, caller_id, src_bytes)

    def _handle_method_invocation(node, caller_id: str, src_bytes):
        """Handle method_invocation: chain calls, static calls, super calls."""
        # Check for chained call: node might be part of a chain
        obj = _find_named_child(node, "object")
        name_node = None

        if obj and obj.kind() == "method_invocation":
            # Chain: obj.method() — recurse into object first
            _handle_method_invocation(obj, caller_id, src_bytes)

        if obj and obj.kind() == "field_access":
            # obj.method() — get method name from field_access's identifier
            name_node = _find_named_child(obj, "identifier")

        if not name_node:
            name_node = _find_named_child(node, "identifier")

        if name_node:
            callee = _node_text(name_node, src_bytes)
            target = _make_cross_ref(callee)
            add_edge(caller_id, _hash_id(target, file_path), "calls",
                     node.start_position().row + 1, target)

        # Extract arguments for data_flows
        args_node = _find_child(node, "argument_list")
        if args_node:
            for arg in _named_children(args_node):
                if arg.kind() == "identifier":
                    arg_name = _node_text(arg, src_bytes)
                    # data_flow: caller var → callee param
                    add_edge(
                        caller_id,
                        _hash_id(f"var::{arg_name}", file_path),
                        "data_flows",
                        arg.start_position().row + 1,
                        target_text=f"{callee if name_node else '?'}::{arg_name}",
                        provenance="tree-sitter",
                    )

    def _handle_object_creation(node, caller_id: str, src_bytes):
        """Handle object_creation_expression: new Foo() → instantiates edge."""
        type_node = _find_named_child(node, "type_identifier")
        if not type_node:
            # Try generic_type: new ArrayList<>() or new ArrayList<String>()
            generic = _find_named_child(node, "generic_type")
            if generic:
                type_node = _find_named_child(generic, "type_identifier")
        if type_node:
            type_name = _node_text(type_node, src_bytes)
            target = _make_cross_ref(type_name)
            add_edge(caller_id, _hash_id(target, file_path), "instantiates",
                     node.start_position().row + 1, target)

        # Also record as calls (to constructor) — but check for args
        args_node = _find_child(node, "argument_list")
        if args_node and type_node:
            for arg in _named_children(args_node):
                if arg.kind() == "identifier":
                    arg_name = _node_text(arg, src_bytes)
                    add_edge(
                        caller_id,
                        _hash_id(f"var::{arg_name}", file_path),
                        "data_flows",
                        arg.start_position().row + 1,
                        target_text=f"{_node_text(type_node, src_bytes)}::{arg_name}",
                    )

    def _handle_lambda(node, caller_id: str, src_bytes):
        """Handle lambda_expression: create anonymous function node."""
        sp = node.start_position()
        ep = node.end_position()
        lambda_name = f"lambda${sp.row + 1}"
        lambda_qname = make_qualified(lambda_name)
        lambda_id = _hash_id(lambda_qname, file_path)

        lambda_node = {
            "id": lambda_id,
            "kind": "function",
            "name": lambda_name,
            "qualified_name": lambda_qname,
            "file_path": file_path,
            "language": "java",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": "package-private",
            "is_abstract": 0,
            "is_exported": 0,
            "signature": "() -> {...}",
        }
        result.nodes.append(lambda_node)
        node_id_set.add(lambda_id)

        if node_stack:
            add_edge(node_stack[-1], lambda_id, "contains",
                     node.start_position().row + 1)

    # ── P45f: Variable Read/Write ────────────────────────────────

    def _handle_variable_declaration(node, caller_id: str, src_bytes):
        """Handle local variable declaration → writes edge."""
        decl = _find_child(node, "variable_declarator")
        if decl:
            name_node = _find_named_child(decl, "identifier")
            if name_node:
                var_name = _node_text(name_node, src_bytes)
                add_edge(
                    caller_id,
                    _hash_id(f"var::{var_name}", file_path),
                    "writes",
                    name_node.start_position().row + 1,
                    target_text=var_name,
                )

    def _handle_assignment(node, caller_id: str, src_bytes):
        """Handle assignment expression → writes + reads edges."""
        left = _find_named_child(node, "identifier")
        if not left:
            # Try field_access on left
            field_acc = _find_named_child(node, "field_access")
            if field_acc:
                # this.field = value or obj.field = value
                obj_node = _find_named_child(field_acc, "identifier") or _find_named_child(field_acc, "this")
                field_node = _find_named_child(field_acc, "identifier")
                if not field_node or obj_node:
                    # The identifier after the dot is the field
                    for nc in _named_children(field_acc):
                        if nc.kind() == "identifier" and nc != obj_node:
                            field_node = nc
                            break
                if field_node:
                    field_name = _node_text(field_node, src_bytes)
                    add_edge(
                        caller_id,
                        _hash_id(f"var::{field_name}", file_path),
                        "writes",
                        field_node.start_position().row + 1,
                        target_text=field_name,
                    )
            return
        var_name = _node_text(left, src_bytes)
        if var_name not in _JAVA_KEYWORDS:
            add_edge(
                caller_id,
                _hash_id(f"var::{var_name}", file_path),
                "writes",
                left.start_position().row + 1,
                target_text=var_name,
            )

        # Right side: read variables used in the value expression
        right = _find_named_child(node, "=" )
        # Actually, find the second named child (the value expression)
        named_kids = list(_named_children(node))
        if len(named_kids) >= 2:
            rhs = named_kids[1]
            _extract_reads_from_expr(rhs, caller_id, src_bytes)

    def _handle_field_access(node, caller_id: str, src_bytes):
        """Handle field access as reads."""
        # Object field access: obj.field → read
        # Don't extract for lhs of assignments (handled there)
        for child in _named_children(node):
            if child.kind() == "identifier" and _node_text(child, src_bytes) not in _JAVA_KEYWORDS:
                field_name = _node_text(child, src_bytes)
                # Track reads on the last identifier (the field being read)
                if child != next(_named_children(node), None):
                    add_edge(
                        caller_id,
                        _hash_id(f"var::{field_name}", file_path),
                        "reads",
                        child.start_position().row + 1,
                        target_text=field_name,
                    )

    def _extract_reads_from_expr(node, caller_id: str, src_bytes):
        """Recursively extract reads from an expression."""
        if node.kind() == "identifier":
            name = _node_text(node, src_bytes)
            if name not in _JAVA_KEYWORDS:
                add_edge(
                    caller_id,
                    _hash_id(f"var::{name}", file_path),
                    "reads",
                    node.start_position().row + 1,
                    target_text=name,
                )
        elif node.kind() == "field_access":
            _handle_field_access(node, caller_id, src_bytes)
        for child in _named_children(node):
            _extract_reads_from_expr(child, caller_id, src_bytes)

    def _is_declaration_context(node) -> bool:
        """Check if this identifier is in a declaration context."""
        parent = None
        # Simple heuristic: check if parent is a variable_declarator or formal_parameter
        # This is imperfect but avoids double-counting
        return False  # For now, let reads be tracked; deduplication in post-processing

    # ── Cross-reference helper ───────────────────────────────────

    def _make_cross_ref(name: str) -> str:
        """Build a cross-reference qualified name using package context."""
        if package_name:
            return f"{package_name}::{name}"
        return f"{file_path}::{name}"

    # ── Entry point ──────────────────────────────────────────────

    root = tree.root_node()

    # P45a: Parse package and imports first
    _parse_package(root)
    _parse_imports(root)

    # Walk the tree for declarations
    for child in _named_children(root):
        _visit_node(child, 0)

    return result


# ============================================================================
# Helpers
# ============================================================================

_JAVA_BUILTIN_TYPES = {
    # Only truly primitive types — java.lang and java.util types are relevant for analysis
    "int", "long", "short", "byte", "float", "double", "boolean", "char", "void",
}

_JAVA_KEYWORDS = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double",
    "else", "enum", "extends", "final", "finally", "float", "for",
    "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false",
    "null", "_", "var",
}


def _visibility_from_modifiers(node, src_bytes) -> str:
    modifiers = _find_child(node, "modifiers")
    search_in = modifiers if modifiers else node
    for child in _children(search_in):
        kind = child.kind()
        if kind == "private":
            return "private"
        if kind == "protected":
            return "protected"
        if kind == "public":
            return "public"
    return "package-private"


def _extract_method_name(node, src_bytes) -> Optional[str]:
    for child in _named_children(node):
        if child.kind() == "identifier":
            return _node_text(child, src_bytes)
    return None


def _build_method_sig(node, src_bytes) -> str:
    params = _find_child(node, "formal_parameters")
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
    return f"({', '.join(sig_parts)})"
