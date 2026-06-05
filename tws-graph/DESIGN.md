# tws-graph 架构设计文档

## 1. 概述

tws-graph 是代码符号关系图引擎。用 tree-sitter 解析 AST → 提取符号和关系边 → 存入 SQLite → agent 通过 CLI 查询图而非 grep。

**定位：** 给 TWS agent 提供客观的代码结构事实。图告诉 agent 谁调了谁、影响半径多大，agent 做主观判断。

**核心原则：**
- 预建索引，查图而非搜索
- 单文件解析错误不影响全量索引
- CLI 作为唯一接口，零网络依赖
- 增量索引（stat 预筛选 + content hash 门控）

### 与 CodeGraph 的关键差异

| | CodeGraph | tws-graph |
|---|---------|-----------|
| 接口 | MCP 协议（外部服务） | CLI 子命令 |
| 语言 | 20+ | Python / TS / Java / Kotlin / Go / Rust / Skill(md) |
| 独有能力 | — | 快照 diff、`unresolved` 分类、skill 文件 lint |
| 索引粒度 | 方法级 | 方法级（不做字段级，字段引用走 Grep） |

## 2. 架构总览

```
CLI (typer)
  ├─ index    扫描→解析→存储→边解析→导入分析→框架检测
  ├─ calls    查询调用关系（BFS）
  ├─ impact   影响半径（BFS 入边）
  ├─ trace    两点间最短路径（BFS）
  ├─ search   FTS5 全文搜索（支持 kind:/lang:/path: qualifier）
  ├─ snapshot 复制整个 .db 文件作为快照
  ├─ diff     对比两个快照 → 符号/边差异报告
  ├─ sync     index 的别名（语义更准确）
  ├─ unresolved  列出未解析引用，按 is_external 分类
  ├─ lint     校验 TWS skill 文件结构
  └─ hooks    安装/卸载 git hooks 自动同步

索引管线:
  scanner → language_detect → parser → extractor → orchestrator
                                         ↑
                                    registry（自动发现提取器）

查询引擎:
  QueryBuilder → GraphTraverser → 格式化输出
       ↓
  edge_resolver（后处理跨文件调用边）
  framework/（FastAPI 路由检测）

存储:
  SQLite（WAL 模式，外键，64MB 缓存）
  ├─ nodes / edges / files / unresolved_refs
  ├─ nodes_fts（FTS5 全文索引）
  └─ schema_versions（迁移追踪）
```

## 3. 数据库 Schema

### 3.1 nodes — 符号表

每个代码符号（类、方法、函数、接口、路由等）一行。主键是 `hash(file_path + qualified_name)` 的前 32 位，不使用自增 ID——同一符号在不同语言/项目中可产生相同 hash，同项目内确定性保证唯一。

```sql
id              TEXT PRIMARY KEY  -- SHA256(file_path:qualified_name)[:32]
kind            TEXT NOT NULL     -- class/function/method/interface/route/variable
name            TEXT NOT NULL     -- 简单名，如 "calculateTotal"
qualified_name  TEXT NOT NULL     -- "file.py::ClassName.methodName"
file_path       TEXT NOT NULL     -- 相对于项目根目录
language        TEXT NOT NULL
start_line / end_line  INTEGER
signature       TEXT              -- 函数签名
docstring       TEXT              -- 前 200 字符
visibility      TEXT              -- public/private/protected
is_abstract     INTEGER DEFAULT 0
is_exported     INTEGER DEFAULT 0
decorators      TEXT              -- JSON 数组
framework       TEXT              -- fastapi 等
updated_at      INTEGER           -- epoch ms
```

### 3.2 edges — 关系表

```sql
id          INTEGER PRIMARY KEY AUTOINCREMENT
source      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE
target      TEXT NOT NULL    -- 可能指向尚未索引的跨文件符号
target_text TEXT             -- 未 hash 的限定名，供后处理边解析使用
kind        TEXT NOT NULL    -- calls/imports/extends/implements/references/contains
source_loc  TEXT             -- "file:line"
provenance  TEXT DEFAULT 'tree-sitter'
            -- tree-sitter: 直接提取
            -- resolved:    边解析后确认
            -- unresolved:  未找到目标
            -- ambiguous:   多个候选
```

### 3.3 files — 文件追踪

```sql
path         TEXT PRIMARY KEY
content_hash TEXT NOT NULL    -- SHA256，用于变更检测
language     TEXT NOT NULL
node_count   INTEGER DEFAULT 0
indexed_at   INTEGER NOT NULL
size         INTEGER          -- 字节数（stat 预筛选）
modified_at  INTEGER          -- mtime 秒（stat 预筛选）
```

### 3.4 unresolved_refs — 未解析引用

```sql
id              INTEGER PRIMARY KEY AUTOINCREMENT
from_node_id    TEXT NOT NULL REFERENCES nodes(id)
reference_name  TEXT NOT NULL
reference_kind  TEXT -- call/import/reference
line / col      INTEGER
candidates      TEXT -- JSON 候选数组
file_path       TEXT
language        TEXT
is_external     INTEGER DEFAULT 0  -- 0=项目内，1=外部 SDK/库
```

**分类逻辑：**
- 调用边：边解析阶段标记为 `unresolved` 的自动录入，`is_external` 默认为 0
- 导入边：后处理比对项目文件列表。模块名映射到项目文件 → `is_external=0`（索引缺失），映射不到 → `is_external=1`（外部库）

### 3.5 FTS5 全文索引

```sql
CREATE VIRTUAL TABLE nodes_fts USING fts5(
    id, name, qualified_name, docstring, signature,
    content='nodes', content_rowid='rowid'
);
```

通过触发器保持与 nodes 表同步。搜索使用 BM25 排序。注意：FTS5 双引号是区分大小写的短语查询，单词语用裸词条做大小写不敏感匹配。

### 3.6 schema_versions — 迁移追踪

```sql
version     INTEGER PRIMARY KEY
applied_at  INTEGER NOT NULL
description TEXT
```

迁移在 `DatabaseConnection._run_migrations()` 中执行，`initialize()` 每次都会调用。使用 `ALTER TABLE ADD COLUMN` 做增量迁移。

## 4. 索引引擎

### 4.1 管线

```
scan_directory(root)
  ↓
  git ls-files（优先，遵循 .gitignore）
  ↓ fallback: os.walk（跳过 SKIP_DIRS）
  ↓
返回排序过的相对路径列表

for each file:
  1. stat → 比对 DB 中的 (size, mtime) → 匹配则跳过
  2. 读文件 → SHA256 → 比对 content_hash → 匹配则跳过
  3. detect_language(ext) → 查 registry 获取语言名
  4. extract_from_source(path, content, lang)
     → registry.get_extractor(lang)
     → tree-sitter parse → BaseExtractor.extract()
     → 返回 {nodes, edges, errors}
  5. BEGIN TRANSACTION
     → delete_file(old) + insert_nodes + insert_edges + upsert_file
     → COMMIT（单文件失败 ROLLBACK，不影响其他文件）
```

### 4.2 增量索引机制

两层门控，从快到慢：

- **Tier 1 — stat 预筛选：** `(size, mtime)` 比对，不变即跳过，不读文件
- **Tier 2 — content hash：** SHA256 比对比对，防止 mtime 漂移

`force=True` 跳过两层门控。

### 4.3 提取器注册机制

`registry.py` 通过 `pkgutil.iter_modules` 自动扫描 `extractors/` 包。新增语言只需：
1. 新建 `extractors/<lang>.py`
2. 继承 `BaseExtractor`，设置 `extensions` 和 `tree_sitter_languages`
3. 实现 `extract(source, tree, ctx)` 方法

无需修改 scanner、parser、language_detect 或 CLI。

### 4.4 ExtractionContext（共享设施）

`BaseExtractor.extract()` 接收 `ExtractionContext`，提供：
- `add_node(kind, name, node, **extra)` → 生成 hash ID，添加到结果
- `add_edge(source, target, kind, line, target_text)` → 添加关系边
- `result` → `ExtractionResult` 累积器

已有提取器实现方式分两种：
- 老式：`visit_xxx()` 函数直接操作 `ExtractionResult`（python_extractor、java_extractor 等）
- 新式：`BaseExtractor` 子类使用 `ExtractionContext`（extractors/ 下的新提取器）

### 4.5 支持的语言

| 语言 | 文件扩展名 | 提取器位置 |
|------|-----------|-----------|
| Python | .py | `python_extractor.py` |
| TypeScript/TSX | .ts .tsx | `ts_extractor.py` |
| Java | .java | `java_extractor.py` |
| Kotlin | .kt .kts | `kotlin_extractor.py` |
| Go | .go | `go_extractor.py` |
| Rust | .rs | `rust_extractor.py` |
| TWS Skill | .md（YAML frontmatter） | `skill_parser.py` |

### 4.6 不索引的内容（设计决策）

**字段/属性访问：** 不做索引。属性访问密度远高于方法调用（一个方法可访问十几个字段），全量索引会导致边数膨胀一个数量级且信噪比极差。大部分属性访问是文件内的，跨文件访问用 Grep 更高效。

## 5. 查询引擎

### 5.1 GraphTraverser

BFS 遍历，抗 N+1 查询：每层用 `get_nodes_by_ids` 批量拉取。

```
get_calls(node_id, direction, depth)
  → BFS，每步查 incoming/outgoing edges
  → 批量 get_nodes_by_ids 取邻接节点
  → 返回 {nodes: {id: dict}, edges: [dict], roots: [id]}

get_impact_radius(node_id, depth)
  → BFS 沿入边方向（谁调了它），排除 contains 边
  → 边优先级排序：contains > calls > references > imports
  → 结果按模块分组

find_path(from_id, to_id)
  → BFS 最短路径
  → 包含 contains 边（允许 class → method 路径）
  → 返回 [{node, via_edge}, ...]
```

### 5.2 边解析（edge_resolver）

索引完成后，处理单文件 AST 无法解析的跨文件调用：

1. 找出所有 call 类型且 target 不在 nodes 表中的边
2. 从所有可调用节点构建后缀索引（最多 3 级）
3. 对每条悬挂边，用 target_text 后缀匹配：3 级 → 2 级 → 1 级
4. 1 级匹配优先同文件消歧义，支持大小写不敏感回退
5. 结果标记：`resolved` / `unresolved` / `ambiguous`

### 5.3 搜索

三层搜索策略：

```
search_nodes(query):
  Tier 1: FTS5 BM25（列权重 name=20, qname=10, sig=3, doc=1）
  Tier 2: LIKE 回退（子串匹配，FTS5 分词边界外的情况）
  Tier 3: 编辑距离 ≤ 2（仅 ≥ 3 字符查询）

search_nodes_field_qualified(query):
  解析 qualifier（kind:/lang:/path:/visibility:/framework:）
  → FTS5 + WHERE 过滤
  → 失败回退 LIKE + WHERE 过滤
```

### 5.4 框架检测

插件式：`framework/` 下的 `BaseFrameworkResolver` 子类。目前支持 FastAPI（检测 `@app.get/post` 等路由装饰器）。检测逻辑在索引管线末尾运行，结果直接写入 nodes/edges。

## 6. CLI 命令清单

| 命令 | 功能 | 关键参数 |
|------|------|---------|
| `index` | 全量/增量索引 | `--force` 跳过门控 |
| `sync` | index 别名（语义更准确） | 同 index |
| `calls <符号>` | 调用关系 | `--inbound` / `--outbound` / `--depth` |
| `impact <符号>` | 影响范围 | `--depth` |
| `trace <起点> <终点>` | 调用路径 | — |
| `search <查询>` | 全文搜索 | `--limit`、qualifier（kind:/lang:/path: 等） |
| `snapshot <名称>` | 拍快照 | 复制 .db 文件 |
| `diff [before] [after]` | 快照对比 | `--brief`；无参数列出所有快照 |
| `unresolved` | 未解析引用 | 自动按 is_external 打 [external]/[internal] 标签 |
| `lint` | 校验 skill 文件 | 5 条规则 |
| `hooks <install\|remove\|status>` | Git hooks 管理 | — |

### search 命令多参数设计

`search` 接受 `list[str]` 作为查询参数，自动用空格拼接。因此以下两种写法等价：

```
tws-graph search kind:class AppContainer
tws-graph search "kind:class AppContainer"
```

不在 shell 层面要求引号，减少 agent 使用时的语法负担。

## 7. Git Hooks 自动同步

安装 `tws-graph hooks install` 后，三个 git hook 会触发后台 `tws-graph sync`：
- `post-commit`
- `post-merge`
- `post-checkout`

Hook 脚本用标记行（`# >>> tws-graph auto-sync >>>` / `# <<< ... <<<`）包裹，与其他 hook 内容共存。卸载时只删除 TWS 管理的块。

**设计意图：** 索引在 commit 后自动更新，agent 不需要每次查图前手动跑 `tws-graph index`。只有在 hooks 未安装、刚改完代码还没 commit、或怀疑索引损坏时才需要手动 index。

## 8. 快照与 Diff

快照通过文件复制实现——复制整个 `.db` 文件到 `.tws/codegraph/index-<name>.db`。不用数据库内表存储快照的理由：
- 更简单（无额外 schema）
- 快照独立可查（任何 SQLite 工具都能打开）
- TWS 场景只需 2 个快照（before/after），无需多版本历史

Diff 直接对比两个快照数据库的 nodes 和 edges 表：
- `added_symbols` / `removed_symbols`：ID 级别的增删
- `signature_changed`：同 ID 但 signature 字段不同
- `added_edges` / `removed_edges`：边级别的增删
- `affected_files`：受影响的文件集

## 9. Skill Linter

`tws-graph lint` 对 TWS 项目根目录的 `.claude/skills/` 下的 SKILL.md 文件执行 5 条校验：

1. **frontmatter**：必须有 YAML frontmatter，含 `name` 和 `description`
2. **SUBAGENT-STOP**：entry/flow 层必须有，comp/found 层不能有
3. **cross-refs**：引用的其他 skill 名称必须存在
4. **prefix**：skill 目录名必须匹配其层级前缀（`using-`、`flow-`、`comp-`、`found-`）
5. **unreferenced**：检测未被任何其他 skill 引用的孤立 skill

skill 文件被视为一种特殊的"源文件"——它们有自己的解析器（`skill_parser.py`），提取 YAML 属性 + internal-refs 边 + Markdown 小节节点，存入图中。

## 10. 导入解析与 is_external 分类

### 10.1 Python 导入处理

- `import X`：创建 import 边，`target_text = "X"`（模块名）
- `from X import Y`：创建 import 边，`target_text = "X.Y"`（模块限定名）。解析 dotted_name、aliased_import、wildcard_import 三种形式，跳过 module_name 子节点防止重复

### 10.2 is_external 判定

在 `orchestrator._populate_import_unresolved()` 中：
1. 查出所有 `kind='imports'` 且 target 不在 nodes 中的边
2. 对每条边，逐级缩短模块名前缀（`X.Y.Z` → `X.Y` → `X`），检查是否映射到项目文件
3. 映射规则：`module → module.py`、`module/__init__.py`、相对导入路径解析（`.` = 当前包，`..` = 父包）
4. 找到映射 → `is_external=0`（索引缺失），找不到 → `is_external=1`（外部库）

CLI 展示时直接读 `is_external` 列，打出 `[external]` 或 `[internal]` 标签。agent 看到 `[external]` 停止追踪（上网搜文档），看到 `[internal]` 回退 Grep。

## 11. 数据库连接管理

- WAL 模式 + NORMAL synchronous（写性能优先）
- 外键 ON DELETE CASCADE
- 64MB 页面缓存
- busy_timeout 5 秒
- `initialize()` 检测新库或需要迁移 → 执行 schema.sql + `_run_migrations()`
- `optimize()` 在批量写入后执行 `PRAGMA optimize` + WAL checkpoint (PASSIVE)

## 12. 技术决策记录

| 决策 | 理由 |
|------|------|
| Python + tree-sitter | typer CLI 生态成熟，tree-sitter Python binding 稳定 |
| SQLite 而非 PostgreSQL | 零配置、单文件、够快。10 万符号约几 MB |
| node ID 用 hash 而非自增 | 确定性——同一符号在不同索引间 ID 不变，diff 才能工作 |
| 快照用文件复制而非 DB 表 | 简单、独立可查、TWS 只需 2 个快照 |
| CLI 而非 MCP | TWS 不需要外部服务，子进程调用即可 |
| 字段/属性不索引 | 密度太高信噪比差，文件内访问为主，Grep 更高效 |
| git hooks 而非文件监听 | 跨平台更可靠，跟随 git 工作流 |
| 不做 `watch`/`map` 命令 | 方案书中的原设计被 eval 后砍掉：hooks 替代了 watch，project-map 的手工维护已经够用 |
| `calls --inbound` 替代 `callers` | 统一命令更易记，`--inbound` 语义明确 |
| search 接受 list 参数 | 避免 shell 空格分割的引号问题 |
