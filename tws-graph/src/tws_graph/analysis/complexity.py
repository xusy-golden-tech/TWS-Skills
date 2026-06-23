"""Code complexity analyzer — computes cyclomatic, cognitive, and Halstead metrics.

Uses tree-sitter to parse Python and TypeScript source files. Queries a tws-graph
Store for file/function discovery, then re-parses source to compute complexity
metrics for each function/method node.

Supports: Python (.py), TypeScript (.ts, .tsx)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from tree_sitter_language_pack import get_parser

if TYPE_CHECKING:
    from tws_graph.store.interface import Store


@dataclass
class ComplexityMetrics:
    """Complexity metrics for a single function/method node."""

    node_id: str
    qualified_name: str
    file_path: str
    language: str
    cyclomatic: int  # Cyclomatic complexity = branch count + 1
    cognitive: int  # Cognitive complexity (SonarSource simplified)
    halstead_volume: float  # Halstead volume
    halstead_difficulty: float  # Halstead difficulty
    halstead_effort: float  # Halstead effort = volume * difficulty
    lines_of_code: int
    risk_level: str  # low / medium / high / critical


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _children(node):
    """Generator over all children (named + unnamed) of a tree-sitter node."""
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    """Generator over named children only."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _node_text(node, source: bytes) -> str:
    """Extract source text for a tree-sitter node."""
    return source[node.start_byte() : node.end_byte()].decode("utf-8")


def _is_same_node(a, b) -> bool:
    """Compare two tree-sitter nodes by position and kind."""
    if a is None or b is None:
        return False
    return (
        a.start_byte() == b.start_byte()
        and a.end_byte() == b.end_byte()
        and a.kind() == b.kind()
    )


# ---------------------------------------------------------------------------
# Language-specific AST node classification
# ---------------------------------------------------------------------------

# Node kinds that represent cyclomatic decision points (each adds +1)
_PY_CYCLOMATIC_KINDS = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "boolean_operator",  # and / or
        "conditional_expression",  # x if cond else y
    }
)

_TS_CYCLOMATIC_KINDS = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "switch_case",
        "ternary_expression",  # cond ? x : y
    }
)

# Node kinds that contribute to cognitive complexity
_PY_COGNITIVE_KINDS = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
    }
)

_TS_COGNITIVE_KINDS = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "catch_clause",
        "switch_case",
        "switch_default",
    }
)

# Body field names for control structures (children at nesting+1 for cognitive)
_COGNITIVE_BODY_FIELDS: dict[str, list[str]] = {
    "if_statement": ["consequence"],  # alternative is same-level
    "elif_clause": ["consequence"],
    "for_statement": ["body"],
    "while_statement": ["body"],
    "except_clause": [],  # all children at nesting+1 (special)
    "for_in_statement": ["body"],
    "do_statement": ["body"],
    "catch_clause": ["body"],
    "switch_case": ["body"],
    "switch_default": ["body"],
}

# Python operators/keywords counted for Halstead (as operators)
_PY_HALSTEAD_OPERATOR_KINDS = frozenset(
    {
        # Binary / comparison / boolean operators
        "binary_operator",
        "unary_operator",
        "boolean_operator",
        "comparison_operator",
        "assignment",
        "augmented_assignment",
        "not_operator",
        # Control flow keywords
        "if_statement",
        "for_statement",
        "while_statement",
        "return_statement",
        "break_statement",
        "continue_statement",
        "pass_statement",
        "raise_statement",
        "yield",
        "assert_statement",
        "del_statement",
        "global_statement",
        "nonlocal_statement",
        "import_statement",
        "import_from_statement",
        # Exception handling
        "try_statement",
        "except_clause",
        "finally_clause",
        "raise_statement",
        # Function/class definition (def/class keywords)
        "function_definition",
        "class_definition",
        "lambda",
        # With statement
        "with_statement",
        # Conditional expression (ternary)
        "conditional_expression",
        # Await
        "await",
    }
)

_TS_HALSTEAD_OPERATOR_KINDS = frozenset(
    {
        "binary_expression",
        "unary_expression",
        "assignment_expression",
        "augmented_assignment_expression",
        "update_expression",
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "return_statement",
        "break_statement",
        "continue_statement",
        "throw_statement",
        "switch_statement",
        "switch_case",
        "switch_default",
        "try_statement",
        "catch_clause",
        "finally_clause",
        "function_declaration",
        "arrow_function",
        "function_expression",
        "method_definition",
        "class_declaration",
        "import_statement",
        "export_statement",
        "ternary_expression",
        "new_expression",
        "await_expression",
        "yield_expression",
        "debugger_statement",
        "labeled_statement",
    }
)

# Operand kinds (identifiers + literals)
_PY_OPERAND_KINDS = frozenset(
    {
        "identifier",
        "integer",
        "float",
        "string",
        "true",
        "false",
        "none",
        "list",
        "tuple",
        "dictionary",
        "set",
        "list_comprehension",
        "dictionary_comprehension",
        "set_comprehension",
        "generator_expression",
    }
)

_TS_OPERAND_KINDS = frozenset(
    {
        "identifier",
        "number",
        "string",
        "true",
        "false",
        "null",
        "undefined",
        "this",
        "super",
        "regex",
        "template_string",
        "array",
        "object",
        "object_pattern",
        "array_pattern",
    }
)

# Operator token strings (used to distinguish operators from keywords)
_OPERATOR_TOKENS = frozenset(
    {
        "+",
        "-",
        "*",
        "/",
        "//",
        "%",
        "**",
        "@",
        "=",
        "+=",
        "-=",
        "*=",
        "/=",
        "//=",
        "%=",
        "**=",
        "@=",
        "&=",
        "|=",
        "^=",
        "<<=",
        ">>=",
        "==",
        "!=",
        "<",
        ">",
        "<=",
        ">=",
        "<>",
        "is",
        "in",
        "not",
        "and",
        "or",
        "&",
        "|",
        "^",
        "~",
        "<<",
        ">>",
        "&&",
        "||",
        "!",
        "??",
        "?.",
        "=>",
    }
)

# Keyword tokens counted as operators in Halstead
_KEYWORD_TOKENS = frozenset(
    {
        "if",
        "else",
        "elif",
        "for",
        "while",
        "return",
        "break",
        "continue",
        "pass",
        "raise",
        "yield",
        "assert",
        "del",
        "global",
        "nonlocal",
        "import",
        "from",
        "def",
        "class",
        "lambda",
        "with",
        "try",
        "except",
        "finally",
        "await",
        "async",
        "function",
        "const",
        "let",
        "var",
        "throw",
        "switch",
        "case",
        "default",
        "new",
        "delete",
        "typeof",
        "instanceof",
        "void",
        "export",
        "extends",
        "implements",
        "interface",
        "enum",
        "type",
        "namespace",
        "debugger",
        "get",
        "set",
    }
)


def _is_operator_token(token: str) -> bool:
    """Check if a token string is an operator (not a keyword)."""
    return token in _OPERATOR_TOKENS


def _is_keyword_token(token: str) -> bool:
    """Check if a token string is a keyword counted as operator."""
    return token in _KEYWORD_TOKENS


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _detect_language(body: str) -> str:
    """Heuristically detect whether source is Python or TypeScript."""
    # Quick heuristic: TypeScript uses function keyword, types, semicolons
    stripped = body.strip()
    if stripped.startswith("function ") or stripped.startswith("export "):
        return "typescript"
    if stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("async def "):
        return "python"
    if "=>" in stripped or "function" in stripped or "const " in stripped or "let " in stripped:
        return "typescript"
    if ": " in stripped and ";" in stripped:
        return "typescript"
    return "python"


def _map_lang_to_ts(language: str) -> str:
    """Map language name to tree-sitter language key."""
    if language in ("typescript", "ts", "tsx"):
        return "typescript"
    return "python"


# ---------------------------------------------------------------------------
# Core analysis logic
# ---------------------------------------------------------------------------


class ComplexityAnalyzer:
    """Computes code complexity metrics using tree-sitter AST analysis."""

    def __init__(self):
        self.errors: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self, store: Store, file_paths: Optional[list[str]] = None
    ) -> list[ComplexityMetrics]:
        """Analyze specified files or all indexed files.

        Args:
            store: A tws-graph Store instance.
            file_paths: Optional list of file paths to limit analysis to.

        Returns:
            List of ComplexityMetrics, one per function/method node found.
        """
        self.errors = []
        results: list[ComplexityMetrics] = []

        all_files = store.get_all_files()
        if not all_files:
            return results

        # Filter by language (only python + typescript)
        supported = {"python", "typescript", "ts", "tsx"}

        for file_rec in all_files:
            path = file_rec.get("path", "")
            lang = file_rec.get("language", "")

            if lang not in supported:
                continue

            if file_paths and path not in file_paths:
                continue

            # Read source from disk
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except (FileNotFoundError, OSError) as e:
                self.errors.append(
                    {
                        "file_path": path,
                        "message": f"Cannot read file: {e}",
                        "severity": "error",
                    }
                )
                continue

            ts_lang = _map_lang_to_ts(lang)

            try:
                parser = get_parser(ts_lang)
            except Exception as e:
                self.errors.append(
                    {
                        "file_path": path,
                        "message": f"Failed to load parser for {ts_lang}: {e}",
                        "severity": "error",
                    }
                )
                continue

            try:
                tree = parser.parse(content)
            except Exception as e:
                self.errors.append(
                    {
                        "file_path": path,
                        "message": f"Parse error: {e}",
                        "severity": "error",
                    }
                )
                continue

            source_bytes = content.encode("utf-8")
            root = tree.root_node()

            # Find all function/method definitions
            func_nodes = self._find_function_nodes(root, source_bytes, lang, path)

            for func_node, node_info in func_nodes:
                func_source = _node_text(func_node, source_bytes)
                metrics = self.analyze_node(node_info, func_source)
                results.append(metrics)

        return results

    def analyze_node(self, node: dict, body: str) -> ComplexityMetrics:
        """Analyze a single function/method body and return complexity metrics.

        Args:
            node: Dict with id, qualified_name, file_path, language, etc.
            body: Complete source code of the function (used for tree-sitter parse).

        Returns:
            ComplexityMetrics for this function/method.
        """
        lang = node.get("language", "python")
        ts_lang = _map_lang_to_ts(lang)

        source_bytes = body.encode("utf-8")

        try:
            parser = get_parser(ts_lang)
            tree = parser.parse(body)
        except Exception:
            # Fallback: return zero metrics
            return ComplexityMetrics(
                node_id=node.get("id", ""),
                qualified_name=node.get("qualified_name", ""),
                file_path=node.get("file_path", ""),
                language=lang,
                cyclomatic=0,
                cognitive=0,
                halstead_volume=0.0,
                halstead_difficulty=0.0,
                halstead_effort=0.0,
                lines_of_code=0,
                risk_level="low",
            )

        root = tree.root_node()

        # Find the function definition node
        func_node = self._find_func_def(root, ts_lang)
        if func_node is None:
            # If we can't find the function definition, use the whole tree
            func_node = root

        # Extract function body (the block inside the function)
        func_body_node = self._get_func_body(func_node, ts_lang)
        if func_body_node is None:
            func_body_node = func_node

        # Compute metrics
        cyclomatic = self._compute_cyclomatic(func_body_node, ts_lang, source_bytes)
        cognitive = self._compute_cognitive(func_body_node, ts_lang, source_bytes, func_node)
        n1, n2, N1, N2 = self._compute_halstead_counts(func_node, ts_lang, source_bytes)

        # Halstead formulas
        if n1 + n2 > 0:
            volume = (N1 + N2) * math.log2(n1 + n2)
        else:
            volume = 0.0

        difficulty = (n1 / 2.0) * (N2 / max(n2, 1)) if n1 > 0 and n2 > 0 else 0.0

        # Round intermediate values before computing effort to preserve
        # the mathematical identity: effort = volume * difficulty
        volume_rounded = round(volume, 2)
        difficulty_rounded = round(difficulty, 2)
        effort = volume_rounded * difficulty_rounded

        # Lines of code
        loc = max(1, body.count("\n"))

        # Risk level
        risk = _compute_risk_level(cyclomatic, cognitive)

        return ComplexityMetrics(
            node_id=node.get("id", ""),
            qualified_name=node.get("qualified_name", ""),
            file_path=node.get("file_path", ""),
            language=lang,
            cyclomatic=cyclomatic,
            cognitive=cognitive,
            halstead_volume=volume_rounded,
            halstead_difficulty=difficulty_rounded,
            halstead_effort=round(effort, 2),
            lines_of_code=loc,
            risk_level=risk,
        )

    # ------------------------------------------------------------------
    # Internal: AST traversal helpers
    # ------------------------------------------------------------------

    def _find_func_def(self, root, ts_lang: str):
        """Find the first function definition node in the tree."""
        for child in _children(root):
            if child.kind() == "function_definition" and ts_lang == "python":
                return child
            if child.kind() in (
                "function_declaration",
                "function_expression",
                "arrow_function",
                "method_definition",
            ) and ts_lang == "typescript":
                return child
            result = self._find_func_def(child, ts_lang)
            if result:
                return result
        return None

    def _get_func_body(self, func_node, ts_lang: str):
        """Get the body block of a function definition node."""
        body = func_node.child_by_field_name("body")
        if body is not None:
            return body
        # Try consequence (some TS arrow functions)
        body = func_node.child_by_field_name("consequence")
        return body

    def _find_function_nodes(self, root, source_bytes, language, file_path):
        """Recursively find all function/method definition nodes.

        Returns list of (tree-sitter-node, node_info_dict).
        """
        results = []

        def walk(node, parent_qname_prefix=""):
            kind = node.kind()

            if language == "python":
                if kind == "function_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name = _node_text(name_node, source_bytes)
                        p = node.parent()
                        is_method = (
                            p is not None
                            and p.parent() is not None
                            and p.parent().kind() == "class_definition"
                        )
                        node_kind = "method" if is_method else "function"
                        qname = f"{file_path}::{name}"
                        if parent_qname_prefix:
                            qname = f"{parent_qname_prefix}::{name}"
                        import hashlib

                        nid = hashlib.sha256(f"{file_path}:{qname}".encode()).hexdigest()[:32]
                        sp = node.start_position()
                        ep = node.end_position()
                        info = {
                            "id": nid,
                            "qualified_name": qname,
                            "file_path": file_path,
                            "language": language,
                            "start_line": sp.row + 1,
                            "end_line": ep.row + 1,
                            "kind": node_kind,
                            "name": name,
                        }
                        results.append((node, info))
                        # Recurse into body for nested functions
                        body = node.child_by_field_name("body")
                        if body:
                            walk(body, qname)

                elif kind == "class_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        cls_name = _node_text(name_node, source_bytes)
                        cls_qname = f"{file_path}::{cls_name}"
                        if parent_qname_prefix:
                            cls_qname = f"{parent_qname_prefix}::{cls_name}"
                        body = node.child_by_field_name("body")
                        if body:
                            walk(body, cls_qname)

                # Recurse other nodes
                if kind not in ("function_definition", "class_definition"):
                    for child in _children(node):
                        walk(child, parent_qname_prefix)

            elif language in ("typescript", "ts", "tsx"):
                if kind == "function_declaration":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name = _node_text(name_node, source_bytes)
                        p = node.parent()
                        is_method = p is not None and p.kind() in ("class_body", "object")
                        if not is_method:
                            self._add_ts_func_node(
                                node, name, file_path, language, parent_qname_prefix, results, source_bytes
                            )
                            body = node.child_by_field_name("body")
                            qname = f"{file_path}::{name}"
                            if parent_qname_prefix:
                                qname = f"{parent_qname_prefix}::{name}"
                            if body:
                                walk(body, qname)

                elif kind == "method_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        name = _node_text(name_node, source_bytes)
                        self._add_ts_func_node(
                            node, name, file_path, language, parent_qname_prefix, results, source_bytes
                        )
                        body = node.child_by_field_name("body")
                        qname = f"{file_path}::{name}"
                        if parent_qname_prefix:
                            qname = f"{parent_qname_prefix}::{name}"
                        if body:
                            walk(body, qname)

                elif kind == "variable_declarator":
                    name_node = node.child_by_field_name("name")
                    value_node = node.child_by_field_name("value")
                    if name_node and value_node and value_node.kind() in (
                        "arrow_function",
                        "function_expression",
                    ):
                        name = _node_text(name_node, source_bytes)
                        self._add_ts_func_node(
                            value_node, name, file_path, language, parent_qname_prefix, results, source_bytes
                        )

                elif kind == "class_declaration":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        cls_name = _node_text(name_node, source_bytes)
                        cls_qname = f"{file_path}::{cls_name}"
                        if parent_qname_prefix:
                            cls_qname = f"{parent_qname_prefix}::{cls_name}"
                        body = node.child_by_field_name("body")
                        if body:
                            walk(body, cls_qname)

                if kind not in (
                    "function_declaration",
                    "method_definition",
                    "class_declaration",
                ):
                    for child in _children(node):
                        walk(child, parent_qname_prefix)

        walk(root)
        return results

    def _add_ts_func_node(
        self, node, name, file_path, language, parent_qname_prefix, results, source_bytes
    ):
        """Add a TypeScript function node to results."""
        import hashlib

        qname = f"{file_path}::{name}"
        if parent_qname_prefix:
            qname = f"{parent_qname_prefix}::{name}"
        nid = hashlib.sha256(f"{file_path}:{qname}".encode()).hexdigest()[:32]
        sp = node.start_position()
        ep = node.end_position()
        info = {
            "id": nid,
            "qualified_name": qname,
            "file_path": file_path,
            "language": language,
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "kind": "function",
            "name": name,
        }
        results.append((node, info))

    # ------------------------------------------------------------------
    # Cyclomatic complexity
    # ------------------------------------------------------------------

    def _compute_cyclomatic(self, node, ts_lang: str, source_bytes: bytes) -> int:
        """Compute cyclomatic complexity (M = 1 + number of decision points)."""
        decision_points = 0

        if ts_lang == "python":
            cyc_kinds = _PY_CYCLOMATIC_KINDS
        else:
            cyc_kinds = _TS_CYCLOMATIC_KINDS

        def walk(n):
            nonlocal decision_points
            kind = n.kind()

            if kind in cyc_kinds:
                decision_points += 1

            # Handle TypeScript logical operators (&&, ||)
            if ts_lang == "typescript":
                if kind in ("binary_expression",) and n.child_count() >= 2:
                    # Check operator token
                    op_text = _node_text(n.child(1), source_bytes) if n.child_count() > 1 else ""
                    if op_text in ("&&", "||"):
                        decision_points += 1

            # Handle case_clause in TypeScript (NOT switch_case which is the case label)
            # switch_case is already in cyc_kinds

            for child in _children(n):
                walk(child)

        walk(node)
        return decision_points + 1  # M = branches + 1

    # ------------------------------------------------------------------
    # Cognitive complexity (SonarSource simplified)
    # ------------------------------------------------------------------

    def _compute_cognitive(
        self, node, ts_lang: str, source_bytes: bytes, func_node
    ) -> int:
        """Compute cognitive complexity with nesting penalties."""
        cognitive = 0

        if ts_lang == "python":
            cog_kinds = _PY_COGNITIVE_KINDS
            body_fields = _COGNITIVE_BODY_FIELDS
        else:
            cog_kinds = _TS_COGNITIVE_KINDS
            body_fields = _COGNITIVE_BODY_FIELDS

        # Extract function/method name for recursion detection
        func_name = self._get_func_name(func_node, ts_lang, source_bytes)

        def walk(n, nesting: int):
            nonlocal cognitive
            kind = n.kind()

            if kind in cog_kinds:
                cognitive += 1 + nesting

                body_field_names = body_fields.get(kind, [])
                if not body_field_names and kind == "except_clause":
                    # Special: all except_clause children at nesting+1
                    for child in _children(n):
                        walk(child, nesting + 1)
                else:
                    for child in _children(n):
                        # Check if this child is a body field
                        is_body = False
                        for fname in body_field_names:
                            field_child = n.child_by_field_name(fname)
                            if _is_same_node(field_child, child):
                                is_body = True
                                break
                        if is_body:
                            walk(child, nesting + 1)
                        else:
                            walk(child, nesting)
                return

            # Logical operators (Python: boolean_operator)
            if kind == "boolean_operator" and ts_lang == "python":
                cognitive += 1

            # TypeScript logical operators (&&, ||)
            if ts_lang == "typescript" and kind == "binary_expression":
                if n.child_count() > 1:
                    op_text = _node_text(n.child(1), source_bytes)
                    if op_text in ("&&", "||"):
                        cognitive += 1

            # Recursive call detection
            if func_name and kind == "call":
                call_name = self._get_call_name(n, source_bytes)
                if call_name == func_name:
                    cognitive += 1

            for child in _children(n):
                walk(child, nesting)

        walk(node, 0)
        return cognitive

    def _get_func_name(self, func_node, ts_lang: str, source_bytes: bytes) -> Optional[str]:
        """Extract function name from a function definition node."""
        if func_node is None:
            return None
        name_node = func_node.child_by_field_name("name")
        if name_node:
            return _node_text(name_node, source_bytes)
        return None

    def _get_call_name(self, call_node, source_bytes: bytes) -> Optional[str]:
        """Get the callee name from a call expression node."""
        func_child = call_node.child_by_field_name("function")
        if func_child is None:
            func_child = call_node.child_by_field_name("callee")
        if func_child is None:
            return None
        # For simple identifier calls: foo()
        if func_child.kind() == "identifier":
            return _node_text(func_child, source_bytes)
        # For attribute calls: obj.method() → return method name
        if func_child.kind() in ("attribute", "member_expression"):
            prop = func_child.child_by_field_name("property")
            if prop:
                return _node_text(prop, source_bytes)
        return None

    # ------------------------------------------------------------------
    # Halstead metrics (heuristic approximation)
    # ------------------------------------------------------------------

    def _compute_halstead_counts(
        self, node, ts_lang: str, source_bytes: bytes
    ) -> tuple[int, int, int, int]:
        """Compute Halstead operator/operand counts (n1, n2, N1, N2).

        Uses a heuristic approach:
        - Operators: keywords + operators + structural nodes
        - Operands: identifiers + literals
        - Distinction by text: operators counted by kind+token, operands by name
        """
        if ts_lang == "python":
            op_kinds = _PY_HALSTEAD_OPERATOR_KINDS
            operand_kinds = _PY_OPERAND_KINDS
        else:
            op_kinds = _TS_HALSTEAD_OPERATOR_KINDS
            operand_kinds = _TS_OPERAND_KINDS

        unique_ops: set[str] = set()
        unique_operands: set[str] = set()
        total_ops = 0
        total_operands = 0

        # Track identifiers seen as operators (function names) to avoid double counting
        func_name_ids: set[str] = set()

        def walk(n):
            nonlocal total_ops, total_operands
            kind = n.kind()

            # Count operators
            if kind in op_kinds:
                token = _node_text(n, source_bytes).strip()
                # For structural nodes (if, for, etc.), use the keyword
                if kind in (
                    "if_statement",
                    "for_statement",
                    "while_statement",
                    "return_statement",
                    "for_in_statement",
                    "do_statement",
                    "function_declaration",
                    "arrow_function",
                    "function_expression",
                    "method_definition",
                    "class_declaration",
                    "switch_statement",
                ):
                    # Extract the keyword
                    keyword = token.split()[0] if token else kind
                    if _is_keyword_token(keyword) or _is_operator_token(keyword):
                        unique_ops.add(keyword)
                        total_ops += 1

            # Count operators by token for expression types
            if kind in (
                "binary_operator",
                "unary_operator",
                "boolean_operator",
                "comparison_operator",
                "assignment",
                "augmented_assignment",
                "not_operator",
                "binary_expression",
                "unary_expression",
                "assignment_expression",
                "augmented_assignment_expression",
                "update_expression",
            ):
                op_text = _node_text(n, source_bytes).strip()
                if op_text and (_is_operator_token(op_text) or _is_keyword_token(op_text)):
                    unique_ops.add(op_text)
                    total_ops += 1

            # Count function call names as operands
            if kind == "call":
                func_child = n.child_by_field_name("function")
                if func_child and func_child.kind() == "identifier":
                    fname = _node_text(func_child, source_bytes)
                    if fname:
                        unique_operands.add(fname)
                        total_operands += 1
                        func_name_ids.add(fname)

            # Count identifiers and literals as operands
            if kind in operand_kinds:
                text = _node_text(n, source_bytes).strip()
                if kind == "identifier":
                    if text and text not in _KEYWORD_TOKENS:
                        # Don't double-count if already seen as function name
                        if text not in func_name_ids or True:  # Count all
                            unique_operands.add(text)
                            total_operands += 1
                elif kind in ("integer", "float", "number"):
                    # Normalize numbers for uniqueness
                    unique_operands.add("<number>")
                    total_operands += 1
                elif kind in ("string", "template_string"):
                    unique_operands.add("<string>")
                    total_operands += 1
                elif kind in ("true", "false", "none", "null", "undefined"):
                    unique_operands.add(text)
                    total_operands += 1

            for child in _children(n):
                walk(child)

        walk(node)

        # Ensure minimum counts
        n1 = len(unique_ops)
        n2 = len(unique_operands)
        N1 = total_ops
        N2 = total_operands

        return n1, n2, N1, N2


# ---------------------------------------------------------------------------
# Risk level classification
# ---------------------------------------------------------------------------


def _compute_risk_level(cyclomatic: int, cognitive: int) -> str:
    """Determine risk level from cyclomatic and cognitive complexity.

    | Cyclomatic | Cognitive | Risk Level |
    |-----------|-----------|------------|
    | <= 5      | <= 5      | low        |
    | 6-10      | 6-15      | medium     |
    | 11-20     | 16-30     | high       |
    | > 20      | > 30      | critical   |
    """
    # Map each metric to a level
    def _level_from_cyclomatic(c):
        if c <= 5:
            return 0  # low
        elif c <= 10:
            return 1  # medium
        elif c <= 20:
            return 2  # high
        else:
            return 3  # critical

    def _level_from_cognitive(c):
        if c <= 5:
            return 0  # low
        elif c <= 15:
            return 1  # medium
        elif c <= 30:
            return 2  # high
        else:
            return 3  # critical

    cyc_level = _level_from_cyclomatic(cyclomatic)
    cog_level = _level_from_cognitive(cognitive)

    # Take the higher (more severe) level
    max_level = max(cyc_level, cog_level)
    risk_map = {0: "low", 1: "medium", 2: "high", 3: "critical"}
    return risk_map[max_level]
