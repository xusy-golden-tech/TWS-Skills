# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TWS (Thoughtful Workflow System) is a collection of structured AI skill prompts that enforce disciplined software development workflows. It has two parts:

1. **Skill files** — 40 `SKILL.md` markdown files, each containing YAML frontmatter + instructions for Claude Code agents. These are the "product."
2. **tws-graph** — a Python CLI that pre-builds a tree-sitter-based code symbol graph (SQLite). Agents query it via bash instead of grep + read. Lives in `tws-graph/`.

See `USAGE.md` for user-facing setup instructions.

## Architecture

The skill system has four layers with strict call-direction rules:

```
Entry (using-tws) → Flows (flow-*) → Components (comp-*) → Foundation (found-*)
```

- **Entry**: Scene router — loads the matching flow skill
- **Flows**: Workflow definitions (add-feature, fix-bug, hotfix, refactor, new-project, documentation, investigate)
- **Components**: Executable units (design-doc, implementation, test, code-review, reproduce, root-cause-analysis, etc.) — loaded by sub-agents via the Skill tool
- **Foundation**: Global rules (7 skills: core-principles, artifact-split, review-methodology, review-triage, branch-flow, environment-gov, impact-report)
- **Init**: `tws-init` (project bootstrapping) and `tws-graph-init` (code graph setup)

Full dependency graph is in `SKILL-INDEX.md`.

## Agent Hierarchy

- **Main agent** = project manager: routes, plans, dispatches sub-agents, verifies deliverables. Does NOT write code.
- **Sub-agents** = workers: each loads 1-2 component skills via `Skill` tool, executes, reports back. Always use `general-purpose` subagent type — `Explore` agents lack the Skill tool.
- **Review sub-agents** must use clean isolated context — no memory files, no main agent history.

## Key Rules for Skill Files

- `<SUBAGENT-STOP>` tags are **required** on entry/flow skills, **forbidden** on component/foundation skills
- Every SKILL.md must have YAML frontmatter with `name` and `description`
- Include a "Rationalization Prevention" table at the end of each skill
- Update `SKILL-INDEX.md` when adding, removing, or renaming skills
- Skills are written in Chinese — keep them in Chinese when editing
- Skill directory name must match its layer prefix: `using-` / `flow-` / `comp-` / `found-`

## tws-graph

### Setup

```bash
pip install -e tws-graph/
```

### Commands

| Command | Purpose |
|---------|---------|
| `tws-graph index` | Full/incremental index of source files |
| `tws-graph sync` | Incremental sync (stat-based, faster) |
| `tws-graph calls <node>` | Show all call targets from a node |
| `tws-graph impact <node>` | Show what would break if node changes |
| `tws-graph trace <src> <tgt>` | Find paths between two nodes |
| `tws-graph search <query>` | FTS5 full-text search (supports `kind:`, `lang:`, `path:` qualifiers) |
| `tws-graph unresolved` | List unresolved cross-file references |
| `tws-graph diff` | Compare snapshots (before/after) |
| `tws-graph snapshot <name>` | Create named snapshot |
| `tws-graph hooks install/remove` | Git hooks for auto-sync |
| `tws-graph lint` | Validate all skill files against structural rules |

The index database lives at `.tws/codegraph/index.db`.

### Testing

```bash
cd tws-graph
pytest                          # all 183 tests
pytest tests/test_fts.py        # single file
pytest -k "test_edit"           # name filter
```

Tests use temporary SQLite databases (`tmp_path` fixture) — no external dependencies needed.

### Adding a Language Extractor

1. Create `src/tws_graph/indexer/extractors/{lang}.py` with a class extending `BaseExtractor`
2. The registry auto-discovers it — no other files need changes

### Skill Validation

```bash
tws-graph lint       # runs all 5 rules: frontmatter, SUBAGENT-STOP, cross-refs, prefix, unreferenced
```

The linter checks 40 skills across D:\TWS-Skills (root project). Currently: 0 errors, 0 warnings.

## Project Initialization (.tws/)

`tws-init` generates these files under `.tws/`:
- `project-map.md` — entry point for all agents (tech stack, key paths, convention index)
- `coding-conventions.md` / `testing-conventions.md` — inferred from code sampling
- `design-conventions.md` / `env-conventions.md` — template + scan
- `architecture-decisions.md` — empty template, populated by workflows over time
- `codegraph/index.db` + `codegraph/index-initial.db` — code symbol graph + baseline

Session state is tracked in `.tws/sessions/` during active workflows. `.tws/` should be in `.gitignore`.

## Core Principles (applied across all flows)

1. **Design First** — no code without a design document
2. **Incremental Implementation** — break into verifiable steps, never skip
3. **Verify** — tests are the minimum; also check edge cases and design doc sync
4. **Design Sync** — any code change must update the design doc, or it's incomplete

