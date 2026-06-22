"""Tests for watcher/watchdog_watcher.py — WatchdogFileWatcher.

These tests primarily test the construction and interface compliance.
Full file event testing relies on watchdog being installed (optional dependency).
"""

import os
import pytest

# Check if watchdog is actually available
try:
    import watchdog  # noqa: F401
    from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher

    SKIP_WATCHDOG = False
except ImportError:
    SKIP_WATCHDOG = True
    WatchdogFileWatcher = None  # type: ignore


@pytest.mark.skipif(
    SKIP_WATCHDOG,
    reason="watchdog is not installed (optional dependency)",
)
class TestWatchdogFileWatcherConstruction:
    """Tests for WatchdogFileWatcher construction and lifecycle."""

    def test_construct_default(self):
        """使用默认参数构造 WatchdogFileWatcher."""
        watcher = WatchdogFileWatcher()
        assert watcher.is_running() is False
        assert len(watcher.ignore_patterns) > 0  # 默认忽略目录

    def test_construct_custom_ignore(self):
        """自定义忽略目录."""
        watcher = WatchdogFileWatcher(ignore_patterns=[".custom_ignore"])
        assert ".custom_ignore" in watcher.ignore_patterns

    def test_construct_with_file_extensions(self):
        """指定文件扩展名过滤."""
        watcher = WatchdogFileWatcher(file_extensions=[".py", ".js"])
        assert watcher.file_extensions == [".py", ".js"]

    def test_is_running_initially_false(self):
        """初始状态 is_running 为 False."""
        watcher = WatchdogFileWatcher()
        assert watcher.is_running() is False

    def test_start_and_stop_basic(self, tmp_path):
        """基本的 start/stop 生命周期."""
        watcher = WatchdogFileWatcher()
        events = []

        def callback(event):
            events.append(event)

        watcher.start(str(tmp_path), callback)
        assert watcher.is_running() is True

        watcher.stop()
        # 注意：observer.join() 后 is_running 应变为 False
        assert watcher.is_running() is False

    def test_start_idempotent(self, tmp_path):
        """start 幂等：多次 start 不抛异常."""
        watcher = WatchdogFileWatcher()

        def callback(event):
            pass

        watcher.start(str(tmp_path), callback)
        # 第二次 start 应静默返回
        watcher.start(str(tmp_path), callback)
        assert watcher.is_running() is True

        watcher.stop()

    def test_stop_when_not_started_silent(self):
        """未启动时 stop 不抛异常."""
        watcher = WatchdogFileWatcher()
        watcher.stop()
        assert watcher.is_running() is False


@pytest.mark.skipif(
    SKIP_WATCHDOG,
    reason="watchdog is not installed (optional dependency)",
)
class TestWatchdogFileWatcherEvents:
    """Tests for file event handling."""

    def test_file_creation_detected(self, tmp_path):
        """watchdog 检测到文件创建."""
        import time

        events = []

        def callback(event):
            events.append(event)

        watcher = WatchdogFileWatcher()
        watcher.start(str(tmp_path), callback)

        time.sleep(0.1)
        test_file = tmp_path / "test.py"
        test_file.write_text("hello", encoding="utf-8")
        time.sleep(0.3)

        watcher.stop()

        created = [e for e in events if e.change_type == "created"]
        assert len(created) >= 1

    def test_file_modification_detected(self, tmp_path):
        """watchdog 检测到文件修改."""
        import time

        test_file = tmp_path / "mod.py"
        test_file.write_text("original", encoding="utf-8")

        events = []

        def callback(event):
            events.append(event)

        watcher = WatchdogFileWatcher()
        watcher.start(str(tmp_path), callback)

        time.sleep(0.1)
        test_file.write_text("modified", encoding="utf-8")
        time.sleep(0.3)

        watcher.stop()

        modified = [e for e in events if e.change_type == "modified"]
        assert len(modified) >= 1

    def test_file_deletion_detected(self, tmp_path):
        """watchdog 检测到文件删除."""
        import time

        test_file = tmp_path / "del.py"
        test_file.write_text("to be deleted", encoding="utf-8")

        events = []

        def callback(event):
            events.append(event)

        watcher = WatchdogFileWatcher()
        watcher.start(str(tmp_path), callback)

        time.sleep(0.1)
        test_file.unlink()
        time.sleep(0.3)

        watcher.stop()

        deleted = [e for e in events if e.change_type == "deleted"]
        assert len(deleted) >= 1

    def test_directory_events_ignored(self, tmp_path):
        """目录事件（创建/删除目录）不应触发文件回调."""
        import time

        events = []

        def callback(event):
            events.append(event)

        watcher = WatchdogFileWatcher()
        watcher.start(str(tmp_path), callback)

        time.sleep(0.1)
        new_dir = tmp_path / "new_subdir"
        new_dir.mkdir()
        time.sleep(0.3)

        watcher.stop()

        # 不应有以 new_subdir 为文件路径的事件（目录事件被过滤）
        dir_events = [e for e in events if "new_subdir" in os.path.basename(e.path)]
        assert len(dir_events) == 0

    def test_ignore_patterns_files_filtered(self, tmp_path):
        """忽略目录中的文件不触发回调."""
        import time

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git_file = git_dir / "config"
        git_file.write_text("data", encoding="utf-8")

        py_file = tmp_path / "app.py"
        py_file.write_text("code", encoding="utf-8")

        events = []

        def callback(event):
            events.append(event)

        watcher = WatchdogFileWatcher()
        watcher.start(str(tmp_path), callback)

        time.sleep(0.15)
        # 修改两个文件
        git_file.write_text("updated", encoding="utf-8")
        py_file.write_text("updated code", encoding="utf-8")
        time.sleep(0.3)

        watcher.stop()

        # .git/config 不应触发事件
        for e in events:
            assert ".git" not in e.path

        # app.py 应触发事件
        py_events = [e for e in events if "app.py" in e.path]
        assert len(py_events) >= 1
