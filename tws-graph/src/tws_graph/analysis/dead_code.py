"""Dead code detection via degree-based analysis.

Detects functions and methods that have no incoming ``calls`` or ``references``
edges and are not marked as known entry points.

Design: P9 Analysis Suite — Dead Code Detector.

Known limitations:
    - Does NOT detect mutual-recursion dead-code clusters (SCC-based detection).
      Two functions that call each other but have no external incoming edges
      will each have in_degree >= 1 and therefore pass this simple check.
      Future work may add a strongly-connected-component (SCC) pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tws_graph.store.interface import Store

# Edge kinds that count toward "being used" (i.e. prevent dead-code classification).
_RELEVANT_EDGE_KINDS = ["calls", "references"]

# Node kinds to inspect.
_TARGET_NODE_KINDS = ["function", "method"]


@dataclass
class DeadCodeCandidate:
    """A code symbol that appears to be unused.

    Attributes:
        node_id: Unique node identifier in the Store.
        qualified_name: Fully qualified name (e.g. ``src/foo.py::function.bar``).
        kind: Symbol kind (``function`` or ``method``).
        file_path: Project-relative file path.
        language: Source language (``python``, ``typescript``, etc.).
        in_degree: Number of incoming ``calls`` + ``references`` edges.
        out_degree: Number of outgoing ``calls`` + ``references`` edges.
        is_entry_point: Whether this node is a known application entry point.
        centrality: Optional PageRank centrality score (reserved for future use).
    """

    node_id: str
    qualified_name: str
    kind: str
    file_path: str
    language: str
    in_degree: int = 0
    out_degree: int = 0
    is_entry_point: bool = False
    centrality: Optional[float] = None


class DeadCodeDetector:
    """Detect unused code via simple degree-based analysis.

    A function or method is considered **dead code** when:
        1. It has zero incoming ``calls`` **and** zero incoming ``references`` edges, AND
        2. It is **not** listed in the *entry_points* set.

    Usage::

        store = MemoryStore()
        # ... populate store ...
        detector = DeadCodeDetector()
        candidates = detector.detect(store, entry_points={"main", "init"})
        for c in candidates:
            print(f"Dead: {c.qualified_name} (in_degree={c.in_degree})")
    """

    def detect(
        self,
        store: Store,
        entry_points: Optional[set[str]] = None,
        use_centrality: bool = False,
    ) -> list[DeadCodeCandidate]:
        """Run dead-code detection over the given *store*.

        Args:
            store: A graph Store with indexed nodes and edges.
            entry_points: Set of ``node_id`` strings that are known application
                entry points.  Nodes in this set are **never** flagged as dead
                code, even if they have zero incoming edges.
            use_centrality: Reserved for future PageRank-enhanced detection.
                Currently accepted but ignored.

        Returns:
            List of ``DeadCodeCandidate`` objects, one per dead-code node.
        """
        if entry_points is None:
            entry_points: set[str] = set()

        candidates: list[DeadCodeCandidate] = []

        for kind in _TARGET_NODE_KINDS:
            for node in store.iter_nodes_by_kind(kind):
                node_id = node["id"]

                incoming = store.get_incoming_edges(node_id, _RELEVANT_EDGE_KINDS)
                outgoing = store.get_outgoing_edges(node_id, _RELEVANT_EDGE_KINDS)

                in_degree = len(incoming)
                out_degree = len(outgoing)
                is_entry = node_id in entry_points

                # Only flag when truly unreachable AND not a known entry point.
                if in_degree == 0 and not is_entry:
                    candidates.append(
                        DeadCodeCandidate(
                            node_id=node_id,
                            qualified_name=node["qualified_name"],
                            kind=node["kind"],
                            file_path=node["file_path"],
                            language=node.get("language", ""),
                            in_degree=in_degree,
                            out_degree=out_degree,
                            is_entry_point=is_entry,
                            centrality=None,
                        )
                    )

        return candidates
