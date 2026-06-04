"""Skill linter — validates TWS skill .md files against structural rules.

Rules:
  1. YAML frontmatter must have 'name' and 'description'
  2. Entry/flow/team skills must have <SUBAGENT-STOP> tag
  3. Component/foundation skills must NOT have <SUBAGENT-STOP> tag
  4. Cross-references must resolve to existing skills
  5. No orphan skills (not referenced by any other skill or flow)
  6. SKILL-INDEX.md must exist and be current
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .indexer.skill_parser import (
    _parse_frontmatter,
    _classify_layer,
    _find_skill_refs,
    collect_known_skills,
    _read_file,
    LAYER_MAP,
)


@dataclass
class LintWarning:
    skill_name: str
    file_path: str
    rule: str
    message: str
    severity: str = "warning"  # warning | error


@dataclass
class LintReport:
    warnings: list[LintWarning] = field(default_factory=list)
    files_checked: int = 0

    @property
    def errors(self) -> list[LintWarning]:
        return [w for w in self.warnings if w.severity == "error"]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def lint_skills(root_dir: str) -> LintReport:
    """Run all lint rules against skill files in root_dir."""
    report = LintReport()
    dir_names, known_paths, fm_to_dir = collect_known_skills(root_dir)

    if not dir_names:
        report.warnings.append(LintWarning(
            skill_name="<project>",
            file_path=root_dir,
            rule="no-skills",
            message="No skill files found",
            severity="error",
        ))
        return report

    # Check SKILL-INDEX.md exists
    index_path = os.path.join(root_dir, "SKILL-INDEX.md")
    index_content = None
    if os.path.isfile(index_path):
        index_content = _read_file(index_path)
        report.files_checked += 1
    else:
        report.warnings.append(LintWarning(
            skill_name="<project>",
            file_path="SKILL-INDEX.md",
            rule="missing-index",
            message="SKILL-INDEX.md not found",
            severity="warning",
        ))

    # Check each skill file
    for dir_name, rel_path in sorted(known_paths.items()):
        full_path = os.path.join(root_dir, rel_path)
        content = _read_file(full_path)
        if not content:
            continue

        report.files_checked += 1
        layer = _classify_layer(dir_name)

        fm, body = _parse_frontmatter(content)

        # Rule 1: Frontmatter
        if not fm:
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="missing-frontmatter",
                message="YAML frontmatter missing or invalid",
                severity="error",
            ))
            continue  # can't check further without frontmatter

        fm_name = fm.get("name", dir_name)

        if not fm.get("name"):
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="missing-name",
                message='Frontmatter missing "name" field',
                severity="error",
            ))

        if not fm.get("description"):
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="missing-description",
                message='Frontmatter missing "description" field',
                severity="warning",
            ))

        # Rule 2/3: SUBAGENT-STOP
        has_tag = "<SUBAGENT-STOP>" in content
        requires_tag = layer in ("entry", "flow", "team")
        forbids_tag = layer in ("component", "foundation")

        if requires_tag and not has_tag:
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="missing-subagent-stop",
                message=f"{layer} layer skill must have <SUBAGENT-STOP> tag",
                severity="error",
            ))
        elif forbids_tag and has_tag:
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="unexpected-subagent-stop",
                message=f"{layer} layer skill should not have <SUBAGENT-STOP> tag",
                severity="warning",
            ))

        # Rule 4: Cross-references (use dir_names for matching)
        # Get ALL refs (not filtered) to detect dangling ones
        all_refs = _find_skill_refs(body, dir_names, filter_known=False)
        for ref in all_refs:
            if ref == dir_name:
                continue
            if ref not in dir_names:
                report.warnings.append(LintWarning(
                    skill_name=dir_name, file_path=rel_path,
                    rule="dangling-reference",
                    message=f"References unknown skill: {ref}",
                    severity="warning",
                ))

        # Rule: Layer prefix matches directory name
        expected_prefix = {
            "entry": "using-",
            "flow": "flow-",
            "component": "comp-",
            "foundation": "found-",
            "team": "team-",
        }.get(layer, "")

        if expected_prefix and not dir_name.startswith(expected_prefix):
            report.warnings.append(LintWarning(
                skill_name=dir_name, file_path=rel_path,
                rule="layer-prefix-mismatch",
                message=f"Directory '{dir_name}' should start with '{expected_prefix}'",
                severity="warning",
            ))

    # Rule 5: Dead skills (not referenced by any other skill body)
    referenced: set[str] = set()
    for dir_name, rel_path in known_paths.items():
        full_path = os.path.join(root_dir, rel_path)
        content = _read_file(full_path)
        if not content:
            continue
        fm, body = _parse_frontmatter(content)
        if fm:
            refs = _find_skill_refs(body, dir_names)
            referenced.update(refs)

    for dir_name in dir_names:
        # Entry skill is always "alive" (it's the entry point)
        if dir_name == "using-tws":
            continue
        if dir_name not in referenced:
            # Check if it's referenced in SKILL-INDEX.md
            if index_content and dir_name in (index_content or ""):
                continue
            report.warnings.append(LintWarning(
                skill_name=dir_name,
                file_path=known_paths.get(dir_name, "?"),
                rule="unreferenced-skill",
                message=f"Skill '{dir_name}' is not referenced by any other skill. "
                        f"Consider adding it to a flow or removing it if dead.",
                severity="warning",
            ))

    return report
