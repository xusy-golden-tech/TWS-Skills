"""Graph algorithm abstract base class and result container.

All graph algorithms (similarity, community, centrality, cycle detection)
inherit from GraphAlgorithm and return AlgorithmResult.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AlgorithmResult:
    """Execution result for a graph algorithm.

    Carries the algorithm name, success flag, result data, error list,
    and wall-clock duration. All fields have sensible defaults so callers
    only need to supply *algorithm*.
    """

    algorithm: str
    success: bool = True
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class GraphAlgorithm(ABC):
    """Abstract interface for graph algorithms.

    Every algorithm must expose a human-readable *name*, a short
    *description*, and a *run* method that accepts a Store instance
    and returns an AlgorithmResult.

    Subclasses are free to add their own __init__ parameters (e.g.
    thresholds, max iterations), but the public contract is limited to
    the three abstract members below.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable algorithm name (e.g. 'minhash-similarity')."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Short description of what the algorithm does."""
        ...

    @abstractmethod
    def run(self, store: "Store") -> AlgorithmResult:
        """Execute the algorithm against *store*.

        Args:
            store: A Store implementation providing graph data access.

        Returns:
            AlgorithmResult with data, success flag, and optional errors.
        """
        ...
