"""Zig extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class ZigExtractor(BaseExtractor):
    extensions = [".zig"]
    tree_sitter_languages = ["zig"]
    language_name = "zig"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..zig_extractor import visit_zig

        result = visit_zig(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
