"""DataFlowPass — extract data flow edges from source files.

Phase 3 of P7 Data Flow Analysis.  After ParseExtractPass, NodeInsertPass,
and EdgeInsertPass have populated the Store with symbol definitions,
DataFlowPass adds data-flow edges (reads, writes, throws, data_flows).

For each stale file determined by the InvalidationTracker:
1. Query the Store for function/method nodes belonging to that file.
2. Parse the file with tree-sitter.
3. Run VariableUsageExtractor → reads/writes/throws edges.
4. Run DataFlowExtractor → data_flows edges (call arg→param mapping).
5. Insert all edges into the Store.
6. Mark the file as valid in the tracker.

Design: .tws/sessions/design-p7-dataflow.md
"""

from __future__ import annotations

import logging
import os

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.edges.kind import EdgeKind

logger = logging.getLogger(__name__)


class DataFlowPass(Pass):
    """Extract data flow edges (reads, writes, throws, data_flows) from source files.

    Depends on ``edge-insert`` because symbol nodes must already exist in the
    Store before we can attach data-flow edges to them.  Uses the
    InvalidationTracker to avoid re-analysing unchanged files (supports
    incremental mode).

    Edges produced:
    * ``reads`` / ``writes`` / ``throws`` — per-function variable usage
      (VariableUsageExtractor).
    * ``data_flows`` — intra-file call-site arg→param mappings
      (DataFlowExtractor).
    """

    name: str = "dataflow"
    description: str = (
        "Extract data flow edges (reads, writes, throws, data_flows) "
        "from source files"
    )
    dependencies: list[str] = ["edge-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("DataFlowPass: ctx.store is None, skipping")
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        # B6: guard against empty root_dir with non-empty files
        if not ctx.root_dir and ctx.files:
            logger.warning(
                "DataFlowPass: ctx.root_dir is empty but ctx.files is not, skipping"
            )
            ctx.errors.append({
                "pass": self.name,
                "error": "root_dir is empty, cannot resolve file paths",
                "severity": "warning",
            })
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        # 1. Get tracker DB path from metadata
        tracker_db_path = ctx.metadata.get(
            "tracker_db_path", ".tws/codegraph/analysis.db"
        )

        # 2. Create InvalidationTracker and register dataflow analyzer
        try:
            from tws_graph.analysis.invalidation import (
                InvalidationTracker,
                AnalyzerRegistration,
            )

            tracker = InvalidationTracker(db_path=tracker_db_path)
        except Exception as exc:
            logger.warning("Failed to create InvalidationTracker: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        try:
            tracker.register(
                AnalyzerRegistration(
                    analyzer_name="dataflow",
                    file_patterns=["**/*.py", "**/*.ts", "**/*.tsx"],
                    node_kinds=["function", "method"],
                    version=1,
                )
            )
        except Exception as exc:
            logger.warning("Failed to register dataflow analyzer: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            try:
                tracker.close()
            except Exception:
                pass
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        # 3. Check which files need re-analysis
        try:
            stale_files = tracker.check_invalidation(store)
        except Exception as exc:
            logger.warning("check_invalidation failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            try:
                tracker.close()
            except Exception:
                pass
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        # 4. Extract file list for dataflow analyzer
        files_to_analyze = stale_files.get("dataflow", [])
        if not files_to_analyze:
            logger.debug("DataFlowPass: no stale files to analyze")
            try:
                tracker.close()
            except Exception:
                pass
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        # 5. Lazy-load analysis dependencies
        try:
            from tree_sitter_language_pack import get_parser
            from tws_graph.indexer.extractors.usage import VariableUsageExtractor
            from tws_graph.indexer.extractors.dataflow import DataFlowExtractor
        except ImportError as exc:
            logger.warning("Failed to import dataflow analysis modules: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "error",
            })
            try:
                tracker.close()
            except Exception:
                pass
            ctx.metadata["dataflow_edges"] = 0
            ctx.metadata["dataflow_files"] = 0
            return ctx

        total_edges: int = 0
        files_analyzed: int = 0

        for file_path in files_to_analyze:
            # --- 5a. Query Store for function/method nodes for this file ---
            func_node_ids: dict[str, str] = {}
            try:
                for node in store.iter_nodes_by_file(file_path):
                    if node.get("kind") in ("function", "method"):
                        qname = node.get("qualified_name", "")
                        nid = node.get("id", "")
                        if qname and nid:
                            func_node_ids[qname] = nid
            except Exception as exc:
                logger.warning(
                    "Failed to query nodes for %s: %s", file_path, exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": f"Node query failed for {file_path}: {exc}",
                    "severity": "warning",
                    "file_path": file_path,
                })
                continue

            if not func_node_ids:
                logger.debug(
                    "No function/method nodes for %s, skipping", file_path
                )
                continue

            # --- 5b. Read file content as bytes ---
            full_path = os.path.join(ctx.root_dir, file_path)
            try:
                with open(full_path, "rb") as fh:
                    source_bytes = fh.read()
            except OSError as exc:
                logger.warning("Failed to read %s: %s", file_path, exc)
                ctx.errors.append({
                    "pass": self.name,
                    "error": f"Read error for {file_path}: {exc}",
                    "severity": "warning",
                    "file_path": file_path,
                })
                continue

            # --- 5c. Detect language from file extension ---
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".py":
                language = "python"
            elif ext in (".ts", ".tsx"):
                language = "typescript"
            else:
                logger.debug(
                    "Skipping %s: extension %s not Python/TypeScript",
                    file_path, ext,
                )
                continue

            # --- 5d. Parse with tree-sitter ---
            try:
                parser = get_parser(language)
                tree = parser.parse(source_bytes.decode("utf-8", errors="replace"))
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", file_path, exc)
                ctx.errors.append({
                    "pass": self.name,
                    "error": f"Parse error for {file_path}: {exc}",
                    "severity": "warning",
                    "file_path": file_path,
                })
                continue

            # --- 5e & 5f. Extract edges ---
            all_edges: list[dict] = []

            # 5e. VariableUsageExtractor → reads / writes / throws
            try:
                usage_extractor = VariableUsageExtractor()
                usage_edges = usage_extractor.extract(
                    source_bytes, tree, func_node_ids, file_path, language
                )
                all_edges.extend(usage_edges)
            except Exception as exc:
                logger.warning(
                    "VariableUsageExtractor failed for %s: %s", file_path, exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": f"VariableUsageExtractor failed for {file_path}: {exc}",
                    "severity": "warning",
                    "file_path": file_path,
                })

            # 5f. DataFlowExtractor → data_flows
            try:
                df_extractor = DataFlowExtractor()
                df_edges = df_extractor.extract(
                    source_bytes, tree, func_node_ids, file_path, language
                )
                all_edges.extend(df_edges)
            except Exception as exc:
                logger.warning(
                    "DataFlowExtractor failed for %s: %s", file_path, exc
                )
                ctx.errors.append({
                    "pass": self.name,
                    "error": f"DataFlowExtractor failed for {file_path}: {exc}",
                    "severity": "warning",
                    "file_path": file_path,
                })

            # --- 5g. Insert edges into Store ---
            if all_edges:
                try:
                    store.insert_edges(all_edges)
                    total_edges += len(all_edges)
                except Exception as exc:
                    logger.warning(
                        "Failed to insert edges for %s: %s", file_path, exc
                    )
                    ctx.errors.append({
                        "pass": self.name,
                        "error": f"Edge insert error for {file_path}: {exc}",
                        "severity": "warning",
                        "file_path": file_path,
                    })
                    continue

            # --- 5h. Mark file as valid in tracker ---
            try:
                tracker.mark_valid("dataflow", [file_path])
            except Exception as exc:
                logger.warning(
                    "Failed to mark_valid for %s: %s", file_path, exc
                )

            files_analyzed += 1

        # 6. Record stats in metadata
        ctx.metadata["dataflow_edges"] = total_edges
        ctx.metadata["dataflow_files"] = files_analyzed

        try:
            tracker.close()
        except Exception:
            pass

        return ctx
