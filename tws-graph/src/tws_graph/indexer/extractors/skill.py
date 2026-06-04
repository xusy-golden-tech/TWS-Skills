"""Skill extractor — treats TWS .md skill files as a structured "language"."""

from ..base import BaseExtractor, ExtractionContext


class SkillExtractor(BaseExtractor):
    extensions = [".skill.md"]  # Not .md — only SKILL.md files, matched by name
    tree_sitter_languages = []  # No tree-sitter; uses custom parser
    language_name = "skill"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        # Skill extraction is handled separately — not via tree-sitter pipeline
        # This class exists for registry completeness.
        pass
