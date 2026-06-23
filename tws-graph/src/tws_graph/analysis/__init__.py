"""Analysis module — invalidation tracking, entry point detection, and dead code detection."""

from .dead_code import DeadCodeCandidate, DeadCodeDetector
from .entry_point import EntryPointDetector, EntryPointResult
from .invalidation import AnalyzerRegistration, ConsistencyReport, InvalidationTracker

__all__ = [
    "AnalyzerRegistration",
    "ConsistencyReport",
    "DeadCodeCandidate",
    "DeadCodeDetector",
    "EntryPointDetector",
    "EntryPointResult",
    "InvalidationTracker",
]
