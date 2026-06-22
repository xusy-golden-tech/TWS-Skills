"""Tests for watcher/interface.py — FileChangeEvent + FileWatcher ABC."""

import pytest

from tws_graph.watcher.interface import FileChangeEvent, FileWatcher


class TestFileChangeEvent:
    """Tests for FileChangeEvent frozen dataclass."""

    def test_construct_created_event(self):
        """FileChangeEvent 可构造 created 类型事件."""
        event = FileChangeEvent(path="/tmp/test.py", change_type="created")
        assert event.path == "/tmp/test.py"
        assert event.change_type == "created"
        assert event.src_path is None

    def test_construct_modified_event(self):
        """FileChangeEvent 可构造 modified 类型事件."""
        event = FileChangeEvent(path="/tmp/test.py", change_type="modified")
        assert event.path == "/tmp/test.py"
        assert event.change_type == "modified"

    def test_construct_deleted_event(self):
        """FileChangeEvent 可构造 deleted 类型事件."""
        event = FileChangeEvent(path="/tmp/test.py", change_type="deleted")
        assert event.path == "/tmp/test.py"
        assert event.change_type == "deleted"

    def test_construct_moved_event_with_src_path(self):
        """FileChangeEvent 可构造 moved 类型事件，含 src_path."""
        event = FileChangeEvent(
            path="/tmp/new.py", change_type="moved", src_path="/tmp/old.py"
        )
        assert event.path == "/tmp/new.py"
        assert event.change_type == "moved"
        assert event.src_path == "/tmp/old.py"

    def test_src_path_defaults_to_none(self):
        """src_path 默认值为 None."""
        event = FileChangeEvent(path="/tmp/test.py", change_type="modified")
        assert event.src_path is None

    def test_frozen_dataclass_immutable(self):
        """FileChangeEvent 是 frozen dataclass，字段不可修改."""
        event = FileChangeEvent(path="/tmp/test.py", change_type="created")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            event.path = "/tmp/other.py"

    def test_equality_same_values(self):
        """相同字段值的两个事件应相等."""
        e1 = FileChangeEvent(path="/tmp/a.py", change_type="modified")
        e2 = FileChangeEvent(path="/tmp/a.py", change_type="modified")
        assert e1 == e2

    def test_hashable(self):
        """FileChangeEvent 应是可哈希的（可用于 set/dict）."""
        e1 = FileChangeEvent(path="/tmp/a.py", change_type="modified")
        e2 = FileChangeEvent(path="/tmp/a.py", change_type="modified")
        s = {e1, e2}
        assert len(s) == 1  # 相同事件去重


class TestFileWatcherABC:
    """Tests for FileWatcher abstract base class."""

    def test_cannot_instantiate_abc(self):
        """FileWatcher 是 ABC，不能直接实例化."""
        with pytest.raises(TypeError):
            FileWatcher()  # type: ignore[abstract]

    def test_concrete_subclass_requires_all_methods(self):
        """子类必须实现所有抽象方法才能实例化."""

        class IncompleteWatcher(FileWatcher):
            def start(self, root_dir, callback):
                pass
            # 缺少 stop 和 is_running

        with pytest.raises(TypeError):
            IncompleteWatcher()  # type: ignore[abstract]

    def test_concrete_subclass_can_instantiate(self):
        """实现所有抽象方法的子类可以实例化."""

        class FullWatcher(FileWatcher):
            def start(self, root_dir, callback):
                self._running = True

            def stop(self):
                self._running = False

            def is_running(self):
                return getattr(self, "_running", False)

        watcher = FullWatcher()
        assert watcher.is_running() is False

    def test_has_abstract_start(self):
        """FileWatcher 定义了抽象方法 start."""
        assert hasattr(FileWatcher, "start")
        assert getattr(FileWatcher.start, "__isabstractmethod__", False) is True

    def test_has_abstract_stop(self):
        """FileWatcher 定义了抽象方法 stop."""
        assert hasattr(FileWatcher, "stop")
        assert getattr(FileWatcher.stop, "__isabstractmethod__", False) is True

    def test_has_abstract_is_running(self):
        """FileWatcher 定义了抽象方法 is_running."""
        assert hasattr(FileWatcher, "is_running")
        assert getattr(FileWatcher.is_running, "__isabstractmethod__", False) is True
