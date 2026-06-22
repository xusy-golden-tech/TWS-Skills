"""PipelineEngine — 多 Pass 索引管线引擎。

职责：
- 管理 Pass 实例的注册（register_pass）
- 按依赖拓扑排序构建执行管线（build_pipeline）
- 构建增量管线（build_incremental_pipeline），只包含支持增量模式的 Pass
- 顺序执行所有 Pass，管理错误恢复（Continue-on-Error）
- 记录每个 Pass 的耗时到 ctx.pass_timings

设计原则：
- Pass 之间不直接耦合，只通过 PipelineContext 传递数据
- Pass 的执行顺序由 build_pipeline 自动拓扑排序确定
- 新 Pass 只需 register_pass 即可加入管线
- 引擎不关心 Pass 内部实现，只调用 run(ctx) 方法
"""

from __future__ import annotations

import time
import logging
from collections import deque
from typing import Callable, Optional

from .pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)

# Type alias for progress callback: callback(pass_name, ctx)
ProgressCallback = Callable[[str, PipelineContext], None]


class PipelineEngine:
    """多 Pass 索引管线引擎。

    用法示例::

        engine = PipelineEngine(store=my_store)
        engine.register_pass(ParseExtractPass())
        engine.register_pass(NodeInsertPass())
        ctx = engine.execute(files=["a.py", "b.py"], root_dir="/project")

    或使用增量模式::

        engine = PipelineEngine(store=my_store)
        engine.register_pass(ParseExtractPass())
        engine.register_pass(NodeInsertPass())
        ctx = engine.execute_incremental(
            changed_files=["a.py"], root_dir="/project"
        )
    """

    def __init__(self, store: object = None):
        """初始化管线引擎。

        Args:
            store: Store 实例（数据库操作接口），可选。
                   传入后会在 PipelineContext 中传递给每个 Pass。
        """
        self.store = store
        self._passes: list[Pass] = []
        self._pass_names: set[str] = set()

    # ── 注册 ──────────────────────────────────────────────────────────────

    def register_pass(self, pass_: Pass) -> None:
        """注册一个 Pass 实例。

        所有 Pass 必须在 build_pipeline() / execute() 之前注册。
        不允许注册同名的两个 Pass 实例。

        Args:
            pass_: Pass 实例。

        Raises:
            ValueError: 如果 pass_.name 为空或已注册。
        """
        name = pass_.name
        if not name:
            raise ValueError(f"Pass 实例 {pass_!r} 的 name 不能为空")
        if name in self._pass_names:
            raise ValueError(
                f"Pass '{name}' 已经注册。"
                f" 已注册的 Pass: {sorted(self._pass_names)}"
            )
        self._passes.append(pass_)
        self._pass_names.add(name)

    # ── 构建 ──────────────────────────────────────────────────────────────

    def build_pipeline(self) -> list[Pass]:
        """按依赖拓扑排序，构建完整管线。

        对所有已注册的 Pass 实例进行拓扑排序（Kahn 算法），
        确保依赖的 Pass 排在前面。

        Returns:
            按依赖排序的 Pass 实例列表。

        Raises:
            ValueError: 如果检测到循环依赖，或某个 Pass 依赖了未注册的名称。
        """
        return self._topological_sort(self._passes)

    def build_incremental_pipeline(self) -> list[Pass]:
        """构建增量管线。

        只包含 supports_incremental 属性为 True 的 Pass。
        默认情况下所有 Pass 的 supports_incremental 为 True。

        Returns:
            按依赖排序的、支持增量模式的 Pass 实例列表。

        Raises:
            ValueError: 如果检测到循环依赖。
        """
        incremental_passes = [
            p for p in self._passes
            if getattr(p, 'supports_incremental', True)
        ]
        return self._topological_sort(incremental_passes)

    # ── 拓扑排序 ──────────────────────────────────────────────────────────

    def _topological_sort(self, passes: list[Pass]) -> list[Pass]:
        """使用 Kahn 算法对 Pass 进行拓扑排序。

        Args:
            passes: 待排序的 Pass 实例列表。

        Returns:
            按依赖排序的 Pass 实例列表（依赖项在前）。

        Raises:
            ValueError: 如果检测到循环依赖，或依赖了未注册的 Pass。
        """
        if not passes:
            return []

        # 构建名称到实例的映射
        name_to_pass: dict[str, Pass] = {}
        for p in passes:
            if p.name in name_to_pass:
                raise ValueError(
                    f"管线中存在同名 Pass: '{p.name}'"
                )
            name_to_pass[p.name] = p

        # 构建入度表和邻接表
        in_degree: dict[str, int] = {p.name: 0 for p in passes}
        adjacency: dict[str, list[str]] = {p.name: [] for p in passes}

        for p in passes:
            for dep_name in p.dependencies:
                if dep_name not in name_to_pass:
                    raise ValueError(
                        f"Pass '{p.name}' 依赖了未注册的 Pass '{dep_name}'。"
                        f" 当前管线中的 Pass: {sorted(name_to_pass.keys())}"
                    )
                # dep_name 指向 p.name：dep_name 必须先执行
                adjacency[dep_name].append(p.name)
                in_degree[p.name] += 1

        # Kahn BFS
        queue: deque[str] = deque(
            name for name, deg in in_degree.items() if deg == 0
        )
        sorted_names: list[str] = []

        while queue:
            current = queue.popleft()
            sorted_names.append(current)
            for dependent in adjacency[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # 循环依赖检测
        if len(sorted_names) != len(passes):
            remaining = {name for name in name_to_pass if name not in sorted_names}
            raise ValueError(
                f"检测到循环依赖，涉及 Pass: {sorted(remaining)}。"
                f" 请检查这些 Pass 的 dependencies 配置。"
            )

        return [name_to_pass[name] for name in sorted_names]

    # ── 执行 ──────────────────────────────────────────────────────────────

    def execute(
        self,
        files: list[str],
        root_dir: str = ".",
        force: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> PipelineContext:
        """执行完整管线。

        1. 调用 build_pipeline() 构建 Pass 执行列表
        2. 创建 PipelineContext，按序执行每个 Pass
        3. Continue-on-Error：单个 Pass 失败不中断管线
        4. ctx.abort = True 时停止后续 Pass
        5. Pass.enabled(ctx) = False 时跳过该 Pass

        Args:
            files: 要处理的文件路径列表。
            root_dir: 项目根目录。
            force: 是否强制全量处理。
            progress_callback: 可选进度回调，签名 callback(pass_name, ctx)。

        Returns:
            PipelineContext，包含所有 Pass 的产出、错误和统计信息。
        """
        pipeline = self.build_pipeline()
        return self._run_pipeline(
            pipeline=pipeline,
            files=files,
            root_dir=root_dir,
            force=force,
            progress_callback=progress_callback,
        )

    def execute_incremental(
        self,
        changed_files: list[str],
        root_dir: str = ".",
    ) -> PipelineContext:
        """执行增量管线。

        使用 build_incremental_pipeline() 获取 Pass 列表，
        始终以 force=False 执行。

        Args:
            changed_files: 变更的文件路径列表。
            root_dir: 项目根目录。

        Returns:
            PipelineContext。
        """
        pipeline = self.build_incremental_pipeline()
        return self._run_pipeline(
            pipeline=pipeline,
            files=changed_files,
            root_dir=root_dir,
            force=False,
            progress_callback=None,
        )

    def _run_pipeline(
        self,
        pipeline: list[Pass],
        files: list[str],
        root_dir: str,
        force: bool,
        progress_callback: Optional[ProgressCallback],
    ) -> PipelineContext:
        """按序执行 Pass 列表，管理错误恢复和生命周期。

        Args:
            pipeline: 要执行的 Pass 列表（已排序）。
            files: 要处理的文件路径列表。
            root_dir: 项目根目录。
            force: 是否强制全量处理。
            progress_callback: 可选进度回调。

        Returns:
            PipelineContext，包含执行结果。
        """
        t0 = time.time()

        ctx = PipelineContext(
            files=list(files),
            store=self.store,
            root_dir=root_dir,
            force=force,
        )

        total = len(pipeline)

        for i, pas in enumerate(pipeline):
            # 检查 abort
            if ctx.abort:
                logger.warning(
                    f"管线已终止 (abort=True)，跳过剩余 {total - i} 个 Pass"
                )
                break

            # 检查 enabled
            if not pas.enabled(ctx):
                logger.info(f"[{i + 1}/{total}] 跳过: {pas.name} (disabled)")
                continue

            # 执行 Pass
            t_pass = time.time()
            try:
                logger.info(f"[{i + 1}/{total}] 执行: {pas.name}")
                ctx = pas.run(ctx)

                elapsed_ms = int((time.time() - t_pass) * 1000)
                ctx.pass_timings[pas.name] = elapsed_ms
                logger.info(f"[{i + 1}/{total}] 完成: {pas.name} ({elapsed_ms}ms)")

                # 成功完成 → 调用 progress_callback
                if progress_callback is not None:
                    progress_callback(pas.name, ctx)

            except Exception as exc:
                elapsed_ms = int((time.time() - t_pass) * 1000)
                ctx.pass_timings[pas.name] = elapsed_ms

                # Continue-on-Error: 记录错误，继续执行后续 Pass
                ctx.errors.append({
                    "pass": pas.name,
                    "error": str(exc),
                    "severity": "error",
                })
                logger.error(
                    f"[{i + 1}/{total}] 失败: {pas.name} ({elapsed_ms}ms): {exc}",
                    exc_info=True,
                )
                # 不调用 progress_callback（Pass 失败不算完成）
                # 不中断管线（Continue-on-Error 策略）

        ctx.duration_ms = int((time.time() - t0) * 1000)
        return ctx

    # ── 查询 ──────────────────────────────────────────────────────────────

    @property
    def registered_count(self) -> int:
        """已注册的 Pass 数量。"""
        return len(self._passes)

    @property
    def registered_names(self) -> list[str]:
        """已注册的 Pass 名称列表（注册顺序）。"""
        return [p.name for p in self._passes]
