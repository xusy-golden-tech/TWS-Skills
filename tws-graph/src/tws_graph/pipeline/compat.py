"""Backward-compatible ExtractionOrchestrator wrapper.

Provides the same public API as ``indexer/orchestrator.py`` but delegates
all work to PipelineEngine + SqliteStore internally.

Usage (old pattern, still works)::

    from tws_graph.pipeline.compat import ExtractionOrchestrator
    orch = ExtractionOrchestrator(root_dir, queries)
    result = orch.index_all()

Usage (new pattern)::

    orch = ExtractionOrchestrator(root_dir, queries)
    result = orch.extract_and_index(files, root_dir)
    result = orch.incremental_index(changed_files, root_dir)
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .engine import PipelineEngine
from .passes import (
    ConfigLinkAnalysisPass,
    CrossFileResolvePass,
    DataFlowPass,
    EdgeInsertPass,
    NodeInsertPass,
    ParseExtractPass,
    StatFilterPass,
    TestEdgeAnalysisPass,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Re-exported types — maintain backward compatibility with indexer/orchestrator
# ---------------------------------------------------------------------------


@dataclass
class IndexResult:
    """Result of a full index run.

    Mirrors ``tws_graph.indexer.orchestrator.IndexResult`` exactly.
    """

    files_indexed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    resolve_result: Optional[object] = None
    framework_result: Optional[object] = None


def hash_content(content: str) -> str:
    """SHA-256 hash of file content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ExtractionOrchestrator — compat wrapper
# ---------------------------------------------------------------------------


class ExtractionOrchestrator:
    """Backward-compatible wrapper around PipelineEngine + SqliteStore.

    Maintains the original ExtractionOrchestrator public API surface but
    delegates all index work to the multi-pass pipeline engine internally.

    Parameters
    ----------
    root_dir : str
        Project root directory (used by index_all for file scanning).
    queries : QueryBuilder
        Existing QueryBuilder instance pointing to the target database.
        The compat wrapper creates a new SqliteStore connection to the same
        database file, enabled by SQLite WAL mode.
    """

    def __init__(self, root_dir: str, queries: object):
        self.root_dir = root_dir
        self.queries = queries

        # ── Resolve database path ────────────────────────────────────
        db_path = self._resolve_db_path(queries)

        # ── Ensure schema is compatible with SqliteStore ─────────────
        # The existing DB may have been created by the old
        # DatabaseConnection.initialize() schema which lacks the
        # ``properties`` columns that the new SqliteStore expects.
        self._ensure_schema(db_path)

        # ── Create Store and Engine ───────────────────────────────────
        # Lazy import to avoid circular deps at module level
        from tws_graph.store.sqlite_store import SqliteStore

        self._store = SqliteStore(db_path)
        self._engine = PipelineEngine(store=self._store)

        # Register the core Passes in dependency order.
        # Dependencies are declared on each Pass class — the engine will
        # topological-sort them automatically.
        self._engine.register_pass(StatFilterPass())
        self._engine.register_pass(ParseExtractPass())
        self._engine.register_pass(NodeInsertPass())
        self._engine.register_pass(EdgeInsertPass())
        self._engine.register_pass(DataFlowPass())
        self._engine.register_pass(CrossFileResolvePass())
        self._engine.register_pass(TestEdgeAnalysisPass())
        self._engine.register_pass(ConfigLinkAnalysisPass())

    # ── db_path resolution + schema compatibility ─────────────────────────

    @staticmethod
    def _ensure_schema(db_path: str) -> None:
        """Ensure the database schema is compatible with SqliteStore.

        - If the DB does not exist, runs the full migration system to create
          the complete schema.
        - If the DB exists but was created by the old schema (lacking
          ``properties`` columns), adds those columns via ALTER TABLE.
        """
        import sqlite3 as _sqlite3

        if not os.path.exists(db_path):
            # Fresh DB: run full migrations
            from tws_graph.store.migrations import MigrationRunner

            from tws_graph.store.connection import configure_connection

            conn = _sqlite3.connect(db_path, isolation_level=None)
            conn.row_factory = _sqlite3.Row
            configure_connection(conn)
            runner = MigrationRunner(conn)
            runner.migrate()
            conn.close()
            return

        # Existing DB: ensure properties columns exist
        from tws_graph.store.connection import configure_connection

        conn = _sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = _sqlite3.Row
        configure_connection(conn)
        try:
            conn.execute("SELECT properties FROM nodes LIMIT 1")
        except _sqlite3.OperationalError:
            conn.execute(
                "ALTER TABLE nodes ADD COLUMN properties TEXT DEFAULT '{}'"
            )
        try:
            conn.execute("SELECT properties FROM edges LIMIT 1")
        except _sqlite3.OperationalError:
            conn.execute(
                "ALTER TABLE edges ADD COLUMN properties TEXT DEFAULT '{}'"
            )
        conn.close()

    @staticmethod
    def _resolve_db_path(queries: object) -> str:
        """Extract the SQLite database file path from a QueryBuilder.

        Handles two cases:
        1. queries._conn_mgr is a ConnectionManager (store/connection) —
           has a ``db_path`` attribute.
        2. queries._conn_mgr is a _RawConnectionAdapter wrapping a raw
           ``sqlite3.Connection`` — use ``PRAGMA database_list``.
        """
        conn_mgr = getattr(queries, "_conn_mgr", None)
        if conn_mgr is None:
            raise ValueError(
                "Cannot resolve db_path from QueryBuilder: "
                "no _conn_mgr attribute found"
            )

        # Case 1: ConnectionManager from store/connection
        db_path = getattr(conn_mgr, "db_path", None)
        if db_path is not None:
            return db_path

        # Case 2: _RawConnectionAdapter → raw sqlite3.Connection
        raw_conn = getattr(conn_mgr, "conn", None)
        if raw_conn is not None:
            rows = raw_conn.execute("PRAGMA database_list").fetchall()
            if rows:
                return rows[0][2]  # file column (name=main, file=/path/to/db)
            raise ValueError(
                "Cannot resolve db_path: PRAGMA database_list returned no rows"
            )

        raise ValueError(
            "Cannot resolve db_path from QueryBuilder._conn_mgr: "
            f"unexpected type {type(conn_mgr).__name__}"
        )

    # ── Public API (backward-compatible) ───────────────────────────────────

    def index_all(self, force: bool = False) -> IndexResult:
        """Full index: scan all source files, parse, and store.

        Delegates to PipelineEngine.execute() and then runs post-processing
        (file record upsert, cross-file resolve, FTS rebuild).
        """
        t0 = time.time()
        result = IndexResult()

        # ── 1. Scan files ──────────────────────────────────────────
        from tws_graph.indexer.scanner import scan_directory

        files = scan_directory(self.root_dir)
        if not files:
            result.errors.append({
                "message": f"No source files found in {self.root_dir}",
                "severity": "warning",
            })
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        # ── 2. Remove stale files from the store ───────────────────
        existing_paths = {f["path"] for f in self._store.get_all_files()}
        current = set(files)
        removed = existing_paths - current
        for path in removed:
            self._store.delete_file(path)

        # ── 3. Execute pipeline ─────────────────────────────────────
        ctx = self._engine.execute(
            files=files,
            root_dir=self.root_dir,
            force=force,
        )

        # ── 4. Post-processing ────────────────────────────────────────
        self._post_process(ctx)

        # ── 5. Map PipelineContext → IndexResult ────────────────────
        result = self._ctx_to_result(ctx)
        result.resolve_result = self._run_edge_resolver()
        # Override duration to include scan time
        result.duration_ms = int((time.time() - t0) * 1000)

        logger.info(
            "index_all complete: %d files indexed, %d skipped, "
            "%d nodes, %d edges in %dms",
            result.files_indexed,
            result.files_skipped,
            result.nodes_created,
            result.edges_created,
            result.duration_ms,
        )
        return result

    def extract_and_index(
        self,
        files: list[str],
        root_dir: str,
        force: bool = False,
    ) -> IndexResult:
        """Index a specific set of files (new API).

        Unlike ``index_all``, this does not scan the directory — it processes
        exactly the files given.  Useful when the caller already knows which
        files need indexing.

        Parameters
        ----------
        files : list[str]
            Relative file paths to index.
        root_dir : str
            Project root directory.
        force : bool
            If True, skip stat-based filtering and force re-extraction.

        Returns
        -------
        IndexResult
        """
        t0 = time.time()
        result = IndexResult()

        if not files:
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        ctx = self._engine.execute(
            files=list(files),
            root_dir=root_dir,
            force=force,
        )

        self._post_process(ctx)
        result = self._ctx_to_result(ctx)
        result.resolve_result = self._run_edge_resolver()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def incremental_index(
        self,
        changed_files: list[str],
        root_dir: str,
    ) -> IndexResult:
        """Incremental index — process only changed files (new API).

        Uses the incremental pipeline (only Passes with
        ``supports_incremental=True``) and always runs with ``force=False``
        so that the StatFilterPass can skip unchanged files.

        Parameters
        ----------
        changed_files : list[str]
            Subset of file paths that may have changed.
        root_dir : str
            Project root directory.

        Returns
        -------
        IndexResult
        """
        t0 = time.time()
        result = IndexResult()

        if not changed_files:
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        ctx = self._engine.execute_incremental(
            changed_files=list(changed_files),
            root_dir=root_dir,
        )

        self._post_process(ctx)
        result = self._ctx_to_result(ctx)
        result.resolve_result = self._run_edge_resolver()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    # ── Resource management ───────────────────────────────────────────────

    def close(self) -> None:
        """Close the internal SqliteStore connection.  Idempotent."""
        if hasattr(self, "_store") and self._store is not None:
            self._store.close()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _post_process(self, ctx) -> None:
        """Run post-pipeline steps: flush, upsert file records, rebuild FTS.

        Must be called after every pipeline execution to ensure buffered
        writes are persisted and file stat records are updated for the next
        incremental run.
        """
        # Flush buffered writes (SqliteStore buffers nodes/edges/refs)
        self._store.flush()

        # Upsert file records so StatFilterPass works on next sync
        self._upsert_file_records(ctx.files)

        # Rebuild FTS index for search
        self._store.rebuild_fts()

    @staticmethod
    def _ctx_to_result(ctx) -> IndexResult:
        """Map PipelineContext fields to IndexResult."""
        return IndexResult(
            files_indexed=len(ctx.files),
            files_skipped=ctx.metadata.get("filtered_out", 0),
            files_errored=sum(
                1 for e in ctx.errors if e.get("severity") == "error"
            ),
            nodes_created=ctx.metadata.get("node_count", 0),
            edges_created=ctx.metadata.get("edge_count", 0),
            errors=ctx.errors,
            duration_ms=ctx.duration_ms,
        )

    def _upsert_file_records(self, processed_files: list[str]) -> None:
        """Write/update file records so StatFilterPass works on next run.

        For each processed file, reads the content to compute a SHA-256
        content hash, detects the language, and calls ``store.upsert_file``.
        """
        from tws_graph.indexer.language_detect import detect_language

        for rel_path in processed_files:
            try:
                full_path = os.path.join(self.root_dir, rel_path)
                fstat = os.stat(full_path)
                fsize = fstat.st_size
                fmtime = int(fstat.st_mtime)

                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                fhash = hash_content(content)
                lang = detect_language(rel_path)

                # Count nodes for this file
                node_count = 0
                existing = self._store.get_file(rel_path)
                if existing:
                    node_count = existing.get("node_count", 0)
                else:
                    # Count from the store if file record doesn't exist yet
                    try:
                        nodes = list(self._store.iter_nodes_by_file(rel_path))
                        node_count = len(nodes)
                    except Exception:
                        node_count = 0

                self._store.upsert_file(
                    rel_path, fhash, lang, node_count,
                    size=fsize, modified_at=fmtime,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to upsert file record for %s: %s", rel_path, exc
                )

    def _run_edge_resolver(self) -> Optional[object]:
        """Run cross-file edge resolution via the legacy EdgeResolver.

        The pipeline's CrossFileResolvePass handles intra-graph resolution.
        This step runs the legacy edge_resolver.resolve_edges for additional
        resolution logic (e.g. unresolved/ambiguous classification).
        """
        try:
            from tws_graph.edge_resolver import resolve_edges
            return resolve_edges(self.queries)
        except Exception as exc:
            logger.warning("Edge resolution failed (non-fatal): %s", exc)
            return None
