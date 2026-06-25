"""Data flow depth v2 (P37 v5.4.0).

Field-level data flow tracking and through-struct propagation.
Enhances existing data_flows edges with transitive closure
and chain analysis.

Usage::

    from tws_graph.analysis.dataflow_v2 import compute_transitive_dataflows
    new_edges = compute_transitive_dataflows(queries, max_depth=3)
"""

from __future__ import annotations
from collections import deque


# ---------------------------------------------------------------------------
# P37a: Field-level transitive closure
# ---------------------------------------------------------------------------


def compute_transitive_dataflows(queries, max_depth: int = 3) -> list[dict]:
    """Compute transitive closure over existing data_flows edges.

    If A → B (data_flows) and B → C (data_flows), then A → C is a
    transitive data flow through B.

    Args:
        queries: QueryBuilder instance.
        max_depth: Maximum number of hops for transitive closure.

    Returns:
        List of new edge dicts (not inserted — caller decides).
    """
    # Load all existing data_flows edges
    edges = queries._exec(
        "SELECT source, target, source_loc, provenance FROM edges WHERE kind = 'data_flows'"
    ).fetchall()

    if not edges:
        return []

    # Build adjacency and edge set
    adjacency: dict[str, set[str]] = {}
    existing_pairs: set[tuple[str, str]] = set()
    for e in edges:
        src, tgt = e["source"], e["target"]
        adjacency.setdefault(src, set()).add(tgt)
        existing_pairs.add((src, tgt))

    # Build reverse adjacency for fast ancestor lookup
    reverse: dict[str, set[str]] = {}
    for src, tgts in adjacency.items():
        for tgt in tgts:
            reverse.setdefault(tgt, set()).add(src)

    # BFS from each source to find transitive paths
    new_edges: list[dict] = []
    all_sources = list(adjacency.keys())

    for src_id in all_sources:
        visited: dict[str, int] = {src_id: 0}  # node → depth
        queue: deque[str] = deque([src_id])

        while queue:
            current = queue.popleft()
            depth = visited[current]

            for neighbor in adjacency.get(current, set()):
                new_depth = depth + 1
                if new_depth > max_depth:
                    continue
                if neighbor not in visited or visited[neighbor] > new_depth:
                    visited[neighbor] = new_depth
                    queue.append(neighbor)

                    # If depth >= 2, this is a transitive edge (not direct)
                    if new_depth >= 2:
                        pair = (src_id, neighbor)
                        if pair not in existing_pairs:
                            existing_pairs.add(pair)  # avoid duplicates
                            new_edges.append({
                                "source": src_id,
                                "target": neighbor,
                                "kind": "data_flows",
                                "source_loc": "",
                                "provenance": f"transitive_depth={new_depth}",
                            })

    return new_edges


# ---------------------------------------------------------------------------
# P37b: Data flow chain analysis
# ---------------------------------------------------------------------------


def find_dataflow_chains(queries, min_length: int = 2) -> list[dict]:
    """Find chains in the data_flows graph.

    A chain is a sequence of data_flows edges forming a path.
    Returns chains ordered by length (longest first).

    Args:
        queries: QueryBuilder instance.
        min_length: Minimum chain length (number of nodes).

    Returns:
        List of chain dicts: {nodes: [id, ...], length: int}
    """
    edges = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'data_flows'"
    ).fetchall()

    if not edges:
        return []

    # Build adjacency
    adjacency: dict[str, set[str]] = {}
    all_nodes: set[str] = set()
    for e in edges:
        src, tgt = e["source"], e["target"]
        adjacency.setdefault(src, set()).add(tgt)
        all_nodes.add(src)
        all_nodes.add(tgt)

    # Find sources (nodes with no inbound data_flow edges)
    has_inbound: set[str] = set()
    for tgts in adjacency.values():
        has_inbound.update(tgts)
    sources = all_nodes - has_inbound

    # If no clear sources, use all nodes
    if not sources:
        sources = all_nodes

    # DFS from each source to find chains
    chains: list[dict] = []

    def dfs(node: str, path: list[str], visited: set[str]):
        if len(path) >= min_length:
            chains.append({"nodes": list(path), "length": len(path)})
        for neighbor in adjacency.get(node, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                dfs(neighbor, path + [neighbor], visited)
                visited.discard(neighbor)
            elif neighbor in path:
                # Cycle detected — record the cycle path
                cycle_start = path.index(neighbor)
                cycle_path = path[cycle_start:] + [neighbor]
                chains.append({"nodes": cycle_path, "length": len(cycle_path)})

    for src in sources:
        dfs(src, [src], {src})

    # Sort by length descending, deduplicate
    seen_chains: set[tuple] = set()
    unique_chains = []
    for c in sorted(chains, key=lambda c: c["length"], reverse=True):
        key = tuple(c["nodes"])
        if key not in seen_chains:
            seen_chains.add(key)
            unique_chains.append(c)

    return unique_chains
