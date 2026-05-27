# Contributing

本仓库的主要产物是 skill 指令。修改时优先保持结构稳定、路径准确、规则可执行。

## Skill 基本格式

每个 skill 至少包含：

```markdown
---
name: skill-name
description: 简短说明触发场景和用途
---

# 标题

## 触发条件

## 工作流程

## 输出格式或完成依据

## Rationalization Prevention
```

入口、流程、调度类 skill 应保留 `<SUBAGENT-STOP>`，避免子 agent 误触发编排流程。人类专用或禁止子 agent 直接调用的组件，应在索引中说明并考虑添加 `<SUBAGENT-STOP>`。

## 路径约定

- 引用 skill 时同时写 skill 名称和相对路径更稳妥，例如 `tws-init/SKILL.md`。
- 引用根目录文档时直接使用根路径，例如 `checkpoint-reference.md`。
- 增强型 skill 可以包含 `scripts/`、`data/`、`assets/`，但必须在 `SKILL.md` 中说明入口脚本、依赖、读写行为和生成物位置。
- Claude Code、Codex、VSCode 使用同一套 `SKILL.md`；不要为不同平台复制一份改名后的 skill。平台差异写入 `PLATFORM-SUPPORT.md` 或 `.tws/platform-skills.md`。

## 编码和平台

- Markdown 使用 UTF-8。
- 命令示例优先提供平台中立描述；涉及 shell 时同时考虑 Bash/Git Bash 和 PowerShell。
- Python 示例优先使用 `python`，必要时补充 Windows 可用的 `py -3`。

## 修改检查清单

- [ ] 所有新增或改名的 skill 已同步 `SKILL-INDEX.md`。
- [ ] `SKILL.md` frontmatter 包含 `name` 和 `description`。
- [ ] 入口/流程/调度类 skill 的 `<SUBAGENT-STOP>` 约定正确。
- [ ] 路径引用能在仓库中找到。
- [ ] 如果改了脚本或数据，运行 `python validate-skills.py`。
- [ ] 如果改了平台调用方式，同步更新 `PLATFORM-SUPPORT.md`、`README.md` 和 `tws-init/SKILL.md` 中的 `.tws/platform-skills.md` 说明。
- [ ] 不提交本地状态和生成物，例如 `.tws/`、`design-system/`、`__pycache__/`、`*.pyc`。
