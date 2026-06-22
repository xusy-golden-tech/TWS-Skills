"""防抖队列 — 聚合短时间内的文件变更事件."""

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .interface import FileChangeEvent


@dataclass(frozen=True)
class DebounceConfig:
    """防抖配置."""
    window_ms: int = 300      # 防抖窗口
    max_wait_ms: int = 2000   # 最大等待时间（避免事件无限缓存）


class DebounceQueue:
    """防抖队列：聚合短时间内的文件变更事件。

    在 debounce 窗口内，同一文件的多次变更合并为一次。
    max_wait_ms 确保事件不会无限缓存。
    """

    def __init__(
        self,
        config: DebounceConfig,
        callback: Callable[[FileChangeEvent], None],
    ):
        self._config = config
        self._callback = callback
        self._buffer: dict[str, FileChangeEvent] = {}  # path -> pending event
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._stopped = False
        self._first_event_time: float | None = None  # 首次事件到达时间（monotonic）

    def push(self, event: FileChangeEvent) -> None:
        """推入一个文件变更事件。

        同一文件的多次变更在防抖窗口内合并为一次。
        合并规则：deleted > modified > created（deleted 优先级最高）。
        """
        if self._stopped:
            return

        with self._lock:
            key = event.path

            # 记录首次事件到达时间（用于 max_wait 判断）
            if self._first_event_time is None:
                self._first_event_time = time.monotonic()

            # 合并规则：同一文件保留最新/最高优先级事件
            if key in self._buffer:
                existing = self._buffer[key]
                # deleted 优先级最高，不覆盖
                if existing.change_type == "deleted":
                    pass  # 保留 deleted
                else:
                    self._buffer[key] = event
            else:
                self._buffer[key] = event

            # 重置防抖定时器
            self._schedule_flush()

    def flush(self) -> None:
        """立即清空队列，触发所有 pending 事件的回调."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            pending = list(self._buffer.values())
            self._buffer.clear()
            self._first_event_time = None

        # 在锁外触发回调，避免死锁
        for event in pending:
            self._callback(event)

    def stop(self) -> None:
        """停止防抖队列，flush 残留事件后不再接收新事件."""
        self.flush()
        self._stopped = True

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _schedule_flush(self) -> None:
        """安排定时器在窗口过期后自动 flush.

        同时检查 max_wait_ms：如果距首次事件已超过 max_wait_ms，
        立即执行 flush。
        """
        # 取消已有定时器
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        # 检查 max_wait 是否已过期
        if self._first_event_time is not None:
            elapsed_ms = (time.monotonic() - self._first_event_time) * 1000
            if elapsed_ms >= self._config.max_wait_ms:
                # max_wait 到期，立即 flush（在锁内调用 self.flush 可能死锁？
                # 这里在 push 的 with self._lock 上下文中，需要在锁外触发）
                # 使用 Timer(0) 来在下一个事件循环中执行 flush
                self._timer = threading.Timer(0, self._on_timer_flush)
                self._timer.start()
                return

        # 设置窗口定时器
        delay_s = self._config.window_ms / 1000.0
        self._timer = threading.Timer(delay_s, self._on_timer_flush)
        self._timer.start()

    def _on_timer_flush(self) -> None:
        """定时器回调：自动 flush."""
        self.flush()
