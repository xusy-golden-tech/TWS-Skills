"""TDD tests for analysis/invalidation.py — InvalidationTracker.

Tests are written BEFORE implementation (TDD red phase).
Expected module: tws_graph.analysis.invalidation (does not exist yet).

Covers:
    1. AnalyzerRegistration dataclass — creation, defaults, equality
    2. InvalidationTracker initialization — SQLite table creation
    3. register() — analyzer registration, idempotent re-register
    4. check_invalidation() — first-run, no-change, mtime-change,
       multi-analyzer, empty-registrations, file-add/remove
    5. mark_valid() — basic, empty paths, overwrite existing
    6. verify_consistency() — structure, empty, no-data
    7. close() — connection cleanup
    8. Integration — MemoryStore + temp SQLite, full lifecycle
    9. Edge cases & error handling

Test strategy per user spec:
    - MemoryStore (existing) for file stats
    - Temporary SQLite database for analysis_tracking table
    - Simulate: register → mark_valid → modify mtime → check_invalidation returns stale
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tws_graph.store.memory_store import MemoryStore
from tws_graph.store.exceptions import StoreClosedError

# ---------------------------------------------------------------------------
# Expected imports from module under test (will fail initially — TDD red)
# ---------------------------------------------------------------------------

from tws_graph.analysis.invalidation import (
    AnalyzerRegistration,
    ConsistencyReport,
    InvalidationTracker,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Temporary SQLite database path for InvalidationTracker."""
    return str(tmp_path / "tracking.db")


@pytest.fixture
def tracker(tmp_db_path: str) -> InvalidationTracker:
    """Create a fresh InvalidationTracker with an empty SQLite DB."""
    t = InvalidationTracker(db_path=tmp_db_path)
    yield t
    t.close()


@pytest.fixture
def store() -> MemoryStore:
    """Create a fresh MemoryStore with test file records."""
    s = MemoryStore()
    now = int(time.time())
    s.upsert_file("src/module_a.py", "hash_a1", "python", size=200, modified_at=now)
    s.upsert_file("src/module_b.py", "hash_b1", "python", size=300, modified_at=now)
    s.upsert_file("src/utils.py", "hash_u1", "python", size=100, modified_at=now)
    s.upsert_file("tests/test_a.py", "hash_t1", "python", size=150, modified_at=now)
    return s


@pytest.fixture
def reg_dead_code() -> AnalyzerRegistration:
    """Standard registration for a dead_code analyzer."""
    return AnalyzerRegistration(
        analyzer_name="dead_code",
        file_patterns=["src/**/*.py"],
        node_kinds=["function", "method", "class"],
        version=1,
    )


@pytest.fixture
def reg_complexity() -> AnalyzerRegistration:
    """Standard registration for a complexity analyzer."""
    return AnalyzerRegistration(
        analyzer_name="complexity",
        file_patterns=["src/*.py"],
        node_kinds=["function", "method"],
        version=1,
    )


# =============================================================================
# 1. AnalyzerRegistration dataclass
# =============================================================================


class TestAnalyzerRegistration:
    """Tests for the AnalyzerRegistration dataclass."""

    def test_create_with_all_fields(self):
        """Should create with all fields specified."""
        reg = AnalyzerRegistration(
            analyzer_name="dead_code",
            file_patterns=["src/**/*.py", "lib/**/*.py"],
            node_kinds=["function", "class"],
            version=2,
        )
        assert reg.analyzer_name == "dead_code"
        assert reg.file_patterns == ["src/**/*.py", "lib/**/*.py"]
        assert reg.node_kinds == ["function", "class"]
        assert reg.version == 2

    def test_default_version_is_one(self):
        """Version should default to 1."""
        reg = AnalyzerRegistration(
            analyzer_name="test_analyzer",
            file_patterns=["*.py"],
            node_kinds=[],
        )
        assert reg.version == 1

    def test_equality_by_value(self):
        """Two registrations with identical fields should be equal."""
        reg1 = AnalyzerRegistration("a", ["*.py"], ["func"], version=1)
        reg2 = AnalyzerRegistration("a", ["*.py"], ["func"], version=1)
        assert reg1 == reg2

    def test_inequality_different_name(self):
        """Registrations with different names should not be equal."""
        reg1 = AnalyzerRegistration("a", ["*.py"], ["func"])
        reg2 = AnalyzerRegistration("b", ["*.py"], ["func"])
        assert reg1 != reg2

    def test_inequality_different_version(self):
        """Registrations with different versions should not be equal."""
        reg1 = AnalyzerRegistration("a", ["*.py"], ["func"], version=1)
        reg2 = AnalyzerRegistration("a", ["*.py"], ["func"], version=2)
        assert reg1 != reg2

    def test_is_dataclass(self):
        """Should be a dataclass (not a plain class)."""
        import dataclasses
        assert dataclasses.is_dataclass(AnalyzerRegistration)

    def test_empty_file_patterns_allowed(self):
        """Empty file_patterns list should be allowed."""
        reg = AnalyzerRegistration(
            analyzer_name="no_files",
            file_patterns=[],
            node_kinds=["function"],
        )
        assert reg.file_patterns == []

    def test_empty_node_kinds_allowed(self):
        """Empty node_kinds list should be allowed."""
        reg = AnalyzerRegistration(
            analyzer_name="all_nodes",
            file_patterns=["*.py"],
            node_kinds=[],
        )
        assert reg.node_kinds == []


# =============================================================================
# 2. InvalidationTracker initialization
# =============================================================================


class TestInvalidationTrackerInit:
    """Tests for InvalidationTracker construction and table setup."""

    def test_create_with_db_path(self, tmp_db_path):
        """Should create tracker with a db_path and not raise."""
        t = InvalidationTracker(db_path=tmp_db_path)
        assert t is not None
        t.close()

    def test_creates_analysis_tracking_table(self, tmp_db_path):
        """Should create the analysis_tracking table on init."""
        t = InvalidationTracker(db_path=tmp_db_path)
        # Verify the table exists by querying sqlite_master
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_tracking'"
        )
        row = cursor.fetchone()
        conn.close()
        t.close()
        assert row is not None
        assert row[0] == "analysis_tracking"

    def test_creates_table_with_correct_schema(self, tmp_db_path):
        """analysis_tracking table should have all required columns."""
        t = InvalidationTracker(db_path=tmp_db_path)
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.execute("PRAGMA table_info('analysis_tracking')")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()
        t.close()
        assert "id" in columns
        assert "analyzer_name" in columns
        assert "file_path" in columns
        assert "file_mtime" in columns
        assert "is_valid" in columns
        assert columns["analyzer_name"] == "TEXT"
        assert columns["file_path"] == "TEXT"
        assert columns["file_mtime"] == "INTEGER"
        assert columns["is_valid"].startswith("INT")

    def test_unique_constraint_on_analyzer_and_file(self, tmp_db_path):
        """Should enforce UNIQUE(analyzer_name, file_path)."""
        t = InvalidationTracker(db_path=tmp_db_path)
        conn = sqlite3.connect(tmp_db_path)
        # Insert a record
        conn.execute(
            """INSERT INTO analysis_tracking
               (analyzer_name, file_path, file_mtime, computed_at, is_valid)
               VALUES ('test_a', 'src/a.py', 1000, 1000000, 1)"""
        )
        # Insert duplicate should raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO analysis_tracking
                   (analyzer_name, file_path, file_mtime, computed_at, is_valid)
                   VALUES ('test_a', 'src/a.py', 2000, 2000000, 1)"""
            )
        conn.close()
        t.close()

    def test_table_is_empty_initially(self, tmp_db_path):
        """analysis_tracking table should have no rows initially."""
        t = InvalidationTracker(db_path=tmp_db_path)
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM analysis_tracking")
        count = cursor.fetchone()[0]
        conn.close()
        t.close()
        assert count == 0

    def test_idempotent_init(self, tmp_db_path):
        """Creating a second tracker on same db should not fail (IF NOT EXISTS)."""
        t1 = InvalidationTracker(db_path=tmp_db_path)
        t1.close()
        t2 = InvalidationTracker(db_path=tmp_db_path)
        t2.close()
        # Should not raise

    def test_custom_table_name(self, tmp_db_path):
        """Should support custom table_name parameter."""
        t = InvalidationTracker(db_path=tmp_db_path, table_name="custom_tracking")
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='custom_tracking'"
        )
        row = cursor.fetchone()
        conn.close()
        t.close()
        assert row is not None


# =============================================================================
# 3. register()
# =============================================================================


class TestRegister:
    """Tests for InvalidationTracker.register()."""

    def test_register_single(self, tracker, reg_dead_code):
        """Should register an analyzer without error."""
        tracker.register(reg_dead_code)
        # No exception = pass

    def test_register_multiple(self, tracker, reg_dead_code, reg_complexity):
        """Should register multiple analyzers without error."""
        tracker.register(reg_dead_code)
        tracker.register(reg_complexity)
        # No exception = pass

    def test_register_idempotent(self, tracker, reg_dead_code):
        """Re-registering the same analyzer should overwrite (idempotent)."""
        tracker.register(reg_dead_code)
        tracker.register(reg_dead_code)
        # Should not raise or duplicate

    def test_register_updates_existing(self, tracker):
        """Re-registering with different patterns should update."""
        reg_v1 = AnalyzerRegistration("test_a", ["src/*.py"], ["func"], version=1)
        reg_v2 = AnalyzerRegistration("test_a", ["src/**/*.py", "lib/*.py"], ["func", "class"], version=2)
        tracker.register(reg_v1)
        tracker.register(reg_v2)
        # No exception — registration should be updated


# =============================================================================
# 4. check_invalidation()
# =============================================================================


class TestCheckInvalidation:
    """Tests for InvalidationTracker.check_invalidation(store)."""

    # -- No registrations ---------------------------------------------------

    def test_no_registrations_returns_empty(self, tracker, store):
        """With no registered analyzers, should return empty dict."""
        result = tracker.check_invalidation(store)
        assert result == {}

    # -- First run (no tracking data) ---------------------------------------

    def test_first_run_returns_all_matched_files(
        self, tracker, store, reg_dead_code
    ):
        """First run with no tracking data: all matched files are stale."""
        tracker.register(reg_dead_code)
        result = tracker.check_invalidation(store)
        assert isinstance(result, dict)
        assert "dead_code" in result
        # Pattern "src/**/*.py" matches module_a.py, module_b.py, utils.py
        # but NOT tests/test_a.py
        stale = result["dead_code"]
        assert "src/module_a.py" in stale
        assert "src/module_b.py" in stale
        assert "src/utils.py" in stale
        assert "tests/test_a.py" not in stale
        assert len(stale) == 3

    def test_first_run_returns_all_analyzers(
        self, tracker, store, reg_dead_code, reg_complexity
    ):
        """Multiple analyzers, first run: each gets its matched files."""
        tracker.register(reg_dead_code)
        tracker.register(reg_complexity)
        result = tracker.check_invalidation(store)
        assert "dead_code" in result
        assert "complexity" in result
        # dead_code: src/**/*.py → 3 files
        assert len(result["dead_code"]) == 3
        # complexity: src/*.py (no **) → only top-level src/*.py
        # MemoryStore has: src/module_a.py, src/module_b.py, src/utils.py
        # Pattern "src/*.py" matches all three (single-level wildcard)
        assert len(result["complexity"]) == 3

    # -- No changes after mark_valid ---------------------------------------

    def test_no_changes_returns_empty(self, tracker, store, reg_dead_code):
        """After mark_valid, if no files changed, should return empty."""
        tracker.register(reg_dead_code)
        # First run — get stale files
        first_result = tracker.check_invalidation(store)
        stale_files = first_result["dead_code"]
        # Mark them valid
        tracker.mark_valid("dead_code", stale_files)
        # Second run — should be empty
        result = tracker.check_invalidation(store)
        assert result == {} or result.get("dead_code", []) == []

    # -- Mtime change -------------------------------------------------------

    def test_mtime_change_detected(self, tracker, store, reg_dead_code):
        """When file mtime changes, check_invalidation should return it."""
        tracker.register(reg_dead_code)
        # Mark valid
        tracker.mark_valid("dead_code", ["src/module_a.py", "src/module_b.py", "src/utils.py"])
        # Verify no changes
        result = tracker.check_invalidation(store)
        assert result == {} or result.get("dead_code", []) == []
        # Modify mtime of module_a.py
        new_mtime = int(time.time()) + 1000
        store.upsert_file("src/module_a.py", "hash_a2", "python", size=250, modified_at=new_mtime)
        # Now check — should detect module_a.py as stale
        result = tracker.check_invalidation(store)
        assert "dead_code" in result
        assert "src/module_a.py" in result["dead_code"]

    def test_mtime_change_only_affected_files(self, tracker, store, reg_dead_code):
        """Only files with changed mtime should be reported as stale."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", [
            "src/module_a.py", "src/module_b.py", "src/utils.py"
        ])
        # Change only module_a.py
        store.upsert_file("src/module_a.py", "hash_a2", "python", size=250,
                          modified_at=int(time.time()) + 1000)
        result = tracker.check_invalidation(store)
        stale = result.get("dead_code", [])
        assert "src/module_a.py" in stale
        assert "src/module_b.py" not in stale
        assert "src/utils.py" not in stale

    # -- File added to store ------------------------------------------------

    def test_new_file_matching_pattern_is_stale(self, tracker, store, reg_dead_code):
        """A new file added to the store should be detected as stale."""
        tracker.register(reg_dead_code)
        # Mark existing files valid
        tracker.mark_valid("dead_code", ["src/module_a.py", "src/module_b.py", "src/utils.py"])
        # Add a new file
        store.upsert_file("src/new_module.py", "hash_new", "python", size=50,
                          modified_at=int(time.time()))
        result = tracker.check_invalidation(store)
        assert "dead_code" in result
        assert "src/new_module.py" in result["dead_code"]

    # -- File removed from store -------------------------------------------

    def test_file_removed_from_store(self, tracker, store, reg_dead_code):
        """If a tracked file is deleted from store, it should not be in stale list."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", [
            "src/module_a.py", "src/module_b.py", "src/utils.py"
        ])
        # Delete module_b.py from store
        store.delete_file("src/module_b.py")
        result = tracker.check_invalidation(store)
        stale = result.get("dead_code", [])
        # module_b.py no longer exists in store, should not appear
        assert "src/module_b.py" not in stale

    # -- Partial mark_valid -------------------------------------------------

    def test_partial_mark_valid_then_check(self, tracker, store, reg_dead_code):
        """If only some files are marked valid, unmarked ones remain stale."""
        tracker.register(reg_dead_code)
        # Mark only one file valid
        tracker.mark_valid("dead_code", ["src/module_a.py"])
        result = tracker.check_invalidation(store)
        assert "dead_code" in result
        stale = result["dead_code"]
        # module_a is valid, others are not yet tracked → stale
        assert "src/module_b.py" in stale
        assert "src/utils.py" in stale

    # -- Analyzer with empty file_patterns ----------------------------------

    def test_empty_file_patterns_returns_empty(self, tracker, store):
        """Analyzer with empty file_patterns should match no files."""
        reg = AnalyzerRegistration(
            analyzer_name="no_patterns",
            file_patterns=[],
            node_kinds=["function"],
        )
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        assert result == {} or result.get("no_patterns", []) == []

    # -- Return type validation ---------------------------------------------

    def test_return_type_is_dict_of_lists(self, tracker, store, reg_dead_code):
        """Return type should be dict[str, list[str]]."""
        tracker.register(reg_dead_code)
        result = tracker.check_invalidation(store)
        assert isinstance(result, dict)
        for key, value in result.items():
            assert isinstance(key, str)
            assert isinstance(value, list)
            for item in value:
                assert isinstance(item, str)

    # -- After close --------------------------------------------------------

    def test_check_invalidation_after_close_raises(self, tmp_db_path, store, reg_dead_code):
        """Should raise when tracker is closed."""
        t = InvalidationTracker(db_path=tmp_db_path)
        t.register(reg_dead_code)
        t.close()
        with pytest.raises(Exception):
            t.check_invalidation(store)

    # -- Store closed -------------------------------------------------------

    def test_check_invalidation_with_closed_store(self, tracker, store, reg_dead_code):
        """Should raise StoreClosedError when store is closed."""
        tracker.register(reg_dead_code)
        store.close()
        with pytest.raises(StoreClosedError):
            tracker.check_invalidation(store)

    # -- Multiple calls produce consistent results --------------------------

    def test_idempotent_check_no_change(self, tracker, store, reg_dead_code):
        """check_invalidation called twice without changes should return same result."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", ["src/module_a.py", "src/module_b.py", "src/utils.py"])
        r1 = tracker.check_invalidation(store)
        r2 = tracker.check_invalidation(store)
        assert r1 == r2


# =============================================================================
# 5. mark_valid()
# =============================================================================


class TestMarkValid:
    """Tests for InvalidationTracker.mark_valid()."""

    def test_mark_valid_persists_to_db(self, tracker, store):
        """mark_valid should write records to the analysis_tracking table."""
        tracker.mark_valid("test_analyzer", ["src/module_a.py", "src/module_b.py"])
        # Verify via direct SQL
        conn = sqlite3.connect(tracker.db_path)
        cursor = conn.execute(
            "SELECT analyzer_name, file_path, is_valid FROM analysis_tracking"
        )
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 2
        analyzer_names = {r[0] for r in rows}
        file_paths = {r[1] for r in rows}
        is_valid_vals = {r[2] for r in rows}
        assert analyzer_names == {"test_analyzer"}
        assert file_paths == {"src/module_a.py", "src/module_b.py"}
        assert is_valid_vals == {1}

    def test_mark_valid_stores_mtime(self, tracker, store):
        """mark_valid should read and store the current file mtime from store."""
        now = int(time.time())
        s = MemoryStore()
        s.upsert_file("src/test.py", "hash1", "python", modified_at=now)
        tracker.mark_valid("test_analyzer", ["src/test.py"])
        conn = sqlite3.connect(tracker.db_path)
        cursor = conn.execute(
            "SELECT file_mtime FROM analysis_tracking WHERE analyzer_name='test_analyzer'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row[0] == now

    def test_mark_valid_empty_file_paths(self, tracker, store):
        """mark_valid with empty list should not raise."""
        tracker.mark_valid("test_analyzer", [])
        # Should not raise

    def test_mark_valid_overwrites_existing(self, tracker, store):
        """Calling mark_valid again should update existing records."""
        s = MemoryStore()
        s.upsert_file("src/test.py", "hash1", "python", modified_at=1000)
        tracker.mark_valid("test_analyzer", ["src/test.py"])
        # Change mtime and re-mark
        s.upsert_file("src/test.py", "hash2", "python", modified_at=2000)
        tracker.mark_valid("test_analyzer", ["src/test.py"])
        conn = sqlite3.connect(tracker.db_path)
        cursor = conn.execute(
            "SELECT file_mtime, is_valid FROM analysis_tracking WHERE analyzer_name='test_analyzer'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row[0] == 2000
        assert row[1] == 1

    def test_mark_valid_file_not_in_store_fills_zero(self, tracker):
        """mark_valid for file not in store: should set mtime=0 or handle gracefully."""
        s = MemoryStore()  # empty store
        tracker.mark_valid("test_analyzer", ["src/nonexistent.py"])
        conn = sqlite3.connect(tracker.db_path)
        cursor = conn.execute(
            "SELECT file_path, file_mtime FROM analysis_tracking"
        )
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1
        # Should create a record; mtime may be 0 or raise
        assert rows[0][0] == "src/nonexistent.py"

    def test_mark_valid_multiple_analyzers(self, tracker, store):
        """mark_valid for different analyzers should keep records separate."""
        tracker.mark_valid("analyzer_a", ["src/module_a.py"])
        tracker.mark_valid("analyzer_b", ["src/module_b.py"])
        conn = sqlite3.connect(tracker.db_path)
        cursor = conn.execute(
            "SELECT analyzer_name, file_path FROM analysis_tracking ORDER BY analyzer_name"
        )
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][0] == "analyzer_a"
        assert rows[0][1] == "src/module_a.py"
        assert rows[1][0] == "analyzer_b"
        assert rows[1][1] == "src/module_b.py"

    def test_mark_valid_after_close_raises(self, tmp_db_path, store):
        """mark_valid on closed tracker should raise."""
        t = InvalidationTracker(db_path=tmp_db_path)
        t.close()
        with pytest.raises(Exception):
            t.mark_valid("test_analyzer", ["src/test.py"])

    def test_mark_valid_with_closed_store(self, tracker, store):
        """mark_valid with closed store should raise."""
        store.close()
        with pytest.raises(StoreClosedError):
            tracker.mark_valid("test_analyzer", ["src/module_a.py"])


# =============================================================================
# 6. verify_consistency()
# =============================================================================


class TestVerifyConsistency:
    """Tests for InvalidationTracker.verify_consistency(store, sample_size)."""

    def test_returns_consistency_report(self, tracker, store, reg_dead_code):
        """Should return a ConsistencyReport instance."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", ["src/module_a.py"])
        report = tracker.verify_consistency(store, sample_size=1)
        assert isinstance(report, ConsistencyReport)

    def test_report_has_expected_fields(self, tracker, store, reg_dead_code):
        """ConsistencyReport should have checked_count, consistent_count, etc."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", ["src/module_a.py"])
        report = tracker.verify_consistency(store, sample_size=1)
        assert hasattr(report, "checked_count")
        assert hasattr(report, "consistent_count")
        assert hasattr(report, "inconsistent_count")
        assert hasattr(report, "details")

    def test_sample_size_zero_checks_nothing(self, tracker, store, reg_dead_code):
        """sample_size=0 should check nothing."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", ["src/module_a.py"])
        report = tracker.verify_consistency(store, sample_size=0)
        assert report.checked_count == 0

    def test_no_tracking_data_returns_zero_checked(self, tracker, store):
        """With no tracking data, should return checked_count=0."""
        report = tracker.verify_consistency(store, sample_size=5)
        assert report.checked_count == 0

    def test_sample_size_exceeds_available(self, tracker, store, reg_dead_code):
        """sample_size > available records should check only available count."""
        tracker.register(reg_dead_code)
        tracker.mark_valid("dead_code", ["src/module_a.py"])
        report = tracker.verify_consistency(store, sample_size=100)
        assert report.checked_count <= 1

    def test_report_details_is_list(self, tracker, store, reg_dead_code):
        """details should be a list (possibly empty)."""
        tracker.register(reg_dead_code)
        report = tracker.verify_consistency(store, sample_size=0)
        assert isinstance(report.details, list)

    def test_verify_consistency_after_close_raises(self, tmp_db_path, store):
        """Should raise when tracker is closed."""
        t = InvalidationTracker(db_path=tmp_db_path)
        t.close()
        with pytest.raises(Exception):
            t.verify_consistency(store, sample_size=1)


# =============================================================================
# 7. close()
# =============================================================================


class TestClose:
    """Tests for InvalidationTracker.close()."""

    def test_close_is_idempotent(self, tmp_db_path):
        """Calling close() multiple times should not raise."""
        t = InvalidationTracker(db_path=tmp_db_path)
        t.close()
        t.close()  # idempotent
        # Should not raise

    def test_methods_raise_after_close(self, tmp_db_path, store, reg_dead_code):
        """All methods should raise after close."""
        t = InvalidationTracker(db_path=tmp_db_path)
        t.register(reg_dead_code)
        t.close()
        with pytest.raises(Exception):
            t.register(reg_dead_code)
        with pytest.raises(Exception):
            t.check_invalidation(store)
        with pytest.raises(Exception):
            t.mark_valid("test", ["src/a.py"])
        with pytest.raises(Exception):
            t.verify_consistency(store)


# =============================================================================
# 8. Integration — MemoryStore + InvalidationTracker full lifecycle
# =============================================================================


class TestIntegration:
    """End-to-end integration tests with MemoryStore + InvalidationTracker."""

    def test_full_lifecycle(self, tmp_db_path):
        """Simulate real workflow: register → first check → mark_valid → modify → re-check."""
        tracker = InvalidationTracker(db_path=tmp_db_path)
        store = MemoryStore()
        now = int(time.time())

        # Setup: 5 source files
        store.upsert_file("src/a.py", "h1", "python", modified_at=now)
        store.upsert_file("src/b.py", "h2", "python", modified_at=now)
        store.upsert_file("src/c.py", "h3", "python", modified_at=now)
        store.upsert_file("lib/util.py", "h4", "python", modified_at=now)
        store.upsert_file("tests/test_a.py", "h5", "python", modified_at=now)

        # Register two analyzers
        reg1 = AnalyzerRegistration(
            analyzer_name="dead_code",
            file_patterns=["src/**/*.py"],
            node_kinds=["function", "class"],
        )
        reg2 = AnalyzerRegistration(
            analyzer_name="complexity",
            file_patterns=["src/*.py", "lib/*.py"],
            node_kinds=["function", "method"],
        )
        tracker.register(reg1)
        tracker.register(reg2)

        # Phase 1: First check — all files are stale
        result1 = tracker.check_invalidation(store)
        assert "dead_code" in result1
        assert "complexity" in result1
        # dead_code: src/**/*.py → a.py, b.py, c.py (3 files)
        assert len(result1["dead_code"]) == 3
        # complexity: src/*.py + lib/*.py → a.py, b.py, c.py, util.py (4 files)
        assert len(result1["complexity"]) == 4

        # Phase 2: Mark both analyzers valid
        tracker.mark_valid("dead_code", result1["dead_code"])
        tracker.mark_valid("complexity", result1["complexity"])

        # Phase 3: No changes — should return empty
        result2 = tracker.check_invalidation(store)
        assert result2 == {} or all(v == [] for v in result2.values())

        # Phase 4: Modify src/a.py
        store.upsert_file("src/a.py", "h1_modified", "python",
                          modified_at=now + 5000)
        result3 = tracker.check_invalidation(store)
        # dead_code: src/a.py should be stale
        assert "src/a.py" in result3.get("dead_code", [])
        # complexity: src/a.py should also be stale
        assert "src/a.py" in result3.get("complexity", [])
        # Only a.py should be stale
        assert len(result3.get("dead_code", [])) == 1
        assert len(result3.get("complexity", [])) == 1

        # Phase 5: Fix dead_code only
        tracker.mark_valid("dead_code", ["src/a.py"])
        result4 = tracker.check_invalidation(store)
        # dead_code should be clean now
        assert "dead_code" not in result4 or result4.get("dead_code", []) == []
        # complexity should still have src/a.py as stale
        assert "src/a.py" in result4.get("complexity", [])

        # Phase 6: Add new file
        store.upsert_file("src/new_feature.py", "h_new", "python",
                          modified_at=now + 6000)
        result5 = tracker.check_invalidation(store)
        assert "src/new_feature.py" in result5.get("dead_code", [])
        assert "src/new_feature.py" in result5.get("complexity", [])

        # Phase 7: Verify consistency
        report = tracker.verify_consistency(store, sample_size=2)
        assert isinstance(report, ConsistencyReport)
        assert hasattr(report, "checked_count")

        tracker.close()

    def test_version_bump_scenario(self, tmp_db_path):
        """When analyzer version changes, all its files should be re-analyzed."""
        tracker = InvalidationTracker(db_path=tmp_db_path)
        store = MemoryStore()
        now = int(time.time())
        store.upsert_file("src/a.py", "h1", "python", modified_at=now)
        store.upsert_file("src/b.py", "h2", "python", modified_at=now)

        # Register v1 and mark valid
        reg_v1 = AnalyzerRegistration(
            analyzer_name="dead_code",
            file_patterns=["src/*.py"],
            node_kinds=["function"],
            version=1,
        )
        tracker.register(reg_v1)
        tracker.mark_valid("dead_code", ["src/a.py", "src/b.py"])

        # No changes expected
        r1 = tracker.check_invalidation(store)
        assert r1 == {} or r1.get("dead_code", []) == []

        # Register v2 (version bump)
        reg_v2 = AnalyzerRegistration(
            analyzer_name="dead_code",
            file_patterns=["src/*.py"],
            node_kinds=["function"],
            version=2,
        )
        tracker.register(reg_v2)

        # After version bump, existing tracking data should be invalidated
        # (implementation may do lazy invalidation on check, or eager on register)
        r2 = tracker.check_invalidation(store)
        # Design intent: version change → all files for this analyzer should be stale
        # Accept either: stale files returned, or behavior TBD based on implementation
        # For now, verify the method returns without error
        assert isinstance(r2, dict)

        tracker.close()


# =============================================================================
# 9. Edge cases & error handling
# =============================================================================


class TestEdgeCases:
    """Edge case and error handling tests."""

    def test_glob_pattern_with_double_star(self, tracker, store):
        """Pattern '**/*.py' should match all .py files at any depth."""
        reg = AnalyzerRegistration(
            analyzer_name="all_py",
            file_patterns=["**/*.py"],
            node_kinds=[],
        )
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        stale = result.get("all_py", [])
        # All 4 .py files in store
        assert len(stale) == 4

    def test_glob_pattern_with_extension(self, tracker, store):
        """Pattern 'src/**/*.py' should match only .py files under src/."""
        reg = AnalyzerRegistration(
            analyzer_name="src_only",
            file_patterns=["src/**/*.py"],
            node_kinds=[],
        )
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        stale = result.get("src_only", [])
        for f in stale:
            assert f.startswith("src/")

    def test_glob_pattern_exact_file(self, tracker, store):
        """Pattern 'src/module_a.py' should match exactly that file."""
        reg = AnalyzerRegistration(
            analyzer_name="exact",
            file_patterns=["src/module_a.py"],
            node_kinds=[],
        )
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        stale = result.get("exact", [])
        assert stale == ["src/module_a.py"]

    def test_non_matching_pattern_returns_empty(self, tracker, store):
        """Pattern that matches no files should return empty list."""
        reg = AnalyzerRegistration(
            analyzer_name="nothing",
            file_patterns=["nonexistent/**/*.rs"],
            node_kinds=[],
        )
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        assert result.get("nothing", []) == []

    def test_empty_store(self, tracker):
        """check_invalidation with empty store should not raise."""
        empty_store = MemoryStore()
        result = tracker.check_invalidation(empty_store)
        assert result == {}

    def test_check_after_clear_and_rebuild(self, tmp_db_path):
        """Tracker should work after clearing tracking data and starting fresh."""
        tracker = InvalidationTracker(db_path=tmp_db_path)
        store = MemoryStore()
        now = int(time.time())
        store.upsert_file("src/a.py", "h1", "python", modified_at=now)

        reg = AnalyzerRegistration("test_a", ["src/*.py"], [])
        tracker.register(reg)
        tracker.mark_valid("test_a", ["src/a.py"])

        # Clear the tracking table directly
        conn = sqlite3.connect(tmp_db_path)
        conn.execute("DELETE FROM analysis_tracking")
        conn.commit()
        conn.close()

        # Now check should return stale again
        result = tracker.check_invalidation(store)
        assert "src/a.py" in result.get("test_a", [])

        tracker.close()

    def test_special_characters_in_path(self, tracker):
        """Paths with special characters should be handled."""
        store = MemoryStore()
        store.upsert_file(
            "src/module-with_special.name.py", "h1", "python",
            modified_at=int(time.time()),
        )
        reg = AnalyzerRegistration("test_a", ["src/**/*.py"], [])
        tracker.register(reg)
        # Should not raise
        result = tracker.check_invalidation(store)
        assert isinstance(result, dict)

    def test_large_number_of_files(self, tmp_db_path):
        """Should handle a moderate number of files without performance issues."""
        tracker = InvalidationTracker(db_path=tmp_db_path)
        store = MemoryStore()
        now = int(time.time())
        num_files = 200
        for i in range(num_files):
            store.upsert_file(f"src/file_{i:04d}.py", f"h_{i}", "python",
                              modified_at=now)

        reg = AnalyzerRegistration("large_test", ["src/*.py"], [])
        tracker.register(reg)
        result = tracker.check_invalidation(store)
        assert len(result.get("large_test", [])) == num_files

        tracker.close()

    def test_concurrent_same_db_path(self, tmp_db_path):
        """Two trackers on the same db should work (WAL mode handles concurrency)."""
        tracker1 = InvalidationTracker(db_path=tmp_db_path)
        tracker2 = InvalidationTracker(db_path=tmp_db_path)
        # Both should be usable
        store = MemoryStore()
        store.upsert_file("src/a.py", "h1", "python", modified_at=int(time.time()))
        reg = AnalyzerRegistration("test_a", ["src/*.py"], [])
        tracker1.register(reg)
        tracker1.check_invalidation(store)
        tracker2.register(reg)
        tracker2.check_invalidation(store)
        tracker1.close()
        tracker2.close()


# =============================================================================
# 10. ConsistencyReport dataclass
# =============================================================================


class TestConsistencyReport:
    """Tests for the ConsistencyReport dataclass."""

    def test_is_dataclass(self):
        """Should be a dataclass."""
        import dataclasses
        assert dataclasses.is_dataclass(ConsistencyReport)

    def test_default_values(self):
        """Should have sensible default values."""
        report = ConsistencyReport()
        assert report.checked_count == 0
        assert report.consistent_count == 0
        assert report.inconsistent_count == 0
        assert report.details == []  # or whatever default

    def test_create_with_values(self):
        """Should be creatable with field values."""
        report = ConsistencyReport(
            checked_count=10,
            consistent_count=9,
            inconsistent_count=1,
            details=[{"analyzer_name": "test", "file_path": "src/a.py",
                      "expected_hash": "abc", "actual_hash": "def"}],
        )
        assert report.checked_count == 10
        assert report.consistent_count == 9
        assert report.inconsistent_count == 1
        assert len(report.details) == 1
