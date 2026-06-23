"""Invalidation tracker for analysis result freshness.

Tracks which analysis results are stale by comparing file mtimes from the
Store against a dedicated SQLite tracking table. Each analyzer registers
its file patterns, and check_invalidation() returns the set of files that
need re-analysis.

Design: P6 edge invalidation (design-p6-edge-inval.md).

Key design decisions:
- ``file_mtime`` stores the Store mtime as *last seen by check_invalidation*
  (the baseline).  ``mark_valid`` does NOT write mtime — it only flips the
  ``is_valid`` flag.  This way the next ``check_invalidation`` can detect
  genuine file changes by comparing the current Store mtime against the
  stored baseline.

- ``mark_valid`` for a *new* tracking record (no prior record) writes
  ``file_mtime = 0`` as a sentinel.  The next ``check_invalidation`` treats
  this as "first baseline" and updates the mtime without marking stale.
"""

from __future__ import annotations

import fnmatch
import sqlite3
import time
from dataclasses import dataclass, field

from tws_graph.store.interface import Store
from tws_graph.store.connection import configure_connection


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class AnalyzerRegistration:
    """Analyzer registration info — declares what files an analyzer depends on.

    Attributes:
        analyzer_name: Unique analyzer identifier (e.g. "dead_code", "complexity").
        file_patterns: Glob patterns matching dependent files (e.g. ["src/**/*.py"]).
        node_kinds: Node kinds the analyzer depends on (e.g. ["function", "class"]).
        version: Semantic version — bump to trigger full re-analysis.
    """

    analyzer_name: str
    file_patterns: list[str]
    node_kinds: list[str]
    version: int = 1


@dataclass
class ConsistencyReport:
    """Result of a self-consistency check.

    Attributes:
        checked_count: Number of tracking records sampled.
        consistent_count: Number whose stored mtime still matches the store.
        inconsistent_count: Number whose stored mtime has diverged.
        details: List of dicts with per-record inconsistency info.
    """

    checked_count: int = 0
    consistent_count: int = 0
    inconsistent_count: int = 0
    details: list = field(default_factory=list)


# =============================================================================
# InvalidationTracker
# =============================================================================


class InvalidationTracker:
    """Tracks analysis result validity by comparing file mtimes.

    Uses an independent SQLite connection (WAL mode) to a dedicated tracking
    database.  The Store provides file stats; the tracker maintains the mapping
    between analyzers, files, and their last-known mtime baselines.

    Lifecycle::

        tracker = InvalidationTracker(db_path)
        tracker.register(AnalyzerRegistration(...))
        stale = tracker.check_invalidation(store)   # -> {analyzer: [paths]}
        # ... re-analyse stale files ...
        tracker.mark_valid("analyzer", [...])       # flip is_valid=1
        tracker.close()
    """

    # Schema sentinel: a file_mtime of 0 means "no baseline yet — set by
    # mark_valid before the first check_invalidation".
    _MTIME_SENTINEL: int = 0

    def __init__(
        self, db_path: str, table_name: str = "analysis_tracking"
    ) -> None:
        self.db_path = db_path
        self.table_name = table_name
        self._closed = False
        self._registrations: dict[str, AnalyzerRegistration] = {}

        # Independent connection with performance pragmas for concurrent readers.
        self._conn = sqlite3.connect(db_path)
        configure_connection(self._conn)
        self._conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzer_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_mtime INTEGER NOT NULL DEFAULT 0,
                computed_at INTEGER NOT NULL DEFAULT 0,
                is_valid INTEGER DEFAULT 1,
                UNIQUE(analyzer_name, file_path)
            )"""
        )
        self._conn.commit()

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(self, registration: AnalyzerRegistration) -> None:
        """Register an analyzer's dependency declaration (idempotent UPSERT).

        Re-registering with the same name overwrites the previous registration.
        If the version has changed, all existing tracking rows for this
        analyzer are invalidated (is_valid = 0).
        """
        self._check_open()
        existing = self._registrations.get(registration.analyzer_name)
        if existing is not None and existing.version != registration.version:
            self._conn.execute(
                f"UPDATE {self.table_name} SET is_valid = 0 "
                "WHERE analyzer_name = ?",
                (registration.analyzer_name,),
            )
            self._conn.commit()
        self._registrations[registration.analyzer_name] = registration

    # -------------------------------------------------------------------------
    # Invalidation check
    # -------------------------------------------------------------------------

    def check_invalidation(self, store: Store) -> dict[str, list[str]]:
        """Check which tracked files are stale.

        Compares each matched file's current *Store* mtime against the
        baseline recorded during the previous ``check_invalidation`` (stored
        in the ``file_mtime`` column).  A change in mtime invalidates the
        analysis result.

        On first run (no prior tracking data) every matched file is returned
        and a tracking record with ``is_valid=0`` is inserted.

        Args:
            store: A Store instance providing ``get_file_stats()``.

        Returns:
            Dict mapping analyzer names to lists of stale file paths.
            Returns ``{}`` when nothing is stale (or no analyzers are
            registered).
        """
        self._check_open()

        file_stats = store.get_file_stats()  # {path: (size, modified_at)}
        store_paths = set(file_stats.keys())

        result: dict[str, list[str]] = {}

        for name, reg in self._registrations.items():
            matched = self._match_patterns(reg.file_patterns, store_paths)
            stale_files: list[str] = []

            for path in sorted(matched):
                store_mtime = file_stats[path][1]

                cur = self._conn.execute(
                    f"SELECT file_mtime, is_valid FROM {self.table_name} "
                    "WHERE analyzer_name = ? AND file_path = ?",
                    (name, path),
                )
                row = cur.fetchone()

                if row is None:
                    # No tracking record yet — insert baseline and mark stale.
                    self._conn.execute(
                        f"INSERT OR REPLACE INTO {self.table_name} "
                        "(analyzer_name, file_path, file_mtime, computed_at, is_valid) "
                        "VALUES (?, ?, ?, ?, 0)",
                        (name, path, store_mtime, int(time.time())),
                    )
                    stale_files.append(path)
                else:
                    tracked_mtime, is_valid = row
                    if tracked_mtime == self._MTIME_SENTINEL:
                        # Sentinel: mark_valid created this record before any
                        # check_invalidation ever saw it.  Set the baseline
                        # now without invalidating.
                        self._conn.execute(
                            f"UPDATE {self.table_name} "
                            "SET file_mtime = ?, computed_at = ? "
                            "WHERE analyzer_name = ? AND file_path = ?",
                            (store_mtime, int(time.time()), name, path),
                        )
                        # Remain valid — carry on.
                        if not is_valid:
                            stale_files.append(path)
                    elif tracked_mtime != store_mtime:
                        # File mtime changed since last baseline — invalidate.
                        self._conn.execute(
                            f"UPDATE {self.table_name} "
                            "SET file_mtime = ?, is_valid = 0, computed_at = ? "
                            "WHERE analyzer_name = ? AND file_path = ?",
                            (store_mtime, int(time.time()), name, path),
                        )
                        stale_files.append(path)
                    elif not is_valid:
                        # Baseline unchanged but still marked invalid.
                        stale_files.append(path)
                    # else: baseline matches AND is_valid=1 — still fresh.

            self._conn.commit()

            if stale_files:
                result[name] = stale_files

        return result

    # -------------------------------------------------------------------------
    # Mark valid
    # -------------------------------------------------------------------------

    def mark_valid(self, analyzer_name: str, file_paths: list[str]) -> None:
        """Record that an analyzer has successfully re-computed results for
        the given *file_paths* and mark them as up-to-date.

        Does **not** write file-mtime — that is handled by
        ``check_invalidation``.  This method only flips the ``is_valid`` flag.
        For files without a prior tracking record a placeholder row with
        ``file_mtime = 0`` (sentinel) is inserted.

        Args:
            analyzer_name: Name of the analyzer that completed.
            file_paths: List of file paths to mark as up-to-date.

        Raises:
            ValueError: If *analyzer_name* has not been registered via
                ``register()``.
        """
        self._check_open()

        if analyzer_name not in self._registrations:
            raise ValueError(
                f"Analyzer '{analyzer_name}' is not registered. "
                f"Call register() before mark_valid()."
            )

        current_time = int(time.time())

        for path in file_paths:
            # UPSERT: if a row exists flip is_valid only;
            # otherwise create a sentinel entry.
            cur = self._conn.execute(
                f"SELECT id FROM {self.table_name} "
                "WHERE analyzer_name = ? AND file_path = ?",
                (analyzer_name, path),
            )
            if cur.fetchone() is not None:
                self._conn.execute(
                    f"UPDATE {self.table_name} SET is_valid = 1, "
                    "computed_at = ? "
                    "WHERE analyzer_name = ? AND file_path = ?",
                    (current_time, analyzer_name, path),
                )
            else:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {self.table_name} "
                    "(analyzer_name, file_path, file_mtime, computed_at, is_valid) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (
                        analyzer_name,
                        path,
                        self._MTIME_SENTINEL,
                        current_time,
                    ),
                )

        self._conn.commit()

    # -------------------------------------------------------------------------
    # Self-consistency verification
    # -------------------------------------------------------------------------

    def verify_consistency(
        self, store: Store, sample_size: int = 10
    ) -> ConsistencyReport:
        """Randomly sample *sample_size* valid tracking records and compare
        the stored mtime baseline with the current Store value.

        Args:
            store: A Store instance providing ``get_file_stats()``.
            sample_size: Maximum number of records to check (default 10).

        Returns:
            ConsistencyReport with checked / consistent / inconsistent counts.
        """
        self._check_open()

        cur = self._conn.execute(
            f"SELECT analyzer_name, file_path, file_mtime "
            f"FROM {self.table_name} "
            "WHERE is_valid = 1 AND file_mtime != 0 "
            "ORDER BY RANDOM() LIMIT ?",
            (sample_size,),
        )
        rows = cur.fetchall()

        if not rows:
            return ConsistencyReport()

        file_stats = store.get_file_stats()

        checked = 0
        consistent = 0
        inconsistent = 0
        details: list = []

        for analyzer_name, file_path, tracked_mtime in rows:
            checked += 1
            store_entry = file_stats.get(file_path)
            store_mtime = store_entry[1] if store_entry is not None else 0
            if tracked_mtime == store_mtime:
                consistent += 1
            else:
                inconsistent += 1
                details.append(
                    {
                        "analyzer_name": analyzer_name,
                        "file_path": file_path,
                        "tracked_mtime": str(tracked_mtime),
                        "store_mtime": str(store_mtime),
                    }
                )

        return ConsistencyReport(
            checked_count=checked,
            consistent_count=consistent,
            inconsistent_count=inconsistent,
            details=details,
        )

    # -------------------------------------------------------------------------
    # Connection management
    # -------------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection.  Idempotent — safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _check_open(self) -> None:
        """Raise if the tracker has been closed."""
        if self._closed:
            raise RuntimeError("InvalidationTracker has been closed")

    @staticmethod
    def _match_patterns(
        patterns: list[str], paths: set[str]
    ) -> set[str]:
        """Return the subset of *paths* matching any glob *pattern*.

        Supports ``**`` (recursive) wildcards via path-component-based
        matching.  An empty *patterns* list yields no matches.
        """
        if not patterns:
            return set()

        matched: set[str] = set()
        for pattern in patterns:
            # Normalise backslashes for cross-platform consistency.
            norm_pattern = pattern.replace("\\", "/")
            for path in paths:
                norm_path = path.replace("\\", "/")
                if InvalidationTracker._glob_match(norm_path, norm_pattern):
                    matched.add(path)
        return matched

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Match *path* against a glob *pattern* that supports ``**``.

        Splits both into slash-delimited components and matches
        component-by-component.  ``**`` matches zero or more path components.
        """
        path_parts = path.split("/")
        pat_parts = pattern.split("/")

        # Quick-reject: a plain path (no glob metacharacters) must match exactly.
        if not any(mc in pattern for mc in ("*", "?", "[")):
            return path == pattern

        return InvalidationTracker._match_comp(
            path_parts, pat_parts, 0, 0, len(path_parts), len(pat_parts)
        )

    @staticmethod
    def _match_comp(
        path_parts: list[str],
        pat_parts: list[str],
        pi: int,
        pp: int,
        plen: int,
        palen: int,
    ) -> bool:
        """Recursive component-wise glob matcher."""
        # Both exhausted → match.
        if pi == plen and pp == palen:
            return True
        # Pattern exhausted but path remains → no match.
        if pp == palen:
            return False
        # "**" — match zero or more path components.
        if pat_parts[pp] == "**":
            # Match zero components.
            if InvalidationTracker._match_comp(
                path_parts, pat_parts, pi, pp + 1, plen, palen
            ):
                return True
            # Match one or more components.
            for i in range(pi, plen):
                if InvalidationTracker._match_comp(
                    path_parts, pat_parts, i + 1, pp + 1, plen, palen
                ):
                    return True
            return False
        # Path exhausted but non-"**" pattern remains → no match.
        if pi == plen:
            return False
        # Single-component match via fnmatch.
        if fnmatch.fnmatch(path_parts[pi], pat_parts[pp]):
            return InvalidationTracker._match_comp(
                path_parts, pat_parts, pi + 1, pp + 1, plen, palen
            )
        return False
