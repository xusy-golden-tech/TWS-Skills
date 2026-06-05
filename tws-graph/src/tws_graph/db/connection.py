"""Database connection management — mirrors CodeGraph's DatabaseConnection."""

import sqlite3
import os
import time
import pathlib


class DatabaseConnection:
    """Manages a SQLite connection with WAL mode and foreign keys enabled."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._configure_pragmas()

    def _configure_pragmas(self):
        """Apply performance and safety PRAGMAs (ported from CodeGraph)."""
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA cache_size = -64000")   # 64MB page cache
        self.conn.execute("PRAGMA temp_store = MEMORY")

    @classmethod
    def initialize(cls, db_path: str) -> "DatabaseConnection":
        """Create a new database with schema, or open existing."""
        db_dir = os.path.dirname(db_path)
        if db_dir:
            pathlib.Path(db_dir).mkdir(parents=True, exist_ok=True)

        is_new = not os.path.exists(db_path)
        conn = cls(db_path)

        if is_new or cls._needs_migration(conn):
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.conn.executescript(f.read())

        cls._run_migrations(conn)
        return conn

    @classmethod
    def open(cls, db_path: str) -> "DatabaseConnection":
        """Open an existing database. Raises FileNotFoundError if missing."""
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")
        return cls(db_path)

    @staticmethod
    def _needs_migration(conn: "DatabaseConnection") -> bool:
        """Check if schema_versions table exists."""
        try:
            conn.conn.execute("SELECT 1 FROM schema_versions LIMIT 1")
            return False
        except sqlite3.OperationalError:
            return True

    @staticmethod
    def _run_migrations(conn: "DatabaseConnection") -> None:
        """Apply any pending schema migrations."""
        db = conn.conn
        try:
            db.execute("SELECT is_external FROM unresolved_refs LIMIT 1")
        except sqlite3.OperationalError:
            db.execute(
                "ALTER TABLE unresolved_refs ADD COLUMN is_external INTEGER NOT NULL DEFAULT 0"
            )
        db.execute("""
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (2, CAST(strftime('%s', 'now') AS INTEGER) * 1000, 'Add is_external to unresolved_refs')
        """)

    def optimize(self):
        """Run maintenance after bulk writes (ported from CodeGraph)."""
        self.conn.execute("PRAGMA optimize")
        self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
