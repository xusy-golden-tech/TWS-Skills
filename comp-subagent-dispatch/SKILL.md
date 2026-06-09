---
name: comp-subagent-dispatch
description: 子 agent 调度规则。主 agent 不写代码，所有编码/测试/同步由子 agent 执行。这是 TWS 的核心机制
---

# 子 Agent 调度规则

## 核心原则

**主 agent = 项目经理，子 agent = 执行者。**

主 agent 不写代码。子 agent 只执行不决策。

> **递归限制**: 子 agent 不应再派子 agent。如果你是被派来执行具体任务的子 agent，你的职责是执行，不是拆分。如需进一步拆分，汇报给主 agent，由主 agent 决定。

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
- 通过 Skill 工具加载对应的 comp skill → 按指引执行 → 汇报
- 不决策、不规划、不改变任务范围
- 完成后上下文释放，不留残留
- 每次只加载 1-2 个组件 skill

子 agent（审查者）：
- 每次领取一个审查任务
- 只收到审查对象和客观背景
- 执行审查 → 输出报告
- 不接触执行者的对话历史
```

## 子 agent 类型

所有需要加载 comp skill 的子 agent 必须使用 **general-purpose** 类型：

```
✅ 正确：Agent(subagent_type: "general-purpose", prompt: "...Load comp-reproduce via Skill tool...")
❌ 错误：Agent(subagent_type: "Explore", prompt: "...investigate...")  ← Explore 没有 Skill 工具
```

主 agent 派子 agent 时的 prompt 模板：

```
你是一个执行子 agent。请完成以下任务：

1. 通过 Skill 工具加载 {comp-skill-name}（如 Skill(skill: "comp-reproduce")）
2. 按 skill 中的指引执行任务
3. 完成后汇报：做了什么、改了哪些文件、发现了什么

任务背景：{简要描述任务上下文}
```

禁止：
❌ 使用 Explore 类型派需要加载 skill 的子 agent
❌ 主 agent 自己写调查 prompt 替代 comp skill 的工作
❌ 在 prompt 中写"读 xxx skill"——必须是"通过 Skill 工具加载 xxx skill"

### 代码图查询任务补充

**所有涉及代码调查的子任务**，dispatch prompt 中必须同时要求子 agent 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")）。

覆盖以下所有 comp skill（这些 skill 的 Step 0 已强制要求加载 `found-tws-graph-usage`）：

| 子任务 | comp skill | 图查询目的 |
|--------|-----------|-----------|
| 复现 | comp-reproduce | 定位相关符号、追踪调用链 |
| 编码 | comp-implementation | 理解现有结构、确认影响范围 |
| 代码审查 | comp-code-review | 验证接线完整性(D6)、架构健康度(D7) |
| 测试 | comp-test | 确认回归测试范围 |
| 后端编码(Python) | comp-backend-impl-python | 理解现有结构 |
| 后端编码(Java) | comp-backend-impl-java | 理解现有结构 |
| 前端编码 | comp-frontend-impl | 理解现有组件结构 |
| 目标验证 | comp-goal-verify | 验证接线性(L3) |
| 迁移计划 | comp-migration-plan | 分析依赖关系 |
| 后端测试 | comp-backend-test | 定位待测符号 |
| 前端测试 | comp-frontend-test | 定位待测组件 |
| 影响评估 | comp-impact-assessment | 查影响范围 |
| 设计同步 | comp-design-sync | 对比 before/after 快照 |
| 根因分析 | comp-root-cause-analysis | 追踪调用链 |

dispatch prompt 模板：

```
你是一个执行子 agent。请完成以下任务：

1. 通过 Skill 工具加载 {comp-skill-name}（Skill(skill: "{comp-skill-name}")）和 found-tws-graph-usage（Skill(skill: "found-tws-graph-usage")）
2. 按 skill 中的指引执行任务
3. 完成后汇报：做了什么、改了哪些文件、发现了什么

任务背景：{简要描述任务上下文}
```

原因：comp skill 的 Step 0 描述「查什么」，found-tws-graph-usage 提供「怎么查」的正确命令语法，防止 agent 编造不存在的命令。两个 skill 缺一不可。

主 agent 管规划和验收，子 agent 管执行。两者职责明确分离，避免主 agent 上下文膨胀。

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

| 场景 | 最大并发 | 说明 |
|------|---------|------|
| 代码改动 | 2 | 避免文件冲突 |
| 纯文档/调查类 | 4 | 不涉及代码冲突 |

**超出限制时排队执行，不新增子 agent。**

## 上下文管理策略

主 agent 和子 agent 按职责分别加载不同的 skill，不做全量加载：

```
主 agent 只加载——流程编排类：
  using-tws（入口）
  add-feature / fix-bug / ...（当前流程）
  task-breakdown、impact-assessment（核心组件）
  comp-subagent-dispatch（子 agent 调度）
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
- 主 agent 派任务时，使用 general-purpose 类型，prompt 中明确写「通过 Skill 工具加载 xxx skill，然后按指引执行」
- 子 agent 完成任务后上下文释放，主 agent 只收取结果
- 这样设计书、编码、测试的叠加内容不会污染主会话上下文

## 读深度规则

主 agent 派子 agent 时，在 prompt 中明确指定需要读的内容范围。子 agent 必须遵守以下规则：

```
1. 设计书：只读「涉及模块」「接口变更」「验收标准」三节。不要读全文。
   - 如果需要了解上下文，先读这三节，按需再读其他节。
   - 禁止：Read 整个设计书后再决定哪些相关。

2. 代码文件：优先用 tws-graph search 定位符号（直接给文件:行号），再用 Read + offset/limit 读相关段落。
   - tws-graph 不可用或返回空时，回退到 Grep 定位。
   - 禁止：Read 整个 500 行的文件"为了了解上下文"。

3. 设计书/文档中引用的其他文件：除非任务直接涉及，否则不读。
   - 如果任务只改模块 A，不要读模块 B 的代码"为了理解接口"——设计书的接口变更节已经有你需要的信息。

4. 规约文件：只读当前任务相关的部分。
   - 编码任务 → 读编码规约
   - 测试任务 → 读测试规约
   - 禁止：一次读完所有规约文件。
```

**执行方式**：主 agent 在 prompt 模板的「任务背景」之后追加一行读深度指引。例如：
「只读设计书的「涉及模块」「接口变更」「验收标准」三节。代码调查优先用 tws-graph 定位符号，不可用时回退 Grep。」

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

0. 更新 session 文件（用 Edit 工具）：
   a. 读取 .tws/sessions/{当前流程文件}
   b. 将对应步骤 [ ] 改为 [x]
   c. 更新"当前任务"为下一步骤
   d. 版本号 +1
   e. Edit 写回

0a. git commit 保存进度（主 agent 执行）：
   a. git add 子 agent 改动的文件
   b. git commit -m "step: {步骤名}"（不 push）
   c. 目的：防止后续子 agent 误操作回滚已验收的改动
   d. 禁止：git push、git commit --amend、git reset

1. 输出完成确认（完成任务描述中的哪些点）
2. 输出改动清单（改了哪些文件）
3. 输出遗留问题（哪些没做、有什么风险）

主 agent 在所有子 agent 完成后：
1. 检查各子任务的完成标准是否都满足
2. 检查是否有文件冲突（两个子 agent 改了一个文件）
3. 跑集成测试（验证各部分的接口是否对齐）
4. 汇总报告
```

### commit 粒度

- **每个独立子任务完成后提交一次**——不是每个小 edit，也不是全部做完才提交
- commit message 格式：`{type}: {简短描述}`（如 `feat: add RemoteModeConfig`、`test: add SidecarProxy tests`）
- 不 push——只在本地积累，全部完成后由用户决定何时 push
- 如果子任务改动被后续子 agent 回滚，可以用 `git diff` 找出丢失的改动恢复

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
