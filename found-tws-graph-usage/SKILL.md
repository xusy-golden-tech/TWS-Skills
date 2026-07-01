---
name: tws-graph-usage
description: tws-graph 代码图使用指南（v7.3.1）。所有需要查图的子 agent 必须加载此 skill。包含安装检查、命令语法、错误处理和最佳实践
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

tws-graph 通过 28+ 个提取器覆盖 30+ 种语言和配置格式。所有提取器均产出节点（可搜索的符号）+ 边（关系）。

| 类别 | 覆盖 |
|------|------|
| 编程语言 | Python, TypeScript, JavaScript, Java, Go, Rust, Kotlin, PHP, Ruby, C, C++, C#, Scala, Elixir, Haskell, Clojure, Lua, Bash/Shell |
| 标记/样式 | HTML, CSS, Markdown |
| 配置/IaC | YAML, TOML, JSON, HCL/Terraform, Kustomize, Kubernetes, Dockerfile, Proto |
| 数据 | SQL |

## 前置检查（每次查图前必做）

```
0. 确定项目根目录（必须第一步执行）
   # 用 Git 定位项目根目录，后续所有 tws-graph 命令都从此目录执行
   Bash: cd $(git rev-parse --show-toplevel 2>/dev/null || pwd) 2>&1

   索引数据库默认路径是项目根下的 .tws/codegraph/index.db。
   如果你 cd 到子目录再跑 tws-graph 命令而不传 --db，会找不到索引库。
   
   ✅ 正确：cd D:/project && tws-graph search MyClass
   ✅ 正确：tws-graph search MyClass --db D:/project/.tws/codegraph/index.db
   ❌ 错误：cd D:/project/subdir && tws-graph search MyClass  （找的是 subdir/.tws/codegraph/index.db）

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

### 索引与同步

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph index` | 全量/增量索引源文件（默认 rayon 并行提取） | `tws-graph index` 或 `tws-graph index --force --deep` |
| `tws-graph sync` | 增量同步（stat 预筛选，比 index 更快） | `tws-graph sync` |
| `tws-graph index` / `sync` 范围过滤 | `--twsignore` 自定义忽略文件，`--include`/`-I` 限定范围，`--exclude`/`-X` 排除文件 | `tws-graph index --include "src/**" --exclude "tests/"` |
| `tws-graph hooks install` | 安装 git hooks（自动增量索引） | `tws-graph hooks install` |
| `tws-graph watch` | 文件变更监听，自动增量同步 | `tws-graph watch --path . --interval 2.0` |

**并行控制**：`tws-graph index` 默认使用 rayon 并行提取（文件级 `par_chunks`）。设置环境变量 `TWS_USE_PARALLEL=0` 可回退到串行模式（调试/对比用，输出与并行 100% 一致）。

```
# 串行提取（调试用）
TWS_USE_PARALLEL=0 tws-graph index

# 并行提取（默认）
tws-graph index
```

### .twsignore 忽略文件（v7.2.0）

项目根目录放置 `.twsignore` 文件，格式与 `.gitignore` 一致，索引时自动排除匹配的文件：

```
# 排除构建产物和测试
*.pyc
__pycache__/
node_modules/
tests/
*.log

# 反选：保留重要文件
!tests/smoke/
```

- 每行一个 glob pattern，`#` 注释，`!` 反选（re-include）
- 索引时 scanner 自动读取项目根 `.twsignore`，与 `.gitignore` 叠加过滤
- 自定义路径：`tws-graph index --twsignore .myignore`

### 符号查询

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph search <query>` | FTS5 全文搜索符号 | `tws-graph search kind:class my` |
| `tws-graph calls <node>` | 查调用目标（这个符号调了谁） | `tws-graph calls my_func` |
| `tws-graph calls <node> --inbound` | 查调用者（谁调了这个符号） | `tws-graph calls my_func --inbound` |
| `tws-graph impact <node>` | 查变更影响范围（谁依赖这个符号） | `tws-graph impact MyClass.my_method --depth 2` |
| `tws-graph trace <src> <tgt>` | 查两个符号之间的调用路径 | `tws-graph trace main parse_config` |
| `tws-graph unresolved` | 列出未解析引用，自动标记 `[external]`/`[internal]` | `tws-graph unresolved` |

> **范围过滤（v7.2.0）**：`search`/`calls`/`impact`/`trace`/`unresolved` 均支持 `--include`/`-I` 和 `--exclude`/`-X`。例：`tws-graph search auth --include "src/**" --exclude "tests/"`

### 快照与差异

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph snapshot <name>` | 创建命名快照 | `tws-graph snapshot before` |
| `tws-graph diff` | 对比快照（不加参数列出所有快照） | `tws-graph diff before after` |
| `tws-graph diff <a> <b> --brief` | 简要对比（仅输出 changed/unchanged） | `tws-graph diff before after --brief` |
| `tws-graph diff <a> <b> --json` | JSON 格式差异输出 | `tws-graph diff before after --json` |

### 图查询与分析

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph query <gql>` | GQL 图查询语言（v5.5.0 起替代 Cypher） | `tws-graph query "FIND function WHERE name MATCHES 'auth'"` |
| `tws-graph cycles` | 检测调用图中的循环依赖 | `tws-graph cycles` |
| `tws-graph layers` | 检测架构层次违规 | `tws-graph layers` |
| `tws-graph metrics` | 计算模块内聚/耦合/不稳定性度量 | `tws-graph metrics` |
| `tws-graph taint` | 安全污点分析（source→sink 路径追踪） | `tws-graph taint` |
| `tws-graph predict-impact` | 预测修改某符号的影响范围和风险 | `tws-graph predict-impact my_func` |
| `tws-graph health` | 代码健康评分（测试覆盖+死代码+耦合） | `tws-graph health --worst 10` |
| `tws-graph analyze` | 图分析（算法：clone/community/centrality） | `tws-graph analyze --algorithm clone --threshold 0.8` |

> **范围过滤（v7.2.0）**：以上全部命令支持 `--include`/`-I` 和 `--exclude`/`-X`。例：`tws-graph cycles --exclude "tests/"`、`tws-graph health --include "src/**" --worst 10`

### 导出与工具

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph export dot` | 导出 Graphviz DOT 格式 | `tws-graph export dot --from my_func --depth 2` |
| `tws-graph export mermaid` | 导出 Mermaid 图（适合 Markdown） | `tws-graph export mermaid --from my_class` |
| `tws-graph export json` | 导出 JSON 格式（节点+边） | `tws-graph export json --kind calls` |
| `tws-graph serve` | 启动 MCP 服务器（21 工具 + 3 资源） | `tws-graph serve --root . --db .tws/codegraph/index.db` |
| `tws-graph lint` | 校验 skill 文件结构 | `tws-graph lint` |
| `tws-graph lsp setup` | 检测已安装的 LSP server 可用性 | `tws-graph lsp setup` |

> **范围过滤（v7.2.0）**：`export` 命令支持 `--include`/`-I` 和 `--exclude`/`-X`。例：`tws-graph export json --kind calls --exclude "tests/"`

## EdgeKind 参考

`tws-graph` 支持 24 种边类型（EdgeKind 枚举定义），以下为实际有产出的边类型：

| 类别 | 边类型 | 说明 |
|------|--------|------|
| 结构关系 | `CALLS` | 函数/方法调用 |
| | `IMPORTS` | 模块/包导入 |
| | `REFERENCES` | 符号引用 |
| | `EXTENDS` | 类继承 |
| | `IMPLEMENTS` | 接口实现 |
| | `OVERRIDES` | 方法覆写（v5.2.0） |
| | `INSTANTIATES` | 类实例化（v5.2.0） |
| | `DECORATES` | 装饰器/注解应用（v5.2.0） |
| | `TYPE_REF` | 类型注解引用（v5.2.0） |
| | `CONTAINS` | 包含关系（如文件包含类） |
| 数据流 | `DATA_FLOWS` | 数据流向（含 cross-function / cross-file） |
| | `READS` | 变量读取 |
| | `WRITES` | 变量写入 |
| | `THROWS` | 异常抛出（含跨函数传播） |
| 环境/事件 | `ENV_ACCESSES` | 环境变量访问 |
| | `EMITS` | 事件发出 |
| | `LISTENS_ON` | 事件监听 |
| 跨服务 | `HTTP_CALLS` | HTTP 调用 |
| | `GRPC_SERVICE` | gRPC 服务定义 |
| | `GRPC_CLIENT` | gRPC 客户端 |
| | `GRPC_SERVER` | gRPC 服务端 |
| 分析 | `SIMILAR_TO` | 代码克隆相似（--deep 模式） |
| | `TEST_EDGE` | 测试关联 |
| | `CONFIG_LINK` | 配置-代码关联 |

## search 命令 qualifier 参考

`tws-graph search` 支持以下 qualifier，用于精确过滤：

| qualifier | 说明 | 取值 |
|-----------|------|------|
| `kind:` | 符号类型 | 见下方完整注册表 |
| `lang:` | 语言 | `python`, `typescript`, `javascript`, `java`, `go`, `rust`, `kotlin`, `php`, `ruby`, `c`, `cpp`, `csharp`, `scala`, `elixir`, `haskell`, `clojure`, `bash`, `lua`, `html`, `css`, `markdown`, `toml`, `sql`, `dockerfile`, `yaml`, `hcl`, `json`, `kustomize`, `proto` |
| `path:` | 文件路径片段 | 任意字符串，如 `src/auth` |

### kind 完整注册表（40+ 种）

**编程语言通用（多语言共用）：** `class`, `function`, `method`, `module`, `interface`, `struct`, `enum`, `variable`, `constant`, `type_alias`, `namespace`, `file`, `field`

**语言特有：**
- Python: `class`, `function`, `method`, `module`, `variable`, `constant`
- TypeScript/JavaScript: `class`, `function`, `method`, `interface`, `enum`, `enum_member`, `variable`, `type_alias`
- Java: `class`, `method`, `interface`, `enum`, `variable`, `package`, `property`, `lambda`, `record`
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
- Bash: `bash_file`, `function`, `variable`
- Lua: `lua_file`, `function`, `variable`, `table`

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
- Proto: `proto_file`, `service`, `rpc_method`

### --semantic 选项

`tws-graph search` 支持 `--semantic` 选项启用 11-signal 融合排序（基于 FTS5 BM25 预过滤）：

```
# 语义搜索（自然语言查询，按相关性排序）
tws-graph search --semantic "authentication handler"
tws-graph search auth --semantic --limit 10
```

11 个信号包括：BM25、qualified name 匹配、docstring 匹配、AST 相似度、API 签名相似度、克隆相似度、模块邻近度、图扩散、caller/callee 邻近度、图中心性、数据流连接。

### 搜索示例

```
# 编程语言搜索
tws-graph search kind:function auth
tws-graph search lang:python kind:class user
tws-graph search path:utils kind:method parse

# 结构式语言搜索
tws-graph search lang:sql kind:sql_table users
tws-graph search lang:dockerfile kind:dockerfile_stage builder
tws-graph search lang:hcl kind:hcl_resource aws_instance
tws-graph search lang:yaml kind:yaml_key replicas

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

# <被改符号> 支持多种记法：
tws-graph impact ClassName.method_name --depth 2        # Class.method 记法（v7.3.1）
tws-graph impact method_name --depth 2                  # 裸方法名
tws-graph impact file::Class::method --depth 2          # qualified_name 记法
```

### 设计同步时

```
tws-graph diff before after
```

### 根因分析时

```
tws-graph trace <入口函数> <报错函数>
tws-graph calls <报错函数> --inbound --depth 3

# trace/calls 支持 Class.method 记法（v7.3.1）：
tws-graph trace "ClassName.method_name" "TargetClass.target_method"
tws-graph calls "ClassName.method_name" --inbound
```

### 查找符号

```
tws-graph search <关键词>
# 如果结果太多，加 qualifier 缩小范围：
tws-graph search kind:function <关键词>
tws-graph search lang:sql kind:sql_table <关键词>
tws-graph search lang:hcl kind:hcl_resource <关键词>
```

### 查函数/方法实现（排除测试文件）

**关键规则：查实现时必须排除测试目录，否则实现会被测试调用淹没。**

```
# 查找方法/函数的真正实现（排除 tests/ 目录）
tws-graph search kind:method <方法名> --exclude "tests/"
tws-graph search kind:function <函数名> --exclude "tests/"

# 如果还需要缩小范围，叠加路径过滤：
tws-graph search kind:method <方法名> --exclude "tests/" --include "src/"

# 查类定义：
tws-graph search kind:class <类名> --exclude "tests/"
```

**为什么需要 `--exclude "tests/"`：** 测试文件中对函数的调用会产生同名节点（如 `test_*` 方法调用目标），搜索时测试文件中的引用会淹没真正的实现。不加此过滤时，前 20 条结果可能全在 tests/ 里。

> **范围过滤（v7.2.0）**：`search`/`calls`/`impact`/`trace`/`unresolved` 均支持 `--include`/`-I` 和 `--exclude`/`-X`。

### 语义搜索

```
tws-graph search --semantic "authentication handler"
# 11-signal 融合排序，返回最相关的符号
```

### GQL 图查询（v5.5.0+）

```
tws-graph query "FIND function WHERE name MATCHES 'auth'"
tws-graph query "FIND class WHERE file_path MATCHES 'src/' RETURN name, file_path LIMIT 20"
tws-graph query "IMPACT MyClass.my_method"
```

### 架构分析

```
tws-graph cycles                    # 检测循环依赖
tws-graph layers                    # 检测层次违规
tws-graph metrics                   # 模块内聚/耦合度量
tws-graph health --worst 10         # 代码健康评分，最差 10 个文件
```

### 架构分析（限定范围，v7.2.0）

```
tws-graph cycles --exclude "tests/"              # 排除测试目录的循环依赖
tws-graph metrics --include "src/**"             # 只统计 src 下的模块
tws-graph health --worst 10 --exclude "tests/"   # 只评估源码健康度
```

### 安全分析

```
tws-graph taint                     # 污点分析（source→sink 全路径）
tws-graph taint --exclude "tests/"  # 排除测试文件的污点路径
```

### 影响预测

```
tws-graph predict-impact my_func    # 预测修改影响（半径+风险+测试建议）
tws-graph predict-impact my_func --exclude "tests/"
```

### 图导出

```
tws-graph export dot --from main --depth 2     # DOT 格式（Graphviz）
tws-graph export mermaid --from MyClass        # Mermaid 格式（Markdown）
tws-graph export json --kind calls             # JSON 格式
tws-graph export json --kind calls --exclude "tests/"
```

### 代码质量

```
tws-graph analyze --run dead-code              # 死代码检测
tws-graph analyze --run entry-point            # 入口点识别
tws-graph analyze --run git-diff               # Git diff 影响分析
tws-graph analyze --run dead-code --exclude "tests/"
tws-graph analyze --algorithm clone --threshold 0.8   # 克隆检测
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

`tws-graph serve` 启动 MCP 服务器，暴露 **21 个工具**和 **3 个资源**。所有工具纯脱网运行，零 HTTP 依赖。

### 搜索类（2 工具）

| 工具名 | 说明 |
|--------|------|
| `search_symbols` | FTS5 全文搜索符号（支持 qualifier） |
| `semantic_search` | 11-signal 融合语义搜索，按相关性排序 |

### 代码类（4 工具）

| 工具名 | 说明 |
|--------|------|
| `get_code` | 根据符号 ID 获取源码和元数据 |
| `get_dependencies` | 获取调用者（inbound）或被调用者（outbound） |
| `get_impact` | 计算影响半径和风险等级 |
| `trace_path` | 查找两个符号之间的调用路径 |

### 分析类（4 工具）

| 工具名 | 说明 |
|--------|------|
| `get_complexity` | 分析代码复杂度（循环/认知/Halstead） |
| `find_dead_code` | 检测潜在未使用代码 |
| `get_test_coverage` | 三项启发式策略分析测试覆盖 |
| `get_entry_points` | 识别项目入口点（按类型分类） |

### 高级类（3 工具）

| 工具名 | 说明 |
|--------|------|
| `find_clones` | MinHash + LSH 检测代码克隆 |
| `get_git_diff_impact` | Git diff 影响分析 |
| `get_config_links` | 发现代码常量与配置文件的关联 |

### 查询类（3 工具）

| 工具名 | 说明 |
|--------|------|
| `query_cypher` | 执行图查询（支持 Cypher/GQL） |
| `get_edge_distribution` | 获取边类型分布统计 |
| `detect_cross_service` | 检测跨服务通信（HTTP、消息、gRPC） |

### 开发辅助类（5 工具）— P44+P46

| 工具名 | 说明 |
|--------|------|
| `review_changes` | 代码审查辅助：下游影响分析 + 测试建议 + 质量门禁 |
| `safe_refactor` | 重构安全检查：依赖分析 + 完整修改清单 + 重构建议 |
| `api_compat_check` | API 兼容性检查：breaking change 检测 + semver 建议 |
| `find_pattern` | AST 结构模式搜索：同义词 + body 搜索 + 结构匹配 |
| `security_scan` | 安全漏洞检测：SQL 注入、硬编码密钥、路径遍历等 5 类 |

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

# 生成 Claude Code MCP 配置
tws-graph serve mcp-config
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

### 命令返回非零退出码（exit code 1）

```
最常见原因：工作目录不对，索引库在项目根目录 .tws/ 下，但你 cd 到了子目录
排查：
  1. pwd 确认当前目录
  2. ls .tws/codegraph/index.db 确认索引库是否存在
  3. 如果不存在 → cd 到项目根目录（git rev-parse --show-toplevel）再试
  4. 如果确实没有索引库 → tws-graph index
处理：修正工作目录后重试。连续 2 次失败 → 标注降级，回退 grep
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
   - `tws-graph cypher` — Cypher 已改为 GQL，正确命令是 `tws-graph query`
   - `tws-graph dead-code` — 正确命令是 `tws-graph analyze --run dead-code`

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
| 「索引覆盖的语言不够全」 | tws-graph 通过 28+ 个提取器覆盖 30+ 语言和配置格式。未覆盖的才回退 grep |
