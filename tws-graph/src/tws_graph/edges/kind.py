"""EdgeKind enum — 统一管理所有边类型，继承 str 保持向后兼容。

序列化契约:
    代码层 EdgeKind.CALLS  →  .value  →  存储层 TEXT "calls"
    存储层 TEXT "calls"    →  EdgeKind(s)  →  代码层 EdgeKind.CALLS

设计依据: 见 design-p6-edge-inval.md section "EdgeKind Enum"
"""

from __future__ import annotations

from enum import Enum


class EdgeKind(str, Enum):
    """边类型枚举。

    继承 str 使 enum 成员可在需要 str 的场景下直接使用，
    同时保留枚举的语义约束。

    使用示例:
        kind = EdgeKind.CALLS
        assert kind == "calls"          # str 继承
        assert EdgeKind("imports") is EdgeKind.IMPORTS  # 数据库读出
        kind = EdgeKind.from_str("references")  # 显式转换
    """

    # -- 现有 6 种（tree-sitter 提取层）--
    CALLS = "calls"
    IMPORTS = "imports"
    REFERENCES = "references"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"

    # -- P7 数据流分析 --
    DATA_FLOWS = "data_flows"
    READS = "reads"
    WRITES = "writes"
    THROWS = "throws"

    # -- P7 环境/事件 --
    ENV_ACCESSES = "env_accesses"
    EMITS = "emits"
    LISTENS_ON = "listens_on"

    # -- P10 跨服务 --
    HTTP_CALLS = "http_calls"

    # -- P4/P8 相似度 --
    SIMILAR_TO = "similar_to"

    # -- P9 分析 --
    TEST_EDGE = "test_edge"
    CONFIG_LINK = "config_link"

    def __str__(self) -> str:
        """返回枚举值字符串，而非 'EdgeKind.CALLS'.

        str(EdgeKind.CALLS) == "calls"  — 设计书要求。
        由于 Python 3.10 的 str(Enum) 混合类不自动代理 __str__,
        需要显式覆盖。
        """
        return self.value

    @classmethod
    def from_str(cls, value: str) -> EdgeKind:
        """从存储层字符串反向映射。兼容旧数据。

        Args:
            value: 存储层字符串，如 "calls", "imports"。

        Returns:
            EdgeKind 枚举成员。

        Raises:
            ValueError: 当 value 无法匹配任何已知边类型时。
        """
        return cls(value)
