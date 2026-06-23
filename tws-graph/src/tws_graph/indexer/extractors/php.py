"""PHP extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class PhpExtractor(BaseExtractor):
    extensions = [".php"]
    tree_sitter_languages = ["php"]
    language_name = "php"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..php_extractor import visit_php

        result = visit_php(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
