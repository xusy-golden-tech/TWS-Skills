"""内置函数注册表，用于 Executor 中执行 toUpper/toLower/toString/coalesce 等标量函数。

函数签名: (*args) -> Any
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

# 标量函数签名: (*args) -> Any
ScalarFunc = Callable[..., Any]

# ---------------------------------------------------------------------------
# Private registry
# ---------------------------------------------------------------------------

_FUNCTIONS: dict[str, ScalarFunc] = {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_function(name: str, func: ScalarFunc) -> None:
    """注册标量函数。名称已存在时抛出 ValueError。"""
    name_lower = name.lower()
    if name_lower in _FUNCTIONS:
        raise ValueError(f"Function '{name}' already registered")
    _FUNCTIONS[name_lower] = func


def get_function(name: str) -> ScalarFunc:
    """获取标量函数。不存在时抛出 KeyError。"""
    return _FUNCTIONS[name.lower()]


def list_functions() -> list[str]:
    """列出所有已注册函数名称。"""
    return sorted(_FUNCTIONS.keys())


# ---------------------------------------------------------------------------
# Built-in function implementations
# ---------------------------------------------------------------------------


def _to_upper(s: Any) -> str | None:
    """Convert a string to uppercase."""
    return str(s).upper() if s is not None else None


def _to_lower(s: Any) -> str | None:
    """Convert a string to lowercase."""
    return str(s).lower() if s is not None else None


def _to_string(v: Any) -> str | None:
    """Convert a value to its string representation."""
    return str(v) if v is not None else None


def _coalesce(*args: Any) -> Any:
    """Return the first non-None argument, or None if all are None."""
    for a in args:
        if a is not None:
            return a
    return None


def _type(v: Any) -> str | None:
    """Return the type name of a value."""
    return type(v).__name__ if v is not None else None


# ---------------------------------------------------------------------------
# Register built-in functions at module load time
# ---------------------------------------------------------------------------

register_function("toUpper", _to_upper)
register_function("toLower", _to_lower)
register_function("toString", _to_string)
register_function("coalesce", _coalesce)
register_function("type", _type)
