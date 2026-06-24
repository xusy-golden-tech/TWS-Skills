"""SqliteStore — SQLite-persistent graph storage implementation.

Implements the full Store ABC (~48 abstract methods) using SQLite as the
persistence backend with write buffering for performance.

Internal components:
    - _conn_mgr: ConnectionManager (from store/connection)
    - _qb: QueryBuilder (from store/query_builder, private)
    - _node_buffer / _edge_buffer / _ref_buffer (write buffers)
    - _auto_flush_size = 10000 — auto-flush threshold
    - _in_transaction: bool — single-level transaction flag
    - _def_index_cache / _def_index_dirty — lazy def index

Write buffer strategy:
    insert_node/insert_edge/insert_unresolved_ref accumulate in buffers.
    flush() bulk-writes via executemany with FTS-trigger lifecycle management.
    commit() auto-flushes before COMMIT.

Per design-store-schema.md section 6.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Iterator, Optional

from .connection import ConnectionManager
from .exceptions import (
    EdgeNotFoundError,
    StoreClosedError,
    StoreError,
    TransactionError,
)
from .interface import Store
from .query_builder import QueryBuilder
from .types import Direction

# ---------------------------------------------------------------------------
# Module-level constants — FTS trigger DDL
# ---------------------------------------------------------------------------

_FTS_TRIGGERS_DROP = (
    "DROP TRIGGER IF EXISTS nodes_fts_ai",
    "DROP TRIGGER IF EXISTS nodes_fts_ad",
    "DROP TRIGGER IF EXISTS nodes_fts_au",
)

_FTS_TRIGGERS_CREATE = (
    """CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
        INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
        VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
    END""",
    """CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
        VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
    END""",
    """CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
        VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
        INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
        VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
    END""",
)

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_INSERT_NODES_SQL = (
    "INSERT OR REPLACE INTO nodes"
    " (id, kind, name, qualified_name, file_path, language,"
    "  start_line, end_line, signature, docstring,"
    "  visibility, is_abstract, is_exported, decorators,"
    "  framework, properties, body, updated_at)"
    " VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?)"
)

_INSERT_EDGES_SQL = (
    "INSERT OR IGNORE INTO edges"
    " (source, target, target_text, kind, source_loc, provenance)"
    " VALUES (?,?,?,?,?,?)"
)

_INSERT_REFS_SQL = (
    "INSERT INTO unresolved_refs"
    " (from_node_id, reference_name, reference_kind, line, col,"
    "  candidates, source, file_path, language, is_external)"
    " VALUES (?,?,?,?,?, ?,?,?,?,?)"
)

# Required fields for validation
_REQUIRED_NODE_FIELDS = [
    "id", "kind", "name", "qualified_name",
    "file_path", "language", "start_line", "end_line",
]
_REQUIRED_EDGE_FIELDS = ["source", "kind"]
_REQUIRED_REF_FIELDS = ["from_node_id", "reference_name", "file_path", "language"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_closed(closed: bool) -> None:
    """Raise StoreClosedError if the Store has been closed."""
    if closed:
        raise StoreClosedError("Store has been closed")


def _check_missing_fields(record: dict, required: list[str], label: str) -> None:
    """Raise ValueError if any required field is missing or empty in *record*."""
    missing = [f for f in required if f not in record or record[f] == ""]
    if missing:
        raise ValueError(f"{label} missing required fields: {missing}")


def _to_json(val):
    """JSON-serialise *val*, returning None for falsy inputs."""
    return json.dumps(val, ensure_ascii=False) if val else None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_dict(row) -> dict:
    """Convert an sqlite3.Row (or dict) to a plain dict."""
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


# ---------------------------------------------------------------------------
# SqliteStore
# ---------------------------------------------------------------------------


class SqliteStore(Store):
    """SQLite-persistent graph storage implementation.

    Uses an internal QueryBuilder for all SQL operations; QueryBuilder is
    never exposed outside this module.

    Supports write buffering — ``insert_node`` / ``insert_edge`` /
    ``insert_unresolved_ref`` accumulate in memory buffers and are
    bulk-written on ``flush()`` or ``commit()``.

    Per design-store-schema.md section 6.
    """

    def __init__(self, db_path: str, auto_flush_size: int = 10000):
        # ── connection + query builder ──────────────────────────────
        self._conn_mgr = ConnectionManager(db_path)
        self._qb = QueryBuilder(self._conn_mgr)

        # ── write buffers ───────────────────────────────────────────
        self._node_buffer: list[dict] = []
        self._edge_buffer: list[dict] = []
        self._ref_buffer: list[dict] = []
        self._auto_flush_size = auto_flush_size

        # ── transaction state ───────────────────────────────────────
        self._in_transaction = False

        # ── def index cache ─────────────────────────────────────────
        self._def_index_cache: Optional[dict[str, str]] = None
        self._def_index_dirty = False

        # ── lifecycle ───────────────────────────────────────────────
        self._closed = False

    # =========================================================================
    # 1. Connection management
    # =========================================================================

    def close(self) -> None:
        """Close the connection and release resources. Idempotent.

        Flushes any remaining buffered writes before closing so that
        unpushed inserts are not silently discarded.
        """
        if self._closed:
            return
        # Flush before marking closed — flush() checks _closed
        try:
            self.flush()
        except Exception:
            pass  # best-effort — discard buffered data on flush failure
        self._closed = True
        self._node_buffer.clear()
        self._edge_buffer.clear()
        self._ref_buffer.clear()
        self._def_index_cache = None
        self._conn_mgr.close()

    # =========================================================================
    # 2. Transaction management
    # =========================================================================

    def begin(self) -> None:
        """Begin a transaction. Single-level semantic — nesting not allowed."""
        _check_closed(self._closed)
        if self._in_transaction:
            raise TransactionError("Already in a transaction")
        self._conn_mgr.conn.execute("BEGIN")
        self._in_transaction = True

    def commit(self) -> None:
        """Commit the active transaction. Auto-flushes buffered writes."""
        _check_closed(self._closed)
        if not self._in_transaction:
            raise TransactionError("Not in a transaction")
        self.flush()
        self._conn_mgr.conn.execute("COMMIT")
        self._in_transaction = False

    def rollback(self) -> None:
        """Roll back the active transaction. Clears buffers."""
        _check_closed(self._closed)
        if not self._in_transaction:
            raise TransactionError("Not in a transaction")
        # Clear all write buffers — data never reached the DB
        self._node_buffer.clear()
        self._edge_buffer.clear()
        self._ref_buffer.clear()
        self._def_index_dirty = True
        self._conn_mgr.conn.execute("ROLLBACK")
        self._in_transaction = False

    # =========================================================================
    # 3. Node CRUD
    # =========================================================================

    def insert_node(self, node: dict) -> None:
        """Insert a single node. REPLACE if id already exists.

        The node is buffered in memory; it is not written to SQLite until
        ``flush()`` or ``commit()`` is called.
        """
        _check_closed(self._closed)
        _check_missing_fields(node, _REQUIRED_NODE_FIELDS, "Node")
        self._node_buffer.append(dict(node))
        self._def_index_dirty = True
        self._maybe_auto_flush()

    def insert_nodes(self, nodes: list[dict]) -> None:
        """Insert a batch of nodes."""
        _check_closed(self._closed)
        for i, n in enumerate(nodes):
            try:
                _check_missing_fields(n, _REQUIRED_NODE_FIELDS, "Node")
            except ValueError:
                raise ValueError(f"Node at index {i} missing required fields")
        self._node_buffer.extend(dict(n) for n in nodes)
        self._def_index_dirty = True
        self._maybe_auto_flush()

    def get_node_by_id(self, id: str) -> Optional[dict]:
        """Look up a node by its id."""
        _check_closed(self._closed)
        # Check buffer first (nodes may not be flushed yet)
        for n in self._node_buffer:
            if n["id"] == id:
                return dict(n)
        row = self._qb.get_node_by_id(id)
        return _row_to_dict(row)

    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, dict]:
        """Batch-lookup nodes by ids."""
        _check_closed(self._closed)
        if not ids:
            return {}
        # Check buffer for unflushed nodes
        result: dict[str, dict] = {}
        buffered = {n["id"]: n for n in self._node_buffer}
        remaining = []
        for nid in ids:
            if nid in buffered:
                result[nid] = dict(buffered[nid])
            else:
                remaining.append(nid)
        # DB query for remaining
        if remaining:
            rows = self._qb.get_nodes_by_ids(remaining)
            for nid, row in rows.items():
                result[nid] = _row_to_dict(row)
        return result

    def delete_nodes_by_file(
        self, file_path: str, kind: Optional[str] = None
    ) -> None:
        """Delete all nodes (and cascade-delete edges) for a file."""
        _check_closed(self._closed)
        if kind is not None:
            self._conn_mgr.conn.execute(
                "DELETE FROM nodes WHERE file_path = ? AND kind = ?",
                (file_path, kind),
            )
        else:
            self._conn_mgr.conn.execute(
                "DELETE FROM nodes WHERE file_path = ?", (file_path,)
            )
        # Remove matching nodes from buffer
        if kind is not None:
            self._node_buffer = [
                n for n in self._node_buffer
                if not (n["file_path"] == file_path and n.get("kind") == kind)
            ]
        else:
            self._node_buffer = [
                n for n in self._node_buffer
                if n["file_path"] != file_path
            ]
        self._def_index_dirty = True

    def update_node_property(self, node_id: str, properties: dict) -> None:
        """Merge-update a node's extended properties."""
        _check_closed(self._closed)
        # Check buffer first
        for n in self._node_buffer:
            if n["id"] == node_id:
                existing_raw = n.get("properties", "{}")
                existing = json.loads(existing_raw) if existing_raw else {}
                existing.update(properties)
                n["properties"] = json.dumps(existing)
                return
        # Node is in DB
        row = self._qb.get_node_by_id(node_id)
        if row is None:
            raise KeyError(f"Node '{node_id}' not found")
        existing_raw = row["properties"] or "{}"
        existing = json.loads(existing_raw) if existing_raw else {}
        existing.update(properties)
        self._conn_mgr.conn.execute(
            "UPDATE nodes SET properties = ? WHERE id = ?",
            (json.dumps(existing), node_id),
        )

    def iter_nodes_by_kind(
        self, kind: str, batch_size: int = 1000
    ) -> Iterator[dict]:
        """Stream nodes filtered by kind using LIMIT/OFFSET."""
        _check_closed(self._closed)
        offset = 0
        while True:
            rows = self._conn_mgr.conn.execute(
                "SELECT * FROM nodes WHERE kind = ? LIMIT ? OFFSET ?",
                (kind, batch_size, offset),
            ).fetchall()
            if not rows:
                break
            yield from (_row_to_dict(r) for r in rows)
            offset += batch_size

    def iter_all_nodes(self, batch_size: int = 1000) -> Iterator[dict]:
        """Stream all nodes using LIMIT/OFFSET."""
        _check_closed(self._closed)
        offset = 0
        while True:
            rows = self._conn_mgr.conn.execute(
                "SELECT * FROM nodes LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            yield from (_row_to_dict(r) for r in rows)
            offset += batch_size

    def count_nodes(self) -> int:
        """Return the total number of nodes."""
        _check_closed(self._closed)
        return self._qb.get_node_count()

    def iter_nodes_by_file(self, file_path: str) -> Iterator[dict]:
        """Stream all nodes belonging to the given file."""
        _check_closed(self._closed)
        if not file_path:
            raise ValueError("file_path must be non-empty")
        rows = self._conn_mgr.conn.execute(
            "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        yield from (_row_to_dict(r) for r in rows)

    # =========================================================================
    # 4. Edge CRUD
    # =========================================================================

    def insert_edge(self, edge: dict) -> None:
        """Insert a single edge. INSERT OR IGNORE semantics."""
        _check_closed(self._closed)
        _check_missing_fields(edge, _REQUIRED_EDGE_FIELDS, "Edge")
        self._edge_buffer.append(dict(edge))
        self._maybe_auto_flush()

    def insert_edges(self, edges: list[dict]) -> None:
        """Insert a batch of edges."""
        _check_closed(self._closed)
        for i, e in enumerate(edges):
            try:
                _check_missing_fields(e, _REQUIRED_EDGE_FIELDS, "Edge")
            except ValueError:
                raise ValueError(f"Edge at index {i} missing required fields")
        self._edge_buffer.extend(dict(e) for e in edges)
        self._maybe_auto_flush()

    def get_outgoing_edges(
        self, source_id: str, kinds: Optional[list[str]] = None
    ) -> list[dict]:
        """Get edges originating from *source_id*."""
        _check_closed(self._closed)
        if not source_id:
            raise ValueError("source_id must be non-empty")
        rows = self._qb.get_outgoing_edges(source_id, kinds)
        return [_row_to_dict(r) for r in rows]

    def get_incoming_edges(
        self, target_id: str, kinds: Optional[list[str]] = None
    ) -> list[dict]:
        """Get edges pointing to *target_id*."""
        _check_closed(self._closed)
        if not target_id:
            raise ValueError("target_id must be non-empty")
        rows = self._qb.get_incoming_edges(target_id, kinds)
        return [_row_to_dict(r) for r in rows]

    def get_edges_between(
        self, source_id: str, target_id: str
    ) -> list[dict]:
        """Get all edges from *source_id* to *target_id*."""
        _check_closed(self._closed)
        if not source_id or not target_id:
            raise ValueError("source_id and target_id must be non-empty")
        rows = self._conn_mgr.conn.execute(
            "SELECT * FROM edges WHERE source = ? AND target = ?",
            (source_id, target_id),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_edge_target(
        self,
        edge_id: int,
        new_target: str,
        provenance: str = "resolved",
    ) -> None:
        """Update an edge's target node and provenance."""
        _check_closed(self._closed)
        if not new_target:
            raise ValueError("new_target must be non-empty")
        # Verify edge exists
        row = self._conn_mgr.conn.execute(
            "SELECT rowid FROM edges WHERE rowid = ?", (edge_id,)
        ).fetchone()
        if row is None:
            raise EdgeNotFoundError(f"Edge id={edge_id} not found")
        self._conn_mgr.conn.execute(
            "UPDATE edges SET target = ?, provenance = ? WHERE rowid = ?",
            (new_target, provenance, edge_id),
        )

    def update_edge_provenance(
        self, edge_id: int, provenance: str
    ) -> None:
        """Update only an edge's provenance field."""
        _check_closed(self._closed)
        if not provenance:
            raise ValueError("provenance must be non-empty")
        row = self._conn_mgr.conn.execute(
            "SELECT rowid FROM edges WHERE rowid = ?", (edge_id,)
        ).fetchone()
        if row is None:
            raise EdgeNotFoundError(f"Edge id={edge_id} not found")
        self._conn_mgr.conn.execute(
            "UPDATE edges SET provenance = ? WHERE rowid = ?",
            (provenance, edge_id),
        )

    def delete_edges_by_source(self, source_id: str) -> None:
        """Delete all edges originating from *source_id*."""
        _check_closed(self._closed)
        self._conn_mgr.conn.execute(
            "DELETE FROM edges WHERE source = ?", (source_id,)
        )

    def delete_edges_by_kind(self, kind: str) -> None:
        """Delete all edges of a given *kind*."""
        _check_closed(self._closed)
        if not kind:
            raise ValueError("kind must be non-empty")
        self._conn_mgr.conn.execute(
            "DELETE FROM edges WHERE kind = ?", (kind,)
        )

    def count_edges(self) -> int:
        """Return the total number of edges."""
        _check_closed(self._closed)
        return self._qb.get_edge_count()

    def iter_all_edges(self, batch_size: int = 1000) -> Iterator[dict]:
        """Stream all edges using LIMIT/OFFSET."""
        _check_closed(self._closed)
        offset = 0
        while True:
            rows = self._conn_mgr.conn.execute(
                "SELECT * FROM edges LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            yield from (_row_to_dict(r) for r in rows)
            offset += batch_size

    def get_dangling_edges(
        self, kind: Optional[str] = None
    ) -> list[dict]:
        """Return all edges whose target is empty (dangling edges).

        An edge is dangling iff target_text is non-empty AND target is empty
        or not associated with any node.
        """
        _check_closed(self._closed)
        if kind is not None:
            rows = self._conn_mgr.conn.execute(
                """SELECT e.* FROM edges e
                   WHERE e.target_text IS NOT NULL
                     AND e.target_text != ''
                     AND (e.target IS NULL
                          OR e.target = ''
                          OR e.target NOT IN (SELECT id FROM nodes))
                     AND e.kind = ?
                   ORDER BY e.source""",
                (kind,),
            ).fetchall()
        else:
            rows = self._conn_mgr.conn.execute(
                """SELECT e.* FROM edges e
                   WHERE e.target_text IS NOT NULL
                     AND e.target_text != ''
                     AND (e.target IS NULL
                          OR e.target = ''
                          OR e.target NOT IN (SELECT id FROM nodes))
                   ORDER BY e.source""",
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def iter_edges(
        self,
        kind: Optional[str] = None,
        with_source_info: bool = False,
    ) -> Iterator[dict]:
        """Iterate over edges with optional kind filter.

        When *with_source_info* is True, each edge dict includes
        ``source_file`` and ``source_language`` fields via a JOIN.
        """
        _check_closed(self._closed)
        if kind is not None:
            if with_source_info:
                sql = (
                    "SELECT e.*, n.file_path AS source_file, n.language AS source_language"
                    " FROM edges e"
                    " JOIN nodes n ON e.source = n.id"
                    " WHERE e.kind = ?"
                )
                rows = self._conn_mgr.conn.execute(sql, (kind,)).fetchall()
                yield from (_row_to_dict(r) for r in rows)
            else:
                rows = self._conn_mgr.conn.execute(
                    "SELECT * FROM edges WHERE kind = ?", (kind,)
                ).fetchall()
                yield from (_row_to_dict(r) for r in rows)
        else:
            if with_source_info:
                sql = (
                    "SELECT e.*, n.file_path AS source_file, n.language AS source_language"
                    " FROM edges e"
                    " JOIN nodes n ON e.source = n.id"
                )
                rows = self._conn_mgr.conn.execute(sql).fetchall()
                yield from (_row_to_dict(r) for r in rows)
            else:
                # Fall back to cursor-based paging for full scan
                offset = 0
                batch_size = 1000
                while True:
                    rows = self._conn_mgr.conn.execute(
                        "SELECT * FROM edges LIMIT ? OFFSET ?",
                        (batch_size, offset),
                    ).fetchall()
                    if not rows:
                        break
                    yield from (_row_to_dict(r) for r in rows)
                    offset += batch_size

    def iter_edges_from(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
        batch_size: int = 1000,
    ) -> Iterator[dict]:
        """Stream edges from/to/both for a node using LIMIT/OFFSET."""
        _check_closed(self._closed)
        if not node_id:
            raise ValueError("node_id must be non-empty")

        yielded: set[int] = set()

        def _yield_batch(sql: str, params: tuple):
            offset = 0
            while True:
                paged_sql = f"{sql} LIMIT ? OFFSET ?"
                rows = self._conn_mgr.conn.execute(
                    paged_sql, params + (batch_size, offset)
                ).fetchall()
                if not rows:
                    break
                for r in rows:
                    rid = r["id"]
                    if rid not in yielded:
                        yielded.add(rid)
                        yield _row_to_dict(r)
                if len(rows) < batch_size:
                    break
                offset += batch_size

        if direction in ("out", "both"):
            if kinds:
                placeholders = ",".join("?" * len(kinds))
                sql = f"SELECT * FROM edges WHERE source = ? AND kind IN ({placeholders})"
                params = (node_id,) + tuple(kinds)
            else:
                sql = "SELECT * FROM edges WHERE source = ?"
                params = (node_id,)
            yield from _yield_batch(sql, params)

        if direction in ("in", "both"):
            if kinds:
                placeholders = ",".join("?" * len(kinds))
                sql = f"SELECT * FROM edges WHERE target = ? AND kind IN ({placeholders})"
                params = (node_id,) + tuple(kinds)
            else:
                sql = "SELECT * FROM edges WHERE target = ?"
                params = (node_id,)
            yield from _yield_batch(sql, params)

    # =========================================================================
    # 5. Graph traversal
    # =========================================================================

    def get_neighbors(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
    ) -> list[dict]:
        """Get a node's neighbours.

        Returns list of {node, edge, direction} dicts, deduplicated by
        neighbour node id.
        """
        _check_closed(self._closed)
        if not node_id:
            raise ValueError("node_id must be non-empty")

        seen: dict[str, dict] = {}

        def _add(nid, edge_dict, dir_label):
            if nid in seen:
                return
            row = self._conn_mgr.conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (nid,)
            ).fetchone()
            if row is None:
                return
            seen[nid] = {
                "node": _row_to_dict(row),
                "edge": edge_dict,
                "direction": dir_label,
            }

        if direction in ("out", "both"):
            if kinds:
                placeholders = ",".join("?" * len(kinds))
                rows = self._conn_mgr.conn.execute(
                    f"SELECT * FROM edges WHERE source = ? AND kind IN ({placeholders})",
                    [node_id] + list(kinds),
                ).fetchall()
            else:
                rows = self._conn_mgr.conn.execute(
                    "SELECT * FROM edges WHERE source = ?", (node_id,)
                ).fetchall()
            for r in rows:
                _add(r["target"], _row_to_dict(r), "out")

        if direction in ("in", "both"):
            if kinds:
                placeholders = ",".join("?" * len(kinds))
                rows = self._conn_mgr.conn.execute(
                    f"SELECT * FROM edges WHERE target = ? AND kind IN ({placeholders})",
                    [node_id] + list(kinds),
                ).fetchall()
            else:
                rows = self._conn_mgr.conn.execute(
                    "SELECT * FROM edges WHERE target = ?", (node_id,)
                ).fetchall()
            for r in rows:
                _add(r["source"], _row_to_dict(r), "in")

        return list(seen.values())

    def get_neighbors_batch(
        self,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
    ) -> dict[str, list[dict]]:
        """Batch-get neighbours for multiple nodes."""
        _check_closed(self._closed)
        if not node_ids:
            raise ValueError("node_ids must be non-empty")

        return {
            nid: self.get_neighbors(nid, kinds=kinds, direction=direction)
            for nid in node_ids
        }

    def find_paths(
        self,
        from_id: str,
        to_id: str,
        kinds: Optional[list[str]] = None,
        max_depth: int = 5,
    ) -> Optional[list[dict]]:
        """Find the shortest path between two nodes using BFS.

        Returns list of {node, via_edge} dicts, or None if unreachable.
        """
        _check_closed(self._closed)
        if not from_id or not to_id:
            raise ValueError("from_id and to_id must be non-empty")

        if kinds is None:
            kinds = ["calls", "references", "imports", "contains"]

        # Check endpoints exist
        from_row = self._conn_mgr.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (from_id,)
        ).fetchone()
        to_row = self._conn_mgr.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (to_id,)
        ).fetchone()
        if from_row is None or to_row is None:
            return None

        if from_id == to_id:
            return [{"node": _row_to_dict(from_row), "via_edge": None}]

        visited = {from_id}
        queue = deque([(from_id, [{"node": _row_to_dict(from_row), "via_edge": None}])])

        placeholders = ",".join("?" * len(kinds))

        while queue:
            current, path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue

            # Neighbors via outgoing edges
            rows = self._conn_mgr.conn.execute(
                f"SELECT * FROM edges WHERE source = ? AND kind IN ({placeholders})",
                [current] + list(kinds),
            ).fetchall()
            for edge in rows:
                nxt = edge["target"]
                if nxt in visited:
                    continue
                visited.add(nxt)
                nxt_node = self._conn_mgr.conn.execute(
                    "SELECT * FROM nodes WHERE id = ?", (nxt,)
                ).fetchone()
                if nxt_node is None:
                    continue
                new_path = path + [{
                    "node": _row_to_dict(nxt_node),
                    "via_edge": _row_to_dict(edge),
                }]
                if nxt == to_id:
                    return new_path
                queue.append((nxt, new_path))

        return None

    # =========================================================================
    # 6. File management
    # =========================================================================

    def upsert_file(
        self,
        path: str,
        content_hash: str,
        language: str,
        node_count: int = 0,
        size: int = 0,
        modified_at: int = 0,
    ) -> None:
        """Insert or update a file record."""
        _check_closed(self._closed)
        if not path or not content_hash or not language:
            raise ValueError("path, content_hash, language must be non-empty")
        self._qb.upsert_file(path, content_hash, language, node_count,
                             size=size, modified_at=modified_at)

    def get_file(self, path: str) -> Optional[dict]:
        """Look up a file record by path."""
        _check_closed(self._closed)
        row = self._qb.get_file_by_path(path)
        return _row_to_dict(row)

    def get_all_files(self) -> list[dict]:
        """Return all file records, sorted by path."""
        _check_closed(self._closed)
        rows = self._qb.get_all_files()
        return [_row_to_dict(r) for r in rows]

    def get_file_stats(self) -> dict[str, tuple[int, int]]:
        """Return {path: (size, modified_at)} mapping for stat pre-filter."""
        _check_closed(self._closed)
        return self._qb.get_file_stats()

    def delete_file(self, path: str) -> None:
        """Delete a file and all its associated data (nodes, edges, unresolved refs).

        FK CASCADE handles edges and unresolved_refs linked to this file's nodes.
        """
        _check_closed(self._closed)
        # Delete unresolved_refs by file_path (not covered by FK cascade)
        self._conn_mgr.conn.execute(
            "DELETE FROM unresolved_refs WHERE file_path = ?", (path,)
        )
        # Delete nodes (FK cascades edges and remaining unresolved_refs)
        self._conn_mgr.conn.execute(
            "DELETE FROM nodes WHERE file_path = ?", (path,)
        )
        self._conn_mgr.conn.execute(
            "DELETE FROM files WHERE path = ?", (path,)
        )
        self._def_index_dirty = True

    # =========================================================================
    # 7. Search
    # =========================================================================

    def fts_search(
        self,
        query: str,
        limit: int = 20,
        kind_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
        path_filter: Optional[str] = None,
    ) -> list[dict]:
        """Full-text search over nodes (three-tier: FTS5 BM25 -> LIKE -> fuzzy)."""
        _check_closed(self._closed)
        if not query:
            raise ValueError("query must be non-empty")

        # Use QueryBuilder's three-tier search, then apply extra filters
        rows = self._qb.search_nodes(query, limit * 2)
        results: list[dict] = []
        for row in rows:
            if kind_filter is not None and row["kind"] != kind_filter:
                continue
            if language_filter is not None and row["language"] != language_filter:
                continue
            if path_filter is not None and path_filter not in (row["file_path"] or ""):
                continue
            results.append(_row_to_dict(row))
            if len(results) >= limit:
                break

        # Convert to SearchResult format
        return [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "qualified_name": r.get("qualified_name", ""),
                "kind": r.get("kind", ""),
                "file_path": r.get("file_path", ""),
                "language": r.get("language", ""),
                "signature": r.get("signature"),
                "docstring": r.get("docstring"),
                "rank": r.get("rank"),
            }
            for r in results
        ]

    def search_by_def_index(self, qualified_name: str) -> Optional[str]:
        """Exact lookup of a node_id via the def index.

        Uses the in-memory cache if available; falls back to database query.
        """
        _check_closed(self._closed)
        if not qualified_name:
            raise ValueError("qualified_name must be non-empty")
        self._ensure_def_index()
        return self._def_index_cache.get(qualified_name)

    def search_by_field_qualified(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Enhanced search with field:value qualified syntax."""
        _check_closed(self._closed)
        if not query:
            raise ValueError("query must be non-empty")
        rows = self._qb.search_nodes_field_qualified(query, limit)
        results = [_row_to_dict(r) for r in rows]
        return [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "qualified_name": r.get("qualified_name", ""),
                "kind": r.get("kind", ""),
                "file_path": r.get("file_path", ""),
                "language": r.get("language", ""),
                "signature": r.get("signature"),
                "docstring": r.get("docstring"),
                "rank": r.get("rank"),
            }
            for r in results
        ]

    # =========================================================================
    # 8. Unresolved references
    # =========================================================================

    def insert_unresolved_ref(self, ref: dict) -> None:
        """Insert a single unresolved reference record."""
        _check_closed(self._closed)
        _check_missing_fields(ref, _REQUIRED_REF_FIELDS, "UnresolvedRef")
        self._ref_buffer.append(dict(ref))
        self._maybe_auto_flush()

    def insert_unresolved_refs(self, refs: list[dict]) -> None:
        """Insert a batch of unresolved reference records."""
        _check_closed(self._closed)
        for i, r in enumerate(refs):
            try:
                _check_missing_fields(r, _REQUIRED_REF_FIELDS, "UnresolvedRef")
            except ValueError:
                raise ValueError(f"Ref at index {i} missing required fields")
        self._ref_buffer.extend(dict(r) for r in refs)
        self._maybe_auto_flush()

    def get_unresolved_refs(
        self, file_path: Optional[str] = None
    ) -> list[dict]:
        """Get unresolved references, optionally filtered by file_path."""
        _check_closed(self._closed)
        if file_path is not None:
            rows = self._qb.get_unresolved_refs_by_file(file_path)
        else:
            rows = self._qb.get_all_unresolved_refs()
        return [_row_to_dict(r) for r in rows]

    def clear_unresolved_refs(self) -> None:
        """Clear all unresolved references."""
        _check_closed(self._closed)
        self._qb.clear_unresolved_refs()
        self._ref_buffer.clear()

    # =========================================================================
    # 9. Batch operations
    # =========================================================================

    def flush(self) -> None:
        """Flush buffered writes to persistent storage.

        Write sequence:
            1. If no buffers → return (no-op)
            2. Auto-BEGIN (only if not already in a transaction)
            3. Disable FTS triggers
            4. executemany INSERT nodes
            5. Rebuild FTS index
            6. Re-enable FTS triggers
            7. Batch-validate edge sources → executemany valid edges
            8. executemany INSERT unresolved refs
            9. Clear all buffers
            10. Set _def_index_dirty = True
            11. Auto-COMMIT (only if auto-began transaction)

        On error: buffers are NOT cleared (retry-safe). Auto-began
        transaction is rolled back.
        """
        _check_closed(self._closed)

        if not self._node_buffer and not self._edge_buffer and not self._ref_buffer:
            return

        auto_txn = False
        if not self._in_transaction:
            self._conn_mgr.conn.execute("BEGIN")
            auto_txn = True

        try:
            # ── 1. Write nodes (with FTS trigger batching) ──
            if self._node_buffer:
                self._disable_fts_triggers()
                try:
                    self._conn_mgr.conn.executemany(
                        _INSERT_NODES_SQL, self._nodes_to_params()
                    )
                finally:
                    self._enable_fts_triggers()
                self._conn_mgr.conn.execute(
                    "INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')"
                )
                self._node_buffer.clear()

            # ── 2. Write edges (with source validation) ──
            if self._edge_buffer:
                valid_edges = self._validate_edge_sources()
                if valid_edges:
                    self._conn_mgr.conn.executemany(
                        _INSERT_EDGES_SQL, valid_edges
                    )
                self._edge_buffer.clear()

            # ── 3. Write unresolved refs ──
            if self._ref_buffer:
                self._conn_mgr.conn.executemany(
                    _INSERT_REFS_SQL, self._refs_to_params()
                )
                self._ref_buffer.clear()

            self._def_index_dirty = True

            if auto_txn:
                self._conn_mgr.conn.execute("COMMIT")

        except Exception:
            if auto_txn:
                try:
                    self._conn_mgr.conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise StoreError("Flush failed — buffers preserved for retry")

    # =========================================================================
    # 10. Maintenance / statistics
    # =========================================================================

    def optimize(self) -> None:
        """Perform storage optimization.

        SqliteStore: PRAGMA optimize + WAL checkpoint.
        """
        _check_closed(self._closed)
        try:
            self._conn_mgr.conn.execute("PRAGMA optimize")
            self._conn_mgr.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass  # fail silently

    def clear(self) -> None:
        """Clear all data (nodes, edges, files, unresolved refs)."""
        _check_closed(self._closed)
        self._qb.clear()
        self._node_buffer.clear()
        self._edge_buffer.clear()
        self._ref_buffer.clear()
        self._def_index_dirty = True

    def rebuild_fts(self) -> None:
        """Rebuild the FTS5 full-text-search index.

        Uses ``INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')`` to
        fully rebuild the index from the content table.
        """
        _check_closed(self._closed)
        self._qb.rebuild_fts()

    def stats(self) -> dict:
        """Return storage statistics."""
        _check_closed(self._closed)
        base = self._qb.get_stats()
        # Add unresolved count and db size
        ur_count = self._conn_mgr.conn.execute(
            "SELECT COUNT(*) FROM unresolved_refs"
        ).fetchone()[0]
        result = dict(base)
        result["unresolved_count"] = ur_count
        # Get database file size
        try:
            # For file-based databases
            db_path = self._conn_mgr.db_path
            if db_path != ":memory:" and os.path.exists(db_path):
                result["db_size_bytes"] = os.path.getsize(db_path)
        except Exception:
            pass
        return result

    def build_def_index(self) -> dict[str, str]:
        """Build and return the full qualified_name -> node_id mapping."""
        _check_closed(self._closed)
        self._ensure_def_index()
        return dict(self._def_index_cache) if self._def_index_cache else {}

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _maybe_auto_flush(self) -> None:
        """Auto-flush if any buffer exceeds the auto-flush threshold."""
        if (len(self._node_buffer) >= self._auto_flush_size or
                len(self._edge_buffer) >= self._auto_flush_size or
                len(self._ref_buffer) >= self._auto_flush_size):
            self.flush()

    def _nodes_to_params(self) -> list[tuple]:
        """Convert node buffer to list of parameter tuples for executemany."""
        now = _now_ms()
        params = []
        for n in self._node_buffer:
            params.append((
                n["id"],
                n["kind"],
                n["name"],
                n["qualified_name"],
                n["file_path"],
                n["language"],
                n.get("start_line", 0),
                n.get("end_line", 0),
                n.get("signature"),
                n.get("docstring"),
                n.get("visibility"),
                n.get("is_abstract", 0),
                n.get("is_exported", 0),
                _to_json(n.get("decorators")),
                n.get("framework"),
                n.get("properties", "{}"),
                n.get("body"),
                now,
            ))
        return params

    def _edges_to_params(self) -> list[tuple]:
        """Convert edge buffer to parameter tuples for executemany."""
        return [
            (
                e["source"],
                e["target"],
                e.get("target_text"),
                e["kind"],
                e.get("source_loc"),
                e.get("provenance", "tree-sitter"),
            )
            for e in self._edge_buffer
        ]

    def _refs_to_params(self) -> list[tuple]:
        """Convert ref buffer to parameter tuples for executemany."""
        return [
            (
                r["from_node_id"],
                r["reference_name"],
                r.get("reference_kind", "call"),
                r.get("line", 0),
                r.get("col", 0),
                _to_json(r.get("candidates")),
                r.get("source", "tree-sitter"),
                r.get("file_path", ""),
                r.get("language", ""),
                r.get("is_external", 0),
            )
            for r in self._ref_buffer
        ]

    def _validate_edge_sources(self) -> list[tuple]:
        """Batch-validate edge source existence, return params for valid edges only.

        If > 50% of sources are missing, logs a warning but does not
        raise an exception (targets may be cross-file).
        """
        if not self._edge_buffer:
            return []

        source_ids = list({e["source"] for e in self._edge_buffer})
        existing = self._get_existing_ids(source_ids)

        valid = []
        for e in self._edge_buffer:
            if e["source"] in existing:
                valid.append((
                    e["source"],
                    e["target"],
                    e.get("target_text"),
                    e["kind"],
                    e.get("source_loc"),
                    e.get("provenance", "tree-sitter"),
                ))

        return valid

    def _get_existing_ids(self, ids: list[str]) -> set[str]:
        """Return the subset of *ids* that exist in the nodes table."""
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        rows = self._conn_mgr.conn.execute(
            f"SELECT id FROM nodes WHERE id IN ({placeholders})", ids
        ).fetchall()
        return {r["id"] for r in rows}

    # ── FTS trigger management ──────────────────────────────────────

    def _disable_fts_triggers(self) -> None:
        """Drop FTS5 triggers to speed up bulk node inserts."""
        for stmt in _FTS_TRIGGERS_DROP:
            self._conn_mgr.conn.execute(stmt)

    def _enable_fts_triggers(self) -> None:
        """Re-create FTS5 triggers after bulk node insert completes."""
        for stmt in _FTS_TRIGGERS_CREATE:
            self._conn_mgr.conn.execute(stmt)

    # ── Def index cache ─────────────────────────────────────────────

    def _ensure_def_index(self) -> None:
        """Build the def index cache if it is dirty or uninitialised."""
        if self._def_index_cache is not None and not self._def_index_dirty:
            return
        rows = self._conn_mgr.conn.execute(
            "SELECT id, qualified_name FROM nodes"
        ).fetchall()
        self._def_index_cache = {r["qualified_name"]: r["id"] for r in rows}
        self._def_index_dirty = False
