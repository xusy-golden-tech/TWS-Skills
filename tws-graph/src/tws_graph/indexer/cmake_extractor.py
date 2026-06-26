"""CMake extractor — walks tree-sitter CST for symbols and edges.

CMake is a build configuration language (not a programming language), so the
extraction pattern differs from OOP languages.  We extract:

Nodes:
  - function/macro definitions → "function"
  - set() variable assignments  → "variable"
  - option() definitions        → "variable"
  - add_executable/add_library  → "target"
  - file-level root             → "file"

Edges:
  - target_link_libraries       → "depends"   (target → library)
  - add_subdirectory            → "imports"   (file → subdirectory)
  - find_package / include      → "imports"   (file → package/module)
  - function/macro invocations  → "calls"     (caller → callee)
"""

from __future__ import annotations

from .base import ExtractionResult, hash_id


# ---------------------------------------------------------------------------
# tree-sitter helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# argument extraction
# ---------------------------------------------------------------------------

_CMAKE_KEYWORDS = frozenset({
    "PRIVATE", "PUBLIC", "INTERFACE",
    "STATIC", "SHARED", "MODULE", "IMPORTED", "ALIAS", "OBJECT",
    "REQUIRED", "COMPONENTS", "OPTIONAL", "EXACT", "QUIET",
    "CONFIG", "MODULE", "VERSION", "LANGUAGES",
})


def _get_identifier_text(command_node, source: bytes) -> str | None:
    """Get the command name from a normal_command node."""
    identifier = _find_named_child(command_node, "identifier")
    if not identifier:
        return None
    return _node_text(identifier, source)


def _get_arguments(command_node, source: bytes) -> list[str]:
    """Get the list of simple-text arguments from a command.

    Each argument child has an unquoted_argument or quoted_argument grandchild
    from which we extract the raw text.  Quoted arguments have quotes stripped.
    """
    arg_list = _find_named_child(command_node, "argument_list")
    if not arg_list:
        return []

    args: list[str] = []
    for child in _named_children(arg_list):
        if child.kind() == "argument":
            for gc in _named_children(child):
                txt = _node_text(gc, source)
                if gc.kind() == "quoted_argument":
                    if len(txt) >= 2 and txt[0] == '"':
                        txt = txt[1:-1]
                args.append(txt)
                break
    return args


# ---------------------------------------------------------------------------
# cmake_json_commands extraction for JSON-based commands
# ---------------------------------------------------------------------------
# Not yet implemented — future enhancement for cmake_parse_arguments style

# ---------------------------------------------------------------------------
# main visitor
# ---------------------------------------------------------------------------

def visit_cmake(file_path: str, source: str, tree) -> ExtractionResult:
    """Extract symbols and edges from a CMake source file."""
    result = ExtractionResult()
    src_bytes = source.encode("utf-8")

    # File-level ID for imports edges
    _file_id = hash_id(f"{file_path}::__cmakefile__", file_path)

    # Track defined functions/macros for calls detection
    _known_functions: set[str] = set()

    # ------------------------------------------------------------------
    # node/edge helpers
    # ------------------------------------------------------------------

    def _make_qualified(simple_name: str) -> str:
        return f"{file_path}::{simple_name}"

    def _add_node(kind: str, simple_name: str, node, **extra) -> str:
        """Create a node and return its id."""
        qname = _make_qualified(simple_name)
        nid = hash_id(qname, file_path)
        sp = node.start_position()
        ep = node.end_position()
        record = {
            "id": nid,
            "kind": kind,
            "name": simple_name,
            "qualified_name": qname,
            "file_path": file_path,
            "language": "cmake",
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": "public",
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        result.nodes.append(record)
        return nid

    def _add_edge(source_id: str, target_text: str, kind: str, line: int) -> None:
        """Add an edge dict to result.edges."""
        target_id = hash_id(_make_qualified(target_text), file_path)
        edge = {
            "source": source_id,
            "target": target_id,
            "kind": kind,
            "source_loc": f"{file_path}:{line}",
            "provenance": "tree-sitter",
            "target_text": target_text,
        }
        result.edges.append(edge)

    # ------------------------------------------------------------------
    # sub-visitors
    # ------------------------------------------------------------------

    def _visit_function_def(node):
        """Process a function_definition or macro_definition."""
        is_macro = node.kind() == "macro_def"
        cmd_kind = "macro_command" if is_macro else "function_command"
        cmd_node = _find_named_child(node, cmd_kind)
        if not cmd_node:
            return

        args = _get_arguments(cmd_node, src_bytes)
        if not args:
            return

        func_name = args[0]
        params = args[1:] if len(args) > 1 else []

        nid = _add_node("function", func_name, node, parameters=params)
        _known_functions.add(func_name)

        # Walk body for calls to other functions
        body = _find_named_child(node, "body")
        if body:
            _walk_body(body, nid)

    def _visit_normal_command(node, caller_id: str | None = None):
        """Process a normal_command, optionally inside a function body.

        When caller_id is set (inside a function body), only calls detection
        runs — no nodes or structural edges are extracted.
        """
        ident = _get_identifier_text(node, source=src_bytes)
        if not ident:
            return

        cmd_name = ident.lower()
        args = _get_arguments(node, source=src_bytes)
        line = node.start_position().row + 1

        # --- Calls detection (inside function body) ---
        if caller_id is not None:
            if ident in _known_functions or cmd_name in _known_functions:
                _add_edge(caller_id, ident, "calls", line)
            return  # do NOT extract nodes / structural edges inside function bodies

        # --- Calls detection (top-level call to a user-defined function) ---
        if ident in _known_functions or cmd_name in _known_functions:
            _add_edge(_file_id, ident, "calls", line)

        # --- set(VAR value ...) → variable ---
        if cmd_name == "set" and args:
            var_name = _clean_variable_name(args[0])
            _add_node("variable", var_name, node)

        # --- option(NAME desc default) → variable ---
        elif cmd_name == "option" and args:
            var_name = _clean_variable_name(args[0])
            _add_node("variable", var_name, node)

        # --- add_executable(name sources...) → target ---
        elif cmd_name == "add_executable" and args:
            target_name = _clean_variable_name(args[0])
            _add_node("target", target_name, node, target_type="executable")

        # --- add_library(name [type] sources...) → target ---
        elif cmd_name == "add_library" and args:
            target_name = _clean_variable_name(args[0])
            _add_node("target", target_name, node, target_type="library")

        # --- target_link_libraries(target lib1 [keyword] lib2 ...) → depends ---
        elif cmd_name == "target_link_libraries" and args:
            target_name = _clean_variable_name(args[0])
            target_id = hash_id(_make_qualified(target_name), file_path)
            for lib in args[1:]:
                clean_lib = _clean_variable_name(lib)
                if clean_lib.upper() in _CMAKE_KEYWORDS:
                    continue
                _add_edge(target_id, clean_lib, "depends", line)

        # --- add_subdirectory(path) → imports ---
        elif cmd_name == "add_subdirectory" and args:
            _add_edge(_file_id, args[0], "imports", line)

        # --- find_package(Pkg ... ) → imports ---
        elif cmd_name == "find_package" and args:
            _add_edge(_file_id, args[0], "imports", line)

        # --- include(Module ...) → imports ---
        elif cmd_name == "include" and args:
            _add_edge(_file_id, args[0], "imports", line)

    def _walk_body(body, caller_id):
        """Walk a body node for normal_commands and nested control structures."""
        for child in _named_children(body):
            _walk_node(child, caller_id)

    def _walk_node(node, caller_id: str | None = None):
        """Recursively walk any node, dispatching by kind."""
        kind = node.kind()

        if kind == "normal_command":
            _visit_normal_command(node, caller_id)

        elif kind in ("function_def", "macro_def"):
            # Function/macro inside a body? Unusual but handle it
            _visit_function_def(node)

        elif kind == "body":
            _walk_body(node, caller_id)

        elif kind == "if_condition":
            # Walk each body inside the if_condition
            for child in _named_children(node):
                _walk_node(child, caller_id)

        elif kind == "foreach_loop":
            for child in _named_children(node):
                _walk_node(child, caller_id)

        elif kind in ("if_command", "elseif_command", "else_command",
                       "endif_command", "foreach_command", "endforeach_command",
                       "endfunction_command", "endmacro_command",
                       "function_command", "macro_command", "argument_list",
                       "argument", "identifier"):
            # Skip structural nodes (they are processed by _visit_function_def or _visit_normal_command)
            pass

        else:
            # Recurse into unrecognized named nodes
            for child in _named_children(node):
                _walk_node(child, caller_id)

    def _clean_variable_name(name: str) -> str:
        """Strip variable expansion markers from a name like ${VAR_NAME}."""
        name = name.strip()
        if name.startswith("${") and name.endswith("}"):
            return name[2:-1]
        return name

    # ------------------------------------------------------------------
    # walk the tree
    # ------------------------------------------------------------------

    root = tree.root_node()
    for child in _named_children(root):
        _walk_node(child)

    return result
