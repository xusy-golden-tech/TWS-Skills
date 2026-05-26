# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TWS (Thoughtful Workflow System) is a collection of structured AI skill prompts that enforce disciplined software development workflows. It is **not** a traditional software project — there is no build system, no tests to run, and no compiled output. Every "skill" is a `SKILL.md` markdown file containing instructions for Claude Code agents.

## Architecture

The system has five layers, with strict call-direction rules:

```
Entry (using-tws) → Flows (flow-*) → Components (comp-*) → Foundation (found-*)
                                              ↕
                                         Team (team-*)
```

- **Entry** (`using-tws/`): Scene router — judges what the user wants, detects Solo/Team mode, loads the matching flow skill
- **Flows** (`flow-*/`): Workflow definitions (add-feature, fix-bug, hotfix, refactor, new-project, documentation, investigate) — orchestrate which components to call in what order
- **Components** (`comp-*/`): Executable units for specific tasks (design-doc, implementation, test, code-review, reproduce, root-cause-analysis, etc.) — loaded by sub-agents via the Skill tool
- **Foundation** (`found-*/`): Global rules (core-principles, artifact-split, review-methodology, review-triage) — referenced by all layers
- **Team** (`team-*/`): Multi-developer coordination (branch-flow, contract-aware, environment-gov, impact-report, subagent-dispatch) — activated when CONTRACTS.md exists

### Agent Hierarchy

- **Main agent** = project manager: routes, plans, dispatches sub-agents, verifies deliverables. Does NOT write code.
- **Sub-agents** = workers: each loads 1-2 component skills via the `Skill` tool, executes, reports back. Always use `general-purpose` subagent type (not `Explore`) because only general-purpose agents have the Skill tool.

### `<SUBAGENT-STOP>` Tags

Flow and entry skills carry `<SUBAGENT-STOP>` tags to prevent sub-agents from accidentally invoking orchestration skills. Component and foundation skills do **not** have this tag — sub-agents are meant to load them.

## Key Conventions

### Session State
Active workflows are tracked in `.tws/sessions/{flow-type}-{short-desc}.md`. Each session file has a version number (incremented on every edit) and step-by-step progress. Multiple sessions can run in parallel.

### Project Initialization
`tws-init` scans an existing or new project and generates convention files under `.tws/`:
- `project-map.md` (entry point for all agents)
- `coding-conventions[-{stack}].md`
- `testing-conventions[-{stack}].md`
- `design-conventions.md`
- `env-conventions.md`
- `architecture-decisions.md`

### Core Principles (applied across all flows)
1. Design First — no code without a design document
2. Incremental Implementation — break into verifiable steps, never skip
3. Verify — tests are the minimum; also check edge cases and design doc sync
4. Design Sync — any code change must update the design doc, or it's incomplete

### Mode Detection (mandatory, every conversation)
The entry skill forces detection of Solo vs Team mode by checking: (1) existence of `CONTRACTS.md`, (2) git author count. This check cannot be skipped or cached.

## Skill Dependency Graph

See `SKILL-INDEX.md` for the full Mermaid dependency diagram and tables mapping which components each flow calls.

## File Structure Pattern

Every skill follows:
```
{category}-{name}/
└── SKILL.md      ← YAML frontmatter (name, description) + instructions
```

There are no other files per skill (no code, no tests, no assets).

## When Editing Skills

- Maintain the YAML frontmatter `---` block with `name` and `description`
- Preserve `<SUBAGENT-STOP>` tags on entry/flow/team skills
- Include a "Rationalization Prevention" table at the end — these counter common shortcuts agents tend to take
- Update `SKILL-INDEX.md` when adding, removing, or renaming skills
- Skills are written in Chinese — keep them in Chinese when editing
