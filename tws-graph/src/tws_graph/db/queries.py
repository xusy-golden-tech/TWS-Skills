"""QueryBuilder — prepared statements for all graph operations.

Patterns ported from CodeGraph's queries.ts:
- Batch get (get_nodes_by_ids) to avoid N+1 per graph step
- INSERT OR IGNORE for edge deduplication
- Transaction-wrapped batch inserts
- Content-hash gating in upsert_file
"""

import sqlite3
import json
import hashlib
from typing import Optional

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID without line number (unlike CodeGraph)."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _to_json(val):
    return json.dumps(val, ensure_ascii=False) if val is not None else None

# ---------------------------------------------------------------------------
# QueryBuilder
# ---------------------------------------------------------------------------

class QueryBuilder:
    """All database reads/writes for the code graph."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._stmts: dict[str, sqlite3.Cursor] = {}

    def _prepare(self, name: str, sql: str):
        if name not in self._stmts:
            self._stmts[name] = self.conn.execute
        return name, sql

    def _exec(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def _execmany(self, sql: str, seq):
        return self.conn.executemany(sql, seq)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def insert_node(self, node: dict):
        self._exec("""
            INSERT OR REPLACE INTO nodes
                (id, kind, name, qualified_name, file_path, language,
                 start_line, end_line, signature, docstring,
                 visibility, is_abstract, is_exported, decorators,
                 framework, updated_at)
            VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)
        """, (
            node["id"], node["kind"], node["name"], node["qualified_name"],
            node["file_path"], node["language"],
            node.get("start_line", 0), node.get("end_line", 0),
            node.get("signature"), node.get("docstring"),
            node.get("visibility"), node.get("is_abstract", 0),
            node.get("is_exported", 0), _to_json(node.get("decorators")),
            node.get("framework"), _now_ms(),
        ))

    def insert_nodes(self, nodes: list[dict]):
        """Batch insert (caller manages transaction)."""
        for n in nodes:
            self.insert_node(n)

    def get_node_by_id(self, node_id: str) -> Optional[sqlite3.Row]:
        row = self._exec("SELECT * FROM nodes WHERE id = ?", (node_id,))
        return row.fetchone()

    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, sqlite3.Row]:
        """Batch fetch — the N+1 killer. Returns {id: Row}."""
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._exec(
            f"SELECT * FROM nodes WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {r["id"]: r for r in rows}

    def get_nodes_by_name(self, name: str) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM nodes WHERE name = ?", (name,)
        ).fetchall()

    def get_nodes_by_file(self, file_path: str) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()

    def search_nodes(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        """Three-tier search: FTS5 BM25 → LIKE fallback → fuzzy (edit distance)."""
        # Tier 1: FTS5 with BM25 ranking
        results = self._search_fts(query, limit)
        if results:
            return results

        # Tier 2: LIKE fallback (handles substrings FTS misses, e.g. "alcul" in "calculate")
        pattern = f"%{query}%"
        results = self._exec(
            "SELECT * FROM nodes WHERE name LIKE ? OR qualified_name LIKE ? LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        if results:
            return results

        # Tier 3: fuzzy search — edit distance ≤ 2, only for queries ≥ 3 chars
        if len(query) >= 3:
            return self._search_fuzzy(query, limit)
        return []

    def _search_fts(self, query: str, limit: int) -> list[sqlite3.Row]:
        """BM25-ranked FTS5 search with column weights."""
        try:
            # Escape special FTS5 chars, build prefix-friendly query
            fts_query = self._build_fts_query(query)
            # Column weights: name=20, qualified_name=10, signature=3, docstring=1
            rows = self._exec("""
                SELECT n.*, rank FROM nodes_fts
                JOIN nodes n ON nodes_fts.rowid = n.rowid
                WHERE nodes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit)).fetchall()
            return rows
        except Exception:
            return []

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Build a safe FTS5 query string with prefix matching.

        Bare terms use FTS5 default case-insensitive matching.
        Double-quoted phrases ARE case-sensitive in FTS5, so we avoid them
        for single-word prefix queries.
        """
        terms = query.strip().split()
        escaped = []
        for t in terms:
            if ":" in t:
                t = t.split(":", 1)[1]
            # Escape double-quotes (would trigger phrase mode)
            t = t.replace('"', '""')
            if t:
                escaped.append(f'{t}*')
        return " AND ".join(escaped) if escaped else query

    def _search_fuzzy(self, query: str, limit: int) -> list[sqlite3.Row]:
        """Fuzzy search using edit distance (Levenshtein) <= 2."""
        # Collect candidates by first letter to narrow the search space
        prefix = query[0]
        candidates = self._exec(
            "SELECT * FROM nodes WHERE name LIKE ? LIMIT 200",
            (f"{prefix}%",)
        ).fetchall()
        results = []
        for row in candidates:
            name = row["name"]
            if _edit_distance(query.lower(), name.lower()) <= 2:
                results.append(row)
                if len(results) >= limit:
                    break
        return results

    def search_nodes_field_qualified(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        """Search with field-qualified syntax: kind:function lang:python api.

        Example queries:
          kind:function api                     → functions named/api-named "api"
          lang:python kind:class controller     → python classes matching "controller"
          path:src/auth handleRequest           → symbols in src/auth matching "handleRequest"
        """
        parsed = _parse_field_qualifiers(query)
        text_terms = parsed["text"]
        filters = parsed["filters"]

        # Build WHERE from filters
        where_parts = []
        params = []
        for field, value in filters.items():
            if field == "kind":
                where_parts.append("n.kind = ?")
                params.append(value)
            elif field == "lang":
                where_parts.append("n.language = ?")
                params.append(value)
            elif field == "path":
                where_parts.append("n.file_path LIKE ?")
                params.append(f"%{value}%")
            elif field == "visibility":
                where_parts.append("n.visibility = ?")
                params.append(value)
            elif field == "framework":
                where_parts.append("n.framework = ?")
                params.append(value)

        if not text_terms:
            # Pure filter query — no text search term
            sql = "SELECT n.* FROM nodes n"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            sql += " LIMIT ?"
            params.append(limit)
            return self._exec(sql, params).fetchall()

        # Try FTS5 with field filters
        fts_query = self._build_fts_query(" ".join(text_terms))
        try:
            sql = """
                SELECT n.*, rank FROM nodes_fts
                JOIN nodes n ON nodes_fts.rowid = n.rowid
                WHERE nodes_fts MATCH ?
            """
            params_full = [fts_query]
            if where_parts:
                sql += " AND " + " AND ".join(where_parts)
                params_full.extend(params)
            sql += " ORDER BY rank LIMIT ?"
            params_full.append(limit)
            return self._exec(sql, params_full).fetchall()
        except Exception:
            pass

        # Fallback: LIKE with field filters
        pattern = f"%{' '.join(text_terms)}%"
        sql = "SELECT n.* FROM nodes n WHERE (n.name LIKE ? OR n.qualified_name LIKE ?)"
        params_full = [pattern, pattern]
        if where_parts:
            sql += " AND " + " AND ".join(where_parts)
            params_full.extend(params)
        sql += " LIMIT ?"
        params_full.append(limit)
        return self._exec(sql, params_full).fetchall()

    def delete_nodes_by_file(self, file_path: str):
        self._exec("DELETE FROM nodes WHERE file_path = ?", (file_path,))

    def get_node_count(self) -> int:
        return self._exec("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def iterate_nodes_by_kind(self, kind: str, batch_size: int = 1000):
        """Lazy iterator for large result sets (ported from CodeGraph)."""
        offset = 0
        while True:
            rows = self._exec(
                "SELECT * FROM nodes WHERE kind = ? LIMIT ? OFFSET ?",
                (kind, batch_size, offset),
            ).fetchall()
            if not rows:
                break
            yield from rows
            offset += batch_size

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def insert_edge(self, edge: dict):
        self._exec("""
            INSERT OR IGNORE INTO edges
                (source, target, target_text, kind, source_loc, provenance)
            VALUES (?,?,?,?,?,?)
        """, (
            edge["source"], edge["target"], edge.get("target_text"),
            edge["kind"],
            edge.get("source_loc"), edge.get("provenance", "tree-sitter"),
        ))

    def insert_edges(self, edges: list[dict]):
        """Batch insert edges. Source must exist in DB; target may be cross-file (not yet indexed).

        Caller manages transaction.
        """
        if not edges:
            return

        # Verify source nodes exist (targets may be cross-file or external)
        source_ids = {e["source"] for e in edges}
        existing = self.get_nodes_by_ids(list(source_ids))
        existing_ids = set(existing.keys())

        for e in edges:
            if e["source"] in existing_ids:
                self.insert_edge(e)

    def get_outgoing_edges(self, source_id: str, kinds: Optional[list[str]] = None) -> list[sqlite3.Row]:
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            return self._exec(
                f"SELECT * FROM edges WHERE source = ? AND kind IN ({placeholders})",
                [source_id] + kinds,
            ).fetchall()
        return self._exec(
            "SELECT * FROM edges WHERE source = ?", (source_id,)
        ).fetchall()

    def get_incoming_edges(self, target_id: str, kinds: Optional[list[str]] = None) -> list[sqlite3.Row]:
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            return self._exec(
                f"SELECT * FROM edges WHERE target = ? AND kind IN ({placeholders})",
                [target_id] + kinds,
            ).fetchall()
        return self._exec(
            "SELECT * FROM edges WHERE target = ?", (target_id,)
        ).fetchall()

    def get_edge_count(self) -> int:
        return self._exec("SELECT COUNT(*) FROM edges").fetchone()[0]

    def get_dangling_call_edges(self) -> list[sqlite3.Row]:
        """Return call edges whose target does not exist in nodes."""
        return self._exec("""
            SELECT e.rowid as edge_rowid, e.id as edge_id, e.source, e.target, e.target_text, e.kind, e.source_loc
            FROM edges e
            LEFT JOIN nodes tgt ON e.target = tgt.id
            WHERE e.kind = 'calls' AND tgt.id IS NULL AND e.target_text IS NOT NULL
        """).fetchall()

    def get_all_callable_nodes(self) -> list[sqlite3.Row]:
        """Return all nodes that can be call targets (method/function/class/interface/object/property)."""
        return self._exec("""
            SELECT id, qualified_name, kind, file_path
            FROM nodes
            WHERE kind IN ('method', 'function', 'class', 'interface', 'object', 'companion_object', 'property')
        """).fetchall()

    def update_edge_target(self, edge_rowid: int, new_target: str, provenance: str):
        """Update an edge's target and provenance."""
        self._exec("""
            UPDATE edges SET target = ?, provenance = ? WHERE rowid = ?
        """, (new_target, provenance, edge_rowid))

    def mark_edge_provenance(self, edge_rowid: int, provenance: str):
        """Mark an edge's provenance without changing the target."""
        self._exec("""
            UPDATE edges SET provenance = ? WHERE rowid = ?
        """, (provenance, edge_rowid))

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def upsert_file(self, file_path: str, content_hash: str, language: str, node_count: int = 0,
                     size: int = 0, modified_at: int = 0):
        self._exec("""
            INSERT OR REPLACE INTO files (path, content_hash, language, node_count, indexed_at, size, modified_at)
            VALUES (?,?,?,?,?,?,?)
        """, (file_path, content_hash, language, node_count, _now_ms(), size, modified_at))

    def get_file_by_path(self, file_path: str) -> Optional[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM files WHERE path = ?", (file_path,)
        ).fetchone()

    def get_all_files(self) -> list[sqlite3.Row]:
        return self._exec("SELECT * FROM files ORDER BY path").fetchall()

    def get_file_stats(self) -> dict[str, tuple[int, int]]:
        """Return {path: (size, modified_at)} for stat pre-filtering."""
        rows = self._exec(
            "SELECT path, size, modified_at FROM files"
        ).fetchall()
        return {r["path"]: (r["size"], r["modified_at"]) for r in rows}

    def delete_file(self, file_path: str):
        """Remove all data for a file (caller manages transaction)."""
        self._exec("DELETE FROM nodes WHERE file_path = ?", (file_path,))
        self._exec("DELETE FROM files WHERE path = ?", (file_path,))

    def get_file_count(self) -> int:
        return self._exec("SELECT COUNT(*) FROM files").fetchone()[0]

    # ------------------------------------------------------------------
    # Stats / maintenance
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "node_count": self.get_node_count(),
            "edge_count": self.get_edge_count(),
            "file_count": self.get_file_count(),
        }

    # ------------------------------------------------------------------
    # Unresolved refs
    # ------------------------------------------------------------------

    def insert_unresolved_ref(self, ref: dict):
        """Insert an unresolved reference record."""
        self._exec("""
            INSERT INTO unresolved_refs
                (from_node_id, reference_name, reference_kind, line, col,
                 candidates, file_path, language, is_external)
            VALUES (?,?,?,?,?, ?,?,?,?)
        """, (
            ref["from_node_id"], ref["reference_name"],
            ref.get("reference_kind", "call"),
            ref.get("line", 0), ref.get("col", 0),
            _to_json(ref.get("candidates")),
            ref.get("file_path", ""), ref.get("language", ""),
            ref.get("is_external", 0),
        ))

    def insert_unresolved_refs(self, refs: list[dict]):
        """Batch insert unresolved refs (caller manages transaction)."""
        for ref in refs:
            self.insert_unresolved_ref(ref)

    def get_unresolved_refs_by_file(self, file_path: str) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM unresolved_refs WHERE file_path = ?",
            (file_path,)
        ).fetchall()

    def get_unresolved_refs_by_node(self, node_id: str) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM unresolved_refs WHERE from_node_id = ?",
            (node_id,)
        ).fetchall()

    def get_all_unresolved_refs(self) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT * FROM unresolved_refs ORDER BY file_path, line"
        ).fetchall()

    def get_unresolved_ref_count(self) -> int:
        return self._exec("SELECT COUNT(*) FROM unresolved_refs").fetchone()[0]

    def delete_unresolved_refs_by_file(self, file_path: str):
        self._exec("DELETE FROM unresolved_refs WHERE file_path = ?", (file_path,))

    def update_unresolved_candidates(self, ref_id: int, candidates: list[str]):
        self._exec(
            "UPDATE unresolved_refs SET candidates = ? WHERE id = ?",
            (_to_json(candidates), ref_id)
        )

    def clear_unresolved_refs(self):
        self._exec("DELETE FROM unresolved_refs")

    # ------------------------------------------------------------------
    # FTS maintenance
    # ------------------------------------------------------------------

    def rebuild_fts(self):
        """Full rebuild of the FTS index (use after bulk inserts bypass triggers)."""
        self._exec("INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')")

    # ------------------------------------------------------------------
    # Stats / maintenance
    # ------------------------------------------------------------------

    def clear(self):
        """Remove all data (caller manages transaction)."""
        self._exec("DELETE FROM edges")
        self._exec("DELETE FROM nodes")
        self._exec("DELETE FROM files")
        self._exec("DELETE FROM unresolved_refs")


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------

def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        cur_row = [i + 1]
        for j, c2 in enumerate(s2):
            insert = prev_row[j + 1] + 1
            delete = cur_row[j] + 1
            sub = prev_row[j] + (0 if c1 == c2 else 1)
            cur_row.append(min(insert, delete, sub))
        prev_row = cur_row
    return prev_row[-1]


def _parse_field_qualifiers(query: str) -> dict:
    """Parse field:value qualifiers from a search query string.

    Returns: {"text": [remaining terms], "filters": {field: value, ...}}

    Example: "kind:function lang:python api" →
        {"text": ["api"], "filters": {"kind": "function", "lang": "python"}}
    """
    KNOWN_FIELDS = {"kind", "lang", "language", "path", "visibility", "framework"}
    text_terms = []
    filters = {}

    for token in query.strip().split():
        if ":" in token:
            field, _, value = token.partition(":")
            field = field.lower()
            if field in KNOWN_FIELDS and value:
                # Normalize "lang" → "language"
                if field == "language":
                    field = "lang"
                filters[field] = value
                continue
        text_terms.append(token)

    return {"text": text_terms, "filters": filters}
