"""Database connection management — internal module for SqliteStore.

Wraps sqlite3 connection with WAL mode, performance pragmas, and a
prepared-statement LRU cache. Not exported through __init__.py.
"""

import sqlite3


class ConnectionManager:
    """Manages a SQLite connection with WAL mode and prepared statement cache.

    Designed for internal use by SqliteStore. Handles connection lifecycle,
    pragma configuration, and LRU-cached prepared statements.

    Attributes:
        db_path: Path to the SQLite database file.
        conn: The underlying sqlite3.Connection, or None after close().
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        # Prepared-statement LRU cache — dict insertion order gives eviction policy.
        self._stmt_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Pragma configuration
    # ------------------------------------------------------------------

    def _configure_pragmas(self) -> None:
        """Apply performance and safety PRAGMAs."""
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA cache_size = -64000")       # 64 MB
        self.conn.execute("PRAGMA temp_store = MEMORY")
        self.conn.execute("PRAGMA mmap_size = 268435456")     # 256 MB

    # ------------------------------------------------------------------
    # Prepared statement cache
    # ------------------------------------------------------------------

    def get_statement(self, key: str, sql: str) -> str:
        """Return cached SQL text for the given key.

        Python's sqlite3 module handles prepared-statement compilation
        internally (it caches the most recently used compiled statements
        transparently).  We cache the SQL text strings to enable LRU
        eviction of less-frequently-used query templates, avoiding
        repeated string construction in hot paths.

        The cache is LRU-style with a maximum of 50 entries.  When the
        cache is full the oldest entry (first inserted key) is evicted.

        Args:
            key: Cache key for the statement (e.g. ``"get_node"``).
            sql: SQL text to cache and return.

        Returns:
            The SQL text string.  Callers use it with
            ``conn.execute(sql, params)`` to benefit from sqlite3's
            internal statement cache.
        """
        if key not in self._stmt_cache:
            if len(self._stmt_cache) >= 50:
                oldest = next(iter(self._stmt_cache))
                del self._stmt_cache[oldest]
            self._stmt_cache[key] = sql
        return self._stmt_cache[key]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection.  Idempotent — safe to call
        multiple times.
        """
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self._stmt_cache.clear()
