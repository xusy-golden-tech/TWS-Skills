# TWS Skills Repository Guidance

This repository contains TWS skill definitions. When Codex opens this repository directly, treat each top-level `*/SKILL.md` directory as a TWS skill source.

For another project to use these skills natively:

- Codex: install or link each skill directory under `.agents/skills/{skill-name}/SKILL.md`.
- Claude Code: install or link each skill directory under `.claude/skills/{skill-name}/SKILL.md`, `~/.claude/skills/`, or an `--add-dir` `.claude/skills/` directory.
- VSCode/editor plugins: follow `PLATFORM-SUPPORT.md` and scan `{sourceRoot}/*/SKILL.md`.

If the whole repository is embedded as a normal folder such as `vendor/TWS_Skills/`, do not assume native skill discovery. Use `.tws/platform-skills.md` to map skill names to paths under that source root.

For a target project to default to TWS in a new session, the target project needs its own persistent entry:

- Claude Code: add a TWS block to the target project `CLAUDE.md`.
- Codex: add a TWS block to the target project `AGENTS.md`.
- VSCode/editor plugins: put the same rule in the plugin workspace instruction or project prompt.

That entry should tell new sessions to read `.tws/project-map.md` and `.tws/platform-skills.md`, route multi-step engineering work through `using-tws/SKILL.md`, run `tws-init/SKILL.md` when `.tws/project-map.md` is missing, and resume unfinished `.tws/sessions/` flows after user confirmation.

Project entries should be TWS managed blocks delimited by `<!-- TWS:BEGIN managed by TWS Skills {version} -->` and `<!-- TWS:END -->`. When a target project overwrites or upgrades this repository, rerun `tws-init` so `.tws/tws-version`, `.tws/platform-skills.md`, and the target project's `AGENTS.md` / `CLAUDE.md` TWS block point at the new version and source root.
