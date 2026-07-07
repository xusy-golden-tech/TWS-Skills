# TWS Code Review 模块升级设计书

> 2026-06-19 | 基于 open-code-review（阿里）+ PR-Agent + TWS 现有资产分析
>
> 版本: v2 | 输入: 主人与念念的多轮讨论收敛

---

## 一、现状

### 1-1 TWS 已有的

| 资产 | 作用 |
|------|------|
| `found-review-methodology` | 隔离上下文、禁引导、只给事实 → 审查公正性 |
| `comp-code-review` | 审查清单：正确性→安全→规约→性能→风格 |
| `comp-impact-assessment` | 改前：涉及模块→接口变更→消费者→连锁影响 |
| `comp-design-sync` | 改后：强制设计书与代码一致 |
| `team-contract-aware` | 改接口前查 CONTRACTS.md、通知消费者 |
| `CodeGraph` | 预建依赖图、BFS/DFS 遍历、调用链分析 |
| `checkpoint` | 会话状态恢复，中断可续 |
| `comp-subagent-dispatch` | 子 agent 调度 |
| `team-subagent-dispatch` | 多子 agent 并发调度 |

### 1-2 TWS 缺的

**Agentic review 引擎** — 现在 code-review 是清单式的人工/子 agent 审查，没有 LLM 自驱动的 Plan→Tool-use→Filter 多轮循环。

---

## 二、外部参考的取舍

### 2-1 open-code-review（阿里，⭐7.9k，Go）

```
借鉴：
  ✅ Agentic 三阶段引擎（Plan → Main Loop → Filter）
  ✅ 三层 Token 管理（Pre-filter + Async/Sync Compression）
  ✅ 结构化 Diff 解析（model.Diff）
  ✅ Per-file 并发审查模型（dispatchSubtasks → executeSubtask）
  ✅ Review Filter（后过滤 diff 可证伪的误报）
  ✅ 审查工具定义（code_comment / file_read / task_done）

不借鉴：
  ❌ code_search / file_find → TWS 用 CodeGraph 替代
  ❌ prompt 模板原文 → TWS 用自己的 review-methodology + checklist
  ❌ Go 二进制 → TWS 是 skill 体系
```

### 2-2 PR-Agent（Codium→社区，⭐6k+，Python）

```
借鉴：
  ✅ similar_issue（向量搜索相似历史变更）→ 可选 Plan 阶段辅助
  ✅ tool-based agent 架构模式（独立工具、可组合、可扩展）
不借鉴：
  ❌ 单轮 LLM 架构 → 不如 open-code-review 的 agentic 循环
  ❌ 其他工具（describe/improve/ask/changelog/labels）→ TWS 已有或不需要
  ❌ 无内置规则 → 不如阿里 20+ 语言规则库
```

### 2-3 字节跳动

```
无专门的 code review 开源项目。
  - deer-flow (⭐71.6k): SuperAgent 调度框架，非 review 专用
  - trae-agent (⭐11.7k): 通用编码 agent，无 review 模块
```

---

## 三、执行模型

### 3-1 核心原则：不是嵌套子 agent

```
❌ 错误理解:
  主 agent → spawn 审查子 agent → spawn Plan孙agent → spawn Main孙agent → spawn Filter孙agent

✅ 实际模型:
  主 agent → spawn 审查子 agent（per-file，仅此一次）
              │
              同一子 agent 的对话中顺序执行:
                第1轮 LLM → Plan
                第2~N轮 LLM → Main Loop（tool-use 循环）
                第N+1轮 LLM → Filter
              → 返回报告
```

Plan/Main/Filter 不是 spawn/spawn/spawn，是**同一个 agent 内的多轮对话**。

### 3-2 分层设计：调度层不碰 diff

```
主 agent（调度层）:
  上下文里只有:
    - 文件列表（文件名，不含 diff 内容）
    - 影响面报告（几百 token）
    - checkpoint 状态（几十行）
    - 子 agent 返回结果（聚合后）
  上下文大小: ~2k~5k tokens，几乎不变
  → 不会因为文件多而膨胀
  → 不会因为审查复杂而偏移

子 agent（审查层）:
  每个子 agent 只审一个文件
  上下文里只有:
    - 该文件的 diff
    - 影响面报告（针对性摘要）
    - review checklist + 规约
  → 单个文件上下文可控
  → 文件间的审查互不污染
```

这和 open-code-review 的分层完全一致：上层拿文件名列表调度，下层拿 diff 做审查。

### 3-3 平台差异：Claude vs g-ass

同一套审查引擎核心（Plan/Main/Filter 约束），适配两个平台：

```
┌─ Claude 上（对话模式）────────────────────┐
│                                           │
│  调度方式: 分批 spawn + checkpoint 兜底    │
│  并发控制: 手动分成 N 个 batch             │
│  进度保护: 每批前后写 checkpoint 文件      │
│  挂掉恢复: 读 checkpoint → 从中断处续     │
│                                           │
│  comp-agentic-review-core 是 SKILL.md     │
│                                           │
├───────────────────────────────────────────┤
│                                           │
│  ┌─ g-ass 上（程序模式）─────────────────┐│
│  │                                        ││
│  │  调度方式: for 循环 + API 回调          ││
│  │  并发控制: API 层限流 queue              ││
│  │  进度保护: 不丢内存变量，不需要 checkpoint││
│  │  挂掉恢复: 钩子 + 事件驱动              ││
│  │                                        ││
│  │  comp-agentic-review-core 是 tool.py   ││
│  │  + pre-hook / post-hook                ││
│  └────────────────────────────────────────┘│
│                                           │
│  共享: Plan/Main/Filter 的 prompt 模板、  │
│         checklist、约束体系、CodeGraph 工具 │
│  差异: 只有调度层的实现方式                │
└───────────────────────────────────────────┘
```

---

## 四、模块架构

### 4-1 整体调用链路

```
用户: "帮我 review feature/user-login 分支的改动"
  │
  ▼
using-tws（入口路由）→ add-feature / fix-bug / hotfix
  │
  ▼
flow-add-feature
  │
  ├→ ① 设计书确认
  ├→ ② 编码
  ├→ ③ 测试
  ├→ ④ ★代码审查★（升级后的 comp-code-review）
  │     │
  │     ├─ 主 agent: impact-assessment → 影响面报告
  │     │
  │     ├─ 主 agent: 分批 spawn 审查子 agent（per-file）
  │     │   ├─ Batch 1: [auth.py, session.py, token.py]
  │     │   ├─ 写 checkpoint
  │     │   ├─ Batch 2: [cache.py, gateway.py]
  │     │   ├─ 写 checkpoint
  │     │   └─ 汇总所有审查报告
  │     │
  │     │   每个子 agent 内部（独立上下文）:
  │     │     Plan → Main Loop（codegraph_query/file_read/code_comment）→ Filter
  │     │
  │     ├─ 主 agent: design-sync
  │     └─ 主 agent: contract-aware
  │
  ├→ ⑤ 设计书同步
  └─ ⑥ 更新 checkpoint → 完成
```

### 4-2 子 agent 内三阶段

```
子 agent 收到 task:
  - 本文件的 diff
  - 影响面报告（针对该文件的摘要）
  - review checklist
  - 项目规约
  - found-review-methodology 约束

▸ Phase 1: Plan（一次 LLM 调用）
  输入: diff + 影响面 + 规约 + checklist
  输出: JSON {change_summary, issues[{severity, description, tool_guidance}]}
  作用: 决定审查策略，哪些地方要深查，用哪些工具

▸ Phase 2: Main Loop（多轮 LLM tool-use）
  每轮可用工具:
    codegraph_query(query, traversal) → 依赖图查询
    file_read(path)                   → 读取完整文件
    file_read_diff(path)              → 读取文件 diff
    code_comment(path, start, end, severity, content, suggestion) → 提交意见
    task_done                         → 结束审查

  约束（注入到 system prompt）:
    - found-review-methodology（上下文隔离、禁引导）
    - comp-code-review checklist（正确性→安全→规约→性能→风格）

  Token 管理（直接搬 open-code-review）:
    第1层: Pre-filter（diff > 80% MaxTokens → 跳过，标记需人工）
    第2层: Async Compression（60% 触发 → 背景异步压缩）
    第3层: Sync Compression（80% 触发 → 立即压缩）

▸ Phase 3: Filter（一次 LLM 调用）
  输入: diff + 所有审查意见 JSON
  输出: 删除可证伪的误报 ID 列表
  规则: 只删 diff 可证明为错的，不删"无法判断"的
```

### 4-3 审查工具集

```
审查子 agent 注册工具:

┌─────────────────────┬──────────────────────────────────┐
│ 工具                 │ 能力                             │
├─────────────────────┼──────────────────────────────────┤
│ codegraph_query      │ TWS CodeGraph 依赖图查询          │
│   - getContext       │   完整上下文（调用者/被调用/类型） │
│   - traverseBFS      │   BFS 遍历调用链                  │
│   - traverseDFS      │   DFS 遍历依赖链                  │
├─────────────────────┼──────────────────────────────────┤
│ file_read            │ 读取完整文件内容                   │
├─────────────────────┼──────────────────────────────────┤
│ file_read_diff       │ 读取指定文件的 diff               │
├─────────────────────┼──────────────────────────────────┤
│ code_comment         │ 提交一条审查意见                   │
│   - path             │                                   │
│   - start_line       │                                   │
│   - end_line         │                                   │
│   - severity         │   high / medium / low             │
│   - content          │   问题描述                         │
│   - suggestion_code  │   建议修复代码（可选）             │
├─────────────────────┼──────────────────────────────────┤
│ task_done            │ 审查完成，结束循环                 │
└─────────────────────┴──────────────────────────────────┘
```

---

## 五、与现有 TWS 组件的接口

### 5-1 输入（审查引擎启动前，主 agent 准备）

```
comp-impact-assessment 输出 → 注入子 agent:
  ├─ 涉及模块: [user, auth, db]
  ├─ 本文件接口变更: [login()]
  ├─ 消费者: [gateway, web]
  ├─ 连锁影响: auth → session → cache
  ├─ 风险等级: 中
  └─ 需回测: [test_auth.py, test_session.py]
```

### 5-2 约束注入（Main Loop 中）

```
found-review-methodology:
  - context:isolated（独立上下文）
  - 不读记忆文件
  - 只接受客观事实
  - 不接受引导性描述
  - 独立判断

comp-code-review checklist:
  □ 逻辑正确性
  □ 安全性
  □ 规约遵守
  □ 性能
  □ 风格（仅在违反团队规约时）

优先级: 正确性 > 安全性 > 规约遵守 > 性能 > 风格
```

### 5-3 输出（审查结束后，主 agent 处理）

```
审查报告 → 主 agent：
  ├─ design-sync → 检查设计书与代码一致
  └─ contract-aware → 检查接口变更是否通知消费者
```

---

## 六、新增文件

```
.tws/
  comp-agentic-review-core/
    SKILL.md                    ← Claude 版审查引擎 skill
    task_template.json          ← Plan / Main / Filter / Compression prompt 模板
    tools.json                  ← 工具定义
  comp-code-review/
    SKILL.md                    ← 更新：编排层（impact→spawn→汇总→sync→contract）
    
g-ass 迁移时新增:
  comp-agentic-review-core/
    tool.py                     ← g-ass 版审查引擎 tool
    hooks.py                    ← pre-hook / post-hook
```

---

## 七、完整使用链路（Claude 版）

```
1. 用户: "帮我审查 feature/user-login 分支的改动"

2. using-tws → flow-add-feature
   checkpoint: 已完成 [设计书, 编码, 测试]，当前 [代码审查]

3. 主 agent 加载 comp-code-review:

   Step 0: impact-assessment
   → 影响面: auth.py(login签名变更), session.py(refresh新增)
     消费者: gateway, web | 风险: 中

   Step 1: 写 checkpoint
   [x] ① impact-assessment
   [ ] ② spawn batch 1/2 [auth.py, session.py, token.py]
   [ ] ③ spawn batch 2/2 [cache.py, gateway.py]
   [ ] ④ 汇总
   [ ] ⑤ design-sync
   [ ] ⑥ contract-aware

   Step 2: spawn batch 1（3个子 agent 发完不管）
   
   ┌─ 子 agent auth.py ───────────────────────┐
   │ Plan: 重点 login() 认证逻辑 + 调用方兼容性 │
   │ Round 1: codegraph_query("login",incoming) │
   │         → gateway.login_handler() 调用     │
   │         file_read("gateway/login_handler") │
   │         → 参数顺序与新的 login() 不一致！  │
   │         code_comment(severity=high,        │
   │           "参数顺序变更，gateway未同步")    │
   │ Round 2: task_done                        │
   │ Filter:  意见有效 → 无删除                 │
   └──────────────────────────────────────────┘
   
   ┌─ 子 agent session.py ──────────────────┐
   │ Plan: refresh() token 处理 + 安全问题    │
   │ Round 1: codegraph_query("refresh",out,2)│
   │         → refresh→validate→db            │
   │         code_comment(severity=medium,    │
   │           "refresh() 缺 max_age 检查")   │
   │ Round 2: task_done                      │
   │ Filter:  意见有效 → 无删除               │
   └─────────────────────────────────────────┘
   
   ┌─ 子 agent token.py ────────────────────┐
   │ Plan: diff 较小，仅 import 和注释变更    │
   │ Round 1: file_read_diff → 确认无逻辑变更│
   │         task_done                       │
   │ Filter:  无意见 → 跳过                  │
   └─────────────────────────────────────────┘

   Step 3: 等 batch1 全部完成 → 写 checkpoint
   [x] ② spawn batch 1/2 [auth.py, session.py, token.py] ✓

   Step 4: spawn batch 2（2个子 agent）
   ...同理...

   Step 5: 汇总 → 审查报告:
     ✦ HIGH: auth.py:45 login() 参数变更 gateway 未同步
     ✦ MEDIUM: session.py:78 refresh() 缺 max_age 检查

   Step 6: design-sync → auth.py 设计书 login() 签名一致 ✓

   Step 7: contract-aware → login() 变更需更新 CONTRACTS.md

4. 更新 checkpoint → [代码审查] 完成 ✓
```

---

## 八、安全防护清单

| 风险 | 防护 |
|------|------|
| 主 agent 上下文随文件数膨胀 | 分层设计：主 agent 只记文件名，不碰 diff |
| 主 agent 漏步骤或偏移 | checkpoint 每步写，frozen zone 不压缩 |
| 单文件过大爆上下文 | 三层 Token 防御（Pre-filter/Async/Sync Compression） |
| 审查公正性 | found-review-methodology（isolated + 禁引导） |
| API 并发限制 | 分批 spawn，非一次性全部发出 |
| 子 agent 之间污染 | per-file 独立上下文，互不可见 |
| 重启丢进度 | checkpoint 持久化，读文件续 |
| 误报 | Review Filter 后过滤 |

---

## 九、设计决策总结

| 决策 | 理由 |
|------|------|
| 引擎骨架选 open-code-review | 三阶段（Plan→Main→Filter）已 battle-test |
| per-file 独立子 agent | 上下文隔离 + 可并发 + 可分批 |
| Plan/Main/Filter 不嵌套 spawn | 同一 agent 的多轮对话，Claude 原生支持 |
| 代码理解用 CodeGraph | 预建依赖图，比 grep 强一个维度 |
| 审查公正性用 TWS methodology | 隔离上下文、禁引导、只给事实 |
| 前置 impact-assessment | 审查前知道影响面，更精准 |
| 后置 design-sync + contract-aware | 审查不止看代码，文档和契约也对 |
| 分批 + checkpoint（Claude） | 对抗 API 限流 + 对话模式"醒来忘记" |
| 共享核心 + 适配调度（g-ass） | 同一引擎，不同平台的调度层不同 |
| 工具注册表可扩展 | 后续可加 similar_issue、security_scan 等 |
