"""Graph traversal — BFS-based queries for calls, impact, and trace.

Patterns ported from CodeGraph's graph/traversal.ts:
- Batch get_nodes_by_ids to avoid N+1 per BFS step
- Edge priority: contains > calls > others
- contains edges excluded from impact radius (container membership ≠ dependence)
- visited set for cycle prevention
"""

from collections import deque
from ..db.queries import QueryBuilder

# Default edge kinds for call-graph traversal
CALL_KINDS = ["calls", "references", "imports"]
ALL_KINDS = ["calls", "references", "imports", "extends", "implements", "contains"]

_EDGE_PRIORITY = {"contains": 0, "calls": 1, "references": 2, "imports": 3}


class GraphTraverser:
    """BFS/DFS traversal over the symbol graph."""

    def __init__(self, queries: QueryBuilder):
        self.queries = queries

    # ------------------------------------------------------------------
    # Calls (callers + callees merged)
    # ------------------------------------------------------------------

    def get_calls(
        self,
        node_id: str,
        direction: str = "inbound",   # "inbound" | "outbound" | "both"
        max_depth: int = 1,
        edge_kinds: list[str] | None = None,
    ) -> dict:
        """Get callers (inbound) / callees (outbound) of a symbol.

        Returns {nodes: {id: Row}, edges: [Row], roots: [node_id]}
        """
        if edge_kinds is None:
            edge_kinds = CALL_KINDS

        result_nodes: dict[str, dict] = {}
        result_edges: list[dict] = []
        visited: set[str] = set()

        node = self.queries.get_node_by_id(node_id)
        if not node:
            return {"nodes": {}, "edges": [], "roots": [node_id]}
        result_nodes[node_id] = dict(node)
        visited.add(node_id)

        queue: deque = deque()
        queue.append((node_id, 0))

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            # Get edges
            if direction in ("inbound", "both"):
                incoming = self.queries.get_incoming_edges(current_id, edge_kinds)
                self._add_adjacent(incoming, "source", depth + 1, queue, visited,
                                   result_nodes, result_edges)

            if direction in ("outbound", "both"):
                outgoing = self.queries.get_outgoing_edges(current_id, edge_kinds)
                self._add_adjacent(outgoing, "target", depth + 1, queue, visited,
                                   result_nodes, result_edges)

        return {
            "nodes": result_nodes,
            "edges": result_edges,
            "roots": [node_id],
        }

    # ------------------------------------------------------------------
    # Impact radius
    # ------------------------------------------------------------------

    def get_impact_radius(
        self,
        node_id: str,
        max_depth: int = 3,
    ) -> dict:
        """Calculate impact radius: BFS outward along incoming call edges.

        Excludes 'contains' edges — container membership doesn't
        imply dependence (ported from CodeGraph issue #536 fix).

        Returns {nodes: {id: Row}, edges: [Row], roots: [node_id]}
        """
        edge_kinds = ["calls", "references", "imports"]
        result_nodes: dict[str, dict] = {}
        result_edges: list[dict] = []
        visited: set[str] = set()

        node = self.queries.get_node_by_id(node_id)
        if not node:
            return {"nodes": {}, "edges": [], "roots": [node_id]}
        result_nodes[node_id] = dict(node)
        visited.add(node_id)

        queue: deque = deque()
        queue.append((node_id, 0))

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            incoming = self.queries.get_incoming_edges(current_id, edge_kinds)
            # Sort to prioritize calls over references over imports
            incoming_sorted = sorted(
                incoming,
                key=lambda e: _EDGE_PRIORITY.get(e["kind"], 4)
            )

            for edge in incoming_sorted:
                neighbor_id = edge["source"]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    neighbor = self.queries.get_node_by_id(neighbor_id)
                    if neighbor:
                        result_nodes[neighbor_id] = dict(neighbor)
                    result_edges.append(dict(edge))
                    queue.append((neighbor_id, depth + 1))

        # Group by module
        modules: dict[str, list[dict]] = {}
        for nid, n in result_nodes.items():
            if nid == node_id:
                continue
            mod = n.get("file_path", "unknown")
            modules.setdefault(mod, []).append(n)

        return {
            "nodes": result_nodes,
            "edges": result_edges,
            "roots": [node_id],
            "modules": modules,
        }

    # ------------------------------------------------------------------
    # Trace / path finding
    # ------------------------------------------------------------------

    def find_path(self, from_id: str, to_id: str) -> list[dict] | None:
        """BFS shortest path between two nodes in the call graph.

        Returns list of [{node: Row, via_edge: Row?}] or None if unreachable.
        Uses only call-graph edge kinds (calls, references, imports).
        """
        if from_id == to_id:
            node = self.queries.get_node_by_id(from_id)
            return [{"node": dict(node), "via_edge": None}] if node else None

        # Validate both endpoints exist
        if not self.queries.get_node_by_id(from_id):
            return None
        if not self.queries.get_node_by_id(to_id):
            return None

        # Include 'contains' edges so paths can go class → method
        edge_kinds = CALL_KINDS + ["contains"]
        visited: set[str] = {from_id}
        parent: dict[str, tuple[str, dict]] = {}  # child -> (parent_id, edge)
        queue: deque[str] = deque([from_id])

        while queue:
            current = queue.popleft()
            if current == to_id:
                break

            outgoing = self.queries.get_outgoing_edges(current, edge_kinds)
            for edge in outgoing:
                neighbor = edge["target"]
                if neighbor not in visited:
                    visited.add(neighbor)
                    parent[neighbor] = (current, dict(edge))
                    queue.append(neighbor)

        if to_id not in parent and from_id != to_id:
            return None

        # Reconstruct path
        path = []
        current = to_id
        while current != from_id:
            node = self.queries.get_node_by_id(current)
            prev_id, edge = parent[current]
            path.append({"node": dict(node) if node else {"id": current}, "via_edge": edge})
            current = prev_id

        # Add start node
        start_node = self.queries.get_node_by_id(from_id)
        path.append({"node": dict(start_node) if start_node else {"id": from_id}, "via_edge": None})
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _add_adjacent(self, edges, neighbor_field, depth, queue, visited,
                      result_nodes, result_edges):
        """Batch-fetch adjacent nodes and add to results."""
        sorted_edges = sorted(edges, key=lambda e: _EDGE_PRIORITY.get(e["kind"], 4))
        want_ids = {
            e[neighbor_field] for e in sorted_edges
            if e[neighbor_field] not in visited
        }
        if not want_ids:
            return

        # Batch fetch all wanted nodes in one query (N+1 killer)
        fetched = self.queries.get_nodes_by_ids(list(want_ids))

        for edge in sorted_edges:
            neighbor_id = edge[neighbor_field]
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            if neighbor_id in fetched:
                result_nodes[neighbor_id] = dict(fetched[neighbor_id])
            result_edges.append(dict(edge))
            queue.append((neighbor_id, depth))
