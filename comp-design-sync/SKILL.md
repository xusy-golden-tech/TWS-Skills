---
name: design-sync
description: 设计书同步。每次改完代码后必须调用，确保设计书与代码一致。「不改设计书 = 代码没改完」
---

# 设计书同步

## 核心原则

**不改设计书 = 代码没改完。**

设计书同步不是编码完成后的可选项，不是「有空再做」的杂务，而是「完成任务」的必要条件。一份过时的设计书比没有设计书更危险——它会误导下一个人。

## Step 0: 图快照对比（自动差异检测）

在进入 Gate Function 之前，用代码图自动检测改了什么——diff 直接告诉你哪些符号变了，不用 agent 逐行去比对。

**前置检查：**

```
0.0 tws-graph --version 2>&1
    → 成功 → 继续 0.1
    → 失败（"command not found" 等）→ Bash: pip install -e tws-graph/ 2>&1
    → 安装成功 → 继续 0.1
    → 安装失败（找不到 tws-graph/ 目录或 pip 报错）→ 「tws-graph 不可用，退回到手动比对模式」

0.1 tws-graph index              ← 改后重新索引（生成最新 DB）

**前置条件：** 改代码之前，impact-assessment 流程中应该已经保存了改前快照（`tws-graph snapshot before`）。如果没有 → 回退到手动比对模式。

```
0.2 tws-graph diff before after
    输出直接告诉你：
    ├─ 新增符号（qualified_name + 文件:行号 + 可见性 + 签名）
    ├─ 删除符号
    ├─ 签名变更（旧签名 → 新签名）
    ├─ 新增调用关系
    └─ 断开的调用关系

0.3 如果 diff 输出 "(no differences)" → 跳到步骤 5（标记完成）
```

**hotfix / 紧急场景：**

```
使用 tws-graph diff before after --brief
→ 仅输出 "changed" 或 "unchanged"
→ 如果是 "changed"，仍然需要完整同步，但可延后
→ 在设计书「变更履历」追加一行 + session-state.md 标记「待补完整 design-sync」
```

→ 以上 diff 输出是「**事实层**」——告诉你改了什么
→ 以下 Gate Function 是「**判断层**」——判断需要同步哪个设计书

## The Gate Function

```
AFTER code changes are complete, BEFORE marking task done:

1. LIST all files you modified (from diff output: affected_files)
2. MATCH each affected file + changed symbol to its design doc
   - diff 签名变更 → 对应设计书「接口定义」需更新
   - diff 新增符号 → 是否需要创建新的设计书
   - diff 删除符号 → 是否需要在设计书中标记 deprecated
   - diff 调用关系变化 → 是否影响其他模块的设计书
3. SYNC: 根据 diff 结果逐项同步设计书
   - Mismatch → UPDATE design doc to match code
   - Missing → CREATE design doc
   - Deprecated → MARK as deprecated
4. VERIFY: re-read updated design doc — does it accurately describe the code?
   - 可选：再次运行 tws-graph diff 确认无差异
5. ONLY THEN: mark task as complete
```

## 轻量模式（hotfix 等紧急场景）

紧急修复时至少做到：在设计书「变更履历」追加一行。完整同步可延后，但必须在 session-state.md 标记「待补完整 design-sync」。

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

报告格式：

```
📌 检测到可升级为项目级的架构决策：

1. {决策标题}
   背景：{为什么需要做这个决策}
   决定：{选了什么}
   排除：{排除了什么}
   影响：{影响哪些模块}
   来源：设计书 {功能名}
```

报告汇总后交给项目经理。项目经理调用 `comp-arch-decision` skill 整理格式、检查重复/矛盾，确认后写入 `.tws/architecture-decisions.md`。

设计书的决策记录保留不动——它是"这个功能为什么这么做"的原始记录。

**闭环机制：**
- 主 agent 汇总所有任务的决策报告后，统一输出给用户
- 如果用户不立即处理，决策报告追加到 `.tws/deferred-issues.md`，标记为「待审核架构决策」
- 下次 using-tws 启动时，检查 deferred-issues.md 中的待审核决策，提醒用户处理

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
| 「diff 返回 changed 但改动太小了」 | diff 说 changed 就是 changed。即使一行也记变更履历 |
| 「diff 跑不了，没快照」 | 那就手动比对。下次记得在 impact-assessment 时拍快照 |

## 为什么重要

- 过时的设计书 → 误导人
- 缺失的设计书 → 看不懂
- 每次不同步 → 设计书群体会逐渐没人信 → 没人写 → 回到原始状态

**30 秒的同步，省的是 30 分钟的未来排查时间。**
