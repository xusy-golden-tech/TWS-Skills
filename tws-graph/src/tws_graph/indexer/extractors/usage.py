from __future__ import annotations

"""VariableUsageExtractor -- 变量使用分析提取器.

从 tree-sitter AST 提取每函数的变量 read/write/throw 集合。
不是 BaseExtractor 的子类。由 DataFlowPass 直接调用。
仅支持 Python 和 TypeScript。

Uses tree-sitter >= 0.25 method-based API:
  node.kind()  (not node.type)
  child_count(), child(i)
  named_child_count(), named_child(i)
  child_by_field_name(name)
  start_byte(), end_byte()  (methods, not attributes)
  start_position().row
"""

from tws_graph.edges.kind import EdgeKind

from ..base import children as _children, named_children as _named_children

# ---------------------------------------------------------------------------
# 工具函数（与已有 extractor 保持一致的 API 用法）
# ---------------------------------------------------------------------------

def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


# ---------------------------------------------------------------------------
# VariableUsageExtractor
# ---------------------------------------------------------------------------

class VariableUsageExtractor:
    """从 tree-sitter AST 提取每函数的变量 read/write/throw 集合。

    不是 BaseExtractor 的子类。由 DataFlowPass 直接调用。
    仅支持 Python 和 TypeScript。
    """

    def extract(
        self,
        source: bytes,
        tree,
        func_node_ids: dict[str, str],
        file_path: str,
        language: str,
    ) -> list[dict]:
        """返回 edges 列表。

        每条 edge 含 source/target/kind/target_text/source_loc/provenance。
        """
        edges: list[dict] = []

        if language == "python":
            self._extract_python(source, tree, func_node_ids, file_path, edges)
        elif language == "typescript":
            self._extract_typescript(source, tree, func_node_ids, file_path, edges)
        elif language == "java":
            self._extract_java(source, tree, func_node_ids, file_path, edges)

        return edges

    # ========================================================================
    # Python
    # ========================================================================

    # Python tree-sitter node kinds for function definitions
    _PY_FUNC_KINDS = frozenset({"function_definition"})
    _PY_SKIP_KINDS = frozenset({
        "function_definition", "class_definition", "decorated_definition",
    })

    def _extract_python(
        self, source: bytes, tree, func_node_ids: dict[str, str],
        file_path: str, edges: list[dict],
    ) -> None:
        root = tree.root_node()
        name_stack: list[str] = []

        def build_qname(simple_name: str) -> str:
            parts = [file_path] + name_stack + [simple_name]
            return "::".join(parts)

        def walk(node) -> None:
            kind = node.kind()

            if kind == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    qname = build_qname(name)
                    if qname in func_node_ids:
                        nid = func_node_ids[qname]
                        self._analyze_python_body(
                            source, node, nid, file_path, edges,
                        )

                    # Push name for nested qualifiers, then walk body
                    name_stack.append(name)
                    self._walk_python_children_for_nested(
                        source, node, walk, name_stack,
                        func_node_ids, file_path, edges,
                    )
                    name_stack.pop()
                return

            elif kind == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    name_stack.append(name)
                    body_node = node.child_by_field_name("body")
                    if body_node:
                        for child in _children(body_node):
                            walk(child)
                    name_stack.pop()
                return

            elif kind == "decorated_definition":
                for child in _children(node):
                    if child.kind() in ("function_definition", "class_definition"):
                        walk(child)
                        return
                return

            # Recurse into children
            for child in _children(node):
                walk(child)

        walk(root)

    def _walk_python_children_for_nested(
        self, source: bytes, func_node, walk_fn,
        name_stack: list[str], func_node_ids: dict[str, str],
        file_path: str, edges: list[dict],
    ) -> None:
        """Walk body of a func_node to find nested function defs,
        skipping the block's direct assignment processing.
        The walk_fn handles finding nested function/class defs.
        """
        body_node = func_node.child_by_field_name("body")
        if body_node is None:
            return

        for child in _children(body_node):
            walk_fn(child)

    def _analyze_python_body(
        self, source: bytes, func_node, nid: str,
        file_path: str, edges: list[dict],
    ) -> None:
        """Analyze a Python function body for reads/writes/throws."""
        body_node = func_node.child_by_field_name("body")
        if body_node is None:
            return

        # Collect parameters as writes
        write_set: dict[str, int] = {}
        params_node = func_node.child_by_field_name("parameters")
        if params_node:
            for child in _children(params_node):
                if child.is_named() and child.kind() == "identifier":
                    name = _node_text(child, source)
                    # self/cls are method binding parameters, not real variable writes
                    if name not in ("self", "cls"):
                        write_set[name] = child.start_position().row + 1

        read_set: dict[str, int] = {}
        throw_set: dict[str, int] = {}
        nonlocal_vars: set[str] = set()
        global_vars: set[str] = set()

        def walk_body(node, is_write_lhs: bool = False) -> None:
            kind = node.kind()

            # Skip nested function/class definitions entirely
            if kind in self._PY_SKIP_KINDS:
                return

            # -- Assignment: LHS = write, RHS = read --
            if kind == "assignment":
                named = list(_named_children(node))
                if named:
                    walk_body(named[0], is_write_lhs=True)
                for c in named[1:]:
                    walk_body(c, is_write_lhs=False)
                return

            # -- Augmented assignment: LHS = read + write --
            if kind == "augmented_assignment":
                named = list(_named_children(node))
                if named:
                    walk_body(named[0], is_write_lhs=True)   # write
                    walk_body(named[0], is_write_lhs=False)   # also read
                for c in named[1:]:
                    walk_body(c, is_write_lhs=False)
                return

            # -- for loop: left = write --
            if kind == "for_statement":
                left = node.child_by_field_name("left")
                if left:
                    walk_body(left, is_write_lhs=True)
                right = node.child_by_field_name("right")
                if right:
                    walk_body(right, is_write_lhs=False)
                body = node.child_by_field_name("body")
                if body:
                    walk_body(body, is_write_lhs=False)
                # Also process alternative/else clause
                alt = node.child_by_field_name("alternative")
                if alt:
                    walk_body(alt, is_write_lhs=False)
                return

            # -- with as: as_pattern_target = write --
            if kind == "as_pattern_target":
                for child in _children(node):
                    if child.is_named():
                        walk_body(child, is_write_lhs=True)
                return

            # -- nonlocal declaration --
            if kind == "nonlocal_statement":
                for child in _named_children(node):
                    if child.kind() == "identifier":
                        nonlocal_vars.add(_node_text(child, source))
                return

            # -- global declaration --
            if kind == "global_statement":
                for child in _named_children(node):
                    if child.kind() == "identifier":
                        global_vars.add(_node_text(child, source))
                return

            # -- raise: collect throw text --
            if kind == "raise_statement":
                for child in _named_children(node):
                    text = _node_text(child, source)
                    throw_set[text] = node.start_position().row + 1
                return

            # -- identifier: read or write based on context --
            if kind == "identifier":
                name = _node_text(node, source)
                if is_write_lhs:
                    write_set[name] = node.start_position().row + 1
                else:
                    read_set[name] = node.start_position().row + 1
                return

            # -- attribute access (e.g. obj.attr / self.x) --
            if kind == "attribute":
                text = _node_text(node, source)
                if is_write_lhs:
                    write_set[text] = node.start_position().row + 1
                else:
                    read_set[text] = node.start_position().row + 1
                return

            # Default: recurse into all children with read context
            for child in _children(node):
                if child.is_named():
                    walk_body(child, is_write_lhs=False)

        walk_body(body_node)

        # -- Build edges --
        self._emit_edges(
            nid, file_path, write_set, read_set, throw_set,
            nonlocal_vars, global_vars, edges,
        )

    # ========================================================================
    # TypeScript
    # ========================================================================

    _TS_FUNC_KINDS = frozenset({
        "function_declaration", "method_definition",
        "arrow_function", "function_expression",
    })
    _TS_SKIP_KINDS = frozenset({
        "function_declaration", "method_definition",
        "arrow_function", "function_expression",
        "class_declaration",
    })

    def _extract_typescript(
        self, source: bytes, tree, func_node_ids: dict[str, str],
        file_path: str, edges: list[dict],
    ) -> None:
        root = tree.root_node()
        name_stack: list[str] = []

        def build_qname(simple_name: str) -> str:
            parts = [file_path] + name_stack + [simple_name]
            return "::".join(parts)

        def walk(node) -> None:
            kind = node.kind()

            # Push/pop for nesting context
            push_pop = None  # name to pop after walking children

            if kind == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    # Check if inside a class (should be treated as method)
                    # In TS tree-sitter, function_declaration inside class_body
                    # is still function_declaration, not method_definition
                    p = node.parent()
                    is_in_class = p is not None and p.kind() == "class_body"
                    if not is_in_class:
                        qname = build_qname(name)
                        if qname in func_node_ids:
                            nid = func_node_ids[qname]
                            self._analyze_ts_body(
                                source, node, nid, file_path, edges,
                            )
                    push_pop = name

            elif kind == "method_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    qname = build_qname(name)
                    if qname in func_node_ids:
                        nid = func_node_ids[qname]
                        self._analyze_ts_body(
                            source, node, nid, file_path, edges,
                        )
                    push_pop = name

            elif kind == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node and value_node:
                    if value_node.kind() in ("arrow_function", "function_expression"):
                        name = _node_text(name_node, source)
                        qname = build_qname(name)
                        if qname in func_node_ids:
                            nid = func_node_ids[qname]
                            self._analyze_ts_body(
                                source, value_node, nid, file_path, edges,
                            )

            elif kind == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    name_stack.append(name)
                    # Walk class body for methods
                    body_node = node.child_by_field_name("body")
                    if body_node:
                        for child in _children(body_node):
                            walk(child)
                    name_stack.pop()
                return

            # Recurse into children
            if push_pop:
                name_stack.append(push_pop)
                # Walk body for nested function defs
                body = node.child_by_field_name("body")
                if body:
                    for child in _children(body):
                        walk(child)
                name_stack.pop()
            else:
                for child in _children(node):
                    walk(child)

        walk(root)

    def _analyze_ts_body(
        self, source: bytes, func_node, nid: str,
        file_path: str, edges: list[dict],
    ) -> None:
        """Analyze a TS function body for reads/writes/throws."""
        body_node = func_node.child_by_field_name("body")
        if body_node is None:
            return

        # Collect parameters as writes
        write_set: dict[str, int] = {}
        params_node = func_node.child_by_field_name("parameters")
        # In TS, parameters might be "formal_parameters"
        if params_node is None:
            params_node = func_node.child_by_field_name("formal_parameters")
        if params_node:
            for child in _children(params_node):
                if child.is_named():
                    # Parameter can be required_parameter, optional_parameter,
                    # rest_parameter, etc. Extract the identifier.
                    self._collect_ts_param_writes(
                        source, child, write_set,
                    )

        read_set: dict[str, int] = {}
        throw_set: dict[str, int] = {}
        # TS doesn't have nonlocal/global

        def walk_body(node, is_write_lhs: bool = False) -> None:
            kind = node.kind()

            # Skip nested function/class definitions entirely
            if kind in self._TS_SKIP_KINDS:
                return

            # -- variable_declarator: name = write, value = read --
            if kind == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node:
                    walk_body(name_node, is_write_lhs=True)
                if value_node:
                    walk_body(value_node, is_write_lhs=False)
                return

            # -- assignment_expression: LHS = write, RHS = read --
            if kind == "assignment_expression":
                named = list(_named_children(node))
                if named:
                    walk_body(named[0], is_write_lhs=True)
                for c in named[1:]:
                    walk_body(c, is_write_lhs=False)
                return

            # -- augmented_assignment_expression: LHS = read + write --
            if kind == "augmented_assignment_expression":
                named = list(_named_children(node))
                if named:
                    walk_body(named[0], is_write_lhs=True)
                    walk_body(named[0], is_write_lhs=False)
                for c in named[1:]:
                    walk_body(c, is_write_lhs=False)
                return

            # -- for_in_statement / for_statement: left = write --
            if kind in ("for_in_statement", "for_statement"):
                left = node.child_by_field_name("left")
                if left:
                    walk_body(left, is_write_lhs=True)
                right = node.child_by_field_name("right")
                if right:
                    walk_body(right, is_write_lhs=False)
                body = node.child_by_field_name("body")
                if body:
                    walk_body(body, is_write_lhs=False)
                return

            # -- throw_statement: collect throw text --
            if kind == "throw_statement":
                for child in _named_children(node):
                    text = _node_text(child, source)
                    throw_set[text] = node.start_position().row + 1
                return

            # -- identifier: read or write based on context --
            if kind == "identifier":
                name = _node_text(node, source)
                if is_write_lhs:
                    write_set[name] = node.start_position().row + 1
                else:
                    read_set[name] = node.start_position().row + 1
                return

            # -- member_expression (e.g. obj.prop, this.x) --
            if kind == "member_expression":
                text = _node_text(node, source)
                if is_write_lhs:
                    write_set[text] = node.start_position().row + 1
                else:
                    read_set[text] = node.start_position().row + 1
                return

            # -- property_identifier / type_identifier: NOT variable uses --
            # (these are names of methods/classes, not variable references)

            # Default: recurse into all children with read context
            for child in _children(node):
                if child.is_named():
                    walk_body(child, is_write_lhs=False)

        walk_body(body_node)

        # -- Build edges --
        self._emit_edges(
            nid, file_path, write_set, read_set, throw_set,
            set(), set(), edges,  # no nonlocal/global for TS
        )

    def _collect_ts_param_writes(
        self, source: bytes, node, write_set: dict[str, int],
    ) -> None:
        """Recursively collect parameter identifiers from a TS param node."""
        kind = node.kind()
        if kind == "identifier":
            name = _node_text(node, source)
            write_set[name] = node.start_position().row + 1
            return
        # Recurse into children (e.g. required_parameter, optional_parameter)
        for child in _children(node):
            if child.is_named():
                self._collect_ts_param_writes(source, child, write_set)

    # ========================================================================
    # Java
    # ========================================================================

    _JAVA_FUNC_KINDS = frozenset({"method_declaration", "constructor_declaration"})
    _JAVA_SKIP_KINDS = frozenset({
        "method_declaration", "constructor_declaration",
        "class_declaration", "interface_declaration", "enum_declaration",
    })

    def _extract_java(
        self, source: bytes, tree, func_node_ids: dict[str, str],
        file_path: str, edges: list[dict],
    ) -> None:
        root = tree.root_node()
        name_stack: list[str] = []

        def build_qname(simple_name: str) -> str:
            parts = [file_path] + name_stack + [simple_name]
            return "::".join(parts)

        def walk(node) -> None:
            kind = node.kind()

            if kind == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    name_stack.append(name)
                    body = node.child_by_field_name("body")
                    if body:
                        for child in _children(body):
                            walk(child)
                    name_stack.pop()
                return

            elif kind in ("method_declaration", "constructor_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, source)
                    qname = build_qname(name)
                    if qname in func_node_ids:
                        nid = func_node_ids[qname]
                        self._analyze_java_body(
                            source, node, nid, file_path, edges,
                        )

                    # Push name for nested class/method defs
                    name_stack.append(name)
                    body = node.child_by_field_name("body")
                    if body is None and kind == "constructor_declaration":
                        # constructor uses "constructor_body"
                        for child in _children(node):
                            if child.kind() == "constructor_body":
                                body = child
                                break
                    if body:
                        for child in _children(body):
                            walk(child)
                    name_stack.pop()
                return

            # Default: recurse into children
            for child in _children(node):
                walk(child)

        walk(root)

    def _analyze_java_body(
        self, source: bytes, func_node, nid: str,
        file_path: str, edges: list[dict],
    ) -> None:
        """Analyze a Java method/constructor body for reads/writes/throws."""
        body_node = func_node.child_by_field_name("body")
        if body_node is None:
            # constructor_declaration uses "constructor_body"
            for child in _children(func_node):
                if child.kind() == "constructor_body":
                    body_node = child
                    break
        if body_node is None:
            return

        # Collect parameters as writes
        write_set: dict[str, int] = {}
        params_node = func_node.child_by_field_name("parameters")
        if params_node:
            for child in _named_children(params_node):
                if child.kind() == "formal_parameter":
                    # Find identifier inside formal_parameter
                    for param_child in _named_children(child):
                        if param_child.kind() == "identifier":
                            name = _node_text(param_child, source)
                            write_set[name] = param_child.start_position().row + 1
                            break

        read_set: dict[str, int] = {}
        throw_set: dict[str, int] = {}

        def walk_body(node, is_write_lhs: bool = False) -> None:
            kind = node.kind()

            # Skip nested function/class definitions entirely
            if kind in self._JAVA_SKIP_KINDS:
                return

            # -- assignment_expression: LHS = write, RHS = read --
            if kind == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left:
                    walk_body(left, is_write_lhs=True)
                if right:
                    walk_body(right, is_write_lhs=False)
                # Also handle unnamed children that aren't left/right
                for child in _children(node):
                    if child.is_named() and child != left and child != right:
                        walk_body(child, is_write_lhs=False)
                return

            # -- variable_declarator: name = write, value = read --
            if kind == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value_node = node.child_by_field_name("value")
                if name_node:
                    walk_body(name_node, is_write_lhs=True)
                if value_node:
                    walk_body(value_node, is_write_lhs=False)
                return

            # -- for_statement: init = write --
            if kind == "for_statement":
                for child in _children(node):
                    if child.is_named():
                        if child.kind() in ("local_variable_declaration",
                                            "assignment_expression"):
                            walk_body(child, is_write_lhs=True)
                        elif child.kind() in ("for_statement", "enhanced_for_statement"):
                            # Skip nested loops — they'll be processed by their own handler
                            pass
                        else:
                            walk_body(child, is_write_lhs=False)
                return

            # -- enhanced_for_statement: loop variable = write --
            if kind == "enhanced_for_statement":
                found_var = False
                for child in _children(node):
                    if child.is_named():
                        if not found_var and child.kind() == "identifier":
                            walk_body(child, is_write_lhs=True)
                            found_var = True
                        else:
                            walk_body(child, is_write_lhs=False)
                return

            # -- throw_statement: collect throw text --
            if kind == "throw_statement":
                for child in _named_children(node):
                    text = _node_text(child, source)
                    throw_set[text] = node.start_position().row + 1
                return

            # -- try_statement: recurse body, record catch exception types --
            if kind == "try_statement":
                for child in _children(node):
                    if child.kind() == "catch_clause":
                        # Find catch_formal_parameter → catch_type → type_identifier
                        for cchild in _named_children(child):
                            if cchild.kind() == "catch_formal_parameter":
                                for cparam in _named_children(cchild):
                                    if cparam.kind() == "catch_type":
                                        text = _node_text(cparam, source)
                                        throw_set[text] = child.start_position().row + 1
                    elif child.is_named():
                        walk_body(child, is_write_lhs=False)
                return

            # -- identifier: read or write based on context --
            if kind == "identifier":
                name = _node_text(node, source)
                if is_write_lhs:
                    write_set[name] = node.start_position().row + 1
                else:
                    read_set[name] = node.start_position().row + 1
                return

            # -- field_access (e.g. obj.field, this.field) --
            if kind == "field_access":
                text = _node_text(node, source)
                if is_write_lhs:
                    write_set[text] = node.start_position().row + 1
                else:
                    read_set[text] = node.start_position().row + 1
                return

            # Default: recurse into all children with read context
            for child in _children(node):
                if child.is_named():
                    walk_body(child, is_write_lhs=False)

        walk_body(body_node)

        # -- Build edges --
        self._emit_edges(
            nid, file_path, write_set, read_set, throw_set,
            set(), set(), edges,  # no nonlocal/global for Java
        )

    # ========================================================================
    # Edge emission (shared)
    # ========================================================================

    def _emit_edges(
        self, nid: str, file_path: str,
        write_set: dict[str, int],
        read_set: dict[str, int],
        throw_set: dict[str, int],
        nonlocal_vars: set[str],
        global_vars: set[str],
        edges: list[dict],
    ) -> None:
        """Generate READS / WRITES / THROWS edges from collected sets.

        Deduplication: sets guarantee at most one edge per (func, var, kind).
        """
        # -- Writes --
        for name, line in write_set.items():
            target_text = f"var:{name}"
            if name in nonlocal_vars:
                target_text = f"var:{name}:nonlocal"
            elif name in global_vars:
                target_text = f"var:{name}:global"
            edges.append({
                "source": nid,
                "target": "",
                "kind": EdgeKind.WRITES.value,
                "target_text": target_text,
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })

        # -- Reads --
        for name, line in read_set.items():
            edges.append({
                "source": nid,
                "target": "",
                "kind": EdgeKind.READS.value,
                "target_text": f"var:{name}",
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })

        # -- Throws --
        for name, line in throw_set.items():
            edges.append({
                "source": nid,
                "target": "",
                "kind": EdgeKind.THROWS.value,
                "target_text": name,
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })
