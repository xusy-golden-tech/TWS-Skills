"""Store abstract base class — the single data-access interface.

All modules (CypherEngine, Pipeline, GraphAlgorithms, LSPResolver) depend
only on this interface. Replacing the storage backend requires only implementing
all abstract methods.

Methods are organized into 10 groups; each group corresponds to a distinct
data operation domain.

Per design-store-schema.md section 5.2.
"""

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from .types import (
    Direction,
    EdgeRecord,
    FileRecord,
    NodeRecord,
    SearchResult,
    StoreStats,
    UnresolvedRefRecord,
)


class Store(ABC):
    """Abstract interface for graph data storage.

    All queries and algorithms depend only on this interface. Replacing the
    storage backend only requires implementing every abstract method.

    General conventions:
    - All returned lists/dicts exclude records marked as deleted.
    - Methods do not silently swallow exceptions (unless explicitly stated).
    - Batch methods prefer internal buffer + flush strategy.
    """

    # =========================================================================
    # 1. Connection management
    # =========================================================================

    @abstractmethod
    def close(self) -> None:
        """Close the connection and release resources.

        Pre-condition: none (idempotent — may be called multiple times).
        Post-condition: connection closed; subsequent operations raise
            StoreClosedError.
        Exceptions: none (idempotent; repeated close is a no-op).
        Performance: O(1).
        """
        ...

    # =========================================================================
    # 2. Transaction management
    # =========================================================================

    @abstractmethod
    def begin(self) -> None:
        """Begin a transaction. Single-level semantic — nesting is not allowed.

        Pre-condition: not currently in a transaction.
        Post-condition: in transaction context. Subsequent writes are not
            visible to other connections until commit.
        Exceptions:
            TransactionError — if called while already in a transaction
            (nested transaction).
        Performance: O(1).
        Notes:
            MemoryStore transactions are no-ops (in-memory operations are
            already atomic) but MUST be implemented for interface consistency.
        Convention:
            Pipeline (the caller) guarantees not to nest begin/commit/rollback
            on the same connection. Transaction boundary is
            "one begin → zero or more operations → one commit or rollback".
            Implementations use a single boolean flag (_in_transaction) to
            track transaction state — no counters needed.
        """
        ...

    @abstractmethod
    def commit(self) -> None:
        """Commit the active transaction.

        Pre-condition: inside an active transaction. Caller (Pipeline) guarantees
            no nested calls.
        Post-condition: transaction committed, changes durably persisted.
            Buffered writes are automatically flushed.
        Exceptions:
            TransactionError — if called without an active transaction.
        Performance: O(buffer_size) — buffered data is written.
        """
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the active transaction.

        Pre-condition: inside an active transaction. Caller (Pipeline) guarantees
            no nested calls.
        Post-condition: transaction rolled back, buffers cleared, data restored
            to pre-begin state.
        Exceptions:
            TransactionError — if called without an active transaction.
        Performance: O(1) — buffers are cleared.
        """
        ...

    # =========================================================================
    # 3. Node CRUD
    # =========================================================================

    @abstractmethod
    def insert_node(self, node: NodeRecord) -> None:
        """Insert a single node. REPLACE if id already exists.

        Pre-condition: *node* contains all required fields (id, kind, name,
            qualified_name, file_path, language, start_line, end_line).
        Post-condition: node exists in the Store (immediate in memory /
            buffered in SqliteStore).
        Exceptions:
            ValueError — a required field is missing.
        Performance: O(1) in-memory write (SqliteStore internally buffers).
        """
        ...

    @abstractmethod
    def insert_nodes(self, nodes: list[NodeRecord]) -> None:
        """Insert a batch of nodes.

        Equivalent to calling insert_node() for each node, but with batch
        semantics.

        Pre-condition: every node contains all required fields.
        Post-condition: all nodes exist in the Store.
        Exceptions:
            ValueError — any node is missing a required field.
        Performance: O(n) in-memory write (SqliteStore internally buffers).
        Convention:
            SqliteStore accumulates nodes in an internal buffer and does not
            execute SQL immediately. The actual write happens on flush() or
            commit().
        """
        ...

    @abstractmethod
    def get_node_by_id(self, id: str) -> Optional[NodeRecord]:
        """Look up a node by its id.

        Pre-condition: *id* is a non-empty string.
        Post-condition: no side effects.
        Exceptions: none (returns None for non-existent ids).
        Performance:
            SqliteStore: O(1) — primary-key index lookup.
            MemoryStore: O(1) — dict lookup.
        """
        ...

    @abstractmethod
    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, NodeRecord]:
        """Batch-lookup nodes by their ids.

        Pre-condition: *ids* is a non-empty list.
        Post-condition: no side effects.
        Exceptions: none (non-existent ids are omitted from the result dict).
        Performance:
            SqliteStore: O(n) — single SQL IN query.
            MemoryStore: O(n) — per-key lookup.
        Convention:
            Returns {id: NodeRecord} mapping. This is the core method for
            eliminating N+1 queries.
        """
        ...

    @abstractmethod
    def delete_nodes_by_file(
        self, file_path: str, kind: Optional[str] = None
    ) -> None:
        """Delete all nodes (and cascade-delete associated edges) for a file.

        Pre-condition: *file_path* is non-empty.
        Post-condition:
            kind=None — all nodes and their edges for this file are removed.
            kind specified — only nodes of that kind (and their edges) are removed.
        Exceptions: none (no-op when the file does not exist).
        Performance: O(n) — n = number of nodes + edges for this file.
        """
        ...

    @abstractmethod
    def update_node_property(self, node_id: str, properties: dict) -> None:
        """Merge-update a node's extended properties.

        Pre-condition: *node_id* exists; *properties* is a JSON-serializable dict.
        Post-condition: the node's ``properties`` JSON field is merge-updated
            (dict.update semantics, not replacement).
        Exceptions: KeyError — node_id does not exist.
        Performance: O(1) — single-row UPDATE.
        Purpose:
            Used by GraphAlgorithms to write computational results such as
            centrality scores, community ids, etc.
        """
        ...

    @abstractmethod
    def iter_nodes_by_kind(
        self, kind: str, batch_size: int = 1000
    ) -> Iterator[NodeRecord]:
        """Stream nodes filtered by kind.

        Pre-condition: *kind* is non-empty.
        Post-condition: no side effects. Concurrent modification of the Store
            during iteration has undefined behaviour.
        Exceptions: none.
        Performance:
            SqliteStore: uses LIMIT/OFFSET, O(batch_size) per batch.
            MemoryStore: O(n) to build list, then iterate.
        Convention:
            Returns an iterator rather than a list to avoid loading large
            result sets entirely into memory.
        """
        ...

    @abstractmethod
    def iter_all_nodes(self, batch_size: int = 1000) -> Iterator[NodeRecord]:
        """Stream all nodes.

        Pre-condition: none.
        Post-condition: same as iter_nodes_by_kind.
        Exceptions: none.
        Performance: same as iter_nodes_by_kind.
        """
        ...

    @abstractmethod
    def count_nodes(self) -> int:
        """Return the total number of nodes.

        Exceptions: none.
        Performance:
            SqliteStore: O(log n) — B-tree count (approximate).
            MemoryStore: O(1) — len(dict).
        """
        ...

    @abstractmethod
    def iter_nodes_by_file(
        self, file_path: str
    ) -> Iterator[NodeRecord]:
        """Stream all nodes belonging to the given file.

        Used by MemoryStore loading and incremental indexing for per-file
        queries. More efficient than iter_all_nodes() + in-memory filtering
        because the filtering happens at the storage layer using the
        file_path index.

        Pre-condition: *file_path* is non-empty.
        Post-condition: no side effects. Concurrent modification of the Store
            during iteration has undefined behaviour.
        Exceptions:
            ValueError — file_path is empty.
        Performance:
            SqliteStore: O(log n + k) using idx_nodes_file index;
            k = number of nodes in this file.
            MemoryStore: O(n) — linear scan with file_path filter.
        Convention:
            Returns an iterator rather than a list.
        """
        ...

    # =========================================================================
    # 4. Edge CRUD
    # =========================================================================

    @abstractmethod
    def insert_edge(self, edge: EdgeRecord) -> None:
        """Insert a single edge. INSERT OR IGNORE (skip if already exists).

        Pre-condition: *edge* contains source, target, kind fields.
        Post-condition: edge exists in the Store (may be buffered).
        Exceptions:
            ValueError — a required field is missing.
        Performance: O(1) write to buffer.
        """
        ...

    @abstractmethod
    def insert_edges(self, edges: list[EdgeRecord]) -> None:
        """Insert a batch of edges.

        Pre-condition: every edge contains required fields.
        Post-condition: all edges exist in the Store.
        Exceptions:
            ValueError — any edge is missing a required field.
        Performance: O(n) write to buffer.
        Convention:
            SqliteStore performs INSERT OR IGNORE in bulk on flush/commit.
            Source-node existence is batch-validated beforehand (target may
            be cross-file and is not validated).
            MemoryStore additionally updates the _outgoing and _incoming
            adjacency tables on write.
        """
        ...

    @abstractmethod
    def get_outgoing_edges(
        self, source_id: str, kinds: Optional[list[str]] = None
    ) -> list[EdgeRecord]:
        """Get edges originating from *source_id*.

        Pre-condition: *source_id* is non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — source_id is empty.
        Performance:
            SqliteStore: O(log n + m) using idx_edges_source_kind index.
            MemoryStore: O(1) adjacency lookup + O(k) kind filtering.
        """
        ...

    @abstractmethod
    def get_incoming_edges(
        self, target_id: str, kinds: Optional[list[str]] = None
    ) -> list[EdgeRecord]:
        """Get edges pointing to *target_id*.

        Pre-condition: *target_id* is non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — target_id is empty.
        Performance: same as get_outgoing_edges, using idx_edges_target_kind.
        """
        ...

    @abstractmethod
    def get_edges_between(
        self, source_id: str, target_id: str
    ) -> list[EdgeRecord]:
        """Get all edges from *source_id* to *target_id*.

        Pre-condition: both *source_id* and *target_id* are non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — either parameter is empty.
        Performance:
            SqliteStore: O(log n + m) using source + target compound condition.
            MemoryStore: O(k) — filter _outgoing[source_id] by target.
        """
        ...

    @abstractmethod
    def update_edge_target(
        self,
        edge_id: int,
        new_target: str,
        provenance: str = "resolved",
    ) -> None:
        """Update an edge's target node.

        Pre-condition: *edge_id* is valid (exists); *new_target* is non-empty.
        Post-condition: the edge's target is changed to *new_target* and
            provenance is updated.
        Exceptions:
            EdgeNotFoundError — edge_id does not exist.
            ValueError — new_target is empty.
        Performance: O(1) — primary-key update.
        """
        ...

    @abstractmethod
    def update_edge_provenance(
        self, edge_id: int, provenance: str
    ) -> None:
        """Update only an edge's provenance field, leaving target unchanged.

        Complements update_edge_target() — that method changes target and
        provenance together; this method changes only provenance. Used by
        CrossFileResolvePass to mark ambiguous/unresolved states.

        Pre-condition: *edge_id* is valid (exists); *provenance* is non-empty.
        Post-condition: the edge's provenance is updated; all other fields
            are unchanged.
        Exceptions:
            EdgeNotFoundError — edge_id does not exist.
            ValueError — provenance is empty.
        Performance: O(1) — primary-key update, single-field UPDATE.
        """
        ...

    @abstractmethod
    def delete_edges_by_source(self, source_id: str) -> None:
        """Delete all edges originating from *source_id*.

        Pre-condition: *source_id* is non-empty.
        Post-condition: all edges with this source are removed.
        Exceptions: none (no-op when source_id does not exist).
        Performance:
            SqliteStore: O(n) — n = number of edges for this source.
            MemoryStore: O(1) — removes dict entry.
        """
        ...

    @abstractmethod
    def delete_edges_by_kind(self, kind: str) -> None:
        """Delete all edges of a given *kind*.

        Pre-condition: *kind* is non-empty.
        Post-condition: all edges with this kind are removed.
        Exceptions: none (no-op when no edges match).  Raises ValueError if
            *kind* is empty.
        Performance:
            SqliteStore: O(n) — DELETE with WHERE kind=? clause.
            MemoryStore: O(n) — scans all outgoing edges.
        """
        ...

    @abstractmethod
    def count_edges(self) -> int:
        """Return the total number of edges.

        Exceptions: none.
        Performance:
            SqliteStore: O(log n) — B-tree count.
            MemoryStore: O(1) — accumulated counter.
        """
        ...

    @abstractmethod
    def iter_all_edges(self, batch_size: int = 1000) -> Iterator[EdgeRecord]:
        """Stream all edges.

        Pre-condition: none.
        Post-condition: no side effects. Concurrent modification of the Store
            during iteration has undefined behaviour.
        Exceptions: none.
        Performance:
            SqliteStore: uses LIMIT/OFFSET, O(batch_size) per batch.
            MemoryStore: O(n) to build list, then iterate.
        Convention:
            Returns an iterator rather than a list to avoid loading large
            result sets entirely into memory. Primarily used by
            MemoryStore.load_from() as the generic loading path. May also be
            used by Pipeline's CrossFileResolvePass for full scans.
        """
        ...

    @abstractmethod
    def get_dangling_edges(
        self, kind: Optional[str] = None
    ) -> list[dict]:
        """Return all edges whose target is empty (dangling edges).

        An edge is dangling iff target_text is non-empty (indicating a target
        intent) but target is not associated with any node. Used by
        CrossFileResolvePass for cross-file resolution.

        kind=None returns all dangling edges; specifying a kind filters to
        only that edge type (e.g. kind="calls" returns only unresolved call edges).

        Pre-condition: none.
        Post-condition: no side effects. Results are sorted by source to
            support batch resolution.
        Exceptions: none.
        Performance:
            SqliteStore: O(n + k) — n = total edges, k = dangling edges.
            Uses WHERE target_text IS NOT NULL AND (target IS NULL OR target = '')
            with idx_edges_provenance and idx_edges_target indexes.
            When kind is specified, adds AND kind = ? filtering.
            MemoryStore: O(n) — linear scan over all edges with filter.
        Convention: returns a list (not an iterator) because callers typically
            need the full set.
        """
        ...

    @abstractmethod
    def iter_edges(
        self,
        kind: Optional[str] = None,
        with_source_info: bool = False,
    ) -> Iterator[dict]:
        """Iterate over edges with optional kind filter.

        Used by ImportClassifyPass for kind-based edge classification.
        When with_source_info=True each edge dict also includes the source
        node's file_path and language, avoiding N+1 queries.

        Pre-condition: none.
        Post-condition: no side effects. Concurrent modification of the Store
            during iteration has undefined behaviour.
        Exceptions: none.
        Performance:
            SqliteStore:
                kind=None: full table scan O(n);
                kind specified: uses idx_edges_kind index, O(log n + k).
                with_source_info=True: JOIN nodes ON edges.source = nodes.id,
                adding ~20% overhead but eliminating N+1.
            MemoryStore: O(n) linear scan; with_source_info adds one _nodes
                dict lookup per edge.
        Convention:
            - Returns an iterator, not a list.
            - with_source_info=True adds source_file and source_language fields
              to each returned dict.
            - kind=None returns edges of all kinds.
        """
        ...

    @abstractmethod
    def iter_edges_from(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
        batch_size: int = 1000,
    ) -> Iterator[EdgeRecord]:
        """Stream edges outgoing from / incoming to / both for a node.

        Used by CypherEngine's EdgeExpand operator and general-purpose graph
        traversal. Unlike get_outgoing_edges/get_incoming_edges, this method
        uses cursor-based pagination (LIMIT/OFFSET), suitable for high-degree
        nodes.

        Pre-condition: *node_id* exists.
        Post-condition: returns an iterator of edges in the specified
            direction(s), batched by batch_size.
        Exceptions:
            ValueError — node_id is empty.
        Performance:
            SqliteStore: uses LIMIT/OFFSET, O(batch_size) per batch.
            MemoryStore: O(k) — k = number of adjacent edges, via direct
                adjacency-table lookup.
        Convention:
            - Returns an iterator, not a list.
            - direction="out": only edges where source == node_id.
            - direction="in": only edges where target == node_id.
            - direction="both": both directions.
            - kinds=None: no edge-type filtering.
        """
        ...

    # =========================================================================
    # 5. Graph traversal
    # =========================================================================

    @abstractmethod
    def get_neighbors(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
    ) -> list[dict]:
        """Get a node's neighbours.

        Return format:
            [
                {
                    "node": {NodeRecord fields},
                    "edge": {EdgeRecord fields},
                    "direction": "out" | "in"
                },
                ...
            ]

        Pre-condition: *node_id* is non-empty.
        Post-condition: no side effects. Deleted nodes are excluded.
        Exceptions:
            ValueError — node_id is empty.
        Performance:
            SqliteStore: O(k + j) — k = neighbour count, j = batch-lookup
                overhead for node retrieval. Uses idx_edges_source_kind and
                idx_edges_target_kind indexes.
            MemoryStore: O(k) — direct dict lookup, no SQL overhead.
        Convention:
            Results are deduplicated — when the same neighbour is reachable
            via multiple edges, it appears once with the first edge's info.
        """
        ...

    @abstractmethod
    def get_neighbors_batch(
        self,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
    ) -> dict[str, list[dict]]:
        """Batch-get neighbours for multiple nodes.

        Returns: {node_id: [neighbour_dict, ...]}

        Pre-condition: *node_ids* is non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — node_ids is empty.
        Performance:
            SqliteStore: O(N + K) — N = queried node count, K = total
                neighbour count. Uses a single SQL (WHERE source IN (...))
                to avoid N+1.
            MemoryStore: O(N * avg_degree) — per-node lookup.
        Convention: non-existent node_ids map to empty lists.
        """
        ...

    @abstractmethod
    def find_paths(
        self,
        from_id: str,
        to_id: str,
        kinds: Optional[list[str]] = None,
        max_depth: int = 5,
    ) -> Optional[list[dict]]:
        """Find the shortest path between two nodes using BFS.

        Returns:
            [
                {"node": NodeRecord, "via_edge": Optional[EdgeRecord]},
                ...
            ]
            or None if the nodes are unreachable.

        Pre-condition: *from_id* and *to_id* are non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — either parameter is empty.
        Performance:
            SqliteStore: BFS per layer may issue multiple SQL calls.
            Actual complexity O(b^d) — b = branching factor, d = max_depth.
            MemoryStore: also BFS, but adjacency lookup is O(1); total
            complexity O(V + E_bounded).
        Convention:
            - BFS guarantees the shortest path.
            - max_depth prevents combinatorial explosion.
            - Default edge kinds: ["calls", "references", "imports", "contains"].
            - Returns None (no exception) when either endpoint does not exist.
        """
        ...

    # =========================================================================
    # 6. File management
    # =========================================================================

    @abstractmethod
    def upsert_file(
        self,
        path: str,
        content_hash: str,
        language: str,
        node_count: int = 0,
        size: int = 0,
        modified_at: int = 0,
    ) -> None:
        """Insert or update a file record.

        Pre-condition: *path*, *content_hash*, *language* are non-empty.
        Post-condition: file record exists.
        Exceptions:
            ValueError — a required field is empty.
        Performance: O(1).
        """
        ...

    @abstractmethod
    def get_file(self, path: str) -> Optional[FileRecord]:
        """Look up a file record by path.

        Exceptions: none.
        Performance: O(1) — primary-key lookup.
        """
        ...

    @abstractmethod
    def get_all_files(self) -> list[FileRecord]:
        """Return all file records, sorted by path.

        Exceptions: none.
        Performance: O(n) — n = file count.
        """
        ...

    @abstractmethod
    def get_file_stats(self) -> dict[str, tuple[int, int]]:
        """Return {path: (size, modified_at)} mapping for stat pre-filter.

        Exceptions: none.
        Performance: O(n) — n = file count.
        Convention: only reads size and modified_at; does not load other fields.
        """
        ...

    @abstractmethod
    def delete_file(self, path: str) -> None:
        """Delete a file and all its associated data (nodes, edges, unresolved refs).

        Pre-condition: *path* is non-empty.
        Post-condition: the file and all associated data are removed.
        Exceptions: none (no-op when the file does not exist).
        Performance: O(n) — n = associated data volume for this file.
        """
        ...

    # =========================================================================
    # 7. Search
    # =========================================================================

    @abstractmethod
    def fts_search(
        self,
        query: str,
        limit: int = 20,
        kind_filter: Optional[str] = None,
        language_filter: Optional[str] = None,
        path_filter: Optional[str] = None,
    ) -> list[SearchResult]:
        """Full-text search over nodes (three-tier strategy: FTS5 BM25 -> LIKE -> fuzzy).

        Pre-condition: *query* is non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — query is empty.
        Performance:
            SqliteStore: O(log n + k) with FTS5 BM25; k = match count.
            MemoryStore: O(n) linear scan + string matching (no FTS).
        Convention:
            - Results sorted by BM25 rank (lower is more relevant).
            - kind_filter / language_filter / path_filter are applied at
              the query layer.
            - Supports field:value qualified syntax in queries
              (e.g. kind:function lang:python).
        """
        ...

    @abstractmethod
    def search_by_def_index(self, qualified_name: str) -> Optional[str]:
        """Exact lookup of a node_id via the def index.

        O(1) HashMap lookup used for fast positioning during cross-file
        reference resolution.

        Pre-condition: *qualified_name* is non-empty.
        Post-condition: no side effects.
        Exceptions:
            ValueError — qualified_name is empty.
        Performance:
            SqliteStore: O(1) if def_index is in memory; otherwise
            O(log n) using idx_nodes_qualified index.
            MemoryStore: O(1) — dict lookup.
        Convention: returns None (no exception) when not found.
        """
        ...

    @abstractmethod
    def search_by_field_qualified(
        self,
        query: str,
        limit: int = 20,
    ) -> list[SearchResult]:
        """Enhanced search with field:value qualified syntax.

        Example: "kind:function lang:python api"

        Pre-condition: *query* is non-empty.
        Post-condition: no side effects.
        Exceptions: same as fts_search.
        Performance: same as fts_search + field filtering.
        Supported fields: kind, lang/language, path, visibility, framework.
        """
        ...

    # =========================================================================
    # 8. Unresolved references
    # =========================================================================

    @abstractmethod
    def insert_unresolved_ref(self, ref: UnresolvedRefRecord) -> None:
        """Insert a single unresolved reference record.

        Pre-condition: *ref* contains from_node_id, reference_name, file_path, etc.
        Post-condition: reference record exists.
        Exceptions: ValueError — a required field is missing.
        Performance: O(1).
        """
        ...

    @abstractmethod
    def insert_unresolved_refs(self, refs: list[UnresolvedRefRecord]) -> None:
        """Insert a batch of unresolved reference records.

        Pre-condition: all refs contain required fields.
        Post-condition: all reference records exist.
        Exceptions: ValueError — any ref is missing a required field.
        Performance: O(n).
        """
        ...

    @abstractmethod
    def get_unresolved_refs(
        self, file_path: Optional[str] = None
    ) -> list[UnresolvedRefRecord]:
        """Get unresolved references.

        If *file_path* is None, returns all unresolved refs.

        Exceptions: none.
        Performance: O(n) — n = reference count.
        """
        ...

    @abstractmethod
    def clear_unresolved_refs(self) -> None:
        """Clear all unresolved references.

        Exceptions: none.
        Performance: O(1) DELETE (may trigger FK checks).
        """
        ...

    # =========================================================================
    # 9. Batch operations
    # =========================================================================

    @abstractmethod
    def flush(self) -> None:
        """Flush buffered writes to persistent storage.

        SqliteStore: runs executemany for buffered nodes and edges in a
        single transaction, then clears buffers.
        MemoryStore: no-op (data is already in memory).

        Pre-condition: none.
        Post-condition:
            SqliteStore: buffers cleared, data persisted.
            MemoryStore: no change.
        Exceptions:
            StoreError — write failure. Buffers are NOT cleared (retry-safe).
        Performance: O(buffer_size) — actual bulk SQL execution.
        Convention:
            - insert_nodes / insert_edges are "deferred" writes — they do not
              execute SQL immediately.
            - flush() is the "commit" point.
            - Transaction commit automatically calls flush(); manual calls are
              not required.
        """
        ...

    # =========================================================================
    # 10. Maintenance / statistics
    # =========================================================================

    @abstractmethod
    def optimize(self) -> None:
        """Perform storage optimization.

        SqliteStore: PRAGMA optimize + WAL checkpoint.
        MemoryStore: compact dict memory (remove entries marked deleted).

        Exceptions: none (fail silently).
        Performance: depends on data volume.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Clear all data (nodes, edges, files, unresolved refs).

        Post-condition: Store is empty.
        Exceptions: StoreError on runtime failure (e.g. disk full).
        Performance: O(1) DELETE (may trigger FK cascade).
        """
        ...

    @abstractmethod
    def rebuild_fts(self) -> None:
        """Rebuild the FTS5 full-text-search index.

        Used by FTSRebuildPass. SqliteStore rebuilds via
        ``INSERT INTO nodes_fts(nodes_fts) VALUES ('rebuild')``.
        MemoryStore is a no-op (no FTS index).

        Pre-condition: Store is usable (not closed).
        Post-condition:
            SqliteStore: FTS5 index is fully consistent with the nodes table.
            MemoryStore: no change.
        Exceptions:
            StoreClosedError — Store is already closed.
        Performance:
            SqliteStore: O(n) — n = node count. Rebuild time is linear with
            node count, approximately 50ms per 10K nodes. Should execute
            inside a transaction for atomicity.
            MemoryStore: O(1) — no-op.
        Convention:
            - Not suitable for calling after every insert during incremental
              indexing; use after large batch writes.
            - The caller (Pipeline's FTSRebuildPass) decides when to invoke.
            - Implementations must ensure FTS queries can still use the old
              index during rebuild (no exceptions).
        """
        ...

    @abstractmethod
    def stats(self) -> StoreStats:
        """Return storage statistics.

        Exceptions: none.
        Performance: O(1) aggregate query.
        """
        ...

    @abstractmethod
    def build_def_index(self) -> dict[str, str]:
        """Build and return the full qualified_name -> node_id mapping.

        Used by Pipeline's CrossFileResolvePass and LSPTypeResolvePass.
        Unlike search_by_def_index() which is a single-point query, this
        method builds the complete index in one pass.

        Post-condition: returns the complete def index mapping for all
            current nodes.
        Exceptions: none.
        Performance: O(n) — n = node count.
        Convention: the returned dict is owned by the caller and unaffected
            by subsequent Store mutations.
        """
        ...

    # =========================================================================
    # NOTE: load_from is NOT defined in the ABC.
    #
    # load_from is a concrete method on MemoryStore for bulk-loading from
    # another Store. SqliteStore does not need this operation. Forcing all
    # implementations to define load_from violates LSP — SqliteStore would
    # only be able to raise NotImplementedError.
    #
    # See: design-store-schema.md section 7.2 for MemoryStore.load_from()
    # design.
    # =========================================================================
