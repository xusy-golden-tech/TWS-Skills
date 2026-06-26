"""Rust source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.

Edge types produced:
  - calls       (function calls, macro invocations)
  - contains    (struct fields, trait methods, impl methods)
  - imports     (use declarations, mod declarations)
  - implements  (impl Trait for Type)
  - decorates   (#[derive(...)], #![...] attributes)
  - type_ref    (generic params, lifetime bounds, function param/return types)
  - reads       (variable reads in function bodies)
  - writes      (variable writes: let, assignment, mutation)
"""

import hashlib
from .base import ExtractionResult, children as _children, named_children as _named_children


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


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


# ------------------------------------------------------------------------
# Rust built-in types — filtered out from type_ref edges
# ------------------------------------------------------------------------
_RUST_BUILTIN_TYPES = frozenset({
    "bool", "char", "i8", "i16", "i32", "i64", "i128", "isize",
    "u8", "u16", "u32", "u64", "u128", "usize",
    "f32", "f64",
    "str", "String", "Box", "Vec", "Option", "Result",
    "HashMap", "HashSet", "BTreeMap", "BTreeSet",
    "Rc", "Arc", "Cell", "RefCell", "Mutex", "RwLock",
    "Cow", "Path", "PathBuf", "OsString", "CString",
    "Iterator", "IntoIterator", "FromIterator",
    "Fn", "FnMut", "FnOnce",
    "Self", "self", "Self",
})


def _extract_type_names_from_rust_type(type_node, source: bytes) -> list[str]:
    """Extract user-defined type names from a Rust type node, filtering built-ins."""
    if type_node is None:
        return []

    kind = type_node.kind()

    if kind == "type_identifier":
        name = _node_text(type_node, source)
        return [] if name in _RUST_BUILTIN_TYPES else [name]

    if kind == "scoped_type_identifier":
        # e.g. std::hash::Hash → extract the last part or full path
        text = _node_text(type_node, source)
        parts = text.split("::")
        # Only return user-relevant types (skip std/core paths)
        names = []
        for p in parts:
            if p not in _RUST_BUILTIN_TYPES and p not in ("std", "core", "alloc", "self", "Self", "crate", "super"):
                names.append(p)
        return names

    if kind in ("generic_type", "scoped_identifier"):
        # Unwrap generic_type to the inner type_identifier
        if kind == "generic_type":
            tid = _find_named_child(type_node, "type_identifier")
            if tid:
                return _extract_type_names_from_rust_type(tid, source)
        return [_node_text(type_node, source)]

    if kind == "reference_type":
        for child in _named_children(type_node):
            names = _extract_type_names_from_rust_type(child, source)
            if names:
                return names
        return []

    if kind == "pointer_type":
        for child in _named_children(type_node):
            names = _extract_type_names_from_rust_type(child, source)
            if names:
                return names
        return []

    if kind == "tuple_type":
        names: list[str] = []
        for child in _named_children(type_node):
            names.extend(_extract_type_names_from_rust_type(child, source))
        return names

    if kind == "array_type":
        for child in _named_children(type_node):
            if child.kind() == "type_identifier":
                return _extract_type_names_from_rust_type(child, source)
        return []

    return []


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
    elif kind == "self":
        names.append("self")
    else:
        for child in _named_children(node):
            names.extend(_extract_identifiers(child, source))
    return names


def visit_rust(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Rust source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []

    # File-level ID for imports edges (mod declarations are file-level)
    _file_id = _hash_id(file_path, file_path)

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
            "language": "rust",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": _rust_visibility(node, src_bytes, simple_name),
            "is_abstract": 0,
            "is_exported": 0,
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
    # Helper: extract simple type/trait name (strip generics, scoping)
    # -------------------------------------------------------------------
    def _simple_type_name(node, src_bytes) -> str:
        """Extract root type name from a type node, stripping type arguments.
        - generic_type<DataStore<K,V>> → DataStore
        - scoped_type_identifier<std::io::Result> → Result
        - type_identifier → as-is
        """
        if node.kind() == "generic_type":
            tid = _find_named_child(node, "type_identifier")
            if tid:
                return _node_text(tid, src_bytes)
        elif node.kind() == "scoped_type_identifier":
            parts = _node_text(node, src_bytes).split("::")
            return parts[-1] if parts else _node_text(node, src_bytes)
        return _node_text(node, src_bytes)

    # -------------------------------------------------------------------
    # Import handling — use declarations (P51a)
    # -------------------------------------------------------------------
    def _visit_use_declaration(node):
        """Parse 'use std::collections::{HashMap, HashSet};' → imports edges."""
        line = node.start_position().row + 1

        # Tree-sitter Rust patterns:
        #   use std::collections::{HashMap, HashSet}; → scoped_use_list
        #   use std::path::PathBuf;                   → scoped_identifier

        def _walk_use_tree(use_node, prefix=""):
            """Recursively walk a use tree (scoped_use_list or scoped_identifier)."""
            kind = use_node.kind()

            if kind == "scoped_identifier":
                # Leaf: this IS the full import path
                text = _node_text(use_node, src_bytes)
                full = f"{prefix}::{text}" if prefix else text
                target_id = _hash_id(f"{file_path}::{full}", file_path)
                add_edge(_file_id, target_id, "imports", line, target_text=full)

            elif kind == "scoped_use_list":
                # Has a prefix (scoped_identifier) + body (use_list)
                prefix_node = _find_named_child(use_node, "scoped_identifier")
                body_node = _find_named_child(use_node, "use_list")

                if prefix_node:
                    new_prefix = _node_text(prefix_node, src_bytes)
                else:
                    new_prefix = prefix

                if body_node:
                    for item in _named_children(body_node):
                        item_kind = item.kind()
                        if item_kind == "identifier":
                            name = _node_text(item, src_bytes)
                            full = f"{new_prefix}::{name}"
                            target_id = _hash_id(f"{file_path}::{full}", file_path)
                            add_edge(_file_id, target_id, "imports", line, target_text=full)
                        elif item_kind == "self":
                            # use std::fmt::{self, ...} → import the module itself
                            target_id = _hash_id(f"{file_path}::{new_prefix}", file_path)
                            add_edge(_file_id, target_id, "imports", line, target_text=new_prefix)
                        elif item_kind == "scoped_identifier":
                            name = _node_text(item, src_bytes)
                            full = f"{new_prefix}::{name}"
                            target_id = _hash_id(f"{file_path}::{full}", file_path)
                            add_edge(_file_id, target_id, "imports", line, target_text=full)
                        elif item_kind == "scoped_use_list":
                            # Nested use list
                            _walk_use_tree(item, new_prefix)
                elif prefix_node and not body_node:
                    # No use_list, just a scoped_identifier → treat as leaf
                    text = _node_text(prefix_node, src_bytes)
                    full = f"{prefix}::{text}" if prefix else text
                    target_id = _hash_id(f"{file_path}::{full}", file_path)
                    add_edge(_file_id, target_id, "imports", line, target_text=full)

        # Walk the top-level use tree
        for child in _children(node):
            ckind = child.kind()
            if ckind == "scoped_use_list":
                _walk_use_tree(child)
            elif ckind == "scoped_identifier":
                _walk_use_tree(child)

    # -------------------------------------------------------------------
    # Mod declaration handling (P51b)
    # -------------------------------------------------------------------
    def _visit_mod_item(node):
        """Parse 'mod database;' → imports edge."""
        name_node = _find_named_child(node, "identifier")
        if not name_node:
            # Named child might be 'name' field
            name_node = node.child_by_field_name("name")
        if name_node:
            module_name = _node_text(name_node, src_bytes)
            line = node.start_position().row + 1
            target_id = _hash_id(f"{file_path}::{module_name}", file_path)
            add_edge(_file_id, target_id, "imports", line, target_text=module_name)

    # -------------------------------------------------------------------
    # Attribute handling — derive macros → decorates edges (P51c)
    # -------------------------------------------------------------------
    def _visit_attribute_item(node):
        """Parse #[derive(Debug, Clone)] → decorates edges."""
        line = node.start_position().row + 1

        # tree-sitter-rust uses 'attribute' node (not 'meta_item')
        attr = _find_named_child(node, "attribute")
        if not attr:
            return

        # Extract the attribute name (e.g. "derive")
        attr_name_node = _find_named_child(attr, "identifier")
        if not attr_name_node:
            return
        attr_name = _node_text(attr_name_node, src_bytes)

        # For derive attributes, extract the trait names from token_tree
        if attr_name == "derive":
            # token_tree contains (Debug, Clone, PartialEq, ...)
            token_tree = _find_named_child(attr, "token_tree")
            if token_tree:
                for child in _named_children(token_tree):
                    if child.kind() == "identifier":
                        trait_name = _node_text(child, src_bytes)
                        target_id = _hash_id(f"{file_path}::{trait_name}", file_path)
                        add_edge(_file_id, target_id, "decorates", line,
                                 target_text=trait_name)
        elif attr_name == "cfg":
            pass  # cfg attributes are not functional decorates
        else:
            # Other attributes like #[allow(...)], #[test], etc.
            target_id = _hash_id(f"{file_path}::{attr_name}", file_path)
            add_edge(_file_id, target_id, "decorates", line,
                     target_text=attr_name)

    # -------------------------------------------------------------------
    # Type reference extraction from generics, params, return types (P51d)
    # -------------------------------------------------------------------
    def _extract_type_refs(func_node, nid, src_bytes):
        """Extract type_ref edges from generic params, function params, and return type."""
        line = func_node.start_position().row + 1

        # Generic type parameters: <K, V> or <R: Read>
        type_params = _find_child(func_node, "type_parameters")
        if type_params:
            for child in _named_children(type_params):
                if child.kind() == "type_parameter":
                    # type_parameter contains type_identifier + optional trait_bounds
                    tid = _find_named_child(child, "type_identifier")
                    if tid:
                        type_name = _node_text(tid, src_bytes)
                        if type_name not in _RUST_BUILTIN_TYPES:
                            add_edge(nid,
                                     _hash_id(f"{file_path}::{type_name}", file_path),
                                     "type_ref", line, target_text=type_name)
                    # Trait bounds inside type_parameter: R: Read + Write
                    bounds = _find_named_child(child, "trait_bounds")
                    if bounds:
                        for bc in _named_children(bounds):
                            for bname in _extract_type_names_from_rust_type(bc, src_bytes):
                                add_edge(nid,
                                         _hash_id(f"{file_path}::{bname}", file_path),
                                         "type_ref", line, target_text=bname)
                elif child.kind() == "lifetime":
                    # 'a → extract the identifier
                    lt_id = _find_named_child(child, "identifier")
                    if lt_id:
                        lt_name = _node_text(lt_id, src_bytes)
                        add_edge(nid,
                                 _hash_id(f"{file_path}::'{lt_name}", file_path),
                                 "type_ref", line, target_text=f"'{lt_name}")

            # Also check for trait_bounds at top level of type_parameters
            bounds = _find_named_child(type_params, "trait_bounds")
            if bounds:
                for bc in _named_children(bounds):
                    for bname in _extract_type_names_from_rust_type(bc, src_bytes):
                        add_edge(nid,
                                 _hash_id(f"{file_path}::{bname}", file_path),
                                 "type_ref", line, target_text=bname)

        # Where clause bounds
        where_clause = _find_child(func_node, "where_clause")
        if where_clause:
            for child in _named_children(where_clause):
                if child.kind() == "where_predicate":
                    for gc in _named_children(child):
                        for type_name in _extract_type_names_from_rust_type(gc, src_bytes):
                            add_edge(nid,
                                     _hash_id(f"{file_path}::{type_name}", file_path),
                                     "type_ref", line, target_text=type_name)

        # Function parameter types
        params = _find_child(func_node, "parameters")
        if params:
            for child in _named_children(params):
                if child.kind() == "parameter":
                    typ_node = _find_named_child(child, "type_identifier")
                    if not typ_node:
                        # Look for reference_type, generic_type, scoped_type_identifier, etc.
                        for tc in _named_children(child):
                            for type_name in _extract_type_names_from_rust_type(tc, src_bytes):
                                add_edge(nid,
                                         _hash_id(f"{file_path}::{type_name}", file_path),
                                         "type_ref", line, target_text=type_name)
                    else:
                        type_name = _node_text(typ_node, src_bytes)
                        if type_name not in _RUST_BUILTIN_TYPES:
                            add_edge(nid,
                                     _hash_id(f"{file_path}::{type_name}", file_path),
                                     "type_ref", line, target_text=type_name)

        # Return type
        return_type = _find_child(func_node, "return_type")
        if return_type:
            for child in _named_children(return_type):
                for type_name in _extract_type_names_from_rust_type(child, src_bytes):
                    add_edge(nid,
                             _hash_id(f"{file_path}::{type_name}", file_path),
                             "type_ref", line, target_text=type_name)

    # -------------------------------------------------------------------
    # Variable reads / writes extraction (P51e)
    # -------------------------------------------------------------------
    def _extract_reads_writes(body_node, caller_id, src_bytes):
        _walk_reads_writes(body_node, caller_id, src_bytes)

    def _walk_reads_writes(node, caller_id, src_bytes):
        """Walk function/method body to detect reads and writes."""
        kind = node.kind()

        if kind == "let_declaration":
            # let pattern = value;
            pattern = _find_child(node, "let_pattern")
            if not pattern:
                pattern = node  # fallback
            # Extract identifiers from pattern (left side) → writes
            for child in _named_children(pattern):
                if child.kind() == "identifier":
                    var_name = _node_text(child, src_bytes)
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "writes", node.start_position().row + 1,
                             target_text=var_name)
                elif child.kind() in ("mutable_identifier", "ref_pattern",
                                       "tuple_pattern", "struct_pattern"):
                    for var_name in _extract_identifiers(child, src_bytes):
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{var_name}", file_path),
                                 "writes", node.start_position().row + 1,
                                 target_text=var_name)

            # Value (right side) → reads
            value = _find_child(node, "value")
            if value:
                for var_name in _extract_identifiers(value, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)
            return  # let_declaration is self-contained, don't recurse deeper (value already handled)

        elif kind == "assignment_expression":
            left = _find_child(node, "left_operand")
            if not left:
                left = node.child_by_field_name("left")
            right = _find_child(node, "right_operand")
            if not right:
                right = node.child_by_field_name("right")
            operator_node = _find_child(node, "compound_assignment_operator")
            if not operator_node:
                operator_node = _find_child(node, "operator")

            # Compound assignment (+=, -=, etc.) → both read + write on left
            is_compound = operator_node is not None

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

        elif kind == "call_expression":
            # Arguments to function calls → reads
            args = _find_child(node, "arguments")
            if args:
                for var_name in _extract_identifiers(args, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        elif kind == "field_expression":
            # obj.field → reads to both obj and field
            for var_name in _extract_identifiers(node, src_bytes):
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{var_name}", file_path),
                         "reads", node.start_position().row + 1,
                         target_text=var_name)

        elif kind == "return_expression":
            # return expr → reads to expr
            for child in _named_children(node):
                for var_name in _extract_identifiers(child, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", node.start_position().row + 1,
                             target_text=var_name)

        # Recurse into children
        for child in _named_children(node):
            _walk_reads_writes(child, caller_id, src_bytes)

    # -------------------------------------------------------------------
    # Main visitor
    # -------------------------------------------------------------------
    def _visit(node):
        kind = node.kind()

        if kind == "use_declaration":
            _visit_use_declaration(node)
            return  # fully handled, don't recurse
        elif kind == "mod_item":
            _visit_mod_item(node)
            return
        elif kind in ("attribute_item", "inner_attribute_item"):
            _visit_attribute_item(node)
            # Don't return — may contain items like struct/fn that need visiting
        elif kind == "function_item":
            _visit_function(node)
        elif kind == "struct_item":
            _visit_struct(node)
        elif kind == "impl_item":
            _visit_impl(node)
        elif kind == "trait_item":
            _visit_trait(node)
        elif kind == "enum_item":
            _visit_enum(node)

        # Recurse (but not into function/struct/impl bodies which are handled internally)
        if kind not in ("function_item", "struct_item", "impl_item",
                        "trait_item", "enum_item", "block",
                        "use_declaration", "mod_item"):
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

        # Type references from generic params and function signature (P51d)
        _extract_type_refs(node, nid, src_bytes)

        body = _find_child(node, "block")
        if body:
            _extract_calls(body, nid, src_bytes)
            _extract_reads_writes(body, nid, src_bytes)

        name_stack.pop()
        node_stack.pop()

    def _visit_struct(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)

        nid = add_node("class", name, node)

        # Type references for generic parameters on struct
        _extract_type_refs(node, nid, src_bytes)

        # Find fields
        body = _find_child(node, "field_declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "field_declaration":
                    fname = _find_named_child(child, "field_identifier")
                    if fname:
                        fn = _node_text(fname, src_bytes)
                        fid = add_node("property", fn, child)
                        add_edge(nid, fid, "contains", child.start_position().row + 1)
            name_stack.pop()
            node_stack.pop()

    def _visit_impl(node):
        """Handle impl blocks — trait impls produce implements edges."""
        target_text = None
        trait_text = None
        body = _find_child(node, "declaration_list")
        if not body:
            return

        # Try field-based access first (tree-sitter-rust uses "trait" and "type" fields)
        trait_field = node.child_by_field_name("trait")
        type_field = node.child_by_field_name("type")

        if trait_field and type_field:
            # Trait impl: impl Trait for Type
            trait_text = _simple_type_name(trait_field, src_bytes)
            target_text = _simple_type_name(type_field, src_bytes)
        else:
            # Fallback: count type-related named children
            type_children = []
            for child in _named_children(node):
                if child.kind() in ("type_identifier", "generic_type"):
                    type_children.append(child)

            if len(type_children) >= 2:
                trait_text = _simple_type_name(type_children[0], src_bytes)
                target_text = _simple_type_name(type_children[-1], src_bytes)
            elif len(type_children) == 1:
                target_text = _simple_type_name(type_children[0], src_bytes)

        if not target_text:
            return

        # Create implements edge if trait is detected
        if trait_text:
            target_qname = f"{file_path}::{target_text}"
            target_id = _hash_id(target_qname, file_path)
            trait_qname = f"{file_path}::{trait_text}"
            trait_id = _hash_id(trait_qname, file_path)
            add_edge(target_id, trait_id, "implements",
                     node.start_position().row + 1,
                     target_text=trait_qname)

        name_stack.append(target_text)
        parent_q = make_qualified(target_text)
        parent_id = _hash_id(parent_q, file_path)
        node_stack.append(parent_id)

        for child in _named_children(body):
            if child.kind() == "function_item":
                _visit_function(child)

        name_stack.pop()
        node_stack.pop()

    def _visit_trait(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        nid = add_node("interface", name, node)

        # Type references for generic/lifetime params on trait
        _extract_type_refs(node, nid, src_bytes)

        body = _find_child(node, "declaration_list")
        if body:
            name_stack.append(name)
            node_stack.append(nid)
            for child in _named_children(body):
                if child.kind() == "function_item":
                    _visit_function(child)
            name_stack.pop()
            node_stack.pop()

    def _visit_enum(node):
        name_node = _find_named_child(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        add_node("enum", name, node)

    # -------------------------------------------------------------------
    # Call extraction — includes macro invocations (P51f)
    # -------------------------------------------------------------------
    def _extract_calls(body, caller_id, src_bytes):
        _walk_calls(body, caller_id, src_bytes)

    def _walk_calls(node, caller_id, src_bytes):
        kind = node.kind()

        if kind == "call_expression":
            fn = _find_named_child(node, "identifier")
            if not fn:
                fn = _find_named_child(node, "field_expression")
                if fn:
                    fn = _find_named_child(fn, "field_identifier")
            if not fn:
                fn = _find_named_child(node, "scoped_identifier")
            if fn:
                callee = _node_text(fn, src_bytes)
                target = f"{file_path}::{callee}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)

        elif kind == "macro_invocation":
            # println!(...), format!(...), write!(...), vec![...], etc.
            macro_name_node = _find_named_child(node, "identifier")
            if not macro_name_node:
                # scoped_identifier for module::macro! calls
                macro_name_node = _find_named_child(node, "scoped_identifier")
            if macro_name_node:
                macro_name = _node_text(macro_name_node, src_bytes)
                target = f"{file_path}::{macro_name}"
                add_edge(caller_id, _hash_id(target, file_path), "calls",
                         node.start_position().row + 1, target)

        for child in _named_children(node):
            _walk_calls(child, caller_id, src_bytes)

    # -------------------------------------------------------------------
    # Walk the tree
    # -------------------------------------------------------------------
    for child in _named_children(tree.root_node()):
        _visit(child)

    return result


def _rust_visibility(node, src_bytes, name: str) -> str:
    """Rust visibility: pub → public, pub(crate) → internal, else private."""
    vis_node = _find_child(node, "visibility_modifier")
    if vis_node:
        vis_text = _node_text(vis_node, src_bytes)
        if "crate" in vis_text:
            return "internal"
        return "public"
    return "private"


def _build_sig(node, src_bytes) -> str:
    """Build a simple signature from parameters."""
    params = _find_child(node, "parameters")
    if not params:
        return "()"
    sig_parts = []
    for child in _named_children(params):
        if child.kind() == "parameter":
            pat = _find_named_child(child, "identifier")
            typ = _find_named_child(child, "type_identifier")
            if pat and typ:
                sig_parts.append(
                    f"{_node_text(pat, src_bytes)}: {_node_text(typ, src_bytes)}"
                )
            elif pat:
                sig_parts.append(_node_text(pat, src_bytes))
    return f"({', '.join(sig_parts)})"
