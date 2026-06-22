"""CrossFileResolvePass — resolve cross-file dangling edges.

Scans the Store for dangling edges (edges with target_text but no resolved
target) and attempts to resolve them against the current node index via:
1. Exact qualified-name lookup (store.search_by_def_index).
2. Fuzzy name search (store.fts_search) with exact-name filtering.

Resolved edges are updated in-place; unresolved/ambiguous edges are marked
via provenance update.
"""

from __future__ import annotations

import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)


class CrossFileResolvePass(Pass):
    """Resolve cross-file dangling edges by matching target_text to node definitions.

    Resolution strategy (three outcomes):
    1. **resolved**: exact match via def_index, or a single FTS match by name.
    2. **ambiguous**: multiple FTS matches share the same target name.
    3. **unresolved**: no match found in def_index or FTS.

    Resolved edges are updated via ``store.update_edge_target()`` with
    provenance="resolved". Ambiguous/unresolved edges are marked via
    ``store.update_edge_provenance()``.

    Results are recorded in ctx.resolve_stats and ctx.metadata["cross_file_resolved"].
    """

    name: str = "cross-file-resolve"
    description: str = (
        "Resolve cross-file dangling edges by matching target_text to node "
        "definitions using exact def_index lookup and fuzzy FTS search"
    )
    dependencies: list[str] = ["edge-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("CrossFileResolvePass: ctx.store is None, skipping")
            stats = ctx.resolve_stats
            ctx.metadata["cross_file_resolved"] = dict(stats)
            return ctx

        stats = ctx.resolve_stats

        # Get all dangling edges (edges with non-empty target_text but empty target)
        dangling = store.get_dangling_edges()
        if not dangling:
            ctx.metadata["cross_file_resolved"] = dict(stats)
            return ctx

        for edge in dangling:
            target_text = edge.get("target_text", "")
            edge_id = edge.get("id")
            if not target_text or edge_id is None:
                stats["unresolved"] += 1
                continue

            # Strategy 1: Exact lookup via def_index
            resolved_id = store.search_by_def_index(target_text)
            if resolved_id and store.get_node_by_id(resolved_id) is not None:
                store.update_edge_target(edge_id, resolved_id, provenance="resolved")
                stats["resolved"] += 1
                continue

            # Strategy 2: Fuzzy search via FTS, filter by exact name match
            # Extract the simple name from the qualified target_text
            simple_name = target_text.rsplit("::", 1)[-1] if "::" in target_text else target_text

            try:
                fts_results = store.fts_search(simple_name, limit=20)
            except Exception:
                # FTS may not be available or may fail
                store.update_edge_provenance(edge_id, "unresolved")
                stats["unresolved"] += 1
                continue

            # Filter: only keep results whose name matches exactly
            exact_matches = [
                r for r in fts_results
                if r.get("name") == simple_name
            ]

            if len(exact_matches) == 1:
                # Unique match — resolve
                store.update_edge_target(
                    edge_id, exact_matches[0]["id"], provenance="resolved"
                )
                stats["resolved"] += 1
            elif len(exact_matches) > 1:
                # Multiple matches — ambiguous
                store.update_edge_provenance(edge_id, "ambiguous")
                stats["ambiguous"] += 1
            else:
                # No match — remain unresolved
                store.update_edge_provenance(edge_id, "unresolved")
                stats["unresolved"] += 1

        ctx.metadata["cross_file_resolved"] = dict(stats)
        return ctx
