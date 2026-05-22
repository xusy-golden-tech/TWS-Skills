---
name: team-subagent-dispatch
description: 子 agent 调度规则。什么任务需要拆子 agent、怎么拆、并发数限制、结果合并方式
---

<SUBAGENT-STOP>
This rule applies to agents that are considering splitting work into sub-agents. If you are already a sub-agent, do not recursively spawn further sub-agents without explicit approval.
</SUBAGENT-STOP>

# 子 Agent 调度规则

## 核心原则

**主 agent = 项目经理，子 agent = 执行者。**

主 agent 不写代码。子 agent 只执行不决策。

```
主 agent（项目经理）：
- 了解全貌（需求、设计、进度）
- 拆任务、排依赖、管进度
- 派子 agent 执行具体任务
- 派子 agent 审查执行结果（review 也交给子 agent）
- 收报告 → 决定下一步 → 推进度
- 不写代码、不亲自审查、不加载组件 skill

子 agent（执行者）：
- 每次领取一个具体任务
- 读对应的技能 → 执行 → 汇报
- 不决策、不规划、不改变任务范围
- 完成后上下文释放，不留残留
- 每次只加载 1-2 个组件 skill

子 agent（审查者）：
- 每次领取一个审查任务
- 只收到审查对象和客观背景
- 执行审查 → 输出报告
- 不接触执行者的对话历史
```

这是我们在 G-Assistant 修复项目里验证过的模式：妈妈（主 agent）管规划和验收，小念（子 agent）管执行。效果好。

---

## 什么时候拆

```
□ 任务超过 5 步或跨越 3 个以上模块？
□ 各部分没有强耦合（A 不依赖 B 的中间状态）？
□ 每个子任务有明确的完成标准和验证方式？
□ 上下文快满了？
→ 全部满足 → 考虑拆

不拆的情况：步骤少于 5 / 强依赖 / 改同一个文件
```

---

## 怎么拆

```
按模块拆（推荐）：Agent A → 后端，Agent B → 前端（条件：模块间只有接口依赖）
按关注点拆：Agent A → 业务逻辑，Agent B → 安全校验（条件：可独立验证）

禁止：
❌ 同一文件拆分 → 合并冲突
❌ 互相依赖且无接口契约 → 串行等
❌ 边界模糊 → 说明还没想清楚
```

---

## 并发数限制

| 模式 | 最大并发 | 说明 |
|------|---------|------|
| 单人开发 | 2 | 个人环境资源有限 |
| 团队开发 | 4 | 有 CI/CD 和环境支持 |
| 纯文档/调查类 | 4 | 不涉及代码冲突 |

**超出限制时排队执行，不新增子 agent。**

## 上下文管理策略

主 agent 和子 agent 按职责分别加载不同的 skill，不做全量加载：

```
主 agent 只加载——流程编排类：
  using-tws（入口）
  add-feature / fix-bug / ...（当前流程）
  task-breakdown、impact-assessment（核心组件）
  team-subagent-dispatch（子 agent 调度）
  checkpoint-reference（状态管理）
  约 4-6 个文件，~300-400 行

子 agent 只加载——具体执行类（每次一个任务）：
  design-doc（写设计书）
  OR implementation（编码）
  OR test（测试）
  OR design-sync（同步）
  每次只加载 1-2 个文件，~100-200 行
```

**规则：**
- 主 agent 不加载组件 skill（design-doc、implementation、test 等）
- 主 agent 派任务时，告诉子 agent「读 xxx skill，然后执行」
- 子 agent 完成任务后上下文释放，主 agent 只收取结果
- 这样设计书、编码、测试的叠加内容不会污染主会话上下文

---

## 子 Agent 隔离规则

审查子 agent 必须使用干净上下文启动：

- 使用 `context:"isolated"` 或等效隔离模式
- 不读记忆文件、不继承主 agent 对话历史
- 只提供审查对象（文件路径）和客观背景（项目技术栈、规约路径）
- 不提供引导性内容（"我觉得这里有问题"）

执行者子 agent 同样使用隔离上下文：

- 只提供任务描述、对应 skill 名称、规约路径
- 不读其他任务的执行结果

---

## 结果合并

```
每个子 agent 完成后：
1. 输出完成确认（完成任务描述中的哪些点）
2. 输出改动清单（改了哪些文件）
3. 输出遗留问题（哪些没做、有什么风险）

主 agent 在所有子 agent 完成后：
1. 检查各子任务的完成标准是否都满足
2. 检查是否有文件冲突（两个子 agent 改了一个文件）
3. 跑集成测试（验证各部分的接口是否对齐）
4. 汇总报告
```

---

## 审查结果的处理

**审查者的报告是输入，不是命令。**

主 agent 收到审查报告后，按 A/B/C 三类分类判断（A=必须修，B=主 agent 决定，C=跳过）。
连续多轮 B 类问题或报告重叠时，应主动叫停审查循环。

分类标准、叫停标准和失败处理详见 `found-review-triage/SKILL.md`。

---

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个任务太大，拆成子 agent 吧」 | 先确认是不是真的可以独立拆。强依赖的任务拆了反而慢 |
| 「多开几个子 agent 更快」 | 并发高不等于完成快。合并冲突的时间往往超过节省的时间 |
| 「子 agent 自己做自己的就行」 | 必须在开始时明确边界和接口，否则合不回来 |
| 「先拆开做，后面再对接口」 | 接口必须提前定。不对齐接口 = 白做 |
