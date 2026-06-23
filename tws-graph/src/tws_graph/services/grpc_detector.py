"""GrpcDetector — 跨语言 protobuf + gRPC stub 检测器。

检测 .proto 文件的 service/rpc 定义，以及各语言中的 gRPC stub 用法。

Uses tree-sitter >= 0.25 method-based API.
"""

from __future__ import annotations

import os
import yaml

from tws_graph.edges.kind import EdgeKind
from tws_graph.indexer.base import hash_id


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------

def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _children(node):
    for i in range(node.child_count()):
        yield node.child(i)


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


# ---------------------------------------------------------------------------
# Pattern loading
# ---------------------------------------------------------------------------

def _default_patterns_path() -> str:
    return os.path.join(os.path.dirname(__file__), "patterns.yaml")


def _load_patterns(path: str | None) -> dict:
    if path is None:
        path = _default_patterns_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Direction mapping
# ---------------------------------------------------------------------------

_DIRECTION_MAP: dict[str, str] = {
    "client": EdgeKind.GRPC_CLIENT,
    "server": EdgeKind.GRPC_SERVER,
    "service": EdgeKind.GRPC_SERVICE,
    "method": EdgeKind.GRPC_SERVICE,
}


def _map_direction(raw: str | None, pattern_name: str = "") -> str:
    """Map direction string to EdgeKind, inferring from pattern name if needed."""
    if raw is not None:
        mapped = _DIRECTION_MAP.get(raw.lower())
        if mapped is not None:
            return mapped
    # Infer direction from pattern name
    if pattern_name:
        lower = pattern_name.lower()
        if "server" in lower or "register" in lower or "addservice" in lower:
            return EdgeKind.GRPC_SERVER
        if "channel" in lower or "stub" in lower or "dial" in lower or "client" in lower:
            return EdgeKind.GRPC_CLIENT
        if "service" in lower or "rpc" in lower:
            return EdgeKind.GRPC_SERVICE
    return EdgeKind.GRPC_CLIENT


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

def _match_pattern(callee_name: str, patterns: list[dict]) -> dict | None:
    """Match a callee name against the pattern list.

    Uses case-insensitive matching.  Only matches when the full pattern
    string appears as a substring, or the last component matches.
    Avoids false matches on common prefixes (e.g. "grpc" matching both
    "grpc.server" and "grpc.insecure_channel").
    """
    if not callee_name or not patterns:
        return None
    callee_lower = callee_name.lower()
    callee_parts = callee_lower.split(".")
    for pat in patterns:
        pattern_str = pat.get("pattern", "").lstrip("@").lstrip(".")
        if not pattern_str:
            continue
        # Strip trailing parentheses for matching (e.g. ".Stub(" → ".Stub")
        pattern_clean = pattern_str.rstrip("(").rstrip(")")
        pattern_lower = pattern_clean.lower()
        pattern_parts = pattern_lower.split(".")

        # Exact match (case-insensitive)
        if callee_lower == pattern_lower:
            return pat
        # Callee ends with .pattern
        if callee_lower.endswith("." + pattern_lower):
            return pat
        # Full pattern string appears as substring (case-insensitive)
        # e.g. "Stub" in "helloworld_pb2_grpc.GreeterStub"
        if pattern_lower in callee_lower:
            return pat
        # Last component matches (e.g. "Server" matches "NewServer" or "RegisterGreeterServer")
        callee_last = callee_parts[-1]
        pattern_last = pattern_parts[-1]
        if pattern_last and pattern_last in callee_last:
            return pat
        # Multi-component patterns: require ALL parts to appear in order
        # (e.g. "grpc.Dial" matches "google.golang.org/grpc.Dial" because
        #  both "grpc" and "dial" appear in order)
        if len(pattern_parts) >= 2:
            if len(callee_parts) >= len(pattern_parts):
                suffix = callee_parts[-len(pattern_parts):]
                if suffix == pattern_parts:
                    return pat
    return None


def _extract_callee_name(func_node, source: bytes) -> str:
    """Extract the full dotted name from a function node."""
    if func_node.kind() == "identifier":
        return _node_text(func_node, source)
    parts: list[str] = []
    for child in _children(func_node):
        if child.is_named():
            if child.kind() == "identifier":
                parts.append(_node_text(child, source))
            elif child.kind() in ("attribute", "member_expression",
                                  "call_expression", "call"):
                inner = _extract_callee_name(child, source)
                if inner:
                    parts.append(inner)
    # Go selector_expression
    if func_node.kind() == "selector_expression":
        left = func_node.child_by_field_name("operand")
        right = func_node.child_by_field_name("field")
        if left and right:
            left_name = _extract_callee_name(left, source)
            right_name = _node_text(right, source)
            if left_name and right_name:
                return f"{left_name}.{right_name}"
    return ".".join(parts)


# ===================================================================
# .proto file detection
# ===================================================================

def _detect_proto(source: bytes, tree, file_path: str) -> list[dict]:
    """Detect gRPC service/rpc definitions in .proto files.

    Proto AST structure (tree-sitter-proto):
      service_declaration
        name: "MyService"
        body:
          rpc_method
            name: "GetUser"
            input_type: ...
            output_type: ...
    """
    edges: list[dict] = []
    root = tree.root_node()

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    # Find all service declarations
    def find_services(node, services: list):
        if node.kind() == "service_declaration" or (
            # tree-sitter-proto uses "service" or "service_declaration"
            "service" in node.kind()
        ):
            services.append(node)
        for child in _children(node):
            find_services(child, services)

    service_nodes: list = []
    find_services(root, service_nodes)

    for svc in service_nodes:
        svc_name = ""
        for child in _named_children(svc):
            if child.kind() == "identifier" or child.kind() == "type_identifier":
                svc_name = _node_text(child, source)
                break

        svc_nid = _qname_to_nid(f"{file_path}::{svc_name}")

        # Find rpc methods
        def find_rpcs(node, rpcs: list):
            if node.kind() == "rpc_method" or node.kind() == "rpc" or (
                "rpc" in node.kind() and "method" in node.kind()
            ):
                rpcs.append(node)
            for child in _children(node):
                find_rpcs(child, rpcs)

        rpc_nodes: list = []
        find_rpcs(svc, rpc_nodes)

        for rpc in rpc_nodes:
            rpc_name = ""
            for child in _named_children(rpc):
                if child.kind() == "identifier" or child.kind() == "type_identifier":
                    rpc_name = _node_text(child, source)
                    break

            line = svc.start_position().row + 1
            edges.append({
                "source": svc_nid,
                "target": "",
                "kind": EdgeKind.GRPC_SERVICE,
                "target_text": svc_name,
                "grpc_method": rpc_name,
                "source_loc": f"{file_path}:{line}",
                "provenance": "tree-sitter",
            })

    return edges


# ===================================================================
# Python gRPC detection
# ===================================================================

def _detect_python_grpc(
    source: bytes, tree, file_path: str, grpc_patterns: list[dict],
) -> list[dict]:
    """Detect gRPC usage in Python source files.

    Detects:
    - grpc.server(...) → server
    - grpc.insecure_channel(...) / grpc.secure_channel(...) → client
    - XxxStub(...) → client
    - add_XxxServicer_to_server(...) → server
    """
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        if node.kind() in ("function_definition", "async_function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                fname = _node_text(name_node, source)
                parts = [file_path]
                if class_name:
                    parts.append(class_name)
                parts.append(fname)
                qname = "::".join(parts)
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        if node.kind() == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                callee_name = _extract_callee_name(func_node, source)
                pat = _match_pattern(callee_name, grpc_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"), pat.get("pattern", ""))

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    # Extract service/method name if available
                    target_text, grpc_method = _extract_grpc_identifiers(
                        callee_name, func_node, source,
                    )

                    edge = {
                        "source": source_nid,
                        "target": "",
                        "kind": direction,
                        "target_text": target_text or callee_name,
                        "grpc_method": grpc_method,
                        "source_loc": source_loc,
                        "provenance": "tree-sitter",
                    }
                    edges.append(edge)

        saved_class = class_name
        if node.kind() == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                class_name = _node_text(name_node, source)

        for child in _children(node):
            walk(child, class_name)

        class_name = saved_class

        if node.kind() in ("function_definition", "async_function_definition"):
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


# ===================================================================
# TypeScript gRPC detection (minimal)
# ===================================================================

def _detect_typescript_grpc(
    source: bytes, tree, file_path: str, grpc_patterns: list[dict],
) -> list[dict]:
    """Detect gRPC usage in TypeScript source files.

    TypeScript gRPC patterns vary significantly across versions,
    so this provides minimal detection.
    """
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        if node.kind() in ("function_declaration", "method_definition",
                           "arrow_function", "function_expression"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                mname = _node_text(name_node, source)
                parts = [file_path]
                if class_name:
                    parts.append(class_name)
                parts.append(mname)
                qname = "::".join(parts)
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        # Decorators: @GrpcMethod, @GrpcService
        if node.kind() == "decorator":
            for child in _named_children(node):
                if child.kind() == "call_expression":
                    func_node = child.child_by_field_name("function")
                    if func_node is not None:
                        callee_name = _extract_callee_name(func_node, source)
                        pat = _match_pattern(callee_name, grpc_patterns)
                        if pat:
                            direction = _map_direction(pat.get("direction"), pat.get("pattern", ""))

                            source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                            source_loc = f"{file_path}:{node.start_position().row + 1}"

                            edge = {
                                "source": source_nid,
                                "target": "",
                                "kind": direction,
                                "target_text": callee_name,
                                "grpc_method": "",
                                "source_loc": source_loc,
                                "provenance": "tree-sitter",
                            }
                            edges.append(edge)

        if node.kind() == "new_expression":
            constructor = node.child_by_field_name("constructor")
            if constructor is not None:
                callee_name = _extract_callee_name(constructor, source)
                pat = _match_pattern(callee_name, grpc_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"), pat.get("pattern", ""))

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    edge = {
                        "source": source_nid,
                        "target": "",
                        "kind": direction,
                        "target_text": callee_name,
                        "grpc_method": "",
                        "source_loc": source_loc,
                        "provenance": "tree-sitter",
                    }
                    edges.append(edge)

        saved_class = class_name
        if node.kind() == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                class_name = _node_text(name_node, source)

        for child in _children(node):
            walk(child, class_name)

        class_name = saved_class

        if node.kind() in ("function_declaration", "method_definition",
                           "arrow_function", "function_expression"):
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


# ===================================================================
# Java gRPC detection
# ===================================================================

def _detect_java_grpc(
    source: bytes, tree, file_path: str, grpc_patterns: list[dict],
) -> list[dict]:
    """Detect gRPC usage in Java source files.

    Detects:
    - ManagedChannelBuilder.forAddress(...) → client
    - ServerBuilder.forPort(...) → server
    - .newBlockingStub(...) / .newStub(...) → client
    - .addService(...) → server
    """
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        if node.kind() == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                mname = _node_text(name_node, source)
                parts = [file_path]
                if class_name:
                    parts.append(class_name)
                parts.append(mname)
                qname = "::".join(parts)
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        if node.kind() == "method_invocation":
            callee_name = _extract_java_method_name(node, source)
            pat = _match_pattern(callee_name, grpc_patterns)
            if pat:
                direction = _map_direction(pat.get("direction"), pat.get("pattern", ""))

                source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                source_loc = f"{file_path}:{node.start_position().row + 1}"

                edge = {
                    "source": source_nid,
                    "target": "",
                    "kind": direction,
                    "target_text": callee_name,
                    "grpc_method": "",
                    "source_loc": source_loc,
                    "provenance": "tree-sitter",
                }
                edges.append(edge)

        saved_class = class_name
        if node.kind() == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                class_name = _node_text(name_node, source)

        for child in _children(node):
            walk(child, class_name)

        class_name = saved_class

        if node.kind() == "method_declaration":
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


def _extract_java_method_name(node, source: bytes) -> str:
    """Extract qualified method name from a Java method_invocation node."""
    parts: list[str] = []
    for child in _children(node):
        if child.is_named():
            if child.kind() == "identifier":
                parts.append(_node_text(child, source))
    return ".".join(parts)


# ===================================================================
# Go gRPC detection
# ===================================================================

def _detect_go_grpc(
    source: bytes, tree, file_path: str, grpc_patterns: list[dict],
) -> list[dict]:
    """Detect gRPC usage in Go source files.

    Detects:
    - pb.NewXxxClient(...) → client
    - grpc.Dial(...) / grpc.DialContext(...) → client
    - grpc.NewServer(...) → server
    - .RegisterXxxServer(...) → server
    - pb.RegisterXxxServer(...) → server
    """
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node):
        nonlocal func_stack

        if node.kind() == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                fname = _node_text(name_node, source)
                qname = f"{file_path}::{fname}"
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        if node.kind() == "method_declaration":
            name_node = node.child_by_field_name("name")
            receiver = node.child_by_field_name("receiver")
            if name_node is not None:
                mname = _node_text(name_node, source)
                if receiver is not None:
                    for child in _children(receiver):
                        if child.is_named():
                            recv_text = _node_text(child, source).lstrip("*")
                            qname = f"{file_path}::{recv_text}::{mname}"
                            nid = _qname_to_nid(qname)
                            func_stack.append((nid, qname))
                            break
                else:
                    qname = f"{file_path}::{mname}"
                    nid = _qname_to_nid(qname)
                    func_stack.append((nid, qname))

        if node.kind() == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                callee_name = _extract_callee_name(func_node, source)
                pat = _match_pattern(callee_name, grpc_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"), pat.get("pattern", ""))

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    # Extract service/method name
                    target_text = callee_name
                    grpc_method = ""
                    if "Register" in callee_name:
                        # pb.RegisterXxxServer → service="Xxx"
                        parts = callee_name.split("Register")
                        if len(parts) > 1:
                            svc_part = parts[1]
                            if "Server" in svc_part:
                                svc_part = svc_part.replace("Server", "")
                            target_text = svc_part or callee_name

                    edge = {
                        "source": source_nid,
                        "target": "",
                        "kind": direction,
                        "target_text": target_text,
                        "grpc_method": grpc_method,
                        "source_loc": source_loc,
                        "provenance": "tree-sitter",
                    }
                    edges.append(edge)

        for child in _children(node):
            walk(child)

        if node.kind() in ("function_declaration", "method_declaration"):
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


# ===================================================================
# gRPC identifier extraction
# ===================================================================

def _extract_grpc_identifiers(
    callee_name: str, func_node, source: bytes,
) -> tuple[str, str]:
    """Extract service name and method name from a gRPC call expression.

    Returns:
        (target_text, grpc_method) tuple.
    """
    target_text = callee_name
    grpc_method = ""

    # Stub pattern: XxxServiceStub(...)
    if "Stub" in callee_name:
        # Extract service name from the stub class
        stub_part = callee_name.split(".")[-1]
        svc_name = stub_part.replace("Stub", "")
        target_text = svc_name or callee_name
        return target_text, grpc_method

    # add_XxxServicer_to_server pattern
    if "add_" in callee_name and "Servicer" in callee_name:
        import re
        m = re.search(r'add_(\w+)Servicer_to_server', callee_name)
        if m:
            target_text = m.group(1)
        return target_text, grpc_method

    return target_text, grpc_method


# ===================================================================
# GrpcDetector
# ===================================================================

class GrpcDetector:
    """检测 protobuf + gRPC stub 定义。

    检测 .proto 文件的 service/rpc 定义，以及各语言中的 gRPC stub 用法。
    """

    def __init__(self, patterns_path: str | None = None):
        """加载 patterns.yaml。

        Args:
            patterns_path: patterns.yaml 路径，默认使用内置 patterns.yaml。
        """
        self._data = _load_patterns(patterns_path)

    def detect(
        self, source: bytes, tree, file_path: str, language: str,
    ) -> list[dict]:
        """检测 gRPC 使用。

        Args:
            source: 源文件字节内容。
            tree: tree-sitter 解析树。
            file_path: 源文件路径。
            language: 编程语言（proto/python/java/go/typescript）。

        Returns:
            边列表，每条边包含::

                {
                    "source": str,
                    "target": str,
                    "kind": str,        # "grpc_service" 或 "grpc_client" 或 "grpc_server"
                    "target_text": str, # service 名称如 "MyService"
                    "grpc_method": str, # rpc 方法名如 "GetUser"
                    "source_loc": str,
                    "provenance": str,
                }
        """
        # Handle .proto files specially
        if language == "proto" or file_path.endswith(".proto"):
            return _detect_proto(source, tree, file_path)

        # Load gRPC patterns for the target language
        grpc_patterns = self._get_grpc_patterns(language)
        if not grpc_patterns:
            return []

        if language == "python":
            return _detect_python_grpc(source, tree, file_path, grpc_patterns)
        elif language in ("typescript", "tsx"):
            return _detect_typescript_grpc(source, tree, file_path, grpc_patterns)
        elif language == "java":
            return _detect_java_grpc(source, tree, file_path, grpc_patterns)
        elif language == "go":
            return _detect_go_grpc(source, tree, file_path, grpc_patterns)
        else:
            return []

    def _get_grpc_patterns(self, language: str) -> list[dict]:
        """Extract gRPC patterns for a specific language from the config."""
        languages = self._data.get("languages", {})
        lang_config = languages.get(language, {})
        return lang_config.get("grpc", [])
