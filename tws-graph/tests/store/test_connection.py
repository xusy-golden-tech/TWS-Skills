"""Tests for store.connection — ConnectionManager for SQLite connectivity."""

import sqlite3
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Create a temporary database path in the tmp_path."""
    db_dir = tmp_path / "store_test"
    db_dir.mkdir()
    return str(db_dir / "test.db")


@pytest.fixture
def conn_mgr(temp_db_path: str):
    """Create a ConnectionManager with a temporary database."""
    from tws_graph.store.connection import ConnectionManager
    mgr = ConnectionManager(temp_db_path)
    yield mgr
    mgr.close()


# ---------------------------------------------------------------------------
# Connection creation
# ---------------------------------------------------------------------------

class TestConnectionCreation:
    """Tests for ConnectionManager.__init__ — connection setup and pragmas."""

    def test_creates_sqlite_file(self, temp_db_path: str):
        """The database file should be created on disk."""
        from tws_graph.store.connection import ConnectionManager
        mgr = ConnectionManager(temp_db_path)
        try:
            assert Path(temp_db_path).exists()
            assert Path(temp_db_path).stat().st_size > 0
        finally:
            mgr.close()

    def test_connection_is_not_none(self, conn_mgr):
        """The internal connection object should be set."""
        assert conn_mgr.conn is not None
        assert isinstance(conn_mgr.conn, sqlite3.Connection)

    def test_row_factory_is_sqlite_row(self, conn_mgr):
        """row_factory should be sqlite3.Row so queries return dict-like rows."""
        assert conn_mgr.conn.row_factory is sqlite3.Row

    def test_isolation_level_is_none(self, conn_mgr):
        """isolation_level=None enables autocommit mode."""
        assert conn_mgr.conn.isolation_level is None

    def test_can_execute_simple_query(self, conn_mgr):
        """Basic SQL execution should work after connection setup."""
        result = conn_mgr.conn.execute("SELECT 1 AS val").fetchone()
        assert result["val"] == 1


# ---------------------------------------------------------------------------
# Pragma verification
# ---------------------------------------------------------------------------

class TestPragmaConfiguration:
    """Verify that all required PRAGMAs are set correctly."""

    def test_wal_mode_enabled(self, conn_mgr):
        """journal_mode should be WAL."""
        row = conn_mgr.conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].upper() == "WAL"

    def test_foreign_keys_on(self, conn_mgr):
        """foreign_keys should be ON."""
        row = conn_mgr.conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_busy_timeout(self, conn_mgr):
        """busy_timeout should be 5000 ms."""
        row = conn_mgr.conn.execute("PRAGMA busy_timeout").fetchone()
        assert row[0] == 5000

    def test_synchronous_normal(self, conn_mgr):
        """synchronous should be NORMAL (1 for normal, 2 for full)."""
        row = conn_mgr.conn.execute("PRAGMA synchronous").fetchone()
        assert row[0] == 1  # 1 = NORMAL

    def test_cache_size(self, conn_mgr):
        """cache_size should be -64000 (64 MB negative = kibibytes)."""
        row = conn_mgr.conn.execute("PRAGMA cache_size").fetchone()
        assert row[0] == -64000

    def test_temp_store_memory(self, conn_mgr):
        """temp_store should be MEMORY (value 2)."""
        row = conn_mgr.conn.execute("PRAGMA temp_store").fetchone()
        assert row[0] == 2  # 2 = MEMORY

    def test_mmap_size(self, conn_mgr):
        """mmap_size should be 268435456 (256 MB)."""
        row = conn_mgr.conn.execute("PRAGMA mmap_size").fetchone()
        assert row[0] == 268435456

    def test_threads(self, conn_mgr):
        """threads should be 4 (allow up to 4 auxiliary worker threads)."""
        row = conn_mgr.conn.execute("PRAGMA threads").fetchone()
        assert row[0] == 4


# ---------------------------------------------------------------------------
# Statement cache
# ---------------------------------------------------------------------------

class TestStatementCache:
    """Tests for get_statement — SQL-text LRU cache.

    Python's sqlite3 handles compiled-statement caching internally,
    so the ConnectionManager caches SQL text strings for hot-path reuse.
    """

    def test_returns_string(self, conn_mgr):
        """get_statement should return a SQL string."""
        sql = conn_mgr.get_statement(
            "create",
            "CREATE TABLE IF NOT EXISTS test1 (id INTEGER)"
        )
        assert isinstance(sql, str)
        assert "CREATE TABLE" in sql

    def test_caches_repeated_keys(self, conn_mgr):
        """Same key should return the same cached string object."""
        sql1 = conn_mgr.get_statement(
            "insert",
            "INSERT INTO test2 (val) VALUES (?)"
        )
        sql2 = conn_mgr.get_statement(
            "insert",
            "INSERT INTO test2 (val) VALUES (?)"
        )
        assert sql1 is sql2

    def test_different_keys_different_entries(self, conn_mgr):
        """Different keys should produce different cache entries."""
        sql_a = conn_mgr.get_statement(
            "ins_a",
            "INSERT INTO test3 (val) VALUES (?)"
        )
        sql_b = conn_mgr.get_statement(
            "ins_b",
            "INSERT INTO test4 (val) VALUES (?)"
        )
        assert sql_a != sql_b

    def test_cached_sql_can_be_executed(self, conn_mgr):
        """Cached SQL text should be executable via conn.execute()."""
        conn_mgr.conn.execute(
            "CREATE TABLE IF NOT EXISTS test_exec (id INTEGER PRIMARY KEY, name TEXT)"
        )
        insert_sql = conn_mgr.get_statement(
            "insert_exec",
            "INSERT INTO test_exec (name) VALUES (?)"
        )
        conn_mgr.conn.execute(insert_sql, ("alpha",))
        conn_mgr.conn.execute(insert_sql, ("beta",))

        rows = conn_mgr.conn.execute(
            "SELECT name FROM test_exec ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["name"] == "alpha"
        assert rows[1]["name"] == "beta"

    def test_cache_lru_eviction(self, conn_mgr):
        """When cache exceeds 50 entries, oldest should be evicted."""
        # Fill the cache with 52 entries to trigger eviction
        for i in range(52):
            conn_mgr.get_statement(
                f"stmt_{i}",
                f"SELECT {i}"
            )

        # After 52 inserts, cache should be at most 50
        assert len(conn_mgr._stmt_cache) <= 50

        # The first entries (stmt_0, stmt_1) should have been evicted
        assert "stmt_0" not in conn_mgr._stmt_cache
        assert "stmt_1" not in conn_mgr._stmt_cache

        # The most recent entry should still be present
        assert "stmt_51" in conn_mgr._stmt_cache


# ---------------------------------------------------------------------------
# Close / lifecycle
# ---------------------------------------------------------------------------

class TestClose:
    """Tests for ConnectionManager.close — connection termination."""

    def test_close_idempotent(self, temp_db_path: str):
        """Calling close() multiple times should not raise an exception."""
        from tws_graph.store.connection import ConnectionManager
        mgr = ConnectionManager(temp_db_path)
        mgr.close()
        # Second close — should be a no-op, not an error
        mgr.close()

    def test_close_then_close(self, temp_db_path: str):
        """Triple close should also be safe."""
        from tws_graph.store.connection import ConnectionManager
        mgr = ConnectionManager(temp_db_path)
        mgr.close()
        mgr.close()
        mgr.close()
        # No exception = pass

    def test_conn_set_to_none_after_close(self, temp_db_path: str):
        """After close(), internal conn reference should be None."""
        from tws_graph.store.connection import ConnectionManager
        mgr = ConnectionManager(temp_db_path)
        mgr.close()
        assert mgr.conn is None

    def test_operations_after_close_raise(self, temp_db_path: str):
        """Using the underlying connection after close should raise."""
        from tws_graph.store.connection import ConnectionManager
        mgr = ConnectionManager(temp_db_path)
        conn_ref = mgr.conn
        mgr.close()
        with pytest.raises(sqlite3.ProgrammingError):
            conn_ref.execute("SELECT 1")
