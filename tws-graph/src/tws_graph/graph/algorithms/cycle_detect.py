"""Cycle dependency detection in call graphs.

Uses DFS-based cycle detection, considering only ``calls`` type edges.
Returns up to *max_cycles* cycles via AlgorithmResult.
"""

from __future__ import annotations

from tws_graph.graph.algorithms.base import GraphAlgorithm, AlgorithmResult
from tws_graph.store.interface import Store


class CycleDetector(GraphAlgorithm):
    """Cycle dependency detector.

    Uses DFS to detect cycles in call graphs.
    Only considers edges with ``kind == "calls"``.

    Parameters
    ----------
    max_cycles : int
        Maximum number of cycles to return. Default 100.
    """

    @property
    def name(self) -> str:
        return "cycle-detection"

    @property
    def description(self) -> str:
        return "DFS-based cycle detection in call graphs"

    def __init__(self, max_cycles: int = 100):
        self._max_cycles = max_cycles

    def run(self, store: Store) -> AlgorithmResult:
        import time
        start = time.perf_counter()

        # Build calls-only adjacency graph
        graph: dict[str, set[str]] = {}
        nodes_set: set[str] = set()
        for edge in store.iter_all_edges():
            if edge["kind"] != "calls":
                continue
            src, tgt = edge["source"], edge["target"]
            nodes_set.add(src)
            nodes_set.add(tgt)
            graph.setdefault(src, set()).add(tgt)

        cycles: list[list[str]] = []

        # DFS-based cycle detection using three-colour marking
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {node: WHITE for node in nodes_set}
        path: list[str] = []

        def dfs(node: str) -> None:
            if color[node] == GRAY:
                # Found a cycle — back edge
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                if len(cycles) < self._max_cycles:
                    cycles.append(cycle)
                return
            if color[node] == BLACK:
                return

            color[node] = GRAY
            path.append(node)

            for neighbor in graph.get(node, set()):
                dfs(neighbor)

            path.pop()
            color[node] = BLACK

        # Traverse all nodes (handles disconnected components)
        for node in nodes_set:
            if color[node] == WHITE:
                dfs(node)

        elapsed = (time.perf_counter() - start) * 1000

        # Collect node details for result enrichment
        affected_files: set[str] = set()
        node_details: dict[str, dict] = {}
        for node_id in nodes_set:
            node = store.get_node_by_id(node_id)
            if node:
                node_details[node_id] = {
                    "name": node["name"],
                    "file_path": node["file_path"],
                }

        cycle_info: list[dict] = []
        for cycle in cycles:
            files: set[str] = set()
            names: list[str] = []
            for nid in cycle:
                if nid in node_details:
                    files.add(node_details[nid]["file_path"])
                    names.append(node_details[nid]["name"])
            cycle_info.append({
                "cycle": names,
                "files": list(files),
                "length": len(cycle) - 1,
            })
            affected_files.update(files)

        return AlgorithmResult(
            algorithm=self.name,
            data={
                "cycles": cycle_info,
                "total_cycles": len(cycles),
                "max_cycles_reached": len(cycles) >= self._max_cycles,
                "affected_files": list(affected_files),
            },
            duration_ms=round(elapsed, 2),
        )
