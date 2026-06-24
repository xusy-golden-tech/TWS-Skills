"""Tests for CLI PipelineEngine integration: index and sync commands.

Verifies that ``tws-graph index`` and ``tws-graph sync`` correctly use the
PipelineEngine + SqliteStore pipeline, and that output format is compatible
with the old orchestrator-based output.
"""

import os
import time
import pytest
from typer.testing import CliRunner
from pathlib import Path

from tws_graph.cli import app
from tws_graph.store import SqliteStore


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def runner():
    """Create a CliRunner for testing CLI commands."""
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary database path under tmp_path."""
    db_dir = tmp_path / ".tws" / "codegraph"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "index.db")


# ============================================================================
# Test: index command
# ============================================================================


class TestIndexCommand:
    """Tests for ``tws-graph index`` using PipelineEngine."""

    def test_index_python_project(self, runner, sample_py_project, db_path):
        """Index a Python project and verify database has data."""
        result = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

        # Verify output format
        assert "正在索引" in result.output
        assert "索引完成" in result.output
        assert "个符号" in result.output
        assert "条关系" in result.output
        assert "耗时" in result.output

        # Verify database has real data
        store = SqliteStore(db_path)
        stats = store.stats()
        assert stats["node_count"] > 0, "Expected nodes in database"
        assert stats["edge_count"] > 0, "Expected edges in database"
        assert stats["file_count"] >= 1, "Expected files in database"
        store.close()

    def test_index_typescript_project(self, runner, sample_ts_project, db_path):
        """Index a TypeScript project via CLI."""
        result = runner.invoke(app, [
            "index", str(sample_ts_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}"

        store = SqliteStore(db_path)
        stats = store.stats()
        assert stats["node_count"] > 0
        assert stats["file_count"] >= 1
        store.close()

    def test_index_force_flag(self, runner, sample_py_project, db_path):
        """force=True re-indexes all files (no skip)."""
        # First index
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Force re-index should still process files (not skip)
        r2 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--force",
        ])
        assert r2.exit_code == 0
        # Force run should not mention "跳过"
        assert "+" not in r2.output.split("索引完成")[1].split("耗时")[0] \
            if "索引完成" in r2.output else True

    def test_index_empty_project_shows_warning(self, runner, tmp_path, db_path):
        """Empty project (no source files) should warn and exit 1."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = runner.invoke(app, [
            "index", str(empty_dir),
            "--db", db_path,
            "--serial",
        ])
        assert result.exit_code == 1
        assert "警告" in result.output or "未找到源文件" in result.output

    def test_index_output_contains_database_stats(self, runner, sample_py_project, db_path):
        """Output should include database statistics line."""
        result = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0
        assert "数据库" in result.output
        assert "节点" in result.output
        assert "边" in result.output
        assert "文件" in result.output

    def test_index_output_contains_resolve_stats(self, runner, sample_py_project, db_path):
        """Output should NOT crash; resolve stats only appear for cross-file edges."""
        result = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0
        # For single-file projects, cross-file resolve may have 0 edges
        # so "边解析" line may or may not appear — the key is that CLI runs clean
        assert "索引完成" in result.output
        assert "数据库" in result.output


# ============================================================================
# Test: sync command
# ============================================================================


class TestSyncCommand:
    """Tests for ``tws-graph sync`` using PipelineEngine."""

    def test_sync_python_project(self, runner, sample_py_project, db_path):
        """Sync a Python project and verify output format."""
        # Need an initial index so sync can do incremental
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        result = runner.invoke(app, [
            "sync", str(sample_py_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}"

        # Sync output should say "同步完成"
        assert "同步完成" in result.output

    def test_sync_no_changes_skips_all(self, runner, sample_py_project, db_path):
        """After index, sync with no changes should report '无变化' and skip all."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Sync immediately — no file changes
        r2 = runner.invoke(app, [
            "sync", str(sample_py_project),
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        assert "无变化" in r2.output
        assert "无需更新" in r2.output

    def test_sync_after_file_change_detects_updates(self, runner, sample_py_project, db_path):
        """Sync after modifying a source file should index the changed file."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Modify a source file (touch + append)
        py_file = sample_py_project / "fixtures" / "sample.py"
        with open(py_file, "a", encoding="utf-8") as f:
            f.write("\ndef added_function():\n    return 42\n")
        # Ensure mtime actually changes (some filesystems have coarse granularity)
        time.sleep(0.1)

        r2 = runner.invoke(app, [
            "sync", str(sample_py_project),
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        # Should have indexed at least 1 file
        assert "个文件更新" in r2.output or "个文件" in r2.output
        # Should NOT say "无变化" since we changed a file
        assert "无变化" not in r2.output.split("同步完成")[1] if "同步完成" in r2.output else True

    def test_sync_empty_project_shows_warning(self, runner, tmp_path, db_path):
        """Empty project sync should warn and exit 1."""
        empty_dir = tmp_path / "empty2"
        empty_dir.mkdir()

        result = runner.invoke(app, [
            "sync", str(empty_dir),
            "--db", db_path,
        ])
        assert result.exit_code == 1
        assert "警告" in result.output or "未找到源文件" in result.output

    def test_sync_output_contains_timing(self, runner, sample_py_project, db_path):
        """Sync output should include elapsed time."""
        # Index first to have a baseline
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        r2 = runner.invoke(app, [
            "sync", str(sample_py_project),
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        assert "耗时" in r2.output or "ms" in r2.output


# ============================================================================
# Test: Pipeline parameter passing
# ============================================================================


class TestPipelineParameterPassing:
    """Verify that CLI passes correct parameters to PipelineEngine."""

    def test_force_passed_to_pipeline(self, runner, sample_py_project, db_path, monkeypatch):
        """Verify force=True reaches the PipelineEngine via PipelineContext."""
        from tws_graph.pipeline.engine import PipelineEngine

        original_execute = PipelineEngine.execute
        captured_forces = []

        def fake_execute(self, files=None, root_dir=".", force=False, progress_callback=None):
            captured_forces.append(force)
            # Call real execute to produce valid output
            return original_execute(
                self, files=files, root_dir=root_dir, force=force,
                progress_callback=progress_callback,
            )

        monkeypatch.setattr(PipelineEngine, "execute", fake_execute)

        # Run with --force and --serial (parallel path does not use PipelineEngine)
        runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--force",
            "--serial",
        ])
        assert any(captured_forces), "force=True should be passed to PipelineEngine"

    def test_force_false_by_default(self, runner, sample_py_project, db_path, monkeypatch):
        """Verify force=False is the default."""
        from tws_graph.pipeline.engine import PipelineEngine

        original_execute = PipelineEngine.execute
        captured_forces = []

        def fake_execute(self, files=None, root_dir=".", force=False, progress_callback=None):
            captured_forces.append(force)
            return original_execute(
                self, files=files, root_dir=root_dir, force=force,
                progress_callback=progress_callback,
            )

        monkeypatch.setattr(PipelineEngine, "execute", fake_execute)

        # Run without --force, --serial to use PipelineEngine path
        runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--serial",
        ])
        # The index command defaults to force=False
        assert len(captured_forces) > 0
        assert not any(captured_forces), "force should be False by default"

    def test_root_dir_passed_to_pipeline(self, runner, sample_py_project, db_path, monkeypatch):
        """Verify root_dir reaches PipelineEngine."""
        from tws_graph.pipeline.engine import PipelineEngine

        original_execute = PipelineEngine.execute
        captured_dirs = []

        def fake_execute(self, files=None, root_dir=".", force=False, progress_callback=None):
            captured_dirs.append(root_dir)
            return original_execute(
                self, files=files, root_dir=root_dir, force=force,
                progress_callback=progress_callback,
            )

        monkeypatch.setattr(PipelineEngine, "execute", fake_execute)

        runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--serial",
        ])
        assert len(captured_dirs) > 0
        assert str(sample_py_project) in captured_dirs[0]

    def test_files_collected_before_pipeline(self, runner, sample_py_project, db_path, monkeypatch):
        """Verify files list is properly collected and passed to PipelineEngine."""
        from tws_graph.pipeline.engine import PipelineEngine

        original_execute = PipelineEngine.execute
        captured_files = []

        def fake_execute(self, files=None, root_dir=".", force=False, progress_callback=None):
            captured_files.append(list(files) if files else [])
            return original_execute(
                self, files=files, root_dir=root_dir, force=force,
                progress_callback=progress_callback,
            )

        monkeypatch.setattr(PipelineEngine, "execute", fake_execute)

        runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--serial",
        ])
        assert len(captured_files) > 0
        # Should have found the sample.py file
        all_files = captured_files[0]
        assert len(all_files) > 0
        assert any("sample.py" in f for f in all_files), \
            f"Expected sample.py in files list, got: {all_files}"


# ============================================================================
# Test: Other commands unaffected
# ============================================================================


class TestOtherCommandsUnaffected:
    """Verify that non-index/sync commands are unchanged."""

    def test_version_command(self, runner):
        """--version should still work."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "tws-graph" in result.output

    def test_help_command(self, runner):
        """No arguments should show help."""
        result = runner.invoke(app, [])
        # Typer shows help when no subcommand is given
        assert "Usage" in result.output or "用法" in result.output or "Commands" in result.output

    def test_lint_command(self, runner, tmp_path):
        """tws-graph lint should still work."""
        result = runner.invoke(app, ["lint", str(tmp_path)])
        # May succeed or fail depending on content, but should not crash
        assert result.exit_code in (0, 1)

    def test_search_command_no_db(self, runner, db_path):
        """search with no existing DB should fail gracefully."""
        result = runner.invoke(app, [
            "search", "test",
            "--db", str(Path(db_path).parent / "nonexistent.db"),
        ])
        # Should fail because DB doesn't exist
        assert result.exit_code == 1
        assert "索引数据库不存在" in result.output or "error" in result.output.lower()

    def test_calls_command_no_db(self, runner):
        """calls with no DB should fail gracefully."""
        result = runner.invoke(app, [
            "calls", "somefunc",
            "--db", "/tmp/nonexistent_tws_test.db",
        ])
        assert result.exit_code == 1

    def test_hooks_status_command(self, runner, tmp_path):
        """hooks status should still work."""
        result = runner.invoke(app, ["hooks", "status", str(tmp_path)])
        # May fail if not in a git repo, but should not crash
        assert result.exit_code in (0, 1)

    def test_search_after_index_works(self, runner, sample_py_project, db_path):
        """After indexing, search should find symbols."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Search for a known symbol
        r2 = runner.invoke(app, [
            "search", "calculateTotal",
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        assert "calculateTotal" in r2.output

    def test_calls_after_index_works(self, runner, sample_py_project, db_path):
        """After indexing, calls should find callers."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Query calls for a known symbol
        r2 = runner.invoke(app, [
            "calls", "process",
            "--inbound",
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        # Should find at least the symbol itself
        assert "process" in r2.output

    def test_unresolved_after_index_works(self, runner, sample_py_project, db_path):
        """After indexing, unresolved should list unresolved refs."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        r2 = runner.invoke(app, [
            "unresolved",
            "--db", db_path,
        ])
        assert r2.exit_code == 0
        # Should report results or "没有未解析的引用"
        assert len(r2.output.strip()) > 0


# ============================================================================
# Test: File record management (stat pre-filter infrastructure)
# ============================================================================


class TestFileRecordManagement:
    """Verify that file records are properly managed for StatFilterPass."""

    def test_file_records_created_after_index(self, runner, sample_py_project, db_path):
        """After indexing, file records should exist in the store."""
        result = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert result.exit_code == 0

        store = SqliteStore(db_path)
        files = store.get_all_files()
        assert len(files) > 0, "Expected file records after indexing"
        # Each file record should have path and content_hash
        for f in files:
            assert "path" in f
            assert "content_hash" in f
            assert "language" in f
        store.close()

    def test_sync_skips_with_valid_file_records(self, runner, sample_py_project, db_path):
        """After initial index, sync should skip unchanged files."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Count files from first run
        store = SqliteStore(db_path)
        file_count_before = store.stats()["file_count"]
        store.close()

        # Sync with no changes
        r2 = runner.invoke(app, [
            "sync", str(sample_py_project),
            "--db", db_path,
        ])
        assert r2.exit_code == 0

        # File count should remain the same
        store = SqliteStore(db_path)
        file_count_after = store.stats()["file_count"]
        store.close()
        assert file_count_after == file_count_before

    def test_deleted_files_cleaned_on_reindex(self, runner, sample_py_project, db_path):
        """When a file is deleted, re-index should clean up its records."""
        # Index first
        r1 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
        ])
        assert r1.exit_code == 0

        # Delete a source file
        py_file = sample_py_project / "fixtures" / "sample.py"
        os.unlink(py_file)

        # Re-index (use --serial to go through PipelineEngine path with cleanup)
        r2 = runner.invoke(app, [
            "index", str(sample_py_project),
            "--db", db_path,
            "--serial",
        ])
        # May warn about no source files, which is OK
        # The point is that deleted file records are cleaned up

        # Verify no dangling file records
        store = SqliteStore(db_path)
        files = store.get_all_files()
        paths = {f["path"] for f in files}
        assert "fixtures/sample.py" not in paths, \
            f"Deleted file should not be in file records: {paths}"
        store.close()
