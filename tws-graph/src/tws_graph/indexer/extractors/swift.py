"""Swift extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class SwiftExtractor(BaseExtractor):
    extensions = [".swift"]
    tree_sitter_languages = ["swift"]
    language_name = "swift"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..swift_extractor import visit_swift

        result = visit_swift(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
