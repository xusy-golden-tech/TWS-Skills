"""Semantic search orchestrator.

Combines FTS5 BM25 pre-filtering with multi-signal weighted scoring to produce
relevance-ranked code symbol search results.

Architecture (P8):
    1. FTS5 BM25 pre-filters the top-200 candidate set
    2. 11 domain-specific signals score each candidate
    3. Weighted-average rank determines final sort order
    4. Top-limit results returned as SemanticSearchResult

Design: search-semantic.md
"""

from __future__ import annotations

import os
import time
from typing import Optional


# ---------------------------------------------------------------------------
# SemanticSearchResult
# ---------------------------------------------------------------------------

class SemanticSearchResult:
    """Result container for semantic_search().

    Attributes:
        results: Ranked list of candidate dicts (each with added ``_score``).
        query: The original search query string.
        candidate_count: Number of FTS pre-filtered candidates before ranking.
        duration_ms: Wall-clock time of the full query pipeline.
        signals_used: Names of signals that contributed to scoring.
        embeddings_enabled: Whether semantic embeddings were active.
    """

    def __init__(
        self,
        results: list,
        query: str,
        candidate_count: int,
        duration_ms: float,
        signals_used: list[str],
        embeddings_enabled: bool,
    ):
        self.results = results
        self.query = query
        self.candidate_count = candidate_count
        self.duration_ms = duration_ms
        self.signals_used = signals_used
        self.embeddings_enabled = embeddings_enabled


# ---------------------------------------------------------------------------
# Signal loading
# ---------------------------------------------------------------------------

def _load_signals():
    """Load signal instances from ``tws_graph.search.signals``.

    Returns:
        (signals, signal_names) — if signals.py does not exist yet (parallel
        agent may still be implementing), returns empty lists.
    """
    try:
        from tws_graph.search.signals import (  # noqa: F811
            APISignatureSimilaritySignal,
            ASTSimilaritySignal,
            BM25Signal,
            CallerCalleeProximitySignal,
            CloneSimilaritySignal,
            DataFlowConnectionSignal,
            DocstringMatchSignal,
            GraphCentralitySignal,
            GraphDiffusionSignal,
            ModuleProximitySignal,
            QualifiedNameMatchSignal,
        )
        signals = [
            BM25Signal(),
            QualifiedNameMatchSignal(),
            DocstringMatchSignal(),
            ASTSimilaritySignal(),
            APISignatureSimilaritySignal(),
            CloneSimilaritySignal(),
            ModuleProximitySignal(),
            GraphDiffusionSignal(),
            CallerCalleeProximitySignal(),
            GraphCentralitySignal(),
            DataFlowConnectionSignal(),
        ]
        return signals, [s.name for s in signals]
    except ImportError:
        return [], []


# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------

def _get_candidates(store_or_queries, query: str, limit: int = 200) -> list[dict]:
    """Retrieve FTS5 pre-filtered candidate set.

    Accepts a Store instance, QueryBuilder instance, or db_path str.
    Normalises each into a usable query interface and returns a list of
    candidate dicts.
    """
    # --- Store instance ---
    if hasattr(store_or_queries, "fts_search"):
        return store_or_queries.fts_search(query, limit=limit)

    # --- QueryBuilder instance ---
    if hasattr(store_or_queries, "search_nodes"):
        rows = store_or_queries.search_nodes(query, limit=limit)
        return [dict(r) for r in rows]

    # --- db_path string ---
    if isinstance(store_or_queries, str):
        from tws_graph.store.sqlite_store import SqliteStore
        if not os.path.exists(store_or_queries):
            raise FileNotFoundError(
                f"Database file not found: {store_or_queries}"
            )
        store = SqliteStore(store_or_queries)
        try:
            return store.fts_search(query, limit=limit)
        finally:
            store.close()

    raise TypeError(
        f"store_or_queries must be Store, QueryBuilder, or db_path str, "
        f"got {type(store_or_queries)}"
    )


# ---------------------------------------------------------------------------
# Store context builder
# ---------------------------------------------------------------------------

def _build_ctx(store_or_queries) -> dict:
    """Build the store_context dict signals use for pre-computed artefacts.

    Lazy-initialised fields (minhash, centrality, embeddings) are set to None
    and created on first access by the relevant signal.
    """
    ctx: dict = {
        "queries": None,
        "traverser": None,
        "minhash": None,
        "lsh": None,
        "centrality_scores": None,
        "embeddings_model": None,
        "clone_pairs": None,
    }

    # If we have a QueryBuilder, store it so signals can issue graph queries
    if hasattr(store_or_queries, "search_nodes"):
        ctx["queries"] = store_or_queries

    return ctx


# ---------------------------------------------------------------------------
# Semantic query
# ---------------------------------------------------------------------------

def semantic_query(
    query: str,
    store_or_queries,
    limit: int = 20,
    signal_weights: Optional[dict[str, float]] = None,
    use_embeddings: bool = False,
) -> SemanticSearchResult:
    """Rank code symbols by multi-signal weighted relevance.

    1. Validate input
    2. FTS5 BM25 pre-filter top-200 candidates
    3. Initialise store_context (MinHash, Centrality, CloneDetector — lazy)
    4. For each candidate, run every signal and compute weighted-average rank
    5. Sort by rank descending, truncate to *limit*
    6. Return ``SemanticSearchResult``

    Args:
        query: The search string. Must be non-empty.
        store_or_queries: ``Store`` instance, ``QueryBuilder`` instance, or
            ``db_path`` string.
        limit: Max number of results to return (default 20).
        signal_weights: Optional per-signal weight overrides, e.g.
            ``{"BM25": 2.0, "GraphDiffusion": 0.5}``.
        use_embeddings: Attempt to load a semantic embedding model. If the
            model is not available, ``embeddings_enabled`` will be False.

    Returns:
        ``SemanticSearchResult`` with ranked candidates.

    Raises:
        ValueError: *query* is empty or None.
        FileNotFoundError: *store_or_queries* is a non-existent file path.
        TypeError: *store_or_queries* has an unsupported type.
    """
    # ------------------------------------------------------------------
    # 1. Validate input
    # ------------------------------------------------------------------
    if query is None or query.strip() == "":
        raise ValueError("query must be a non-empty string")

    t0 = time.perf_counter()

    # ------------------------------------------------------------------
    # 2. FTS5 pre-filter candidates
    # ------------------------------------------------------------------
    candidates = _get_candidates(store_or_queries, query, limit=200)

    # ------------------------------------------------------------------
    # 3. Load signals & apply custom weights
    # ------------------------------------------------------------------
    signals, signal_names = _load_signals()

    if signal_weights:
        for sig in signals:
            if sig.name in signal_weights:
                sig.weight = signal_weights[sig.name]

    # ------------------------------------------------------------------
    # 4. Build context
    # ------------------------------------------------------------------
    ctx = _build_ctx(store_or_queries)

    # Attempt embeddings setup if requested
    embeddings_enabled = False
    if use_embeddings:
        try:
            # Lazy: import sentence-transformers only when needed
            from sentence_transformers import SentenceTransformer
            ctx["embeddings_model"] = SentenceTransformer(
                "all-MiniLM-L6-v2"
            )
            embeddings_enabled = True
        except ImportError:
            embeddings_enabled = False
        except Exception:
            embeddings_enabled = False

    # ------------------------------------------------------------------
    # 5. Score each candidate
    # ------------------------------------------------------------------
    if not candidates or not signals:
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return SemanticSearchResult(
            results=[] if not candidates else [
                {**c, "_score": 0.0} for c in candidates[:limit]
            ],
            query=query,
            candidate_count=len(candidates),
            duration_ms=duration_ms,
            signals_used=signal_names,
            embeddings_enabled=embeddings_enabled,
        )

    total_weight = sum(s.weight for s in signals)
    scored = []

    for candidate in candidates:
        weighted_sum = 0.0
        for sig in signals:
            try:
                score = sig.compute(query, candidate, ctx)
            except Exception:
                score = 0.0
            weighted_sum += sig.weight * score

        rank = weighted_sum / total_weight if total_weight > 0 else 0.0
        candidate["_score"] = rank
        scored.append(candidate)

    # ------------------------------------------------------------------
    # 6. Sort & truncate
    # ------------------------------------------------------------------
    scored.sort(key=lambda c: c.get("_score", 0.0), reverse=True)
    results = scored[:limit]

    # ------------------------------------------------------------------
    # 7. Build result
    # ------------------------------------------------------------------
    duration_ms = (time.perf_counter() - t0) * 1000.0

    return SemanticSearchResult(
        results=results,
        query=query,
        candidate_count=len(candidates),
        duration_ms=duration_ms,
        signals_used=signal_names,
        embeddings_enabled=embeddings_enabled,
    )
