---
name: flow-hotfix
description: 紧急热修复流程。生产环境 Bug 快速修复用。跳过讨论、plan、review，直接定位 → 修复 → 验证 → 同步
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 热修复

## 流程

```
① 定位 → ② 修复 → ③ 验证 → ④ 同步
```

相比 `fix-bug` 流程，跳过了：discuss、方案确认、防复燃分析、集成测试。

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
4. 在 session-state.md 中标记「待补完整 design-sync」
```

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
