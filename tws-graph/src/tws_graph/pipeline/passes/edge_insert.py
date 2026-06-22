"""EdgeInsertPass — write call and reference edges into the Store.

For each file's parsed edges (calls + refs), attempts to resolve the target
node. Resolved edges are inserted directly; unresolved edges are inserted as
dangling edges (empty target) for later resolution by CrossFileResolvePass.
"""

from __future__ import annotations

import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)


class EdgeInsertPass(Pass):
    """Insert call and reference edges into the Store.

    For each file in ctx.parsed_results:
    1. Collect ``calls`` and ``refs`` edge lists.
    2. For each edge, try to resolve the target:
       a. If ``target`` is non-empty and the node exists → insert directly.
       b. If ``target_text`` is set, try ``store.search_by_def_index()`` for an
          exact qualified-name match → if found, set target and insert.
       c. Otherwise → insert as a dangling edge (target="" with target_text preserved,
          provenance="unresolved") for later cross-file resolution.
    3. Record edge_count and unresolved_count in metadata.

    All data operations go through the Store interface — no direct SQL access.
    """

    name: str = "edge-insert"
    description: str = (
        "Insert call and reference edges into the Store, resolving targets "
        "within the same file and deferring cross-file resolution"
    )
    dependencies: list[str] = ["node-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("EdgeInsertPass: ctx.store is None, skipping")
            ctx.metadata["edge_count"] = 0
            ctx.metadata["unresolved_count"] = 0
            return ctx

        total_edges: int = 0
        total_unresolved: int = 0

        for file_path, parsed in ctx.parsed_results.items():
            calls = parsed.get("calls", [])
            refs = parsed.get("refs", [])
            all_edges = calls + refs

            for edge in all_edges:
                target = edge.get("target", "")
                target_text = edge.get("target_text", "")
                kind = edge.get("kind", "calls")
                source_id = edge.get("source", "")

                if not source_id:
                    continue

                # Case 1: Target node already exists in store
                if target and store.get_node_by_id(target) is not None:
                    edge_copy = dict(edge)
                    store.insert_edge(edge_copy)
                    total_edges += 1
                    continue

                # Case 2: Try to resolve via target_text using def_index
                if target_text:
                    resolved_id = store.search_by_def_index(target_text)
                    if resolved_id and store.get_node_by_id(resolved_id) is not None:
                        edge_copy = dict(edge)
                        edge_copy["target"] = resolved_id
                        edge_copy["provenance"] = "resolved"
                        store.insert_edge(edge_copy)
                        total_edges += 1
                        continue

                    # Case 3: Cannot resolve — insert as dangling edge
                    edge_copy = dict(edge)
                    edge_copy["target"] = ""
                    edge_copy["target_text"] = target_text
                    edge_copy["provenance"] = "unresolved"
                    store.insert_edge(edge_copy)
                    total_edges += 1
                    total_unresolved += 1
                    continue

                # Case 4: No target and no target_text — insert as-is
                edge_copy = dict(edge)
                store.insert_edge(edge_copy)
                total_edges += 1

        ctx.metadata["edge_count"] = total_edges
        ctx.metadata["unresolved_count"] = total_unresolved
        return ctx
