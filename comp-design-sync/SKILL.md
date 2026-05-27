---
name: design-sync
description: 设计书同步。每次改完代码后必须调用，确保设计书与代码一致。「不改设计书 = 代码没改完」
---

# 设计书同步

## 核心原则

**不改设计书 = 代码没改完。**

设计书同步不是编码完成后的可选项，不是「有空再做」的杂务，而是「完成任务」的必要条件。一份过时的设计书比没有设计书更危险——它会误导下一个人。

## The Gate Function

```
AFTER code changes are complete, BEFORE marking task done:

1. LIST all files you modified (code, config, tests)
2. MATCH each modified file to its design doc
3. COMPARE: does the design doc still match the code?
   - Mismatch → UPDATE design doc to match code
   - Missing → CREATE design doc
   - Deprecated → MARK as deprecated
4. VERIFY: re-read updated design doc — does it accurately describe the code?
5. ONLY THEN: mark task as complete
```

## 轻量模式（hotfix 等紧急场景）

紧急修复时至少做到：在设计书「变更履历」追加一行。完整同步可延后，但必须在 `.tws/sessions/{当前流程文件}` 的待补事项中标记「待补完整 design-sync」。

## 修改对照表

| 你改了 | 设计书要同步 |
|-------|------------|
| 函数签名（参数/返回值变了） | 更新函数定义 |
| API 路由或响应格式 | 更新接口文档 |
| 新增了一个文件/模块 | 新建设计书 |
| 删了一个功能 | 标记废弃，说明原因 |
| 修了一个 Bug | 在相关章节加修复说明 |

## 变更履历管理

每次修改设计书时，在「变更履历」段追加一行：

```markdown
### 变更履历
- 2026-05-01：初版创建
- 2026-05-06：修改 upload API 返回格式（加 url 字段）— 配合前端新组件
```

**规则：**
- 不覆盖已有履历，只追加
- 每次修改都要记，不管改动大小
- 记清楚「改了哪里 + 为什么改」
- 没有履历的设计书 → 说明这是新创建的，加一行「初版创建」

## CONTRACTS.md 同步

如果改了跨模块接口，必须同步更新 CONTRACTS.md：

```
1. 改了 API 路由、事件或公共函数接口 → 更新 CONTRACTS.md
2. 新增接口 → 在 CONTRACTS.md 添加新条目
3. 废弃接口 → 标记 deprecated
4. 改了接口定义 → 追加变更记录

如果 CONTRACTS.md 不存在 → 调用 tws-init 生成初始版本
```

## 架构决策上报

同步设计书时，检查"决策记录"段中的决策。如果某个决策满足以下任一条件，标记为待审核：

```
升级条件（满足任一）：
- 跨模块生效（不只影响当前功能）
- 影响后续开发方向（其他人需要知道）
- 排除了某个方案（避免以后重复讨论）

不升级（留在设计书）：
- 只影响当前功能的局部选择
- 不涉及其他模块的实现细节
```

**agent 只出报告，不直接写入。** 架构决策影响全局，必须由人类项目经理审核后才能记录。

报告汇总后交给项目经理。项目经理调用 `comp-arch-decision` skill 整理格式、检查重复/矛盾，确认后写入 `.tws/architecture-decisions.md`。报告格式和闭环机制见 `references/architecture-escalation.md`。

## 什么情况应该新写，什么情况应该修改

```
新写一本：
- 新增一个独立的模块/组件（之前没有对应的设计书）
- 新增一个跨功能的新能力
- 现有设计书已经严重过时，修改不如重写

修改现有：
- 在现有功能上做增量改动
- 改了参数、接口、行为但功能本质没变
- 修 Bug 后更新设计书中对应的逻辑描述

修改后必须更新变更履历（在 design-doc 模板的「变更履历」段追加一行）。
```

## 常见失败模式

| 表现 | 后果 |
|------|------|
| 「改动太小了，不用同步」 | 下一个人以为还是旧接口 → 写出 Bug |
| 「设计书没有，懒得建了」 | 代码成了孤岛，没人知道有这个模块 |
| 「我之后补」 | 之后忘了 → 设计书永久过时 |
| 「别人会同步的」 | 别人不知道你改了 |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「就改了一个字段名而已」 | 然后下一个人对着旧字段名调试了 3 小时 |
| 「下次改的时候一起同步」 | 下次不记得。现在就做 |
| 「设计书跟代码差不多，不用改」 | 差不多 = 不一样。精准同步 |
| 「这个模块没有设计书」 | 那就建一个，哪怕只有一段话 |

## 为什么重要

过时或缺失的设计书会误导后续开发；每次同步是维持设计书可信度的最低成本。
