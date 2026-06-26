"""CMake extractor — BaseExtractor subclass for CMake build files.

Handles .cmake, .cmake.in extensions, and CMakeLists.txt filename patterns.
"""

from ..base import BaseExtractor, ExtractionContext


class CMakeExtractor(BaseExtractor):
    extensions = [".cmake", ".cmake.in", "CMakeLists.txt"]
    tree_sitter_languages = ["cmake"]
    language_name = "cmake"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        from ..cmake_extractor import visit_cmake

        result = visit_cmake(ctx.file_path, source.decode("utf-8", errors="replace"), tree)

        ctx.result.nodes.extend(result.nodes)
        ctx.result.edges.extend(result.edges)
        ctx.result.errors.extend(result.errors)
