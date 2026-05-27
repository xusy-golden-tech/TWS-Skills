---
name: flow-hotfix
description: 紧急热修复流程。生产环境 Bug 快速修复用。阻塞路径只做定位 → 修复 → 验证 → 同步；完整审查作为事后补齐项
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 热修复

## 流程

```
① 定位 → ② 修复 → ③ 验证 → ④ 同步
```

相比 `fix-bug` 流程，阻塞路径跳过：方案确认、防复燃分析、集成测试、完整代码审查。

这些不是永久省略，而是进入「后续事项」补齐；热修复完成的最低条件是止血、验证和最小同步。

**仅用于生产环境紧急修复。日常修复用 `fix-bug`。**

---

## ① 定位

子 agent 通过 Skill 工具加载 `comp-root-cause-analysis`（快速版本）

```
1. 快速定位出错位置
2. 确认改动范围最小化
3. 一次改完，不涉及其它
```

## ② 修复

子 agent 通过 Skill 工具加载 `comp-implementation`（最小改动模式）

```
1. 最小改动原则——只修出问题的代码，不改其它
2. 不重构，不优化，不顺手改别的
```

## ③ 验证

子 agent 通过 Skill 工具加载 `comp-test`（核心回归）

```
1. 确认 Bug 不再复现
2. 跑核心回归测试
```

## ④ 同步

子 agent 通过 Skill 工具加载 `comp-design-sync`（精简版）

```
1. 标记 hotfix 分支（分支名：hotfix/{描述}）
2. 备注紧急修复原因
3. 必须同步设计书——至少在设计书的变更履历追加一行
4. 在 `.tws/sessions/{当前流程文件}` 中标记「待补完整 design-sync」
```

## Closeout

删除 session 前，若存在「待补完整 design-sync」或其他待补事项，必须先转写到 `.tws/deferred-issues.md`，或取得用户确认的处理方式。

## 完成依据

标准完成依据见根目录 `checkpoint-reference.md`。

## 后续事项

```
hotfix 是应急处理，架构质量不是此时的优先级。
但修复完成后，建议在非紧急时安排一次完整的 code review（加载 `comp-code-review`，含 D7 架构健康度检查），
确认紧急修复没有引入技术债。如果引入了，走 fix-bug 流程补修。
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「热修就是越快越好，不用记录」 | 热修更需要留下原因和后续补齐项 |
| 「能止血就完成」 | 完成还包括验证和最小设计同步 |
| 「紧急时不用回头看」 | 热修后的补审查是防止技术债固化 |
