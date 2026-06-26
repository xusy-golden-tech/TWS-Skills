"""Nix extractor — BaseExtractor subclass."""
from ..base import BaseExtractor, ExtractionContext


class NixExtractor(BaseExtractor):
    extensions = [".nix"]
    tree_sitter_languages = ["nix"]
    language_name = "nix"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..nix_extractor import visit_nix

        result = visit_nix(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
