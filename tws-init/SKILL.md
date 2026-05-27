---
name: tws-init
description: TWS 项目初始化。新项目走问答生成规约，既有项目分层采样推断规约。首次使用 TWS 必须调用
---

<SUBAGENT-STOP>
This skill initializes or rewrites project conventions. If you are a subagent, do not invoke it unless the main agent or user explicitly asked for initialization.
</SUBAGENT-STOP>

# TWS 项目初始化

## 核心原则

**规约不是约束，是给 AI 和团队看的「说明书」。**

---

## 流程

```
① 检测技术栈 + 判断项目状态 + 开发平台 → ② 按策略生成规约 → ③ CONTRACTS.md 生成（可选）→ ④ 确认 → ⑤ 保存
```

## ① 检测

```
有 .git 历史 + 有代码文件 → 既有项目（走采样推断）
无代码文件或少代码       → 新项目（走问答）
```

同时检测技术栈：

```
扫 package.json / requirements.txt / pom.xml / go.mod / Cargo.toml
→ 输出技术栈列表：Python(FastAPI) + TypeScript(React) 等
```

同时检测开发平台，用于生成同等 skill 调用映射：

```
用户明确说明 / 当前运行环境可识别 → 采用明确平台
运行环境或用户说明为 Codex / 存在 .agents/skills → Codex
有 Claude Code 原生 Skill 工具 / .claude/skills → Claude Code
运行环境或用户说明为 VSCode / 编辑器插件 → VSCode
无法判断 → Other（按平台中立映射）
```

平台检测不改变 TWS 流程，只决定 `.tws/platform-skills.md` 中如何说明 skill 调用方式。

### 确定性生成脚本（优先）

生成或刷新 `.tws/tws-version`、`.tws/platform-skills.md`、`CLAUDE.md` / `AGENTS.md` TWS 托管区块时，优先运行本 skill 的脚本，减少手写模板漂移：

```bash
python tws-init/scripts/tws_bootstrap.py --platform Codex --project-root <目标项目> --write
python tws-init/scripts/tws_bootstrap.py --platform "Claude Code" --project-root <目标项目> --write
python tws-init/scripts/tws_bootstrap.py --platform VSCode --emit platform-skills
```

无脚本运行条件时，才按下文模板手写；手写后必须确认字段与脚本输出结构一致。

---

## ② 按策略生成

### 既有项目 — 分层策略

不同规约类型的生成策略不同：

```
┌──────────────┬────────────────────────────────────────────────────┐
│ 规约类型     │ 生成策略                                          │
├──────────────┼────────────────────────────────────────────────────┤
│ 代码规约     │ 采样推断（每栈 2 个源码文件）                      │
│ 测试规约     │ 推断框架（每栈 1 个测试文件）+ 模板补策略          │
│ 设计书规约   │ 纯模板（TWS 定义格式，项目里不会有）               │
│ 环境规约     │ 扫描配置文件 + 模板补策略                          │
│ 架构决策     │ 空模板（使用中积累，无法推断）                     │
└──────────────┴────────────────────────────────────────────────────┘
```

#### 步骤 2a：代码规约推断（每栈采样 2 个源码文件）

```
采样目标：每种技术栈找 2 个核心文件
  Python → main.py + 一个 service/api 文件
  Java → Application.java + 一个 Controller
  TypeScript → App.tsx + 一个页面组件
  Go → main.go + 一个 handler
  没有对应文件 → 找同目录下任意 2 个源码文件

每个文件只看 4 个维度：
  命名风格（snake_case / camelCase / PascalCase）
  缩进（2空格 / 4空格 / tab）
  引号（单引号 / 双引号）
  注释习惯（行内 / docstring / JSDoc）

按技术栈套默认规约，用采样结果覆盖不一致的部分：
  Python 默认：snake_case, 4空格, 双引号, docstring
  Java 默认：camelCase, 4空格, 双引号, Javadoc
  TypeScript 默认：camelCase, 2空格, 单引号, JSDoc
  Go 默认：camelCase, tab, 双引号, 行内注释

置信度标注：
  2/2 一致 → 高（直接确认）
  1/2 一致 → 中（提示用户确认）
  0/2 → 用默认值，标注"未能从代码推断"
```

#### 步骤 2b：测试规约推断（每栈采样 1 个测试文件）

```
采样目的：识别测试框架和命名风格
  Python → test_*.py 或 *_test.py → 推断 pytest / unittest
  Java → *Test.java → 推断 JUnit / TestNG
  TypeScript → *.test.ts 或 *.spec.ts → 推断 Jest / Vitest
  Go → *_test.go → 标准库 testing

没有测试文件 → 按技术栈给默认框架，标注"无现有测试，使用默认"

推断结果 + TWS 模板策略 合并为测试规约：
  推断的：框架、命名风格
  模板的：覆盖标准、边界条件要求、重试机制、回归验证流程
```

#### 步骤 2c：设计书规约（纯模板）

```
设计书格式由 TWS 定义（见 design-doc skill 的模板），项目里不会有。
直接按模板生成，不采样。

模板内容：
  必填章节（涉及模块 / 实现方案 / 接口变更 / 测试要点 / 决策记录 / 变更履历）
  格式规范（标题层级、表格、代码块）
  变更履历管理规则
```

#### 步骤 2d：环境规约（扫描 + 模板）

```
扫描根目录找配置文件：
  docker-compose.yml / .env / config.* / CI 配置文件

从配置文件推断：
  数据库类型和连接方式
  外部依赖（Redis / MQ / OSS 等）
  测试数据库配置

TWS 模板补充：
  mock 策略（优先 mock，不动真实环境）
  依赖安装策略（先问再装）
  环境问题处理流程（报报告 → 问开发者 → 记入 env-rules.md）

没有配置文件 → 生成纯模板，标注"未检测到配置文件，请后续补充"
```

#### 步骤 2e：架构决策（空模板）

```
生成空文件，只写标题和说明：
  "本文件记录项目的架构决策，由 design-sync、impact-assessment 等流程使用中积累。"

后续流程会往里追加条目。不采样，不推断。
```

#### 总采样量

```
每栈 3 个文件（2 源码 + 1 测试）+ 1 次根目录配置扫描
= 3N + 1（N = 技术栈数量）

典型：
  单栈（Python）→ 4
  双栈（Python + React）→ 7
  微服务（Python + Java + Go）→ 10
```

### 新项目 — 问答

```
按技术栈生成默认规约模板 → 逐条询问用户调整
新项目无代码可采样，所有规约走模板 + 用户确认。
```

---

## ③ CONTRACTS.md 生成（可选）

如果项目检测到多人协作，或用户选择启用团队模式：

```
既有项目：
  1. 扫描代码中的 API 路由（FastAPI/@RequestMapping/Express 等）
  2. 扫描事件定义（event_bus.emit、EventEmitter）
  3. 扫描公共函数接口（export、public 方法）
  4. 生成初始 CONTRACTS.md
  5. 标注「消费者信息需要人工补充」

新项目：
  → 先不生成，等第一个接口出现时提示「是否记录到 CONTRACTS.md」
```

---

## ④ 确认

输出推断结果，用户确认或微调：

```
📋 项目类型：既有项目（Python + FastAPI）
📋 技术栈：Python(FastAPI) + TypeScript(React)
📋 代码规约（Python）：snake_case, 4空格, 双引号 — 高置信度
📋 代码规约（TypeScript）：camelCase, 2空格, 单引号 — 高置信度
📋 测试框架：pytest + Jest
📋 环境配置：SQLite内存 + mock外部依赖

可以这样吗？还是有要改的？
```

---

## ⑤ 保存

```
.tws/
├── project-map.md                        ← 项目结构索引（所有 agent 启动时先读此文件）
├── platform-skills.md                    ← Claude Code / Codex / VSCode 的 skill 调用映射
├── tws-version                           ← 当前项目使用的 TWS Skills 版本和 source root 指纹
├── coding-conventions.md                 ← 代码规约（单栈项目无后缀）
├── coding-conventions-{栈}.md            ← 代码规约（多栈项目按栈分文件）
├── testing-conventions.md                ← 测试规约（单栈项目无后缀）
├── testing-conventions-{栈}.md           ← 测试规约（多栈项目按栈分文件）
├── design-conventions.md                 ← 设计书格式规约（不限技术栈）
├── env-conventions.md                    ← 环境规约（不限技术栈）
├── architecture-decisions.md             ← 架构决策（空模板，使用中积累）
├── env-rules.md                          ← 环境问题决策记录（使用中积累）
├── sessions/                             ← 会话断点续传状态（每个流程一个文件）
└── deferred-issues.md                    ← 延迟问题列表（审查中积累）
```

同时必须写入或更新项目级持久入口，避免新会话只看到目标项目而看不到 TWS。只有平台没有项目级入口，或用户明确拒绝写入时，才改为输出可复制内容并声明无法默认主动生效。完整模板以根目录 `PLATFORM-SUPPORT.md` 的“项目级持久入口”为准，本 skill 只规定写入责任和必填字段：

- Claude Code：项目根 `CLAUDE.md`
- Codex：项目根 `AGENTS.md`
- VSCode / 编辑器插件：插件配置、workspace instruction 或项目 prompt

写入时保留既有内容，只追加或更新 TWS 托管区块，不覆盖用户已有规则。若已存在旧的 TWS 托管区块，必须整体替换为当前版本，不追加第二份，防止 Claude Code / Codex 新会话继续读到旧规则。

TWS 托管区块必须使用稳定边界：

```markdown
<!-- TWS:BEGIN managed by TWS Skills {version} -->
...
<!-- TWS:END -->
```

TWS 区块必须包含：`TWS version`、`skill source root`、`skill source VERSION`、`skill source root realpath`、`skill source commit`、`skill source fingerprint`、`using-tws/SKILL.md`、`tws-init/SKILL.md`、新会话读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`、上下文压缩恢复协议、未完成 `.tws/sessions/` 的恢复规则、无原生 loader 的显式读取规则、用户可明确跳过 TWS。

同时写入 `.tws/tws-version`：

```markdown
version: {读取根目录 VERSION}
skill_source_root: {TWS-Skills 仓库路径或安装路径}
skill_source_root_realpath: {解析后的真实路径}
skill_source_version: {读取 {skill_source_root}/VERSION}
skill_source_commit: {git rev-parse --short HEAD；不可用则 unknown}
skill_source_fingerprint: {skill_source_version + skill_source_commit；无 git 时用 VERSION + 目录内容 hash/时间戳}
updated_at: {YYYY-MM-DD HH:MM}
```

如果目标工程已经覆盖了新版 TWS Skills 仓库，`tws-init` 必须更新 `.tws/tws-version`、`.tws/platform-skills.md` 和项目级持久入口中的 TWS 托管区块，使 Claude Code / Codex 新会话读取的是新版入口，而不是旧的项目说明。
刷新判断必须直接读取 `{skill source root}/VERSION`，不能只相信旧 `.tws/tws-version` 或旧托管区块。

### project-map.md 格式

```markdown
## 技术栈
{技术栈列表}

## 关键入口
- {用途}：{文件路径}

## 路径映射
- {源码目录} → {设计书目录}

## 规约索引（按需读取，不要预加载全量）
### 按技术栈（单栈项目无后缀，多栈带栈名后缀）
- {栈} 编码规约：.tws/coding-conventions-{栈}.md
- {栈} 测试规约：.tws/testing-conventions-{栈}.md

### 共享规约（不限技术栈）
- 设计书规约：.tws/design-conventions.md
- 环境规约：.tws/env-conventions.md
- 架构决策：.tws/architecture-decisions.md
```

保存后所有 TWS skill 启动时自动读取 project-map.md，按需加载规约。

### platform-skills.md 格式

完整解释和示例以根目录 `PLATFORM-SUPPORT.md` 为准。初始化时生成的 `.tws/platform-skills.md` 必须包含：

```
□ 当前平台：Claude Code / Codex / VSCode / Other
□ skill 源目录
□ TWS version（读取根目录 VERSION）
□ `.tws/tws-version` 的版本、skill source root、realpath、commit、fingerprint
□ 所有映射路径基准
□ Claude Code 原生可发现：是/否 + 实际路径
□ Codex 原生可发现：是/否 + 实际路径
□ VSCode 兼容 loader：是/否/未知 + 扫描规则 `{sourceRoot}/*/SKILL.md`
□ 是否支持子 agent：是/否/未知
□ `/tws-init` → `tws-init/SKILL.md`
□ `/using-tws` → `using-tws/SKILL.md`
□ 新会话恢复规则
□ 上下文压缩 / 摘要恢复 / 新窗口续跑恢复规则
□ 无原生 skill loader / 无子 agent / 无隔离上下文的降级规则
□ 完整 skill 映射表：枚举 `{skill 源目录}/*/SKILL.md`，不得用 `...` 代替
□ 映射表列包含 `canonical_id`、`path`、`display_name`、`aliases`
□ `canonical_id` 必须等于目录名；frontmatter `name` 只作展示/别名
□ 至少覆盖 `using-tws`、`tws-init`、`flow-refactor`、`flow-fix-bug`、`comp-subagent-dispatch`、`comp-impact-assessment`、`comp-migration-plan`、`comp-implementation`、`comp-test`、`comp-design-sync`
```

### 规约的用途

| 规约 | 谁读 | 生成方式 |
|------|------|---------|
| platform-skills | 所有 agent | 平台检测 + 模板 |
| coding-conventions | 编码中的 agent | 采样推断 |
| testing-conventions | 写和执行测试的 agent | 推断框架 + 模板 |
| design-conventions | 写设计书的 agent | 纯模板 |
| env-conventions | 遇到环境问题的 agent | 扫描 + 模板 |
| architecture-decisions | 所有 agent | 空模板，使用中积累 |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「项目很小，不用初始化」 | 初始化让后续 agent 有共同上下文 |
| 「规约可以凭经验写」 | 既有项目要采样，避免把外部偏好强加给项目 |
| 「先跑流程，缺规约再说」 | 缺规约会让设计、测试和同步标准漂移 |
