"""SQL builder for all graph database read/write operations.

Internal module — used only by SqliteStore.  Not exported through
``store/__init__.py``.  External code must go through the Store interface.

Migrated from ``db/queries.py``.  The main change is that the constructor
accepts ``ConnectionManager`` (from ``store/connection``) instead of a raw
``sqlite3.Connection``, enabling per-connection prepared-statement LRU caching
through ``ConnectionManager.get_statement()``.

For backward compatibility the constructor also accepts a raw
``sqlite3.Connection`` — when detected it wraps it in a minimal adapter so
existing callers (tests, CLI) continue to work without changes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID without line number."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_json(val):
    """JSON-serialise *val*, returning None for falsy inputs."""
    return json.dumps(val, ensure_ascii=False) if val else None


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

    Example: "kind:function lang:python api" ->
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
                # Normalize "lang" -> "language"
                if field == "language":
                    field = "lang"
                filters[field] = value
                continue
        text_terms.append(token)

    return {"text": text_terms, "filters": filters}


# ---------------------------------------------------------------------------
# Connection adapter (for backward compat with raw sqlite3.Connection)
# ---------------------------------------------------------------------------

class _RawConnectionAdapter:
    """Minimal adapter so QueryBuilder can accept a bare ``sqlite3.Connection``.

    Provides the same ``.conn`` and ``.get_statement()`` interface as
    ``ConnectionManager`` so the rest of the code is identical regardless of
    which backend is used.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._stmt_cache: dict[str, str] = {}

    def get_statement(self, key: str, sql: str) -> str:
        """LRU-cached SQL text store (mirrors ConnectionManager.get_statement)."""
        if key not in self._stmt_cache:
            if len(self._stmt_cache) >= 50:
                oldest = next(iter(self._stmt_cache))
                del self._stmt_cache[oldest]
            self._stmt_cache[key] = sql
        return self._stmt_cache[key]


# ---------------------------------------------------------------------------
# QueryBuilder
# ---------------------------------------------------------------------------

class QueryBuilder:
    """All database reads/writes for the code graph.

    Accepts a ``ConnectionManager`` (from ``store.connection``) so that
    prepared-statement caching is shared across all consumers.  For backward
    compatibility a raw ``sqlite3.Connection`` is also accepted — it is
    transparently wrapped in a minimal adapter with the same interface.
    """

    def __init__(self, conn_mgr):
        """*conn_mgr* may be a ``ConnectionManager`` or raw ``sqlite3.Connection``."""
        if isinstance(conn_mgr, sqlite3.Connection):
            self._conn_mgr = _RawConnectionAdapter(conn_mgr)
        else:
            self._conn_mgr = conn_mgr

    # ------------------------------------------------------------------
    # Backward-compat aliases
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Backward-compat: direct ``sqlite3.Connection`` access.

        Several modules (orchestrator, framework detect) access
        ``queries.conn`` directly for transaction control.  This
        property delegates to ``self._conn_mgr.conn``.
        """
        return self._conn_mgr.conn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _exec(self, sql: str, params=()):
        """Execute *sql* with *params* on the managed connection."""
        return self._conn_mgr.conn.execute(sql, params)

    def _execmany(self, sql: str, seq):
        """``executemany`` wrapper."""
        return self._conn_mgr.conn.executemany(sql, seq)

    def _stmt(self, key: str, sql: str) -> str:
        """Return the SQL text for *key*, caching through ConnectionManager.

        Python's sqlite3 handles compiled-statement caching internally, so
        this merely caches the SQL text string to avoid repeated string
        construction in hot paths.  The same ``key`` always yields the same
        string object.
        """
        return self._conn_mgr.get_statement(key, sql)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def insert_node(self, node: dict):
        self._exec("""
            INSERT OR REPLACE INTO nodes
                (id, kind, name, qualified_name, file_path, language,
                 start_line, end_line, signature, docstring,
                 visibility, is_abstract, is_exported, decorators,
                 framework, properties, body, body_hash, updated_at)
            VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?,?)
        """, (
            node["id"], node["kind"], node["name"], node["qualified_name"],
            node["file_path"], node["language"],
            node.get("start_line", 0), node.get("end_line", 0),
            node.get("signature"), node.get("docstring"),
            node.get("visibility"), node.get("is_abstract", 0),
            node.get("is_exported", 0), _to_json(node.get("decorators")),
            node.get("framework"), node.get("properties", "{}"),
            node.get("body"), node.get("body_hash"), _now_ms(),
        ))

    def insert_nodes(self, nodes: list[dict]):
        """Batch insert (caller manages transaction)."""
        for n in nodes:
            self.insert_node(n)

    def get_node_by_id(self, node_id: str) -> Optional[sqlite3.Row]:
        sql = self._stmt("qb_get_node_by_id",
                         "SELECT * FROM nodes WHERE id = ?")
        row = self._exec(sql, (node_id,))
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
        sql = self._stmt("qb_get_nodes_by_name",
                         "SELECT * FROM nodes WHERE name = ?")
        return self._exec(sql, (name,)).fetchall()

    def get_nodes_by_file(self, file_path: str) -> list[sqlite3.Row]:
        sql = self._stmt("qb_get_nodes_by_file",
                         "SELECT * FROM nodes WHERE file_path = ?")
        return self._exec(sql, (file_path,)).fetchall()

    def update_node_lines(self, node_id: str, start_line: int, end_line: int):
        """P33c: Update line numbers for a node whose body hasn't changed."""
        self._exec(
            "UPDATE nodes SET start_line = ?, end_line = ?, updated_at = ? WHERE id = ?",
            (start_line, end_line, _now_ms(), node_id),
        )

    def delete_single_node(self, node_id: str):
        """Delete a single node (edges cascade via FK)."""
        self._exec("DELETE FROM nodes WHERE id = ?", (node_id,))

    def search_nodes(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        """Three-tier search: FTS5 BM25 -> LIKE fallback -> fuzzy (edit distance)."""
        # Tier 1: FTS5 with BM25 ranking
        results = self._search_fts(query, limit)
        if results:
            return results

        # Tier 2: LIKE fallback
        pattern = f"%{query}%"
        results = self._exec(
            "SELECT * FROM nodes WHERE name LIKE ? OR qualified_name LIKE ? LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
        if results:
            return results

        # Tier 3: fuzzy search — edit distance <= 2, only for queries >= 3 chars
        if len(query) >= 3:
            return self._search_fuzzy(query, limit)
        return []

    def search_fts(self, query: str, limit: int = 50) -> list[sqlite3.Row]:
        """BM25-ranked FTS5 search with column weights.

        Public API — callers such as semantic search signals use this
        to query the FTS5 index directly (e.g. BM25Signal).

        Returns rows with an additional ``rank`` column (FTS5 BM25 score).
        """
        return self._search_fts(query, limit)

    def _search_fts(self, query: str, limit: int) -> list[sqlite3.Row]:
        """BM25-ranked FTS5 search with column weights (internal impl)."""
        try:
            fts_query = self._build_fts_query(query)
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
        """Search with field-qualified syntax: kind:function lang:python api."""
        parsed = _parse_field_qualifiers(query)
        text_terms = parsed["text"]
        filters = parsed["filters"]

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
        sql = self._stmt("qb_node_count",
                         "SELECT COUNT(*) FROM nodes")
        return self._exec(sql).fetchone()[0]

    def iterate_nodes_by_kind(self, kind: str, batch_size: int = 1000):
        """Lazy iterator for large result sets."""
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
                (source, target, target_text, kind, source_loc, provenance, properties)
            VALUES (?,?,?,?,?,?,?)
        """, (
            edge["source"], edge["target"], edge.get("target_text"),
            edge["kind"],
            edge.get("source_loc"), edge.get("provenance", "tree-sitter"),
            edge.get("properties"),
        ))

    def insert_edges(self, edges: list[dict]):
        """Batch insert edges. Source must exist in DB; target may be cross-file.

        Caller manages transaction.
        """
        if not edges:
            return

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
        sql = self._stmt("qb_outgoing_all",
                         "SELECT * FROM edges WHERE source = ?")
        return self._exec(sql, (source_id,)).fetchall()

    def get_incoming_edges(self, target_id: str, kinds: Optional[list[str]] = None) -> list[sqlite3.Row]:
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            return self._exec(
                f"SELECT * FROM edges WHERE target = ? AND kind IN ({placeholders})",
                [target_id] + kinds,
            ).fetchall()
        sql = self._stmt("qb_incoming_all",
                         "SELECT * FROM edges WHERE target = ?")
        return self._exec(sql, (target_id,)).fetchall()

    def get_edge_count(self) -> int:
        sql = self._stmt("qb_edge_count",
                         "SELECT COUNT(*) FROM edges")
        return self._exec(sql).fetchone()[0]

    def get_dangling_call_edges(self) -> list[sqlite3.Row]:
        """Return call edges whose target does not exist in nodes."""
        return self._exec("""
            SELECT e.rowid as edge_rowid, e.id as edge_id, e.source, e.target, e.target_text, e.kind, e.source_loc
            FROM edges e
            LEFT JOIN nodes tgt ON e.target = tgt.id
            WHERE e.kind = 'calls' AND tgt.id IS NULL AND e.target_text IS NOT NULL
        """).fetchall()

    def get_dangling_structural_edges(self) -> list[sqlite3.Row]:
        """Return extends/implements edges whose target does not exist in nodes."""
        return self._exec("""
            SELECT e.rowid as edge_rowid, e.id as edge_id, e.source, e.target, e.target_text, e.kind, e.source_loc
            FROM edges e
            LEFT JOIN nodes tgt ON e.target = tgt.id
            WHERE e.kind IN ('extends', 'implements') AND tgt.id IS NULL AND e.target_text IS NOT NULL
        """).fetchall()

    def get_all_callable_nodes(self) -> list[sqlite3.Row]:
        """Return all nodes that can be call targets."""
        return self._exec("""
            SELECT id, qualified_name, kind, file_path
            FROM nodes
            WHERE kind IN ('method', 'function', 'class', 'interface', 'object', 'companion_object', 'property')
        """).fetchall()

    def update_edge_target(self, edge_rowid: int, new_target: str, provenance: str):
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
        sql = self._stmt("qb_file_by_path",
                         "SELECT * FROM files WHERE path = ?")
        return self._exec(sql, (file_path,)).fetchone()

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
        sql = self._stmt("qb_file_count",
                         "SELECT COUNT(*) FROM files")
        return self._exec(sql).fetchone()[0]

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
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self):
        """Remove all data (caller manages transaction)."""
        self._exec("DELETE FROM edges")
        self._exec("DELETE FROM nodes")
        self._exec("DELETE FROM files")
        self._exec("DELETE FROM unresolved_refs")
