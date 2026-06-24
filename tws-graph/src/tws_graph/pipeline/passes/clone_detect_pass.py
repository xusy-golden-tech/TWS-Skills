"""CloneDetectionPass — find near-duplicate functions via MinHash+LSH.

Runs the CloneDetector algorithm (MinHash + LSH) on all function/method
nodes to find similar code pairs and writes ``similar_to`` edges into the
Store.  This pass is expensive and is only registered when the ``--deep``
flag is passed to ``tws-graph index``.

Design: P4/P8 similarity analysis, gated behind --deep to avoid slowing
down normal incremental index runs.
"""

from __future__ import annotations

import json
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.edges.kind import EdgeKind

logger = logging.getLogger(__name__)


class CloneDetectionPass(Pass):
    """Find near-duplicate function/method pairs using MinHash + LSH.

    Depends on ``node-insert`` because function/method nodes must already
    exist in the Store for the detector to iterate over them.

    Edges produced:
    * ``similar_to`` — links two function/method nodes that are likely
      clones.  The Jaccard similarity score is stored in the edge
      ``properties`` column.

    Note:
        This pass is O(n^2) in the worst case (all-pairs comparison after
        LSH bucketing) and is disabled by default.  Use ``tws-graph index
        --deep`` to enable it.
    """

    name: str = "clone-detection"
    description: str = (
        "Find near-duplicate function/method pairs via MinHash+LSH "
        "(similar_to edges).  Expensive — gated behind --deep flag."
    )
    dependencies: list[str] = ["node-insert"]
    supports_incremental: bool = False  # only on full re-index

    def __init__(self, threshold: float = 0.8):
        self._threshold = threshold

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("CloneDetectionPass: ctx.store is None, skipping")
            ctx.metadata["similar_to_count"] = 0
            return ctx

        try:
            from tws_graph.graph.algorithms.similarity import CloneDetector
        except ImportError as exc:
            logger.warning("CloneDetectionPass: import failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["similar_to_count"] = 0
            return ctx

        detector = CloneDetector(threshold=self._threshold)
        try:
            result = detector.run(store)
        except Exception as exc:
            logger.warning("CloneDetectionPass: detection failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["similar_to_count"] = 0
            return ctx

        similar_pairs = result.data.get("similar_pairs", [])
        if not similar_pairs:
            ctx.metadata["similar_to_count"] = 0
            return ctx

        # Delete old similar_to edges
        try:
            store.delete_edges_by_kind(EdgeKind.SIMILAR_TO.value)
        except AttributeError:
            pass

        edge_dicts: list[dict] = []
        for pair in similar_pairs:
            node_a = pair.get("node_a", "")
            node_b = pair.get("node_b", "")
            sim = pair.get("similarity", 0.0)
            name_a = pair.get("name_a", "")
            name_b = pair.get("name_b", "")

            if not node_a or not node_b:
                continue

            edge_dicts.append({
                "source": node_a,
                "target": node_b,
                "kind": EdgeKind.SIMILAR_TO.value,
                "source_loc": "",
                "target_text": name_b,
                "provenance": "analysis",
                "properties": json.dumps({
                    "similarity": sim,
                    "name_a": name_a,
                    "name_b": name_b,
                }, ensure_ascii=False),
            })

        if edge_dicts:
            try:
                store.insert_edges(edge_dicts)
            except Exception as exc:
                logger.warning(
                    "CloneDetectionPass: insert_edges failed: %s", exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": str(exc),
                    "severity": "warning",
                })

        ctx.metadata["similar_to_count"] = len(edge_dicts)
        ctx.metadata["total_functions_checked"] = (
            result.data.get("total_functions_checked", 0)
        )
        logger.info(
            "CloneDetectionPass: %d similar_to edges inserted "
            "(checked %d functions)",
            len(edge_dicts),
            result.data.get("total_functions_checked", 0),
        )
        return ctx
