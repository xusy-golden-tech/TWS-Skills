"""TypeScript / TSX extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class TypeScriptExtractor(BaseExtractor):
    extensions = [".ts", ".tsx", ".js", ".jsx", ".mjs"]
    tree_sitter_languages = ["typescript", "tsx"]
    language_name = "typescript"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..ts_extractor import visit_typescript

        result = visit_typescript(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
