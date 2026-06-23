"""Semantic search module for the TWS Code Graph.

P8: 11-signal fusion ranking layer built on top of FTS5.
"""

from .semantic import SemanticSearchResult, semantic_query  # noqa: F401
from .signals import (  # noqa: F401
    Signal,
    ScoredResult,
    StoreContext,
    BM25Signal,
    QualifiedNameMatchSignal,
    DocstringMatchSignal,
    ASTSimilaritySignal,
    APISignatureSimilaritySignal,
    CloneSimilaritySignal,
    ModuleProximitySignal,
    GraphDiffusionSignal,
    CallerCalleeProximitySignal,
    GraphCentralitySignal,
    DataFlowConnectionSignal,
)

__all__ = [
    "SemanticSearchResult",
    "semantic_query",
    "Signal",
    "ScoredResult",
    "StoreContext",
    "BM25Signal",
    "QualifiedNameMatchSignal",
    "DocstringMatchSignal",
    "ASTSimilaritySignal",
    "APISignatureSimilaritySignal",
    "CloneSimilaritySignal",
    "ModuleProximitySignal",
    "GraphDiffusionSignal",
    "CallerCalleeProximitySignal",
    "GraphCentralitySignal",
    "DataFlowConnectionSignal",
]
