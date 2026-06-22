"""聚合函数注册表，用于 Executor 中执行 COUNT/SUM/AVG/MIN/MAX/COLLECT 等聚合操作。

聚合函数采用两参数签名 (accumulator, row_value) -> new_accumulator，
Executor 逐行调用，累积中间状态，在所有行遍历完毕后提取最终结果。

COUNT(*) 为特例：无参闭包，每次调用返回 1。
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

# 聚合函数签名: (accumulator, row_value) -> new_accumulator
# 或对于 count(*): () -> increment
AggregateFunc = Callable[..., Any]

# ---------------------------------------------------------------------------
# Private registry
# ---------------------------------------------------------------------------

_AGGREGATES: dict[str, AggregateFunc] = {}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_aggregate(name: str, func: AggregateFunc) -> None:
    """注册聚合函数。名称已存在时抛出 ValueError。"""
    name_upper = name.upper()
    if name_upper in _AGGREGATES:
        raise ValueError(f"Aggregate '{name}' already registered")
    _AGGREGATES[name_upper] = func


def get_aggregate(name: str) -> AggregateFunc:
    """获取聚合函数。不存在时抛出 KeyError。"""
    return _AGGREGATES[name.upper()]


def list_aggregates() -> list[str]:
    """列出所有已注册聚合函数名称。"""
    return sorted(_AGGREGATES.keys())


# ---------------------------------------------------------------------------
# Built-in aggregate function implementations
# ---------------------------------------------------------------------------


def _count_star() -> int:
    """COUNT(*) — 无参闭包，每调用一次代表一行。"""
    return 1


def _sum(acc: float, val: Any) -> float:
    """SUM — 逐行累加。"""
    return acc + val


def _avg(acc: tuple[float, int], val: Any) -> tuple[float, int]:
    """AVG — 累积 (sum, count) 对。

    Executor 在遍历完所有行后，将 sum / count 得到最终均值。
    count == 0 时返回 None。
    """
    return (acc[0] + val, acc[1] + 1)


def _min(acc: Any, val: Any) -> Any:
    """MIN — 逐行比较取最小值。"""
    if acc is None:
        return val
    return acc if acc < val else val


def _max(acc: Any, val: Any) -> Any:
    """MAX — 逐行比较取最大值。"""
    if acc is None:
        return val
    return acc if acc > val else val


def _collect(acc: list, val: Any) -> list:
    """COLLECT — 逐行收集到列表。"""
    acc.append(val)
    return acc


# ---------------------------------------------------------------------------
# Register built-in aggregates at module load time
# ---------------------------------------------------------------------------

register_aggregate("COUNT", _count_star)
register_aggregate("SUM", _sum)
register_aggregate("AVG", _avg)
register_aggregate("MIN", _min)
register_aggregate("MAX", _max)
register_aggregate("COLLECT", _collect)
