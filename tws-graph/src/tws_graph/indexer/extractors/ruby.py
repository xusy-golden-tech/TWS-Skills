"""Ruby extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class RubyExtractor(BaseExtractor):
    extensions = [".rb"]
    tree_sitter_languages = ["ruby"]
    language_name = "ruby"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..ruby_extractor import visit_ruby

        result = visit_ruby(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
