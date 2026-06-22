"""Centrality analysis algorithms — PageRank and Betweenness.

Provides CentralityComputer, a GraphAlgorithm implementation that computes
node centrality scores over a call graph (or other edge-type-filtered graph).

Supports two algorithms:
- pagerank: PageRank centrality (damping-factor based iterative computation)
- betweenness: Betweenness centrality (shortest-path based, Brandes algorithm)

Prioritises NetworkX when available; falls back to built-in pure-Python
implementations with zero external dependencies.

Reference:
- Page, L., Brin, S., Motwani, R., Winograd, T. (1999) "The PageRank Citation Ranking"
- Brandes, U. (2001) "A Faster Algorithm for Betweenness Centrality"

Design decisions:
- Edge-type filtering via ``edge_kinds`` parameter — only edges matching
  specified kinds are included in the graph used for centrality computation.
- Default edge kind is ``["calls"]``, representing the function call graph.
- All node pairs with edges in BOTH directions count as strongly connected
  for betweenness computation (directed graph).
- Top-100 results returned sorted by descending score.
- Scores rounded to 6 decimal places for readability.
- NetworkX is tried first for correctness and performance; built-in
  implementation serves as a zero-dependency fallback.
"""

from __future__ import annotations

from tws_graph.graph.algorithms.base import GraphAlgorithm, AlgorithmResult
from tws_graph.store.interface import Store


class CentralityComputer(GraphAlgorithm):
    """Centrality analysis algorithm.

    Supports PageRank and Betweenness centrality computation.
    Prioritises NetworkX when available; falls back to built-in pure-Python
    implementation when NetworkX is not installed.

    Parameters
    ----------
    algorithm : str
        ``"pagerank"`` or ``"betweenness"``. Default ``"pagerank"``.
    edge_kinds : list[str] | None
        Only edges whose ``kind`` is in this list are included.
        Default ``["calls"]``.
    damping_factor : float
        Damping factor for PageRank (0.0–1.0). Default 0.85.
    max_iterations : int
        Maximum iterations for PageRank convergence. Default 100.
    """

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "centrality"

    @property
    def description(self) -> str:
        return "PageRank and Betweenness centrality computation"

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    def __init__(
        self,
        algorithm: str = "pagerank",
        edge_kinds: list[str] | None = None,
        damping_factor: float = 0.85,
        max_iterations: int = 100,
    ) -> None:
        self._algorithm = algorithm
        self._edge_kinds = edge_kinds or ["calls"]
        self._damping = damping_factor
        self._max_iter = max_iterations

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, store: Store) -> AlgorithmResult:
        """Execute centrality computation against *store*.

        Builds an adjacency list from ``iter_all_nodes`` and
        ``iter_all_edges``, then delegates to NetworkX or the built-in
        implementation.

        Returns an AlgorithmResult with ``scores``, ``node_count``, and
        ``method`` in the data dict.
        """
        import time
        start = time.perf_counter()
        errors: list[str] = []

        # ---------- Build adjacency list ----------
        adj: dict[str, set[str]] = {}
        nodes_set: set[str] = set()

        for node in store.iter_all_nodes():
            nid = node["id"]
            nodes_set.add(nid)
            adj.setdefault(nid, set())

        for edge in store.iter_all_edges():
            kind = edge.get("kind", "")
            if kind not in self._edge_kinds:
                continue
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in nodes_set and tgt in nodes_set:
                adj.setdefault(src, set()).add(tgt)

        # ---------- Empty graph guard ----------
        if not adj:
            return AlgorithmResult(
                algorithm=self.name,
                data={"scores": {}},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # ---------- Try NetworkX ----------
        try:
            import networkx as nx

            G = nx.DiGraph()
            for node in adj:
                G.add_node(node)
                for target in adj[node]:
                    G.add_edge(node, target)

            if self._algorithm == "pagerank":
                scores = nx.pagerank(
                    G, alpha=self._damping, max_iter=self._max_iter
                )
            else:  # betweenness
                scores = nx.betweenness_centrality(G)
        except ImportError:
            # Fall back to built-in implementations
            if self._algorithm == "pagerank":
                scores = self._simple_pagerank(adj)
            else:
                scores = self._simple_betweenness(adj)
            errors.append(
                "NetworkX not available, used built-in implementation"
            )
        except Exception as e:
            errors.append(str(e))
            scores = {}

        # ---------- Build result ----------
        elapsed = (time.perf_counter() - start) * 1000

        # Sort by score descending, keep top 100
        top_nodes = sorted(
            scores.items(), key=lambda x: x[1], reverse=True
        )[:100]

        return AlgorithmResult(
            algorithm=self.name,
            data={
                "scores": {
                    nid: round(score, 6) for nid, score in top_nodes
                },
                "node_count": len(nodes_set),
                "method": self._algorithm,
            },
            duration_ms=round(elapsed, 2),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Built-in PageRank
    # ------------------------------------------------------------------

    def _simple_pagerank(self, adj: dict[str, set[str]]) -> dict[str, float]:
        """Built-in PageRank implementation — zero external dependencies.

        Uses the standard iterative formula:

            PR(v) = (1-d)/N + d * Σ PR(u) / out_degree(u)

        where the sum is over all ``u`` that have an edge ``u → v``.
        """
        nodes = list(adj.keys())
        n = len(nodes)
        if n == 0:
            return {}

        pr = {node: 1.0 / n for node in nodes}

        for _ in range(self._max_iter):
            new_pr: dict[str, float] = {}
            for node in nodes:
                rank = (1.0 - self._damping) / n
                for other in nodes:
                    if node in adj.get(other, set()):
                        out_deg = max(len(adj[other]), 1)
                        rank += self._damping * pr[other] / out_deg
                new_pr[node] = rank
            pr = new_pr

        return pr

    # ------------------------------------------------------------------
    # Built-in Betweenness (Brandes)
    # ------------------------------------------------------------------

    def _simple_betweenness(
        self, adj: dict[str, set[str]]
    ) -> dict[str, float]:
        """Built-in Betweenness centrality — Brandes' algorithm.

        Computes betweenness centrality for a directed, unweighted graph
        using BFS from each source node.  Complexity O(V * (V + E)).

        Reference: Brandes, U. (2001) "A Faster Algorithm for Betweenness
        Centrality", Journal of Mathematical Sociology 25(2):163-177.
        """
        nodes = list(adj.keys())
        betweenness: dict[str, float] = {node: 0.0 for node in nodes}

        for source in nodes:
            # --- BFS from source ---
            stack: list[str] = []
            predecessors: dict[str, list[str]] = {
                node: [] for node in nodes
            }
            sigma: dict[str, float] = {node: 0.0 for node in nodes}
            sigma[source] = 1.0
            dist: dict[str, int] = {node: -1 for node in nodes}
            dist[source] = 0
            queue: list[str] = [source]

            while queue:
                v = queue.pop(0)
                stack.append(v)
                for w in adj.get(v, set()):
                    # Found w for the first time
                    if dist[w] < 0:
                        queue.append(w)
                        dist[w] = dist[v] + 1
                    # Shortest path to w via v
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        predecessors[w].append(v)

            # --- Accumulation ---
            delta: dict[str, float] = {node: 0.0 for node in nodes}
            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != source:
                    betweenness[w] += delta[w]

        return betweenness
