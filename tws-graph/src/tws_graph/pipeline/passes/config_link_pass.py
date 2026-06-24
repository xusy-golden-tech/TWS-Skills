"""ConfigLinkAnalysisPass — link code constants to config file keys.

Runs the P9 ConfigLinkAnalyzer after the core indexing pipeline has populated
symbol nodes.  Converts ConfigLink dataclass objects to edge dicts with kind
``config_link`` and writes them into the Store so they are available for
queries immediately after ``tws-graph index``.

Config links point from code constants to config-file keys.  Because config
keys are not indexed graph nodes, the edge target is set to empty string
and the link metadata (config_file, config_key, confidence, derivation) is
stored in the edge ``properties`` JSON column.

Design: This pass runs automatically as part of the index pipeline.
No manual ``tws-graph analyze --run config-links`` needed.
"""

from __future__ import annotations

import json
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.edges.kind import EdgeKind

logger = logging.getLogger(__name__)


class ConfigLinkAnalysisPass(Pass):
    """Scan project config files and link constants to config keys.

    Runs ConfigLinkAnalyzer on the complete Store + project root and inserts
    the resulting config_link edges so they are queryable immediately after
    indexing.

    Depends on ``node-insert`` because constant/variable nodes must already
    exist in the Store for the analyzer to find them.

    Edges produced:
    * ``config_link`` — associates a code-level constant (source) with a
      config file key (target_text).  Metadata (config_file, config_key,
      confidence, derivation) is stored in the edge ``properties`` column.
    """

    name: str = "config-link-analysis"
    description: str = (
        "Scan project config files and link constants to config keys "
        "(config_link) — exact / prefix / contains match"
    )
    dependencies: list[str] = ["node-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("ConfigLinkAnalysisPass: ctx.store is None, skipping")
            ctx.metadata["config_link_count"] = 0
            return ctx

        root_dir = ctx.root_dir
        if not root_dir:
            logger.warning(
                "ConfigLinkAnalysisPass: ctx.root_dir is empty, skipping"
            )
            ctx.errors.append({
                "pass": self.name,
                "error": "root_dir is empty, cannot scan config files",
                "severity": "warning",
            })
            ctx.metadata["config_link_count"] = 0
            return ctx

        try:
            from tws_graph.analysis.config_links import ConfigLinkAnalyzer
        except ImportError as exc:
            logger.warning("ConfigLinkAnalysisPass: import failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["config_link_count"] = 0
            return ctx

        analyzer = ConfigLinkAnalyzer()
        try:
            links = analyzer.analyze(store, project_root=root_dir)
        except Exception as exc:
            logger.warning("ConfigLinkAnalysisPass: analysis failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["config_link_count"] = 0
            return ctx

        if not links:
            ctx.metadata["config_link_count"] = 0
            return ctx

        # Delete old config_link records so we don't accumulate stale data.
        try:
            store.delete_edges_by_kind(EdgeKind.CONFIG_LINK.value)
        except AttributeError:
            pass

        edge_dicts: list[dict] = []
        for link in links:
            try:
                edge_dicts.append({
                    "source": link.node_id,
                    "target": "",
                    "kind": EdgeKind.CONFIG_LINK.value,
                    "source_loc": _format_loc(link.file_path,
                                               getattr(link, "line", 0)),
                    "target_text": link.config_key,
                    "provenance": "analysis",
                    "properties": json.dumps({
                        "config_file": link.config_file,
                        "config_key": link.config_key,
                        "confidence": link.confidence,
                        "derivation": link.derivation,
                    }, ensure_ascii=False),
                })
            except AttributeError:
                continue

        if edge_dicts:
            try:
                store.insert_edges(edge_dicts)
            except Exception as exc:
                logger.warning(
                    "ConfigLinkAnalysisPass: insert_edges failed: %s", exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": str(exc),
                    "severity": "warning",
                })

        ctx.metadata["config_link_count"] = len(edge_dicts)
        logger.info(
            "ConfigLinkAnalysisPass: %d config_link edges inserted",
            len(edge_dicts),
        )
        return ctx


def _format_loc(file_path: str, line: int) -> str:
    if line and line > 0:
        return f"{file_path}:{line}"
    return file_path
