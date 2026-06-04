"""Tests for snapshot and diff functionality."""

import pytest
import os
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.diff import (
    save_snapshot, list_snapshots, compare_snapshots,
    DiffReport, format_diff_report,
)
from tws_graph.indexer.orchestrator import ExtractionOrchestrator


class TestSnapshot:
    """Tests for snapshot file operations."""

    @pytest.fixture
    def db_path_with_data(self, sample_py_project, tmp_path):
        """Create an indexed DB and return the db_path."""
        db_path = str(tmp_path / "codegraph" / "index.db")
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        orch.index_all()
        db.close()
        return db_path

    def test_save_snapshot(self, db_path_with_data, tmp_path):
        snapshots_dir = str(tmp_path / "snapshots")
        dest = save_snapshot(db_path_with_data, "test1", snapshots_dir)
        assert os.path.exists(dest)
        assert "index-test1.db" in dest

    def test_list_snapshots(self, db_path_with_data, tmp_path):
        snapshots_dir = str(tmp_path / "snapshots")
        save_snapshot(db_path_with_data, "a", snapshots_dir)
        save_snapshot(db_path_with_data, "b", snapshots_dir)
        names = list_snapshots(snapshots_dir)
        assert "a" in names
        assert "b" in names

    def test_list_empty(self, tmp_path):
        names = list_snapshots(str(tmp_path / "nonexistent"))
        assert names == []


class TestDiff:
    """Tests for snapshot comparison."""

    @pytest.fixture
    def before_after(self, sample_py_project, tmp_path):
        """Create before/after snapshots by modifying source between indexes."""
        proj = tmp_path / "project"
        proj.mkdir(parents=True)
        src_file = proj / "sample.py"

        # Before: original content
        src_file.write_text("""
def foo(x):
    return x + 1

def bar(y):
    return foo(y)
""", encoding="utf-8")

        before_db = str(tmp_path / "before_dir" / "index.db")
        db = DatabaseConnection.initialize(before_db)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(proj), queries)
        orch.index_all()
        db.close()

        snap_dir = str(tmp_path / "snapshots")
        before_path = save_snapshot(before_db, "before", snap_dir)

        # After: add a new function and change signature
        src_file.write_text("""
def foo(x, z=0):
    return x + z

def bar(y):
    return foo(y)

def baz(w):
    return w * 2
""", encoding="utf-8")

        after_db = str(tmp_path / "after_dir" / "index.db")
        db2 = DatabaseConnection.initialize(after_db)
        queries2 = QueryBuilder(db2.conn)
        orch2 = ExtractionOrchestrator(str(proj), queries2)
        orch2.index_all()
        db2.close()

        after_path = save_snapshot(after_db, "after", snap_dir)
        return before_path, after_path

    def test_diff_added_symbol(self, before_after):
        before_path, after_path = before_after
        report = compare_snapshots(before_path, after_path)
        assert isinstance(report, DiffReport)
        # baz(w) should be added
        added_names = [s["name"] for s in report.added_symbols]
        assert "baz" in added_names

    def test_diff_signature_changed(self, before_after):
        before_path, after_path = before_after
        report = compare_snapshots(before_path, after_path)
        sig_changes = {s["name"]: s for s in report.signature_changed}
        assert "foo" in sig_changes
        assert "z" in sig_changes["foo"]["new_signature"]
        assert "z" not in (sig_changes["foo"]["old_signature"] or "")

    def test_diff_removed_symbol(self, before_after):
        before_path, after_path = before_after
        # Reverse: compare after (new) vs before (old)
        report = compare_snapshots(after_path, before_path)
        removed_names = [s["name"] for s in report.removed_symbols]
        assert "baz" in removed_names

    def test_diff_brief_changed(self, before_after):
        before_path, after_path = before_after
        report = compare_snapshots(before_path, after_path)
        output = format_diff_report(report, brief=True)
        assert output == "changed"

    def test_diff_brief_unchanged(self, before_after):
        before_path, _ = before_after
        report = compare_snapshots(before_path, before_path)
        output = format_diff_report(report, brief=True)
        assert output == "unchanged"

    def test_diff_has_changes_property(self, before_after):
        before_path, after_path = before_after
        report = compare_snapshots(before_path, after_path)
        assert report.has_changes is True

        # Same snapshots → no changes
        report2 = compare_snapshots(before_path, before_path)
        assert report2.has_changes is False

    def test_diff_full_format(self, before_after):
        before_path, after_path = before_after
        report = compare_snapshots(before_path, after_path)
        output = format_diff_report(report)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_diff_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compare_snapshots(
                str(tmp_path / "nonexistent.db"),
                str(tmp_path / "also_nonexistent.db"),
            )
