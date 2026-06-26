"""Groovy source extractor — walks tree-sitter CST for symbols and edges.

The Groovy tree-sitter grammar uses generic node types (command, unit, block,
func, identifier, decorate) rather than typed declaration nodes. This extractor
identifies declarations by keyword patterns on the first unit child of a command.

Uses tree-sitter >= 0.25 method-based API.
"""

import hashlib
from .base import ExtractionResult, children as _children, named_children as _named_children


# ---------------------------------------------------------------------------
# Shared helpers (module-level for reuse)
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


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


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


def _first_identifier_text(node, source: bytes) -> str | None:
    """Extract the text of the first identifier descendant of a node."""
    if node.kind() == "identifier":
        return _node_text(node, source)
    for child in _named_children(node):
        result = _first_identifier_text(child, source)
        if result:
            return result
    return None


def _get_block(node):
    """Return the first block child of a node."""
    return _find_named_child(node, "block")


def _get_func_node(node):
    """Find the first func node in a subtree."""
    if node.kind() == "func":
        return node
    for child in _named_children(node):
        result = _get_func_node(child)
        if result:
            return result
    return None


def _has_func(node) -> bool:
    """Check if a node contains a func node (indicating a method/constructor)."""
    if node.kind() == "func":
        return True
    for child in _named_children(node):
        if _has_func(child):
            return True
    return False


def _get_func_name(func_node, source: bytes) -> str | None:
    """Extract the method name from a func node."""
    return _first_identifier_text(func_node, source)


def _extract_identifiers_recursive(node, source: bytes) -> list[str]:
    """Recursively extract all identifier names from a node."""
    names = []
    if node.kind() == "identifier":
        name = _node_text(node, source)
        if name and name != "_":
            names.append(name)
    else:
        for child in _named_children(node):
            names.extend(_extract_identifiers_recursive(child, source))
    return names


def _extract_modifiers_from_command(cmd, source: bytes) -> list[str]:
    """Extract visibility/static/final modifiers from a command's unit children."""
    modifiers = []
    for child in _named_children(cmd):
        if child.kind() == "unit":
            kw = _first_identifier_text(child, source)
            if kw in ("public", "private", "protected", "static", "final"):
                modifiers.append(kw)
    return modifiers


def _is_annotation_command(cmd, source: bytes) -> bool:
    """Check if a command is purely an annotation (has decorate node, no block)."""
    decorate = _find_named_child(cmd, "decorate")
    if not decorate:
        return False
    # It's an annotation if the first named child is decorate AND no block
    nc = _named_children(cmd)
    first = nc[0] if nc else None
    if first and first.kind() == "decorate":
        return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def visit_groovy(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a Groovy source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    name_stack: list[str] = []
    node_stack: list[str] = []
    # Track parent_type_kind on node_stack too
    type_kind_stack: list[str] = []

    # File-level ID for imports edges
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
            "language": "groovy",
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
            "source": source, "target": target, "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        result.edges.append(edge)

    # -------------------------------------------------------------------
    # Import handling
    # -------------------------------------------------------------------
    def _visit_import(command_node):
        """Process import declaration."""
        units = _find_all_named_children(command_node, "unit")
        if len(units) < 2:
            return
        # Import path is dot-separated identifiers in unit[1]
        import_unit = units[1]
        path_parts = []
        for child in _named_children(import_unit):
            if child.kind() == "identifier":
                path_parts.append(_node_text(child, src_bytes))
        import_path = ".".join(path_parts) if path_parts else _node_text(import_unit, src_bytes)

        target_id = _hash_id(f"{file_path}::{import_path}", file_path)
        add_edge(_file_id, target_id, "imports",
                 command_node.start_position().row + 1, target_text=import_path)

    # -------------------------------------------------------------------
    # Annotation handling
    # -------------------------------------------------------------------
    def _process_annotation(decorate_node, target_nid):
        """Process a single @name annotation from a decorate node."""
        ident = _find_named_child(decorate_node, "identifier")
        if not ident:
            return
        anno_name = _node_text(ident, src_bytes)
        target_text = f"@{anno_name}"
        add_edge(
            target_nid,
            _hash_id(target_text, file_path),
            "decorates",
            decorate_node.start_position().row + 1,
            target_text=target_text,
        )

    def _apply_pending_annotations(pending, target_nid):
        """Apply collected annotation commands to a target node."""
        for cmd in pending:
            decorate = _find_named_child(cmd, "decorate")
            if decorate:
                _process_annotation(decorate, target_nid)

    # -------------------------------------------------------------------
    # Method/Constructor visitor
    # -------------------------------------------------------------------
    def _visit_method_command(command_node, parent_nid):
        """Visit a method or constructor command."""
        modifiers = _extract_modifiers_from_command(command_node, src_bytes)

        blk = _get_block(command_node)
        if not blk:
            return

        func_node = _get_func_node(blk)
        if not func_node:
            return

        method_name = _get_func_name(func_node, src_bytes)
        if not method_name:
            return

        # Determine if it's a constructor (name matches enclosing class name)
        kind = "method"
        if name_stack and method_name == name_stack[-1]:
            kind = "constructor"

        vis = "public"
        for mod in ("private", "protected"):
            if mod in modifiers:
                vis = mod
                break

        nid = add_node(kind, method_name, func_node, visibility=vis)

        if parent_nid:
            add_edge(parent_nid, nid, "contains", command_node.start_position().row + 1)

        name_stack.append(method_name)
        node_stack.append(nid)

        # Walk block body for calls and reads/writes
        _walk_for_calls_and_rw(blk, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Field visitor
    # -------------------------------------------------------------------
    def _visit_field_command(command_node, parent_nid):
        """Visit a field declaration command."""
        modifiers = _extract_modifiers_from_command(command_node, src_bytes)

        # Get non-modifier units (type + name)
        units = _find_all_named_children(command_node, "unit")
        field_units = []
        for u in units:
            kw = _first_identifier_text(u, src_bytes)
            if kw and kw in ("private", "protected", "public", "static", "final", "def"):
                continue
            field_units.append(u)

        if len(field_units) < 1:
            return

        # Last unit is the field name (type may be implicit with 'def')
        field_name_unit = field_units[-1]
        field_name = _first_identifier_text(field_name_unit, src_bytes)
        if not field_name:
            return

        # Field with default value: type + name before =, already handled by
        # picking the last non-modifier unit as the field name above.

        vis = "public"
        for mod in ("private", "protected"):
            if mod in modifiers:
                vis = mod
                break

        nid = add_node("property", field_name, command_node, visibility=vis)

        if parent_nid:
            add_edge(parent_nid, nid, "contains", command_node.start_position().row + 1)

    # -------------------------------------------------------------------
    # Enum constant visitor
    # -------------------------------------------------------------------
    def _visit_enum_constant(command_node, parent_nid):
        """Visit an enum constant command."""
        units = _find_all_named_children(command_node, "unit")
        for u in units:
            name = _first_identifier_text(u, src_bytes)
            if name:
                nid = add_node("enum_constant", name, u)
                add_edge(parent_nid, nid, "contains", command_node.start_position().row + 1)

    # -------------------------------------------------------------------
    # Call and read/write walking
    # -------------------------------------------------------------------
    def _walk_for_calls_and_rw(body_node, caller_id):
        """Walk a body node for call edges and read/write edges."""
        _walk_commands(body_node, caller_id)

    def _walk_commands(node, caller_id):
        """Walk a node's children for calls and reads/writes."""
        # If this node itself is a closure call block, detect the call
        if node.kind() == "block":
            _detect_closure_call(node, caller_id)

        for child in _named_children(node):
            if child.kind() == "command":
                _process_body_command(child, caller_id)
            elif child.kind() == "end_command":
                _process_body_command(child, caller_id)
            elif child.kind() == "block":
                # Recurse into nested blocks
                _walk_commands(child, caller_id)

    def _detect_closure_call(block_node, caller_id):
        """Detect closure-style method calls like numbers.findAll { ... }.
        Pattern: block → unit(obj.method) + end_command(closure body).
        The last identifier in the unit is the method name being called.
        """
        first_unit = _find_named_child(block_node, "unit")
        if not first_unit:
            return
        # Collect all identifiers in this unit
        idents = _find_all_named_children(first_unit, "identifier")
        if len(idents) < 2:
            return
        # Last identifier is the method name
        method_name = _node_text(idents[-1], src_bytes)
        if method_name and method_name != "_":
            target = f"{file_path}::{method_name}"
            add_edge(caller_id,
                     _hash_id(target, file_path), "calls",
                     first_unit.start_position().row + 1, target)
        # First identifier is the receiver (add as read)
        receiver_name = _node_text(idents[0], src_bytes)
        if receiver_name and receiver_name != "_":
            add_edge(caller_id,
                     _hash_id(f"{file_path}::{receiver_name}", file_path),
                     "reads", first_unit.start_position().row + 1,
                     target_text=receiver_name)

    def _process_body_command(cmd, caller_id):
        """Process a body command for calls, assignments, and reads."""
        operators_list = _find_all_named_children(cmd, "operators")
        has_assign = any(
            _node_text(op, src_bytes).strip() == "=" for op in operators_list
        )

        if has_assign:
            _handle_assignment(cmd, caller_id)
        else:
            _handle_expression_statement(cmd, caller_id)

        # Recurse into blocks inside this command (closures, nested blocks)
        for child in _named_children(cmd):
            if child.kind() == "block":
                _walk_commands(child, caller_id)

    def _handle_assignment(cmd, caller_id):
        """Handle assignment expression → writes + reads edges."""
        # Build a flat list of items from children to determine left/right of =
        all_items = []
        for child in _children(cmd):
            if child.kind() == "unit":
                all_items.append(("unit", child))
            elif child.kind() == "operators":
                op_text = _node_text(child, src_bytes).strip()
                all_items.append(("op", child, op_text))
            elif child.kind() in ("number", "string"):
                all_items.append(("lit", child))
            elif child.kind() in ("list", "map"):
                all_items.append(("collection", child))
            # Also handle identifiers at the top level of the command
        for child in _named_children(cmd):
            if child.kind() == "identifier":
                name = _node_text(child, src_bytes)
                if name and name != "_":
                    all_items.append(("id", child, name))

        # Find = operator position
        eq_positions = [
            i for i, item in enumerate(all_items)
            if item[0] == "op" and item[2] == "="
        ]

        if eq_positions:
            eq_pos = eq_positions[0]
            # Left side: writes
            for i in range(eq_pos):
                item = all_items[i]
                if item[0] == "unit":
                    for name in _extract_identifiers_recursive(item[1], src_bytes):
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{name}", file_path),
                                 "writes", cmd.start_position().row + 1,
                                 target_text=name)
            # Right side: reads
            for i in range(eq_pos + 1, len(all_items)):
                item = all_items[i]
                if item[0] == "unit":
                    for name in _extract_identifiers_recursive(item[1], src_bytes):
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{name}", file_path),
                                 "reads", cmd.start_position().row + 1,
                                 target_text=name)
                elif item[0] == "id":
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{item[2]}", file_path),
                             "reads", cmd.start_position().row + 1,
                             target_text=item[2])

    def _handle_expression_statement(cmd, caller_id):
        """Handle expression statement for calls and reads."""
        for child in _named_children(cmd):
            if child.kind() == "unit":
                func_node = _get_func_node(child)
                if func_node:
                    callee_name = _get_func_name(func_node, src_bytes)
                    if callee_name:
                        target = f"{file_path}::{callee_name}"
                        add_edge(caller_id,
                                 _hash_id(target, file_path), "calls",
                                 cmd.start_position().row + 1, target)
                    # Also read identifiers in the unit (e.g., receiver object)
                    for name in _extract_identifiers_recursive(child, src_bytes):
                        if name and name != callee_name:
                            add_edge(caller_id,
                                     _hash_id(f"{file_path}::{name}", file_path),
                                     "reads", cmd.start_position().row + 1,
                                     target_text=name)
                else:
                    # Regular expression: just reads
                    for name in _extract_identifiers_recursive(child, src_bytes):
                        add_edge(caller_id,
                                 _hash_id(f"{file_path}::{name}", file_path),
                                 "reads", cmd.start_position().row + 1,
                                 target_text=name)
            elif child.kind() == "block":
                # Could contain calls inside closure
                _walk_commands(child, caller_id)
            elif child.kind() == "identifier":
                name = _node_text(child, src_bytes)
                if name and name != "_":
                    add_edge(caller_id,
                             _hash_id(f"{file_path}::{name}", file_path),
                             "reads", cmd.start_position().row + 1,
                             target_text=name)

    # -------------------------------------------------------------------
    # Type declaration visitor (class/interface/trait/enum)
    # -------------------------------------------------------------------
    def _visit_type_declaration(command_node, pending_annotations=None):
        """Visit a class, interface, trait, or enum declaration."""
        if pending_annotations is None:
            pending_annotations = []

        # Get keyword and name
        units = _find_all_named_children(command_node, "unit")
        if not units:
            return
        keyword = _first_identifier_text(units[0], src_bytes)
        if keyword not in ("class", "interface", "enum", "trait"):
            return

        # Name: second unit child (before extends/implements) or in block
        name = None
        if len(units) >= 2:
            kw2 = _first_identifier_text(units[1], src_bytes)
            if kw2 and kw2 not in ("extends", "implements"):
                name = kw2

        blk = _get_block(command_node)
        if not name and blk:
            first_unit = _find_named_child(blk, "unit")
            if first_unit:
                name = _first_identifier_text(first_unit, src_bytes)

        if not name:
            return

        # Map keyword to node kind
        kind_map = {
            "class": "class",
            "interface": "interface",
            "enum": "enum",
            "trait": "interface",
        }
        kind = kind_map.get(keyword, "class")

        # Visibility from modifiers
        modifiers = _extract_modifiers_from_command(command_node, src_bytes)
        vis = "public"
        for mod in ("private", "protected"):
            if mod in modifiers:
                vis = mod
                break

        nid = add_node(kind, name, command_node, visibility=vis)

        # Apply pending annotations on this type
        _apply_pending_annotations(pending_annotations, nid)

        name_stack.append(name)
        node_stack.append(nid)
        type_kind_stack.append(kind)

        # Handle extends and implements
        _extract_inheritance(command_node, nid, blk)

        # Walk body
        if blk:
            _walk_type_body(blk, nid, kind)

        type_kind_stack.pop()
        name_stack.pop()
        node_stack.pop()

    def _extract_inheritance(command_node, parent_nid, blk):
        """Extract extends and implements edges from a type command."""
        units = _find_all_named_children(command_node, "unit")

        # Collect unit text pairs
        all_ident_units = []
        for u in units:
            kw = _first_identifier_text(u, src_bytes)
            if kw:
                all_ident_units.append((kw, u))

        # Look for extends keyword followed by unit with parent name
        for i, (kw, u) in enumerate(all_ident_units):
            if kw == "extends" and i + 1 < len(all_ident_units):
                parent_name, _ = all_ident_units[i + 1]
                add_edge(parent_nid,
                         _hash_id(make_qualified(parent_name), file_path),
                         "extends", u.start_position().row + 1,
                         target_text=parent_name)

        # Look for implements keyword
        for i, (kw, u) in enumerate(all_ident_units):
            if kw == "implements":
                # Interface names may follow as unit children or inside a block
                for j in range(i + 1, len(all_ident_units)):
                    iface_kw, iface_u = all_ident_units[j]
                    if iface_kw in ("extends", "implements"):
                        continue
                    add_edge(parent_nid,
                             _hash_id(make_qualified(iface_kw), file_path),
                             "implements", u.start_position().row + 1,
                             target_text=iface_kw)

                # Also check if the implements keyword is followed by a block
                # (e.g., "implements" block{Serializable ...}) — the interface
                # name is in the block's first unit
                if blk:
                    first_block_unit = _find_named_child(blk, "unit")
                    if first_block_unit:
                        # The block's first unit is the name if we got here via "implements"
                        # But we already added from the preceding units — check if block has
                        # additional names that were not in units
                        block_name = _first_identifier_text(first_block_unit, src_bytes)
                        if block_name:
                            # Check if this name isn't already in unit-based implements list
                            already_added = any(
                                e["kind"] == "implements" and e.get("target_text") == block_name
                                for e in result.edges
                            )
                            if not already_added:
                                add_edge(parent_nid,
                                         _hash_id(make_qualified(block_name), file_path),
                                         "implements", u.start_position().row + 1,
                                         target_text=block_name)

    def _walk_type_body(body_node, parent_nid, type_kind):
        """Walk a type body (class/interface/trait/enum block) for members."""
        children_list = list(_named_children(body_node))
        pending_annotations = []

        i = 0
        while i < len(children_list):
            child = children_list[i]
            cn = child.kind()

            if cn == "decorate":
                pending_annotations.append(child)

            elif cn == "command":
                # Skip annotation-only commands
                if _is_annotation_command(child, src_bytes):
                    pending_annotations.append(child)
                    i += 1
                    continue

                # Check if it's a method/constructor or field
                blk_inner = _get_block(child)
                has_func = _has_func(child)

                if blk_inner and has_func:
                    # Method or constructor
                    _visit_method_command(child, parent_nid)
                    # Apply pending annotations to last node added
                    if result.nodes:
                        _apply_pending_annotations(pending_annotations, result.nodes[-1]["id"])
                    pending_annotations = []
                elif type_kind == "enum":
                    # Enum constant
                    _visit_enum_constant(child, parent_nid)
                    pending_annotations = []
                else:
                    # Field declaration
                    _visit_field_command(child, parent_nid)
                    # Apply pending annotations to last added field node
                    if result.nodes:
                        _apply_pending_annotations(pending_annotations, result.nodes[-1]["id"])
                    pending_annotations = []

            elif cn == "end_command":
                # Enum constants or trailing expressions
                if type_kind == "enum":
                    units_inner = _find_all_named_children(child, "unit")
                    for u in units_inner:
                        name = _first_identifier_text(u, src_bytes)
                        if name:
                            nid_const = add_node("enum_constant", name, u)
                            add_edge(parent_nid, nid_const, "contains",
                                     child.start_position().row + 1)

            elif cn == "unit":
                # Type name — skip
                pass

            i += 1

    # -------------------------------------------------------------------
    # Top-level function visitor
    # -------------------------------------------------------------------
    def _visit_top_function(command_node):
        """Visit a top-level function (def keyword)."""
        blk = _get_block(command_node)
        if not blk:
            return
        func_node = _get_func_node(blk)
        if not func_node:
            return
        func_name = _get_func_name(func_node, src_bytes)
        if not func_name:
            return

        nid = add_node("function", func_name, func_node)

        name_stack.append(func_name)
        node_stack.append(nid)

        _walk_for_calls_and_rw(blk, nid)

        name_stack.pop()
        node_stack.pop()

    # -------------------------------------------------------------------
    # Top-level walker
    # -------------------------------------------------------------------
    def _walk_top_level(node):
        """Walk top-level commands categorizing by keyword."""
        source_commands = _find_all_named_children(node, "command")
        pending_annotations = []

        for cmd in source_commands:
            # Check for standalone annotation command first
            if _is_annotation_command(cmd, src_bytes):
                pending_annotations.append(cmd)
                continue

            # Get keyword from first unit
            units = _find_all_named_children(cmd, "unit")
            keyword = None
            if units:
                keyword = _first_identifier_text(units[0], src_bytes)

            if keyword == "import":
                _visit_import(cmd)
            elif keyword in ("class", "interface", "enum", "trait"):
                _visit_type_declaration(cmd, pending_annotations)
                pending_annotations = []
            elif keyword == "def":
                _visit_top_function(cmd)
                pending_annotations = []
            else:
                # Script-level statement — could be function call like main()
                # or assignment like def x = ...
                blk = _get_block(cmd)
                if blk and _has_func(cmd):
                    # Named block with func — treat as anonymous/top-level callable
                    _visit_method_command(cmd, None)
                pending_annotations = []

    # -------------------------------------------------------------------
    # Walk the tree
    # -------------------------------------------------------------------
    root = tree.root_node()
    _walk_top_level(root)

    return result
