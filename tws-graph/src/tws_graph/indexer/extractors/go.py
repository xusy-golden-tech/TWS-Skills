"""Go extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class GoExtractor(BaseExtractor):
    extensions = [".go"]
    tree_sitter_languages = ["go"]
    language_name = "go"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..go_extractor import visit_go

        result = visit_go(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
