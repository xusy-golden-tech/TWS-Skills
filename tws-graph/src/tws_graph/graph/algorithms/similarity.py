"""Code clone detection using MinHash + LSH.

Uses MinHash signatures and LSH bucketing to efficiently find
near-duplicate function/method bodies in a code graph.

References:
- Broder, A. "On the resemblance and containment of documents" (1997)
- Indyk, P. & Motwani, R. "Approximate nearest neighbors" (1998)
"""

from __future__ import annotations

from tws_graph.graph.algorithms.base import GraphAlgorithm, AlgorithmResult
from tws_graph.graph.algorithms.minhash import (
    MinHash,
    LSHIndex,
    extract_ast_tokens,
    estimate_jaccard,
)
from tws_graph.store.interface import Store


class CloneDetector(GraphAlgorithm):
    """Code clone detector using MinHash + LSH.

    Algorithm flow:
    1. Read all function/method nodes from Store via ``iter_nodes_by_kind``
    2. Extract AST tokens from each node's ``body`` field
    3. Compute MinHash signatures
    4. Build LSH index, find candidate similar pairs
    5. Filter by Jaccard similarity threshold, sort by similarity descending

    Parameters
    ----------
    threshold : float
        Minimum Jaccard similarity for reporting a clone pair. Default 0.8.
    num_perm : int
        MinHash signature dimensions. Default 128.
    bands : int
        LSH band count. Default 16.
    rows : int
        Rows per band. Default 8. ``bands * rows`` should match ``num_perm``.
    """

    @property
    def name(self) -> str:
        return "clone-detection"

    @property
    def description(self) -> str:
        return "MinHash+LSH based code clone detection"

    def __init__(
        self,
        threshold: float = 0.8,
        num_perm: int = 128,
        bands: int = 16,
        rows: int = 8,
    ) -> None:
        self._threshold = threshold
        self._num_perm = num_perm
        self._bands = bands
        self._rows = rows

    def run(self, store: Store) -> AlgorithmResult:
        import time
        start = time.perf_counter()

        # 1. Gather all function and method nodes
        functions = list(store.iter_nodes_by_kind("function"))
        methods = list(store.iter_nodes_by_kind("method"))
        nodes = functions + methods

        # Build id -> node lookup for result enrichment
        nodes_by_id = {node["id"]: node for node in nodes}

        # 2. Compute MinHash signatures for nodes with non-empty body
        mh = MinHash(num_perm=self._num_perm)
        signatures: dict[str, list[int]] = {}
        body_map: dict[str, str] = {}
        for node in nodes:
            body = node.get("body", "") or ""
            if body:
                tokens = extract_ast_tokens(body)
                sig = mh.compute_signature(tokens)
                signatures[node["id"]] = sig
                body_map[node["id"]] = body

        # 3. Build LSH index
        lsh = LSHIndex(bands=self._bands, rows=self._rows)
        for nid, sig in signatures.items():
            lsh.insert(nid, sig)

        # 4. Find candidate similar pairs
        pairs = lsh.find_similar_pairs()

        # 5. Filter by threshold and enrich with metadata
        similar_pairs: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for id1, id2, _ in pairs:
            key = tuple(sorted([id1, id2]))
            if key in seen:
                continue
            seen.add(key)
            jaccard = estimate_jaccard(signatures[id1], signatures[id2])
            if jaccard >= self._threshold:
                similar_pairs.append({
                    "node_a": id1,
                    "node_b": id2,
                    "similarity": round(jaccard, 4),
                    "name_a": nodes_by_id[id1].get("name", ""),
                    "name_b": nodes_by_id[id2].get("name", ""),
                })

        similar_pairs.sort(key=lambda x: x["similarity"], reverse=True)
        elapsed = (time.perf_counter() - start) * 1000

        return AlgorithmResult(
            algorithm=self.name,
            data={
                "similar_pairs": similar_pairs,
                "total_functions_checked": len(nodes),
                "total_comparisons": len(pairs),
                "threshold": self._threshold,
            },
            duration_ms=round(elapsed, 2),
        )
