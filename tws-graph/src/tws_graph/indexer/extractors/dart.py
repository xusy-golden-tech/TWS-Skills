"""Dart extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class DartExtractor(BaseExtractor):
    extensions = [".dart"]
    tree_sitter_languages = ["dart"]
    language_name = "dart"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..dart_extractor import visit_dart

        result = visit_dart(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
