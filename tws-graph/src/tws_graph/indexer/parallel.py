"""Parallel extraction orchestrator — ProcessPoolExecutor-based batch indexing.

Design (P14):
- ProcessPoolExecutor (not ThreadPoolExecutor — Python GIL)
- Worker functions at module level (pickle requirement)
- chunk_size = 100 files per batch
- max_workers = min(20, cpu_count)
- Main process writes SQLite — workers only parse/extract
- Stat pre-filter runs in main process before dispatching to pool
- Error isolation: single-file failure never crashes the whole batch
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from ..db.queries import QueryBuilder
from .scanner import scan_directory
from .language_detect import detect_language
from .parser import extract_full
from ..edge_resolver import resolve_edges, resolve_structural_edges, resolve_overrides, resolve_instantiates, ResolveResult, is_call_target_external
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

            extraction = extract_full(rel_path, content, lang)

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
        chunk_size: int = 100,  # P28: 50→100 reduce scheduling overhead
    ):
        self.root_dir = root_dir
        self.max_workers = min(max_workers or min(20, os.cpu_count() or 1), 20)
        self.chunk_size = chunk_size

    def index_all(
        self,
        queries: QueryBuilder,
        force: bool = False,
        deep: bool = False,
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
            # No files changed — fast return, but run clone detection if --deep
            if deep:
                _detect_clones(queries)
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
        # Group files into write batches of 100 to reduce transaction overhead
        _WRITE_BATCH_SIZE = 500  # P28: 100→500 reduce transaction overhead
        for batch_start in range(0, len(all_file_results), _WRITE_BATCH_SIZE):
            batch_files = all_file_results[batch_start:batch_start + _WRITE_BATCH_SIZE]

            queries.conn.execute("BEGIN")
            try:
                for file_result in batch_files:
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

                    if queries.get_file_by_path(rel_path):
                        queries.delete_file(rel_path)

                    if valid_nodes:
                        queries.insert_nodes(valid_nodes)

                    if valid_edges:
                        queries.insert_edges(valid_edges)
                        result.edges_created += len(valid_edges)

                    queries.upsert_file(rel_path, fhash, lang, len(valid_nodes),
                                       size=fsize, modified_at=fmtime)

                    result.files_indexed += 1
                    result.nodes_created += len(valid_nodes)

                    if file_result["errors"]:
                        result.errors.extend(file_result["errors"])

                queries.conn.execute("COMMIT")
            except Exception:
                queries.conn.execute("ROLLBACK")
                raise
                result.errors.extend(file_result["errors"])

        # Step 6: Post-processing (A1: only if files were actually indexed)
        if result.files_indexed > 0:
            result.resolve_result = resolve_edges(queries)
            resolve_structural_edges(queries)
            resolve_overrides(queries)
            resolve_instantiates(queries)
            _populate_unresolved_refs(queries, result.resolve_result)
            _populate_import_unresolved(queries)
            _propagate_throws(queries)
            _propagate_cross_function_rw(queries)
            _propagate_cross_file_dataflow(queries)
            if deep:
                _detect_clones(queries)
            # P28: FTS triggers keep index in sync — full rebuild is redundant
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
    """Populate unresolved_refs table from edges with provenance 'unresolved'.

    Classifies each unresolved call target as external (stdlib / built-in /
    third-party) or internal (possible index gap) via
    :func:`is_call_target_external`.
    """
    if not resolve_result or resolve_result.unresolved == 0:
        return

    project_files = frozenset(
        row["path"] for row in queries.get_all_files()
    )

    rows = queries._exec("""
        SELECT e.*, n.file_path, n.language
        FROM edges e
        JOIN nodes n ON e.source = n.id
        WHERE e.provenance = 'unresolved'
    """).fetchall()

    for row in rows:
        target_text = row["target_text"] or ""
        is_ext = is_call_target_external(target_text, project_files)
        ref = {
            "from_node_id": row["source"],
            "reference_name": target_text,
            "reference_kind": row["kind"] or "call",
            "line": 0,
            "col": 0,
            "file_path": row["file_path"] or "",
            "language": row["language"] or "",
            "is_external": int(is_ext),
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


def _propagate_throws(queries: QueryBuilder) -> None:
    """Propagate throws edges along call chains (cross-function exception tracking).

    For every call edge (caller → callee), if the callee has throws edges
    (direct or previously-propagated), propagate those exception types to the
    caller.  Limited to depth 3 to avoid unbounded propagation through
    recursive or deeply-nested call chains.

    Propagated edges use ``provenance = 'propagated'`` to distinguish them
    from direct tree-sitter ``throws`` edges.

    All propagation is done in memory; results are written in a single batch
    at the end for performance.
    """
    MAX_DEPTH = 3

    # 1. Load all existing throws edges: source → (target, target_text)
    throws_rows = queries._exec("""
        SELECT source, target, target_text
        FROM edges
        WHERE kind = 'throws'
    """).fetchall()

    if not throws_rows:
        return

    # throws_map[source_id] = {(target_id, target_text), ...}
    throws_map: dict[str, set[tuple[str, str]]] = {}
    for r in throws_rows:
        throws_map.setdefault(r["source"], set()).add(
            (r["target"], r["target_text"] or "")
        )

    # 2. Load all calls edges: caller → callee
    call_rows = queries._exec("""
        SELECT source, target
        FROM edges
        WHERE kind = 'calls'
    """).fetchall()

    if not call_rows:
        return

    # call_graph[callee] = {caller1, caller2, ...}
    call_graph: dict[str, set[str]] = {}
    for r in call_rows:
        call_graph.setdefault(r["target"], set()).add(r["source"])

    # 3. BFS propagate: for each function with throws, propagate to callers
    inserted: set[tuple[str, str, str]] = set()
    for source_id, exc_set in throws_map.items():
        for target_id, target_text in exc_set:
            inserted.add((source_id, target_id, target_text))

    # All propagated edges collected here, written once at the end
    all_new_edges: list[tuple[str, str, str]] = []

    depth = 0
    # Start with direct throws
    current_sources: set[str] = set(throws_map.keys())

    while depth < MAX_DEPTH and current_sources:
        new_batch: list[tuple[str, str, str]] = []
        next_sources: set[str] = set()

        for source_id in sorted(current_sources):
            exc_set = throws_map.get(source_id)
            if not exc_set:
                continue
            for caller_id in call_graph.get(source_id, set()):
                for target_id, target_text in exc_set:
                    key = (caller_id, target_id, target_text)
                    if key not in inserted:
                        inserted.add(key)
                        new_batch.append((caller_id, target_id, target_text))
                        next_sources.add(caller_id)

        if not new_batch:
            break

        all_new_edges.extend(new_batch)

        # Update throws_map with new edges for next depth iteration
        for s, t, tt in new_batch:
            throws_map.setdefault(s, set()).add((t, tt))

        current_sources = next_sources
        depth += 1

    # 4. Write all propagated edges at once
    if all_new_edges:
        rows = [
            {
                "source": s,
                "target": t,
                "target_text": tt,
                "kind": "throws",
                "source_loc": "",
                "provenance": "propagated",
            }
            for s, t, tt in all_new_edges
        ]
        queries.insert_edges(rows)


def _write_throws_batch(
    queries: QueryBuilder,
    edges: list[tuple[str, str, str]],
    depth: int,
) -> None:
    """Write a batch of propagated throws edges."""
    rows = [
        {
            "source": s,
            "target": t,
            "target_text": tt,
            "kind": "throws",
            "source_loc": "",
            "provenance": "propagated",
        }
        for s, t, tt in edges
    ]
    queries.insert_edges(rows)


def _propagate_cross_function_rw(queries: QueryBuilder) -> None:
    """Create data_flows edges for variables shared across function boundaries.

    Analyses reads/writes edges to find variables accessed by multiple
    functions, then creates ``data_flows`` edges from writer → reader with
    ``provenance='cross-function'``.

    Handles three patterns:

    1. **Global/nonlocal variables** — ``target_text`` ends with ``:global``
       or ``:nonlocal``.  These are guaranteed cross-function by language
       semantics.

    2. **Class attributes** — ``target_text`` starts with ``self.`` /
       ``this.``.  Scoped per-class so ``self.x`` in two different classes
       does not produce a false edge.

    3. **Attribute writes** — ``target_text`` contains ``.`` (e.g.
       ``obj.attr``, ``module.var``).  Scoped per-file.
    """
    rows = queries._exec("""
        SELECT e.source, e.target_text, e.kind, n.file_path, n.qualified_name
        FROM edges e
        JOIN nodes n ON e.source = n.id
        WHERE e.kind IN ('reads', 'writes')
          AND e.target_text IS NOT NULL
    """).fetchall()

    if not rows:
        return

    from collections import defaultdict

    # Group by (scope, var_name):
    # - global / nonlocal / module  → scope = file_path
    # - self.x / this.x             → scope = class qname (extracted from qualified_name)
    # - obj.attr (other dot access) → scope = file_path
    writers: dict[tuple[str, str], set[str]] = defaultdict(set)
    readers: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in rows:
        target_text = row["target_text"]
        kind = row["kind"]
        source_id = row["source"]
        file_path = row["file_path"]
        qname = row["qualified_name"] or ""

        # Determine scope and variable key
        is_global = ":global" in target_text
        is_nonlocal = ":nonlocal" in target_text
        is_module = ":module" in target_text

        # Extract pure variable name (strip "var:" prefix if present)
        var_name = target_text
        if var_name.startswith("var:"):
            var_name = var_name[4:]

        # Decide scoping
        if is_global or is_nonlocal or is_module:
            # Language-guaranteed cross-function — scope at file level
            scope = file_path
        elif var_name.startswith("self."):
            # Class attribute — scope to the class
            # qname format: file_path::ClassName::method_name or file_path::method_name
            if qname.count("::") >= 2:
                # Has class scope: extract ClassName
                parts = qname.rsplit("::", 1)[0]  # file_path::ClassName
                scope = parts
            else:
                scope = file_path
        elif var_name.startswith("this."):
            # TypeScript/Java this.attr — scope to the class
            if qname.count("::") >= 2:
                parts = qname.rsplit("::", 1)[0]
                scope = parts
            else:
                scope = file_path
        elif "." in var_name:
            # Other dot access (obj.attr, module.var) — scope at file level
            scope = file_path
        else:
            # Plain variable — skip (could be same-named locals, not cross-function)
            continue

        key = (scope, var_name)
        if kind == "writes":
            writers[key].add(source_id)
        elif kind == "reads":
            readers[key].add(source_id)

    # Create data_flows edges from writers to readers
    new_edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for (scope, var_name), writer_set in writers.items():
        reader_set = readers.get((scope, var_name), set())
        if not reader_set:
            continue
        for writer_id in writer_set:
            for reader_id in reader_set:
                if writer_id == reader_id:
                    continue
                key = (writer_id, reader_id, var_name)
                if key not in seen:
                    seen.add(key)
                    new_edges.append({
                        "source": writer_id,
                        "target": reader_id,
                        "kind": "data_flows",
                        "target_text": var_name,
                        "source_loc": "",
                        "provenance": "cross-function",
                    })

    if new_edges:
        queries.insert_edges(new_edges)


def _propagate_cross_file_dataflow(queries: QueryBuilder) -> None:
    """Propagate data_flows across file boundaries through resolved calls edges.

    For every cross-file call (caller in file A → callee in file B), connect
    the caller's pre-call data sources to the caller's post-call data consumers
    by threading through the callee's internal data flow.

    Algorithm:
      1. Find cross-file ``calls`` edges (source file ≠ target file)
      2. For each such call, find:
         a. data_flows in the caller flowing INTO the callee (target = callee_id)
         b. data_flows in the callee flowing from params to returns
         c. data_flows in the caller flowing OUT of the callee (source = callee_id)
      3. Create cross-file data_flows: caller_source → caller_consumer
         through the callee's internal flow

    Created edges use ``provenance = 'cross-file'`` to distinguish from
    intra-file ``tree-sitter`` and intra-project ``cross-function`` edges.

    P28: All node lookups are batched into a single pre-load query to avoid
    N+1 SQLite round-trips in the nested loops.
    """
    # 1. Find cross-file calls edges
    cross_calls = queries._exec("""
        SELECT e.source AS caller_id, e.target AS callee_id,
               ns.file_path AS caller_file, nt.file_path AS callee_file
        FROM edges e
        JOIN nodes ns ON e.source = ns.id
        JOIN nodes nt ON e.target = nt.id
        WHERE e.kind = 'calls'
          AND ns.file_path != nt.file_path
    """).fetchall()

    if not cross_calls:
        return

    # Build set of (caller, callee) pairs for fast lookup
    cross_call_pairs: set[tuple[str, str]] = set()
    callee_set: set[str] = set()
    for row in cross_calls:
        cross_call_pairs.add((row["caller_id"], row["callee_id"]))
        callee_set.add(row["callee_id"])

    # 2. Find data_flows edges where target is a cross-file callee (flows INTO call)
    callee_list = list(callee_set)
    flows_into_call = queries._exec(f"""
        SELECT source, target, source_loc
        FROM edges
        WHERE kind = 'data_flows'
          AND provenance = 'tree-sitter'
          AND target IN ({','.join('?' for _ in callee_list)})
    """, callee_list).fetchall()

    # 3. Find data_flows edges where source is a cross-file callee (flows OUT of call)
    flows_out_of_call = queries._exec(f"""
        SELECT source, target, source_loc
        FROM edges
        WHERE kind = 'data_flows'
          AND provenance = 'tree-sitter'
          AND source IN ({','.join('?' for _ in callee_list)})
    """, callee_list).fetchall()

    # 4. Find data_flows within callee functions (param → return chains)
    callee_internal_flows = queries._exec("""
        SELECT source, target, source_loc
        FROM edges
        WHERE kind = 'data_flows'
          AND provenance = 'tree-sitter'
          AND source IN (
            SELECT id FROM nodes WHERE qualified_name LIKE '%::param:%'
          )
          AND target IN (
            SELECT id FROM nodes WHERE qualified_name LIKE '%::return:%'
          )
    """).fetchall()

    if not flows_into_call or not flows_out_of_call:
        return

    # P28: Batch-load all relevant node qualified_names into a single dict.
    # Collect all node IDs we'll need to look up.
    needed_ids: set[str] = set()
    for flow in callee_internal_flows:
        needed_ids.add(flow["source"])
        needed_ids.add(flow["target"])
    for into in flows_into_call:
        needed_ids.add(into["source"])
        needed_ids.add(into["target"])
    for out in flows_out_of_call:
        needed_ids.add(out["source"])
        needed_ids.add(out["target"])

    # Batch query: fetch all needed node info at once
    node_info: dict[str, tuple[str, str]] = {}  # id → (qualified_name, kind)
    if needed_ids:
        rows = queries._exec(f"""
            SELECT id, qualified_name, kind
            FROM nodes
            WHERE id IN ({','.join('?' for _ in needed_ids)})
        """, list(needed_ids)).fetchall()
        for r in rows:
            node_info[r["id"]] = (r["qualified_name"], r["kind"])

    # Also batch-load function/method nodes by qualified_name for fast lookup
    func_qn_to_id: dict[str, str] = {}
    func_rows = queries._exec(
        "SELECT id, qualified_name FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()
    for r in func_rows:
        func_qn_to_id[r["qualified_name"]] = r["id"]

    # Build lookup: callee_id → [callee internal flows (param → return)]
    callee_flows: dict[str, list[dict]] = {}
    for flow in callee_internal_flows:
        src_info = node_info.get(flow["source"])
        if not src_info:
            continue
        src_qn = src_info[0]
        # Extract function qualified_name: "file.py::func::param:x" → "file.py::func"
        func_qn = "::".join(src_qn.rsplit("::", 1)[:-1])
        if func_qn and func_qn in func_qn_to_id:
            callee_flows.setdefault(func_qn_to_id[func_qn], []).append(flow)

    # 5. Create cross-file data_flows edges
    new_edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for into in flows_into_call:
        callee_id = into["target"]
        src_info = node_info.get(into["source"])
        if not src_info:
            continue
        src_qn = src_info[0]

        # Extract function qualified name: strip last :: segment
        # e.g. "file.py::func::param:x" → "file.py::func"
        caller_qn = "::".join(src_qn.rsplit("::", 1)[:-1]) if "::" in src_qn else src_qn
        if not caller_qn:
            continue

        caller_id = func_qn_to_id.get(caller_qn)
        if not caller_id:
            continue

        if (caller_id, callee_id) not in cross_call_pairs:
            continue

        # Check if callee has internal param→return flows
        if callee_id not in callee_flows:
            continue

        # Find matching out-of-call flows
        for out in flows_out_of_call:
            if out["source"] != callee_id:
                continue

            out_info = node_info.get(out["target"])
            if not out_info:
                continue
            out_qn = out_info[0]
            out_caller_qn = "::".join(out_qn.rsplit("::", 1)[:-1]) if "::" in out_qn else out_qn
            if out_caller_qn != caller_qn:
                continue

            # Create cross-file edge: source of into → target of out
            edge_key = (into["source"], out["target"])
            if edge_key not in seen:
                seen.add(edge_key)
                source_loc = into["source_loc"] if "source_loc" in into.keys() else ""
                new_edges.append({
                    "source": into["source"],
                    "target": out["target"],
                    "kind": "data_flows",
                    "source_loc": source_loc,
                    "target_text": "",
                    "provenance": "cross-file",
                    "properties": "{}",
                })

    if new_edges:
        queries.insert_edges(new_edges)


def _detect_clones(queries: QueryBuilder) -> None:
    """Run MinHash+LSH clone detection on function/method bodies.

    Finds near-duplicate function/method pairs and creates ``similar_to``
    edges.  Only runs when ``--deep`` is passed because it is O(n^2) in the
    worst case after LSH bucketing.

    Requires function/method nodes to have non-empty ``body`` field (populated
    by Python/TypeScript/Java extractors).
    """
    try:
        from tws_graph.graph.algorithms.similarity import CloneDetector
        from tws_graph.graph.algorithms.minhash import MinHash, LSHIndex, extract_ast_tokens, estimate_jaccard
    except ImportError:
        return

    # Load function/method nodes with non-empty body
    rows = queries._exec("""
        SELECT id, name, body, kind
        FROM nodes
        WHERE kind IN ('function', 'method')
          AND body IS NOT NULL
          AND body != ''
    """).fetchall()

    if len(rows) < 2:
        return

    nodes = [{"id": r["id"], "name": r["name"], "body": r["body"], "kind": r["kind"]} for r in rows]
    nodes_by_id = {n["id"]: n for n in nodes}

    # Compute MinHash signatures
    NUM_PERM = 128
    mh = MinHash(num_perm=NUM_PERM)
    signatures: dict[str, list[int]] = {}
    for node in nodes:
        body = node.get("body", "") or ""
        if body:
            tokens = extract_ast_tokens(body)
            sig = mh.compute_signature(tokens)
            signatures[node["id"]] = sig

    if not signatures:
        return

    # Build LSH index
    BANDS = 16
    ROWS = 8
    lsh = LSHIndex(bands=BANDS, rows=ROWS)
    for nid, sig in signatures.items():
        lsh.insert(nid, sig)

    # Find candidate pairs
    pairs = lsh.find_similar_pairs()

    # Filter by threshold
    THRESHOLD = 0.7
    similar_pairs = []
    seen = set()
    for id1, id2, _ in pairs:
        key = tuple(sorted([id1, id2]))
        if key in seen:
            continue
        seen.add(key)
        jaccard = estimate_jaccard(signatures[id1], signatures[id2])
        if jaccard >= THRESHOLD:
            similar_pairs.append({
                "node_a": id1,
                "node_b": id2,
                "similarity": round(jaccard, 4),
                "name_a": nodes_by_id[id1].get("name", ""),
                "name_b": nodes_by_id[id2].get("name", ""),
            })

    if not similar_pairs:
        return

    # Delete old similar_to edges
    queries._exec("DELETE FROM edges WHERE kind = 'similar_to'")

    # Insert new edges
    edge_dicts = []
    for pair in similar_pairs:
        edge_dicts.append({
            "source": pair["node_a"],
            "target": pair["node_b"],
            "kind": "similar_to",
            "source_loc": "",
            "target_text": pair["name_b"],
            "provenance": "analysis",
            "properties": json.dumps({
                "similarity": pair["similarity"],
                "name_a": pair["name_a"],
                "name_b": pair["name_b"],
            }, ensure_ascii=False) if json else "",
        })

    if edge_dicts:
        queries.insert_edges(edge_dicts)
