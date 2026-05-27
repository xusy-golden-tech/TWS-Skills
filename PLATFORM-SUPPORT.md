# Platform Support

TWS 的目标是让 Claude Code、Codex、VSCode 等开发环境使用同一套 skills，而不是维护多份互相漂移的 prompt。

## 核心原则

同一个 skill 目录下的 `SKILL.md` 是唯一源头：

```
using-tws/SKILL.md
flow-fix-bug/SKILL.md
comp-implementation/SKILL.md
...
```

不同平台只负责“发现、加载、执行方式”的适配，不改变 skill 的语义。

## 平台等价表

| TWS 概念 | Claude Code | Codex | VSCode / 编辑器插件 |
|----------|-------------|-------|---------------------|
| 原生 skill 安装 | 放入 `~/.claude/skills/`、项目 `.claude/skills/` 或 `--add-dir` 下的 `.claude/skills/` | 放入项目 `.agents/skills/`、用户 `~/.agents/skills/` 或管理员级 skills 目录 | 使用插件支持的 prompt/skill/command 目录；若无标准则采用下面的最低发现协议 |
| 整仓嵌入 | 作为 source root，不会自动发现；需复制/链接到 `.claude/skills` 或显式读取 | 作为 source root，不会自动发现；需复制/链接到 `.agents/skills` 或显式读取 | 可作为 source root，前提是插件按最低发现协议扫描 |
| 调用入口 | `/tws-init`, `/using-tws` | 等价调用 `tws-init/SKILL.md`, `using-tws/SKILL.md` | 等价调用 `tws-init/SKILL.md`, `using-tws/SKILL.md` |
| `Skill(skill: "x")` | 原生 Skill 工具加载 | 读取 `x/SKILL.md` 并遵循其中规则 | 读取 `x/SKILL.md` 并遵循其中规则 |
| 子 agent | `Agent(...)` | 可用子 agent 时使用；不可用时当前 agent 按子 agent 约束执行 | 取决于插件；不可用时当前 agent 按子 agent 约束执行 |
| 文件工具 | `Read/Edit/Write` | Codex 文件读取/编辑工具 | 插件或编辑器文件操作 |

## 整仓嵌入规则

把本仓库全文件放入另一个项目时，推荐放在稳定子目录：

```text
target-project/vendor/TWS_Skills/
```

这称为 `skill source root`。所有相对映射路径都以该目录为基准解析。

注意：

- `vendor/TWS_Skills/using-tws/SKILL.md` 可以作为显式读取路径。
- Claude Code 不会因为存在 `vendor/TWS_Skills/*/SKILL.md` 就自动发现 skills。
- Codex 不会因为存在 `vendor/TWS_Skills/*/SKILL.md` 就自动发现 skills。
- 要原生自动发现，必须复制或软链接到对应平台发现目录。

## 兼容客户端最低发现协议

VSCode 或其他编辑器插件若要“导入 TWS_Skills 仓库后自动识别”，至少实现以下协议：

1. 配置一个 `skill source root`，例如 `./vendor/TWS_Skills`
2. 枚举 `{sourceRoot}/*/SKILL.md`，不递归进入任意深层目录作为 skill
3. canonical skill id 必须使用目录名；frontmatter `name` 只作展示/别名，不作为调用 ID，也不得要求它与目录名相同
4. `/using-tws` 映射到 `{sourceRoot}/using-tws/SKILL.md`
5. `/tws-init` 映射到 `{sourceRoot}/tws-init/SKILL.md`
6. `Skill(skill: "x")` 映射到 `{sourceRoot}/{x}/SKILL.md`
7. 若插件没有命令系统，用户显式要求读取对应 `SKILL.md` 也视为正式激活 skill

## 项目级持久入口

原生 skill 安装只保证 skill 可以被发现，不保证每个新会话都会默认、主动进入 TWS。若希望同一个工程打开新会话后仍然优先遵循 TWS，目标项目必须有会被该平台自动读取的项目级入口。

Claude Code 项目建议在项目根 `CLAUDE.md` 保留以下 TWS 托管区块。更新 TWS Skills 后，应替换该区块，而不是追加第二份：

```markdown
<!-- TWS:BEGIN managed by TWS Skills {version} -->
## TWS

本项目使用 TWS。

- TWS version: {version}
- skill source root: {skill source root}
- skill source VERSION: {读取 {skill source root}/VERSION}
- skill source root realpath: {解析后的 skill source root}
- skill source commit: {短 commit；不可用则 unknown}
- skill source fingerprint: {VERSION + commit/hash}
- 开发、修复、重构、排查、文档类多步骤工程任务，默认先调用 `/using-tws`；不得只按本区块直接开发。
- 如果 `.tws/project-map.md` 不存在，先调用 `/tws-init`。
- 如果 `/using-tws` 不可用，读取 `{skill source root}/using-tws/SKILL.md` 并按其中流程执行。
- 新会话开始时先读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`；若 `.tws/sessions/` 有未完成流程，先询问是否续上。
- 发生上下文压缩、摘要恢复或新窗口续跑时，按 `using-tws/SKILL.md` 的“上下文压缩恢复协议”重新读取 TWS 文件，不得只凭摘要继续。
- 如果 `.tws/tws-version` 与本区块或 `{skill source root}/VERSION` 的 version/root/realpath/commit/fingerprint 任一不一致，先运行 `/tws-init` 或读取当前 `tws-init/SKILL.md` 刷新入口映射。
- 用户明确要求跳过 TWS 时才跳过。
<!-- TWS:END -->
```

Codex 项目建议在项目根 `AGENTS.md` 保留同等 TWS 托管区块：

```markdown
<!-- TWS:BEGIN managed by TWS Skills {version} -->
## TWS

This project uses TWS.

- TWS version: {version}
- skill source root: {skill source root}
- skill source VERSION: {read {skill source root}/VERSION}
- skill source root realpath: {resolved skill source root}
- skill source commit: {short commit or unknown}
- skill source fingerprint: {VERSION + commit/hash}
- For development, fix, refactor, investigation, documentation, or other multi-step engineering tasks, start from `using-tws/SKILL.md`; do not implement directly from this block.
- If `.tws/project-map.md` is missing, run `tws-init/SKILL.md` first.
- If native skills are unavailable, read `{skill source root}/using-tws/SKILL.md` directly and follow it.
- At the beginning of a new session, read `.tws/project-map.md` and `.tws/platform-skills.md`; if `.tws/sessions/` contains unfinished flows, ask whether to resume.
- After context compaction, summary restore, or a new window continuation, follow the context recovery protocol in `using-tws/SKILL.md`; do not continue from summary alone.
- If `.tws/tws-version` does not match this block or `{skill source root}/VERSION` on version/root/realpath/commit/fingerprint, run `tws-init/SKILL.md` to refresh TWS entry mappings before development work.
- Skip TWS only when the user explicitly asks to skip it.
<!-- TWS:END -->
```

VSCode 或其他编辑器插件没有统一的项目入口文件。兼容插件应把同等内容写入插件配置、项目 prompt、workspace instruction，或任何新会话会自动读取的位置。若没有这种机制，就只能作为显式调用降级方案，不能声明为默认主动生效。

`.tws/platform-skills.md` 是运行期映射文件，不是唯一 bootstrap 入口。首次使用且尚未生成 `.tws/platform-skills.md` 时，必须从已知的 `skill source root` 直接读取 `tws-init/SKILL.md`；初始化后再依赖 `.tws/platform-skills.md` 做流程内映射。

若目标工程通过复制覆盖升级了 `vendor/TWS_Skills/`，还必须重新运行 `/tws-init` 或显式读取新版 `tws-init/SKILL.md`，刷新 `.tws/tws-version`、`.tws/platform-skills.md`、`CLAUDE.md` / `AGENTS.md` 的 TWS 托管区块。否则平台可能继续遵循旧项目入口说明。即使旧 `.tws/tws-version` 与旧托管区块相互一致，只要 `{skill source root}/VERSION` 或 fingerprint 与当前源码不一致，也以当前源码为准先刷新。

## 初始化时生成的适配文件

`/tws-init` 应在目标项目的 `.tws/` 中生成：

```
.tws/platform-skills.md
.tws/tws-version
```

这些文件记录当前项目使用的平台、TWS 版本、skill source root，以及如何把 TWS skill 调用映射到该平台。

建议内容：

```markdown
## 平台
- 当前平台：Claude Code / Codex / VSCode / Other
- TWS version：{读取根目录 VERSION}
- skill 源目录：{TWS-Skills 仓库路径或安装路径}
- skill source root realpath：{解析后的真实路径}
- skill source commit：{短 commit；不可用则 unknown}
- skill source fingerprint：{VERSION + commit/hash}
- updated_at：{YYYY-MM-DD HH:MM}
- 所有映射路径基准：相对 skill 源目录解析
- Claude Code 原生可发现：是/否（实际路径：{.claude/skills 路径或无}）
- Codex 原生可发现：是/否（实际路径：{.agents/skills 路径或无}）
- VSCode 兼容 loader：是/否/未知（扫描规则：{sourceRoot}/*/SKILL.md）
- 是否支持子 agent：是/否/未知

## 调用映射
- `/tws-init` → `tws-init/SKILL.md`
- `/using-tws` → `using-tws/SKILL.md`
- `Skill(skill: "comp-test")` → 读取 `comp-test/SKILL.md`
- `Skill(skill: "flow-fix-bug")` → 读取 `{skill 源目录}/flow-fix-bug/SKILL.md`

## 新会话恢复
- 新会话先读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`
- 开发、修复、重构、排查、文档类多步骤任务默认进入 `using-tws/SKILL.md`
- 若 `.tws/sessions/` 存在未完成流程，先询问是否续上
- 上下文压缩、摘要恢复、新窗口续跑时，按 `using-tws/SKILL.md` 的恢复协议重新读取 `.tws/tws-version`、project-map、platform-skills、session、flow 和 dispatch
- 若原生入口不可用，显式读取 `{skill 源目录}/using-tws/SKILL.md`

## 完整 skill 映射
初始化时必须枚举 `{skill 源目录}/*/SKILL.md`，为每个 skill 生成一行映射；不得用 `...` 代替完整列表。至少包含：

canonical_id 必须等于目录名；`display_name`/`aliases` 只辅助展示或兼容旧称，不能改变调用 ID。

| canonical_id | path | display_name | aliases |
|--------------|------|--------------|---------|
| using-tws | `{skill 源目录}/using-tws/SKILL.md` | using-tws | - |
| tws-init | `{skill 源目录}/tws-init/SKILL.md` | tws-init | - |
| flow-fix-bug | `{skill 源目录}/flow-fix-bug/SKILL.md` | flow-fix-bug | - |
| flow-refactor | `{skill 源目录}/flow-refactor/SKILL.md` | flow-refactor | - |
| comp-subagent-dispatch | `{skill 源目录}/comp-subagent-dispatch/SKILL.md` | comp-subagent-dispatch | - |
| comp-impact-assessment | `{skill 源目录}/comp-impact-assessment/SKILL.md` | comp-impact-assessment | - |
| comp-migration-plan | `{skill 源目录}/comp-migration-plan/SKILL.md` | comp-migration-plan | migration-plan |
| comp-implementation | `{skill 源目录}/comp-implementation/SKILL.md` | comp-implementation | implementation |
| comp-test | `{skill 源目录}/comp-test/SKILL.md` | comp-test | test |
| comp-design-sync | `{skill 源目录}/comp-design-sync/SKILL.md` | comp-design-sync | design-sync |

## 降级规则
- 无原生 skill loader：显式读取对应 `SKILL.md`
- 无子 agent：标注“按子 agent 约束由当前 agent 执行”
- 无隔离上下文：只提供必要文件和客观背景，避免复用执行者推理
```

## 上下文预算

TWS 默认按需加载，不预加载全仓。若会话已经很长，或当前客户端启用了很多 MCP / 插件工具，入口 skill 只提醒一次，由用户决定是否调整。

建议检查：

- 浏览器 / Playwright：当前是否有 UI 预览、截图或端到端测试任务。
- GUI 自动化：当前是否确实需要操作本机界面。
- 图片分析：当前是否需要看截图、设计图或视觉资产。
- 大型连接器：当前是否需要读取外部文档库、工单或云端文件。

如果当前任务不涉及这些能力，建议临时禁用对应工具或避免在本流程中调用。不要为了节省上下文而跳过 TWS 的流程门禁、测试、审查或设计同步。

## 维护规则

- 不为 Codex、VSCode 复制一份改名后的 skill。复制会导致规则漂移。
- 如需平台差异，写在 `PLATFORM-SUPPORT.md`、`README.md` 或 `.tws/platform-skills.md` 中。
- 修改某个 skill 的核心流程时，默认所有平台同时生效。
