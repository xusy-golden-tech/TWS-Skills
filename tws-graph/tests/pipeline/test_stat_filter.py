"""Tests for StatFilterPass — file stat (mtime/size) filtering."""

import os
import time
import pytest
from pathlib import Path

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Helpers
# ============================================================================


def _make_store() -> MemoryStore:
    """Create a fresh MemoryStore."""
    return MemoryStore()


def _make_pass():
    """Create a StatFilterPass instance (lazy import)."""
    from tws_graph.pipeline.passes.stat_filter import StatFilterPass
    return StatFilterPass()


def _create_file(root_dir: Path, rel_path: str, content: str) -> str:
    """Create a file under root_dir and return its relative path."""
    full_path = root_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return rel_path


# ============================================================================
# Basic behavior
# ============================================================================


class TestStatFilterBasic:
    """Basic StatFilterPass behavior."""

    def test_force_true_keeps_all_files(self, tmp_path: Path):
        """When force=True, all files are retained regardless of stat."""
        store = _make_store()
        # Register file in store with fixed mtime/size
        store.upsert_file(
            path="a.py",
            content_hash="abc",
            language="python",
            size=100,
            modified_at=1000,
        )

        _create_file(tmp_path, "a.py", "x = 1")
        _create_file(tmp_path, "b.py", "y = 2")

        ctx = PipelineContext(
            files=["a.py", "b.py"],
            root_dir=str(tmp_path),
            force=True,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert set(result.files) == {"a.py", "b.py"}
        assert result.metadata.get("filtered_out", 0) == 0

    def test_store_none_keeps_all_files(self, tmp_path: Path):
        """When store is None, all files are retained (no stat comparison possible)."""
        _create_file(tmp_path, "a.py", "x = 1")
        _create_file(tmp_path, "b.py", "y = 2")

        ctx = PipelineContext(
            files=["a.py", "b.py"],
            root_dir=str(tmp_path),
            force=False,
            store=None,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert set(result.files) == {"a.py", "b.py"}

    def test_empty_files_list(self, tmp_path: Path):
        """Empty files list should not crash."""
        store = _make_store()
        ctx = PipelineContext(
            files=[],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.files == []
        assert result.metadata.get("filtered_out", 0) == 0


# ============================================================================
# Filtering logic
# ============================================================================


class TestStatFilterFiltering:
    """Tests for the filtering logic — mtime/size comparison."""

    def test_file_not_in_store_is_retained(self, tmp_path: Path):
        """File with no existing record in store is retained."""
        store = _make_store()
        _create_file(tmp_path, "new_file.py", "print('hello')")

        ctx = PipelineContext(
            files=["new_file.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.files == ["new_file.py"]

    def test_file_with_changed_mtime_is_retained(self, tmp_path: Path):
        """File whose mtime differs from store record is retained."""
        store = _make_store()
        store.upsert_file(
            path="a.py",
            content_hash="abc",
            language="python",
            size=100,
            modified_at=1000,
        )

        _create_file(tmp_path, "a.py", "x = 1")
        # File on disk has different mtime than store (1000)

        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.files == ["a.py"]

    def test_file_with_changed_size_is_retained(self, tmp_path: Path):
        """File whose size differs from store record is retained."""
        store = _make_store()

        _create_file(tmp_path, "a.py", "x = 1" * 100)
        actual_stat = os.stat(str(tmp_path / "a.py"))

        # Register with wrong size
        store.upsert_file(
            path="a.py",
            content_hash="abc",
            language="python",
            size=10,  # wrong size
            modified_at=int(actual_stat.st_mtime),
        )

        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.files == ["a.py"]

    def test_unchanged_file_is_filtered_out(self, tmp_path: Path):
        """File with matching mtime and size is filtered out."""
        store = _make_store()

        _create_file(tmp_path, "a.py", "unchanged content")
        stat = os.stat(str(tmp_path / "a.py"))

        store.upsert_file(
            path="a.py",
            content_hash="abc",
            language="python",
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.files == []
        assert result.metadata.get("filtered_out", 0) == 1

    def test_mixed_files_some_changed_some_unchanged(self, tmp_path: Path):
        """Mix of changed and unchanged files."""
        store = _make_store()

        # Create three files
        _create_file(tmp_path, "a.py", "content a")
        _create_file(tmp_path, "b.py", "content b")
        _create_file(tmp_path, "c.py", "content c changed")

        # Register a.py and b.py in store with matching stats
        for name in ["a.py", "b.py"]:
            stat = os.stat(str(tmp_path / name))
            store.upsert_file(
                path=name,
                content_hash="hash_" + name,
                language="python",
                size=stat.st_size,
                modified_at=int(stat.st_mtime),
            )

        # c.py NOT in store (should be retained)
        # a.py -> matching, should be filtered
        # b.py -> matching, should be filtered

        ctx = PipelineContext(
            files=["a.py", "b.py", "c.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert set(result.files) == {"c.py"}
        assert result.metadata.get("filtered_out", 0) == 2


# ============================================================================
# Edge / error cases
# ============================================================================


class TestStatFilterEdgeCases:
    """Edge case and error handling tests."""

    def test_file_stat_fails_retained(self, tmp_path: Path):
        """File that cannot be stat'd (e.g., deleted) is retained."""
        store = _make_store()

        ctx = PipelineContext(
            files=["nonexistent.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        # File that doesn't exist on disk should still be retained
        # (so downstream passes can handle the error)
        assert result.files == ["nonexistent.py"]

    def test_no_store_no_force_keeps_all(self, tmp_path: Path):
        """Without store and without force, all files are kept."""
        _create_file(tmp_path, "a.py", "x = 1")
        _create_file(tmp_path, "b.py", "y = 2")

        ctx = PipelineContext(
            files=["a.py", "b.py"],
            root_dir=str(tmp_path),
            force=False,
            store=None,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert set(result.files) == {"a.py", "b.py"}

    def test_filtered_out_metadata_default(self, tmp_path: Path):
        """filtered_out is always present in metadata after run."""
        store = _make_store()
        _create_file(tmp_path, "a.py", "x = 1")

        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
            force=False,
            store=store,
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert "filtered_out" in result.metadata


# ============================================================================
# Pass interface compliance
# ============================================================================


class TestStatFilterInterface:
    """Verify StatFilterPass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name_and_description(self):
        pas = _make_pass()
        assert pas.name == "stat-filter"
        assert len(pas.description) > 0

    def test_no_dependencies(self):
        pas = _make_pass()
        assert pas.dependencies == []

    def test_supports_incremental(self):
        pas = _make_pass()
        assert pas.supports_incremental is True

    def test_enabled_default(self):
        pas = _make_pass()
        assert pas.enabled(PipelineContext()) is True

    def test_run_returns_pipeline_context(self, tmp_path: Path):
        store = _make_store()
        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
            store=store,
        )
        pas = _make_pass()
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
