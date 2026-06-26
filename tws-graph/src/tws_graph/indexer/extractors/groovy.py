"""Groovy extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class GroovyExtractor(BaseExtractor):
    extensions = [".groovy"]
    tree_sitter_languages = ["groovy"]
    language_name = "groovy"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..groovy_extractor import visit_groovy

        result = visit_groovy(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
