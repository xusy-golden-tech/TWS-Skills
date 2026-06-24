"""Parallel extraction orchestrator — ProcessPoolExecutor-based batch indexing.

Design (P14):
- ProcessPoolExecutor (not ThreadPoolExecutor — Python GIL)
- Worker functions at module level (pickle requirement)
- chunk_size = 50 files per batch
- max_workers = min(20, cpu_count)
- Main process writes SQLite — workers only parse/extract
- Stat pre-filter runs in main process before dispatching to pool
- Error isolation: single-file failure never crashes the whole batch
"""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from ..db.queries import QueryBuilder
from .scanner import scan_directory
from .language_detect import detect_language
from .parser import extract_from_source
from ..edge_resolver import resolve_edges, ResolveResult
from ..framework import detect_frameworks, FrameworkDetectionResult


# ---------------------------------------------------------------------------
# IndexResult — mirrors orchestrator.IndexResult
# ---------------------------------------------------------------------------

@dataclass
class IndexResult:
    files_indexed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    errors: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    resolve_result: ResolveResult | None = None
    framework_result: FrameworkDetectionResult | None = None


def hash_content(content: str) -> str:
    """SHA-256 hash of file content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Module-level worker function (must be at module level for pickle)
# ---------------------------------------------------------------------------

def _process_file_batch(args: tuple) -> list[dict]:
    """Process a batch of files in a worker process.

    Args:
        args: (root_dir, rel_paths) tuple — must be a single tuple argument
              for ProcessPoolExecutor.map compatibility.

    Returns:
        List of per-file result dicts with keys:
        rel_path, nodes, edges, errors, content_hash, language, size, mtime,
        status ("ok" or "error").
    """
    root_dir, rel_paths = args
    results = []

    for rel_path in rel_paths:
        try:
            full_path = os.path.join(root_dir, rel_path)
            fstat = os.stat(full_path)
            fsize = fstat.st_size
            fmtime = int(fstat.st_mtime)

            lang = detect_language(rel_path)

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            fhash = hash_content(content)

            extraction = extract_from_source(rel_path, content, lang)

            # Filter valid nodes (must have id, kind, name)
            valid_nodes = [
                n for n in extraction.nodes
                if n.get("id") and n.get("kind") and n.get("name")
            ]

            # Filter valid edges (source must be in this file's valid nodes)
            inserted_ids = {n["id"] for n in valid_nodes}
            valid_edges = [
                e for e in extraction.edges
                if e.get("source") in inserted_ids
            ]

            results.append({
                "rel_path": rel_path,
                "nodes": valid_nodes,
                "edges": valid_edges,
                "errors": extraction.errors,
                "content_hash": fhash,
                "language": lang,
                "size": fsize,
                "mtime": fmtime,
                "status": "ok",
            })
        except Exception as e:
            results.append({
                "rel_path": rel_path,
                "nodes": [],
                "edges": [],
                "errors": [{
                    "message": str(e),
                    "file_path": rel_path,
                    "severity": "error",
                }],
                "content_hash": "",
                "language": "unknown",
                "size": 0,
                "mtime": 0,
                "status": "error",
            })

    return results


# ---------------------------------------------------------------------------
# ParallelExtractionOrchestrator
# ---------------------------------------------------------------------------


class ParallelExtractionOrchestrator:
    """Orchestrates parallel index: scan -> chunk -> pool -> collect -> store."""

    def __init__(
        self,
        root_dir: str,
        max_workers: int | None = None,
        chunk_size: int = 50,
    ):
        self.root_dir = root_dir
        self.max_workers = min(max_workers or min(20, os.cpu_count() or 1), 20)
        self.chunk_size = chunk_size

    def index_all(
        self,
        queries: QueryBuilder,
        force: bool = False,
    ) -> IndexResult:
        """Full index with parallel extraction.

        If force=False:
          1. Stat pre-filter (main process): skip files with matching mtime+size
          2. Content hash check (main process): skip files with matching SHA256
          3. Chunk remaining files into batches of chunk_size
          4. ProcessPoolExecutor processes batches in parallel
          5. Main process collects results and batch-inserts into SQLite
          6. Post-processing: cross-file resolve, FTS rebuild, framework detection
        """
        t0 = time.time()
        result = IndexResult()

        # Step 1: Scan + stat pre-filter (same as serial, in main process)
        files = scan_directory(self.root_dir)
        if not files:
            result.errors.append({
                "message": f"No source files found in {self.root_dir}",
                "severity": "warning",
            })
            return result

        # Remove files from DB that no longer exist on disk
        existing = {f["path"] for f in queries.get_all_files()}
        current = set(files)
        removed = existing - current
        for path in removed:
            queries.delete_file(path)

        # Pre-load file stats for skipping
        db_stats = queries.get_file_stats() if not force else {}

        # Step 2: Determine which files need re-indexing
        files_to_reindex = []

        for rel_path in files:
            full_path = os.path.join(self.root_dir, rel_path)
            fstat = os.stat(full_path)
            fsize = fstat.st_size
            fmtime = int(fstat.st_mtime)

            if not force:
                # Tier 1: stat pre-filter (no file read)
                prev = db_stats.get(rel_path)
                if prev and prev[0] == fsize and prev[1] == fmtime:
                    result.files_skipped += 1
                    continue

            # Read content for hash check
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            fhash = hash_content(content)

            if not force:
                # Tier 2: content hash match
                lang = detect_language(rel_path)
                existing_file = queries.get_file_by_path(rel_path)
                if existing_file and existing_file["content_hash"] == fhash:
                    # Update stat even when hash matches
                    if (existing_file["size"] != fsize
                            or existing_file["modified_at"] != fmtime):
                        queries.upsert_file(rel_path, fhash, lang,
                                           existing_file["node_count"],
                                           size=fsize, modified_at=fmtime)
                    result.files_skipped += 1
                    continue

            files_to_reindex.append(rel_path)

        if not files_to_reindex:
            # No files changed — skip all post-processing (A1: fast return)
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        # Step 3: Chunk files into batches
        batches = _chunk_list(files_to_reindex, self.chunk_size)
        batch_args = [(self.root_dir, batch) for batch in batches]

        # Step 4: Process batches in parallel
        all_file_results = []
        worker_count = min(self.max_workers, len(batches))

        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_process_file_batch, args): idx
                for idx, args in enumerate(batch_args)
            }
            for future in as_completed(futures):
                try:
                    batch_results = future.result()
                    all_file_results.extend(batch_results)
                except Exception as e:
                    result.errors.append({
                        "message": f"Batch worker failed: {e}",
                        "severity": "error",
                    })

        # Step 5: Batch INSERT into SQLite (main process, single-threaded)
        for file_result in all_file_results:
            rel_path = file_result["rel_path"]

            if file_result["status"] == "error":
                result.files_errored += 1
                result.errors.extend(file_result["errors"])
                continue

            valid_nodes = file_result["nodes"]
            valid_edges = file_result["edges"]
            fhash = file_result["content_hash"]
            lang = file_result["language"]
            fsize = file_result["size"]
            fmtime = file_result["mtime"]

            queries.conn.execute("BEGIN")
            try:
                if queries.get_file_by_path(rel_path):
                    queries.delete_file(rel_path)

                if valid_nodes:
                    queries.insert_nodes(valid_nodes)

                if valid_edges:
                    queries.insert_edges(valid_edges)
                    result.edges_created += len(valid_edges)

                queries.upsert_file(rel_path, fhash, lang, len(valid_nodes),
                                   size=fsize, modified_at=fmtime)

                queries.conn.execute("COMMIT")
            except Exception:
                queries.conn.execute("ROLLBACK")
                raise

            result.files_indexed += 1
            result.nodes_created += len(valid_nodes)

            if file_result["errors"]:
                result.errors.extend(file_result["errors"])

        # Step 6: Post-processing (A1: only if files were actually indexed)
        if result.files_indexed > 0:
            result.resolve_result = resolve_edges(queries)
            _populate_unresolved_refs(queries, result.resolve_result)
            _populate_import_unresolved(queries)
            queries.rebuild_fts()
            result.framework_result = detect_frameworks(self.root_dir, queries)

        result.duration_ms = int((time.time() - t0) * 1000)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks of chunk_size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def _module_in_project(module_name: str, source_file: str,
                       project_files: set[str]) -> bool:
    """Check if a Python module name corresponds to a known project file."""
    module_path = module_name.replace(".", "/")

    # Direct file
    if f"{module_path}.py" in project_files:
        return True

    # Package init
    if f"{module_path}/__init__.py" in project_files:
        return True

    # Relative imports
    if module_name.startswith("."):
        source_dir = os.path.dirname(source_file)
        dot_count = 0
        for c in module_name:
            if c == ".":
                dot_count += 1
            else:
                break
        relative_rest = module_name[dot_count:]
        levels_up = dot_count - 1
        parts = source_dir.split("/")
        if levels_up <= len(parts):
            base = ("/".join(parts[:len(parts)-levels_up])
                    if levels_up > 0 else source_dir)
            candidate = os.path.join(
                base, relative_rest.replace(".", "/")
            ).replace("\\", "/")
            if f"{candidate}.py" in project_files:
                return True
            if f"{candidate}/__init__.py" in project_files:
                return True

    return False


def _populate_unresolved_refs(queries: QueryBuilder,
                               resolve_result: ResolveResult) -> None:
    """Populate unresolved_refs table from edges with provenance 'unresolved'."""
    if not resolve_result or resolve_result.unresolved == 0:
        return

    rows = queries._exec("""
        SELECT e.*, n.file_path, n.language
        FROM edges e
        JOIN nodes n ON e.source = n.id
        WHERE e.provenance = 'unresolved'
    """).fetchall()

    for row in rows:
        ref = {
            "from_node_id": row["source"],
            "reference_name": row["target_text"] or "",
            "reference_kind": row["kind"] or "call",
            "line": 0,
            "col": 0,
            "file_path": row["file_path"] or "",
            "language": row["language"] or "",
        }
        queries.insert_unresolved_ref(ref)


def _populate_import_unresolved(queries: QueryBuilder) -> None:
    """Populate unresolved_refs from import edges with externality classification."""
    project_files = {row["path"] for row in queries.get_all_files()}

    rows = queries._exec("""
        SELECT e.*, n.file_path, n.language
        FROM edges e
        JOIN nodes n ON e.source = n.id
        WHERE e.kind = 'imports'
          AND e.target NOT IN (SELECT id FROM nodes)
          AND e.target_text IS NOT NULL
    """).fetchall()

    for row in rows:
        full_name = row["target_text"]
        source_file = row["file_path"]

        parts = full_name.split(".")
        is_external = True
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if _module_in_project(candidate, source_file, project_files):
                is_external = False
                break

        ref = {
            "from_node_id": row["source"],
            "reference_name": full_name,
            "reference_kind": "import",
            "line": 0,
            "col": 0,
            "file_path": source_file,
            "language": row["language"] or "",
            "is_external": int(is_external),
        }
        queries.insert_unresolved_ref(ref)
