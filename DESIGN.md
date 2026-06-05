# TWS 设计书

## 1. 概述

TWS（Thoughtful Workflow System）是一套结构化 AI 开发流程系统。核心思想：**让 AI agent 像有纪律的工程师一样工作——先设计再编码、增量实施、测试验证、文档同步。**

TWS 不是代码框架，不提供运行时库。它由两部分组成：

1. **技能文件（41 个 SKILL.md）** — 用 YAML frontmatter + Markdown 编写的 AI 指令，定义软件开发的标准操作流程
2. **tws-graph（Python CLI）** — 代码符号关系图引擎，预建 tree-sitter 索引，替代 agent 用 grep 探索代码

### 与传统 SDLC 工具的差异

| | TWS | 传统工具（CI/CD、linter、脚手架） |
|---|-----|----------------------------------|
| 执行者 | AI agent（Claude Code） | 确定性程序 |
| 控制对象 | agent 的思考过程 | 代码/构建产物 |
| 验证方式 | 设计书 ↔ 代码一致性 | 语法检查、测试覆盖率 |
| 灵活性 | agent 可根据上下文判断 | 固定规则，无上下文 |

TWS 解决的是「AI 写代码太快但太随意」的问题——不是限制 agent 的速度，而是给速度加上方向。

## 2. 核心原则

TWS 所有流程都遵循四条全局规则：

1. **设计优先** — 没有设计书就不写代码。设计书是 agent 之间的契约
2. **增量实施** — 拆成可验证的小步，每步独立验证，不跳步
3. **验证驱动** — 测试通过是最低要求，还要检查边界条件和设计书同步
4. **设计同步** — 改了代码必须更新设计书。未同步 = 未完成

这四条规则通过 `found-core-principles` 贯彻，所有 flow skill 和 comp skill 均受其约束。

## 3. 四层架构

```
入口层 (using-*)   →  流程层 (flow-*)   →  组件层 (comp-*)   →  基础层 (found-*)
     ↑                      ↑                    ↑                  ↑
  场景路由             流程编排          可执行单元            全局规则
  using-tws      add-feature/fix-bug/   design-doc/          core-principles/
                 hotfix/refactor/       implementation/       branch-flow/
                 new-project/           test/code-review/     tws-graph-usage/
                 documentation/         impact-assessment/    artifact-split/
                 investigate            design-sync/...       review-methodology/...
```

**调用方向规则：** 只能从左到右调用。入口调流程，流程调组件，组件引基础。不允许反向依赖（组件不能调流程，基础不知道入口的存在）。

### 3.1 入口层 — using-tws

唯一入口。职责：
- 判断场景 → 路由到对应 flow skill
- 检查环境（tws-graph 可用性、.tws/ 初始化状态）
- 检测未完成流程（`.tws/sessions/`）→ 支持断点续传
- 全局纠错——方向偏离时主动停下调整

**不做的：** 不读业务代码、不调查问题、不写执行计划。这些是 flow skill 的职责。

### 3.2 流程层 — 7 条流程

每条流程定义了从始至终的完整步骤，包括门禁条件（什么情况走简化路径 vs 完整路径）。

| 流程 | 触发场景 | 门禁条件（简化/完整） |
|------|---------|---------------------|
| add-feature | 新增功能 | 涉及 ≥ 3 模块 或 API 变更 → 完整 |
| fix-bug | 修复缺陷 | 涉及架构决策 → 完整 |
| hotfix | 紧急修复 | 总是简化（时间优先） |
| refactor | 重构 | 跨模块 → 完整 |
| new-project | 新建项目 | 总是完整 |
| documentation | 写文档 | 总是简化 |
| investigate | 排查 | 总是简化（只查不修） |

### 3.3 组件层 — 23 个可执行单元

每个组件是单一职责的工作手册。子 agent 每次加载 1-2 个组件 skill 执行具体任务。

关键组件：
- **design-doc** — 写设计书（输出到 `.tws/designs/`）
- **implementation** — 编码（派发到语言专用 skill：backend-impl-python/java、frontend-impl）
- **test** — 测试（派发到 backend-test / frontend-test）
- **impact-assessment** — 影响分析（用 tws-graph 查图）
- **design-sync** — 设计书同步（用 tws-graph diff 对比变更）
- **code-review** — 代码审查（独立子 agent，干净上下文）
- **root-cause-analysis** — 根因分析（用 tws-graph trace 追踪调用链）

### 3.4 基础层 — 8 个全局规则

| 技能 | 功能 |
|------|------|
| core-principles | 四条核心原则的详细说明 |
| branch-flow | 分支管理规范（从 develop 开分支等） |
| tws-graph-usage | tws-graph 使用指南（命令参考、优先级规则、Grep 白名单） |
| artifact-split | 文档/代码拆分规则 |
| review-methodology | 审查必须新开独立子 agent |
| review-triage | 审查报告 A/B/C 分类和叫停规则 |
| environment-gov | 环境治理 |
| impact-report | 跨模块影响上报 |

## 4. Agent 层级

TWS 定义了严格的两级 agent 架构：

### 主 agent（项目经理）

- 加载：入口 skill + 流程 skill + 子 agent 调度 skill
- 职责：判断场景、拆任务、排依赖、派子 agent、验收产出
- **不写代码** — 这是硬规则
- **不加载 comp skill** — comp skill 是子 agent 的工作手册

### 子 agent（执行者）

- 加载：1-2 个 comp skill，按需加载 found skill
- 职责：执行单一任务，完成后汇报，上下文释放
- **不决策、不规划、不改变任务范围**
- 使用 `general-purpose` 类型，通过 `Skill()` 工具加载 comp skill

### 审查子 agent（特殊类型）

- 必须使用干净上下文（隔离模式）——不读记忆文件、不继承对话历史
- 只提供审查对象（文件路径）和客观背景
- 报告按 A/B/C 分类：A=必须修，B=主 agent 决定，C=跳过

### 调度机制（comp-subagent-dispatch）

主 agent 派子 agent 的 prompt 模板：
```
1. 通过 Skill 工具加载 {comp-skill-name}
2. 按 skill 中的指引执行任务
3. 完成后汇报
```

子 agent 完成后，主 agent：
1. 更新 session 文件标记完成
2. git commit（防止后续子 agent 回滚已验收改动）
3. 检查完成标准 → 决定下一步

## 5. 流程编排

### 5.1 Session 状态管理

`.tws/sessions/{flow-type}-{short-desc}.md` 记录进行中的流程：

```markdown
## 当前流程
- 流程名：添加广告位功能
- flow skill：flow-add-feature
- 开始时间：2026-06-05 14:30
- 版本号：3

## 进度
- [x] ① 现状分析
- [x] ② 目标设计
- [ ] ③ 迁移计划

## 当前任务
- 正在做：③ 迁移计划
- 状态：进行中
```

新会话启动时，`using-tws` 检测 `.tws/sessions/` 下的文件 → 支持断点续传。流程完成后自动清理。

### 5.2 并发控制

子 agent 并发数默认 2。超过时排队，不新增。防止上下文膨胀和合并冲突。

### 5.3 上下文管理

- 主 agent 只加载 4-6 个 skill（~300-400 行）
- 子 agent 每次只加载 1-2 个 skill（~100-200 行）
- 子 agent 完成后上下文释放，不留残留
- 主 agent 自检信号：已派 5+ 子 agent / 汇报累积超 3 屏 / 下一个任务很复杂 → 偏重时减少汇报内联

## 6. tws-graph 集成

tws-graph 是 TWS 的「客观事实层」。图和 agent 的分工：

- **图告诉 agent 客观事实** — 谁调了谁、影响半径多大、两点间经过哪些路径
- **agent 做主观判断** — 风险等级、是否需要通知、是否值得改

### 集成点

| TWS 环节 | tws-graph 命令 | 提供什么 |
|---------|---------------|---------|
| 影响分析 | `impact --depth 2` + `calls --inbound` | 被改符号的影响范围 |
| 根因分析 | `trace 入口 报错点` | 两点间的调用路径 |
| 设计同步 | `diff before after` | 代码变更的符号级差异 |
| 项目初始化 | `index` + `snapshot initial` | 全量索引 + 基线 |
| 符号查找 | `search kind:class Foo` | 替代 grep 查类名/方法名 |

### 工具优先级

子 agent 加载 `found-tws-graph-usage` 后遵守：
- 查符号、查调用、查影响 → tws-graph 优先
- tws-graph 返回空 / `[internal]` → 回退 Grep
- XML、.gradle、资源文件 → Grep（tws-graph 不索引这些类型）

### 索引维护

Git hooks 自动增量同步（post-commit/post-merge/post-checkout），子 agent 通常不需要手动 `tws-graph index`。

## 7. 项目初始化（tws-init）

对新项目执行一次性初始化，生成 `.tws/` 目录：

```
.tws/
├── project-map.md              ← 项目结构索引（技术栈、路径映射）
├── coding-conventions.md        ← 编码规约（推断自代码采样）
├── testing-conventions.md       ← 测试规约（推断自测试文件）
├── design-conventions.md        ← 设计书格式规约（模板）
├── env-conventions.md           ← 环境规约
├── architecture-decisions.md    ← 架构决策记录（空模板，使用中积累）
├── env-rules.md                 ← 环境问题决策记录
├── deferred-issues.md           ← 延迟问题列表
├── designs/                     ← 设计书存放
├── sessions/                    ← 流程状态（运行时创建/清理）
└── codegraph/
    ├── index.db                 ← 当前代码图索引
    └── index-initial.db         ← 基线快照
```

`tws-graph-init` 负责 code graph 部分：安装 tws-graph、全量索引、创建基线快照。

## 8. SUBAGENT-STOP 保护机制

防止子 agent 错误地把自己当主 agent 来编排流程：

- **有 SUBAGENT-STOP 标签：** 入口 skill、流程 skill、人类专用 comp skill（arch-decision）
- **无 SUBAGENT-STOP 标签：** 所有 comp skill（子 agent 执行任务所需）、所有 found skill

当 skill 被加载时，如果看到 `<SUBAGENT-STOP>` 标签且当前上下文是子 agent，则停止——提示「你应该把这个任务汇报给主 agent」。

## 9. 设计书体系

设计书是 TWS 中 agent 之间的核心契约，存放在 `.tws/designs/`。

- **格式：** 由 `design-conventions.md` 定义
- **创建：** 由 `comp-design-doc` 子 agent 完成
- **同步：** 代码变更后由 `comp-design-sync` 子 agent 执行（用 tws-graph diff 对比）
- **引用：** 设计书之间可以有交叉引用，通过 tws-graph 的 internal-refs 边追踪

## 10. 技术决策记录

| 决策 | 理由 |
|------|------|
| Skill 用 Markdown 而非代码 | Claude Code 原生支持，不需要额外运行时。markdown 既是文档又是可执行指令 |
| 四层架构 + 单向依赖 | 防止循环引用。入口不知道细节，基础不知道全局 |
| 主 agent 不写代码 | 防止「经理亲自搬砖」导致全局视角丢失。子 agent 专注执行，主 agent 专注编排 |
| 子 agent 完成后即释放上下文 | 防止多任务上下文叠加导致膨胀和决策质量下降 |
| git commit 每个子任务 | 防止后续子 agent 误操作回滚已验收的改动。commit 不 push，全部完成后由用户决定 |
| Session 文件追踪进度 | 支持断点续传。文件系统比内存可靠，跨会话保持 |
| 审查用独立子 agent | 干净上下文确保审查的独立性。审查者不知道执行者的思维过程，只看产物 |
| tws-graph 独立 pip 包 | 与 skill 系统解耦。CLI 接口清晰，任何 agent 都能调用，不绑定 Claude Code |
| SUBAGENT-STOP 用标签而非文件名规则 | 标签在文件内容中，linter 可校验。比「约定俗成」可靠 |
| 设计书在 .tws/ 下 | 独立于源码仓库的文档结构。agent 知道去哪里找，不需要猜测路径 |
