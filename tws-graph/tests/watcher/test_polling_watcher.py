"""Tests for watcher/polling_watcher.py — PollingFileWatcher."""

import os
import time
import pytest

from tws_graph.watcher.polling_watcher import PollingFileWatcher
from tws_graph.watcher.interface import FileChangeEvent


def write_file(root, filename, content="hello"):
    """Helper: write a file and return its full path."""
    path = os.path.join(root, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestPollingFileWatcherConstruction:
    """Tests for PollingFileWatcher construction and lifecycle."""

    def test_construct_default(self):
        """使用默认参数构造 PollingFileWatcher."""
        watcher = PollingFileWatcher()
        assert watcher.polling_interval == 2.0
        assert watcher.ignore_patterns == []
        assert watcher.file_extensions is None
        assert watcher.is_running() is False

    def test_construct_custom_params(self):
        """使用自定义参数构造."""
        watcher = PollingFileWatcher(
            ignore_patterns=["*.log"],
            file_extensions=[".py", ".js"],
            polling_interval=0.5,
        )
        assert watcher.polling_interval == 0.5
        assert watcher.ignore_patterns == ["*.log"]
        assert watcher.file_extensions == [".py", ".js"]
        assert watcher.is_running() is False

    def test_is_running_initially_false(self):
        """初始状态 is_running 为 False."""
        watcher = PollingFileWatcher()
        assert watcher.is_running() is False

    def test_start_and_stop(self, tmp_path):
        """完整的 start/stop 生命周期."""
        watcher = PollingFileWatcher(polling_interval=0.5)
        assert watcher.is_running() is False

        events = []

        def callback(event):
            events.append(event)

        watcher.start(str(tmp_path), callback)
        assert watcher.is_running() is True

        watcher.stop()
        assert watcher.is_running() is False

    def test_stop_when_not_started_silent(self):
        """未启动时 stop 不抛异常."""
        watcher = PollingFileWatcher()
        watcher.stop()  # 不应抛异常
        assert watcher.is_running() is False


class TestPollingFileWatcherFileDetection:
    """Tests for file change detection via polling."""

    def test_detect_file_creation(self, tmp_path):
        """轮询检测到文件创建."""
        events = []

        def callback(event):
            events.append(event)

        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), callback)

        # 创建文件
        time.sleep(0.05)
        write_file(str(tmp_path), "new_file.py", "print(1)")

        # 等待轮询
        time.sleep(0.3)

        watcher.stop()

        created = [e for e in events if e.change_type == "created"]
        assert len(created) >= 1
        assert any("new_file.py" in e.path for e in created)

    def test_detect_file_modification(self, tmp_path):
        """轮询检测到文件修改."""
        filepath = write_file(str(tmp_path), "mod_file.py", "original")

        events = []

        def callback(event):
            events.append(event)

        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), callback)

        # 先让初始扫描完成（第一次扫描通常是 created）
        time.sleep(0.2)

        # 修改文件
        time.sleep(0.1)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("modified content")

        time.sleep(0.3)

        watcher.stop()

        modified = [e for e in events if e.change_type == "modified"]
        assert len(modified) >= 1
        assert any("mod_file.py" in e.path for e in modified)

    def test_detect_file_deletion(self, tmp_path):
        """轮询检测到文件删除."""
        filepath = write_file(str(tmp_path), "del_file.py", "to be deleted")

        events = []

        def callback(event):
            events.append(event)

        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), callback)

        # 等待初始扫描
        time.sleep(0.2)

        # 删除文件
        os.remove(filepath)
        time.sleep(0.3)

        watcher.stop()

        deleted = [e for e in events if e.change_type == "deleted"]
        assert len(deleted) >= 1
        assert any("del_file.py" in e.path for e in deleted)


class TestPollingFileWatcherIgnorePatterns:
    """Tests for ignore_patterns filtering."""

    def test_ignore_dir_filtered(self, tmp_path):
        """忽略目录中的文件不触发事件."""
        events = []

        def callback(event):
            events.append(event)

        # .git 是默认忽略目录，不需要额外配置 ignore_patterns
        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), callback)

        # 等待初始扫描完成
        time.sleep(0.15)

        # 创建 .git 目录及文件（应被忽略）
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        write_file(str(git_dir), "config", "data")

        # 创建普通文件
        py_file = write_file(str(tmp_path), "app.py", "code")

        time.sleep(0.3)

        watcher.stop()

        # .git/config 不应触发事件
        git_events = [e for e in events if ".git" in os.path.basename(e.path) or ".git" in e.path]
        assert len(git_events) == 0

        # app.py 应触发事件
        py_events = [e for e in events if "app.py" in e.path]
        assert len(py_events) >= 1

    def test_file_extensions_filter(self, tmp_path):
        """文件扩展名过滤正确."""
        events = []

        def callback(event):
            events.append(event)

        watcher = PollingFileWatcher(
            file_extensions=[".py"], polling_interval=0.1
        )
        watcher.start(str(tmp_path), callback)

        # 等待初始扫描完成
        time.sleep(0.15)

        # 创建各种扩展名的文件
        write_file(str(tmp_path), "app.py", "py code")
        write_file(str(tmp_path), "readme.md", "md text")
        write_file(str(tmp_path), "style.css", "css code")

        time.sleep(0.3)

        watcher.stop()

        # 只有 .py 文件触发事件
        all_paths = [e.path for e in events]
        for p in all_paths:
            assert p.endswith(".py"), f"Unexpected path: {p}"

        py_events = [e for e in events if "app.py" in e.path]
        assert len(py_events) >= 1

    def test_no_crash_on_empty_directory(self, tmp_path):
        """空目录启动不崩溃."""
        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), lambda e: None)
        time.sleep(0.15)
        watcher.stop()
        assert watcher.is_running() is False

    def test_no_crash_with_very_short_interval(self, tmp_path):
        """极短轮询间隔不崩溃."""
        watcher = PollingFileWatcher(polling_interval=0.01)
        events = []

        def callback(event):
            events.append(event)

        watcher.start(str(tmp_path), callback)
        time.sleep(0.1)
        watcher.stop()
        assert watcher.is_running() is False

    def test_subdirectory_detection(self, tmp_path):
        """子目录中的文件变更也被检测."""
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()

        events = []

        def callback(event):
            events.append(event)

        watcher = PollingFileWatcher(polling_interval=0.1)
        watcher.start(str(tmp_path), callback)

        time.sleep(0.15)
        write_file(str(sub_dir), "nested.py", "code")
        time.sleep(0.3)

        watcher.stop()

        created = [e for e in events if e.change_type == "created"]
        assert any("nested.py" in e.path for e in created)
