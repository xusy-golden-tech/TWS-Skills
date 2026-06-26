# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TWS (Thoughtful Workflow System) is a collection of structured AI skill prompts that enforce disciplined software development workflows. It has two parts:

1. **Skill files** — 41 `SKILL.md` markdown files, each containing YAML frontmatter + instructions for Claude Code agents. These are the "product."
2. **tws-graph** — a Rust-powered (v7.0.0) CLI that pre-builds a tree-sitter-based code symbol graph (SQLite). Agents query it via bash instead of grep + read. Python CLI is now a thin wrapper around the Rust core (_core native library). Lives in `tws-graph/`.

See `USAGE.md` for user-facing setup instructions.

## Architecture

The skill system has four layers with strict call-direction rules:

```
Entry (using-tws / using-tws-goal) → Flows (flow-*) → Components (comp-*) → Foundation (found-*)
```

- **Entry**: Scene routers — `using-tws` (interactive, step-by-step confirmation) and `using-tws-goal` (autonomous, goal-driven TDD loop). Both route to the same flow skills but differ in execution mode
- **Flows**: Workflow definitions (add-feature, fix-bug, hotfix, refactor, new-project, documentation, investigate)
- **Components**: Executable units (design-doc, implementation, test, code-review, reproduce, root-cause-analysis, etc.) — loaded by sub-agents via the Skill tool
- **Foundation**: Global rules (7 skills: core-principles, artifact-split, review-methodology, review-triage, branch-flow, environment-gov, impact-report)
- **Init**: `tws-init` (project bootstrapping) and `tws-graph-init` (code graph setup)

Full dependency graph can be queried via `tws-graph search kind:skill`.

## Agent Hierarchy

- **Main agent** = project manager: routes, plans, dispatches sub-agents, verifies deliverables. Does NOT write code.
- **Sub-agents** = workers: each loads 1-2 component skills via `Skill` tool, executes, reports back. Always use `general-purpose` subagent type — `Explore` agents lack the Skill tool.
- **Review sub-agents** must use clean isolated context — no memory files, no main agent history.

## Key Rules for Skill Files

- `<SUBAGENT-STOP>` tags are **required** on entry/flow skills, **forbidden** on component/foundation skills
- Every SKILL.md must have YAML frontmatter with `name` and `description`
- Include a "Rationalization Prevention" table at the end of each skill
- Run `tws-graph lint` after adding, removing, or renaming skills to verify structural rules
- Skills are written in Chinese — keep them in Chinese when editing
- Skill directory name must match its layer prefix: `using-` / `flow-` / `comp-` / `found-`

## tws-graph

### Architecture (v7.0.0)

tws-graph 7.0.0 has been rewritten from Python to Rust. The Python package (`tws-graph/`) is now a thin CLI wrapper that delegates all core operations to the Rust native library (`_core`). The bridge module is `src/tws_graph/rust_bridge.py`.

- **Rust core** (`_core/_core` native library): indexer, query engine, graph algorithms, analysis, GQL, lint, LSP setup, watch, federate, hooks, snapshots, taint, health, metrics, layers, cycles
- **Python CLI** (`src/tws_graph/cli.py`): typer-based command-line interface, thin wrapper calling rust_bridge
- **Python MCP** (`src/tws_graph/mcp/`): MCP server (21 tools + 3 resources), uses Python store/analysis modules
- **Python Store** (`src/tws_graph/store/`): SQLite data layer used by MCP and CLI helpers

### Setup

```bash
pip install -e tws-graph/
```

The Rust core is built separately. See `tws-graph/_core/` for build instructions.

### Commands (18 CLI commands, all backed by Rust core)

| Command | Purpose |
|---------|---------|
| `tws-graph index` | Full/incremental index of source files (Rust indexer) |
| `tws-graph sync` | Incremental sync via Rust indexer |
| `tws-graph search <query>` | FTS5 full-text search (supports `kind:`, `lang:`, `path:` qualifiers) |
| `tws-graph calls <node>` | Show call targets/callers from a node |
| `tws-graph impact <node>` | Show what would break if node changes |
| `tws-graph trace <src> <tgt>` | Find paths between two nodes |
| `tws-graph unresolved` | List unresolved cross-file references |
| `tws-graph snapshot <name>` | Create named snapshot |
| `tws-graph diff` | Compare snapshots (before/after) |
| `tws-graph hooks install/remove` | Git hooks for auto-sync |
| `tws-graph lint` | Validate all skill files against structural rules |
| `tws-graph watch` | File watcher with auto-sync |
| `tws-graph query <gql>` | GQL graph query |
| `tws-graph cycles` | Detect circular dependencies |
| `tws-graph layers` | Architecture layer violation detection |
| `tws-graph metrics` | Module cohesion/coupling metrics |
| `tws-graph taint` | Security taint analysis |
| `tws-graph health` | Code health scoring |
| `tws-graph analyze` | Analysis runners (--run and --algorithm modes) |
| `tws-graph export` | Export to DOT/Mermaid/JSON |
| `tws-graph predict-impact` | Change impact prediction |
| `tws-graph federate` | Multi-repo federation |
| `tws-graph serve` | MCP server (21 tools + 3 resources) |
| `tws-graph lsp setup` | LSP server availability check |

The index database lives at `.tws/codegraph/index.db`.

### Testing

```bash
cd tws-graph
pytest                          # Python tests
pytest tests/test_fts.py        # single file
pytest -k "test_edit"           # name filter
```

Python tests use temporary SQLite databases (`tmp_path` fixture). Rust core has its own test suite (1025 tests).

### Skill Validation

```bash
tws-graph lint       # runs all 5 rules: frontmatter, SUBAGENT-STOP, cross-refs, prefix, unreferenced
```

The linter checks 42 skills across D:\TWS-Skills (root project). Currently: 0 errors, 213 warnings (all pre-existing dangling-refs in comp-frontend-ui-design).

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

