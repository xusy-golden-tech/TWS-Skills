"""P10: Route detector for cross-service HTTP route and call analysis.

Uses tree-sitter AST traversal + a YAML pattern database to detect
HTTP route definitions and outbound HTTP calls across Python, TypeScript,
Java, and Go source files.

Output: edges with kind "http_route" or "http_calls", consumed by
the index pipeline to build Route nodes and HTTP_CALLS edges.
"""

from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# HTTP method constants
# ---------------------------------------------------------------------------

_HTTP_METHOD_PATTERNS = {
    "get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE",
    "patch": "PATCH", "head": "HEAD", "options": "OPTIONS",
    "all": "UNKNOWN",
}


def _infer_http_method(pattern_key: str) -> str:
    """Extract HTTP method from a pattern or full decorator string.

    Examples:
        "@app.get"       → "GET"
        "@router.post"   → "POST"
        "@GetMapping(\"/a\")" → "GET"
        "path("          → "UNKNOWN"
        "app.get(\"/a\")" → "GET"
    """
    # Strip leading @ and trim anything after '(' (arguments)
    text = pattern_key.lstrip("@").split("(")[0].lower()
    # Strip common suffixes for framework-agnostic matching
    text = text.replace("mapping", "")

    # Check for explicit method words: .get, .post, get, post, etc.
    for word, method in _HTTP_METHOD_PATTERNS.items():
        if text.endswith("." + word) or text.endswith(word):
            return method
        if text.startswith(word):
            return method
    return "UNKNOWN"


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _children(node):
    for i in range(node.child_count()):
        yield node.child(i)


# ---------------------------------------------------------------------------
# RouteDetector
# ---------------------------------------------------------------------------

class RouteDetector:
    """Detect HTTP route definitions and outbound HTTP calls in source files.

    Uses a YAML pattern database (services/patterns.yaml) that catalogues
    known framework-specific patterns across Python, TypeScript, Java, and Go.

    Integration point: called from the index pipeline after tree-sitter
    extraction.  Returns edges that feed into Route node creation and
    HTTP_CALLS edge population.
    """

    def __init__(self, patterns_path: str | None = None):
        """Load patterns.yaml.

        Args:
            patterns_path: Path to patterns.yaml.  Defaults to the file
                shipped alongside this module.
        """
        if patterns_path is None:
            patterns_path = str(
                Path(__file__).resolve().parent / "patterns.yaml"
            )
        self._patterns: dict[str, Any] = {}
        if os.path.isfile(patterns_path):
            with open(patterns_path, "r", encoding="utf-8") as f:
                self._patterns = yaml.safe_load(f) or {}
        self._lang_patterns: dict[str, list[dict]] = {}
        self._build_index()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def detect(
        self,
        source: bytes,
        tree,
        file_path: str,
        language: str,
    ) -> list[dict]:
        """Detect route definitions and HTTP calls in a single source file.

        Args:
            source: Raw source bytes.
            tree: tree-sitter parse tree.
            file_path: Relative path for location tagging.
            language: One of 'python', 'typescript', 'java', 'go'.

        Returns:
            List of edge dicts, each with keys:
                source, target, kind, target_text, http_method,
                source_loc, provenance
        """
        if language not in self._lang_patterns:
            return []

        patterns = self._lang_patterns[language]
        extractors = {
            "python": self._detect_python,
            "typescript": self._detect_typescript,
            "java": self._detect_java,
            "go": self._detect_go,
        }

        extractor = extractors.get(language)
        if extractor is None:
            return []

        edges = extractor(source, tree, file_path, patterns)
        return edges

    # ------------------------------------------------------------------
    # pattern index builder
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Flatten nested language → category → entries into a simple dict."""
        langs = self._patterns.get("languages", {})
        for lang_name, categories in langs.items():
            entries: list[dict] = []
            for category_name, patterns in categories.items():
                for p in patterns:
                    entry = dict(p)
                    entry["_category"] = category_name
                    entries.append(entry)
            self._lang_patterns[lang_name] = entries

    # ------------------------------------------------------------------
    # per-language detection
    # ------------------------------------------------------------------

    def _detect_python(
        self,
        source: bytes,
        tree,
        file_path: str,
        patterns: list[dict],
    ) -> list[dict]:
        """Python: traverse decorators + call expressions for route defs."""
        edges: list[dict] = []
        root = tree.root_node()

        def walk(node):
            kind = node.kind()

            # --- decorators on route handlers ---
            if kind == "decorator":
                dec_text = _node_text(node, source)
                matched = self._match_decorator_by_method(
                    dec_text, source, node, patterns
                )
                if matched:
                    edge = self._build_edge_from_decorator(
                        node, source, file_path, dec_text, matched
                    )
                    if edge:
                        edges.append(edge)

            # --- function calls (routes + HTTP client calls) ---
            elif kind == "call":
                func_node = node.child_by_field_name("function")
                if func_node:
                    call_text = _node_text(func_node, source)
                    # Try route patterns first
                    matched = self._match_route_pattern(call_text, patterns)
                    if matched:
                        args_node = node.child_by_field_name("arguments")
                        path = self._extract_first_string_arg(args_node, source)
                        if path is not None:
                            http_method = _infer_http_method(call_text)
                            edges.append({
                                "source": "",
                                "target": _hash_id(
                                    f"{file_path}::__route__{http_method}{path}", file_path
                                ),
                                "kind": "http_route",
                                "target_text": path,
                                "http_method": http_method,
                                "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                "provenance": "tree-sitter",
                            })
                    else:
                        # Try HTTP client call patterns (requests.get, httpx, etc.)
                        http_call = RouteDetector._detect_http_client_call(
                            call_text, node, source, file_path
                        )
                        if http_call:
                            edges.append(http_call)

            for child in _children(node):
                walk(child)

        walk(root)
        return edges

    def _detect_typescript(
        self,
        source: bytes,
        tree,
        file_path: str,
        patterns: list[dict],
    ) -> list[dict]:
        """TypeScript: decorators (NestJS) + call expressions (Express)."""
        edges: list[dict] = []
        root = tree.root_node()

        def walk(node):
            kind = node.kind()

            # --- decorators (NestJS: @Get("/path"), @Controller("/prefix")) ---
            if kind == "decorator":
                dec_text = _node_text(node, source)
                matched = self._match_decorator(dec_text, patterns)
                if matched:
                    edge = self._build_edge_from_decorator(
                        node, source, file_path, dec_text, matched
                    )
                    if edge:
                        edges.append(edge)

            # --- call expressions (Express: app.get("/path", handler)) ---
            elif kind == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node and func_node.kind() == "member_expression":
                    call_text = _node_text(func_node, source)
                    matched = self._match_route_pattern(call_text, patterns)
                    if matched:
                        args = node.child_by_field_name("arguments")
                        path = self._extract_first_string_arg(args, source)
                        if path is not None:
                            http_method = _infer_http_method(call_text)
                            edges.append({
                                "source": "",
                                "target": _hash_id(
                                    f"{file_path}::__route__{http_method}{path}", file_path
                                ),
                                "kind": "http_route",
                                "target_text": path,
                                "http_method": http_method,
                                "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                "provenance": "tree-sitter",
                            })

            for child in _children(node):
                walk(child)

        walk(root)
        return edges

    def _detect_java(
        self,
        source: bytes,
        tree,
        file_path: str,
        patterns: list[dict],
    ) -> list[dict]:
        """Java: annotations (Spring, Jakarta) + method invocations."""
        edges: list[dict] = []
        root = tree.root_node()

        def walk(node):
            kind = node.kind()

            # --- annotations (@GetMapping("/path"), @Path("/resource")) ---
            if kind == "marker_annotation" or kind == "annotation":
                ann_text = _node_text(node, source)
                matched = self._match_route_pattern(ann_text, patterns)
                if matched:
                    http_method = _infer_http_method(ann_text)
                    # Extract path from annotation arguments
                    path = self._extract_java_annotation_path(node, source)
                    if path is not None:
                        edges.append({
                            "source": "",
                            "target": _hash_id(
                                f"{file_path}::__route__{http_method}{path}", file_path
                            ),
                            "kind": "http_route",
                            "target_text": path,
                            "http_method": http_method,
                            "source_loc": f"{file_path}:{node.start_position().row + 1}",
                            "provenance": "tree-sitter",
                        })

            for child in _children(node):
                walk(child)

        walk(root)
        return edges

    def _detect_go(
        self,
        source: bytes,
        tree,
        file_path: str,
        patterns: list[dict],
    ) -> list[dict]:
        """Go: call expressions for net/http, gorilla/mux, chi, gin, echo, fiber."""
        edges: list[dict] = []
        root = tree.root_node()

        def walk(node):
            kind = node.kind()

            if kind == "call_expression":
                # Try selector_expression (.GET, .POST, etc.)
                func_node = node.child_by_field_name("function")
                if func_node:
                    if func_node.kind() == "selector_expression":
                        call_text = _node_text(func_node, source)
                        matched = self._match_route_pattern(call_text, patterns)
                        if matched:
                            args = node.child_by_field_name("arguments")
                            path = self._extract_first_string_arg(args, source)
                            if path is not None:
                                http_method = _infer_http_method(call_text)
                                edges.append({
                                    "source": "",
                                    "target": _hash_id(
                                        f"{file_path}::__route__{http_method}{path}", file_path
                                    ),
                                    "kind": "http_route",
                                    "target_text": path,
                                    "http_method": http_method,
                                    "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                    "provenance": "tree-sitter",
                                })
                    # Plain function calls: http.HandleFunc("/path", ...)
                    elif func_node.kind() == "identifier":
                        call_text = _node_text(func_node, source)
                        matched = self._match_route_pattern(call_text, patterns)
                        if matched:
                            args = node.child_by_field_name("arguments")
                            path = self._extract_first_string_arg(args, source)
                            if path is not None:
                                http_method = _infer_http_method(call_text)
                                edges.append({
                                    "source": "",
                                    "target": _hash_id(
                                        f"{file_path}::__route__{http_method}{path}", file_path
                                    ),
                                    "kind": "http_route",
                                    "target_text": path,
                                    "http_method": http_method,
                                    "source_loc": f"{file_path}:{node.start_position().row + 1}",
                                    "provenance": "tree-sitter",
                                })
                    # Qualified calls: mux.HandleFunc("/path", handler)
                    elif func_node.kind() == "qualified_type" or func_node.kind() == "selector_expression":
                        pass  # handled by selector_expression above

            for child in _children(node):
                walk(child)

        walk(root)
        return edges

    # ------------------------------------------------------------------
    # pattern matching helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_decorator(
        dec_text: str, patterns: list[dict]
    ) -> dict | None:
        """Match a decorator string against route patterns.

        For patterns like \"@app.route\" or \"@router.get\", we match on the
        method-name suffix (e.g. \".route(\", \".get(\") so that variable names
        like `api`, `router`, `bp` are all matched correctly.

        For annotation-style patterns (Java), we do substring matching.
        """
        for p in patterns:
            if p.get("kind") not in ("decorator", "annotation"):
                continue
            pat = p["pattern"]
            # Exact match: e.g. "@SubscribeMessage" is unambiguous
            if pat in dec_text:
                return p
        return None

    @staticmethod
    def _match_decorator_by_method(
        dec_text: str, source: bytes, decorator_node, patterns: list[dict]
    ) -> dict | None:
        """Match a Python/TS decorator by extracting the method name.

        For decorators like @api.get('/path'), the call→attribute node
        contains 'api.get'. We match '.get' against known route methods.
        """
        # Try to extract the method name from the decorator's call child
        method_name = None
        for child in _children(decorator_node):
            if child.kind() == "call":
                for cc in _children(child):
                    if cc.kind() == "attribute":
                        att_text = _node_text(cc, source)
                        method_name = att_text.split(".")[-1] if "." in att_text else att_text
                        break
                break

        if method_name is None:
            # Fallback: try substring matching
            for p in patterns:
                if p.get("kind") in ("decorator", "annotation"):
                    if p["pattern"] in dec_text:
                        return p
            return None

        # Match method_name against patterns that end with it
        find_str = f".{method_name}"
        for p in patterns:
            if p.get("kind") in ("decorator", "annotation"):
                pat = p["pattern"]
                if pat.endswith(find_str):
                    return p
        return None

    @staticmethod
    def _match_route_pattern(
        call_text: str, patterns: list[dict]
    ) -> dict | None:
        """Match a call expression text against route patterns.

        Patterns may include trailing '(' (e.g. \"app.get(\"), but the
        call_text from tree-sitter is the function identifier without parens.
        We match against both the text and text + '('.
        """
        call_with_paren = call_text + "("
        for p in patterns:
            if p.get("kind") in ("function", "method", "class", "constructor", "annotation"):
                pat = p["pattern"]
                # Try exact substring match against call_text, and also
                # against call_text + '(' for patterns that end with '('
                if pat in call_text or pat in call_with_paren:
                    return p
                # Also try: strip trailing '(' from pattern and match
                if pat.endswith("("):
                    pat_no_paren = pat.rstrip("(")
                    if pat_no_paren in call_text:
                        return p
        return None

    # ------------------------------------------------------------------
    # argument extraction
    # ------------------------------------------------------------------

    # Tree-sitter string literal kinds across languages
    _STRING_KINDS = frozenset({
        "string", "string_literal",           # Python, TypeScript, Java
        "interpreted_string_literal",         # Go: "..."
        "raw_string_literal",                 # Go: `...`
        "rune_literal",                       # Go: 'x'
        "template_string",                    # TypeScript: `...`
        "template_literal",                   # Java: text blocks
    })

    @staticmethod
    def _extract_first_string_arg(args_node, source: bytes) -> str | None:
        """Extract the first string literal from a tree-sitter argument_list."""
        if args_node is None:
            return None
        for child in _children(args_node):
            if child.kind() in RouteDetector._STRING_KINDS:
                text = _node_text(child, source)
                # Strip quotes
                text = text.strip()
                if (text.startswith('"') and text.endswith('"')) or \
                   (text.startswith("'") and text.endswith("'")):
                    text = text[1:-1]
                elif text.startswith("`") and text.endswith("`"):
                    text = text[1:-1]
                return text
            elif child.kind() == "binary_expression":
                # e.g., "/api" + "/v1" — take first string piece only
                left = child.child_by_field_name("left")
                if left and left.kind() in RouteDetector._STRING_KINDS:
                    text = _node_text(left, source).strip()
                    if (text.startswith('"') and text.endswith('"')) or \
                       (text.startswith("'") and text.endswith("'")):
                        text = text[1:-1]
                    return text
        return None

    @staticmethod
    def _extract_java_annotation_path(
        node, source: bytes
    ) -> str | None:
        """Extract path value from Java annotation arguments.

        Handles:
            @GetMapping("/path")
            @RequestMapping(value="/path", method=GET)
            @Path("/resource")
        """
        for child in _children(node):
            if child.kind() == "annotation_argument_list":
                for arg in _children(child):
                    # Bare string: @GetMapping("/path")
                    if arg.kind() == "string_literal":
                        text = _node_text(arg, source).strip()
                        if text.startswith('"') and text.endswith('"'):
                            return text[1:-1]
                    # Named: @RequestMapping(value="/path")
                    elif arg.kind() == "element_value_pair":
                        val = arg.child_by_field_name("value")
                        if val and val.kind() == "string_literal":
                            text = _node_text(val, source).strip()
                            if text.startswith('"') and text.endswith('"'):
                                return text[1:-1]
        return None

    # ------------------------------------------------------------------
    # edge construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_edge_from_decorator(
        node,
        source: bytes,
        file_path: str,
        dec_text: str,
        matched: dict,
    ) -> dict | None:
        """Build a http_route edge from a decorator/annotation node.

        In Python tree-sitter, the decorator node structure is:
            decorator → call → argument_list → string | keyword_argument
        In TypeScript/NestJS:
            decorator → arguments → string
        This method handles both, searching deeply for the argument list.
        """
        # First, find the argument container (direct or inside a call node)
        arg_container = None
        for child in _children(node):
            if child.kind() in ("arguments", "argument_list"):
                arg_container = child
                break
            elif child.kind() in ("call", "call_expression"):
                # Python (call) / TypeScript (call_expression): args inside call child
                for cc in _children(child):
                    if cc.kind() in ("arguments", "argument_list"):
                        arg_container = cc
                        break
                if arg_container:
                    break

        if arg_container is None:
            return None

        path = None
        for arg in _children(arg_container):
            if arg.kind() == "string" or arg.kind() == "string_literal":
                text = _node_text(arg, source).strip()
                if (text.startswith('"') and text.endswith('"')) or \
                   (text.startswith("'") and text.endswith("'")):
                    path = text[1:-1]
                elif text.startswith("`") and text.endswith("`"):
                    path = text[1:-1]
                else:
                    path = text
                break
            elif arg.kind() == "keyword_argument":
                # Flask: @app.route("/path", methods=["POST"])
                name = arg.child_by_field_name("name")
                if name and _node_text(name, source) == "path":
                    val = arg.child_by_field_name("value")
                    if val and (val.kind() == "string" or val.kind() == "string_literal"):
                        text = _node_text(val, source).strip()
                        if text.startswith('"') and text.endswith('"'):
                            path = text[1:-1]
                        elif text.startswith("'") and text.endswith("'"):
                            path = text[1:-1]

        if path is None:
            return None

        http_method = _infer_http_method(dec_text)

        # For Flask @app.route, check if methods=[...] is specified
        if matched["pattern"] in ("@app.route", "@bp.route"):
            http_method = _extract_flask_methods_from_container(arg_container, source)

        return {
            "source": "",
            "target": _hash_id(
                f"{file_path}::__route__{http_method}{path}", file_path
            ),
            "kind": "http_route",
            "target_text": path,
            "http_method": http_method,
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "provenance": "tree-sitter",
        }

    @staticmethod
    def _detect_http_client_call(
        call_text: str,
        node,
        source: bytes,
        file_path: str,
    ) -> dict | None:
        """Detect HTTP client calls (requests.get, httpx.get, urllib, etc.).

        Returns a http_calls edge dict or None.
        """
        http_client_patterns = [
            "requests.get", "requests.post", "requests.put",
            "requests.delete", "requests.patch", "requests.head",
            "requests.options", "requests.request",
            "httpx.get", "httpx.post", "httpx.put",
            "httpx.delete", "httpx.patch", "httpx.head",
            "httpx.options", "httpx.request",
            "urllib.request.urlopen",
            "aiohttp.ClientSession",
            "fetch(",
            "axios.get", "axios.post", "axios.put",
            "axios.delete", "axios.patch",
            "$.get", "$.post", "$.ajax",
            "RestTemplate",
            "WebClient",
            "HttpClient",
            "OkHttpClient",
        ]

        matched_client = None
        for cp in http_client_patterns:
            if cp in call_text:
                matched_client = cp
                break

        if not matched_client:
            return None

        # Try to extract target URL
        http_method = _infer_http_method(call_text)
        args = node.child_by_field_name("arguments")
        target_url = ""
        if args:
            url = RouteDetector._extract_first_string_arg(args, source)
            if url:
                target_url = url

        return {
            "source": "",
            "target": _hash_id(
                f"{file_path}::__http_call__{target_url}", file_path
            ),
            "kind": "http_calls",
            "target_text": target_url,
            "http_method": http_method,
            "source_loc": f"{file_path}:{node.start_position().row + 1}",
            "provenance": "tree-sitter",
        }


# ---------------------------------------------------------------------------
# Flask-specific helpers
# ---------------------------------------------------------------------------

def _extract_flask_methods_from_container(arg_container, source: bytes) -> str:
    """Extract HTTP method from Flask @app.route(..., methods=["POST"]).

    Takes the argument_list node (already found within the decorator→call→argument_list tree).
    Defaults to "GET" if no methods= specified.
    """
    for arg in _children(arg_container):
        if arg.kind() == "keyword_argument":
            name = arg.child_by_field_name("name")
            if name and _node_text(name, source) == "methods":
                val = arg.child_by_field_name("value")
                if val and val.kind() == "list":
                    for item in _children(val):
                        if item.kind() == "string":
                            method = _node_text(item, source).strip()
                            method = method.strip("'\"")
                            return method.upper()
    return "GET"
