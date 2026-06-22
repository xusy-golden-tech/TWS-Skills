"""Louvain community detection algorithm.

CommunityDetector partitions graph nodes into communities using the
Louvain algorithm. NetworkX is preferred for production accuracy; a
built-in simplified Louvain is used as a zero-dependency fallback.

Algorithm:
    1. Load edges from Store, filter by edge_kinds.
    2. Build adjacency set for each node.
    3. Execute Louvain (NetworkX or built-in).
    4. Return community assignments with modularity score.
"""

from __future__ import annotations

import time

from tws_graph.graph.algorithms.base import AlgorithmResult, GraphAlgorithm
from tws_graph.store.interface import Store


class CommunityDetector(GraphAlgorithm):
    """Louvain community detection algorithm.

    Prefers NetworkX's ``louvain_communities`` for accuracy.
    Falls back to a built-in simplified Louvain when NetworkX is
    unavailable.

    Parameters
    ----------
    edge_kinds : list[str] | None
        Edge kinds to include in the graph. Defaults to
        ``["calls", "imports"]``. Edges of other kinds are ignored.
    """

    @property
    def name(self) -> str:
        return "community-detection"

    @property
    def description(self) -> str:
        return "Louvain community detection"

    def __init__(self, edge_kinds: list[str] | None = None) -> None:
        self._edge_kinds = edge_kinds or ["calls", "imports"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, store: Store) -> AlgorithmResult:
        """Execute community detection against *store*.

        Steps:
            1. Load edges, filter by ``_edge_kinds``.
            2. Build adjacency dict (undirected).
            3. Run Louvain (NetworkX or built-in fallback).
            4. Package result.
        """
        start = time.perf_counter()
        errors: list[str] = []

        # ------------------------------------------------------------------
        # 1. Build adjacency from store edges
        # ------------------------------------------------------------------
        adj: dict[str, set[str]] = {}
        for edge_dict in store.iter_all_edges():
            kind = edge_dict.get("kind", "")
            if self._edge_kinds and kind not in self._edge_kinds:
                continue
            src = edge_dict.get("source", "")
            tgt = edge_dict.get("target", "")
            if not src or not tgt:
                continue
            adj.setdefault(src, set()).add(tgt)
            adj.setdefault(tgt, set()).add(src)

        # No edges to process — return empty result
        if not adj:
            return AlgorithmResult(
                algorithm=self.name,
                data={
                    "communities": [],
                    "modularity": 0.0,
                    "community_count": 0,
                },
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # ------------------------------------------------------------------
        # 2. Run Louvain
        # ------------------------------------------------------------------
        community_list: list[dict] = []
        modularity: float = 0.0

        try:
            # --- NetworkX Louvain (preferred) ---
            community_list, modularity = self._networkx_louvain(adj)
        except ImportError:
            # --- Built-in simplified Louvain (fallback) ---
            communities, _ = self._simple_louvain(adj)
            community_list = [
                {"id": f"community_{i}", "members": sorted(c), "size": len(c)}
                for i, c in enumerate(communities)
            ]
            errors.append(
                "NetworkX not available, used built-in simplified Louvain"
            )
        except Exception as exc:
            # NetworkX available but raised — fallback
            communities, _ = self._simple_louvain(adj)
            community_list = [
                {"id": f"community_{i}", "members": sorted(c), "size": len(c)}
                for i, c in enumerate(communities)
            ]
            errors.append(str(exc))

        elapsed = (time.perf_counter() - start) * 1000

        return AlgorithmResult(
            algorithm=self.name,
            data={
                "communities": community_list,
                "modularity": round(modularity, 4),
                "community_count": len(community_list),
            },
            duration_ms=round(elapsed, 2),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # NetworkX path
    # ------------------------------------------------------------------

    @staticmethod
    def _networkx_louvain(adj: dict[str, set[str]]) -> tuple[list[dict], float]:
        """Execute NetworkX Louvain community detection.

        Returns
        -------
        (community_list, modularity)
        """
        import networkx as nx

        G = nx.Graph()
        for node, neighbors in adj.items():
            for nb in neighbors:
                G.add_edge(node, nb)

        raw_communities = list(nx.community.louvain_communities(G))
        modularity = nx.community.modularity(G, raw_communities)

        community_list = [
            {"id": f"community_{i}", "members": sorted(c), "size": len(c)}
            for i, c in enumerate(raw_communities)
        ]
        return community_list, modularity

    # ------------------------------------------------------------------
    # Built-in simplified Louvain (no NetworkX dependency)
    # ------------------------------------------------------------------

    @staticmethod
    def _simple_louvain(
        adj: dict[str, set[str]],
    ) -> tuple[list[set[str]], float]:
        """Built-in simplified Louvain algorithm.

        Algorithm:
            1. Each node starts in its own community.
            2. Greedy pass: for each node, evaluate moving to neighbour
               communities; accept the move with the best modularity gain.
            3. Repeat until convergence or max rounds.

        Parameters
        ----------
        adj : dict[str, set[str]]
            Undirected adjacency dict.

        Returns
        -------
        (list of community sets, modularity)
        """
        nodes = list(adj.keys())

        # Each node = its own community
        node_to_comm: dict[str, str] = {n: n for n in nodes}
        communities: dict[str, set[str]] = {n: {n} for n in nodes}

        # Total edge weight (each undirected edge counted once from adj)
        total_weight = 0
        seen_edges: set[tuple[str, str]] = set()
        for u, neighbors in adj.items():
            for v in neighbors:
                edge = (u, v) if u < v else (v, u)
                if edge not in seen_edges:
                    seen_edges.add(edge)
                    total_weight += 1

        if total_weight == 0:
            return list(communities.values()), 0.0

        # Total degree of each node for normalization
        degree: dict[str, int] = {n: len(adj[n]) for n in nodes}

        max_rounds = 20
        for _round in range(max_rounds):
            changed = False
            for node in nodes:
                current_comm = node_to_comm[node]
                current_comm_nodes = communities[current_comm]

                # Sum of edge weights from node to each neighbour community
                comm_weights: dict[str, float] = {}
                for nb in adj[node]:
                    nc = node_to_comm[nb]
                    if nc == current_comm:
                        continue
                    comm_weights[nc] = comm_weights.get(nc, 0.0) + 1.0

                if not comm_weights:
                    continue

                # Modularity gain: ΔQ = (k_i_in / m) - (k_i * Σ_tot / (2 * m^2))
                # Simplified: ΔQ ≈ (w_to_comm / total_weight)
                #              - (degree[node] * degree_sum_of_comm / (total_weight^2))
                best_comm = current_comm
                best_gain = 0.0
                for target_comm, w_to_comm in comm_weights.items():
                    tgt_nodes = communities[target_comm]
                    sum_deg = sum(degree[n] for n in tgt_nodes)

                    gain = (w_to_comm / total_weight) - (
                        degree[node] * sum_deg / (2.0 * total_weight * total_weight)
                    )

                    if gain > best_gain:
                        best_gain = gain
                        best_comm = target_comm

                if best_comm != current_comm:
                    communities[current_comm].discard(node)
                    if not communities[current_comm]:
                        del communities[current_comm]
                    communities[best_comm].add(node)
                    node_to_comm[node] = best_comm
                    changed = True

            if not changed:
                break

        # Build non-empty community list
        non_empty = [c for c in communities.values() if c]
        modularity = CommunityDetector._compute_modularity(
            adj, non_empty, node_to_comm, total_weight
        )
        return non_empty, modularity

    # ------------------------------------------------------------------
    # Modularity computation for built-in Louvain
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_modularity(
        adj: dict[str, set[str]],
        community_sets: list[set[str]],
        node_to_comm: dict[str, str],
        total_weight: int,
    ) -> float:
        """Compute Newman-Girvan modularity for the current partition.

        Q = 1/(2m) * Σ_ij [A_ij - (k_i * k_j) / (2m)] * δ(c_i, c_j)
        """
        if total_weight == 0:
            return 0.0

        degree: dict[str, int] = {n: len(adj[n]) for n in adj}
        Q = 0.0
        two_m = 2 * total_weight

        seen: set[tuple[str, str]] = set()
        for u, neighbors in adj.items():
            for v in neighbors:
                edge = (u, v) if u < v else (v, u)
                if edge in seen:
                    continue
                seen.add(edge)
                A_uv = 1.0
                expected = (degree[u] * degree[v]) / two_m
                if node_to_comm[u] == node_to_comm[v]:
                    Q += A_uv - expected

        return Q / two_m
