"""11 semantic search signals for multi-dimensional ranking.

Each signal independently scores a candidate node against a query,
returning a float in [0.0, 1.0]. The raw score is returned by compute().
Weights are metadata applied by the caller (semantic_query), NOT inside
compute() — this keeps unit tests independent of default weight values.

Design: signals only depend on pre-computed resources passed through *ctx*.
They never instantiate heavy objects inside compute().
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Optional


# ============================================================================
# TypedDict helpers (runtime-compatible)
# ============================================================================

class ScoredResult(dict):
    """Extended search result with per-signal scores."""
    pass


class StoreContext(dict):
    """Pre-computed context for signal computation."""
    pass


# ============================================================================
# Abstract base
# ============================================================================


class Signal(ABC):
    """Base class for all semantic search signals.

    Attributes
    ----------
    name : str
        Unique signal name (e.g. ``"bm25"``, ``"qualified_name_match"``).
    weight : float
        Multiplier applied to the raw score, capped at 1.0. Default 1.0.
    """

    name: str = ""
    weight: float = 1.0

    def __init__(self, weight: float | None = None, **kwargs: Any) -> None:
        # Allow subclasses to set custom weight; use default if not provided
        if weight is not None:
            self.weight = weight
        super().__init__(**kwargs)

    @abstractmethod
    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        """Score *candidate* against *query*, returning a float in [0.0, 1.0].

        Parameters
        ----------
        query : str
            The user's search query text.
        candidate : dict
            A single node row (from FTS5 / nodes table) with at least
            ``id``, ``name``, ``qualified_name``, ``kind``, ``file_path``,
            ``signature``, ``docstring``, ``rank``.
            May contain a ``body`` field when available.
        ctx : dict
            Pre-computed resources:
            - ``queries``: QueryBuilder instance
            - ``traverser``: GraphTraverser instance
            - ``minhash``: MinHash instance
            - ``lsh``: LSHIndex instance (optional)
            - ``centrality_scores``: dict[node_id -> float]
            - ``clone_pairs``: list of clone pair dicts
            - ``embeddings_model``: optional embeddings model

        Returns
        -------
        float
            Score in [0.0, 1.0]. 0.0 means no match. 1.0 means perfect match.
        """
        ...

    def _weighted(self, raw: float) -> float:
        """Apply weight and cap at 1.0."""
        return min(1.0, raw * self.weight)


# ============================================================================
# Helper: tokenize text into lowercase word tokens
# ============================================================================

_TOKENIZE_RE = re.compile(r"[a-zA-Z_]\w*")

def _tokenize(text: str) -> list[str]:
    """Extract word tokens from *text*, lowercased."""
    if not text:
        return []
    return [t.lower() for t in _TOKENIZE_RE.findall(text)]


def _jaccard_tokens(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Jaccard similarity between two token lists."""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


# ============================================================================
# Signal 1: BM25 FTS5 rank
# ============================================================================


class BM25Signal(Signal):
    """Convert FTS5 BM25 rank to a [0, 1] score.

    Formula: ``1 / (1 + rank)``. rank > 0 is the FTS5 BM25 score.
    Lower FTS5 rank = better match = higher score.
    """

    name = "bm25"
    weight = 1.0

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            rank = candidate.get("rank")
            if rank is None:
                return 0.0
            raw = 1.0 / (1.0 + float(rank))
            return min(1.0, raw)
        except Exception:
            return 0.0


# ============================================================================
# Signal 2: Qualified name match
# ============================================================================


class QualifiedNameMatchSignal(Signal):
    """Match query against the candidate's qualified_name.

    Scoring:
    - Exact match (case-insensitive): 1.0
    - Prefix match (qualified_name starts with query): 0.5
    - Query text appears inside qualified_name: 0.3
    - No match: 0.0
    """

    name = "qualified_name_match"
    weight = 1.5

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            qname = (candidate.get("qualified_name") or "").lower()
            q = query.lower().strip()
            if not q or not qname:
                return 0.0

            if qname == q:
                return min(1.0,1.0)
            if qname.startswith(q):
                return min(1.0,0.5)
            if q in qname:
                return min(1.0,0.3)
            return 0.0
        except Exception:
            return 0.0


# ============================================================================
# Signal 3: Docstring match
# ============================================================================


class DocstringMatchSignal(Signal):
    """Token overlap between query and candidate docstring.

    Uses Jaccard-like ratio: ``|Q_tokens ∩ D_tokens| / |Q_tokens|``.
    """

    name = "docstring_match"
    weight = 0.8

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            docstring = candidate.get("docstring") or ""
            if not docstring or not query:
                return 0.0

            q_tokens = set(_tokenize(query))
            d_tokens = set(_tokenize(docstring))
            if not q_tokens:
                return 0.0

            overlap = len(q_tokens & d_tokens)
            raw = overlap / len(q_tokens)
            return min(1.0,raw)
        except Exception:
            return 0.0


# ============================================================================
# Signal 4: AST similarity (MinHash Jaccard on body)
# ============================================================================


class ASTSimilaritySignal(Signal):
    """Structural similarity between query (treated as code) and
    the candidate's ``body`` field using MinHash Jaccard estimation.

    Uses ``extract_ast_tokens`` + ``MinHash.compute_signature`` +
    ``estimate_jaccard`` from the P4 minhash module.
    """

    name = "ast_similarity"
    weight = 1.2

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            body = candidate.get("body") or ""
            if not body or not query:
                return 0.0

            minhash = ctx.get("minhash")
            if minhash is None:
                return 0.0

            from tws_graph.graph.algorithms.minhash import (
                extract_ast_tokens,
                estimate_jaccard,
            )

            query_tokens = extract_ast_tokens(query)
            body_tokens = extract_ast_tokens(body)

            if not query_tokens or not body_tokens:
                return 0.0

            sig_q = minhash.compute_signature(query_tokens)
            sig_b = minhash.compute_signature(body_tokens)

            raw = estimate_jaccard(sig_q, sig_b)
            return min(1.0,raw)
        except Exception:
            return 0.0


# ============================================================================
# Signal 5: API signature similarity
# ============================================================================


class APISignatureSimilaritySignal(Signal):
    """Similarity between query tokens and candidate ``signature`` tokens
    using MinHash Jaccard estimation.

    Tokenization is word-based (simple alpha-numeric split), not AST-based.
    """

    name = "api_signature_similarity"
    weight = 1.0

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            signature = candidate.get("signature") or ""
            if not signature or not query:
                return 0.0

            minhash = ctx.get("minhash")
            if minhash is None:
                return 0.0

            from tws_graph.graph.algorithms.minhash import estimate_jaccard

            q_tokens = _tokenize(query)
            s_tokens = _tokenize(signature)

            if not q_tokens or not s_tokens:
                return 0.0

            sig_q = minhash.compute_signature(q_tokens)
            sig_s = minhash.compute_signature(s_tokens)

            raw = estimate_jaccard(sig_q, sig_s)
            return min(1.0,raw)
        except Exception:
            return 0.0


# ============================================================================
# Signal 6: Clone similarity
# ============================================================================


class CloneSimilaritySignal(Signal):
    """Score based on pre-computed clone pairs in *ctx*.

    If the candidate's ``id`` appears in any clone pair, the maximum
    similarity value from matching pairs is used as the score.
    """

    name = "clone_similarity"
    weight = 0.8

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            clone_pairs = ctx.get("clone_pairs")
            if not clone_pairs:
                return 0.0

            cid = candidate.get("id")
            if not cid:
                return 0.0

            best = 0.0
            for pair in clone_pairs:
                if pair.get("node_a") == cid or pair.get("node_b") == cid:
                    sim = pair.get("similarity", 0.0)
                    if sim > best:
                        best = sim
            return min(1.0,best)
        except Exception:
            return 0.0


# ============================================================================
# Signal 7: Module proximity
# ============================================================================


class ModuleProximitySignal(Signal):
    """Score based on file-path / qualified-name prefix matching.

    - Query prefix matches file_path prefix → 0.3 (same module)
    - Query prefix matches qualified_name prefix → 0.3
    - Same directory (parent dir matches) → 0.15
    - No match → 0.0

    Takes the best match across both dimensions.
    """

    name = "module_proximity"
    weight = 0.7

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            if not query or not query.strip():
                return 0.0

            q = query.strip().lower()
            file_path = (candidate.get("file_path") or "").lower()
            qname = (candidate.get("qualified_name") or "").lower()

            best = 0.0

            # Check file_path prefix
            if file_path and file_path.startswith(q):
                best = max(best, 0.3)

            # Check qualified_name prefix
            if qname and qname.startswith(q):
                best = max(best, 0.3)

            # Check same directory via parent path matching
            import os
            if file_path:
                parent = os.path.dirname(file_path)
                if parent and q in parent:
                    best = max(best, 0.15)

            return min(1.0,best)
        except Exception:
            return 0.0


# ============================================================================
# Signal 8: Graph diffusion (impact radius)
# ============================================================================


class GraphDiffusionSignal(Signal):
    """Score based on whether the candidate is in the impact radius of
    nodes matching the query.

    Finds nodes whose name/qualified_name matches the query, then checks
    if the candidate falls within their BFS impact radius via
    ``GraphTraverser.get_impact_radius()``.

    Score: ``1 / (1 + depth)`` where *depth* is the BFS distance.
    """

    name = "graph_diffusion"
    weight = 1.0

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            traverser = ctx.get("traverser")
            queries = ctx.get("queries")
            if not traverser or not queries:
                return 0.0

            cid = candidate.get("id")
            if not cid:
                return 0.0

            # Find query nodes via FTS5
            query_nodes = queries.search_nodes(query, limit=5)
            if not query_nodes:
                return 0.0

            best = 0.0
            for qn in query_nodes:
                qid = qn["id"]
                if qid == cid:
                    # Candidate is the query node itself → high score
                    best = max(best, 1.0)
                    continue

                impact = traverser.get_impact_radius(qid, max_depth=3)
                impacted_nodes = impact.get("nodes", {})
                if cid in impacted_nodes:
                    # Estimate depth from edges: count hops from qid to cid
                    depth = self._estimate_depth(qid, cid, impact.get("edges", []))
                    score = 1.0 / (1.0 + float(depth))
                    best = max(best, score)

            return min(1.0,best)
        except Exception:
            return 0.0

    @staticmethod
    def _estimate_depth(
        source_id: str, target_id: str, edges: list[dict]
    ) -> int:
        """Estimate BFS depth from *source_id* to *target_id* using *edges*.

        Returns 3 (max depth) if path not determinable.
        """
        if not edges:
            return 3

        # Build adjacency from edges (both directions)
        adj: dict[str, set[str]] = {}
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s:
                adj.setdefault(s, set()).add(t)
            if t:
                adj.setdefault(t, set()).add(s)

        # BFS from source
        from collections import deque
        visited = {source_id}
        queue = deque([(source_id, 0)])
        while queue:
            node, dist = queue.popleft()
            if node == target_id:
                return dist
            if dist >= 3:
                continue
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))

        return 3


# ============================================================================
# Signal 9: Caller / Callee proximity
# ============================================================================


class CallerCalleeProximitySignal(Signal):
    """Score based on direct call-graph proximity.

    Finds query-matching nodes, then checks via
    ``GraphTraverser.get_calls()`` whether the candidate is a
    direct caller/callee (depth=1 → 1.0) or one hop away (depth=2 → 0.5).
    """

    name = "caller_callee_proximity"
    weight = 1.2

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            traverser = ctx.get("traverser")
            queries = ctx.get("queries")
            if not traverser or not queries:
                return 0.0

            cid = candidate.get("id")
            if not cid:
                return 0.0

            query_nodes = queries.search_nodes(query, limit=5)
            if not query_nodes:
                return 0.0

            best = 0.0
            for qn in query_nodes:
                qid = qn["id"]
                if qid == cid:
                    best = max(best, 1.0)
                    continue

                calls = traverser.get_calls(qid, direction="both", max_depth=2)
                call_nodes = calls.get("nodes", {})
                if cid in call_nodes:
                    # Determine depth from edges
                    depth = self._estimate_call_depth(
                        qid, cid, calls.get("edges", [])
                    )
                    if depth == 1:
                        best = max(best, 1.0)
                    elif depth == 2:
                        best = max(best, 0.5)

            return min(1.0,best)
        except Exception:
            return 0.0

    @staticmethod
    def _estimate_call_depth(
        source_id: str, target_id: str, edges: list[dict]
    ) -> int:
        """Estimate BFS distance via call edges."""
        if not edges:
            return 2
        adj: dict[str, set[str]] = {}
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s:
                adj.setdefault(s, set()).add(t)
            if t:
                adj.setdefault(t, set()).add(s)
        from collections import deque
        visited = {source_id}
        queue = deque([(source_id, 0)])
        while queue:
            node, dist = queue.popleft()
            if node == target_id:
                return dist
            if dist >= 2:
                continue
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))
        return 2


# ============================================================================
# Signal 10: Graph centrality (PageRank)
# ============================================================================


class GraphCentralitySignal(Signal):
    """Score based on pre-computed centrality scores in *ctx*.

    Looks up the candidate's ``id`` in ``ctx["centrality_scores"]``.
    The raw score is the centrality value; the final score is
    raw / max_score (normalized to [0, 1]).
    """

    name = "graph_centrality"
    weight = 0.6

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            scores: dict | None = ctx.get("centrality_scores")
            if not scores:
                return 0.0

            cid = candidate.get("id")
            if not cid:
                return 0.0

            raw = scores.get(cid)
            if raw is None:
                return 0.0

            # Normalize by max score
            max_score = max(scores.values()) if scores else 0.0
            if max_score <= 0.0:
                return 0.0

            normalized = raw / max_score
            return min(1.0,normalized)
        except Exception:
            return 0.0


# ============================================================================
# Signal 11: Data flow connection
# ============================================================================


class DataFlowConnectionSignal(Signal):
    """Score based on data-flow edge paths between query nodes and the
    candidate, using ``GraphTraverser.find_path()``.

    If a path exists via DATA_FLOWS / READS / WRITES edges:
    score = ``1 / (1 + path_length)``.
    """

    name = "data_flow_connection"
    weight = 0.5

    def compute(self, query: str, candidate: dict, ctx: dict) -> float:
        try:
            traverser = ctx.get("traverser")
            queries = ctx.get("queries")
            if not traverser or not queries:
                return 0.0

            cid = candidate.get("id")
            if not cid:
                return 0.0

            query_nodes = queries.search_nodes(query, limit=5)
            if not query_nodes:
                return 0.0

            best = 0.0
            for qn in query_nodes:
                qid = qn["id"]
                if qid == cid:
                    best = max(best, 1.0)
                    continue

                path = traverser.find_path(qid, cid)
                if path and len(path) > 1:
                    # path includes both from and to nodes;
                    # path_length = number of edges = len(path) - 1
                    path_len = len(path) - 1
                    score = 1.0 / (1.0 + path_len)
                    best = max(best, score)

            return min(1.0,best)
        except Exception:
            return 0.0
