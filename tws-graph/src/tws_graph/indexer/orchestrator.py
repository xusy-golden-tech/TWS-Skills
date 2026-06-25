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
from ..edge_resolver import resolve_edges, resolve_structural_edges, resolve_overrides, resolve_instantiates, ResolveResult, is_call_target_external
from .scanner import scan_directory
from .language_detect import detect_language
from .parser import extract_full
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

    def index_all(self, force: bool = False, parallel: bool = True, deep: bool = False) -> IndexResult:
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
            return par_orch.index_all(self.queries, force=force, deep=deep)

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
                    extraction = extract_full(rel_path, content, lang)

                    # P33: Compute body_hashes from source content
                    _compute_body_hashes(extraction.nodes, content)

                    # P33: Get old nodes for function-level incremental
                    existing_file = self.queries.get_file_by_path(rel_path)
                    if existing_file:
                        old_nodes = self.queries.get_nodes_by_file(rel_path)
                        old_by_qname = {
                            n["qualified_name"]: {
                                "id": n["id"], "body_hash": n["body_hash"],
                                "start_line": n["start_line"], "end_line": n["end_line"],
                            }
                            for n in old_nodes
                        }
                    else:
                        old_by_qname = {}

                    # Separate: changed vs unchanged vs new vs deleted
                    changed_nodes = []
                    unchanged_drifts = []  # (old_id, new_start, new_end)
                    new_qnames = {n.get("qualified_name") for n in extraction.nodes}
                    old_qnames = set(old_by_qname.keys())

                    for n in extraction.nodes:
                        qname = n.get("qualified_name")
                        if not qname:
                            continue
                        old = old_by_qname.get(qname)
                        if old and old.get("body_hash") == n.get("body_hash"):
                            # Unchanged body — P33c: fix line number drift only
                            if (old["start_line"] != n["start_line"] or
                                    old["end_line"] != n["end_line"]):
                                unchanged_drifts.append(
                                    (old["id"], n["start_line"], n["end_line"])
                                )
                        else:
                            changed_nodes.append(n)

                    # Delete old nodes that changed or disappeared
                    for qname in old_qnames:
                        if qname in new_qnames:
                            old_info = old_by_qname[qname]
                            new_node = next(
                                (n for n in extraction.nodes
                                 if n.get("qualified_name") == qname), None
                            )
                            if new_node and old_info["body_hash"] == new_node.get("body_hash"):
                                continue  # unchanged — keep
                            # Changed → delete old node
                            self.queries.delete_single_node(old_info["id"])
                        else:
                            # Removed from file
                            self.queries.delete_single_node(old_by_qname[qname]["id"])

                    # Insert changed/new nodes
                    valid_nodes = [
                        n for n in changed_nodes
                        if n.get("id") and n.get("kind") and n.get("name")
                    ]
                    if valid_nodes:
                        self.queries.insert_nodes(valid_nodes)

                    # Insert edges from changed nodes
                    if extraction.edges:
                        changed_ids = {n["id"] for n in valid_nodes}
                        valid_edges = [
                            e for e in extraction.edges
                            if e["source"] in changed_ids
                        ]
                        if valid_edges:
                            self.queries.insert_edges(valid_edges)
                        result.edges_created += len(valid_edges)

                    # P33c: Update line numbers for unchanged nodes
                    for old_id, new_start, new_end in unchanged_drifts:
                        self.queries.update_node_lines(old_id, new_start, new_end)

                    total_nodes = (len(valid_nodes) + len(unchanged_drifts) +
                                   sum(1 for q in old_qnames & new_qnames
                                       if old_by_qname[q]["body_hash"] ==
                                       next((n.get("body_hash") for n in extraction.nodes
                                             if n.get("qualified_name") == q), None)))
                    # Simplified: count = changed + unchanged
                    total_nodes = len(valid_nodes) + len(unchanged_drifts)
                    # Also count nodes that were kept (in old_by_qname and in new but unchanged)
                    kept_count = 0
                    for qname in old_qnames & new_qnames:
                        old_info = old_by_qname[qname]
                        new_node = next((n for n in extraction.nodes
                                        if n.get("qualified_name") == qname), None)
                        if new_node and old_info["body_hash"] == new_node.get("body_hash"):
                            kept_count += 1
                    total_nodes = len(valid_nodes) + kept_count

                    # Upsert file record with stat info
                    self.queries.upsert_file(rel_path, fhash, lang, total_nodes,
                                             size=fsize, modified_at=fmtime)

                    self.queries.conn.execute("COMMIT")
                except Exception:
                    self.queries.conn.execute("ROLLBACK")
                    raise

                result.files_indexed += 1
                result.nodes_created += total_nodes

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
            resolve_structural_edges(self.queries)
            resolve_overrides(self.queries)
            resolve_instantiates(self.queries)

            # Populate unresolved_refs from unresolved/ambiguous edges
            self._populate_unresolved_refs(result.resolve_result)

            # Populate unresolved_refs from import edges with externality classification
            self._populate_import_unresolved()

            # P28: FTS triggers keep index in sync — full rebuild is redundant
            # Framework detection
            result.framework_result = detect_frameworks(self.root_dir, self.queries)

        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def _populate_unresolved_refs(self, resolve_result: ResolveResult) -> None:
        """Populate unresolved_refs table from edges with provenance 'unresolved'.

        Classifies each unresolved call target as external (stdlib / built-in /
        third-party) or internal (possible index gap) via
        :func:`is_call_target_external`.
        """
        if not resolve_result or resolve_result.unresolved == 0:
            return

        project_files = frozenset(
            row["path"] for row in self.queries.get_all_files()
        )

        # Query all unresolved edges
        rows = self.queries._exec("""
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


# ---------------------------------------------------------------------------
# P33: Incremental indexing helpers
# ---------------------------------------------------------------------------

def _compute_body_hashes(nodes: list[dict], content: str) -> None:
    """P33a: Compute body_hash for each node from source content.

    Extracts the source text between start_line and end_line for each node
    and stores a truncated SHA256 as ``body_hash``. Nodes without valid
    line ranges are skipped (hash remains None).
    """
    if not content:
        return
    lines = content.split("\n")
    for n in nodes:
        start = n.get("start_line")
        end = n.get("end_line")
        if start and end and start <= end:
            # Extract body text from source lines (1-indexed)
            body_text = "\n".join(lines[start - 1:end])
            n["body_hash"] = hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16]
