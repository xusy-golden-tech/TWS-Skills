"""C++ extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class CppExtractor(BaseExtractor):
    extensions = [".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh"]
    tree_sitter_languages = ["cpp"]
    language_name = "cpp"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..cpp_extractor import visit_cpp

        result = visit_cpp(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
