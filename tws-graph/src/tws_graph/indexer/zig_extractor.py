"""Zig source extractor — walks tree-sitter CST for symbols and edges.

Uses tree-sitter >= 0.25 method-based API.

Zig tree-sitter (PascalCase node types):
  - Decl         — wrapper for top-level / container-level declarations
  - VarDecl      — const/var variable declaration
  - FnProto      — function prototype (name + params + return type)
  - Block        — code block (function body)
  - ContainerDecl — struct / enum / union body
  - ContainerDeclType — "struct", "enum", "union" keyword
  - ContainerField — field inside a container
  - ErrorSetDecl — error set body  error{ ... }
  - TestDecl     — test block
  - ComptimeDecl — comptime block

Visibility: the ``pub`` keyword is an unnamed sibling before the target named
node (Decl or ContainerField).  We track it during child iteration.

Edge types produced:
  - calls       (function calls, method calls)
  - contains    (struct fields, struct methods)
  - imports     (@import expressions, usingnamespace)
  - type_ref    (function parameter / return types)
  - reads       (variable reads in function bodies)
  - writes      (variable writes: var/const declarations, assignments)
"""

import hashlib
from .base import ExtractionResult


# ---------------------------------------------------------------------------
# Helpers
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


def _find_descendant(node, target_kind: str):
    """Depth-first search for a descendant of the given kind."""
    if node.kind() == target_kind:
        return node
    for child in _named_children(node):
        result = _find_descendant(child, target_kind)
        if result:
            return result
    return None


def _collect_descendants(node, target_kind: str) -> list:
    """Collect all descendants (including self) of the given kind."""
    result = []
    if node.kind() == target_kind:
        result.append(node)
    for child in _named_children(node):
        result.extend(_collect_descendants(child, target_kind))
    return result


# ---------------------------------------------------------------------------
# Zig built-in types — filtered out from type_ref edges
# ---------------------------------------------------------------------------
_ZIG_BUILTINS = frozenset({
    "bool", "void", "noreturn", "anyerror",
    "i8", "i16", "i32", "i64", "i128", "isize",
    "u8", "u16", "u32", "u64", "u128", "usize",
    "f16", "f32", "f64", "f80", "f128",
    "c_short", "c_ushort", "c_int", "c_uint",
    "c_long", "c_ulong", "c_longlong", "c_ulonglong",
    "c_longdouble", "comptime_int", "comptime_float",
    "type", "anytype", "anyframe",
    "anyopaque", "Self",
})


def _extract_type_names(type_node, src_bytes) -> list[str]:
    """Recursively extract user type names from a Zig type expression node."""
    names: list[str] = []
    kind = type_node.kind()

    if kind == "IDENTIFIER":
        name = _node_text(type_node, src_bytes)
        if name not in _ZIG_BUILTINS:
            names.append(name)
    elif kind == "BuildinTypeExpr":
        pass  # built-in type — ignore
    elif kind in ("ErrorUnionExpr", "SuffixExpr"):
        for child in _named_children(type_node):
            names.extend(_extract_type_names(child, src_bytes))
    elif kind == "PrefixTypeOp":
        for child in _named_children(type_node):
            names.extend(_extract_type_names(child, src_bytes))
    elif kind == "ParamType":
        for child in _named_children(type_node):
            names.extend(_extract_type_names(child, src_bytes))
    else:
        for child in _named_children(type_node):
            names.extend(_extract_type_names(child, src_bytes))
    return names


def _extract_identifiers(node, src_bytes) -> list[str]:
    """Recursively extract all simple identifier names from an expression node."""
    names: list[str] = []
    kind = node.kind()

    if kind == "IDENTIFIER":
        name = _node_text(node, src_bytes)
        if name and name != "_":
            names.append(name)
    elif kind == "STRINGLITERALSINGLE":
        pass  # string literals are not variable names
    elif kind == "FieldOrFnCall":
        # For .status in self.status — extract the field name too
        fn_name = _find_named_child(node, "IDENTIFIER")
        if fn_name:
            names.append(_node_text(fn_name, src_bytes))
        # Also check for FnCallArguments → don't extract those as variables
    elif kind == "FnCallArguments":
        pass  # don't recurse into call arguments for simple identifier extraction
    elif kind == "FieldInit":
        pass  # .field = value in init lists — not variable reads
    elif kind == "InitList":
        pass  # struct initialization — not variable reads
    elif kind == "BuildinTypeExpr":
        pass  # built-in types
    else:
        for child in _named_children(node):
            names.extend(_extract_identifiers(child, src_bytes))
    return names


def _build_sig(fn_proto, src_bytes) -> str:
    """Build a human-readable parameter signature from a FnProto node."""
    params = _find_named_child(fn_proto, "ParamDeclList")
    if not params:
        return "()"
    sig_parts = []
    for param in _find_all_named_children(params, "ParamDecl"):
        pname = _find_named_child(param, "IDENTIFIER")
        ptype = _find_named_child(param, "ParamType")
        if pname and ptype:
            sig_parts.append(
                f"{_node_text(pname, src_bytes)}: {_node_text(ptype, src_bytes)[:40]}"
            )
        elif pname:
            sig_parts.append(_node_text(pname, src_bytes))
    return f"({', '.join(sig_parts)})"


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def visit_zig(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Zig source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []
    _file_id = _hash_id(file_path, file_path)

    # -- helpers for building nodes / edges --------------------------------

    def make_qualified(simple_name: str) -> str:
        parts = [file_path] + name_stack + [simple_name]
        return "::".join(parts)

    def add_node(kind: str, simple_name: str, node, **extra) -> str:
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
            "language": "zig",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": extra.pop("visibility", "public"),
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        result.nodes.append(record)
        return nid

    def add_edge(source: str, target: str, kind: str, line: int,
                  target_text: str | None = None):
        edge = {
            "source": source, "target": target, "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # -- visit dispatcher --------------------------------------------------

    def _walk_children(parent, container_id: str | None = None):
        """Walk all children of *parent*, tracking ``pub`` visibility."""
        saw_pub = False
        for i in range(parent.child_count()):
            child = parent.child(i)
            if not child.is_named():
                text = _node_text(child, src_bytes)
                if text == "pub":
                    saw_pub = True
                continue
            _dispatch(child, saw_pub, container_id)
            saw_pub = False  # pub only applies to immediate next named child

    def _dispatch(node, saw_pub: bool, container_id: str | None):
        kind = node.kind()

        if kind == "Decl":
            _visit_decl(node, saw_pub, container_id)
        elif kind == "TestDecl":
            _visit_test(node)
        elif kind == "ComptimeDecl":
            _visit_comptime(node)
        elif kind == "ContainerField":
            _visit_field(node, saw_pub, container_id)
        elif kind == "ERROR":
            # tree-sitter-zig produces ERROR nodes for pub fields
            # The actual field data may be in the error children
            _visit_error_field(node, saw_pub, container_id)
        elif kind in ("line_comment", "document_comment"):
            pass  # skip comments
        # else: skip unknown top-level nodes

    # -- Decl visitor ------------------------------------------------------

    def _visit_decl(decl, saw_pub: bool, container_id: str | None):
        """Dispatch a Decl node: function, variable (struct/enum/error/var), or expression."""
        fn_proto = _find_named_child(decl, "FnProto")
        var_decl = _find_named_child(decl, "VarDecl")
        block = _find_named_child(decl, "Block")

        if fn_proto:
            _visit_function(fn_proto, block, saw_pub, container_id)
        elif var_decl:
            _visit_var_decl(var_decl, saw_pub, container_id)
            # Scan for @import inside any var decl (e.g. const std = @import("std"))
            _extract_imports_from_node(var_decl)
        elif block:
            # Expression decl with a block
            _visit_expression_decl(decl, saw_pub)
        else:
            # Expression-only decl: usingnamespace, bare @import, etc.
            _extract_imports_from_node(decl)

    # -- Function visitor --------------------------------------------------

    def _visit_function(fn_proto, block, saw_pub: bool, container_id: str | None):
        name_node = _find_named_child(fn_proto, "IDENTIFIER")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        visibility = "public" if saw_pub else "private"

        sig = _build_sig(fn_proto, src_bytes)
        nid = add_node("function", name, fn_proto, signature=sig,
                       visibility=visibility)

        if container_id:
            add_edge(container_id, nid, "contains",
                     fn_proto.start_position().row + 1)

        name_stack.append(name)
        node_stack.append(nid)

        _extract_type_refs(fn_proto, nid)

        if block:
            _extract_calls(block, nid)
            _extract_reads_writes(block, nid)

        name_stack.pop()
        node_stack.pop()

    # -- VarDecl visitor ---------------------------------------------------

    def _visit_var_decl(var_decl, saw_pub: bool, container_id: str | None):
        name_node = _find_named_child(var_decl, "IDENTIFIER")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        visibility = "public" if saw_pub else "private"

        # Determine what kind of declaration this is by looking at the value
        container = _find_descendant(var_decl, "ContainerDecl")
        error_set = _find_descendant(var_decl, "ErrorSetDecl")

        if container:
            decl_type_node = _find_named_child(container, "ContainerDeclType")
            decl_type_text = _node_text(decl_type_node, src_bytes) if decl_type_node else ""

            if "enum" in decl_type_text:
                node_kind = "enum"
            elif "struct" in decl_type_text or "union" in decl_type_text:
                node_kind = "class"
            else:
                node_kind = "class"

            nid = add_node(node_kind, name, var_decl, visibility=visibility)

            if container_id:
                add_edge(container_id, nid, "contains",
                         var_decl.start_position().row + 1)

            # Push scope and walk container children (fields + methods)
            name_stack.append(name)
            node_stack.append(nid)
            _walk_children(container, nid)
            name_stack.pop()
            node_stack.pop()

        elif error_set:
            nid = add_node("enum", name, var_decl, visibility=visibility)
            if container_id:
                add_edge(container_id, nid, "contains",
                         var_decl.start_position().row + 1)
        else:
            # Regular variable (const or var)
            nid = add_node("variable", name, var_decl, visibility=visibility)
            if container_id:
                add_edge(container_id, nid, "contains",
                         var_decl.start_position().row + 1)

    # -- Expression decl (usingnamespace, etc.) ---------------------------

    def _visit_expression_decl(decl, saw_pub: bool):
        """Handle expression-only decls: usingnamespace @import(...)."""
        line = decl.start_position().row + 1
        # Find any @import BUILTINIDENTIFIER in the decl
        for bi in _collect_descendants(decl, "BUILTINIDENTIFIER"):
            bi_text = _node_text(bi, src_bytes)
            if bi_text == "@import":
                # Navigate to find the FnCallArguments in the same SuffixExpr
                # BUILTINIDENTIFIER is inside SuffixExpr which also has FnCallArguments
                suffix_expr = bi
                while suffix_expr and suffix_expr.kind() != "SuffixExpr":
                    suffix_expr = suffix_expr.parent()
                if suffix_expr:
                    fn_args = _find_named_child(suffix_expr, "FnCallArguments")
                    if fn_args:
                        str_lit = _find_descendant(fn_args, "STRINGLITERALSINGLE")
                        if str_lit:
                            path = _node_text(str_lit, src_bytes).strip('"')
                            target_id = _hash_id(f"{file_path}::{path}", file_path)
                            add_edge(_file_id, target_id, "imports", line,
                                     target_text=path)

    # -- Test visitor ------------------------------------------------------

    def _visit_test(node):
        """TestDecl: STRINGLITERALSINGLE + Block."""
        block = _find_named_child(node, "Block")
        str_node = _find_named_child(node, "STRINGLITERALSINGLE")
        test_name = _node_text(str_node, src_bytes).strip('"') if str_node else "unnamed"

        nid = add_node("function", f"test.{test_name}", node, visibility="private")

        name_stack.append(f"test.{test_name}")
        node_stack.append(nid)

        if block:
            _extract_calls(block, nid)
            _extract_reads_writes(block, nid)

        name_stack.pop()
        node_stack.pop()

    # -- Comptime visitor --------------------------------------------------

    def _visit_comptime(node):
        """ComptimeDecl: comptime { ... } — extract calls & imports inside."""
        block = _find_named_child(node, "Block")
        if block:
            _extract_calls(block, _file_id)
            # Also check for @import inside comptime
            _extract_imports_from_node(block)

    # -- Field visitor -----------------------------------------------------

    def _visit_field(field, saw_pub: bool, container_id: str | None):
        """ContainerField inside a struct/enum/union."""
        name_node = _find_named_child(field, "IDENTIFIER")
        if not name_node:
            return
        name = _node_text(name_node, src_bytes)
        visibility = "public" if saw_pub else "private"

        fid = add_node("property", name, field, visibility=visibility)
        if container_id:
            add_edge(container_id, fid, "contains",
                     field.start_position().row + 1)

    # -- Error field (pub fields produce ERROR nodes in tree-sitter-zig) ---

    def _visit_error_field(error_node, saw_pub: bool, container_id: str | None):
        """Handle ERROR nodes that contain pub fields.

        tree-sitter-zig may parse ``pub value: u32`` as:
          ERROR [named]
            pub [UNNAMED]
            ERROR [named] = "val"??  — fragile, skip or best-effort
            : [UNNAMED]
            ContainerField = "32"   — just the type part

        We try to extract the field name from the error's children.
        """
        # Best-effort: look at the first named child that is an ERROR
        # and try to find an IDENTIFIER inside it
        for child in _named_children(error_node):
            if child.kind() == "ERROR":
                for gc in _named_children(child):
                    if gc.kind() == "IDENTIFIER":
                        name = _node_text(gc, src_bytes)
                        visibility = "public" if saw_pub else "private"
                        fid = add_node("property", name, error_node,
                                       visibility=visibility)
                        if container_id:
                            add_edge(container_id, fid, "contains",
                                     error_node.start_position().row + 1)
                        return

    # -- Imports extraction ------------------------------------------------

    def _extract_imports_from_node(expr_node):
        """Find @import("path") in a node tree and emit imports edges."""
        line = expr_node.start_position().row + 1
        for bi in _collect_descendants(expr_node, "BUILTINIDENTIFIER"):
            bi_text = _node_text(bi, src_bytes)
            if bi_text == "@import":
                # Walk up to find the SuffixExpr that contains FnCallArguments
                suffix = bi
                while suffix and suffix.kind() != "SuffixExpr":
                    suffix = suffix.parent()
                if suffix:
                    fn_args = _find_named_child(suffix, "FnCallArguments")
                    if fn_args:
                        str_lit = _find_descendant(fn_args, "STRINGLITERALSINGLE")
                        if str_lit:
                            path = _node_text(str_lit, src_bytes).strip('"')
                            target_id = _hash_id(f"{file_path}::{path}", file_path)
                            add_edge(_file_id, target_id, "imports", line,
                                     target_text=path)

    # -- Type references ---------------------------------------------------

    def _extract_type_refs(fn_proto, nid):
        """Extract type_ref edges from function params and return type."""
        line = fn_proto.start_position().row + 1

        # Parameter types
        params = _find_named_child(fn_proto, "ParamDeclList")
        if params:
            for param in _find_all_named_children(params, "ParamDecl"):
                ptype = _find_named_child(param, "ParamType")
                if ptype:
                    for type_name in _extract_type_names(ptype, src_bytes):
                        add_edge(nid,
                                 _hash_id(f"{file_path}::{type_name}", file_path),
                                 "type_ref", line, target_text=type_name)

        # Return type — look for ErrorUnionExpr as direct named child of FnProto
        # (after ParamDeclList)
        for child in _named_children(fn_proto):
            if child.kind() == "ErrorUnionExpr":
                for type_name in _extract_type_names(child, src_bytes):
                    add_edge(nid,
                             _hash_id(f"{file_path}::{type_name}", file_path),
                             "type_ref", line, target_text=type_name)

    # -- Call extraction ---------------------------------------------------

    def _extract_calls(body, caller_id):
        _walk_calls(body, caller_id)

    def _walk_calls(node, caller_id):
        kind = node.kind()

        if kind == "SuffixExpr":
            children = list(_named_children(node))
            if len(children) >= 2 and children[0].kind() == "IDENTIFIER":
                fn_args = children[1] if children[1].kind() == "FnCallArguments" else None
                field_call = children[1] if children[1].kind() == "FieldOrFnCall" else None

                if fn_args:
                    callee = _node_text(children[0], src_bytes)
                    target = f"{file_path}::{callee}"
                    add_edge(caller_id, _hash_id(target, file_path), "calls",
                             node.start_position().row + 1, target)
                elif field_call:
                    # Method call: obj.method(args)
                    fn_name = _find_named_child(field_call, "IDENTIFIER")
                    fn_args_inner = _find_named_child(field_call, "FnCallArguments")
                    if fn_name and fn_args_inner:
                        callee = _node_text(fn_name, src_bytes)
                        target = f"{file_path}::{callee}"
                        add_edge(caller_id, _hash_id(target, file_path), "calls",
                                 node.start_position().row + 1, target)

        # Also handle BUILTINIDENTIFIER calls — @import, @cImport, etc.
        if kind == "SuffixExpr":
            builtin = _find_named_child(node, "BUILTINIDENTIFIER")
            if builtin:
                bi_text = _node_text(builtin, src_bytes)
                fn_args = _find_named_child(node, "FnCallArguments")
                if fn_args and bi_text == "@import":
                    # Don't add a calls edge for @import — it's an import edge
                    pass

        for child in _named_children(node):
            _walk_calls(child, caller_id)

    # -- Read / Write extraction ------------------------------------------

    def _extract_reads_writes(body_node, caller_id):
        _walk_reads_writes(body_node, caller_id)

    def _walk_reads_writes(node, caller_id):
        kind = node.kind()
        line = node.start_position().row + 1

        if kind == "VarDecl":
            name_node = _find_named_child(node, "IDENTIFIER")
            if name_node:
                var_name = _node_text(name_node, src_bytes)
                # Write: variable being declared
                add_edge(caller_id,
                         _hash_id(f"{file_path}::{var_name}", file_path),
                         "writes", line, target_text=var_name)

            # Read: any identifiers in the initializer value expression.
            # Skip the name and optional type annotation; extract reads from
            # the value expression(s).
            named = list(_named_children(node))
            saw_name = False
            saw_type = False
            # Count expression-like children to distinguish type-or-value
            expr_children = [c for c in named
                             if c.kind() in ("ErrorUnionExpr", "SuffixExpr",
                                             "BuildinTypeExpr", "BinaryExpr",
                                             "PrefixTypeOp")]
            has_explicit_type = len(expr_children) >= 2  # type + value

            for child in named:
                ck = child.kind()
                if ck == "IDENTIFIER" and not saw_name:
                    saw_name = True
                    continue
                if has_explicit_type and not saw_type and ck in (
                    "ErrorUnionExpr", "BuildinTypeExpr", "PrefixTypeOp",
                ):
                    # This is the type annotation — skip
                    saw_type = True
                    continue
                # Everything else is part of the value expression → reads
                for var_name in _extract_identifiers(child, src_bytes):
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{var_name}", file_path),
                             "reads", line, target_text=var_name)
            return  # VarDecl is self-contained

        elif kind == "AssignExpr":
            named = list(_named_children(node))
            has_assign_op = any(c.kind() == "AssignOp" for c in named)

            # Bare AssignExpr without an operator (return, bare call): all reads
            if not has_assign_op:
                for child in named:
                    ck = child.kind()
                    if ck in ("ErrorUnionExpr", "SuffixExpr", "IDENTIFIER",
                              "FieldOrFnCall", "BinaryExpr"):
                        for vn in _extract_identifiers(child, src_bytes):
                            add_edge(caller_id,
                                     _hash_id(f"{file_path}::{vn}", file_path),
                                     "reads", line, target_text=vn)
            else:
                # Has AssignOp (=, +=, etc.): left = writes, right = reads
                left_done = False
                for child in named:
                    ck = child.kind()
                    if ck == "AssignOp":
                        left_done = True
                        continue
                    if ck in ("ErrorUnionExpr", "SuffixExpr", "IDENTIFIER",
                              "FieldOrFnCall", "BinaryExpr"):
                        idents = _extract_identifiers(child, src_bytes)
                        if not left_done:
                            for vn in idents:
                                add_edge(caller_id,
                                         _hash_id(f"{file_path}::{vn}", file_path),
                                         "writes", line, target_text=vn)
                                # Compound assignment (+=, *=, etc.): also read
                                add_edge(caller_id,
                                         _hash_id(f"{file_path}::{vn}", file_path),
                                         "reads", line, target_text=vn)
                            left_done = True
                        else:
                            for vn in idents:
                                add_edge(caller_id,
                                         _hash_id(f"{file_path}::{vn}", file_path),
                                         "reads", line, target_text=vn)

        # Recurse into children for other expression patterns
        for child in _named_children(node):
            _walk_reads_writes(child, caller_id)

    # -----------------------------------------------------------------
    # Main: walk the top-level source file
    # -----------------------------------------------------------------

    _walk_children(tree.root_node())

    return result
