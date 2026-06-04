"""Java extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class JavaExtractor(BaseExtractor):
    extensions = [".java"]
    tree_sitter_languages = ["java"]
    language_name = "java"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..java_extractor import visit_java

        result = visit_java(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
