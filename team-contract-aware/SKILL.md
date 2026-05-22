---
name: team-contract-aware
description: CONTRACTS.md 契约感知。团队模式下生效——改接口前查消费者、改完后更新契约。没有 CONTRACTS.md 时跳过
---

<SUBAGENT-STOP>
Skip this skill if CONTRACTS.md does not exist in the project root.
</SUBAGENT-STOP>

# 契约感知

## 核心原则

**如果别人依赖于你改的东西，你必须知道他们是谁。**

CONTRACTS.md 不是可选的文档，是团队协作的「红绿灯」。没有它，每个人的改动都是盲改。

## The Gate Function

### 开工前（必做）

```
Before starting any task:
1. git checkout main && git pull
2. 读 CONTRACTS.md → 检查你依赖的接口是否有变更
3. 有变更 → 通知开发者：「{你的模块} 依赖的 {接口} 上周被改了，建议回测」
4. git checkout -b {type}/{描述}
```

### 改接口时（必做）

```
When modifying an interface that appears in CONTRACTS.md:
1. 搜索该接口
2. 找出所有消费者模块
3. 输出：⚠️ 「{接口} 被 {模块A, 模块B} 使用」
4. 更新接口定义
5. 在变更记录追加一行：{YYYY-MM-DD}: {变更内容}
6. PR 描述 + 标注影响范围
```

### 禁止

```
❌ 改了接口但不更新 CONTRACTS.md
❌ 改了接口但不说（不通知消费者）
❌ 「我确定没人用这个接口」→ 去 CONTRACTS.md 查，查不到再说
```

## 常见失败模式

| 表现 | 后果 |
|------|------|
| 改了接口没更新 CONTRACTS.md | 别人合并时才发现不兼容 → 冲突 |
| 改之前没查消费者 | 别人的功能悄无声息地坏了 |
| 「这个接口没人用了」 | 没查过就别假设。查了再说 |
| 改了但没写变更记录 | 一周后：谁改的？改了什么？→ 不知道 |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个接口只有我自己在用」 | 查 CONTRACTS.md 确认，不是猜 |
| 「改动向后兼容，不用通知」 | 兼容也要通知。消费者需要知道有新增 |
| 「我跟团队说过了」 | 口头说了 ≠ 更新了契约。写下来 |
| 「这个不算接口变更」 | 参数、返回值、行为，改了任何一项都算 |
| 「先改，等上线了再补契约」 | 上线前没补 = 永远不补 |

## 为什么重要

- 没有契约意识 = 多人修改一个接口 = 谁改谁炸
- CONTRACTS.md 是唯一的「谁依赖什么」的记录
- 不查消费者就改接口 ≈ 拆承重墙不看图纸
