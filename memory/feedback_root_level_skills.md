---
name: root-level skills are source
description: Root-level skill files are the source; .claude/skills/ is compiled deployment output — update root first
type: feedback
---

Always update root-level markdown and skill files first, never `.claude/skills/` directly.

**Why:** User corrected this three times. Root-level files (e.g., `D:\TWS-Skills\found-tws-graph-usage\SKILL.md`, `D:\TWS-Skills\CLAUDE.md`, `D:\TWS-Skills\USAGE.md`) are the source. `.claude/skills/` contains compiled/deployment versions. The user's exact words: "说了多少次首先要更新的是根目录下的skill文件，.claude里的相当于编译后的部署结果"

**How to apply:** When told to update skill files or documentation, always target root-level paths first (e.g., `D:\TWS-Skills\found-*\SKILL.md`, `D:\TWS-Skills\comp-*\SKILL.md`, `D:\TWS-Skills\CLAUDE.md`). Only after root-level is updated should `.claude/skills/` be considered for sync.
