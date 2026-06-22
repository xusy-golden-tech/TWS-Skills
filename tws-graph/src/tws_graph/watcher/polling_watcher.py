"""PollingFileWatcher — 纯 Python os.walk 轮询实现（无外部依赖）."""

import os
import threading
import time
from fnmatch import fnmatch
from typing import Callable, Optional

from .interface import FileChangeEvent, FileWatcher


# 默认忽略的目录名
DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".tws", ".venv", "venv",
    "build", "dist", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


class PollingFileWatcher(FileWatcher):
    """纯 Python 轮询文件监听器。

    通过定期 os.walk 扫描文件系统，比较 mtime 检测变更。
    不依赖任何第三方库（无需 watchdog）。
    """

    def __init__(
        self,
        ignore_patterns: Optional[list[str]] = None,
        file_extensions: Optional[list[str]] = None,
        polling_interval: float = 2.0,
    ):
        self.ignore_patterns = ignore_patterns or []
        self.file_extensions = file_extensions
        self.polling_interval = polling_interval

        self._root_dir: str | None = None
        self._callback: Callable[[FileChangeEvent], None] | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._started = False
        self._mtime_cache: dict[str, float] = {}  # path -> mtime

    def start(self, root_dir: str, callback: Callable[[FileChangeEvent], None]) -> None:
        """启动轮询监听.

        Args:
            root_dir: 监听的根目录路径
            callback: 文件变更时的回调函数
        """
        with self._state_lock:
            if self._started:
                return
            self._started = True

        self._root_dir = root_dir
        self._callback = callback
        self._stop_event.clear()

        # 初始扫描，建立 mtime 基线（不触发事件）
        self._initial_scan()

        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="PollingFileWatcher"
        )
        self._thread.start()

    def stop(self) -> None:
        """停止轮询监听."""
        with self._state_lock:
            if not self._started:
                return

        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        with self._state_lock:
            self._started = False
            self._thread = None

    def is_running(self) -> bool:
        """返回当前是否正在监听."""
        return self._started

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _initial_scan(self) -> None:
        """初始化扫描，建立 mtime 基线."""
        self._mtime_cache.clear()
        snapshot = self._scan_directory()
        self._mtime_cache = snapshot

    def _scan_directory(self) -> dict[str, float]:
        """扫描 root_dir 并返回 {path: mtime}."""
        result: dict[str, float] = {}
        if not self._root_dir or not os.path.isdir(self._root_dir):
            return result

        for dirpath, dirnames, filenames in os.walk(self._root_dir):
            # 过滤忽略目录
            dirnames[:] = [
                d for d in dirnames
                if not self._is_ignored_dir(d)
            ]

            for fname in filenames:
                full_path = os.path.join(dirpath, fname)

                # 检查是否在忽略目录中
                if self._is_under_ignored_dir(full_path):
                    continue

                # 检查文件扩展名
                if not self._matches_extension(fname):
                    continue

                try:
                    mtime = os.path.getmtime(full_path)
                    result[full_path] = mtime
                except OSError:
                    pass

        return result

    def _is_ignored_dir(self, dirname: str) -> bool:
        """检查目录名是否在忽略列表中."""
        # 精确匹配
        if dirname in DEFAULT_IGNORE_DIRS:
            return True
        # fnmatch 模式匹配
        for pattern in self.ignore_patterns:
            if fnmatch(dirname, pattern):
                return True
        return False

    def _is_under_ignored_dir(self, filepath: str) -> bool:
        """检查文件路径是否在忽略目录下."""
        if not self._root_dir:
            return False
        try:
            rel_path = os.path.relpath(filepath, self._root_dir)
            parts = os.path.normpath(rel_path).split(os.sep)
            for part in parts:
                if part in DEFAULT_IGNORE_DIRS:
                    return True
                for pattern in self.ignore_patterns:
                    if fnmatch(part, pattern):
                        return True
        except ValueError:
            pass
        return False

    def _matches_extension(self, filename: str) -> bool:
        """检查文件名是否匹配指定的扩展名过滤."""
        if self.file_extensions is None:
            return True
        _, ext = os.path.splitext(filename)
        return ext in self.file_extensions

    def _poll_loop(self) -> None:
        """主轮询循环."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                pass  # 单次轮询异常不影响后续

            # 分段等待，以便快速响应 stop
            self._stop_event.wait(self.polling_interval)

    def _poll_once(self) -> None:
        """执行一次轮询扫描并报告变更."""
        if not self._root_dir or not os.path.isdir(self._root_dir):
            return

        new_snapshot = self._scan_directory()
        old_snapshot = self._mtime_cache
        self._mtime_cache = new_snapshot

        old_paths = set(old_snapshot.keys())
        new_paths = set(new_snapshot.keys())

        # 检测创建
        for path in sorted(new_paths - old_paths):
            self._emit(FileChangeEvent(path=path, change_type="created"))

        # 检测删除
        for path in sorted(old_paths - new_paths):
            self._emit(FileChangeEvent(path=path, change_type="deleted"))

        # 检测修改
        common = old_paths & new_paths
        for path in sorted(common):
            if new_snapshot[path] != old_snapshot[path]:
                self._emit(FileChangeEvent(path=path, change_type="modified"))

    def _emit(self, event: FileChangeEvent) -> None:
        """触发回调."""
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                pass
