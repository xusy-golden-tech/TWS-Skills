---
name: skill-source-location
description: Skill 源码在根目录而非 .claude/skills/ — 后者是部署产物
type: feedback
---

Skill 文件的源码在项目根目录下的 `{skill-name}/SKILL.md`，`.claude/skills/` 是编译后的部署产物。

**Why:** 用户明确指出 `.claude/skills/` 相当于编译后的部署产物，根目录下的才是修改中的代码。类比代码工程的 src/dist 分离。

**How to apply:** 新建或修改任何 skill 时，只操作根目录下的 `{skill-name}/SKILL.md`，不碰 `.claude/skills/`。部署流程会从根目录同步到 `.claude/skills/`。
