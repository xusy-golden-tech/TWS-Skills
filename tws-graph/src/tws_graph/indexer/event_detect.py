"""Lightweight event system detection — inline pattern matching for extractors.

Used by language extractors during call_expression handling to detect
event emit/listen patterns without re-parsing or separate pipeline passes.

Design: O(1) dict/suffix/contains matching against known event patterns,
analogous to grpc_detect.py and the HTTP detection in python_extractor.py.
"""

from __future__ import annotations

# ============================================================================
# Python event patterns
# ============================================================================

# Dict-based: exact callee name → (edge_kind, event_source)
_PYTHON_EVENT_EMIT: dict[str, str] = {
    "signal.send": "signal",
    "blinker.signal": "blinker",
    "dispatch": "custom",
    "fire_event": "custom",
    "send_event": "custom",
}

# Suffix-based: callee ending with these → emit
_PYTHON_EMIT_SUFFIXES: list[tuple[str, str]] = [
    (".emit", "custom"),
    (".send", "signal"),
    (".fire", "custom"),
    (".trigger", "custom"),
    (".publish", "custom"),
    (".dispatch", "custom"),
    (".broadcast", "custom"),
    (".notify", "custom"),
    ("EventBus.emit", "eventbus"),
    ("event_bus.emit", "eventbus"),
]

# Suffix-based: callee ending with these → listen
_PYTHON_LISTEN_SUFFIXES: list[tuple[str, str]] = [
    (".on", "custom"),
    (".connect", "signal"),
    (".subscribe", "custom"),
    (".add_listener", "custom"),
    (".addEventListener", "custom"),
    (".listen", "custom"),
    (".register", "custom"),
    (".attach", "custom"),
    (".bind", "custom"),
]

# Decorator-based: these decorators indicate event listeners
_PYTHON_LISTEN_DECORATORS: frozenset[str] = frozenset({
    "receiver",
    "signal_receiver",
    "event_handler",
    "on_event",
    "listen",
    "subscribe",
    "handler",
    "listener",
})


def detect_python_emit(callee_name: str) -> tuple[str, str] | None:
    """Detect if a Python call is an event emission.

    Returns (edge_kind="emits", event_name) or None.
    """
    # Exact match
    if callee_name in _PYTHON_EVENT_EMIT:
        return ("emits", callee_name)

    # Suffix match
    for suffix, source in _PYTHON_EMIT_SUFFIXES:
        if callee_name.endswith(suffix):
            return ("emits", callee_name)

    return None


def detect_python_listen(callee_name: str) -> tuple[str, str] | None:
    """Detect if a Python call registers an event listener.

    Returns (edge_kind="listens_on", event_name) or None.
    """
    for suffix, source in _PYTHON_LISTEN_SUFFIXES:
        if callee_name.endswith(suffix):
            return ("listens_on", callee_name)
    return None


def is_python_listen_decorator(decorator_name: str) -> bool:
    """Check if a Python decorator indicates an event listener."""
    name = decorator_name.split(".")[-1].strip("@")
    if name in _PYTHON_LISTEN_DECORATORS:
        return True
    # Check "on_<event>" pattern: e.g. @on_click, @on_message
    if name.startswith("on_"):
        return True
    return False


# ============================================================================
# TypeScript event patterns
# ============================================================================

_TS_EMIT_SUFFIXES: list[tuple[str, str]] = [
    (".emit(", "custom"),
    (".fire(", "custom"),
    (".trigger(", "custom"),
    (".publish(", "custom"),
    (".dispatch(", "custom"),
    (".broadcast(", "custom"),
    (".notify(", "custom"),
    (".dispatchEvent(", "dom"),
    (".fireEvent(", "dom"),
]

_TS_LISTEN_SUFFIXES: list[tuple[str, str]] = [
    (".on(", "custom"),
    (".once(", "custom"),
    (".addListener(", "custom"),
    (".subscribe(", "custom"),
    (".addEventListener(", "dom"),
    (".onEvent(", "custom"),
    (".listen(", "custom"),
    (".register(", "custom"),
]


def detect_ts_emit(callee: str) -> tuple[str, str] | None:
    """Detect if a TypeScript call is an event emission.

    Returns (edge_kind="emits", event_name) or None.
    Note: callee may include parentheses from the AST node text.
    """
    clean = callee.rstrip("(")
    for suffix, source in _TS_EMIT_SUFFIXES:
        if clean.endswith(suffix.rstrip("(")):
            return ("emits", clean)
    return None


def detect_ts_listen(callee: str) -> tuple[str, str] | None:
    """Detect if a TypeScript call registers an event listener.

    Returns (edge_kind="listens_on", event_name) or None.
    """
    clean = callee.rstrip("(")
    for suffix, source in _TS_LISTEN_SUFFIXES:
        if clean.endswith(suffix.rstrip("(")):
            return ("listens_on", clean)
    return None
