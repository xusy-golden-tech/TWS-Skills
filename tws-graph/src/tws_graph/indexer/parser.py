"""Generic tree-sitter parsing framework.

Extractors are auto-discovered via :mod:`registry` — no hardcoded
language dispatch.  Adding a new language only requires placing a
``BaseExtractor`` subclass in the ``extractors/`` package.
"""

from tree_sitter import Parser, Language
from tree_sitter_language_pack import get_language, get_parser  # type: ignore[import]

from .base import ExtractionResult, ExtractionContext
from .registry import get_extractor


def _get_language_obj(lang_name: str) -> Language:
    """Get a tree-sitter Language object by name."""
    return get_language(lang_name)


def _get_parser_for(lang_name: str) -> Parser:
    """Get a tree-sitter Parser configured for a language."""
    return get_parser(lang_name)


def extract_from_source(
    file_path: str,
    content: str,
    language: str,
) -> ExtractionResult:
    """Parse one source file and extract all symbols + edges.

    Uses the extractor registry to find the right extractor for the
    given language.  Errors during loading / parsing are captured in
    ``result.errors`` — individual file failure never crashes the
    whole index.
    """
    ctx = ExtractionContext(file_path, language)

    extractor = get_extractor(language)
    if extractor is None:
        ctx.result.errors.append({
            "message": f"Unsupported language: {language}",
            "file_path": file_path,
            "severity": "error",
        })
        return ctx.result

    try:
        lang_obj = _get_language_obj(language)
        parser_obj = _get_parser_for(language)
    except Exception as e:
        ctx.result.errors.append({
            "message": f"Failed to load language '{language}': {e}",
            "file_path": file_path,
            "severity": "error",
        })
        return ctx.result

    try:
        tree = parser_obj.parse(content)
    except Exception as e:
        ctx.result.errors.append({
            "message": f"Parse error: {e}",
            "file_path": file_path,
            "severity": "error",
        })
        return ctx.result

    source_bytes = content.encode("utf-8")
    extractor.extract(source_bytes, tree, ctx)
    return ctx.result
