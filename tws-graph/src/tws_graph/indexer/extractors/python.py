"""Python extractor — BaseExtractor subclass."""

from ..base import BaseExtractor, ExtractionContext


class PythonExtractor(BaseExtractor):
    extensions = [".py"]
    tree_sitter_languages = ["python"]
    language_name = "python"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..python_extractor import visit_python

        result = visit_python(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
