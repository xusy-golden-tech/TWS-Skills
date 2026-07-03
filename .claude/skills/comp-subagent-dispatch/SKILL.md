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

1. 通过 Skill 工具加载 found-tws-graph-usage（Skill(skill: "found-tws-graph-usage")）和 {comp-skill-name}（Skill(skill: "{comp-skill-name}")）
2. 按 skill 中的指引执行任务。代码调查必须遵循以下优先级（不可协商）：
   a. 第一步：tws-graph search <关键符号>（查定义和位置）
   b. 第二步：tws-graph calls / impact / trace（查关系和影响）
   c. 第三步：仅在 a/b 无结果或返回 [internal] 时，才回退到 Grep
   跳过 a 直接 Grep = 方向性错误，将被判定为任务不合格
3. 完成后汇报：做了什么、改了哪些文件、发现了什么
4. 如果提供了 goal-spec 文件路径，在任务完成后立即用 Edit 更新该文件：
   a. 先 Read goal-spec 文件，找到当前阶段
   b. 勾选已完成的门禁条件（- [x]）
   c. 勾选已通过的测试用例（- [x]）
   d. 勾选已满足的验收标准（- [x]）
   e. 将阶段状态更新为 ✅ 已完成（如全部验收通过）
   f. 在执行日志区追加一条记录
   g. 如果发现阻塞 Bug，追加到「阻塞级 Bug」区

任务背景：{简要描述任务上下文}
{goal-spec 文件路径（如有）：.tws/goal-specs/xxx.md}
```

**强制规则（不可协商）：所有子 agent dispatch prompt 必须同时包含 found-tws-graph-usage + comp skill，缺一不可。遗漏 found-tws-graph-usage 的 dispatch = 主 agent 失职。主 agent 自身也应在每次规划前加载 found-tws-graph-usage。**

原因：comp skill 描述「查什么」，found-tws-graph-usage 提供「怎么查」的正确命令语法，防止 agent 编造不存在的命令。

禁止：
❌ 使用 Explore 类型派需要加载 skill 的子 agent
❌ 主 agent 自己写调查 prompt 替代 comp skill 的工作
❌ 在 prompt 中写"读 xxx skill"——必须是"通过 Skill 工具加载 xxx skill"
❌ dispatch prompt 中遗漏 found-tws-graph-usage —— 这是子 agent 默认 grep 的根因
❌ Goal Mode 下收子 agent 报告后跳过 goal-spec 更新 —— spec 过期 = 后续子 agent 拿到错误前置状态，导致任务错乱

### 代码图查询任务补充

以下所有 comp skill 涉及的代码调查任务，统一使用上述主模板（已内置 found-tws-graph-usage）：

| 子任务 | comp skill | 图查询目的 |
|--------|-----------|-----------|
| 复现 | comp-reproduce | 定位相关符号、追踪调用链 |
| 编码 | comp-implementation | 理解现有结构、确认影响范围 |
| 代码审查(Plan) | comp-agentic-review-core | 分析 diff，产出 risk_zones 审查计划 |
| 代码审查(Main Loop) | comp-agentic-review-core | 基于 Plan 用 CodeGraph 深度调查，确认 issues |
| 代码审查(Filter) | comp-agentic-review-core | 干净上下文过滤误报 |
| 代码审查(编排) | comp-code-review | 编排三阶段并行 spawn + 汇总 + Triage |
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
| 代码审查 | 5 | 审查只读，不修改代码，可用更高并发 |
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
  found-tws-graph-usage（代码图使用指南，必加载） + design-doc（写设计书）
  OR found-tws-graph-usage（必加载） + implementation（编码）
  OR found-tws-graph-usage（必加载） + test（测试）
  OR found-tws-graph-usage（必加载） + design-sync（同步）
  每次只加载 2 个文件，~200-300 行
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
   - 单次 Read 上限 80 行，超过用 offset/limit 分段读。
   - 同一文件最多 Read 2 次。第 3 次起必须在汇报中说明理由。
   - 禁止无 offset 参数 Read 整个文件。未知文件先用 Read limit=1 看行数。

3. 设计书/文档中引用的其他文件：除非任务直接涉及，否则不读。
   - 如果任务只改模块 A，不要读模块 B 的代码"为了理解接口"——设计书的接口变更节已经有你需要的信息。

4. 规约文件：只读当前任务相关的部分。
   - 编码任务 → 读编码规约
   - 测试任务 → 读测试规约
   - 禁止：一次读完所有规约文件。

5. ⚠️ Grep 硬限制（不可分割的固定前缀 — 任何 dispatch prompt 必须逐字包含以下三段，不允许简化、不允许改写、不允许遗漏）：
   a. 查已知符号（类名、方法名、函数名、SQL 表、HCL 资源、YAML 键等）→ 必须先跑 tws-graph search，禁止跳过直接 Grep
   b. Grep 仅在以下情况允许：① tws-graph 返回空结果（连续 2 次） ② 目标在 tws-graph 非索引文件类型中（XML/.gradle/图片/二进制等） ③ 搜索目标为字面字符串/正则模式，非已知符号名
   c. 每次发起 Grep 调用前，必须在思考（thinking）中写一句明确理由：为什么 tws-graph 不适用于本次搜索。无理由的 Grep = 违规
   — 违反以上任一条 = 方向性错误，等同于编造不存在的命令。主 agent 收到子 agent 汇报时，必须检查自审计统计中的 Grep 调用次数，如有 Grep 但无对应理由说明 → 判定该子 agent 执行不合格

6. 主 agent 派发检查 — 主 agent 在发出 dispatch prompt 前必须自查：
   - [ ] dispatch prompt 中是否包含「通过 Skill 工具加载 found-tws-graph-usage」字样
   - [ ] dispatch prompt 中是否包含了「Grep 硬限制」的 a/b/c 三条
   - [ ] dispatch prompt 是否指定了 goal-spec 文件路径（Goal Mode 下）
   以上三条缺一不可。缺少任一条 → 不允许派发，先补齐 prompt

7. 自审计 — 完成后汇报末尾必须追加以下统计：
   - Read 调用次数 / 涉及不同文件数
   - Grep 调用次数
   - tws-graph 命令调用次数
   - 如有文件被 Read 超过 2 次：逐文件列明原因
```

**执行方式**：主 agent 在 prompt 模板的「任务背景」之后追加一行读深度指引。例如：
「只读设计书的「涉及模块」「接口变更」「验收标准」三节。代码调查优先用 tws-graph 定位符号，不可用时回退 Grep。单次 Read ≤80 行，同文件 ≤2 次。」

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

0b. 更新 goal-spec 文件（Goal Mode 专用，主 agent 执行）：
   a. 读取 .tws/goal-specs/{name}.md
   b. 根据子 agent 汇报，勾选对应阶段的：
      - 已完成的门禁条件
      - 已通过的测试用例
      - 已满足的验收标准
   c. 如全部验收通过 → 阶段状态改为 ✅ 已完成，追加执行日志
   d. 如发现阻塞 Bug → 追加到「阻塞级 Bug」区
   e. Edit 写回
   f. ⚠️ 此步骤不可跳过。spec 文件是 goal mode 的外脑，过期 = 迷路。必须在收报告后立即更新，不攒到阶段结束

0c. git commit 保存进度（主 agent 执行）：
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
4. 验证 goal-spec 文件中所有阶段的验收标准均已勾选（Goal Mode）
5. 汇总报告
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
| 「子 agent 做完了一起更新 spec」 | 子 agent 上下文每次释放，攒到后面 = 靠记忆更新 = 漏项 + 错乱。收一个报告更新一次 |
| 「先拆开做，后面再对接口」 | 接口必须提前定。不对齐接口 = 白做 |
