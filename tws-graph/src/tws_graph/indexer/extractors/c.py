"""C extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class CExtractor(BaseExtractor):
    extensions = [".c", ".h"]
    tree_sitter_languages = ["c"]
    language_name = "c"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..c_extractor import visit_c

        result = visit_c(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
