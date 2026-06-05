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
① 检测 tws-graph 是否可用 → ② 安装（如需要）→ ③ 构建索引 → ④ 创建基线快照
```

## ① 检测 tws-graph 是否可用

```
Bash: tws-graph --version 2>&1

→ 输出 "tws-graph 0.1.0" 或类似版本号 → 已安装，跳到步骤 ③
→ 输出 "command not found" 或报错 → 未安装，进入步骤 ②
```

## ② 安装

tws-graph 是随 TWS-Skills 仓库分发的 Python 包，位于项目根目录的 `tws-graph/` 下。

```
Bash: pip install -e tws-graph/ 2>&1

→ 成功 → 验证: tws-graph --version → 继续步骤 ③
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
→ 如果部分文件解析失败 → 继续，但标注「部分文件索引失败，涉及这些文件的功能可能无法查询」
→ 如果全部失败 → 标注「索引构建失败」，后续技能退回 grep
```

## ④ 创建基线快照

```
Bash: tws-graph snapshot initial 2>&1

→ 保存为 .tws/codegraph/index-initial.db
→ 后续 design-sync 可以 diff 到这个基线
```

## 完成标准

- [ ] tws-graph --version 正常输出
- [ ] tws-graph index 成功运行
- [ ] .tws/codegraph/index.db 文件存在且 > 0
- [ ] tws-graph snapshot initial 已创建基线

## 在 TWS 流程中的位置

- `flow-new-project` 的 `①.5 初始化代码图` 会调用本技能
- `tws-init` 完成规约生成后，调用本技能初始化代码图
- 如果项目已有 `.tws/codegraph/index.db` → 跳过安装和索引，只更新快照

## 错误处理

| 问题 | 处理 |
|------|------|
| tws-graph --version 失败 | 安装 tws-graph |
| pip install 失败 | 提示用户，后续退回 grep |
| tws-graph index 部分失败 | 继续，标注不完整 |
| tws-graph index 全部失败 | 标注失败，退回 grep |
| tws-graph snapshot 失败 | 不阻塞，下次 design-sync 用最新 DB 比 |

## 后续使用

代码图初始化完成后，日常查询请参考 `found-tws-graph-usage` skill。
任何需要查图的子 agent 应通过 Skill 工具加载它，获取准确的命令语法。

## 常规维护

项目代码有大量变更后，重新索引。具体命令语法以 `found-tws-graph-usage` 为准：

```
tws-graph index    ← 增量索引（只处理修改过的文件）
```

如果要强制全量重建：

```
rm .tws/codegraph/index.db
tws-graph index
tws-graph snapshot initial
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「tw-graph 太重了，grep 够用」 | grep 找不到跨文件间接调用链 |
| 「先装再初始化吧」 | 安装后立即 index，不然下次又忘了 |
| 「这个项目很小，不用代码图」 | 项目会长大。现在建好基线，后面受益 |
| 「装不上就算了」 | 装不上时至少确认是缺少 Python 还是缺少 tws-graph/ 目录 |
