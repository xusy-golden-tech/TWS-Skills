"""Pass 抽象基类 + PipelineContext 数据类。

定义管线引擎的核心接口 —— Pass 之间通过 PipelineContext 传递数据，
不直接引用彼此。所有 Pass 实现必须继承 Pass ABC。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tws_graph.store.interface import Store


@dataclass
class PipelineContext:
    root_dir: str = ""
    files: list[str] = field(default_factory=list)
    force: bool = False
    store: Optional["Store"] = None
    parsed_results: dict = field(default_factory=dict)
    resolve_stats: dict = field(default_factory=lambda: {"resolved": 0, "unresolved": 0, "ambiguous": 0})
    errors: list = field(default_factory=list)
    pass_timings: dict = field(default_factory=dict)
    abort: bool = False
    metadata: dict = field(default_factory=dict)


class Pass(ABC):
    name: str = ""
    description: str = ""
    dependencies: list[str] = []

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext: ...

    def enabled(self, ctx: PipelineContext) -> bool:
        return True
