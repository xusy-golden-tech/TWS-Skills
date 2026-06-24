"""Generic tree-sitter parsing framework.

Extractors are auto-discovered via :mod:`registry` — no hardcoded
language dispatch.  Adding a new language only requires placing a
``BaseExtractor`` subclass in the ``extractors/`` package.
"""

from tree_sitter import Parser, Language
from tree_sitter_language_pack import get_language, get_parser  # type: ignore[import]

from .base import ExtractionResult, ExtractionContext
from .registry import get_extractor

# Languages that support variable-usage / dataflow analysis
_DATAFLOW_LANGUAGES = frozenset({"python", "typescript", "tsx", "java"})


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


def extract_full(
    file_path: str,
    content: str,
    language: str,
) -> ExtractionResult:
    """Parse one source file and extract structural + dataflow symbols/edges.

    Compared to ``extract_from_source``, this also runs variable-usage and
    data-flow analysis on the same parse tree, avoiding a costly re-parse
    in the post-processing phase.

    Only applicable to languages in ``_DATAFLOW_LANGUAGES``; for other
    languages, falls back to structural-only extraction.
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

    # Phase 1: Structural extraction (function/class/method definitions, calls, etc.)
    try:
        extractor.extract(source_bytes, tree, ctx)
    except Exception as e:
        ctx.result.errors.append({
            "message": f"Extraction error: {e}",
            "file_path": file_path,
            "severity": "error",
        })
        return ctx.result

    # Phase 2: Dataflow extraction (reads/writes/throws/data_flows) on the SAME tree
    if language not in _DATAFLOW_LANGUAGES or not ctx.result.nodes:
        return ctx.result

    func_node_ids: dict[str, str] = {}
    for node in ctx.result.nodes:
        if node.get("kind") in ("function", "method"):
            qname = node.get("qualified_name", "")
            nid = node.get("id", "")
            if qname and nid:
                func_node_ids[qname] = nid

    if not func_node_ids:
        return ctx.result

    # Variable usage extraction → reads, writes, throws
    try:
        from .extractors.usage import VariableUsageExtractor
        usage_extractor = VariableUsageExtractor()
        usage_edges = usage_extractor.extract(
            source_bytes, tree, func_node_ids, file_path, language,
        )
        ctx.result.edges.extend(usage_edges)
    except Exception:
        pass

    # Data flow extraction → data_flows
    try:
        from .extractors.dataflow import DataFlowExtractor
        df_extractor = DataFlowExtractor()
        df_edges = df_extractor.extract(
            source_bytes, tree, func_node_ids, file_path, language,
        )
        ctx.result.edges.extend(df_edges)
    except Exception:
        pass

    return ctx.result
