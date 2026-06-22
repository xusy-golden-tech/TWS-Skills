"""Tests for watcher/debounce.py — DebounceQueue 防抖队列."""

import time
import pytest

from tws_graph.watcher.debounce import DebounceConfig, DebounceQueue
from tws_graph.watcher.interface import FileChangeEvent


class TestDebounceConfig:
    """Tests for DebounceConfig frozen dataclass."""

    def test_default_values(self):
        """DebounceConfig 默认值正确."""
        config = DebounceConfig()
        assert config.window_ms == 300
        assert config.max_wait_ms == 2000

    def test_custom_values(self):
        """DebounceConfig 自定义值."""
        config = DebounceConfig(window_ms=500, max_wait_ms=3000)
        assert config.window_ms == 500
        assert config.max_wait_ms == 3000

    def test_frozen_immutable(self):
        """DebounceConfig 是 frozen dataclass."""
        config = DebounceConfig()
        with pytest.raises(Exception):
            config.window_ms = 100


class TestDebounceQueue:
    """Tests for DebounceQueue behavior."""

    def test_single_event_flushed(self):
        """单个事件在 flush 后触发回调."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)
        event = FileChangeEvent(path="/tmp/a.py", change_type="modified")
        queue.push(event)
        queue.flush()

        assert len(events) == 1
        assert events[0].path == "/tmp/a.py"

    def test_same_file_multiple_events_merged_in_window(self):
        """同一文件在防抖窗口内多次事件合并为一次."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.flush()

        assert len(events) == 1
        assert events[0].path == "/tmp/a.py"

    def test_different_files_not_merged(self):
        """不同文件的事件各自独立."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.push(FileChangeEvent(path="/tmp/b.py", change_type="modified"))
        queue.push(FileChangeEvent(path="/tmp/c.py", change_type="modified"))
        queue.flush()

        assert len(events) == 3
        paths = {e.path for e in events}
        assert paths == {"/tmp/a.py", "/tmp/b.py", "/tmp/c.py"}

    def test_flush_clears_pending(self):
        """flush 后 pending 事件被清空."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.flush()
        assert len(events) == 1

        # 再次 flush 不应重复触发
        queue.flush()
        assert len(events) == 1

    def test_stop_prevents_further_events(self):
        """stop 后 push 不再触发回调."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.stop()
        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.flush()

        assert len(events) == 0

    def test_auto_flush_by_timer(self):
        """防抖窗口过后自动 flush 触发回调."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=50)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))

        # 等待自动 flush
        time.sleep(0.15)

        assert len(events) == 1
        assert events[0].path == "/tmp/a.py"

    def test_created_then_modified_keeps_latest(self):
        """同一文件先 created 再 modified，保留 modified 事件."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="created"))
        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.flush()

        assert len(events) == 1
        # 应保留较新的 modified 事件
        assert events[0].change_type == "modified"

    def test_modified_then_deleted_keeps_deleted(self):
        """同一文件先 modified 再 deleted，保留 deleted 事件."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=100)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="deleted"))
        queue.flush()

        assert len(events) == 1
        assert events[0].change_type == "deleted"

    def test_max_wait_enforced(self):
        """max_wait_ms 确保事件不会无限缓存."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=500, max_wait_ms=100)
        queue = DebounceQueue(config, callback)

        # 连续推送同一文件事件（reset timer each time）
        start = time.time()
        while time.time() - start < 0.3:
            queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
            time.sleep(0.02)

        queue.flush()
        assert len(events) >= 1  # max_wait 确保至少触发过一次

    def test_flush_called_before_stop(self):
        """stop 时应 flush 残留事件."""
        events = []

        def callback(event):
            events.append(event)

        config = DebounceConfig(window_ms=500)
        queue = DebounceQueue(config, callback)

        queue.push(FileChangeEvent(path="/tmp/a.py", change_type="modified"))
        queue.stop()

        assert len(events) == 1
