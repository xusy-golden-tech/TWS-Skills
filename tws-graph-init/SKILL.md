---
name: tws-graph-init
description: 代码图初始化——安装 tws-graph CLI、构建项目符号关系图、创建基线快照
---

# TWS 代码图初始化

## 核心原则

**没有代码图，agent 就只能 grep + read 手工追代码——慢，而且容易漏。**

tws-graph-init 把代码图工具链的安装和初始化封装成一个标准流程，在任何 TWS 项目里运行一次即可。

## 流程

**执行前必须：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），获取准确的命令语法和错误处理策略。以下各步骤的命令仅为流程描述，实际执行以 found-tws-graph-usage 为准。

```
① 检测 tws-graph 版本 → ② 安装/升级（如需要）→ ③ 构建索引 → ④ 创建基线快照 → ⑤ 安装 git hooks
```

## ① 检测 tws-graph 版本

```
# 1. 读源码期望版本
Bash: grep 'version\s*=' tws-graph/pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'
# 或 Read tws-graph/pyproject.toml 读 project.version

# 2. 查已安装版本
Bash: tws-graph --version 2>&1

→ 未安装（command not found）→ 进入步骤 ②「安装」
→ 已安装但版本号 < 源码版本 → 进入步骤 ②「升级」
  （editable install 代码变更自动生效，但新依赖/入口点需要重装才能生效）
→ 已安装且版本一致 → 跳到步骤 ③
```

> **版本号判断**：运行 `tws-graph --version` 获取已安装版本（如 `0.1.0`），与 `tws-graph/pyproject.toml` 中的 `version` 比较。小于则升级。

## ② 安装/升级

tws-graph 是随 TWS-Skills 仓库分发的 Python 包，位于项目根目录的 `tws-graph/` 下。

**安装（首次）：**
```
Bash: pip install -e tws-graph/ 2>&1
```

**升级（版本不匹配）：**
```
Bash: pip install -e tws-graph/ --upgrade 2>&1
```

```
→ 成功 → 验证: tws-graph --version → 继续步骤 ③
  升级后建议全量重建索引以获取新增符号类型
→ 失败（找不到 tws-graph/ 目录或 pip 报错）→ 输出以下提示并结束：
  「tws-graph 安装失败。请检查：
    1. tws-graph/ 目录是否存在
    2. Python >= 3.10 是否可用
    3. tree-sitter-language-pack 依赖是否满足
   在问题解决之前，影响分析/设计书同步/根因分析将降级为手工 grep。」
```

## ③ 构建索引

```
Bash: tws-graph index 2>&1

→ 输出索引统计（N 个文件, N 个符号, N 条关系）→ 继续步骤 ④
  tws-graph 通过 28+ 个 tree-sitter 提取器覆盖 30+ 种语言和文件格式，
	  含编程语言、标记样式、配置格式、容器/数据库、技能文档等。
	  详细清单见 found-tws-graph-usage。所有提取器均产出节点（可搜索符号）+ 边（关系）。
→ 如果部分文件解析失败 → 继续，但标注「部分文件索引失败，涉及这些文件的功能可能无法查询」
→ 如果全部失败 → 标注「索引构建失败」，后续技能退回 grep
```

## ④ 创建基线快照

```
Bash: tws-graph snapshot initial 2>&1

→ 保存为 .tws/codegraph/index-initial.db
→ 后续 design-sync 可以 diff 到这个基线
```

## ⑤ 安装 git hooks

```
Bash: tws-graph hooks install 2>&1

→ 安装 post-commit / post-merge / post-checkout hooks
→ 之后每次 commit/merge/checkout 自动增量同步索引
→ 失败不阻塞，提示手动安装
```

## 完成标准

- [ ] tws-graph --version 正常输出版本号
- [ ] 版本号与 `tws-graph/pyproject.toml` 中一致
- [ ] tws-graph index 成功运行
- [ ] .tws/codegraph/index.db 文件存在且 > 0
- [ ] tws-graph snapshot initial 已创建基线
- [ ] tws-graph hooks install 已安装

## 在 TWS 流程中的位置

- `flow-new-project` 的 `①.5 初始化代码图` 会调用本技能
- `tws-init` 完成规约生成后，调用本技能初始化代码图
- 如果已安装且版本匹配 → 跳过安装步骤，直接进入索引

## 错误处理

| 问题 | 处理 |
|------|------|
| tws-graph --version 失败 | 安装 tws-graph |
| 已安装版本 < 源码版本 | 升级 tws-graph（pip install -e --upgrade） |
| pip install 失败 | 提示用户，后续退回 grep |
| tws-graph index 部分失败 | 继续，标注不完整 |
| tws-graph index 全部失败 | 标注失败，退回 grep |
| tws-graph snapshot 失败 | 不阻塞，下次 design-sync 用最新 DB 比 |

## 后续使用

代码图初始化完成后，日常查询需通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")）。
任何需要查图的子 agent 必须通过 Skill 工具加载它，获取准确的命令语法。

## 常规维护

项目代码有大量变更后，重新索引。具体命令语法以 `found-tws-graph-usage` 为准：

```
tws-graph index    ← 增量索引（只处理修改过的文件）
```

如果要强制全量重建（如索引版本升级、提取器扩展后想获得新增符号类型）：

```
rm .tws/codegraph/index.db
tws-graph index
tws-graph snapshot initial
```

> **索引版本升级**：tws-graph 提取器持续扩展。如果项目索引是较早版本构建的，增量 index 不会重建已有文件。全量重建后可获得新增的符号类型（如结构式语言的节点、新编程语言的符号）。不影响已有查询，只是让 `search` 覆盖面更广。

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「tw-graph 太重了，grep 够用」 | grep 找不到跨文件间接调用链 |
| 「先装再初始化吧」 | 安装后立即 index，不然下次又忘了 |
| 「这个项目很小，不用代码图」 | 项目会长大。现在建好基线，后面受益 |
| 「装不上就算了」 | 装不上时至少确认是缺少 Python 还是缺少 tws-graph/ 目录 |
