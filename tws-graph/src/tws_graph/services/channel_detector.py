"""ChannelDetector — 跨语言消息 channel 检测器。

检测 Kafka、RabbitMQ、Redis、Celery、Django Signals 等消息 channel 的
发布/订阅使用，按语言分策略实现。

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
# Channel direction mapping
# ---------------------------------------------------------------------------

_DIRECTION_MAP: dict[str, str] = {
    "produce": EdgeKind.EMITS,
    "consume": EdgeKind.LISTENS_ON,
    "both": "both",
    "emit": EdgeKind.EMITS,
    "consume": EdgeKind.LISTENS_ON,
}


def _map_direction(raw: str | None) -> str:
    if raw is None:
        return EdgeKind.EMITS
    return _DIRECTION_MAP.get(raw.lower(), EdgeKind.EMITS)


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

def _match_pattern(callee_name: str, patterns: list[dict]) -> dict | None:
    """Match a callee name against the pattern list.

    Returns the first matching pattern dict, or None.
    Uses case-insensitive component-level matching.
    """
    if not callee_name or not patterns:
        return None
    callee_lower = callee_name.lower()
    callee_parts = callee_lower.split(".")
    for pat in patterns:
        pattern_str = pat.get("pattern", "").lstrip("@")  # Strip @ prefix
        if not pattern_str:
            continue
        pattern_lower = pattern_str.lower()
        pattern_parts = pattern_lower.split(".")

        # Exact match (case-insensitive)
        if callee_lower == pattern_lower:
            return pat
        # Callee ends with .pattern (e.g. "redis.Redis.publish" ends with ".publish")
        if callee_lower.endswith("." + pattern_lower):
            return pat
        # Pattern is a suffix of callee
        if pattern_lower.endswith("(") and pattern_lower[:-1] in callee_lower:
            return pat
        # Last component match (e.g. "shared_task" matches "celery.shared_task")
        if pattern_parts[-1] == callee_parts[-1]:
            return pat
        # Any component of pattern appears in any component of callee
        # (e.g. "KafkaTemplate" matches "kafkaTemplate.send")
        if any(pp in callee_parts for pp in pattern_parts):
            return pat
    return None


def _extract_callee_name(func_node, source: bytes) -> str:
    """Extract the full dotted callee name from a function node in a call expression.

    Handles: simple identifier, attribute access (obj.method), nested attribute.
    """
    if func_node.kind() == "identifier":
        return _node_text(func_node, source)
    # attribute / member_expression: collect named children
    parts: list[str] = []
    for child in _children(func_node):
        if child.is_named():
            if child.kind() == "identifier":
                parts.append(_node_text(child, source))
            elif child.kind() in ("attribute", "member_expression",
                                  "call_expression", "call"):
                # Recurse into nested attribute access
                inner = _extract_callee_name(child, source)
                if inner:
                    parts.append(inner)
    # For Go selector_expression: left.right
    if func_node.kind() == "selector_expression":
        left = func_node.child_by_field_name("operand")
        right = func_node.child_by_field_name("field")
        if left and right:
            left_name = _extract_callee_name(left, source)
            right_name = _node_text(right, source)
            if left_name and right_name:
                return f"{left_name}.{right_name}"
    return ".".join(parts)


def _extract_first_string_arg(call_node, source: bytes) -> str | None:
    """Extract the first string literal argument from a call expression."""
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return None
    for child in _named_children(args_node):
        # Python: string / string_literal
        if child.kind() in ("string", "string_literal"):
            text = _node_text(child, source)
            return text.strip('"').strip("'")
        # TypeScript: string / template_string
        if child.kind() in ("string", "string_fragment", "template_string"):
            text = _node_text(child, source)
            # Template literal: `topic-name`
            if child.kind() == "template_string":
                return text.strip("`")
            return text.strip('"').strip("'")
        # Java: string_literal
        if child.kind() == "string_literal":
            text = _node_text(child, source)
            return text.strip('"')
        # Go: interpreted_string_literal / raw_string_literal
        if child.kind() in ("interpreted_string_literal", "raw_string_literal"):
            text = _node_text(child, source)
            return text.strip('"').strip("`")
    return None


def _extract_annotation_arg(annotation_node, source: bytes) -> str | None:
    """Extract the first string argument from an annotation/decorator."""
    args_node = None
    for child in _named_children(annotation_node):
        if child.kind() in ("arguments", "annotation_argument_list"):
            args_node = child
            break
    if args_node is None:
        return None
    for child in _named_children(args_node):
        if child.kind() in ("string", "string_literal", "interpreted_string_literal"):
            text = _node_text(child, source)
            return text.strip('"').strip("'")
    return None


# ===================================================================
# Python channel detection
# ===================================================================

def _detect_python_channels(
    source: bytes, tree, file_path: str, channel_patterns: list[dict],
) -> list[dict]:
    """Detect channel usage in Python source files.

    Walks the AST looking for:
    - Call expressions matching known channel patterns
    - Decorators (celery @task, django @receiver, etc.)
    """
    edges: list[dict] = []
    root = tree.root_node()
    # Track enclosing function qname → node_id
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        # Track function/method definitions
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

        # Check decorators
        if node.kind() == "decorator":
            decorator_name = _extract_callee_name_from_decorator(node, source)
            if decorator_name:
                pat = _match_pattern(decorator_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(decorator_name)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    # Try to extract channel name from decorator args
                    topic = _extract_decorator_string_arg(node, source)

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, decorator_name,
                    )
                    edges.extend(emits_edges)

        # Check call expressions
        if node.kind() == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                callee_name = _extract_callee_name(func_node, source)
                pat = _match_pattern(callee_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                    topic = _extract_first_string_arg(node, source)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, callee_name,
                    )
                    edges.extend(emits_edges)

        # Recurse
        for child in _children(node):
            walk(child, class_name)

        # Pop function
        if node.kind() in ("function_definition", "async_function_definition"):
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


def _extract_callee_name_from_decorator(decorator_node, source: bytes) -> str:
    """Extract the full name from a Python decorator node.

    e.g. @app.route('/path') → 'app.route'
         @receiver(post_save) → 'receiver'
         @celery.task → 'celery.task'
    """
    for child in _named_children(decorator_node):
        if child.kind() == "identifier":
            return _node_text(child, source)
        if child.kind() == "attribute":
            return _extract_callee_name(child, source)
        if child.kind() == "call":
            func_node = child.child_by_field_name("function")
            if func_node is not None:
                # @receiver(post_save): call's function is "receiver"
                result = _extract_callee_name(func_node, source)
                if result:
                    return result
            # Fallback: check children of the call node directly
            for c in _named_children(child):
                if c.kind() == "identifier":
                    return _node_text(c, source)
    return ""


def _extract_decorator_string_arg(decorator_node, source: bytes) -> str | None:
    """Extract string argument from a decorator like @receiver(post_save)."""
    for child in _named_children(decorator_node):
        if child.kind() == "call":
            return _extract_first_string_arg(child, source)
    return None


# ===================================================================
# TypeScript channel detection
# ===================================================================

def _detect_typescript_channels(
    source: bytes, tree, file_path: str, channel_patterns: list[dict],
) -> list[dict]:
    """Detect channel usage in TypeScript source files."""
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        # Track function/method definitions
        if node.kind() in ("function_declaration", "method_definition",
                           "arrow_function", "function_expression"):
            name = _get_ts_func_name(node, source, class_name)
            if name:
                qname = f"{file_path}::{name}"
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        # Decorators: @SubscribeMessage, @MessagePattern, etc.
        if node.kind() == "decorator":
            for child in _named_children(node):
                if child.kind() == "call_expression":
                    func_node = child.child_by_field_name("function")
                    if func_node is not None:
                        callee_name = _extract_callee_name(func_node, source)
                        pat = _match_pattern(callee_name, channel_patterns)
                        if pat:
                            direction = _map_direction(pat.get("direction"))
                            channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                            topic = _extract_first_string_arg(child, source)

                            source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                            source_loc = f"{file_path}:{node.start_position().row + 1}"

                            emits_edges = _make_channel_edges(
                                source_nid, direction, topic, channel_type,
                                source_loc, callee_name,
                            )
                            edges.extend(emits_edges)

        # Call expressions
        if node.kind() == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                callee_name = _extract_callee_name(func_node, source)
                pat = _match_pattern(callee_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                    topic = _extract_first_string_arg(node, source)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, callee_name,
                    )
                    edges.extend(emits_edges)

        # New expressions: new Kafka(...)
        if node.kind() == "new_expression":
            constructor = node.child_by_field_name("constructor")
            if constructor is not None:
                callee_name = _extract_callee_name(constructor, source)
                pat = _match_pattern(callee_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                    topic = _extract_first_string_arg(node, source)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, callee_name,
                    )
                    edges.extend(emits_edges)

        # Track classes for methods
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


def _get_ts_func_name(node, source: bytes, class_name: str) -> str | None:
    """Get the function name for a TypeScript function node."""
    if node.kind() == "method_definition":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            mname = _node_text(name_node, source)
            if class_name:
                return f"{class_name}::{mname}"
            return mname
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        if class_name:
            return f"{class_name}::{_node_text(name_node, source)}"
        return _node_text(name_node, source)
    return None


# ===================================================================
# Java channel detection
# ===================================================================

def _detect_java_channels(
    source: bytes, tree, file_path: str, channel_patterns: list[dict],
) -> list[dict]:
    """Detect channel usage in Java source files.

    Key patterns: annotations (@KafkaListener, @RabbitListener) and
    method invocations (KafkaTemplate.send, RabbitTemplate.convertAndSend).
    """
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node, class_name: str = ""):
        nonlocal func_stack

        # Track method declarations
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

        # Annotations: @KafkaListener, @RabbitListener, @JmsListener
        if node.kind() == "annotation":
            anno_name = ""
            for child in _named_children(node):
                if child.kind() == "identifier":
                    anno_name = _node_text(child, source)
                    break

            if anno_name:
                pat = _match_pattern(anno_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(anno_name)
                    # Try to extract topic from annotation args
                    topic = _extract_annotation_arg(node, source)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, anno_name,
                    )
                    edges.extend(emits_edges)

        # Method invocations: kafkaTemplate.send(...)
        if node.kind() == "method_invocation":
            # Extract method name chain
            callee_name = _extract_java_method_name(node, source)
            pat = _match_pattern(callee_name, channel_patterns)
            if pat:
                direction = _map_direction(pat.get("direction"))
                channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                topic = _extract_java_first_string_arg(node, source)

                source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                source_loc = f"{file_path}:{node.start_position().row + 1}"

                emits_edges = _make_channel_edges(
                    source_nid, direction, topic, channel_type,
                    source_loc, callee_name,
                )
                edges.extend(emits_edges)

        # Track class declarations
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
    """Extract qualified method name from a Java method_invocation node.

    Handles: simpleMethod(), obj.method(), Class.staticMethod().
    """
    parts: list[str] = []
    for child in _children(node):
        if child.is_named():
            if child.kind() == "identifier":
                parts.append(_node_text(child, source))
    return ".".join(parts)


def _extract_java_first_string_arg(call_node, source: bytes) -> str | None:
    """Extract the first string literal from a Java method_invocation arg list."""
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return None
    for child in _named_children(args_node):
        if child.kind() == "string_literal":
            text = _node_text(child, source)
            return text.strip('"')
    return None


# ===================================================================
# Go channel detection
# ===================================================================

def _detect_go_channels(
    source: bytes, tree, file_path: str, channel_patterns: list[dict],
) -> list[dict]:
    """Detect channel usage in Go source files."""
    edges: list[dict] = []
    root = tree.root_node()
    func_stack: list[tuple[str, str]] = []

    def _qname_to_nid(qname: str) -> str:
        return hash_id(qname, file_path)

    def walk(node):
        nonlocal func_stack

        # Track function declarations
        if node.kind() == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                fname = _node_text(name_node, source)
                qname = f"{file_path}::{fname}"
                nid = _qname_to_nid(qname)
                func_stack.append((nid, qname))

        # Method declarations
        if node.kind() == "method_declaration":
            name_node = node.child_by_field_name("name")
            receiver = node.child_by_field_name("receiver")
            if name_node is not None:
                mname = _node_text(name_node, source)
                if receiver is not None:
                    # Find receiver type name
                    for child in _children(receiver):
                        if child.is_named():
                            recv_text = _node_text(child, source)
                            # Strip pointer *
                            recv_text = recv_text.lstrip("*")
                            qname = f"{file_path}::{recv_text}::{mname}"
                            nid = _qname_to_nid(qname)
                            func_stack.append((nid, qname))
                            break
                else:
                    qname = f"{file_path}::{mname}"
                    nid = _qname_to_nid(qname)
                    func_stack.append((nid, qname))

        # Call expressions
        if node.kind() == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                callee_name = _extract_callee_name(func_node, source)
                pat = _match_pattern(callee_name, channel_patterns)
                if pat:
                    direction = _map_direction(pat.get("direction"))
                    channel_type = pat.get("frameworks", [""])[0] or _infer_channel_type(callee_name)
                    topic = _extract_first_string_arg(node, source)

                    source_nid = func_stack[-1][0] if func_stack else _qname_to_nid(f"{file_path}::__global__")
                    source_loc = f"{file_path}:{node.start_position().row + 1}"

                    emits_edges = _make_channel_edges(
                        source_nid, direction, topic, channel_type,
                        source_loc, callee_name,
                    )
                    edges.extend(emits_edges)

        for child in _children(node):
            walk(child)

        if node.kind() in ("function_declaration", "method_declaration"):
            if func_stack:
                func_stack.pop()

    walk(root)
    return edges


# ===================================================================
# Channel type inference
# ===================================================================

def _infer_channel_type(name: str) -> str:
    """Infer channel type from the matched pattern name."""
    name_lower = name.lower()
    if "kafka" in name_lower:
        return "kafka"
    if "rabbit" in name_lower or "amqp" in name_lower or "pika" in name_lower:
        return "rabbitmq"
    if "redis" in name_lower or "pubsub" in name_lower:
        return "redis"
    if "celery" in name_lower:
        return "celery"
    if "signal" in name_lower or "receiver" in name_lower:
        return "signal"
    if "jms" in name_lower:
        return "jms"
    if "nsq" in name_lower:
        return "nsq"
    return "unknown"


# ===================================================================
# Edge construction
# ===================================================================

def _make_channel_edges(
    source_nid: str,
    direction: str,
    topic: str | None,
    channel_type: str,
    source_loc: str,
    callee_name: str,
) -> list[dict]:
    """Create channel edge dicts from detection results.

    If direction is "both", creates two edges: one EMITS and one LISTENS_ON.
    """
    edges: list[dict] = []

    if direction == "both":
        edges.append({
            "source": source_nid,
            "target": "",
            "kind": EdgeKind.EMITS,
            "target_text": topic or callee_name,
            "channel_type": channel_type,
            "source_loc": source_loc,
            "provenance": "tree-sitter",
        })
        edges.append({
            "source": source_nid,
            "target": "",
            "kind": EdgeKind.LISTENS_ON,
            "target_text": topic or callee_name,
            "channel_type": channel_type,
            "source_loc": source_loc,
            "provenance": "tree-sitter",
        })
    else:
        edges.append({
            "source": source_nid,
            "target": "",
            "kind": direction,
            "target_text": topic or callee_name,
            "channel_type": channel_type,
            "source_loc": source_loc,
            "provenance": "tree-sitter",
        })
    return edges


# ===================================================================
# ChannelDetector
# ===================================================================

class ChannelDetector:
    """检测消息 channel 发布/订阅（Kafka、RabbitMQ、Redis、Celery 等）。

    按语言分策略实现，不强行统一抽象。
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
        """检测源文件中的 channel 使用。

        Args:
            source: 源文件字节内容。
            tree: tree-sitter 解析树。
            file_path: 源文件路径。
            language: 编程语言（python/typescript/java/go）。

        Returns:
            边列表，每条边包含::

                {
                    "source": str,        # 函数/方法 node_id
                    "target": str,        # ""
                    "kind": str,          # "emits" 或 "listens_on"
                    "target_text": str,   # channel/topic/queue 名称
                    "channel_type": str,  # kafka/rabbitmq/redis/celery/signal
                    "source_loc": str,
                    "provenance": str,
                }
        """
        # Load channel patterns for the target language
        channel_patterns = self._get_channel_patterns(language)
        if not channel_patterns:
            return []

        # Dispatch by language
        if language == "python":
            return _detect_python_channels(source, tree, file_path, channel_patterns)
        elif language in ("typescript", "tsx"):
            return _detect_typescript_channels(source, tree, file_path, channel_patterns)
        elif language == "java":
            return _detect_java_channels(source, tree, file_path, channel_patterns)
        elif language == "go":
            return _detect_go_channels(source, tree, file_path, channel_patterns)
        else:
            return []

    def _get_channel_patterns(self, language: str) -> list[dict]:
        """Extract channel patterns for a specific language from the config."""
        languages = self._data.get("languages", {})
        lang_config = languages.get(language, {})
        return lang_config.get("channels", [])
