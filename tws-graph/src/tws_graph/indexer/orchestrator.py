"""Extraction orchestrator — scan, parse, store, sync.

Ported from CodeGraph's extraction/index.ts.
Key patterns:
- Content-hash gating: skip files whose SHA256 hasn't changed
- Delete + re-insert per file (safe, simple)
- Individual file failure never crashes the whole index
"""

import hashlib
import time
from dataclasses import dataclass, field

from ..db.queries import QueryBuilder
from ..edge_resolver import resolve_edges, ResolveResult
from .scanner import scan_directory
from .language_detect import detect_language
from .parser import extract_from_source
from ..framework import detect_frameworks, FrameworkDetectionResult


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


class ExtractionOrchestrator:
    """Orchestrates the full index pipeline: scan -> parse -> store."""

    def __init__(self, root_dir: str, queries: QueryBuilder):
        self.root_dir = root_dir
        self.queries = queries

    def index_all(self, force: bool = False, parallel: bool = True) -> IndexResult:
        """Full index: scan all source files, parse, and store.

        If force=False:
          1. Stat pre-filter (cheap): if mtime + size match DB → skip
          2. Content hash (expensive): read file, compute SHA256, compare

        If parallel=True (default):
          Uses ProcessPoolExecutor for parallel extraction (P14).
          Workers parse files in parallel, main process writes SQLite.
          chunk_size=50, max_workers=min(20, cpu_count).
        """
        if parallel:
            from .parallel import ParallelExtractionOrchestrator
            par_orch = ParallelExtractionOrchestrator(self.root_dir)
            return par_orch.index_all(self.queries, force=force)

        t0 = time.time()
        result = IndexResult()
        import os

        files = scan_directory(self.root_dir)
        if not files:
            result.errors.append({
                "message": f"No source files found in {self.root_dir}",
                "severity": "warning",
            })
            return result

        # Remove files from DB that no longer exist on disk
        existing = {f["path"] for f in self.queries.get_all_files()}
        current = set(files)
        removed = existing - current
        for path in removed:
            self.queries.delete_file(path)

        # Pre-load file stats for cheap skipping
        db_stats = self.queries.get_file_stats() if not force else {}

        for rel_path in files:
            try:
                full_path = os.path.join(self.root_dir, rel_path)
                fstat = os.stat(full_path)
                fsize = fstat.st_size
                fmtime = int(fstat.st_mtime)

                lang = detect_language(rel_path)

                if not force:
                    # Tier 1: stat pre-filter (no file read)
                    prev = db_stats.get(rel_path)
                    if prev and prev[0] == fsize and prev[1] == fmtime:
                        result.files_skipped += 1
                        continue

                # Read content (needed for hash check or re-index)
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                fhash = hash_content(content)

                if not force:
                    # Tier 2: content hash match
                    existing_file = self.queries.get_file_by_path(rel_path)
                    if existing_file and existing_file["content_hash"] == fhash:
                        # Update stat even when hash matches (mtime may drift)
                        if existing_file["size"] != fsize or existing_file["modified_at"] != fmtime:
                            self.queries.upsert_file(rel_path, fhash, lang,
                                                     existing_file["node_count"],
                                                     size=fsize, modified_at=fmtime)
                        result.files_skipped += 1
                        continue

                # Re-index this file
                self.queries.conn.execute("BEGIN")
                try:
                    if self.queries.get_file_by_path(rel_path):
                        self.queries.delete_file(rel_path)

                    extraction = extract_from_source(rel_path, content, lang)

                    # Store valid nodes
                    valid_nodes = [
                        n for n in extraction.nodes
                        if n.get("id") and n.get("kind") and n.get("name")
                    ]
                    if valid_nodes:
                        self.queries.insert_nodes(valid_nodes)

                    # Store valid edges (source must be in this file; target may be cross-file)
                    if extraction.edges:
                        inserted_ids = {n["id"] for n in valid_nodes}
                        valid_edges = [
                            e for e in extraction.edges
                            if e["source"] in inserted_ids
                        ]
                        if valid_edges:
                            self.queries.insert_edges(valid_edges)
                        result.edges_created += len(valid_edges)

                    # Upsert file record with stat info
                    self.queries.upsert_file(rel_path, fhash, lang, len(valid_nodes),
                                             size=fsize, modified_at=fmtime)

                    self.queries.conn.execute("COMMIT")
                except Exception:
                    self.queries.conn.execute("ROLLBACK")
                    raise

                result.files_indexed += 1
                result.nodes_created += len(valid_nodes)

                if extraction.errors:
                    result.errors.extend(extraction.errors)

            except Exception as e:
                result.files_errored += 1
                result.errors.append({
                    "message": str(e),
                    "file_path": rel_path,
                    "severity": "error",
                })
        # Post-processing (A1: only if files were actually indexed)
        if result.files_indexed > 0:
            # Resolve cross-file call edges
            result.resolve_result = resolve_edges(self.queries)

            # Populate unresolved_refs from unresolved/ambiguous edges
            self._populate_unresolved_refs(result.resolve_result)

            # Populate unresolved_refs from import edges with externality classification
            self._populate_import_unresolved()

            # Rebuild FTS index
            self.queries.rebuild_fts()

            # Framework detection
            result.framework_result = detect_frameworks(self.root_dir, self.queries)

        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def _populate_unresolved_refs(self, resolve_result: ResolveResult) -> None:
        """Populate unresolved_refs table from edges with provenance 'unresolved'."""
        if not resolve_result or resolve_result.unresolved == 0:
            return

        # Query all unresolved edges
        rows = self.queries._exec("""
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
            self.queries.insert_unresolved_ref(ref)

    def _populate_import_unresolved(self) -> None:
        """Populate unresolved_refs from import edges, classifying external vs internal.

        For each import edge whose target is not in the nodes table:
          - Check if the imported module name maps to a project file
          - If no project file matches → is_external=True (SDK/lib)
          - If project file matches but not in nodes → is_external=False (index gap)
        """
        import os

        project_files = {row["path"] for row in self.queries.get_all_files()}

        rows = self.queries._exec("""
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

            # For "from X import Y" the target_text is "X.Y" but module is "X".
            # Try progressively shorter prefixes to find the actual module.
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
            self.queries.insert_unresolved_ref(ref)


def _module_in_project(module_name: str, source_file: str, project_files: set[str]) -> bool:
    """Check if a Python module name corresponds to a known project file."""
    import os

    module_path = module_name.replace(".", "/")

    # Direct file: "mylib.utils" → "mylib/utils.py"
    if f"{module_path}.py" in project_files:
        return True

    # Package init: "mylib.utils" → "mylib/utils/__init__.py"
    if f"{module_path}/__init__.py" in project_files:
        return True

    # Relative imports: "from . import sibling" or "from ..parent import foo"
    # . = current package (go up 0), .. = parent (go up 1), ... = grandparent (go up 2)
    if module_name.startswith("."):
        source_dir = os.path.dirname(source_file)
        dot_count = 0
        for c in module_name:
            if c == ".":
                dot_count += 1
            else:
                break
        relative_rest = module_name[dot_count:]
        levels_up = dot_count - 1  # 1 dot = current, 2 dots = parent, etc.
        parts = source_dir.split("/")
        if levels_up <= len(parts):
            base = "/".join(parts[:len(parts)-levels_up]) if levels_up > 0 else source_dir
            candidate = os.path.join(base, relative_rest.replace(".", "/")).replace("\\", "/")
            if f"{candidate}.py" in project_files:
                return True
            if f"{candidate}/__init__.py" in project_files:
                return True

    return False
