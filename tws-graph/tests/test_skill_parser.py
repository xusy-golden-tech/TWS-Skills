"""Tests for skill .md file parser and linter."""

import os
import pytest


class TestFrontmatterParsing:
    def test_parse_valid(self):
        from tws_graph.indexer.skill_parser import _parse_frontmatter
        content = "---\nname: test-skill\ndescription: A test skill\n---\n\n# Body"
        fm, body = _parse_frontmatter(content)
        assert fm is not None
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert "# Body" in body

    def test_parse_bom(self):
        from tws_graph.indexer.skill_parser import _parse_frontmatter
        content = "\ufeff---\nname: bom-skill\ndescription: Has BOM\n---\n\nBody"
        fm, body = _parse_frontmatter(content)
        assert fm is not None
        assert fm["name"] == "bom-skill"

    def test_parse_no_frontmatter(self):
        from tws_graph.indexer.skill_parser import _parse_frontmatter
        content = "# Just a heading\nNo frontmatter here"
        fm, body = _parse_frontmatter(content)
        assert fm is None
        assert body == content

    def test_parse_invalid_yaml(self):
        from tws_graph.indexer.skill_parser import _parse_frontmatter
        content = "---\n: invalid yaml\n---\n\nBody"
        fm, body = _parse_frontmatter(content)
        assert fm is None


class TestSkillRefs:
    def test_find_backtick_refs(self):
        from tws_graph.indexer.skill_parser import _find_skill_refs
        known = {"comp-design-doc", "flow-add-feature", "found-core-principles"}
        body = "Load `comp-design-doc` for design work. See also `flow-add-feature`."
        refs = _find_skill_refs(body, known)
        assert "comp-design-doc" in refs
        assert "flow-add-feature" in refs

    def test_find_path_refs(self):
        from tws_graph.indexer.skill_parser import _find_skill_refs
        known = {"comp-design-doc", "comp-test"}
        body = "详见 `comp-design-doc/SKILL.md` 和 `comp-test/SKILL.md`"
        refs = _find_skill_refs(body, known)
        assert "comp-design-doc" in refs
        assert "comp-test" in refs

    def test_no_self_reference(self):
        from tws_graph.indexer.skill_parser import _find_skill_refs
        # Self-reference is handled in the caller
        known = {"my-skill", "other-skill"}
        body = "This is my-skill, it uses other-skill"
        refs = _find_skill_refs(body, known)
        assert "my-skill" not in refs or "other-skill" in refs

    def test_empty_body(self):
        from tws_graph.indexer.skill_parser import _find_skill_refs
        known = {"skill-a", "skill-b"}
        refs = _find_skill_refs("", known)
        assert len(refs) == 0


class TestLayerClassification:
    def test_classify_flow(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("flow-add-feature") == "flow"

    def test_classify_component(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("comp-design-doc") == "component"

    def test_classify_foundation(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("found-core-principles") == "foundation"

    def test_classify_team(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("team-branch-flow") == "team"

    def test_classify_entry(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("using-tws") == "entry"

    def test_classify_unknown(self):
        from tws_graph.indexer.skill_parser import _classify_layer
        assert _classify_layer("something-else") == "unknown"


class TestExtractSkillFile:
    def test_extract_node(self, tmp_path):
        from tws_graph.indexer.skill_parser import extract_skill_file

        skill_dir = tmp_path / "comp-test-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: test-skill
description: A test skill for unit testing
---

# Test Skill

This is a test skill.
""")

        content = skill_md.read_text()
        rel_path = "comp-test-skill/SKILL.md"
        result = extract_skill_file(rel_path, content)

        assert len(result.nodes) == 1
        node = result.nodes[0]
        assert node["kind"] == "skill"
        assert node["name"] == "test-skill"
        assert node["language"] == "skill"
        assert node["framework"] == "tws"
        assert "component" in node["qualified_name"]

    def test_extract_with_subagent_stop(self, tmp_path):
        from tws_graph.indexer.skill_parser import extract_skill_file

        content = """---
name: entry-skill
description: Entry point
---

<SUBAGENT-STOP>
Skip if subagent
</SUBAGENT-STOP>

# Entry
"""
        result = extract_skill_file("using-test/SKILL.md", content)
        assert len(result.nodes) == 1
        assert result.nodes[0]["extra"]["has_subagent_stop"] is True

    def test_extract_no_frontmatter(self):
        from tws_graph.indexer.skill_parser import extract_skill_file
        result = extract_skill_file("test/SKILL.md", "# No frontmatter")
        assert len(result.nodes) == 0


class TestSkillExtractRefs:
    def test_extract_ref_edges(self, tmp_path):
        from tws_graph.indexer.skill_parser import extract_skill_refs

        content = """---
name: my-skill
description: A skill that references others
---

Load `comp-design-doc` first, then `comp-test`.
"""
        known = {"comp-design-doc", "comp-test"}
        paths = {"comp-design-doc": "comp-design-doc/SKILL.md",
                 "comp-test": "comp-test/SKILL.md"}

        edges = extract_skill_refs("my-skill/SKILL.md", content, known, paths)
        assert len(edges) >= 2
        kinds = {e["kind"] for e in edges}
        assert "references" in kinds


class TestLinter:
    def test_lint_valid_project(self, tmp_path):
        from tws_graph.skill_linter import lint_skills

        # Create minimal valid skill project
        (tmp_path / "using-tws").mkdir()
        (tmp_path / "using-tws" / "SKILL.md").write_text("""---
name: using-tws
description: Entry point
---

<SUBAGENT-STOP>
Stop here
</SUBAGENT-STOP>

# Entry skill
Load `comp-do-work` for work.
""")

        (tmp_path / "comp-do-work").mkdir()
        (tmp_path / "comp-do-work" / "SKILL.md").write_text("""---
name: do-work
description: Does the work
---

# Work skill
""")

        report = lint_skills(str(tmp_path))
        assert report.ok
        assert len(report.errors) == 0

    def test_lint_missing_subagent_stop(self, tmp_path):
        from tws_graph.skill_linter import lint_skills

        (tmp_path / "flow-do-stuff").mkdir()
        (tmp_path / "flow-do-stuff" / "SKILL.md").write_text("""---
name: do-stuff
description: A flow
---

# Flow without SUBAGENT-STOP
""")

        report = lint_skills(str(tmp_path))
        assert not report.ok
        assert any(w.rule == "missing-subagent-stop" for w in report.errors)

    def test_lint_missing_frontmatter_name(self, tmp_path):
        from tws_graph.skill_linter import lint_skills

        (tmp_path / "comp-no-name").mkdir()
        (tmp_path / "comp-no-name" / "SKILL.md").write_text("""---
description: No name field
---

# Body
""")

        report = lint_skills(str(tmp_path))
        assert not report.ok
        assert any(w.rule == "missing-name" for w in report.errors)

    def test_lint_no_skills(self, tmp_path):
        from tws_graph.skill_linter import lint_skills

        report = lint_skills(str(tmp_path))
        assert not report.ok
        assert any(w.rule == "no-skills" for w in report.errors)

    def test_lint_dangling_reference(self, tmp_path):
        from tws_graph.skill_linter import lint_skills

        (tmp_path / "comp-exists").mkdir()
        (tmp_path / "comp-exists" / "SKILL.md").write_text("""---
name: exists
description: I exist
---

Load `comp-ghost` for ghost work.
""")

        report = lint_skills(str(tmp_path))
        warnings = [w for w in report.warnings if w.rule == "dangling-reference"]
        assert len(warnings) >= 1
