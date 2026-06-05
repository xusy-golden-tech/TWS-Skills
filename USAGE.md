# TWS 使用说明

## 前置条件

### ① 配置 TWS Skills

在 Claude Code 的 settings.json 中，把 TWS-Skills 仓库的路径配为 skill 搜索目录。

### ② 安装 tws-graph（代码图引擎）

tws-graph 是独立的 Python CLI 包，提供代码符号关系图查询能力。TWS 的影响分析、设计书同步、根因分析三个环节依赖它。

```
pip install -e /path/to/tws-graph
```

> **为什么需要单独安装？** tws-graph 不是 Claude Code skill，不放在 `.claude/skills/` 里。它依赖 tree-sitter 原生 C 扩展来解析源码，通过 `pip install` 安装编译好的二进制包。TWS 技能通过 bash 命令调用它。

验证安装：
```
tws-graph --help
```

## 第一步：初始化（每个项目做一次）

在项目根目录下对 Claude Code 说：

```
/tws-init
```

TWS 会扫描项目，推断技术栈和代码规约，在项目下生成 `.tws/` 目录，并自动完成代码图初始化（索引所有源文件 + 创建基线快照），无需手动操作。

目前已支持的语言：Python、TypeScript、Kotlin、Java、Go、Rust。

> 后续代码变更时用 `tws-graph sync` 增量更新（stat 预筛选，秒级完成），无需每次全量重建。如果跳过这步，TWS 技能在需要查图时会自动补上索引。

## 第二步：日常使用

先输入 `/using-tws` 激活 TWS 入口，然后用自然语言告诉它你要做什么：

```
/using-tws 加个用户注册功能
```

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
├── sessions/                ← 运行中的流程状态（完成后自动清理）
└── codegraph/
    ├── index.db             ← 代码符号关系图（SQLite）
    └── index-*.db           ← 快照文件（before/after/initial）
```

这个目录建议加到 `.gitignore`，不需要提交到仓库。

## tws-graph 常用命令

```
tws-graph sync              ← 增量同步（日常使用）
tws-graph index             ← 全量重建索引
tws-graph impact <节点>     ← 查变更影响范围
tws-graph calls <节点>      ← 查调用关系
tws-graph trace <A> <B>     ← 查 A 到 B 的路径
tws-graph search <关键词>   ← FTS5 全文搜索符号
tws-graph lint              ← 校验 skill 文件完整性
tws-graph hooks install     ← 安装 git hooks 自动同步
tws-graph diff              ← 对比快照
tws-graph snapshot <名称>   ← 创建快照
```

## 几个要点

- **不会跳步。** 每一步都有产出物和验证标准，通过了才进下一步
- **代码必须同步设计书。** 改了代码不改设计书 = 没改完
- **方案会过架构校验。** 修复 Bug 和添加功能时，方案会检查是否在堆技术债
- **主 agent 不写代码。** 所有编码由子 agent 执行，主 agent 只做项目管理和验收
