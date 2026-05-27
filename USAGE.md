# TWS 使用说明

## 前置条件

Claude Code 官方方式是把 skill 目录放到 `~/.claude/skills/`、项目 `.claude/skills/`，或 `--add-dir` 目录内的 `.claude/skills/`。Codex 原生方式是把 skill 目录放到 `.agents/skills/`、`~/.agents/skills/` 或管理员级 skills 目录。本仓库是多 skill 平铺源码仓库，安装时可将各 skill 目录复制或软链接到对应平台发现目录。

Codex、VSCode 或其他兼容客户端也使用同一套 `SKILL.md`。如果只是把本仓库全文件放进项目目录，例如 `vendor/TWS_Skills/`，它只是 skill source root，不等于平台原生安装；必须按 `PLATFORM-SUPPORT.md` 和 `.tws/platform-skills.md` 显式映射。

兼容客户端搜索路径示例（非 Claude Code 官方 settings 键名）：

```json
{
  "skillSearchPaths": ["D:\\TWS_Skills"]
}
```

不同客户端的配置键名可能不同。更完整的安装、Codex 适配和故障排查见 `README.md`。

## 第一步：初始化（每个项目做一次）

在项目根目录下启动初始化：

- Claude Code：输入 `/tws-init`
- Codex：原生安装时用 `$tws-init` 或 skill 选择器；未原生安装时显式读取 `tws-init/SKILL.md`
- VSCode/编辑器插件：有命令机制时调用等价命令；否则显式读取 `tws-init/SKILL.md`

TWS 会扫描项目，推断技术栈和代码规约，在项目下生成 `.tws/` 目录。以后所有流程都依赖这个目录。

同时会生成 `.tws/platform-skills.md`，记录当前平台（Claude Code / Codex / VSCode / Other）如何调用同等语义的 TWS skills。

如果跳过这步，后续流程会提醒你先初始化。

## 新会话恢复和默认入口

同一工程开新会话后能否继续默认遵循 TWS，取决于目标项目是否有平台会自动读取的持久入口：

- Claude Code：项目根 `CLAUDE.md`
- Codex：项目根 `AGENTS.md`
- VSCode / 编辑器插件：插件配置、workspace instruction 或项目 prompt

入口内容应要求新会话先读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`，并把开发、修复、重构、排查、文档类多步骤工程任务默认路由到 `using-tws/SKILL.md`。如果 `.tws/project-map.md` 不存在，先执行 `tws-init/SKILL.md`；如果 `.tws/sessions/` 有未完成流程，先询问是否续上。

只安装或嵌入 skills 不等于默认主动生效。没有原生 loader 或项目级入口时，必须显式读取 `{skill source root}/using-tws/SKILL.md` 或 `{skill source root}/tws-init/SKILL.md`。

## 第二步：日常使用

先激活 TWS 入口，然后用自然语言告诉它你要做什么：

- Claude Code：`/using-tws 加个用户注册功能`
- Codex：原生安装时 `$using-tws 加个用户注册功能` 或通过 skill 选择器；未原生安装时显式读取 `using-tws/SKILL.md`
- VSCode/编辑器插件：调用等价命令；无命令机制时显式读取 `using-tws/SKILL.md`

TWS 会自动判断场景并路由到对应流程：

| 你说什么 | TWS 走什么流程 |
|---------|--------------|
| 「加个 XX 功能」 | 添加功能 |
| 「这个坏了 / 报错 / 行为不对」 | 修复 Bug |
| 「紧急！生产挂了」 | 热修复 |
| 「重构 XX / 优化 XX」 | 重构 |
| 「从零建一个新项目」 | 新建项目 |
| 「查一下为什么 XX」 | 排查（只查不修） |
| 「补一下 XX 的文档」 | 写文档 |

也可以先输 `/using-tws`，等它响应后再描述需求。

## 第三步：确认计划，然后等着

TWS 判断完场景后会输出一个执行计划，问你"要开始吗？"。确认后它会自动拆任务、派子 agent 去做。你只需要在关键节点做确认（比如确认设计书、确认方案）。

如果做到一半会话断了，下次开新会话时 TWS 会检测到未完成的流程，问你要不要续上。

## 工作目录说明

初始化后项目里会多一个 `.tws/` 目录：

```
.tws/
├── project-map.md           ← 项目索引，agent 启动时先读这个
├── coding-conventions.md    ← 代码规约
├── testing-conventions.md   ← 测试规约
├── design-conventions.md    ← 设计书格式规约
├── env-conventions.md       ← 环境规约
├── architecture-decisions.md ← 架构决策（使用中积累）
├── platform-skills.md       ← Claude Code / Codex / VSCode 的 skill 调用映射
└── sessions/                ← 运行中的流程状态（完成后自动清理）
```

这个目录建议加到 `.gitignore`，不需要提交到仓库。

## 团队模式

TWS 有 Solo 和 Team 两种模式。每次 `/using-tws` 时会自动检测，不需要手动切换。

**自动检测逻辑：** 项目根目录存在 `CONTRACTS.md`，或者 git 历史中有 2 个及以上作者 → Team 模式。否则 → Solo 模式。

**如果需要启用团队模式：**

1. 运行 `/tws-init` 时告诉它是团队项目，例如：
   ```
   /tws-init 这是团队项目，需要启用团队模式
   ```
   TWS 会扫描项目中的 API 路由、事件定义、公共接口，生成初始 `CONTRACTS.md`
2. 也可以手动创建 `CONTRACTS.md`，只要文件存在，TWS 就会进入团队模式

团队模式会额外启用：分支管理规范、接口变更通知、跨模块影响上报等协作流程。

## 几个要点

- **不会跳步。** 每一步都有产出物和验证标准，通过了才进下一步
- **代码必须同步设计书。** 改了代码不改设计书 = 没改完
- **方案会过架构校验。** 修复 Bug 和添加功能时，方案会检查是否在堆技术债
- **主 agent 不写代码。** Claude Code 且可用子 agent 时，所有编码由子 agent 执行，主 agent 只做项目管理和验收；其他平台按 `README.md` 的平台映射执行
