"""文件监听模块 — 提供文件变更事件监听和防抖聚合能力。

包含：
- FileChangeEvent / FileWatcher ABC — 统一接口
- WatchdogFileWatcher — 基于 watchdog 库的高效实现
- PollingFileWatcher — 纯 Python os.walk 轮询 fallback
- DebounceQueue — 事件防抖聚合
"""

from .interface import FileChangeEvent, FileWatcher
from .debounce import DebounceConfig, DebounceQueue
from .polling_watcher import PollingFileWatcher

__all__ = [
    "FileChangeEvent",
    "FileWatcher",
    "DebounceConfig",
    "DebounceQueue",
    "PollingFileWatcher",
]
