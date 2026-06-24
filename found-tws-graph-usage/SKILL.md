---
name: tws-graph-usage
description: tws-graph 代码图使用指南。所有需要查图的子 agent 必须加载此 skill。包含安装检查、命令语法、错误处理和最佳实践
---

# tws-graph 代码图使用指南

## 核心原则

tws-graph 是代码符号关系图引擎。它用 tree-sitter 预建 SQLite 索引，agent 通过 CLI 查询而非 grep。

**工具优先级**：代码调查任务中，tws-graph 是第一选择，Grep 是回退手段。

强制规则：
- 查符号（类名、方法名、函数名）→ 必须先走 `tws-graph search`，不允许跳过直接 Grep
- 查调用关系 → `tws-graph calls`，不允许直接 Grep
- 查影响范围 → `tws-graph impact`，不允许直接 Grep
- 只有以下情况才允许使用 Grep：
  1. tws-graph 返回空结果或标记 `[internal]` 未解析
  2. 搜索目标在 tws-graph 不索引的文件类型中（XML、.gradle、图片、二进制文件等）
  3. 文件模式匹配（如查找 test 文件）
  4. 搜索字面字符串/正则，而非已知符号名

违规示例：
```
❌ Grep "AppContainer"              → 这是类名，应该用 tws-graph search kind:class AppContainer
❌ Grep "readerSettingsStore"       → 这是符号名，应该用 tws-graph search
❌ Grep "isPremium|setPremium"      → 这是方法名，应该用 tws-graph search kind:method
```

合规示例：
```
✅ tws-graph search kind:class AppContainer
✅ tws-graph search readerSettingsStore
✅ tws-graph search lang:sql kind:sql_table users
✅ tws-graph search lang:dockerfile kind:dockerfile_stage builder
✅ tws-graph search lang:hcl kind:hcl_resource aws_instance
✅ Grep "adContainer|FrameLayout" fragment_home.xml   （XML 文件，tws-graph 不索引）
✅ Grep "**/test/**/*Test.kt"                          （文件模式匹配）
```

**图和 agent 分工**：
- **图告诉 agent 客观事实**——谁调了谁、影响半径有多大、两个符号之间经过哪些路径
- **agent 做主观判断**——风险等级、是否需要通知、是否值得改

agent 不应该猜命令。加载此 skill 就是为了确保命令准确。

## 索引覆盖范围

tws-graph 通过 15 个 tree-sitter 提取器索引项目源文件，**所有提取器均产出节点（可搜索的符号）+ 边（关系）**：

| 类别 | 语言 | 文件扩展名 | 关系类型 |
|------|------|-----------|----------|
| 编程语言 | Python, TypeScript, JavaScript, Java, Go, Rust, Kotlin, PHP, Ruby, C, C++, C#, Scala, Elixir, Haskell, Clojure | 对应扩展名 | contains, calls, imports |
| 标记/样式 | HTML, CSS, Markdown | .html, .css, .md | contains, imports, calls |
| 配置 | YAML, TOML, JSON, HCL, Kustomize | .yaml/.yml, .toml, .json, .hcl/.tf, .kustomize | contains, imports |
| 容器/数据库 | Dockerfile, SQL | Dockerfile, .sql | contains, imports, calls, env_accesses |

**这意味着 `tws-graph search` 可以搜索任何被索引文件中的符号**——不只是函数和类，还包括 SQL 表、Docker 构建阶段、HCL 资源、YAML 键、Kubernetes 资源等。

## 前置检查（每次查图前必做）

```
1. 检查可用性
   tws-graph --version
   → 如果报错 command not found 或返回非零：
     pip install -e tws-graph/

2. 索引由 git hooks 自动维护
   tws-graph hooks install 安装后，每次 commit/merge/checkout 自动增量同步。
   通常不需要手动跑 tws-graph index。
   只有在以下情况才需要手动 index：
   - hooks 未安装（tws-graph hooks status 检查）
   - 子 agent 刚修改了代码但还没 commit
   - 怀疑索引损坏（查询结果明显不对）
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
| `tws-graph serve` | 启动 MCP 服务器（15 工具 + 3 资源） | `tws-graph serve --root . --db .tws/codegraph/index.db` |
| `tws-graph analyze` | 图分析（算法：clone/community/centrality/cycle/all） | `tws-graph analyze --algorithm clone` |
| `tws-graph analyze --run <name>` | 运行 P9 分析器 | `tws-graph analyze --run dead-code` |
| `tws-graph watch` | 文件变更监听，自动增量同步索引 | `tws-graph watch --path . --interval 2.0` |
| `tws-graph query <cypher>` | Cypher 图查询 | `tws-graph query "MATCH (n:class) RETURN n"` |
| `tws-graph lsp setup` | 检测已安装的 LSP server 可用性 | `tws-graph lsp setup` |

## EdgeKind 参考

`tws-graph` 支持 21 种边类型，用于查询和分析时过滤关系：

| 类别 | 边类型 | 说明 |
|------|--------|------|
| 结构关系 | `CALLS` | 函数/方法调用 |
| | `IMPORTS` | 模块/包导入 |
| | `REFERENCES` | 符号引用 |
| | `EXTENDS` | 类继承 |
| | `IMPLEMENTS` | 接口实现 |
| | `CONTAINS` | 包含关系（如文件包含类） |
| 数据流 | `DATA_FLOWS` | 数据流向 |
| | `READS` | 变量读取 |
| | `WRITES` | 变量写入 |
| | `THROWS` | 异常抛出 |
| 环境/事件 | `ENV_ACCESSES` | 环境变量访问 |
| | `EMITS` | 事件发出 |
| | `LISTENS_ON` | 事件监听 |
| 跨服务 | `HTTP_CALLS` | HTTP 调用 |
| | `GRPC_SERVICE` | gRPC 服务定义 |
| | `GRPC_CLIENT` | gRPC 客户端 |
| | `GRPC_SERVER` | gRPC 服务端 |
| 分析 | `SIMILAR_TO` | 代码克隆相似 |
| | `TEST_EDGE` | 测试关联 |
| | `CONFIG_LINK` | 配置-代码关联 |

在 `tws-graph analyze --edge-kinds` 中可以使用逗号分隔的边类型过滤分析范围。

## search 命令 qualifier 参考

`tws-graph search` 支持以下 qualifier，用于精确过滤：

| qualifier | 说明 | 取值 |
|-----------|------|------|
| `kind:` | 符号类型 | 见下方完整注册表 |
| `lang:` | 语言 | `python`, `typescript`, `javascript`, `java`, `go`, `rust`, `kotlin`, `php`, `ruby`, `c`, `cpp`, `csharp`, `scala`, `elixir`, `haskell`, `clojure`, `html`, `css`, `markdown`, `toml`, `sql`, `dockerfile`, `yaml`, `hcl`, `json`, `kustomize` |
| `path:` | 文件路径片段 | 任意字符串，如 `src/auth` |

### kind 完整注册表（40+ 种）

**编程语言通用（多语言共用）：** `class`, `function`, `method`, `module`, `interface`, `struct`, `enum`, `variable`, `constant`, `type_alias`, `namespace`, `file`, `field`

**语言特有：**
- Python: `class`, `function`, `method`, `module`, `variable`, `constant`
- TypeScript/JavaScript: `class`, `function`, `method`, `interface`, `enum`, `enum_member`, `variable`, `type_alias`
- Java: `class`, `method`, `interface`, `enum`, `variable`, `package`, `property`
- Go: `function`, `method`, `type_alias`, `variable`, `property`
- Rust: `function`, `class`, `interface`, `enum`, `variable`, `property`
- Kotlin: `class`, `function`, `method`, `interface`, `enum`, `variable`, `package`, `property`
- PHP: `class`, `function`, `method`, `interface`, `trait`, `namespace`, `variable`, `file`, `property`
- Ruby: `class`, `module`, `method`, `constant`, `variable`, `attribute`, `file`
- C: `function`, `struct`, `union`, `enum`, `variable`, `field`
- C++: `class`, `function`, `method`, `struct`, `namespace`, `enum`, `variable`, `template`, `field`
- C#: `class`, `method`, `struct`, `interface`, `namespace`, `enum`, `variable`, `field`, `file`
- Scala: `scala_file`, `class`, `object`, `trait`, `function`, `variable`, `package`
- Elixir: `elixir_file`, `module`, `function`
- Haskell: `haskell_file`, `module`, `function`, `type_def`, `class`, `instance`, `signature`
- Clojure: `clojure_file`, `namespace`, `var_def`

**结构式语言：**
- HTML: `html_element`
- CSS: `css_rule`, `css_import`, `css_keyframes`, `css_media`
- Markdown: `md_heading`, `md_code_block`, `md_link`, `md_image`, `md_refdef`
- TOML: `toml_table`, `toml_table_array`
- SQL: `sql_table`, `sql_index`, `sql_view`, `sql_query`
- Dockerfile: `dockerfile`, `dockerfile_stage`
- YAML: `yaml_key`, `yaml_document`
- Kubernetes: `k8s_resource`
- HCL: `hcl_resource`, `hcl_data`, `hcl_module`, `hcl_provider`, `hcl_variable`, `hcl_output`, `hcl_terraform`, `hcl_locals`, `hcl_backend`, `hcl_required_providers`, `hcl_provisioner`
- JSON: `json_key`
- Kustomize: `kustomize_section`

### --semantic 选项

`tws-graph search` 支持 `--semantic` 选项启用 11-signal 融合排序（基于 FTS5 BM25 预过滤）：

```
# 语义搜索（自然语言查询，按相关性排序）
tws-graph search --semantic "authentication handler"
tws-graph search auth --semantic --limit 10

# 自定义信号权重（JSON 格式）
tws-graph search tax --semantic --weights '{"BM25":2.0}'

# 结合 embedding 增强
tws-graph search login --semantic --embeddings
```

11 个信号包括：BM25、qualified name 匹配、docstring 匹配、AST 相似度、API 签名相似度、克隆相似度、模块邻近度、图扩散、caller/callee 邻近度、图中心性、数据流连接。

### 搜索示例

```
# 编程语言搜索
tws-graph search kind:function auth
tws-graph search lang:python kind:class user
tws-graph search path:utils kind:method parse
tws-graph search lang:haskell kind:function greet

# 结构式语言搜索
tws-graph search lang:sql kind:sql_table users
tws-graph search lang:dockerfile kind:dockerfile_stage builder
tws-graph search lang:hcl kind:hcl_resource aws_instance
tws-graph search lang:yaml kind:yaml_key replicas
tws-graph search lang:markdown kind:md_heading

# 不指定 kind 搜索所有符号类型
tws-graph search myapp
```

## 常用查询模式

> 索引由 git hooks 自动维护。以下模式省略了 `tws-graph index`。如果刚修改了代码还没 commit，需要先手动 `tws-graph index`。

### 影响分析前

```
tws-graph impact <被改符号> --depth 2
tws-graph calls <被改符号> --inbound
tws-graph snapshot before
```

### 设计同步时

```
tws-graph diff before after
```

### 根因分析时

```
tws-graph trace <入口函数> <报错函数>
tws-graph calls <报错函数> --inbound --depth 3
```

### 查找符号

```
tws-graph search <关键词>
# 如果结果太多，加 qualifier 缩小范围：
tws-graph search kind:function <关键词>
tws-graph search lang:sql kind:sql_table <关键词>
tws-graph search lang:hcl kind:hcl_resource <关键词>
```

### 语义搜索

```
tws-graph search --semantic "authentication handler"
# 11-signal 融合排序，返回最相关的符号
```

### 死代码检测

```
tws-graph analyze --run dead-code
# 检测零入度（无调用者）的函数/方法，排除已知入口点
```

### 入口点检测

```
tws-graph analyze --run entry-point
# 识别项目入口：main 函数、test 函数、CLI 入口、路由 handler 等
```

### 克隆检测

```
tws-graph analyze --algorithm clone --threshold 0.8
# 基于 MinHash + LSH 检测近似重复函数
```

### Cypher 图查询

```
tws-graph query "MATCH (n:class) RETURN n LIMIT 10"
tws-graph query "MATCH (a)-[e:calls]->(b) RETURN a.name, b.name, e.kind"
```

### Git diff 影响分析

```
tws-graph analyze --run git-diff
# 对比当前状态与 HEAD~1，列出变更符号及其影响半径和风险等级
```

### LSP 检测

```
tws-graph lsp setup
# 扫描并检测已安装的 LSP server 可用性
```

## unresolved 引用分类与行动策略

`tws-graph unresolved` 会自动将未解析引用分为两类：

| 标签 | 含义 | 行动策略 |
|------|------|---------|
| `[external]` | 外部 SDK/库（如 `os`, `re`, `typer`, `fastapi`），不在项目源码中 | **停止追踪**，如需了解此依赖的作用，上网搜索文档 |
| `[internal]` | 项目内符号，但因索引缺失/动态调用等原因未能解析 | **回退 grep**，用 Grep 工具在项目中搜索该符号名，手动追踪 |

```
典型使用流程：
tws-graph unresolved
# → 看到 [external] → 忽略，这些是正常的
# → 看到 [internal] → grep 搜索该符号，补全缺失的调用链
```

## MCP 工具参考

`tws-graph serve` 启动 MCP 服务器，暴露 15 个工具和 3 个资源。所有工具通过统一的 `tws://` 协议访问。

### MCP 工具列表（15 工具）

| 工具名 | 类别 | 说明 |
|--------|------|------|
| `search_symbols` | 搜索 | FTS5 全文搜索符号（支持 qualifier） |
| `semantic_search` | 搜索 | 11-signal 融合语义搜索，按相关性排序 |
| `get_code` | 代码 | 根据符号 ID 获取源码和元数据 |
| `get_dependencies` | 代码 | 获取调用者（inbound）或被调用者（outbound） |
| `get_impact` | 代码 | 计算影响半径和风险等级 |
| `trace_path` | 代码 | 查找两个符号之间的调用路径 |
| `query_cypher` | 查询 | 执行 Cypher 图查询 |
| `detect_cross_service` | 查询 | 检测跨服务通信（HTTP 路由、消息通道、gRPC） |
| `get_complexity` | 分析 | 分析代码复杂度（循环/认知/Halstead） |
| `find_dead_code` | 分析 | 检测潜在未使用代码 |
| `get_test_coverage` | 分析 | 三项启发式策略分析测试覆盖 |
| `get_entry_points` | 分析 | 识别项目入口点（按类型分类） |
| `find_clones` | 高级 | MinHash + LSH 检测代码克隆 |
| `get_git_diff_impact` | 高级 | Git diff 影响分析 |
| `get_config_links` | 高级 | 发现代码常量与配置文件的关联 |

### MCP 资源（3 资源）

| URI | 说明 |
|-----|------|
| `tws://stats` | 代码图统计（节点数、边数、文件数、语言分布、索引时间） |
| `tws://languages` | 各语言节点数和文件数分布 |
| `tws://health` | 健康状态（索引就绪、LSP 可用、运行时间） |

### 启动方式

```bash
# 默认配置
tws-graph serve

# 指定项目根目录和数据库路径
tws-graph serve --root /path/to/project --db .tws/codegraph/index.db
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

2. **未 commit 的修改不跑 index 直接查图**。子 agent 刚改完代码还没 commit → hooks 没触发 → 索引是旧的。此时应先 `tws-graph index`。

3. **改前不拍快照**。design-sync 需要 before/after 对比，没有 before 快照就等于白做。

4. **不检查可用性就假设已安装**。每次加载此 skill 时都必须先跑 `tws-graph --version`。

5. **用 Grep 查已知符号名**。类名、方法名、函数名、SQL 表、HCL 资源、YAML 键等所有被索引的符号必须先用 `tws-graph search` 查。Grep 只允许用于 tws-graph 不索引的文件类型（XML、.gradle、图片、二进制）、文件模式匹配、或图返回空/`[internal]` 后的回退。

## Rationalization Prevention

| 「用 grep 也一样」 | grep 找不到跨文件间接调用，也看不到多跳路径。图给你完整的依赖闭包 |
| 「我记住命令了不用加载」 | 加载此 skill 正是为了防止命令拼错。`callers` 不是命令，`--inbound` 才是 |
| 「hooks 应该同步了，不用管」 | 刚改完代码还没 commit 时 hooks 不会触发，此时手动 `tws-graph index` 是必要的 |
| 「返回空就是没调用关系」 | 动态调度、回调、闭包不会出现在静态分析中。标注 heuristic，回退 grep |
| 「AppContainer 是类名，Grep 一下就行」 | 类名是符号，必须用 `tws-graph search kind:class`。Grep 只能看到文本出现，看不到结构化关系 |
| 「users 表名，grep 一下就行」 | SQL 表是 `sql_table` 节点，用 `tws-graph search lang:sql kind:sql_table users`。Grep 看不到表与索引/视图的关系 |
| 「YAML 文件不看代码图」 | YAML 键、K8s 资源、HCL 配置块都已索引为节点。`tws-graph search` 能找到 grep 漏掉的跨文件关联 |
| 「这个参数应该存在」 | 不猜。此 skill 中的命令参考表和 kind 注册表是唯一权威，表上没有的就是不存在 |
| 「语义搜索和普通搜索没区别」 | 语义搜索使用 11-signal 融合排序（BM25 + AST 相似 + 图扩散等），能发现关键词匹配不到的语义相关符号 |
| 「直接 grep 也是一样的搜索效果」 | 图搜索有结构化关系（kind、lang、path 过滤），语义搜索有多信号排序。grep 只有文本匹配 |
| 「用 MCP 工具跟用 CLI 一样」 | MCP 工具供 IDE 和外部 agent 集成，适合远程/进程内查询；CLI 适合终端交互和脚本。场景不同 |
| 「索引覆盖的语言不够全」 | tws-graph 通过 15 个 tree-sitter 提取器覆盖 20+ 语言和配置文件格式。未覆盖的才回退 grep |
