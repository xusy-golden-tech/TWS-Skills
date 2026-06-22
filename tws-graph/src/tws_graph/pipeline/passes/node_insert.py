"""NodeInsertPass — write extracted symbol definitions as nodes into the Store.

For each file with parsed defs, deletes existing nodes for that file and
inserts the new definitions. All writes go through the Store interface only.
"""

from __future__ import annotations

import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)


class NodeInsertPass(Pass):
    """Insert extracted symbol definitions as nodes into the Store.

    For each file in ctx.parsed_results:
    1. Read the ``defs`` list.
    2. Delete all existing nodes for that file via ``store.delete_nodes_by_file()``.
    3. Batch-insert new nodes via ``store.insert_nodes()``.

    All data operations go through the Store interface — no direct SQL access.
    """

    name: str = "node-insert"
    description: str = (
        "Write extracted symbol definitions as nodes into the Store, "
        "replacing any existing nodes for the same file"
    )
    dependencies: list[str] = ["parse-extract"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("NodeInsertPass: ctx.store is None, skipping")
            ctx.metadata["node_count"] = 0
            return ctx

        total_nodes: int = 0

        for file_path, parsed in ctx.parsed_results.items():
            defs = parsed.get("defs", [])
            if not defs:
                continue

            # Remove existing nodes for this file (replace semantics)
            store.delete_nodes_by_file(file_path)

            # Insert all defs as new nodes
            store.insert_nodes(defs)
            total_nodes += len(defs)

        ctx.metadata["node_count"] = total_nodes
        return ctx
