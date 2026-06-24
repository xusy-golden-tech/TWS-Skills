"""TestEdgeAnalysisPass — derive test<->source edges during indexing.

Runs the P9 TestEdgeAnalyzer after the core indexing pipeline has populated
symbol nodes and call/import edges.  Converts TestEdge dataclass objects to
edge dicts with kind ``test_edge`` and writes them into the Store so they
are available for queries immediately after ``tws-graph index``.

Design: This pass runs automatically as part of the index pipeline.
No manual ``tws-graph analyze --run test-edges`` needed.
"""

from __future__ import annotations

import json
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.edges.kind import EdgeKind

logger = logging.getLogger(__name__)


class TestEdgeAnalysisPass(Pass):
    """Derive test<->source associations from the indexed symbol graph.

    Runs TestEdgeAnalyzer on the complete Store and inserts the resulting
    test_edge edges so they are queryable immediately after indexing.

    Depends on ``edge-insert`` because call/import edges must already exist
    in the Store for strategies 2 (call_graph) and 3 (import_reference).

    Edges produced:
    * ``test_edge`` — links a test function/method/class to the source
      symbol it exercises, with confidence and derivation info stored in
      the edge ``properties`` column.
    """

    name: str = "test-edge-analysis"
    description: str = (
        "Derive test<->source associations (test_edge) from the indexed "
        "symbol graph — naming convention + call graph + import reference"
    )
    dependencies: list[str] = ["edge-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("TestEdgeAnalysisPass: ctx.store is None, skipping")
            ctx.metadata["test_edge_count"] = 0
            return ctx

        try:
            from tws_graph.analysis.test_edges import TestEdgeAnalyzer
        except ImportError as exc:
            logger.warning("TestEdgeAnalysisPass: import failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["test_edge_count"] = 0
            return ctx

        analyzer = TestEdgeAnalyzer()
        try:
            test_edges = analyzer.analyze(store)
        except Exception as exc:
            logger.warning("TestEdgeAnalysisPass: analysis failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["test_edge_count"] = 0
            return ctx

        if not test_edges:
            ctx.metadata["test_edge_count"] = 0
            return ctx

        # Delete old test_edge records so we don't accumulate stale data on
        # re-index.  The analyzer produces a complete set each run.
        try:
            store.delete_edges_by_kind(EdgeKind.TEST_EDGE.value)
        except AttributeError:
            # Fallback for backends that don't support delete_edges_by_kind
            pass

        edge_dicts: list[dict] = []
        for te in test_edges:
            try:
                edge_dicts.append({
                    "source": te.test_node_id,
                    "target": te.source_node_id,
                    "kind": EdgeKind.TEST_EDGE.value,
                    "source_loc": _format_loc(te.test_file_path,
                                               getattr(te, "test_line", 0)),
                    "target_text": te.source_name,
                    "provenance": "analysis",
                    "properties": json.dumps({
                        "confidence": te.confidence,
                        "derivation": te.derivation,
                    }, ensure_ascii=False),
                })
            except AttributeError:
                continue

        if edge_dicts:
            try:
                store.insert_edges(edge_dicts)
            except Exception as exc:
                logger.warning(
                    "TestEdgeAnalysisPass: insert_edges failed: %s", exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": str(exc),
                    "severity": "warning",
                })

        ctx.metadata["test_edge_count"] = len(edge_dicts)
        logger.info(
            "TestEdgeAnalysisPass: %d test_edge edges inserted",
            len(edge_dicts),
        )
        return ctx


def _format_loc(file_path: str, line: int) -> str:
    if line and line > 0:
        return f"{file_path}:{line}"
    return file_path
