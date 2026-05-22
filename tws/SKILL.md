# TWS — Claude Code Adapter

> **注意：** 将此文件复制到目标项目的 `.claude/skills/tws/SKILL.md` 即可安装。

## 加载方式

自动加载（推荐）：安装到 `.claude/skills/` 后，Claude Code 启动时自动发现此 skill。

手动加载：在对话中输入 `/skill tws`。

## 核心规则

入口 skill 是 `skills/using-tws/SKILL.md`，请读取并按其中的流程执行。

## 补充说明

- TWS 流程中的 Plan 步骤：如果项目已安装 Superpowers 插件，可以用 `superpowers:writing-plans` skill 辅助写实施计划（任务拆分、执行顺序）。没有装则主 agent 直接输出
- TWS 流程中的设计书步骤（design-doc）和 Plan 是两回事：design-doc 产出设计书（涉及模块、实现方案、接口变更），Plan 产出实施计划（任务拆分、执行顺序）。先设计书后计划
- 项目根目录存在 `CONTRACTS.md` 时自动启用 Team Mode
- 项目规约和结构索引在 `.tws/project-map.md`（由 tws-init 生成）
- TWS 的"设计书"（design-doc skill 产出）是**本次任务的设计书**。项目 `docs/` 目录下可能有**系统架构设计书**，两者不是同一个东西。design-sync 同步的是 `docs/` 下的架构设计书，不是覆盖 TWS 的任务设计书
