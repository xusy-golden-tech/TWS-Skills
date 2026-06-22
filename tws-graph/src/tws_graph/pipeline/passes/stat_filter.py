"""StatFilterPass — mtime/size-based file filtering for incremental indexing.

Filters a file list by comparing os.stat(mtime, size) against the Store's
existing file records. Only changed or new files pass through.
"""

from __future__ import annotations

import os
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)


class StatFilterPass(Pass):
    """Filter files by stat (mtime/size), keeping only changed files.

    Reads ctx.files, compares each file's mtime and size against the Store's
    file records. Files with unchanged mtime AND unchanged size are removed
    from ctx.files.

    - force=True: all files pass through (no filtering).
    - store=None: all files pass through (no comparison data available).
    - File stat failure (OSError): file is retained (downstream handles error).
    """

    name: str = "stat-filter"
    description: str = (
        "Filter files by stat (mtime/size), keeping only files that have "
        "changed since the last indexing run"
    )
    dependencies: list[str] = []
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.force:
            ctx.metadata["filtered_out"] = 0
            return ctx

        store = ctx.store
        if store is None:
            ctx.metadata["filtered_out"] = 0
            return ctx

        filtered: list[str] = []
        filtered_out: int = 0

        for file_path in ctx.files:
            full_path = os.path.join(ctx.root_dir, file_path)

            # Stat the file on disk
            try:
                stat = os.stat(full_path)
            except OSError:
                # File may have been deleted since scan; retain so
                # downstream passes can handle the error (or delete the record)
                filtered.append(file_path)
                continue

            # Look up existing record in Store
            file_record = store.get_file(file_path)
            if file_record is None:
                # New file — never indexed before
                filtered.append(file_path)
                continue

            stored_mtime = file_record.get("modified_at", 0)
            stored_size = file_record.get("size", 0)

            if (int(stat.st_mtime) != stored_mtime) or (stat.st_size != stored_size):
                filtered.append(file_path)
            else:
                filtered_out += 1

        ctx.files = filtered
        ctx.metadata["filtered_out"] = filtered_out
        return ctx
