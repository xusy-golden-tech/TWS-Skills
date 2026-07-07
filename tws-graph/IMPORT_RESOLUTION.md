# 跨文件引用解析 — 改进方向

## 背景

本次（v7.2.1）修了三个 bug：FTS5 管道符 OR、glob 子路径匹配、bash glob 展开污染 query。测试过程中发现了两个能力缺口：

## 发现的缺口

### 1. 跨文件 import 引用未解析成边

**现象**：
```
tws-graph impact META_TOOL_GUIDANCE --depth 3   →  "No impact found"
tws-graph search META_TOOL_GUIDANCE              →  找到了这个常量
```

**根因**：`META_TOOL_GUIDANCE` 是 `tool_schema.py` 中定义的常量，`context_assembler.py` 等文件中通过 `from tool_schema import META_TOOL_GUIDANCE` 引用。但 Python extractor 只提取了模块级别的 IMPORTS 边（target 是模块名 hash，永远解析不到真实节点），没有为每个被 import 的符号创建可解析的引用边。

更一般地：当前索引器逐文件独立提取，不 import 的文件完全不知道对方的存在。跨文件的调用、继承、类型引用、import 等关系全部断链。

**影响范围**：
- `impact`：叶子节点（无出边的函数/常量/类）返回空，即使被多个文件 import 使用
- `calls`：只能看同文件内的调用，跨文件调用链断裂
- `trace`：起点和终点在不同文件时基本找不到路径
- `unresolved_refs`：表是空的（Rust 侧没填充，由 Python 侧处理但似乎也没做）

### 2. `--brief` 参数未透传

**现象**：`tws-graph diff --brief` 参数定义了但 CLI 未传给 Rust，brief 模式不生效。Rust `snapshot_diff` 已支持 `brief: Option<bool>`，纯 Python 侧漏传。

已修复（v7.2.1 中）。

## 改进方向

### 方向 A：跨文件引用解析（核心能力）

**目标**：索引完成后，扫描所有 target 不存在的 IMPORTS / REFERENCES 边，解析 `target_text` 找到真实节点，补上正确的 REFERENCES 边。

**关键点**：

1. **Extractor 增强** — `from X import Y` 语句，除了现有的模块级 IMPORTS 边，还要为每个被导入的符号（Y）单独建一条 REFERENCES 边，`target_text` 存 `模块.符号` 的完整路径，供后续解析使用。

2. **模块名 → 文件路径 映射** — 扫描 nodes 表中所有 file_path，建立反向索引：
   - `tool_schema` → `g_assistant_backend/core/tool_schema.py`
   - `a.b.c` → `g_assistant_backend/a/b/c.py` 或 `g_assistant_backend/a/b/c/__init__.py`
   - 支持 `.py`、`.pyi`、`__init__.py` 等 Python 包结构

3. **Post-index 解析** — 新增解析模块（如 `indexer/resolver.rs`）：
   - 查 edges 表中 target 不存在的 IMPORTS/REFERENCES 边
   - 解析 `target_text`（格式：`模块.符号`）
   - 在模块映射中找到目标文件，在该文件中按 name 匹配节点
   - 找到则用正确的 hash 建 REFERENCES 边
   - 找不到的写入 `unresolved_refs` 表

4. **CLI 集成**：
   - 新命令 `tws-graph resolve` — 手动触发解析
   - `index` / `sync` 末尾自动调一次 resolve
   - 输出统计：resolved / unresolved / already_valid

5. **扩展性**：先做 Python，架构保留其他语言的扩展点（不同语言的模块名→文件路径解析规则不同）。

**预期效果**：
```
tws-graph index
tws-graph resolve           # 输出: 已解析 1234 条, 未解析 56 条
tws-graph impact META_TOOL_GUIDANCE --depth 3   # 有结果了
```

### 方向 B：`unresolved_refs` 表填充

将 Rust 侧的解析结果同步到 `unresolved_refs`（目前 Rust 完全不写这张表），让 `tws-graph unresolved` 命令和 MCP 的 `get_unresolved_references` 工具有数据可用。

- 解析成功的：不写入 unresolved_refs（已经是有效的边了）
- 解析失败的（模块不存在 / 符号找不到）：写入 unresolved_refs，标记 is_external
- 外部模块（如 `os`、`json`）：标记 is_external=1

### 方向 C：通用跨文件引用解析（后续）

当前方案针对 Python import 语句。后续可扩展：
- 其他语言的 import/require/include 解析（TypeScript、Rust、Go 等各有模块系统）
- 跨文件调用解析（`obj.method()` 在另一个文件定义）
- 跨文件类型引用（type annotation 指向其他文件的类）

## 本次（v7.2.1）已完成修复

| 问题 | 修复 |
|------|------|
| `\|` 管道符 FTS5 报错 | `fts5_escape_query` 替换 `\|` → ` OR ` |
| `AND`/`OR`/`NOT` 被加 `*` 后缀 | 关键字跳过 prefix |
| `--include "plugins/tool-*/**"` 匹配不上 | `compile_glob` 增加 `**/` 子路径变体 |
| bash glob 展开污染 query | CLI 层过滤路径 term |
| `--brief` 不生效 | 透传 brief 到 Rust |
| impact 叶子节点返回空 | 设计如此，需方向 A 解决 |

## 待决策

- 方向 A（跨文件解析）是否作为 v7.3.0 目标启动？
- 是否需要同时处理方向 B（unresolved_refs 填充）？
- 是否需要先做详细技术调查再定方案？
