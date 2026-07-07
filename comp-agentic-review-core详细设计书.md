# comp-agentic-review-core 详细设计书

> 2026-06-29 | 基于 TWS-CodeReview模块设计书 v2 + open-code-review 源码分析
>
> 版本: v3（可实现级） | 输入: Plan 模式多轮讨论收敛

---

## 〇、配置参数

以下参数在主 agent 加载 `comp-code-review` 时可根据场景覆盖，不做硬编码。默认值参考 open-code-review 设定，部分按 TWS 实际情况调整。

| 参数 | 默认值 | OCR 原始值 | 说明 |
|------|--------|-----------|------|
| `plan_mode_line_threshold` | **10** | 50 | diff 变更行数低于此值跳过 Plan 阶段。TWS 取 10 而非 OCR 的 50：Plan 是一次纯思考 LLM 调用，成本极低，即使 5 行改动也可能是关键逻辑。阈值设小，大部分 diff 都走 Plan |
| `max_tool_request_rounds` | **30** | 30 | Main Loop 最大工具调用轮数。与 OCR 一致 |
| `max_concurrency` | **5** | 8 | 每阶段最大并行子 agent 数。OCR 默认 8，TWS 先设 5 后续按 API 配额调整（部分 API 支持 500+ 并发） |
| `token_soft_threshold` | **0.60** | 0.60 | 上下文使用率达此比例触发背景摘要。与 OCR 一致 |
| `token_warning_threshold` | **0.80** | 0.80 | 上下文使用率达此比例触发立即摘要。与 OCR 一致 |
| `max_diff_lines` | **500** | - | diff 超过此行数时 Plan 阶段标注"仅分析关键路径" |
| `max_file_read_lines` | **100** | - | Main Loop 单次 Read 最大行数 |

> **配置方式**：编排层 `comp-code-review/SKILL.md` 中声明参数及默认值。主 agent 在 spawn prompt 中代入当前值。后续可迁移至 `.tws/review-config.json`。

---

## 一、架构总览

### 1.1 两层 + 三阶段并行模型

```
主 agent（编排层，加载 comp-code-review）
  │
  ├─ Step 0: Impact Assessment（spawn 子 agent → 影响面报告）
  ├─ Step 1: Scope Determination（风险分级，确定审查文件列表）
  │
  ├─ Step 2: Phase 1 — Plan（并行，每文件一个子 agent）
  │   ┌─────────────────────────────────────────┐
  │   │ sub-agent(file=a.py, phase=plan)        │
  │   │ sub-agent(file=b.py, phase=plan)        │ 同时 spawn
  │   │ sub-agent(file=c.py, phase=plan)        │
  │   └─────────────────────────────────────────┘
  │   → 收齐所有 plan JSON → 写 checkpoint
  │
  ├─ Step 3: Phase 2 — Main Loop（并行，每文件一个子 agent）
  │   ┌─────────────────────────────────────────┐
  │   │ sub-agent(file=a.py, phase=main,        │
  │   │           plan=plan_a)                  │
  │   │ sub-agent(file=b.py, phase=main,        │ 同时 spawn
  │   │           plan=plan_b)                  │
  │   │ sub-agent(file=c.py, phase=main,        │
  │   │           plan=plan_c)                  │
  │   └─────────────────────────────────────────┘
  │   → 收齐所有 issues + evidence → 写 checkpoint
  │
  ├─ Step 4: Phase 3 — Filter（并行，每文件一个子 agent）
  │   ┌─────────────────────────────────────────┐
  │   │ sub-agent(file=a.py, phase=filter,      │
  │   │           diff + issues_a)              │ 干净上下文
  │   │ sub-agent(file=b.py, phase=filter,      │ 只有 diff+issues
  │   │           diff + issues_b)              │
  │   │ sub-agent(file=c.py, phase=filter,      │
  │   │           diff + issues_c)              │
  │   └─────────────────────────────────────────┘
  │   → 应用 filter 删除 → 最终报告
  │
  ├─ Step 5: Aggregation（汇总、去重、交叉引用）
  ├─ Step 6: Triage（A/B/C 分类）
  └─ Step 7: Design-Sync（条件触发）
```

### 1.2 关键设计决策

| 决策 | 理由 |
|------|------|
| Plan/Main/Filter 拆成三个独立子 agent | 避免单 agent 长流程偏移。每个阶段短且聚焦，阶段间可 checkpoint 恢复 |
| 同阶段内所有文件并行 spawn | 最大化并发，审查时长 = 最慢那个文件的时间 |
| Filter 子 agent 只拿 diff + issues | 与 open-code-review 一致：filter agent 不被调查过程污染，只看 diff 证伪 |
| CodeGraph 替代 grep | 预建依赖图，结构化查询比文本搜索强一个维度 |
| 小 diff 跳过 Plan | change_lines < 50 时不值得单独规划，直接进入 Main Loop |

---

## 二、子 agent 详细设计

### 2.1 Plan 子 agent

**定位**：分析 diff，识别风险区，产出结构化审查计划。**纯思考，不调工具。**

#### 输入

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `file_path` | string | main agent | 审查文件路径 |
| `diff` | string | main agent | 统一 diff 格式 |
| `impact_excerpt` | string | impact-assessment 报告 | 仅提取与该文件相关的部分 |
| `checklist` | markdown | comp-code-review | D1-D7 审查维度 |
| `conventions_path` | string | main agent | .tws/ 规约文件路径 |

#### 跳过条件

当 `diff` 中 insertions + deletions < `plan_mode_line_threshold`（默认 10）时，跳过 Plan 阶段，输出：

```json
{"skip_plan": true, "reason": "diff too small for plan overhead"}
```

main agent 收到 skip 后，在 Phase 2 的 spawn prompt 中将 `plan_json` 替换为 `null`，Main Loop 子 agent 自行决定调查策略。

#### 执行流程

```
1. 读取 diff，理解变更内容
2. 逐段分析：
   a. 新增代码 → 检查：是否正确处理了所有分支？是否有安全检查？是否符合规约？
   b. 修改代码 → 检查：修改是否影响到调用方？签名是否变化？旧逻辑是否完全替换？
   c. 删除代码 → 仅作上下文参考，不报告问题
3. 识别 risk_zones，每个 zone 包含：
   - 问题位置（推测的行号范围）
   - 严重程度预判
   - 需要什么 CodeGraph 查询来确认
4. 按 severity 排序（high → medium → low）
5. 输出 JSON
```

#### 输出格式

```json
{
  "file": "auth.py",
  "change_summary": "修改了 login() 函数签名，增加了 refresh() 函数。login() 新增 max_retry 参数，refresh() 是纯新增的 token 刷新逻辑。",
  "risk_zones": [
    {
      "id": "rz-0",
      "severity": "high",
      "description": "login() 签名新增 max_retry 参数，需确认所有调用方已更新或参数有默认值",
      "location_hint": "auth.py:45-52",
      "tool_guidance": [
        "tws-graph calls login --inbound",
        "tws-graph impact login"
      ]
    },
    {
      "id": "rz-1",
      "severity": "medium",
      "description": "refresh() 中 token 操作涉及安全边界，需确认无注入或泄露风险",
      "location_hint": "auth.py:78-95",
      "tool_guidance": [
        "tws-graph taint",
        "tws-graph calls refresh --outbound --depth 2"
      ]
    }
  ]
}
```

**字段说明：**

| 字段 | 必填 | 说明 |
|------|------|------|
| `file` | 是 | 审查文件路径 |
| `change_summary` | 是 | 1-3 句中文描述变更内容 |
| `risk_zones` | 是 | 数组，可为空（没有发现风险） |
| `risk_zones[].id` | 是 | 唯一标识，格式 `rz-{seq}` |
| `risk_zones[].severity` | 是 | high / medium / low |
| `risk_zones[].description` | 是 | 问题是什么 + 为什么是风险 |
| `risk_zones[].location_hint` | 是 | 推测的行号范围（可被 Main Loop 纠正） |
| `risk_zones[].tool_guidance` | 是 | CodeGraph 命令列表，给 Main Loop 的调查指引 |

**severity 定义：**

| 级别 | 定义 | 示例 |
|------|------|------|
| high | 安全漏洞、数据丢失、崩溃、关键功能异常 | SQL 注入、空指针、签名变更未同步 |
| medium | 性能退化、可维护性问题、边界条件遗漏 | 缺少错误处理、不必要的耦合、性能隐患 |
| low | 风格问题、命名不规范（仅在违反规约时） | 变量命名不一致、缺少注释 |

**约束：**
- 禁止调用任何工具（Bash/Read/Grep 均不可用）
- 禁止读取文件系统中的任何文件
- 只能基于 diff 内容分析
- 输出必须是纯 JSON（用 ```json 围栏包裹），无其他文字

---

### 2.2 Main Loop 子 agent

**定位**：基于 Plan 的调查指引，使用 CodeGraph + 文件读取进行深度调查，确认问题并收集证据。**工具使用循环。**

#### 输入

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `file_path` | string | main agent | 审查文件路径 |
| `diff` | string | main agent | 统一 diff 格式 |
| `plan_json` | object 或 null | Phase 1 输出 | Plan 的 risk_zones；为 null 时自主决定调查策略 |
| `impact_excerpt` | string | impact-assessment | 仅该文件相关的影响面 |
| `checklist` | markdown | comp-code-review | D1-D7 |
| `conventions_path` | string | main agent | 规约文件路径 |

#### 可用工具

| 工具 | 用途 | 约束 |
|------|------|------|
| `Skill` | 加载 `found-tws-graph-usage` | 第一轮必须调用 |
| `Bash` | 执行 tws-graph 命令 | 仅限 tws-graph 命令 |
| `Read` | 读取文件片段 | 必须先 CodeGraph 定位 → offset/limit ≤100 行 |
| `Grep` | 文本搜索回退 | 仅在 CodeGraph 返回空时使用 |

#### CodeGraph 调查模式清单

```
D1 逻辑正确性:
  tws-graph calls <symbol> --inbound       ← 谁调用了这个函数？调用方式正确吗？
  tws-graph trace <caller> <symbol>        ← 调用路径是否可达？
  tws-graph search kind:class <TypeName>   ← 类型定义是否存在？方法与签名一致？

D3 设计书同步:
  tws-graph search kind:interface <Name>   ← 接口定义是否与设计书一致？
  tws-graph diff <before> <after> --brief  ← 符号层面的增删改

D5 安全性:
  tws-graph taint                          ← 污点分析：外部输入 → 敏感操作路径
  tws-graph analyze --run dead-code        ← 是否有死代码（可能是不完整的安全检查）

D6 接线完整性:
  tws-graph calls <new_func> --inbound     ← 新增函数被正确调用了吗？
  tws-graph calls <event> --outbound       ← 事件/回调被正确订阅了吗？

D7 架构健康度:
  tws-graph impact <symbol> --depth 2      ← 修改的波及范围
  tws-graph cycles                         ← 是否引入循环依赖？
  tws-graph layers                         ← 是否违反分层架构？
  tws-graph metrics                        ← 是否引入不合理耦合？
```

#### 执行流程

```
第 1 轮: 加载 found-tws-graph-usage
第 2~N 轮: 按 risk_zones 顺序调查（high → medium → low）
  对每个 risk_zone:
    1. 执行 tool_guidance 中的 CodeGraph 命令
    2. 分析命令输出，判断是否需要进一步调查
    3. 如需要更多上下文 → Read 文件片段（≤100行/次）
    4. 得出结论：confirmed / disproved / uncertain
    5. confirmed → 按格式报告 issue
    6. disproved → 记录调查结果，不报告
    7. uncertain → 标记 provenance: "heuristic" 报告

循环终止条件（满足任一）:
  - 所有 risk_zones 已调查完毕
  - 累计 `max_tool_request_rounds`（默认 30）轮对话
  - 仅剩 low severity 项且上下文紧张

每轮结束前检查上下文状态:
  - 已输出 ≥10 个 issues → 写 [CONTEXT SUMMARY]
  - CodeGraph 输出超过 200 行 → 截断保留关键信息
```

#### 问题报告格式（单个 issue）

```json
{
  "id": "c-0",
  "severity": "high",
  "dimension": "D5",
  "line_range": [45, 48],
  "content": "login() 新增 max_retry 参数但未做范围校验，max_retry ≤ 0 会导致无限循环",
  "suggestion": "在函数入口添加：if max_retry <= 0: raise ValueError('max_retry must be positive')",
  "evidence": {
    "type": "codegraph",
    "source": "tws-graph calls login --inbound → gateway/login_handler.py:23 传入用户可控的 config.max_retry，无校验逻辑",
    "detail": "Config.get('max_retry') 默认值为 0，login() 内部 while retry < max_retry 当 max_retry=0 时永不执行但也不报错，属于静默异常"
  }
}
```

**evidence.type 枚举：**
- `codegraph` — 通过 tws-graph 命令确认
- `file_read` — 通过 Read 确认
- `diff` — 在 diff 本身中直接可见
- `unavailable` — 文件无法读取（标注在 detail 中说明原因）

#### 输出格式（Phase 2 最终输出）

```json
{
  "file": "auth.py",
  "investigation_summary": "Plan 识别 3 个 risk_zones，全部调查完成。确认 2 个 high 问题、1 个 medium 问题。disproved 0 个。主要风险：login() 参数范围校验缺失，refresh() 缺少 max_age 检查。",
  "issues": [
    { "id": "c-0", "severity": "high", ... },
    { "id": "c-1", "severity": "medium", ... }
  ]
}
```

**如果 Plan 为 null（跳过 Plan 阶段时）：**
子 agent 自行判断调查优先级，流程同上。输出中增加 `plan_source: "self"` 字段。

#### Token 自管理协议

当满足以下条件之一时，子 agent 必须在下一轮响应的**开头**输出上下文摘要：

```
[CONTEXT SUMMARY]
已确认问题: c-0(high), c-1(high), c-2(medium)
待调查: rz-3(medium), rz-4(low)
已排除: rz-5(disproved — 调用方确认已更新)
继续调查 rz-3...
```

触发条件：
- 已报告 ≥ 10 个 issues
- 单次 CodeGraph 输出 > 200 行
- 对话已超过 `max_tool_request_rounds * 0.5`（默认 15）轮

#### 约束
- 必须加载 `found-tws-graph-usage`
- **只报告 confirmed 的问题**（disproved 和 uncertain 不出现在 issues 中）
- 文件读取必须先定位再读（禁止 `Read` 整个文件）
- CodeGraph 返回空时：标注 evidence source 为 "heuristic"，用 Grep 回退
- 禁止读取与当前文件无关的其他源文件

---

### 2.3 Filter 子 agent

**定位**：以干净上下文审视 Phase 2 的审查发现，删除 diff 可以证明为误报的问题。**不调查，只判断。**

#### 输入

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `file_path` | string | main agent | 审查文件路径 |
| `diff` | string | main agent | 统一 diff 格式 |
| `issues_json` | array | Phase 2 输出 | issues 数组（含 evidence） |

**关键：Filter 子 agent 不接收 plan_json、impact_excerpt、checklist、conventions_path。**
它的上下文只有 diff + issues。这与 open-code-review 的 filter agent 设计一致——filter 只看 diff，不被调查过程污染。

#### 执行流程

```
对每个 issue:
  1. 阅读 issue.content 和 issue.evidence
  2. 在 diff 中查找相关代码
  3. 判断：

     情况 A — diff 证明问题不存在:
       例: issue 声称 login() 缺少 null check，
           但 diff 第 48 行明确显示 "if user is None: raise AuthError()"
       → 加入删除列表

     情况 B — diff 不涉及相关代码:
       例: issue 指向 auth.py:100，但 diff 只覆盖 1-80 行
       → 保留 issue（diff 覆盖不全 ≠ issue 错误）

     情况 C — evidence 与 diff 矛盾:
       例: evidence 称 "file_read 确认 auth.py:60 存在 SQL 拼接"，
           但 diff 显示第 60 行是日志语句
       → 加入删除列表

     情况 D — 无法判断:
       例: issue 涉及跨文件调用，diff 中看不到调用方
       → 保留 issue（不能确定就是误报）
```

#### 删除判断矩阵

| evidence 声称 | diff 显示 | 判断 |
|---------------|-----------|------|
| 代码存在某问题 | 代码行不存在于 diff 中 | **保留** — diff 可能不包括该区域 |
| 代码存在某问题 | 代码行存在但逻辑不同 | 删除 — evidence 错误 |
| 缺少某种检查 | diff 中明确有该检查 | 删除 — 检查已存在 |
| 缺少某种检查 | diff 中无相关代码 | **保留** — 不能证明检查存在 |
| 调用方未同步 | diff 中调用方代码已更新 | 删除 — 已同步 |
| 调用方未同步 | diff 不包含调用方文件 | **保留** — 无法判断 |

**核心原则：只删除 diff 能**证明**为错的。不确定 = 保留。**

#### 输出格式

```json
["c-2", "c-5"]
```

- 数组元素：要删除的 issue ID
- 无删除 → `[]`
- 只输出 JSON 数组，无其他文字

#### 约束
- **禁止**加载 found-tws-graph-usage
- **禁止**执行任何 Bash 命令
- **禁止**读取任何文件
- **禁止**添加新问题
- **禁止**修改 issue 内容
- **禁止**重新调查
- 只能基于 diff 文本判断

---

## 三、编排层详细设计

### 3.1 comp-code-review/SKILL.md 结构（覆写）

```
---
name: code-review
description: 代码审查编排层。Impact Assessment → 三阶段并行 spawn → 汇总 → Triage → Design-Sync
---

# 代码审查编排

## 架构（两层 + 三阶段图）

## Step 0: Impact Assessment
- spawn 子 agent，加载 comp-impact-assessment
- 输出：影响面报告（文件列表 + 风险分级 + 接口变更 + 消费者）

## Step 1: Scope Determination
- HIGH/MEDIUM → 完整三阶段审查
- LOW → 轻量检查（main agent 30秒扫一眼）
- 输出：审查文件列表

## Step 2: Phase 1 — Plan（并行）
- 并发控制：≤5文件全并发, 6-10→5并发, 11+→5并发分批
- Spawn prompt 模板（客观事实，禁引导语）
- 收集所有 plan JSON
- 写 checkpoint

## Step 3: Phase 2 — Main Loop（并行）
- 并发控制同上
- Spawn prompt 模板（含 plan_json）
- 收集所有 issues + evidence
- 写 checkpoint

## Step 4: Phase 3 — Filter（并行）
- 并发控制同上
- Spawn prompt 模板（干净上下文，只有 diff + issues）
- 应用删除 → 最终报告
- 写 checkpoint

## Step 5: Aggregation
- 合并所有文件最终报告
- 去重
- 按 severity 排序

## Step 6: Triage
- A/B/C 分类（引用 found-review-triage）
- 停止规则

## Step 7: Design-Sync（条件触发）
- D3 medium+ → spawn design-sync 子 agent

## Checkpoint 集成
- Session 文件格式

## Rationalization Prevention
```

### 3.2 Spawn Prompt 模板

#### Phase 1: Plan

```
你是审查 Plan 子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")），执行 Phase 1: Plan。

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

#### Phase 2: Main Loop

```
你是审查调查子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
1. 加载 found-tws-graph-usage（Skill(skill: "found-tws-graph-usage")）
2. 加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")）
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
按 comp-agentic-review-core 中 Phase 2 的完整指引执行。使用 CodeGraph 调查每个 risk_zone。
只报告已确认的问题。输出 issues JSON。
```

#### Phase 3: Filter

```
你是审查 Filter 子 agent。这是一个干净的独立审查会话。不要读记忆文件、日记或任何历史记录。

== 任务 ==
加载 comp-agentic-review-core（Skill(skill: "comp-agentic-review-core")），执行 Phase 3: Filter。

== 输入 ==
文件路径：{file_path}
Diff 内容：
{diff}
审查发现（Main Loop 阶段输出）：
{issues_json}

== 要求 ==
按 comp-agentic-review-core 中 Phase 3 的完整指引执行。
用 diff 证伪误报。只输出要删除的 issue ID 数组。
不要加载其他 skill，不要执行命令，不要读取文件。
```

### 3.3 并发控制规则

| 审查文件数 | 每阶段最大并发 | 说明 |
|-----------|-------------|------|
| ≤5 | 全并发（≤`max_concurrency`，默认 5） | 一次性 spawn 全部 |
| 6-10 | 5 | 分批：第一批 5 个，完成后 spawn 剩余 |
| 11+ | 5 | 多批次：每批 5 个，批次间写 checkpoint |

> 并发数可配。部分 API 支持 500+ 并发时调高 `max_concurrency` 即可。

> 审查子 agent 是只读的（不修改代码），所以可以比代码编写子 agent（限制 2 并发）开更多。

### 3.4 Checkpoint 格式

在现有 `.tws/sessions/{name}.md` 中增加审查批次追踪：

```markdown
## 审查进度

### Phase 1: Plan
| 文件 | 状态 | risk_zones |
|------|------|-----------|
| auth.py | ✅ done | 3 (1H, 1M, 1L) |
| session.py | ✅ done | 2 (1H, 1M) |
| token.py | ✅ done | skip (small diff) |

### Phase 2: Main Loop
| 文件 | 状态 | issues |
|------|------|--------|
| auth.py | 🔄 running | - |
| session.py | ⏳ pending | - |
| token.py | ⏳ pending | - |

### Phase 3: Filter
| 文件 | 状态 | removed |
|------|------|---------|
```

---

## 四、数据流总览

```
Phase 1: Plan
  Input:  file_path, diff, impact_excerpt, checklist
  Output: {file, change_summary, risk_zones[{id, severity, description, tool_guidance}]}
  或:     {skip_plan: true, reason: "..."}

Phase 2: Main Loop
  Input:  file_path, diff, plan_json, impact_excerpt, checklist
  Output: {file, investigation_summary, issues[{id, severity, dimension,
           line_range, content, suggestion, evidence}]}

Phase 3: Filter
  Input:  file_path, diff, issues_json
  Output: ["c-2", "c-5"]   ← 要删除的 ID 列表

Final (main agent 聚合):
  issues_final = issues - filter_removed
```

---

## 五、错误处理矩阵

| 场景 | 阶段 | 处理 |
|------|------|------|
| CodeGraph 不可用（`tws-graph --version` 失败） | Phase 2 | evidence.type = "heuristic"，退到 Grep + Read。summary 中注明 "WARNING: CodeGraph unavailable" |
| 文件读取权限不足 | Phase 2 | evidence.type = "unavailable"，detail 说明原因。issue 保留，标注"建议人工复核" |
| diff 超过 `max_diff_lines`（默认 500）行 | Phase 1 | change_summary 中注明 "WARNING: diff 过大({N}行)，仅分析关键路径"。risk_zones 只覆盖最显著的变更 |
| Plan 子 agent 输出无法解析 | Phase 1→2 | main agent 将 plan_json 设为 null，Main Loop 子 agent 自主决定调查策略 |
| Main Loop 子 agent 未返回 issues | Phase 2→3 | main agent 将 issues_json 设为 `[]`，Filter 子 agent 输出 `[]`（无问题则无过滤） |
| Filter 子 agent 输出无法解析 | Phase 3 | 保守策略：不应用任何删除，所有 issues 原样保留 |
| 子 agent 超时或崩溃 | 任意 | checkpoint 记录已完成文件，重试失败的文件子 agent |

---

## 六、与现有 TWS 组件的接口

### 6.1 输入来源

```
comp-impact-assessment 输出 → 注入 spawn prompt:
  impact_excerpt（仅本文件相关）:
    - 本文件接口变更
    - 消费者模块
    - 连锁影响
    - 风险等级

found-review-methodology → 约束 spawn prompt:
  - context: isolated
  - 不读记忆文件
  - 只接受客观事实
  - 不接受引导性描述
```

### 6.2 D1-D7 审查维度（从原 comp-code-review 继承）

审查清单不变，但执行方式从"人工逐条核对"变为"Plan 阶段预判 + Main Loop 阶段工具调查确认"。

### 6.3 与其他组件的交接

```
审查报告 → main agent:
  ├─ Triage（found-review-triage）→ A/B/C 分类
  ├─ Design-Sync（comp-design-sync）→ D3 问题触发
  └─ Contract-Aware（如适用）→ 接口变更通知
```

---

## 七、文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **NEW** | `.claude/skills/comp-agentic-review-core/SKILL.md` | 三阶段 prompt 模板（~300行） |
| **MODIFY** | `.claude/skills/comp-code-review/SKILL.md` | 覆写为编排层（~250行） |
| **MODIFY** | `.claude/skills/comp-subagent-dispatch/SKILL.md` | dispatch table 增加 agentic-review-core 引用 |
| **MODIFY** | `.claude/skills/flow-fix-bug/SKILL.md` | Step 7 描述更新 |
| **MODIFY** | `.claude/skills/flow-add-feature/SKILL.md` | Step 5h 描述更新 |

---

## 八、设计决策记录

| 决策 | 替代方案 | 选择理由 |
|------|---------|---------|
| 三阶段拆成独立子 agent | 三阶段合并在一个子 agent 内多轮对话 | 单 agent 长流程容易偏移，拆开后每阶段短且聚焦，checkpoint 更精准 |
| 阶段内并行、阶段间串行 | 每文件全流程串行（Plan→Main→Filter 一个子 agent 做完再下一个文件） | 最大化并发，审查总时长 = 最慢文件 × 3 轮，而非 N 个文件串行 |
| Filter 拿干净上下文 | Filter 子 agent 也拿到 investigation 过程 | open-code-review 的经验：filter agent 只看 diff 能更客观地判断误报，不被调查过程中的假设污染 |
| Plan 阈值取 10（OCR 为 50） | 与 OCR 保持一致取 50 | Plan 是一次纯思考 LLM 调用，成本极低。5 行改错也出大问题，不值得跳过。取 10 是折中——纯格式/注释变更（通常 <10 行）跳过，逻辑变更都走 Plan |
| 所有参数可配置 | 数字写死在 prompt 里 | 不同场景、不同 API 配额需要不同配置。编排层声明默认值，主 agent spawn 时按需覆盖 |
| 并发默认 5（OCR 为 8） | 与 OCR 保持一致取 8 | 先保守设 5，避免触发 API 限流。部分 API 支持 500+ 并发时调高即可 |
| CodeGraph 替代 grep 作为主要调查工具 | 继续用 Grep + Read | CodeGraph 提供结构化依赖关系（caller/callee/impact），grep 只能做文本匹配 |
| 中文输出 | 英文输出 | TWS 整体是中文体系，保持一致性 |
