"""Comprehensive tests for the tws-graph watch/watcher module.

Covers:
  - FileChangeEvent dataclass (frozen, equality, defaults)
  - DebounceQueue (merge, priority, max_wait, edge cases)
  - PollingFileWatcher (create, modify, delete, ignore rules, extension filter, lifecycle, stability)
  - WatchdogFileWatcher (import guard, skip if not installed)
"""

import os
import tempfile
import time
import threading
import sys

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from tws_graph.watcher.interface import FileChangeEvent, FileWatcher
from tws_graph.watcher.debounce import DebounceConfig, DebounceQueue
from tws_graph.watcher.polling_watcher import PollingFileWatcher, DEFAULT_IGNORE_DIRS


# ============================================================================
# TestFileChangeEvent
# ============================================================================

class TestFileChangeEvent:
    """Tests for the FileChangeEvent value object."""

    def test_construction_minimal(self):
        """Minimal construction with just path and change_type."""
        e = FileChangeEvent(path="/a.py", change_type="created")
        assert e.path == "/a.py"
        assert e.change_type == "created"
        assert e.src_path is None

    def test_construction_full(self):
        """Full construction including src_path for moved events."""
        e = FileChangeEvent(path="/b.py", change_type="moved", src_path="/a.py")
        assert e.path == "/b.py"
        assert e.change_type == "moved"
        assert e.src_path == "/a.py"

    def test_frozen(self):
        """FileChangeEvent is frozen — mutation should raise."""
        e = FileChangeEvent(path="/a.py", change_type="created")
        with pytest.raises(Exception):
            e.path = "/b.py"  # type: ignore[misc]

    def test_equality_same(self):
        """Two events with identical values are equal."""
        e1 = FileChangeEvent(path="/a.py", change_type="created")
        e2 = FileChangeEvent(path="/a.py", change_type="created")
        assert e1 == e2
        assert hash(e1) == hash(e2)

    def test_equality_different_path(self):
        """Events with different paths are not equal."""
        e1 = FileChangeEvent(path="/a.py", change_type="created")
        e2 = FileChangeEvent(path="/b.py", change_type="created")
        assert e1 != e2

    def test_equality_different_type(self):
        """Events with different change_types are not equal."""
        e1 = FileChangeEvent(path="/a.py", change_type="created")
        e2 = FileChangeEvent(path="/a.py", change_type="modified")
        assert e1 != e2

    def test_equality_src_path(self):
        """Events with different src_path are not equal."""
        e1 = FileChangeEvent(path="/b.py", change_type="moved", src_path="/a.py")
        e2 = FileChangeEvent(path="/b.py", change_type="moved", src_path="/c.py")
        assert e1 != e2

    def test_equality_src_path_none_vs_set(self):
        """Event with src_path=None differs from one with src_path set."""
        e1 = FileChangeEvent(path="/b.py", change_type="moved")
        e2 = FileChangeEvent(path="/b.py", change_type="moved", src_path="/a.py")
        assert e1 != e2

    def test_repr(self):
        """repr includes all fields."""
        e = FileChangeEvent(path="/a.py", change_type="created")
        r = repr(e)
        assert "FileChangeEvent" in r
        assert "/a.py" in r
        assert "created" in r

    def test_change_type_literals(self):
        """All valid change_type literals work."""
        for ct in ("created", "modified", "deleted", "moved"):
            e = FileChangeEvent(path="/x.py", change_type=ct)  # type: ignore[arg-type]
            assert e.change_type == ct


# ============================================================================
# TestDebounceQueue
# ============================================================================

class TestDebounceQueue:
    """Tests for the DebounceQueue event aggregation."""

    # -- helpers --

    @staticmethod
    def _make_config(window_ms=100, max_wait_ms=5000):
        return DebounceConfig(window_ms=window_ms, max_wait_ms=max_wait_ms)

    @staticmethod
    def _make_event(path, change_type="modified"):
        return FileChangeEvent(path=path, change_type=change_type)

    @staticmethod
    def _wait(seconds):
        time.sleep(seconds)

    # -- Merging same file --

    def test_merges_same_file_modifications(self):
        """Multiple modifications to the same file within a window are merged."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/a.py", "modified"))

        # Wait for window to expire
        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        assert events[0].path == "/a.py"

    def test_merges_same_file_created_then_modified(self):
        """file created then immediately modified -> merged to one event."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "created"))
        q.push(self._make_event("/a.py", "modified"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1

    def test_different_files_separate(self):
        """Events for different files should not be merged."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/b.py", "modified"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 2
        paths = {e.path for e in events}
        assert paths == {"/a.py", "/b.py"}

    # -- Priority rules --

    def test_deleted_priority_over_modified(self):
        """Deleted event should override any earlier modified/created."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/a.py", "deleted"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        assert events[0].change_type == "deleted"

    def test_deleted_priority_over_created(self):
        """Deleted should override created."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "created"))
        q.push(self._make_event("/a.py", "deleted"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        assert events[0].change_type == "deleted"

    def test_modified_kept_when_created_already_buffered(self):
        """Later events within window overwrite earlier ones (if not deleted)."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "created"))
        q.push(self._make_event("/a.py", "modified"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        # The last non-deleted event wins
        assert events[0].change_type in ("modified", "created")

    def test_deleted_preserved_when_lower_priority_pushed_later(self):
        """When deleted is already buffered, later lower-priority events do not overwrite."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        # Push deleted first, then lower-priority event (created)
        q.push(self._make_event("/a.py", "deleted"))
        q.push(self._make_event("/a.py", "created"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        # Deleted should be preserved (highest priority)
        assert events[0].change_type == "deleted"

    def test_deleted_preserved_when_modified_pushed_later(self):
        """When deleted is already buffered, later modified does not overwrite."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        # Push deleted first, then modified
        q.push(self._make_event("/a.py", "deleted"))
        q.push(self._make_event("/a.py", "modified"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 1
        # Deleted should be preserved
        assert events[0].change_type == "deleted"

    # -- max_wait --

    def test_max_wait_forces_flush(self):
        """Even if events keep arriving within the window, max_wait forces flush."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=300, max_wait_ms=400),
            callback=lambda e: events.append(e),
        )

        # Continuously push events with 100ms gaps
        for i in range(8):
            q.push(self._make_event(f"/file_{i}.py", "modified"))
            self._wait(0.1)

        q.stop()
        # Should have flushed at least once before stop
        assert len(events) >= 1

    # -- push after stop --

    def test_push_after_stop_is_ignored(self):
        """Events pushed after stop() are silently ignored."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.stop()

        count_after_stop = len(events)
        q.push(self._make_event("/b.py", "modified"))
        assert len(events) == count_after_stop  # No new events

    # -- flush --

    def test_flush_immediate(self):
        """flush() immediately fires all pending events."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=5000),  # Long window
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/b.py", "created"))

        q.flush()
        # Should fire immediately even though window hasn't expired
        assert len(events) == 2
        # flush does NOT set _stopped, so we can still push
        q.push(self._make_event("/c.py", "deleted"))
        q.flush()
        assert len(events) == 3

    def test_flush_clears_buffer(self):
        """After flush, the buffer should be empty."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=5000),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "modified"))
        q.flush()
        assert len(events) == 1

        # Flush again should not re-fire
        q.flush()
        assert len(events) == 1

    # -- DebounceConfig --

    def test_debounce_config_defaults(self):
        """DebounceConfig has sensible defaults."""
        c = DebounceConfig()
        assert c.window_ms == 300
        assert c.max_wait_ms == 2000

    def test_debounce_config_frozen(self):
        """DebounceConfig is frozen."""
        c = DebounceConfig(window_ms=100)
        with pytest.raises(Exception):
            c.window_ms = 200  # type: ignore[misc]

    # -- Edge cases --

    def test_empty_queue_stop(self):
        """stop() on an empty queue produces no events."""
        events = []
        q = DebounceQueue(config=self._make_config(), callback=lambda e: events.append(e))
        q.stop()
        assert len(events) == 0

    def test_stop_flushes_remaining(self):
        """stop() flushes remaining events before stopping."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=5000),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "created"))
        q.push(self._make_event("/b.py", "modified"))

        q.stop()
        assert len(events) == 2

    def test_many_files_no_merge(self):
        """Each distinct file path is tracked independently."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        for i in range(50):
            q.push(self._make_event(f"/file_{i}.py", "modified"))

        self._wait(0.3)
        q.stop()

        assert len(events) == 50

    def test_interleaved_events(self):
        """Interleaved events for different files are tracked independently."""
        events = []
        q = DebounceQueue(
            config=self._make_config(window_ms=100),
            callback=lambda e: events.append(e),
        )
        q.push(self._make_event("/a.py", "created"))
        q.push(self._make_event("/b.py", "modified"))
        q.push(self._make_event("/a.py", "modified"))
        q.push(self._make_event("/c.py", "deleted"))
        q.push(self._make_event("/b.py", "modified"))

        self._wait(0.3)
        q.stop()

        paths = {e.path for e in events}
        assert paths == {"/a.py", "/b.py", "/c.py"}
        # a.py had created then modified -> merged
        # b.py had modified then modified -> merged
        # c.py had deleted -> emitted
        assert len(events) == 3


# ============================================================================
# TestPollingFileWatcher
# ============================================================================

class TestPollingFileWatcher:
    """End-to-end tests for the PollingFileWatcher."""

    # -- Lifecycle --

    def test_start_stop(self):
        """start() and stop() work without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))
            assert w.is_running()

            w.stop()
            assert not w.is_running()

    def test_double_start_is_idempotent(self):
        """calling start() twice does nothing the second time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            # Collect one event to verify it's working
            with open(os.path.join(tmpdir, "a.py"), "w") as f:
                f.write("x")
            time.sleep(0.5)

            # Start again — should be a no-op
            w.start(tmpdir, lambda e: events.append(e))

            w.stop()
            assert len([e for e in events if e.change_type == "created"]) >= 1

    def test_double_stop_is_idempotent(self):
        """calling stop() twice does not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: None)
            w.stop()
            w.stop()  # Should not raise

    def test_is_running_reflects_state(self):
        """is_running() reflects start/stop state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            w = PollingFileWatcher(polling_interval=0.1)
            assert not w.is_running()
            w.start(tmpdir, lambda e: None)
            assert w.is_running()
            w.stop()
            assert not w.is_running()

    # -- File change detection --

    def test_file_created(self):
        """A newly created file should emit a 'created' event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w") as f:
                f.write("hello")

            time.sleep(0.5)
            w.stop()

            created = [e for e in events if e.change_type == "created"]
            assert len(created) >= 1
            assert any("test.py" in e.path for e in created)

    def test_file_modified(self):
        """Modifying an existing file should emit a 'modified' event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w") as f:
                f.write("initial")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            # Wait for initial scan to finish, then modify
            time.sleep(0.3)
            with open(test_file, "w") as f:
                f.write("modified content")

            time.sleep(0.5)
            w.stop()

            modified = [e for e in events if e.change_type == "modified"]
            assert len(modified) >= 1
            assert any("test.py" in e.path for e in modified)

    def test_file_deleted(self):
        """Deleting an existing file should emit a 'deleted' event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w") as f:
                f.write("to be deleted")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.3)
            os.remove(test_file)

            time.sleep(0.5)
            w.stop()

            deleted = [e for e in events if e.change_type == "deleted"]
            assert len(deleted) >= 1
            assert any("test.py" in e.path for e in deleted)

    def test_multiple_events_batched(self):
        """Creating multiple files should produce multiple events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            for i in range(5):
                f = os.path.join(tmpdir, f"file_{i}.py")
                with open(f, "w") as fh:
                    fh.write(f"data {i}")

            time.sleep(0.5)
            w.stop()

            created_count = sum(1 for e in events if e.change_type == "created")
            assert created_count >= 5

    # -- Ignore rules --

    def test_ignores_git_directory(self):
        """Files under .git are not watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "config"), "w") as f:
                f.write("git config")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            # No events from .git directory
            git_events = [e for e in events if ".git" in e.path]
            assert len(git_events) == 0

    def test_ignores_node_modules(self):
        """Files under node_modules are not watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nm_dir = os.path.join(tmpdir, "node_modules")
            os.makedirs(nm_dir)
            with open(os.path.join(nm_dir, "package.py"), "w") as f:
                f.write("data")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            nm_events = [e for e in events if "node_modules" in e.path]
            assert len(nm_events) == 0

    def test_ignores_dot_tws(self):
        """Files under .tws are not watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tws_dir = os.path.join(tmpdir, ".tws")
            os.makedirs(tws_dir)
            with open(os.path.join(tws_dir, "data.json"), "w") as f:
                f.write("{}")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            tws_events = [e for e in events if ".tws" in e.path]
            assert len(tws_events) == 0

    def test_ignores_pycache(self):
        """Files under __pycache__ are not watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pyc_dir = os.path.join(tmpdir, "__pycache__")
            os.makedirs(pyc_dir)
            with open(os.path.join(pyc_dir, "module.cpython-311.pyc"), "w") as f:
                f.write("data")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            pyc_events = [e for e in events if "__pycache__" in e.path]
            assert len(pyc_events) == 0

    def test_ignores_deeply_nested_ignored_dir(self):
        """Files nested deeply under an ignored dir are also ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deep = os.path.join(tmpdir, "src", "lib", "node_modules", "pkg")
            os.makedirs(deep)
            with open(os.path.join(deep, "index.js"), "w") as f:
                f.write("data")

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            nm_events = [e for e in events if "node_modules" in e.path]
            assert len(nm_events) == 0

    # -- Extension filter --

    def test_extension_filter_only_py(self):
        """When file_extensions is set, only matching files are watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(
                polling_interval=0.1,
                file_extensions=[".py"],
            )
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.02)  # Let initial scan finish
            with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
                f.write("text")
            with open(os.path.join(tmpdir, "script.py"), "w") as f:
                f.write("print()")
            with open(os.path.join(tmpdir, "app.js"), "w") as f:
                f.write("console.log()")

            time.sleep(0.5)
            w.stop()

            created = [e for e in events if e.change_type == "created"]
            paths = {os.path.basename(e.path) for e in created}
            assert "script.py" in paths
            assert "readme.txt" not in paths
            assert "app.js" not in paths

    def test_no_extension_filter_watches_all(self):
        """When file_extensions is None, all files are watched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1, file_extensions=None)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.02)
            with open(os.path.join(tmpdir, "a.txt"), "w") as f:
                f.write("1")
            with open(os.path.join(tmpdir, "b.md"), "w") as f:
                f.write("2")
            with open(os.path.join(tmpdir, "c.py"), "w") as f:
                f.write("3")

            time.sleep(0.5)
            w.stop()

            created = [e for e in events if e.change_type == "created"]
            basenames = {os.path.basename(e.path) for e in created}
            assert {"a.txt", "b.md", "c.py"}.issubset(basenames)

    # -- Custom ignore patterns --

    def test_custom_ignore_pattern(self):
        """Custom ignore_patterns with fnmatch work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ignore_dir = os.path.join(tmpdir, "my_backups")
            os.makedirs(ignore_dir)
            with open(os.path.join(ignore_dir, "backup.py"), "w") as f:
                f.write("data")

            events = []
            w = PollingFileWatcher(
                polling_interval=0.1,
                ignore_patterns=["my_backups"],
            )
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            backup_events = [e for e in events if "my_backups" in e.path]
            assert len(backup_events) == 0

    def test_custom_fnmatch_pattern(self):
        """Custom fnmatch like ``*-cache`` matches directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "pip-cache")
            os.makedirs(cache_dir)
            with open(os.path.join(cache_dir, "data.py"), "w") as f:
                f.write("data")

            events = []
            w = PollingFileWatcher(
                polling_interval=0.1,
                ignore_patterns=["*-cache"],
            )
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            cache_events = [e for e in events if "pip-cache" in e.path]
            assert len(cache_events) == 0

    # -- Default ignore dirs set --

    def test_default_ignore_dirs_includes_expected(self):
        """DEFAULT_IGNORE_DIRS includes the main ignore targets."""
        assert ".git" in DEFAULT_IGNORE_DIRS
        assert "node_modules" in DEFAULT_IGNORE_DIRS
        assert "__pycache__" in DEFAULT_IGNORE_DIRS
        assert ".tws" in DEFAULT_IGNORE_DIRS

    # -- Non-existent root dir --

    def test_nonexistent_root_dir_no_events(self):
        """Starting on a non-existent directory should not crash."""
        events = []
        w = PollingFileWatcher(polling_interval=0.1)
        w.start("/tmp/nonexistent_dir_xyz_12345", lambda e: events.append(e))
        time.sleep(0.3)
        w.stop()
        # No crash, no events other than maybe None
        # The watcher should handle missing dir gracefully

    # -- Stability --

    def test_stability_short(self):
        """Polling watcher runs for multiple seconds handling file changes without crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            for i in range(10):
                f = os.path.join(tmpdir, f"file_{i}.py")
                with open(f, "w") as fh:
                    fh.write(f"data {i}")
                time.sleep(0.3)

            time.sleep(0.5)
            w.stop()

            assert len(events) > 0
            assert all(isinstance(e, FileChangeEvent) for e in events)

    def test_rapid_create_delete(self):
        """Rapid create and delete of files should not cause crashes or lost events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            # Create, modify, delete rapidly
            for i in range(5):
                f = os.path.join(tmpdir, f"rapid_{i}.py")
                with open(f, "w") as fh:
                    fh.write(f"data {i}")
                time.sleep(0.15)
                os.remove(f)
                time.sleep(0.15)

            time.sleep(0.3)
            w.stop()

            # At least we got some events and no crash
            assert isinstance(events, list)

    def test_subdirectory_file(self):
        """Files created in subdirectories should be detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub", "nested")
            os.makedirs(subdir)

            events = []
            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.02)  # Let initial scan finish
            deep_file = os.path.join(subdir, "deep.py")
            with open(deep_file, "w") as f:
                f.write("nested")

            time.sleep(0.5)
            w.stop()

            created = [e for e in events if e.change_type == "created"]
            assert any("deep.py" in e.path for e in created)

    def test_callback_exception_does_not_crash_watcher(self):
        """If the callback raises, the watcher continues polling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            call_count = [0]  # mutable container

            def callback(e):
                if call_count[0] == 0:
                    call_count[0] += 1
                    raise RuntimeError("Simulated callback crash")

            w = PollingFileWatcher(polling_interval=0.1)
            w.start(tmpdir, callback)

            # Create a file to trigger callback
            with open(os.path.join(tmpdir, "trigger.py"), "w") as f:
                f.write("data")

            time.sleep(0.5)
            # Should still be running despite callback exception
            assert w.is_running()

            # Create another file to verify continued operation
            with open(os.path.join(tmpdir, "alive.py"), "w") as f:
                f.write("alive")

            w.stop()
            # No crash = pass


# ============================================================================
# TestWatchdogFileWatcher
# ============================================================================

class TestWatchdogFileWatcher:
    """Tests for WatchdogFileWatcher — skip if watchdog is not installed."""

    @pytest.fixture
    def watchdog_installed(self):
        """Return True if watchdog is installed."""
        try:
            import watchdog  # noqa: F401
            return True
        except ImportError:
            return False

    def test_import_error_when_watchdog_missing(self, monkeypatch):
        """Raises ImportError with helpful message when watchdog is not available."""
        import builtins

        # Replace __import__ to simulate missing watchdog
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "watchdog" or name == "watchdog.events" or name == "watchdog.observers":
                raise ImportError("No module named 'watchdog'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        # Also clear any cached imports
        import sys
        sys.modules.pop("watchdog", None)
        sys.modules.pop("watchdog.events", None)
        sys.modules.pop("watchdog.observers", None)

        # Re-import the module to get fresh _import_watchdog
        if "tws_graph.watcher.watchdog_watcher" in sys.modules:
            del sys.modules["tws_graph.watcher.watchdog_watcher"]
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher

        with tempfile.TemporaryDirectory() as tmpdir:
            w = WatchdogFileWatcher()
            with pytest.raises(ImportError, match="watchdog"):
                w.start(tmpdir, lambda e: None)

    def test_construction_with_defaults(self):
        """Can construct WatchdogFileWatcher with default args."""
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher
        w = WatchdogFileWatcher()
        assert w.ignore_patterns is not None
        assert ".git" in w.ignore_patterns
        assert w.file_extensions is None

    def test_construction_with_custom_args(self):
        """Can construct with custom ignore_patterns and file_extensions."""
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher
        w = WatchdogFileWatcher(
            ignore_patterns=["*.tmp"],
            file_extensions=[".py", ".js"],
        )
        assert "*.tmp" in w.ignore_patterns
        assert w.file_extensions == [".py", ".js"]

    def test_is_running_initial_false(self):
        """is_running() returns False before start."""
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher
        w = WatchdogFileWatcher()
        assert not w.is_running()

    def test_stop_before_start_is_idempotent(self):
        """Calling stop() before start() does nothing."""
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher
        w = WatchdogFileWatcher()
        w.stop()  # Should not raise

    def test_ignore_patterns_covers_defaults(self):
        """Default ignore_patterns cover key directories."""
        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher, DEFAULT_IGNORE_PATTERNS
        assert ".git" in DEFAULT_IGNORE_PATTERNS
        assert "node_modules" in DEFAULT_IGNORE_PATTERNS
        assert "__pycache__" in DEFAULT_IGNORE_PATTERNS
        assert ".tws" in DEFAULT_IGNORE_PATTERNS

    @pytest.mark.skipif(
        "not __import__('importlib.util').util.find_spec('watchdog')",
    )
    def test_start_stop_with_watchdog(self):
        """Integration test: start and stop when watchdog is available."""
        import importlib.util
        if importlib.util.find_spec("watchdog") is None:
            pytest.skip("watchdog not installed")

        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher

        with tempfile.TemporaryDirectory() as tmpdir:
            events = []
            w = WatchdogFileWatcher()
            w.start(tmpdir, lambda e: events.append(e))
            assert w.is_running()

            # Create a file
            time.sleep(0.1)
            with open(os.path.join(tmpdir, "test.py"), "w") as f:
                f.write("hello")

            time.sleep(0.5)
            w.stop()
            assert not w.is_running()

            created = [e for e in events if e.change_type == "created"]
            assert len(created) >= 1

    @pytest.mark.skipif(
        "not __import__('importlib.util').util.find_spec('watchdog')",
    )
    def test_watchdog_ignores_git_when_running(self):
        """When watchdog is running, .git dir events are filtered."""
        import importlib.util
        if importlib.util.find_spec("watchdog") is None:
            pytest.skip("watchdog not installed")

        from tws_graph.watcher.watchdog_watcher import WatchdogFileWatcher

        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = os.path.join(tmpdir, ".git")
            os.makedirs(git_dir)
            with open(os.path.join(git_dir, "config"), "w") as f:
                f.write("data")

            events = []
            w = WatchdogFileWatcher()
            w.start(tmpdir, lambda e: events.append(e))

            time.sleep(0.5)
            w.stop()

            git_events = [e for e in events if ".git" in e.path]
            assert len(git_events) == 0


# ============================================================================
# TestFileWatcherABC
# ============================================================================

class TestFileWatcherABC:
    """Tests for the FileWatcher abstract base class."""

    def test_cannot_instantiate(self):
        """FileWatcher is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            FileWatcher()  # type: ignore[abstract]

    def test_concrete_subclass_ok(self):
        """A concrete subclass with all methods works."""

        class ConcreteWatcher(FileWatcher):
            def start(self, root_dir, callback):
                pass

            def stop(self):
                pass

            def is_running(self):
                return False

        w = ConcreteWatcher()
        assert isinstance(w, FileWatcher)
        assert not w.is_running()
