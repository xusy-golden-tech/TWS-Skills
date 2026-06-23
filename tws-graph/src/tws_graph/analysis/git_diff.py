"""Git diff impact analyzer — identifies changed symbols and assesses risk.

Uses tws-graph's diff/snapshot capabilities for production mode, and direct
Store comparison for test/fallback mode.

Risk classification:
    | Impact Radius | Risk Level |
    |--------------|------------|
    | 0            | low        |
    | 1-5          | medium     |
    | 6-15         | high       |
    | > 15         | critical   |
"""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from tws_graph.store.interface import Store


class RiskLevel(str, Enum):
    """Risk level classification for changed symbols."""
    LOW = "low"           # No dependents
    MEDIUM = "medium"     # 1-5 dependents
    HIGH = "high"         # 6-15 dependents
    CRITICAL = "critical"  # > 15 dependents


@dataclass
class DiffImpact:
    """Impact assessment for a single changed symbol."""
    node_id: str
    node_name: str
    change_type: str       # "added" | "modified" | "removed"
    impact_radius: int     # number of affected nodes in the dependency graph
    risk_level: RiskLevel
    affected_files: list[str]
    affected_nodes: list[str]


# ---------------------------------------------------------------------------
# Risk classification helpers
# ---------------------------------------------------------------------------

def _classify_risk(impact_radius: int) -> RiskLevel:
    """Map impact_radius to RiskLevel per the defined thresholds."""
    if impact_radius == 0:
        return RiskLevel.LOW
    elif impact_radius <= 5:
        return RiskLevel.MEDIUM
    elif impact_radius <= 15:
        return RiskLevel.HIGH
    else:
        return RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Impact radius computation (BFS on incoming edges)
# ---------------------------------------------------------------------------

# Edge kinds that represent dependency (excludes 'contains' — container
# membership doesn't imply dependence, ported from CodeGraph issue #536 fix).
_IMPACT_EDGE_KINDS = ["calls", "references", "imports"]

_DEFAULT_MAX_DEPTH = 3


def _compute_impact_radius(
    store: Store,
    node_id: str,
    max_depth: int = _DEFAULT_MAX_DEPTH,
) -> dict:
    """Compute the impact radius and affected nodes for a given node.

    Performs BFS outward along incoming edges (callers). Excludes
    'contains' edges — container membership doesn't imply dependence.

    Args:
        store: The current Store (to query for impact).
        node_id: The ID of the changed node.
        max_depth: Maximum BFS depth (default 3).

    Returns:
        {"radius": int, "nodes": list[str], "files": list[str]}
            radius = number of impacted nodes (excluding self)
            nodes = node IDs in the impact radius
            files = file_paths of impacted nodes
    """
    visited: set[str] = set()
    affected_nodes: list[str] = []
    affected_files: set[str] = set()

    queue: deque = deque()
    queue.append((node_id, 0))

    while queue:
        current_id, depth = queue.popleft()

        # Don't count the changed node itself in the radius
        if current_id != node_id:
            visited.add(current_id)
            affected_nodes.append(current_id)
            node = store.get_node_by_id(current_id)
            if node:
                fp = node.get("file_path", "")
                if fp:
                    affected_files.add(fp)

        if depth >= max_depth:
            continue

        # Get incoming edges (who depends on this node)
        try:
            incoming = store.get_incoming_edges(current_id, _IMPACT_EDGE_KINDS)
        except Exception:
            incoming = []

        for edge in incoming:
            source_id = edge.get("source", "")
            if source_id and source_id not in visited and source_id != node_id:
                queue.append((source_id, depth + 1))

    return {
        "radius": len(affected_nodes),
        "nodes": affected_nodes,
        "files": list(affected_files) if affected_files else [],
    }


# ---------------------------------------------------------------------------
# GitDiffAnalyzer
# ---------------------------------------------------------------------------


class GitDiffAnalyzer:
    """Analyzes git diff through the tws-graph code symbol graph.

    Two modes:
    1. Production: Uses tws-graph snapshots + compare_snapshots() for diff,
       then GraphTraverser for impact radius.
    2. Fallback/Test: Direct Store comparison when baseline_store is provided.
    """

    def __init__(
        self,
        db_path: str = ".tws/codegraph/index.db",
        snapshots_dir: str = ".tws/codegraph/snapshots",
    ):
        self.db_path = db_path
        self.snapshots_dir = snapshots_dir

    def analyze(
        self,
        store: Store,
        baseline: str = "HEAD",
        snapshot: Optional[str] = None,
        baseline_store: Optional[Store] = None,
    ) -> list[DiffImpact]:
        """Compare current code with baseline and assess impact of changes.

        Args:
            store: The current Store (after state).
            baseline: Git baseline reference (default: "HEAD"). Used in
                production mode with snapshots.
            snapshot: Named tws-graph snapshot to compare against.
            baseline_store: Direct Store to compare against (for testing
                and fallback mode). When provided, skips file-based diff.

        Returns:
            List of DiffImpact, one per changed symbol.
        """
        if baseline_store is not None:
            return self._analyze_from_stores(baseline_store, store)

        # Production path: try to use compare_snapshots()
        return self._analyze_from_snapshots(store, baseline, snapshot)

    # ------------------------------------------------------------------
    # Direct Store comparison (test / fallback)
    # ------------------------------------------------------------------

    def _analyze_from_stores(
        self,
        baseline_store: Store,
        current_store: Store,
    ) -> list[DiffImpact]:
        """Compare two Stores directly and build DiffImpact list."""
        results: list[DiffImpact] = []

        # Build qualified_name → node mappings for both stores
        baseline_nodes: dict[str, dict] = {}
        for node in baseline_store.iter_all_nodes():
            qname = node.get("qualified_name", "")
            if qname:
                baseline_nodes[qname] = dict(node)

        current_nodes: dict[str, dict] = {}
        for node in current_store.iter_all_nodes():
            qname = node.get("qualified_name", "")
            if qname:
                current_nodes[qname] = dict(node)

        baseline_names = set(baseline_nodes.keys())
        current_names = set(current_nodes.keys())

        # Added symbols (in current but not in baseline)
        for qname in sorted(current_names - baseline_names):
            node = current_nodes[qname]
            nid = node.get("id", "")
            impact = _compute_impact_radius(current_store, nid)

            results.append(DiffImpact(
                node_id=nid,
                node_name=node.get("name", qname),
                change_type="added",
                impact_radius=impact["radius"],
                risk_level=_classify_risk(impact["radius"]),
                affected_files=self._collect_affected_files(node, impact),
                affected_nodes=impact["nodes"],
            ))

        # Removed symbols (in baseline but not in current)
        for qname in sorted(baseline_names - current_names):
            node = baseline_nodes[qname]
            nid = node.get("id", "")
            # For removed nodes, we can't compute impact on current store
            results.append(DiffImpact(
                node_id=nid,
                node_name=node.get("name", qname),
                change_type="removed",
                impact_radius=0,
                risk_level=RiskLevel.LOW,
                affected_files=[node.get("file_path", "")] if node.get("file_path") else [],
                affected_nodes=[],
            ))

        # Modified symbols (in both, signature changed)
        common_names = baseline_names & current_names
        for qname in sorted(common_names):
            before_sig = baseline_nodes[qname].get("signature")
            after_sig = current_nodes[qname].get("signature")
            if before_sig != after_sig:
                node = current_nodes[qname]
                nid = node.get("id", "")
                impact = _compute_impact_radius(current_store, nid)

                results.append(DiffImpact(
                    node_id=nid,
                    node_name=node.get("name", qname),
                    change_type="modified",
                    impact_radius=impact["radius"],
                    risk_level=_classify_risk(impact["radius"]),
                    affected_files=self._collect_affected_files(node, impact),
                    affected_nodes=impact["nodes"],
                ))

        return results

    # ------------------------------------------------------------------
    # Snapshot-based comparison (production)
    # ------------------------------------------------------------------

    def _analyze_from_snapshots(
        self,
        store: Store,
        baseline: str,
        snapshot: Optional[str],
    ) -> list[DiffImpact]:
        """Compare using tws-graph snapshots (production mode).

        Falls back to empty result if snapshot files are not available.
        """
        import os
        from tws_graph.diff import compare_snapshots, list_snapshots

        # Determine the "before" path
        if snapshot:
            before_path = os.path.join(self.snapshots_dir, f"index-{snapshot}.db")
            if not os.path.exists(before_path):
                # Try listing snapshots to help debug
                available = list_snapshots(self.snapshots_dir)
                raise FileNotFoundError(
                    f"Snapshot '{snapshot}' not found. "
                    f"Available snapshots: {available}"
                )
        elif baseline:
            # For git baseline, we need a snapshot of that state.
            # This requires the baseline to have been indexed.
            # Try snapshot named after the baseline.
            before_path = os.path.join(self.snapshots_dir, f"index-{baseline}.db")
            if not os.path.exists(before_path):
                # No snapshot available — return empty (caller should
                # use baseline_store for test mode, or create snapshots
                # for production mode)
                return []
        else:
            return []

        # Current state is in self.db_path
        after_path = self.db_path
        if not os.path.exists(after_path):
            return []

        try:
            report = compare_snapshots(before_path, after_path)
        except FileNotFoundError:
            return []

        results: list[DiffImpact] = []

        # Process added symbols
        for sym in report.added_symbols:
            impact = self._compute_impact_for_symbol(store, sym)
            results.append(impact)

        # Process signature-changed symbols
        for sym in report.signature_changed:
            impact = self._compute_impact_for_symbol(store, sym)
            impact.change_type = "modified"
            results.append(impact)

        # Process removed symbols
        for sym in report.removed_symbols:
            results.append(DiffImpact(
                node_id="",  # removed nodes don't have current store IDs
                node_name=sym.get("qualified_name", sym.get("name", "")),
                change_type="removed",
                impact_radius=0,
                risk_level=RiskLevel.LOW,
                affected_files=[sym.get("file_path", "")] if sym.get("file_path") else [],
                affected_nodes=[],
            ))

        return results

    def _compute_impact_for_symbol(
        self,
        store: Store,
        sym: dict,
    ) -> DiffImpact:
        """Compute DiffImpact for a symbol from snapshot diff."""
        qname = sym.get("qualified_name", "")
        if not qname:
            # Try to look up by name in the store
            return DiffImpact(
                node_id="",
                node_name=sym.get("name", ""),
                change_type="added",
                impact_radius=0,
                risk_level=RiskLevel.LOW,
                affected_files=[sym.get("file_path", "")] if sym.get("file_path") else [],
                affected_nodes=[],
            )

        # Try to find the node in the current store
        nid = store.search_by_def_index(qname)
        if not nid:
            return DiffImpact(
                node_id="",
                node_name=sym.get("name", qname),
                change_type="added",
                impact_radius=0,
                risk_level=RiskLevel.LOW,
                affected_files=[sym.get("file_path", "")] if sym.get("file_path") else [],
                affected_nodes=[],
            )

        impact = _compute_impact_radius(store, nid)
        node = store.get_node_by_id(nid)
        file_path = sym.get("file_path", "")
        affected_files = self._collect_affected_files(
            {"file_path": file_path}, impact
        )

        return DiffImpact(
            node_id=nid,
            node_name=sym.get("name", qname),
            change_type="added",  # caller may override to "modified"
            impact_radius=impact["radius"],
            risk_level=_classify_risk(impact["radius"]),
            affected_files=affected_files,
            affected_nodes=impact["nodes"],
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_affected_files(
        node: dict,
        impact: dict,
    ) -> list[str]:
        """Collect file paths from the changed node and impacted nodes."""
        files: set[str] = set()
        own_file = node.get("file_path", "")
        if own_file:
            files.add(own_file)
        for f in impact.get("files", []):
            if f:
                files.add(f)
        return sorted(files)
