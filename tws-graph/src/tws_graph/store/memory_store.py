"""MemoryStore — in-memory graph storage implementation.

Implements the full Store ABC (48 abstract methods) using Python dict
adjacency tables. No external dependencies — pure Python dict operations.

Data structure:
    _nodes: dict[str, dict]                          node_id -> NodeRecord
    _outgoing: dict[str, list[tuple[str, dict]]]     source -> [(target_id, EdgeRecord)]
    _incoming: dict[str, list[tuple[str, dict]]]     target -> [(source_id, EdgeRecord)]
    _def_index: dict[str, str]                       qualified_name -> node_id
    _files: dict[str, dict]                           path -> FileRecord
    _unresolved_refs: list[dict]                      unresolved references

Transactions are no-ops (memory operations are already atomic) but the
_in_transaction flag is maintained for interface consistency.

load_from(other: Store) bulk-loads data from another Store via
iter_all_nodes() + iter_all_edges() — no direct access to other's
private fields.

Per design-store-schema.md section 7 and design-memory-store.md.
"""

import json
from collections import deque
from typing import Iterator, Optional

from .exceptions import (
    EdgeNotFoundError,
    StoreClosedError,
    TransactionError,
)
from .interface import Store
from .types import Direction


# Required fields for each record type, used for validation.
_REQUIRED_NODE_FIELDS = [
    "id", "kind", "name", "qualified_name",
    "file_path", "language", "start_line", "end_line",
]
_REQUIRED_EDGE_FIELDS = ["source", "kind"]  # target may be empty for dangling edges
_REQUIRED_REF_FIELDS = ["from_node_id", "reference_name", "file_path", "language"]


def _check_closed(closed: bool) -> None:
    """Raise StoreClosedError if the Store has been closed."""
    if closed:
        raise StoreClosedError("Store has been closed")


def _check_missing_fields(record: dict, required: list[str], label: str) -> None:
    """Raise ValueError if any required field is missing or empty in record."""
    missing = [f for f in required if f not in record or record[f] == ""]
    if missing:
        raise ValueError(f"{label} missing required fields: {missing}")


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore(Store):
    """In-memory graph store — Python dict-based adjacency-table implementation.

    Data structure:
        _nodes: dict[str, dict]                          node_id -> NodeRecord
        _outgoing: dict[str, list[tuple[str, dict]]]     source -> [(target_id, EdgeRecord)]
        _incoming: dict[str, list[tuple[str, dict]]]     target -> [(source_id, EdgeRecord)]
        _def_index: dict[str, str]                       qualified_name -> node_id
        _files: dict[str, dict]                           path -> FileRecord
        _unresolved_refs: list[dict]                      unresolved references

    Memory estimation (per design doc sec 7.1):
        ~265 MB for 100K nodes + 500K edges (Python 3.12).
        Actual may reach 350-400 MB due to Python object overhead.
    """

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._outgoing: dict[str, list[tuple[str, dict]]] = {}
        self._incoming: dict[str, list[tuple[str, dict]]] = {}
        self._def_index: dict[str, str] = {}
        self._files: dict[str, dict] = {}
        self._unresolved_refs: list[dict] = []
        self._closed = False
        self._in_transaction = False
        self._edge_id_counter = 1
        self._edge_count = 0

    # =========================================================================
    # NON-ABC: load_from
    # =========================================================================

    def load_from(self, other: Store) -> None:
        """Bulk-load all data from another Store.

        Uses only the Store interface's public iterators
        (iter_all_nodes + iter_all_edges). Does not access the other Store's
        private fields.

        Loading process:
            1. Clear current data.
            2. Stream other.iter_all_nodes(batch_size=5000)
               - Insert each node into _nodes and _def_index.
            3. Stream other.iter_all_edges(batch_size=5000)
               - Build adjacency tables _outgoing / _incoming.
               - Update _edge_count counter.

        Per design-store-schema.md section 7.2.
        """
        _check_closed(self._closed)
        self.clear()

        # Load nodes via public iterator
        for node in other.iter_all_nodes(batch_size=5000):
            node_dict = dict(node)
            self._nodes[node_dict["id"]] = node_dict
            self._def_index[node_dict["qualified_name"]] = node_dict["id"]

        # Load edges via public iterator; build adjacency tables
        for edge in other.iter_all_edges(batch_size=5000):
            edge_dict = dict(edge)
            src = edge_dict["source"]
            tgt = edge_dict["target"]
            # Only add edges whose source node exists (safety check)
            if src not in self._nodes:
                continue
            # Assign edge id from the loaded data
            eid = edge_dict.get("id", self._edge_id_counter)
            if "id" not in edge_dict:
                edge_dict["id"] = self._edge_id_counter
                self._edge_id_counter += 1
            else:
                if eid >= self._edge_id_counter:
                    self._edge_id_counter = eid + 1

            # Check for duplicate (source, target, kind) triple
            dup = False
            if src in self._outgoing:
                for _, existing in self._outgoing[src]:
                    if (existing["target"] == tgt and
                            existing["kind"] == edge_dict["kind"]):
                        dup = True
                        break
            if dup:
                continue

            # Add to outgoing adjacency
            self._outgoing.setdefault(src, []).append((tgt, edge_dict))
            # Add to incoming adjacency
            self._incoming.setdefault(tgt, []).append((src, edge_dict))
            self._edge_count += 1

    # =========================================================================
    # 1. Connection management
    # =========================================================================

    def close(self) -> None:
        """Close the connection and release resources. Idempotent."""
        self._closed = True

    # =========================================================================
    # 2. Transaction management (no-op for MemoryStore)
    # =========================================================================

    def begin(self) -> None:
        """Begin a transaction. No-op but enforces single-level semantics."""
        _check_closed(self._closed)
        if self._in_transaction:
            raise TransactionError("Already in a transaction")
        self._in_transaction = True

    def commit(self) -> None:
        """Commit the active transaction. No-op."""
        _check_closed(self._closed)
        if not self._in_transaction:
            raise TransactionError("Not in a transaction")
        self._in_transaction = False

    def rollback(self) -> None:
        """Roll back the active transaction. No-op."""
        _check_closed(self._closed)
        if not self._in_transaction:
            raise TransactionError("Not in a transaction")
        self._in_transaction = False

    # =========================================================================
    # 3. Node CRUD
    # =========================================================================

    def insert_node(self, node: dict) -> None:
        """Insert a single node. REPLACE if id already exists."""
        _check_closed(self._closed)
        _check_missing_fields(node, _REQUIRED_NODE_FIELDS, "Node")
        node_dict = dict(node)
        self._nodes[node_dict["id"]] = node_dict
        self._def_index[node_dict["qualified_name"]] = node_dict["id"]

    def insert_nodes(self, nodes: list[dict]) -> None:
        """Insert a batch of nodes."""
        _check_closed(self._closed)
        for i, node in enumerate(nodes):
            try:
                self.insert_node(node)
            except ValueError:
                raise ValueError(
                    f"Node at index {i} missing required fields"
                )

    def get_node_by_id(self, id: str) -> Optional[dict]:
        """Look up a node by its id. Returns None if not found."""
        _check_closed(self._closed)
        return self._nodes.get(id)

    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, dict]:
        """Batch-lookup nodes by ids. Non-existent ids are omitted."""
        _check_closed(self._closed)
        result = {}
        for nid in ids:
            node = self._nodes.get(nid)
            if node is not None:
                result[nid] = node
        return result

    def delete_nodes_by_file(
        self, file_path: str, kind: Optional[str] = None
    ) -> None:
        """Delete all nodes (and associated edges) for a file."""
        _check_closed(self._closed)
        ids_to_delete = []
        for nid, node in self._nodes.items():
            if node["file_path"] == file_path:
                if kind is None or node.get("kind") == kind:
                    ids_to_delete.append(nid)
        for nid in ids_to_delete:
            self._remove_node_and_edges(nid)

    def update_node_property(self, node_id: str, properties: dict) -> None:
        """Merge-update a node's extended properties."""
        _check_closed(self._closed)
        if node_id not in self._nodes:
            raise KeyError(f"Node '{node_id}' not found")
        node = self._nodes[node_id]
        existing_raw = node.get("properties", "{}")
        existing = json.loads(existing_raw) if existing_raw else {}
        existing.update(properties)
        node["properties"] = json.dumps(existing)

    def iter_nodes_by_kind(
        self, kind: str, batch_size: int = 1000
    ) -> Iterator[dict]:
        """Stream nodes filtered by kind."""
        _check_closed(self._closed)
        for node in self._nodes.values():
            if node.get("kind") == kind:
                yield node

    def iter_all_nodes(self, batch_size: int = 1000) -> Iterator[dict]:
        """Stream all nodes."""
        _check_closed(self._closed)
        for node in self._nodes.values():
            yield node

    def count_nodes(self) -> int:
        """Return the total number of nodes."""
        _check_closed(self._closed)
        return len(self._nodes)

    def iter_nodes_by_file(self, file_path: str) -> Iterator[dict]:
        """Stream all nodes belonging to the given file."""
        _check_closed(self._closed)
        if not file_path:
            raise ValueError("file_path must be non-empty")
        for node in self._nodes.values():
            if node.get("file_path") == file_path:
                yield node

    # =========================================================================
    # 4. Edge CRUD
    # =========================================================================

    def insert_edge(self, edge: dict) -> None:
        """Insert a single edge. INSERT OR IGNORE (skip if already exists).

        Validates that the source node exists. Skips edge if source is missing.
        Deduplicates by (source, target, kind) triple.
        """
        _check_closed(self._closed)
        _check_missing_fields(edge, _REQUIRED_EDGE_FIELDS, "Edge")
        src = edge["source"]
        tgt = edge["target"]
        kind = edge["kind"]

        # Source must exist
        if src not in self._nodes:
            return

        # Check for duplicate (source, target, kind)
        if src in self._outgoing:
            for _, existing in self._outgoing[src]:
                if (existing["target"] == tgt and
                        existing["kind"] == kind):
                    return  # duplicate, skip (INSERT OR IGNORE)

        edge_dict = dict(edge)
        edge_dict["id"] = self._edge_id_counter
        self._edge_id_counter += 1

        # Update outgoing
        self._outgoing.setdefault(src, []).append((tgt, edge_dict))
        # Update incoming
        self._incoming.setdefault(tgt, []).append((src, edge_dict))
        self._edge_count += 1

    def insert_edges(self, edges: list[dict]) -> None:
        """Insert a batch of edges."""
        _check_closed(self._closed)
        for i, edge in enumerate(edges):
            try:
                self.insert_edge(edge)
            except ValueError:
                raise ValueError(
                    f"Edge at index {i} missing required fields"
                )

    def get_outgoing_edges(
        self, source_id: str, kinds: Optional[list[str]] = None
    ) -> list[dict]:
        """Get edges originating from source_id."""
        _check_closed(self._closed)
        if not source_id:
            raise ValueError("source_id must be non-empty")
        entries = self._outgoing.get(source_id, [])
        result = [edge for _, edge in entries]
        if kinds is not None:
            result = [e for e in result if e.get("kind") in kinds]
        return result

    def get_incoming_edges(
        self, target_id: str, kinds: Optional[list[str]] = None
    ) -> list[dict]:
        """Get edges pointing to target_id."""
        _check_closed(self._closed)
        if not target_id:
            raise ValueError("target_id must be non-empty")
        entries = self._incoming.get(target_id, [])
        result = [edge for _, edge in entries]
        if kinds is not None:
            result = [e for e in result if e.get("kind") in kinds]
        return result

    def get_edges_between(
        self, source_id: str, target_id: str
    ) -> list[dict]:
        """Get all edges from source_id to target_id."""
        _check_closed(self._closed)
        if not source_id or not target_id:
            raise ValueError("source_id and target_id must be non-empty")
        entries = self._outgoing.get(source_id, [])
        return [
            edge for tgt, edge in entries
            if tgt == target_id
        ]

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

        # Find the edge by scanning
        found_edge = None
        old_target = None
        old_source = None
        for tgt_entries in self._outgoing.values():
            for tgt, edge in tgt_entries:
                if edge.get("id") == edge_id:
                    found_edge = edge
                    old_target = tgt
                    break
            if found_edge is not None:
                break

        if found_edge is None:
            raise EdgeNotFoundError(f"Edge id={edge_id} not found")

        old_source = found_edge["source"]

        # Update the edge dict
        found_edge["target"] = new_target
        found_edge["provenance"] = provenance

        # Update adjacency tables: remove old incoming, add new incoming
        if old_target and old_target in self._incoming:
            self._incoming[old_target] = [
                (s, e) for s, e in self._incoming[old_target]
                if e.get("id") != edge_id
            ]
            if not self._incoming[old_target]:
                del self._incoming[old_target]

        # Add to new target's incoming
        self._incoming.setdefault(new_target, []).append(
            (old_source, found_edge)
        )

        # Update outgoing table: replace the entry
        if old_source in self._outgoing:
            for i, (tgt, e) in enumerate(self._outgoing[old_source]):
                if e.get("id") == edge_id:
                    self._outgoing[old_source][i] = (new_target, found_edge)
                    break

    def update_edge_provenance(
        self, edge_id: int, provenance: str
    ) -> None:
        """Update only an edge's provenance field."""
        _check_closed(self._closed)
        if not provenance:
            raise ValueError("provenance must be non-empty")

        found_edge = None
        for tgt_entries in self._outgoing.values():
            for _, edge in tgt_entries:
                if edge.get("id") == edge_id:
                    found_edge = edge
                    break
            if found_edge is not None:
                break

        if found_edge is None:
            raise EdgeNotFoundError(f"Edge id={edge_id} not found")

        found_edge["provenance"] = provenance

    def delete_edges_by_source(self, source_id: str) -> None:
        """Delete all edges originating from source_id."""
        _check_closed(self._closed)
        if source_id not in self._outgoing:
            return

        removed_count = 0
        for tgt, edge in self._outgoing[source_id]:
            # Remove from incoming
            if tgt in self._incoming:
                self._incoming[tgt] = [
                    (s, e) for s, e in self._incoming[tgt]
                    if s != source_id or e.get("id") != edge.get("id")
                ]
                if not self._incoming[tgt]:
                    del self._incoming[tgt]
            removed_count += 1

        del self._outgoing[source_id]
        self._edge_count -= removed_count

    def count_edges(self) -> int:
        """Return the total number of edges."""
        _check_closed(self._closed)
        return self._edge_count

    def iter_all_edges(self, batch_size: int = 1000) -> Iterator[dict]:
        """Stream all edges."""
        _check_closed(self._closed)
        seen = set()
        for entries in self._outgoing.values():
            for _, edge in entries:
                eid = edge.get("id")
                if eid not in seen:
                    seen.add(eid)
                    yield edge

    def get_dangling_edges(
        self, kind: Optional[str] = None
    ) -> list[dict]:
        """Return all edges whose target is empty (dangling edges).

        An edge is dangling iff target_text is non-empty but target is empty
        or not associated with any node.
        """
        _check_closed(self._closed)
        result = []
        seen = set()
        for entries in self._outgoing.values():
            for tgt, edge in entries:
                eid = edge.get("id")
                if eid in seen:
                    continue
                seen.add(eid)
                target_text = edge.get("target_text")
                if (target_text and
                        (not tgt or tgt not in self._nodes)):
                    if kind is None or edge.get("kind") == kind:
                        result.append(edge)
        return result

    def iter_edges(
        self,
        kind: Optional[str] = None,
        with_source_info: bool = False,
    ) -> Iterator[dict]:
        """Iterate over edges with optional kind filter."""
        _check_closed(self._closed)
        seen = set()
        for entries in self._outgoing.values():
            for tgt, edge in entries:
                eid = edge.get("id")
                if eid in seen:
                    continue
                seen.add(eid)
                if kind is not None and edge.get("kind") != kind:
                    continue
                result = dict(edge)
                if with_source_info:
                    src_node = self._nodes.get(edge["source"])
                    result["source_file"] = (
                        src_node["file_path"] if src_node else ""
                    )
                    result["source_language"] = (
                        src_node["language"] if src_node else ""
                    )
                yield result

    def iter_edges_from(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Direction = "both",
        batch_size: int = 1000,
    ) -> Iterator[dict]:
        """Stream edges from/to/both for a node."""
        _check_closed(self._closed)
        if not node_id:
            raise ValueError("node_id must be non-empty")

        yielded = set()

        if direction in ("out", "both"):
            for _, edge in self._outgoing.get(node_id, []):
                if kinds is not None and edge.get("kind") not in kinds:
                    continue
                eid = edge.get("id")
                if eid not in yielded:
                    yielded.add(eid)
                    yield edge

        if direction in ("in", "both"):
            for _, edge in self._incoming.get(node_id, []):
                if kinds is not None and edge.get("kind") not in kinds:
                    continue
                eid = edge.get("id")
                if eid not in yielded:
                    yielded.add(eid)
                    yield edge

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

        Returns list of {node, edge, direction} dicts. Deduplicated by neighbour
        node id.
        """
        _check_closed(self._closed)
        if not node_id:
            raise ValueError("node_id must be non-empty")

        seen_neighbors: dict[str, dict] = {}

        def _add_neighbor(nid, edge_dict, dir_label):
            if nid in seen_neighbors:
                return
            node = self._nodes.get(nid)
            if node is None:
                return
            seen_neighbors[nid] = {
                "node": node,
                "edge": edge_dict,
                "direction": dir_label,
            }

        if direction in ("out", "both"):
            for tgt, edge in self._outgoing.get(node_id, []):
                if kinds is not None and edge.get("kind") not in kinds:
                    continue
                _add_neighbor(tgt, edge, "out")

        if direction in ("in", "both"):
            for src, edge in self._incoming.get(node_id, []):
                if kinds is not None and edge.get("kind") not in kinds:
                    continue
                _add_neighbor(src, edge, "in")

        return list(seen_neighbors.values())

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

        if from_id not in self._nodes or to_id not in self._nodes:
            return None

        if kinds is None:
            kinds = ["calls", "references", "imports", "contains"]

        if from_id == to_id:
            return [{"node": self._nodes[from_id], "via_edge": None}]

        visited = {from_id}
        queue = deque([(from_id, [{"node": self._nodes[from_id], "via_edge": None}])])

        while queue:
            current, path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue

            # Neighbors via outgoing edges
            for tgt, edge in self._outgoing.get(current, []):
                if kinds is not None and edge.get("kind") not in kinds:
                    continue
                if tgt in visited:
                    continue
                visited.add(tgt)
                new_path = path + [{"node": self._nodes.get(tgt, {}), "via_edge": edge}]
                if tgt == to_id:
                    return new_path
                queue.append((tgt, new_path))

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
        self._files[path] = {
            "path": path,
            "content_hash": content_hash,
            "language": language,
            "node_count": node_count,
            "indexed_at": 0,  # will be set by caller if needed
            "size": size,
            "modified_at": modified_at,
        }

    def get_file(self, path: str) -> Optional[dict]:
        """Look up a file record by path."""
        _check_closed(self._closed)
        return self._files.get(path)

    def get_all_files(self) -> list[dict]:
        """Return all file records, sorted by path."""
        _check_closed(self._closed)
        return sorted(self._files.values(), key=lambda f: f["path"])

    def get_file_stats(self) -> dict[str, tuple[int, int]]:
        """Return {path: (size, modified_at)} mapping."""
        _check_closed(self._closed)
        return {
            path: (f["size"], f["modified_at"])
            for path, f in self._files.items()
        }

    def delete_file(self, path: str) -> None:
        """Delete a file and all its associated data."""
        _check_closed(self._closed)
        self.delete_nodes_by_file(path)
        self._files.pop(path, None)

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
        """Full-text search over nodes (linear scan + case-insensitive LIKE).

        MemoryStore has no FTS5 — uses Python string matching.
        """
        _check_closed(self._closed)
        if not query:
            raise ValueError("query must be non-empty")

        query_lower = query.lower()
        results = []
        for node in self._nodes.values():
            # Match against name, qualified_name, docstring
            if query_lower not in node.get("name", "").lower():
                if query_lower not in node.get("qualified_name", "").lower():
                    doc = node.get("docstring") or ""
                    if query_lower not in doc.lower():
                        continue

            # Apply filters
            if kind_filter is not None and node.get("kind") != kind_filter:
                continue
            if language_filter is not None and node.get("language") != language_filter:
                continue
            if path_filter is not None:
                fp = node.get("file_path", "")
                if path_filter not in fp:
                    continue

            results.append({
                "id": node["id"],
                "name": node.get("name", ""),
                "qualified_name": node.get("qualified_name", ""),
                "kind": node.get("kind", ""),
                "file_path": node.get("file_path", ""),
                "language": node.get("language", ""),
                "signature": node.get("signature"),
                "docstring": node.get("docstring"),
                "rank": None,  # no BM25 in MemoryStore
            })

        return results[:limit]

    def search_by_def_index(self, qualified_name: str) -> Optional[str]:
        """Exact lookup of a node_id via the def index."""
        _check_closed(self._closed)
        if not qualified_name:
            raise ValueError("qualified_name must be non-empty")
        return self._def_index.get(qualified_name)

    def search_by_field_qualified(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Enhanced search with field:value qualified syntax.

        Example: "kind:function lang:python my_func"
        """
        _check_closed(self._closed)
        if not query:
            raise ValueError("query must be non-empty")

        # Parse field:value qualifiers from the query
        tokens = query.split()
        kind_filter = None
        lang_filter = None
        path_filter = None
        visibility_filter = None
        framework_filter = None
        search_terms = []

        for token in tokens:
            if ":" in token:
                field, _, value = token.partition(":")
                if field == "kind":
                    kind_filter = value
                elif field in ("lang", "language"):
                    lang_filter = value
                elif field == "path":
                    path_filter = value
                elif field == "visibility":
                    visibility_filter = value
                elif field == "framework":
                    framework_filter = value
                else:
                    search_terms.append(token)
            else:
                search_terms.append(token)

        remaining = " ".join(search_terms)
        if not remaining:
            # No text to search — return all matching filters
            results = []
            for node in self._nodes.values():
                if kind_filter and node.get("kind") != kind_filter:
                    continue
                if lang_filter and node.get("language") != lang_filter:
                    continue
                if path_filter and path_filter not in node.get("file_path", ""):
                    continue
                if visibility_filter and node.get("visibility") != visibility_filter:
                    continue
                if framework_filter and node.get("framework") != framework_filter:
                    continue
                results.append({
                    "id": node["id"],
                    "name": node.get("name", ""),
                    "qualified_name": node.get("qualified_name", ""),
                    "kind": node.get("kind", ""),
                    "file_path": node.get("file_path", ""),
                    "language": node.get("language", ""),
                    "signature": node.get("signature"),
                    "docstring": node.get("docstring"),
                    "rank": None,
                })
            return results[:limit]

        return self.fts_search(
            remaining,
            limit=limit,
            kind_filter=kind_filter,
            language_filter=lang_filter,
            path_filter=path_filter,
        )

    # =========================================================================
    # 8. Unresolved references
    # =========================================================================

    def insert_unresolved_ref(self, ref: dict) -> None:
        """Insert a single unresolved reference record."""
        _check_closed(self._closed)
        _check_missing_fields(ref, _REQUIRED_REF_FIELDS, "UnresolvedRef")
        self._unresolved_refs.append(dict(ref))

    def insert_unresolved_refs(self, refs: list[dict]) -> None:
        """Insert a batch of unresolved reference records."""
        _check_closed(self._closed)
        for i, ref in enumerate(refs):
            try:
                self.insert_unresolved_ref(ref)
            except ValueError:
                raise ValueError(
                    f"Ref at index {i} missing required fields"
                )

    def get_unresolved_refs(
        self, file_path: Optional[str] = None
    ) -> list[dict]:
        """Get unresolved references, optionally filtered by file_path."""
        _check_closed(self._closed)
        if file_path is None:
            return list(self._unresolved_refs)
        return [
            r for r in self._unresolved_refs
            if r.get("file_path") == file_path
        ]

    def clear_unresolved_refs(self) -> None:
        """Clear all unresolved references."""
        _check_closed(self._closed)
        self._unresolved_refs.clear()

    # =========================================================================
    # 9. Batch operations
    # =========================================================================

    def flush(self) -> None:
        """Flush buffered writes. No-op for MemoryStore."""
        _check_closed(self._closed)
        # Data is already in memory; nothing to flush.

    # =========================================================================
    # 10. Maintenance / statistics
    # =========================================================================

    def optimize(self) -> None:
        """Perform storage optimization. No-op for MemoryStore."""
        _check_closed(self._closed)
        # Memory-only — nothing to optimize.

    def clear(self) -> None:
        """Clear all data (nodes, edges, files, unresolved refs)."""
        _check_closed(self._closed)
        self._nodes.clear()
        self._outgoing.clear()
        self._incoming.clear()
        self._def_index.clear()
        self._files.clear()
        self._unresolved_refs.clear()
        self._edge_id_counter = 1
        self._edge_count = 0

    def rebuild_fts(self) -> None:
        """Rebuild the FTS5 index. No-op for MemoryStore."""
        _check_closed(self._closed)
        # No FTS index to rebuild.

    def stats(self) -> dict:
        """Return storage statistics."""
        _check_closed(self._closed)
        return {
            "node_count": len(self._nodes),
            "edge_count": self._edge_count,
            "file_count": len(self._files),
            "unresolved_count": len(self._unresolved_refs),
            "memory_usage_bytes": self._estimate_memory(),
        }

    def build_def_index(self) -> dict[str, str]:
        """Build and return the full qualified_name -> node_id mapping."""
        _check_closed(self._closed)
        return dict(self._def_index)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _remove_node_and_edges(self, node_id: str) -> None:
        """Remove a node and all edges connected to it."""
        # Remove outgoing edges and their incoming counterparts
        if node_id in self._outgoing:
            for tgt, edge in self._outgoing[node_id]:
                if tgt in self._incoming:
                    self._incoming[tgt] = [
                        (s, e) for s, e in self._incoming[tgt]
                        if s != node_id or e.get("id") != edge.get("id")
                    ]
                    if not self._incoming[tgt]:
                        del self._incoming[tgt]
                self._edge_count -= 1
            del self._outgoing[node_id]

        # Remove incoming edges and their outgoing counterparts
        if node_id in self._incoming:
            for src, edge in self._incoming[node_id]:
                if src in self._outgoing:
                    self._outgoing[src] = [
                        (t, e) for t, e in self._outgoing[src]
                        if t != node_id or e.get("id") != edge.get("id")
                    ]
                    if not self._outgoing[src]:
                        del self._outgoing[src]
                self._edge_count -= 1
            del self._incoming[node_id]

        # Remove the node itself
        node = self._nodes.pop(node_id, None)
        if node:
            self._def_index.pop(node.get("qualified_name", ""), None)

    def _estimate_memory(self) -> int:
        """Rough estimate of memory usage in bytes."""
        import sys
        total = 0
        for obj in [
            self._nodes, self._outgoing, self._incoming,
            self._def_index, self._files, self._unresolved_refs,
        ]:
            total += sys.getsizeof(obj)
        return total
