"""Rust extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class RustExtractor(BaseExtractor):
    extensions = [".rs"]
    tree_sitter_languages = ["rust"]
    language_name = "rust"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..rust_extractor import visit_rust

        result = visit_rust(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
