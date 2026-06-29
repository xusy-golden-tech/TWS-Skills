---
name: code-review
description: 代码审查编排层。Impact Assessment → 三阶段并行 spawn(Plan→Main Loop→Filter) → 汇总 → Triage → Design-Sync
---

# 代码审查编排层

你是主 agent。按以下步骤编排代码审查流程。**你不做具体审查——你只派子 agent。**

## 架构

```
主 agent（编排层，你在这里）
  │
  ├─ Step 0: Impact Assessment（spawn 子 agent → 影响面报告）
  ├─ Step 1: Scope Determination（风险分级，确定审查文件列表）
  │
  ├─ Step 2: Phase 1 — Plan（并行，每文件一个子 agent）
  ├─ Step 3: Phase 2 — Main Loop（并行，每文件一个子 agent）
  ├─ Step 4: Phase 3 — Filter（并行，每文件一个子 agent）
  │
  ├─ Step 5: Aggregation（汇总、去重、交叉引用）
  ├─ Step 6: Triage（A/B/C 分类）
  └─ Step 7: Design-Sync（条件触发）
```

三个 Phase 子 agent 都是只读的——不修改代码。每个 Phase 内部文件间并行 spawn，Phase 之间串行（等上一阶段全部完成再进入下一阶段）。

---

## Step 0: Impact Assessment

派子 agent 加载 `comp-impact-assessment`，获取影响面报告：
- 变更文件列表
- 风险分级（HIGH / MEDIUM / LOW）
- 接口变更摘要
- 消费者模块

## Step 1: Scope Determination

基于 Step 0 报告分类：

| 风险等级 | 处理方式 |
|---------|---------|
| HIGH | 完整三阶段审查 |
| MEDIUM | 完整三阶段审查 |
| LOW | 轻量检查（主 agent 30 秒扫一眼，不进入三阶段） |

输出：审查文件列表（只含 HIGH + MEDIUM）。

## Step 2: Phase 1 — Plan（并行）

每文件 spawn 一个 Plan 子 agent。使用 `general-purpose` 类型，**干净上下文**。

并发控制：≤5 文件全并发；6-10 → 5 并发分批；11+ → 5 并发多批次。

### Spawn Prompt 模板

```
你是审查 Plan 子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
通过 Skill 工具加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")），执行 Phase 1: Plan。

== 输入 ==
文件路径：{file_path}
Diff 内容：
{diff}
影响面摘要（仅本文件相关）：
{impact_excerpt}
审查维度：
D1: 逻辑正确性 — 分支覆盖、空值处理、错误路径、循环终止
D2: 测试覆盖 — 公共接口测试、边界条件、断言质量
D3: 设计书同步 — 接口签名一致、行为文档化
D4: 规约遵守 — 编码规范（{conventions_path}）
D5: 安全性 — 输入验证、注入风险、权限检查
D6: 接线完整性 — 函数调用、API 路由、事件订阅
D7: 架构健康度 — 耦合、分层、维护性

== 要求 ==
按 comp-agentic-review-core 中 Phase 1 的完整指引执行。输出审查计划 JSON。
```

### 收齐后

收集所有 plan JSON。收到 `{"skip_plan": true}` 的文件在 Phase 2 中将 `plan_json` 设为 `null`。写 checkpoint 到 session 文件。

## Step 3: Phase 2 — Main Loop（并行）

每文件 spawn 一个 Main Loop 子 agent。并发控制同上。

### Spawn Prompt 模板

```
你是审查调查子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
1. 通过 Skill 工具加载 found-tws-graph-usage（Skill(skill: "found-tws-graph-usage")）
2. 通过 Skill 工具加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")）
3. 执行 Phase 2: Main Loop

== 输入 ==
文件路径：{file_path}
Diff 内容：
{diff}
审查计划（Plan 阶段输出）：
{plan_json}
影响面摘要（仅本文件相关）：
{impact_excerpt}
审查维度：D1-D7（同 Phase 1）
规约路径：{conventions_path}

== 要求 ==
按 comp-agentic-review-core 中 Phase 2 的完整指引执行。使用 CodeGraph 调查每个 risk_zone。只报告已确认的问题。输出 issues JSON。
```

### 收齐后

收集所有 issues + evidence。写 checkpoint。

## Step 4: Phase 3 — Filter（并行）

每文件 spawn 一个 Filter 子 agent。**干净上下文：只给 diff + issues，不给 plan/impact/checklist。**

并发控制同上。

### Spawn Prompt 模板

```
你是审查 Filter 子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
通过 Skill 工具加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")），执行 Phase 3: Filter。

== 输入 ==
文件路径：{file_path}
Diff 内容：
{diff}
审查发现（Main Loop 阶段输出）：
{issues_json}

== 要求 ==
按 comp-agentic-review-core 中 Phase 3 的完整指引执行。用 diff 证伪误报。只输出要删除的 issue ID 数组。不要加载其他 skill，不要执行命令，不要读取文件。
```

### 收齐后

应用删除：`issues_final = issues - filter_removed`。写 checkpoint。

## Step 5: Aggregation

- 合并所有文件最终 issues
- 去重（相同 content 的 issue 合并）
- 按 severity 排序（high → medium → low）
- 按 dimension 分组

## Step 6: Triage

引用 `found-review-triage` 进行 A/B/C 分类：
- A 类：必须修（安全漏洞、数据丢失、功能异常）
- B 类：建议修（可维护性、边界条件、性能）
- C 类：可跳过（风格、命名，不违反规约）

连续多轮 B 类问题或报告重叠时，按 found-review-triage 的叫停规则处理。

## Step 7: Design-Sync（条件触发）

D3 问题含 medium+ 级别 → spawn design-sync 子 agent（加载 `comp-design-sync`）。

---

## 并发控制

| 审查文件数 | 每阶段最大并发 | 说明 |
|-----------|-------------|------|
| ≤5 | 全并发 | 一次性 spawn |
| 6-10 | 5 | 分批，第一批完成后 spawn 剩余 |
| 11+ | 5 | 多批次，批次间写 checkpoint |

审查子 agent 是只读的，可用比代码编写子 agent 更高的并发。

## Checkpoint 格式

在 session 文件中追加：

```markdown
### Phase 1: Plan
| 文件 | 状态 | risk_zones |
|------|------|-----------|
| auth.py | ✅ done | 3 (1H, 1M, 1L) |
| session.py | ✅ done | skip (small diff) |

### Phase 2: Main Loop
| 文件 | 状态 | issues |
|------|------|--------|
| auth.py | ✅ done | 3 (2H, 1M) |
| session.py | 🔄 running | - |

### Phase 3: Filter
| 文件 | 状态 | removed |
|------|------|---------|
| auth.py | ✅ done | ["c-1"] |
```

## 审查报告格式

```markdown
## 代码审查报告

### 审查对象
{文件列表}

### 发现
| # | 维度 | 严重性 | 分类 | 描述 | 位置 |
|---|------|--------|------|------|------|
| 1 | D5 | high | A | {问题} | {文件:行号} |

### 结论
- A 类：{N} 个（必须修）
- B 类：{N} 个（建议修）
- C 类：{N} 个（可跳过）
- 总体：通过 / 有条件通过 / 阻塞
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「文件少，Phase 并行省不了多少时间」 | 并行后审查时长 = 最慢文件的时间，不是 N 个文件之和 |
| 「Filter 也给它看看 plan 吧」 | Filter 只看 diff 才能客观判断误报，不被调查过程污染 |
| 「这个 LOW 风险文件也走三阶段吧」 | LOW 文件主 agent 扫一眼就够了，三阶段有开销 |
| 「Plan 输出解析不了，我手动看看 diff 代替」 | 设 plan_json=null，让 Main Loop 自主调查，不要手动替代 |
| 「三阶段太慢了，合并成一步吧」 | 拆开的目的是 checkpoint + 阶段间质量控制，合起来会漂移 |
