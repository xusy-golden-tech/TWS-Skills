"""Kotlin extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class KotlinExtractor(BaseExtractor):
    extensions = [".kt", ".kts"]
    tree_sitter_languages = ["kotlin"]
    language_name = "kotlin"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..kotlin_extractor import visit_kotlin

        result = visit_kotlin(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
