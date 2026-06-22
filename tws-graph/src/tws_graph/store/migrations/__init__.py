"""Versioned database migration system for SqliteStore.

MigrationRunner automatically discovers migration modules in this package,
sorts them by version number, and applies any that have not yet been
executed.  Each migration runs in its own transaction so that a failure
in migration N does not undo migrations 1..N-1.

Usage::

    conn = sqlite3.connect("index.db")
    runner = MigrationRunner(conn)
    applied = runner.migrate()
    print(f"Applied {applied} migrations")

Migration modules must export four attributes::

    VERSION     int          – monotonically increasing version number
    DESCRIPTION str          – human-readable description
    UP_SQL      str          – SQL to apply the migration
    DOWN_SQL    str          – SQL to revert (optional, may be empty)
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sqlite3
import time
import inspect


class MigrationRunner:
    """Apply pending database schema migrations in version order.

    Migrations are discovered by scanning the ``tws_graph.store.migrations``
    package for modules whose names match ``v<NNN>_*.py``.  Each module's
    ``VERSION`` attribute determines execution order (ascending).

    Pending migrations are those with a ``VERSION`` strictly greater than
    the maximum version recorded in the ``schema_versions`` table.  If the
    ``schema_versions`` table does not yet exist (brand-new database) the
    current version is treated as 0.

    Each migration is applied inside its own ``SAVEPOINT`` / ``RELEASE``
    boundary.  If a migration's ``UP_SQL`` raises an exception the savepoint
    is rolled back, the ``schema_versions`` row is NOT inserted, and the
    exception propagates to the caller.  Previously-applied migrations are
    unaffected.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialise MigrationRunner with a database connection.

        Args:
            conn: An open ``sqlite3.Connection``.  The runner never closes
                this connection — lifecycle management is the caller's
                responsibility.  ``isolation_level=None`` (autocommit) is
                recommended so that the runner can manage its own savepoints.
        """
        self.conn = conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def migrate(self) -> int:
        """Execute all pending migrations and return the count applied.

        Returns:
            Number of migrations that were applied during this call.
            0 if the database is already at the latest version.
        """
        current = self._get_current_version()
        pending = self._find_pending(current)

        if not pending:
            return 0

        for version, modname, module in pending:
            self._apply(module)

        return len(pending)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_current_version(self) -> int:
        """Return the highest migration version applied to the database.

        If the ``schema_versions`` table does not exist (brand-new or
        pre-v1 database) this method returns 0 without raising an error.
        """
        try:
            row = self.conn.execute(
                "SELECT MAX(version) FROM schema_versions"
            ).fetchone()
            return row[0] if row[0] is not None else 0
        except sqlite3.OperationalError:
            # schema_versions table does not exist yet — treat as v0
            return 0

    def _find_pending(
        self, current: int
    ) -> list[tuple[int, str, object]]:
        """Discover migration modules whose VERSION > *current*.

        Returns:
            A list of ``(version, module_name, module)`` tuples sorted by
            version ascending.
        """
        pkg_path = os.path.dirname(__file__)

        pending: list[tuple[int, str, object]] = []

        for _finder, modname, _ispkg in pkgutil.iter_modules([pkg_path]):
            # Only consider modules matching v<NNN>_* pattern
            if not modname.startswith("v"):
                continue
            try:
                version = int(modname[1:4])
            except (ValueError, IndexError):
                continue

            if version <= current:
                continue

            full_name = f"tws_graph.store.migrations.{modname}"
            try:
                module = importlib.import_module(full_name)
            except ImportError:
                # Module cannot be loaded — skip and continue
                continue

            # Validate the module has the required attributes
            if not hasattr(module, "VERSION"):
                continue
            if not hasattr(module, "UP_SQL"):
                continue

            pending.append((version, modname, module))

        # Sort by version ascending to guarantee ordered execution
        pending.sort(key=lambda x: x[0])
        return pending

    # ------------------------------------------------------------------
    # SQL splitting (uses sqlite3.complete_statement for correctness)
    # ------------------------------------------------------------------

    @staticmethod
    def _split_sql(sql: str) -> list[str]:
        """Split a multi-statement SQL string into individual statements.

        Uses ``sqlite3.complete_statement()`` to detect statement
        boundaries, which correctly handles compound statements such as
        ``CREATE TRIGGER … BEGIN … END;`` where semicolons appear inside
        the trigger body.

        Returns only non-empty, non-comment statements.
        """
        statements: list[str] = []
        buf = ""
        for line in sql.splitlines():
            buf += line + "\n"
            stripped = buf.strip()
            if stripped and sqlite3.complete_statement(stripped):
                # Verify it is not a pure-comment statement
                if not all(
                    l.strip().startswith("--") or not l.strip()
                    for l in stripped.splitlines()
                ):
                    statements.append(stripped.rstrip(";"))
                buf = ""

        # Catch any trailing SQL without a terminating semicolon
        trailing = buf.strip()
        if trailing and not all(
            l.strip().startswith("--") or not l.strip()
            for l in trailing.splitlines()
        ):
            statements.append(trailing.rstrip(";"))

        return statements

    # ------------------------------------------------------------------
    # Migration application
    # ------------------------------------------------------------------

    def _apply(self, module) -> None:
        """Execute a single migration inside a savepoint.

        SQL statements are split and executed individually so that the
        savepoint remains active throughout.  On success the version row
        is inserted into ``schema_versions``.

        Args:
            module: A migration module with ``VERSION``, ``DESCRIPTION``,
                and ``UP_SQL`` attributes.

        Raises:
            sqlite3.Error: If the migration SQL fails.  The savepoint is
                rolled back before re-raising.
        """
        version = getattr(module, "VERSION", 0)
        description = getattr(module, "DESCRIPTION", "")
        up_sql = getattr(module, "UP_SQL", "")

        sp_name = f"migrate_v{version}"

        try:
            self.conn.execute(f"SAVEPOINT {sp_name}")

            for stmt in self._split_sql(up_sql):
                self.conn.execute(stmt)

            now_ms = int(time.time() * 1000)
            self.conn.execute(
                "INSERT INTO schema_versions (version, applied_at, description) "
                "VALUES (?, ?, ?)",
                (version, now_ms, description),
            )

            self.conn.execute(f"RELEASE {sp_name}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO {sp_name}")
            raise
