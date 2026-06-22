"""ParseExtractPass — tree-sitter parsing and symbol extraction.

Parses each file in ctx.files using the language-appropriate tree-sitter
extractor (via ExtractorRegistry) and stores extracted symbols (defs, calls,
refs) in ctx.parsed_results.
"""

from __future__ import annotations

import os
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext

logger = logging.getLogger(__name__)


class ParseExtractPass(Pass):
    """Parse source files with tree-sitter and extract symbol definitions, calls, and references.

    For each file in ctx.files:
    1. Read file content from disk.
    2. Detect language via file extension (using extractor registry).
    3. Parse with tree-sitter and extract symbols via the registered extractor.
    4. Organize results into {defs, calls, refs, errors} per file.

    Reuses the existing ``ExtractorRegistry`` (via ``tws_graph.indexer.parser.extract_from_source``)
    — does not reimplement parsing logic.
    """

    name: str = "parse-extract"
    description: str = (
        "Parse source files with tree-sitter and extract symbol definitions, "
        "call edges, and reference edges"
    )
    dependencies: list[str] = ["stat-filter"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        # Lazy imports so the registry is only loaded when this pass actually runs
        from tws_graph.indexer.parser import extract_from_source
        from tws_graph.indexer.language_detect import detect_language

        for file_path in ctx.files:
            full_path = os.path.join(ctx.root_dir, file_path)

            # Read file content
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Failed to read %s: %s", file_path, exc)
                ctx.parsed_results[file_path] = {
                    "defs": [],
                    "calls": [],
                    "refs": [],
                    "errors": [{
                        "message": f"Read error: {exc}",
                        "file_path": file_path,
                        "severity": "error",
                    }],
                }
                continue

            # Detect language
            language = detect_language(file_path)
            if language == "unknown":
                logger.debug("Skipping %s: unsupported language", file_path)
                ctx.parsed_results[file_path] = {
                    "defs": [],
                    "calls": [],
                    "refs": [],
                    "errors": [],
                }
                continue

            # Parse and extract symbols
            result = extract_from_source(file_path, content, language)

            # Separate edges by kind
            defs = list(result.nodes)
            calls = [e for e in result.edges if e.get("kind") == "calls"]
            refs = [e for e in result.edges if e.get("kind") in ("references", "imports", "refs")]

            ctx.parsed_results[file_path] = {
                "defs": defs,
                "calls": calls,
                "refs": refs,
                "errors": list(result.errors),
            }

            # Propagate extraction errors to ctx.errors
            for err in result.errors:
                ctx.errors.append({
                    "pass": self.name,
                    "error": str(err),
                    "severity": err.get("severity", "error"),
                    "file_path": file_path,
                })

        return ctx
