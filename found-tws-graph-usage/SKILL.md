---
name: tws-graph-usage
description: tws-graph 代码图使用指南。所有需要查图的子 agent 必须加载此 skill。包含安装检查、命令语法、错误处理和最佳实践
---

# tws-graph 代码图使用指南

## 核心原则

tws-graph 是代码符号关系图引擎。它用 tree-sitter 预建 SQLite 索引，agent 通过 CLI 查询而非 grep。

**工具优先级**：代码调查任务中，tws-graph 是第一选择，grep 是回退手段。
- 查符号 → `tws-graph search`，不要用 Grep
- 查调用关系 → `tws-graph calls`，不要用 Grep
- 查影响范围 → `tws-graph impact`，不要用 Grep  
- 只有 tws-graph 返回空或标记 `[internal]` 时，才回退到 Grep + Read

**图和 agent 分工**：
- **图告诉 agent 客观事实**——谁调了谁、影响半径有多大、两个符号之间经过哪些路径
- **agent 做主观判断**——风险等级、是否需要通知、是否值得改

agent 不应该猜命令。加载此 skill 就是为了确保命令准确。

## 前置检查（每次查图前必做）

以下检查必须在任何 tws-graph 查询命令之前完成：

```
1. 检查可用性
   tws-graph --version
   → 如果报错 command not found 或返回非零：
     pip install -e tws-graph/

2. 确保索引最新
   tws-graph index
   → 这会做增量索引（只重新扫描有变化的文件），通常很快
   → 如果是全新项目或索引损坏，会走全量索引（较慢但只需一次）
   → 跳过此步 = 用过期数据做判断，后果比索引等待时间严重得多
```

## 命令参考

以下为 tws-graph 的**全部可用命令**。agent 只能使用此列表中的命令，禁止编造不存在的命令名。

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph index` | 全量/增量索引源文件 | `tws-graph index` |
| `tws-graph sync` | 增量同步（stat 预筛选，比 index 更快） | `tws-graph sync` |
| `tws-graph impact <node>` | 查变更影响范围（谁依赖这个符号） | `tws-graph impact MyClass.my_method --depth 2` |
| `tws-graph calls <node>` | 查调用目标（这个符号调了谁） | `tws-graph calls my_func` |
| `tws-graph calls <node> --inbound` | 查调用者（谁调了这个符号） | `tws-graph calls my_func --inbound` |
| `tws-graph trace <src> <tgt>` | 查两个符号之间的调用路径 | `tws-graph trace main parse_config` |
| `tws-graph search <query>` | FTS5 全文搜索符号 | `tws-graph search kind:class my` |
| `tws-graph snapshot <name>` | 创建命名快照 | `tws-graph snapshot before` |
| `tws-graph diff` | 对比快照（不加参数列出所有快照） | `tws-graph diff before after` |
| `tws-graph diff <a> <b> --brief` | 简要对比（仅输出 changed/unchanged） | `tws-graph diff before after --brief` |
| `tws-graph lint` | 校验 skill 文件结构 | `tws-graph lint` |
| `tws-graph hooks install` | 安装 git hooks（自动增量索引） | `tws-graph hooks install` |
| `tws-graph unresolved` | 列出未解析引用，自动标记 `[external]`/`[internal]` | `tws-graph unresolved` |

## search 命令 qualifier 参考

`tws-graph search` 支持以下 qualifier，用于精确过滤：

| qualifier | 说明 | 取值 |
|-----------|------|------|
| `kind:` | 符号类型 | `class`, `function`, `method`, `module` |
| `lang:` | 编程语言 | `python`, `typescript`, `java`, `go`, `rust`, `kotlin` |
| `path:` | 文件路径片段 | 任意字符串，如 `src/auth` |

示例：
```
tws-graph search kind:function auth
tws-graph search lang:python kind:class user
tws-graph search path:utils kind:method parse
```

## 常用查询模式

### 影响分析前

```
tws-graph index
tws-graph impact <被改符号> --depth 2
tws-graph calls <被改符号> --inbound
tws-graph snapshot before
```

### 设计同步时

```
tws-graph index
# ... 代码修改完成后 ...
tws-graph diff before after
```

### 根因分析时

```
tws-graph index
tws-graph trace <入口函数> <报错函数>
tws-graph calls <报错函数> --inbound --depth 3
```

### 查找符号

```
tws-graph search <关键词>
# 如果结果太多，加 qualifier 缩小范围：
tws-graph search kind:function <关键词>
```

## unresolved 引用分类与行动策略

`tws-graph unresolved` 会自动将未解析引用分为两类：

| 标签 | 含义 | 行动策略 |
|------|------|---------|
| `[external]` | 外部 SDK/库（如 `os`, `re`, `typer`, `fastapi`），不在项目源码中 | **停止追踪**，如需了解此依赖的作用，上网搜索文档 |
| `[internal]` | 项目内符号，但因索引缺失/动态调用等原因未能解析 | **回退 grep**，用 Grep 工具在项目中搜索该符号名，手动追踪 |

```
典型使用流程：
tws-graph index
tws-graph unresolved
# → 看到 [external] → 忽略，这些是正常的
# → 看到 [internal] → grep 搜索该符号，补全缺失的调用链
```

## 错误处理

### 查询返回空结果

```
原因：可能是动态调度、闭包、回调函数，tree-sitter 无法静态分析
处理：标注 provenance=heuristic，回退到 grep + read 手动追踪
不得：认定「没有调用关系」——tree-sitter 看不到不代表不存在
```

### 符号未找到

```
原因：可能是局部变量、lambda、或符号名拼写不对
处理：
  1. 先用 tws-graph search <关键词> 确认符号名
  2. 如果 search 也找不到 → 标注「图中无此符号，手动追踪」
  3. 不要轻易改符号名去匹配——可能确实不在索引范围内
```

### tws-graph 安装失败

```
处理：标注降级，后续用 grep/read 手动追踪
不得静默跳过——必须明确告知「tws-graph 不可用，已降级为手动追踪」
```

## 禁止的行为

1. **编造命令名**。以下命令不存在，永远不要使用：
   - `tws-graph callers` — 正确命令是 `tws-graph calls --inbound`
   - `tws-graph dependents` — 正确命令是 `tws-graph impact`
   - `tws-graph path` — 正确命令是 `tws-graph trace`
   - `tws-graph callees` — 正确命令是 `tws-graph calls`
   - `tws-graph references` — 不存在，用 `tws-graph search` 或 `tws-graph impact`

2. **跳过 index 直接用过期数据查**。图不会自动更新，代码改了图还是旧的。

3. **改前不拍快照**。design-sync 需要 before/after 对比，没有 before 快照就等于白做。

4. **不检查可用性就假设已安装**。每次加载此 skill 时都必须先跑 `tws-graph --version`。

## Rationalization Prevention

| 「用 grep 也一样」 | grep 找不到跨文件间接调用，也看不到多跳路径。图给你完整的依赖闭包 |
| 「我记住命令了不用加载」 | 加载此 skill 正是为了防止命令拼错。`callers` 不是命令，`--inbound` 才是 |
| 「先查再说，不跑 index 了」 | index 可能过期，旧数据会误导。索引一分钟，排错一小时 |
| 「返回空就是没调用关系」 | 动态调度、回调、闭包不会出现在静态分析中。标注 heuristic，回退 grep |
| 「这个参数应该存在」 | 不猜。此 skill 中的命令参考表是唯一权威，表上没有的就是不存在 |
