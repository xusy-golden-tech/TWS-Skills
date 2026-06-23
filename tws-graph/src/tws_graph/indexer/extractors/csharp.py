"""C# extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class CSharpExtractor(BaseExtractor):
    extensions = [".cs"]
    tree_sitter_languages = ["csharp"]
    language_name = "csharp"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..csharp_extractor import visit_csharp

        result = visit_csharp(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
