"""WatchdogFileWatcher — 基于 watchdog 库的文件监听器实现.

watchdog 是可选依赖，不可用时通过 ImportError 提示安装.
"""

import os
import time
import threading
from typing import Callable, Optional

from .interface import FileChangeEvent, FileWatcher

# 默认忽略的目录名
DEFAULT_IGNORE_PATTERNS = [
    ".git", "node_modules", "__pycache__", ".tws", ".venv", "venv",
    "build", "dist", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]

# 最大重启次数
MAX_RESTART_ATTEMPTS = 3


def _import_watchdog():
    """延迟导入 watchdog 模块，如果不可用则抛出 ImportError."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        return FileSystemEventHandler, Observer
    except ImportError:
        raise ImportError(
            "watchdog is required for WatchdogFileWatcher. "
            "Install it with: pip install watchdog, "
            "or use PollingFileWatcher instead."
        )


class WatchdogFileWatcher(FileWatcher):
    """基于 watchdog 库的文件监听器.

    使用 OS 级文件系统事件，高效监文件的创建/修改/删除。
    watchdog 不可用时（ImportError），使用 PollingFileWatcher 作为替代。
    """

    def __init__(
        self,
        ignore_patterns: Optional[list[str]] = None,
        file_extensions: Optional[list[str]] = None,
    ):
        self.ignore_patterns = (
            ignore_patterns if ignore_patterns is not None
            else list(DEFAULT_IGNORE_PATTERNS)
        )
        self.file_extensions = file_extensions

        self._root_dir: str | None = None
        self._callback: Callable[[FileChangeEvent], None] | None = None
        self._observer = None        # watchdog.observers.Observer
        self._state_lock = threading.Lock()
        self._started = False
        self._restart_count = 0

    def start(self, root_dir: str, callback: Callable[[FileChangeEvent], None]) -> None:
        """启动 watchdog 监听.

        Args:
            root_dir: 监听的根目录路径
            callback: 文件变更时的回调函数

        幂等：已启动时静默返回.
        """
        with self._state_lock:
            if self._started:
                return
            self._started = True

        self._root_dir = root_dir
        self._callback = callback
        self._restart_count = 0

        FileSystemEventHandler, Observer = _import_watchdog()

        # 构建事件处理器类
        watcher = self  # 闭包引用

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                watcher._handle_event(event, "created")

            def on_modified(self, event):
                watcher._handle_event(event, "modified")

            def on_deleted(self, event):
                watcher._handle_event(event, "deleted")

            def on_moved(self, event):
                watcher._handle_move_event(event)

        self._observer = Observer()
        self._observer.schedule(Handler(), root_dir, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        """停止监听，阻塞等待线程退出.

        未启动时静默返回.
        """
        with self._state_lock:
            if not self._started:
                return

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
            except Exception:
                pass

        with self._state_lock:
            self._started = False
            self._observer = None

    def is_running(self) -> bool:
        """返回当前是否正在监听."""
        return self._started

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _handle_event(self, event, change_type: str) -> None:
        """处理 watchdog 文件事件."""
        # 过滤目录事件
        if hasattr(event, "is_directory") and event.is_directory:
            return

        # 获取文件路径
        filepath = getattr(event, "src_path", None)
        if filepath is None:
            return

        # 转换为字符串
        filepath = str(filepath)

        # 过滤忽略目录中的文件
        if self._is_under_ignored_dir(filepath):
            return

        # 过滤文件扩展名
        if not self._matches_extension(filepath):
            return

        self._emit(FileChangeEvent(path=filepath, change_type=change_type))  # type: ignore[arg-type]

    def _handle_move_event(self, event) -> None:
        """处理文件移动事件."""
        # 过滤目录事件
        if hasattr(event, "is_directory") and event.is_directory:
            return

        dest_path = getattr(event, "dest_path", None)
        src_path = getattr(event, "src_path", None)
        if dest_path is None:
            return

        dest_path = str(dest_path)
        src_path_str = str(src_path) if src_path else None

        # 过滤忽略目录中的文件
        if self._is_under_ignored_dir(dest_path):
            return

        # 过滤文件扩展名
        if not self._matches_extension(dest_path):
            return

        self._emit(FileChangeEvent(
            path=dest_path,
            change_type="moved",
            src_path=src_path_str,
        ))

    def _emit(self, event: FileChangeEvent) -> None:
        """触发回调.

        如果回调异常，尝试重启 observer（最多 3 次）.
        """
        if self._callback:
            try:
                self._callback(event)
            except Exception:
                self._attempt_restart()

    def _attempt_restart(self) -> None:
        """尝试重启 observer 线程（异常恢复）."""
        with self._state_lock:
            if self._restart_count >= MAX_RESTART_ATTEMPTS:
                return
            self._restart_count += 1

        # 指数退避：1s, 2s, 4s
        delay = 2 ** (self._restart_count - 1)
        time.sleep(delay)

        # 尝试重启
        try:
            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=3.0)

            FileSystemEventHandler, Observer = _import_watchdog()
            watcher = self

            class Handler(FileSystemEventHandler):
                def on_created(self, event):
                    watcher._handle_event(event, "created")

                def on_modified(self, event):
                    watcher._handle_event(event, "modified")

                def on_deleted(self, event):
                    watcher._handle_event(event, "deleted")

                def on_moved(self, event):
                    watcher._handle_move_event(event)

            self._observer = Observer()
            self._observer.schedule(
                Handler(), self._root_dir, recursive=True
            )
            self._observer.start()
        except Exception:
            pass

    def _is_under_ignored_dir(self, filepath: str) -> bool:
        """检查文件路径是否在忽略目录下."""
        if not self._root_dir:
            return False
        try:
            rel_path = os.path.relpath(filepath, self._root_dir)
            parts = os.path.normpath(rel_path).split(os.sep)
            for part in parts:
                if part in self.ignore_patterns:
                    return True
        except ValueError:
            pass
        return False

    def _matches_extension(self, filepath: str) -> bool:
        """检查文件扩展名是否匹配过滤."""
        if self.file_extensions is None:
            return True
        _, ext = os.path.splitext(filepath)
        return ext in self.file_extensions
