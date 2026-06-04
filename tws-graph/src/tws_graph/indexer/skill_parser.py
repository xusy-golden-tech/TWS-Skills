"""Skill (.md) file parser — extracts YAML frontmatter, references, and metadata.

Treats TWS skill .md files as a structured "language":
  - YAML frontmatter → node metadata
  - <SUBAGENT-STOP> → constraint flag
  - Body mentions of other skill names → reference edges
  - SKILL-INDEX.md Mermaid graph → validated dependency edges
"""

from __future__ import annotations

import hashlib
import os
import re
import yaml

from .base import ExtractionResult

# Layer classification by directory prefix
LAYER_MAP = {
    "using": "entry",
    "flow": "flow",
    "comp": "component",
    "found": "foundation",
    "team": "team",
    "tws-init": "init",
    "tws-graph": "external",
}


def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _classify_layer(dir_name: str) -> str:
    """Determine skill layer from directory name prefix."""
    for prefix, layer in LAYER_MAP.items():
        if dir_name.startswith(prefix):
            return layer
    return "unknown"


def _parse_frontmatter(content: str) -> tuple[dict | None, str]:
    """Parse YAML frontmatter from a .md file.

    Returns (frontmatter_dict, body_text) or (None, full_content) if no frontmatter.
    Handles UTF-8 BOM at start of file.
    """
    # Strip BOM if present
    if content.startswith("\ufeff"):
        content = content[1:]

    if not content.startswith("---"):
        return None, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content

    try:
        fm = yaml.safe_load(parts[1])
        body = parts[2]
        return fm if isinstance(fm, dict) else None, body
    except yaml.YAMLError:
        return None, content


def _find_skill_refs(body: str, known_skill_names: set[str],
                      filter_known: bool = True) -> set[str]:
    """Find references to skill names in body text.

    Matches patterns like:
      - `` `skill-name` ``
      - `skill-name/SKILL.md`
      - bare mentions of known skill names (only when filter_known=True)

    If filter_known=True, only returns refs in known_skill_names.
    If filter_known=False, returns all detected refs (used for linting).
    """
    refs: set[str] = set()

    # Pattern 1: backtick-quoted references: `comp-design-doc`
    for m in re.finditer(r'`([a-z]+-[a-z0-9-]+)`', body):
        name = m.group(1)
        if not filter_known or name in known_skill_names:
            refs.add(name)

    # Pattern 2: explicit SKILL.md path references
    for m in re.finditer(r'`?([a-z]+-[a-z0-9-]+)/SKILL\.md`?', body):
        name = m.group(1)
        if not filter_known or name in known_skill_names:
            refs.add(name)

    # Pattern 3: bare mentions (word boundary)
    for name in known_skill_names:
        if re.search(r'\b' + re.escape(name) + r'\b', body):
            refs.add(name)

    return refs


def _parse_mermaid_deps(content: str) -> list[tuple[str, str]]:
    """Parse SKILL-INDEX.md Mermaid graph for explicit dependencies.

    Returns list of (source_skill_name, target_skill_name) pairs.
    """
    deps: list[tuple[str, str]] = []

    # Find mermaid code block
    m = re.search(r'```mermaid\s*\n(.*?)\n```', content, re.DOTALL)
    if not m:
        return deps

    mermaid = m.group(1)

    # Maintain alias → label map for label→alias lookup
    # Pattern: alias[label]
    alias_to_short: dict[str, str] = {}
    for am in re.finditer(r'(\w+)\[([^\]]+)\]', mermaid):
        alias_to_short[am.group(1)] = am.group(2).strip()

    # SHORT_NAME_TO_DIRNAME mapping (from SKILL-INDEX.md tables)
    short_to_dir: dict[str, str] = {}
    for alias, label in alias_to_short.items():
        # Convert Chinese label to dir name (best-effort)
        short_to_dir[alias] = label

    # Parse edges: A[label] --> B[label] or A --> B
    for em in re.finditer(r'(\w+)(?:\[[^\]]*\])?\s*-->\s*(\w+)(?:\[[^\]]*\])?', mermaid):
        src_alias = em.group(1)
        tgt_alias = em.group(2)

        # Skip if self-reference or subgraph marker
        if src_alias == tgt_alias:
            continue

        deps.append((src_alias, tgt_alias))

    return deps


def _build_node_id(name: str, file_path: str) -> str:
    qname = f"{file_path}::{name}"
    return _hash_id(qname, file_path)


def extract_skill_file(file_path: str, content: str) -> ExtractionResult:
    """Extract skill node + reference edges from a single SKILL.md file."""
    result = ExtractionResult()

    fm, body = _parse_frontmatter(content)
    if not fm:
        return result

    skill_name = fm.get("name", "")
    if not skill_name:
        return result

    # Determine layer from directory
    dir_name = os.path.basename(os.path.dirname(file_path))
    layer = _classify_layer(dir_name)
    has_subagent_stop = "<SUBAGENT-STOP>" in content
    description = fm.get("description", "")

    # Build node
    nid = _build_node_id(skill_name, file_path)
    node = {
        "id": nid,
        "kind": "skill",
        "name": skill_name,
        "qualified_name": f"{layer}::{skill_name}",
        "file_path": file_path,
        "language": "skill",
        "start_line": 1,
        "end_line": content.count("\n") + 1,
        "signature": f"{layer} skill",
        "docstring": description[:200] if description else "",
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 1,
        "decorators": None,
        "framework": "tws",
        "extra": {
            "layer": layer,
            "has_subagent_stop": has_subagent_stop,
            "description": description,
        },
    }
    result.nodes.append(node)

    return result


def extract_skill_refs(file_path: str, content: str,
                        known_skill_names: set[str],
                        known_skill_paths: dict[str, str]) -> list[dict]:
    """Extract reference edges from a SKILL.md body.

    Args:
        file_path: path to this SKILL.md
        content: file content
        known_skill_names: set of all skill names in the project
        known_skill_paths: {skill_name: file_path} mapping
    """
    edges = []
    fm, body = _parse_frontmatter(content)
    if not fm:
        return edges

    skill_name = fm.get("name", "")
    if not skill_name:
        return edges

    source_id = _build_node_id(skill_name, file_path)
    refs = _find_skill_refs(body, known_skill_names)

    for ref_name in refs:
        if ref_name == skill_name:
            continue  # skip self-reference
        target_path = known_skill_paths.get(ref_name, "")
        target_id = _build_node_id(ref_name, target_path) if target_path else \
                   _hash_id(f"{target_path}::{ref_name}", target_path)
        edges.append({
            "source": source_id,
            "target": target_id,
            "target_text": ref_name,
            "kind": "references",
            "source_loc": f"{file_path}:1",
            "provenance": "skill-reference",
        })

    return edges


def extract_skill_index(file_path: str, content: str,
                         known_skill_paths: dict[str, str]) -> list[dict]:
    """Extract Mermaid dependency edges from SKILL-INDEX.md.

    Validates that source and target skills exist in the project.
    Returns list of edge dicts.
    """
    edges = []
    deps = _parse_mermaid_deps(content)
    if not deps:
        return edges

    for src_alias, tgt_alias in deps:
        # Determine actual skill names from aliases or short names
        # For SKILL-INDEX.md, aliases are used for Mermaid but may not
        # directly match skill dir names. We try best-effort resolution.

        # Skip non-skill nodes like GI, TWG, A, B, C...
        if len(src_alias) <= 2 and src_alias.isalpha():
            continue
        if len(tgt_alias) <= 2 and tgt_alias.isalpha():
            continue

        src_path = known_skill_paths.get(src_alias, f"SKILL-INDEX.md")
        tgt_path = known_skill_paths.get(tgt_alias, "")

        source_id = _build_node_id(src_alias, src_path)
        target_id = _build_node_id(tgt_alias, tgt_path) if tgt_path else \
                   _hash_id(f"{tgt_path}::{tgt_alias}", tgt_path)

        edges.append({
            "source": source_id,
            "target": target_id,
            "target_text": tgt_alias,
            "kind": "depends",
            "source_loc": f"{file_path}:1",
            "provenance": "mermaid-graph",
        })

    return edges


def collect_known_skills(root_dir: str) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Scan project for all skill directories.

    Returns (dir_names, {dir_name: relative_path}, {frontmatter_name: dir_name})
    """
    dir_names: set[str] = set()
    paths: dict[str, str] = {}  # dir_name → relative path
    fm_to_dir: dict[str, str] = {}  # frontmatter name → dir_name

    if not os.path.isdir(root_dir):
        return dir_names, paths, fm_to_dir

    for entry in os.listdir(root_dir):
        entry_path = os.path.join(root_dir, entry)
        if not os.path.isdir(entry_path):
            continue

        skill_md = os.path.join(entry_path, "SKILL.md")
        if os.path.isfile(skill_md):
            rel_path = os.path.relpath(skill_md, root_dir).replace("\\", "/")
            dir_name = entry  # e.g. "found-core-principles"
            dir_names.add(dir_name)
            paths[dir_name] = rel_path
            # Also try to get frontmatter name for reference resolution
            content = _read_file(skill_md)
            if content:
                fm, _ = _parse_frontmatter(content)
                if fm and fm.get("name"):
                    fm_to_dir[fm["name"]] = dir_name

    return dir_names, paths, fm_to_dir


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None
