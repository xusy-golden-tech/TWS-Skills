# TWS Skills

TWS（Thoughtful Workflow System）是一组结构化 AI skill，用来把软件开发任务拆成可确认、可执行、可验证的流程。

本仓库不是传统应用项目，没有统一构建产物；主要内容是各目录下的 `SKILL.md`，外加少量增强型 skill 的脚本和数据。

## 快速开始

1. 克隆仓库到本地，例如 `D:\TWS_Skills`。
2. 选择一种安装形态：平台原生自动识别，或整仓嵌入后显式映射。
3. 在目标项目中先运行 `/tws-init`，生成 `.tws/` 项目规约。
4. 日常使用 `/using-tws 你的需求` 进入对应流程。

更新说明：如果目标工程已经复制覆盖了新版 TWS Skills，请重新运行 `/tws-init`，刷新 `.tws/tws-version`、`.tws/platform-skills.md` 和项目根 `CLAUDE.md` / `AGENTS.md` 中的 TWS 托管区块。否则新会话可能继续读取旧入口说明。

## 配置示例

## 安装形态

### 形态 A：平台原生自动识别

如果希望工具自动发现并在 skill 列表中显示 TWS skills，必须把每个 skill 目录放到对应平台的发现目录。

Claude Code：

- 用户级：`~/.claude/skills/{skill-name}/SKILL.md`
- 项目级：`{project}/.claude/skills/{skill-name}/SKILL.md`
- 额外目录：通过 `--add-dir` 加入的目录中放置 `.claude/skills/{skill-name}/SKILL.md`

Codex：

- 项目级：`{project}/.agents/skills/{skill-name}/SKILL.md`
- 用户级：`~/.agents/skills/{skill-name}/SKILL.md`
- 管理员级：`/etc/codex/skills/{skill-name}/SKILL.md`

本仓库当前是“多 skill 平铺源码仓库”。直接把 `D:\TWS_Skills` 作为普通目录放进项目，Claude Code/Codex 不会自动把一级目录识别为原生 skills。需要复制或软链接每个 skill 目录到上述发现目录。

### 形态 B：整仓嵌入为 skill source root

如果想把本仓库全文件放进另一个项目，例如：

```text
target-project/
├── vendor/
│   └── TWS_Skills/
│       ├── using-tws/SKILL.md
│       ├── tws-init/SKILL.md
│       └── ...
└── .tws/
```

这种形态不会保证平台原生自动发现。它的正确用法是把 `vendor/TWS_Skills` 记录为 `skill source root`，并通过 `.tws/platform-skills.md` 或兼容 loader 显式映射：

```markdown
- skill 源目录：./vendor/TWS_Skills
- `/using-tws` → `./vendor/TWS_Skills/using-tws/SKILL.md`
- `Skill(skill: "flow-fix-bug")` → `./vendor/TWS_Skills/flow-fix-bug/SKILL.md`
```

VSCode 或编辑器插件若要兼容 TWS，至少需要扫描 `{sourceRoot}/*/SKILL.md`。

## 配置示例

兼容客户端如果支持自定义 skill source root，可以把本仓库根目录或嵌入目录加入搜索路径。以下 JSON 仅作为兼容客户端示例，不是 Claude Code/Codex 官方 settings 键名：

Windows 示例：

```json
{
  "skillSearchPaths": ["D:\\TWS_Skills"]
}
```

Unix 示例：

```json
{
  "skillSearchPaths": ["/home/me/TWS_Skills"]
}
```

配置或安装后可用 `/tws-init` 或 `/using-tws` 验证 skill 是否被发现。

## 目录结构

| 类型 | 目录 | 说明 |
|------|------|------|
| 入口 | `using-tws/`, `tws-init/` | 初始化和场景路由 |
| 流程 | `flow-*/` | 添加功能、修 bug、热修、重构、排查等完整流程 |
| 组件 | `comp-*/` | 设计、实现、测试、审查、影响评估等可执行单元 |
| 基础 | `found-*/` | 全局原则、审查方法、产物拆分等基础规则 |
| 团队 | `team-*/` | 契约、分支、环境、影响上报等协作规则 |

完整依赖关系见 `SKILL-INDEX.md`。

## 新会话是否默认生效

结论分三层：

1. 只把 TWS 仓库放进目标项目，例如 `vendor/TWS_Skills/`：不会默认生效，只能作为 `skill source root` 显式读取。
2. 安装到 `.claude/skills/` 或 `.agents/skills/`：skill 可被平台发现，但是否自动触发取决于平台选择逻辑。
3. 同时在目标项目根写入持久入口：新会话才能稳定默认进入 TWS。

Claude Code 使用目标项目 `CLAUDE.md`，Codex 使用目标项目 `AGENTS.md`。两者都应说明：开发、修复、重构、排查、文档类多步骤工程任务默认先走 `using-tws/SKILL.md`；如果 `.tws/project-map.md` 不存在先走 `tws-init/SKILL.md`；新会话或上下文压缩恢复时先读取 `.tws/tws-version`、`.tws/project-map.md` 和 `.tws/platform-skills.md`；若 `.tws/tws-version` 与当前 source `VERSION` / root / realpath / commit / fingerprint 不一致，先刷新入口；若 `.tws/sessions/` 有未完成流程，先询问是否续上。VSCode 或其他编辑器插件需要把同等说明放进插件配置、workspace instruction 或项目 prompt。

首次无原生 loader 时，还没有 `.tws/platform-skills.md`。这时必须从已知的 `skill source root` 直接读取 `tws-init/SKILL.md` 完成初始化；之后再依赖 `.tws/platform-skills.md` 做 skill 映射。

项目级入口使用 `<!-- TWS:BEGIN managed by TWS Skills {version} -->` / `<!-- TWS:END -->` 托管区块。升级或覆盖 TWS 后，应替换该区块而不是追加第二份，并同步 `.tws/tws-version` 中的 source root、realpath、commit 和 fingerprint。

可用 `tws-init/scripts/tws_bootstrap.py` 生成或刷新这些入口文件，降低手写模板漂移：

```powershell
python tws-init/scripts/tws_bootstrap.py --platform Codex --project-root <目标项目> --write
python tws-init/scripts/tws_bootstrap.py --platform "Claude Code" --project-root <目标项目> --write
```

## Codex 或其他平台使用

TWS 对 Claude Code、Codex、VSCode 使用同一套 skills。`SKILL.md` 是唯一源头；不同平台只做加载和执行方式的适配。完整说明见 `PLATFORM-SUPPORT.md`。

部分 skill 使用 Claude Code 的工具语义，例如 `Skill(...)`、`Agent(...)`、`Read/Edit/Write`。在 Codex、VSCode 或其他环境中，按平台中立含义执行：

- `Skill(skill: "xxx")`：读取对应 `xxx/SKILL.md` 并遵循其中约束。
- `Agent(...)`：使用可用的子 agent 或隔离上下文；不可用时由主 agent 按组件 skill 自我约束执行。
- `Read/Edit/Write`：使用当前平台提供的读取和编辑工具。

`/tws-init` 会在目标项目 `.tws/platform-skills.md` 中记录当前平台的 skill 调用映射，确保 Codex/VSCode 与 Claude Code 使用同等语义的入口、流程和组件。

核心要求是不变的：先确认流程，明确产出物，逐步验证，不跳过设计、测试和同步。

## 本地校验

需要 Python 3.9+。请从仓库根目录运行；Windows 如 `python` 不可用，可用 `py -3 validate-skills.py`。

仓库提供轻量校验脚本：

```powershell
python validate-skills.py
```

它会检查 skill frontmatter、`SKILL-INDEX.md` 路径、`<SUBAGENT-STOP>`、平台入口、source fingerprint、上下文压缩恢复协议、flow 交接收口、bootstrap 生成器、UI Gate、CSV 基础可读性，并阻止 `__pycache__` / `.pyc` 进入仓库。

## 常见问题

- 中文乱码：文件使用 UTF-8。Windows PowerShell 读取时建议显式指定 `-Encoding utf8`。
- 找不到 `/tws-init`：如果使用 Claude Code/Codex 原生 skills，确认 skill 已安装到 `.claude/skills/` 或 `.agents/skills/`；如果只是整仓嵌入，确认 `.tws/platform-skills.md` 记录了正确的 skill 源目录。
- UI 设计脚本路径不对：从仓库根目录运行 `python comp-frontend-ui-design/scripts/search.py ...`，或在已安装 skill 目录中使用相同的相对路径。
- `.tws/` 是否提交：通常不提交，它是目标项目的本地流程状态和规约目录。
