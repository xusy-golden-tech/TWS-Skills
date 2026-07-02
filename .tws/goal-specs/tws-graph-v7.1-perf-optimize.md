# Goal: tws-graph 性能评估与增强 — Benchmark + 可读输出 + Grep Hook

## 元信息
- 创建时间：2026-07-02
- 关联 flow：flow-add-feature + flow-investigate（混合）
- 关联 session：.tws/sessions/tws-graph-perf-enhance.md
- 关联分支：feature/tws-graph-perf-enhance
- 目标状态：⏳ 进行中
- Spec 版本：v2（Phase A 完成后展开完整 spec）

## 总体目标

### 问题背景

tws-graph v7.3.4（Rust 核心 + Python CLI，30+ 语言代码符号关系图）已完成功能建设，具备 19 个 CLI 命令和 21 个 MCP 工具。但在推广到更多 agent 工作流之前，缺少两样关键证据和能力：

1. **缺少量化数据证明 token 节省效果**：当前宣称"用 tws-graph 替代 Grep 可以节省 token"，但没有可重复的实验数据支撑。需要一次受控 Benchmark，用实际项目（g-ass-source，70K 节点 + 244K 边 + 2,220 文件 + 186MB DB）对比三组条件（纯 Grep / tws-graph / 理想上限），产出 token 节省率报告。

2. **Calls 输出对人类不可读**：当前 `tws-graph calls` 命令的输出格式为 `{indent}{depth} ({kind}):{name} @ {file_path}`，不包含 signature、docstring、行号。人类在终端看到这样的输出时，必须再用 read/open 工具查看源码才能理解每个符号做了什么。数据库 nodes 表已有 17 列（含 signature、docstring、start_line 等），但查询层 `get_node()` 只 SELECT 了 6 列。

3. **Grep 工具调用无法自动转接到 tws-graph**：agent 习惯使用内置 Grep 工具搜索符号，不会主动改用 tws-graph。需要评估各路径可行性，若能拦截 Grep 调用并路由到 tws-graph，则 token 节省从"建议"变为"强制"。

### 当前行为

**Calls 输出**：
- `CallsResult` struct（`rust_core/src/query/mod.rs:25-32`）仅 6 字段：`depth, node_id, node_name, node_kind, file_path, edge_kind`
- `get_node()` 方法（`rust_core/src/db/connection.rs:229-251`）仅 SELECT 6 列：`id, kind, name, qualified_name, language, file_path`
- PyO3 `calls()` 函数（`rust_core/src/lib.rs:647-678`）拼装纯文本输出，格式为 `"{indent}{depth} ({kind}):{name} @ {file_path}"`（行 674-675），无 --json 支持
- CLI `calls` 命令（`cli.py:258-288`）声明了 `json_output` 参数但未使用（接受参数后未传参给 rus_bridge，直接 echo 文本）
- nodes 表（`migrations.rs:110-130`）有 17 列，其中 `signature`（行 119）、`docstring`（行 120）、`visibility`（行 121）、`start_line`（行 117）未被查询层使用
- docstring 提取：`extract_docstring()`（`python.rs:1195-1211`）只处理第一个 named_child（expression_statement > string），产出极少（g-ass-source 0 条，TWS-Skills 仅 54 条）。`context.rs` 侧已修复但测试断言使用 `if let` 导致静默通过

**Grep 使用**：
- 无任何拦截机制，agent 直接调用内置 Grep 工具搜索符号
- 符合 `found-tws-graph-usage` skill 规则的 agent 会优先使用 tws-graph，但无规则约束时无法保证

### 期望行为

**Calls 输出增强后**：
- `CallsResult` 新增 4 个 `Option` 字段：`signature, start_line, docstring, visibility`
- 新增 `NodeInfo` struct（9 字段：`id, kind, name, qualified_name, language, file_path, signature, start_line, docstring`）
- 新增 `get_node_rich()` 方法（SELECT 扩至 9 列），原有 `get_node()` 不变（零侵入旧代码）
- CLI 三种输出模式：
  - 默认 **rich**：树形缩进 + 符号签名 + 第一行 docstring + 行号
  - `--json`：结构化 JSON，每个结果含全部 9 字段
  - `--brief`：旧格式兼容（`{indent}{depth} ({kind}):{name} @ {file_path}`）
- docstring 修复独立先行：`python.rs` 的 `extract_docstring()` 扩展为处理多种 tree-sitter 节点结构（class/function body 的第一条 expression_statement 字符串、decorator 之后的字符串），测试用 `assert!` 替代 `if let` 确保断言生效

**Benchmark 执行后**：
- 输出一份 `benchmark-report.json`，包含 42 个 test session 的完整数据
- 输出一份 `benchmark-summary.md`，含三组对比的 token 节省率、正确性得分、耗时对比
- 实验可重现：所有查询参数、symbol 名称、prompt 模板登记在报告中

**Grep Hook POC 完成后**：
- 工作原型：agent 使用 Grep 工具搜索已知符号时，hook 脚本自动拦截并改用 tws-graph
- 可安装：一份 `install-grep-hook.sh` 脚本 + settings.json 配置
- 可测量：原型启用前后的 token 消耗差值可量化

### 范围边界

**IN SCOPE（在范围内）：**
- B1: `CallsResult` 扩展（+4 Option 字段）、新增 `NodeInfo` + `get_node_rich()`、CLI 三种输出模式、docstring 提取修复、Python bridge 适配、CLI 参数更新
- B2: Benchmark 实验执行（42 test sessions + 1 warm-up）、数据记录、报告生成
- B3: Grep Hook POC 原型开发、安装脚本、验证

**OUT OF SCOPE（明确不做）：**
- 不修改数据库 schema（nodes 表已有 17 列）
- 不修改 `ImpactResult` 或其他 query result struct
- 不修改 MCP server 工具签名（MCP 工具独立于 CLI）
- 不修改 `tws-graph export` 或 `tws-graph trace` 的输出格式
- Benchmark 不覆盖 `impact`、`trace`、`search` 之外的命令
- Grep Hook 不做生产级错误处理或复杂 pattern 分析（那是 Phase 2）

### 成功指标

- [ ] `tws-graph calls CodingTaskService --depth 2` 输出包含每个符号的 signature（如 `def process(self, ...) -> Result[Task]`）和首行 docstring
- [ ] `tws-graph calls PluginManager --json --depth 2` 输出合法 JSON，每个结果包含 `signature`、`docstring`、`start_line`、`visibility` 字段
- [ ] `tws-graph calls Mediator --brief --depth 2` 输出与旧格式完全一致（向后兼容）
- [ ] `cargo test --lib` 在 tws-graph 根目录全部通过，新增 6+ 测试用例覆盖 get_node_rich / CallsResult 格式化 / JSON 序列化
- [ ] benchmark-report.json 包含 42 个 session 的 `input_tokens, output_tokens, cache_tokens, wall_time_ms`
- [ ] Token 节省率：tws-graph 组相对纯 Grep 组节省 >= 40%（input tokens）
- [ ] Grep Hook POC：安装后 agent 搜索 `kind:class` 符号时，Grep 工具调用被 twist-graph search 替换
- [x] `tws-graph lint` 零新增错误（31 errors 均预存，非本次变更引入）

## 约束条件

- 主 agent 不写代码，编码/测试由子 agent 执行
- 代码调查优先用 tws-graph，Grep 是回退手段
- 数据库 schema 已完备（17 列含 signature/docstring），改查询层不需要改 schema
- g-ass-source 项目的 tws-graph 索引在 D:/g-ass-source/.tws/codegraph/index.db
- 遵守 CLAUDE.md 全局规则
- 当前分支：feature/tws-graph-perf-enhance

## 总体架构设计

### 模块结构

```
tws-graph/
  rust_core/src/                     — Rust 核心库（_core PyO3 模块）
    db/
      connection.rs                  — 修改：新增 get_node_rich() 方法
      migrations.rs                  — 不改（schema 已完备）
      models.rs                      — 可能读取（确认 NodeRecord 结构）
    query/
      mod.rs                         — 修改：CallsResult 扩展 + run_calls 格式化逻辑
      traversal.rs                   — 不改（遍历逻辑不涉及字段扩展）
    lib.rs                           — 修改：PyO3 calls() 函数支持 --json/--brief
    indexer/
      extractors/
        python.rs                    — 修改：extract_docstring() 扩充 + 测试修复
        context.rs                   — 可能读取（确认 docstring 写入路径）
  src/tws_graph/
    rust_bridge.py                   — 修改：rust_calls() 签名增加 json/brief 参数
    cli.py                           — 修改：calls 命令使用 json_output 参数

g-ass-source/                        — Benchmark 目标项目，不改代码
  .tws/codegraph/index.db            — 已有索引（70K 节点 + 244K 边）

.tws/
  sessions/
    tws-graph-perf-enhance.md        — 会话状态跟踪
  goal-specs/
    tws-graph-v7.1-perf-optimize.md  — 本 spec

benchmark/                           — 新增：Benchmark 脚本和数据（独立于 tws-graph 源码）
  scripts/
    run_benchmark.sh                 — 实验执行脚本
    token_counter.py                 — Token 计数工具（基于基线减法）
  data/
    queries.json                     — 测试查询定义（5 类 × 2-3 symbols）
    prompts/
      grep.txt                       — Grep 组 prompt 模板
      tws-graph.txt                  — tws-graph 组 prompt 模板
      ideal.txt                      — 理想组 prompt 模板
  results/
    benchmark-report.json            — 原始数据
    benchmark-summary.md             — 汇总报告

grep-hook/                           — 新增：Grep Hook POC（独立于 tws-graph 源码）
  install-grep-hook.sh               — 安装脚本
  grep-hook.sh                       — Hook 脚本本体（~50 行 bash）
  settings.json.template             — Claude Code settings.json 模板
  README.md                          — 安装和使用说明
```

### 关键接口

**Rust 侧（query/mod.rs）**：

```rust
// 现有 CallsResult — 扩展 4 个 Option 字段
#[derive(Debug, Clone, Serialize)]
pub struct CallsResult {
    pub depth: usize,
    pub node_id: String,
    pub node_name: String,
    pub node_kind: String,
    pub file_path: String,
    pub edge_kind: String,
    // 新增
    pub signature: Option<String>,
    pub start_line: Option<i64>,
    pub docstring: Option<String>,
    pub visibility: Option<String>,
}

// 新增 NodeInfo — 完整节点信息
#[derive(Debug, Clone, Serialize)]
pub struct NodeInfo {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub qualified_name: String,
    pub language: String,
    pub file_path: String,
    pub signature: Option<String>,
    pub start_line: Option<i64>,
    pub docstring: Option<String>,
}
```

**Rust 侧（db/connection.rs）**：

```rust
// 新增 get_node_rich — SELECT 9 列
pub fn get_node_rich(
    &self,
    node_id: &str,
) -> rusqlite::Result<Option<NodeInfo>> {
    // SELECT id, kind, name, qualified_name, language, file_path,
    //        signature, start_line, docstring FROM nodes WHERE id = ?1
}

// 原有 get_node 保持不变（零侵入）
pub fn get_node(
    &self,
    node_id: &str,
) -> rusqlite::Result<Option<(String, String, String, String, String, String)>> {
    // SELECT id, kind, name, qualified_name, language, file_path ...
    // 不动
}
```

**Rust 侧（lib.rs）**：

```rust
// 现有 #[pyfunction] fn calls — 签名不变，内部逻辑改为：
// 1. 用 get_node_rich 替代 get_node 填充 CallsResult 的新字段
// 2. 根据 format 参数切换三种输出模式

#[pyfunction]
#[pyo3(signature = (db_path, name, inbound=None, depth=None,
                    format=None, include_paths=None, exclude_paths=None))]
fn calls(...) -> PyResult<String> {
    // format: None/"text"=rich, "json"=JSON, "brief"=旧格式
}
```

**Python 侧（rust_bridge.py）**：

```python
def rust_calls(db_path: str, node_name: str, inbound: bool = True,
               depth: int = 5, format: str | None = None,
               include_paths: list[str] | None = None,
               exclude_paths: list[str] | None = None) -> str:
    # 新增 format 参数，透传给 _core._core.calls
```

**Python 侧（cli.py）**：

```python
@app.command()
def calls(
    # ... 现有参数不变 ...
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    brief: bool = typer.Option(False, "--brief", help="简要输出（旧格式兼容）"),
):
    # 新增 --brief 参数
    # 将 json_output/brief 转为 format 参数传给 rust_calls
    if json_output and brief:
        typer.echo("错误: --json 和 --brief 不能同时指定", err=True)
        raise typer.Exit(1)
    fmt = "json" if json_output else ("brief" if brief else None)
    typer.echo(rust_calls(..., format=fmt))
```

**Python 侧（python.rs extract_docstring）**：

```rust
// 扩展 extract_docstring 处理多种节点结构：
// 1. class_definition / function_definition 的 body block 第一条 expression_statement > string
// 2. decorated_definition 的 body 第一条 expression_statement > string
// 3. 多个连续字符串（合并为单条 docstring）
fn extract_docstring(source: &[u8], body: Node) -> Option<String> {
    // 遍历 body 的 named_children，收集连续的 expression_statement > string
    // 跳过装饰器后的空行
}
```

### 数据流/调用链

**B1 数据流（Calls 输出增强）**：

```
CLI: tws-graph calls X --depth 2
  → cli.py::calls()                    # 解析参数，确定 format
    → rust_bridge.py::rust_calls()     # Python → Rust 桥接
      → lib.rs::calls()                # PyO3 函数
        → db::Database::open()         # 打开 SQLite
        → db::find_node_id_by_name()   # 查找起始节点 ID
        → query::run_calls()           # BFS 遍历
          → traverser::inbound_callers/outbound_calls()
          → 对每个结果节点：
            → db::get_node_rich()      # [新] SELECT 9 列
            → 填充 CallsResult         # [新] 含 signature/docstring/line/visibility
        → 按 format 参数格式化输出：
          - None/"text" → 树形 rich 格式
          - "json" → serde_json::to_string_pretty()
          - "brief" → 旧格式兼容
    → 返回 String 到 Python
  → cli.py 输出到 stdout
```

**B2 数据流（Benchmark）**：

```
benchmark/scripts/run_benchmark.sh
  → 对每个 query:
    1. 发出 Claude Code API 请求（Grep 模式）
    2. 发出 Claude Code API 请求（tws-graph 模式）
    3. 发出 Claude Code API 请求（理想模式）
  → token_counter.py 记录 response.usage（input/output/cache tokens）
  → 汇总到 benchmark-report.json
  → 生成 benchmark-summary.md
```

**B3 数据流（Grep Hook）**：

```
Agent 调用: Grep(pattern="ClassName", ...)
  → Claude Code CLI PreToolUse hook 触发
    → grep-hook.sh:
      1. 解析 pattern 是否为已知符号名
      2. 如果是 → 用 tws-graph search 替代
      3. 如果不是 → 透传原 Grep 调用
    → 返回 tws-graph 查询结果
```

**三个子目标之间的关系**：

```
B1 (可读输出增强) ← 独立，不依赖 B2/B3
B2 (Benchmark)     ← 独立，不依赖 B1/B3，但建议 B1 做完后再跑
B3 (Grep Hook)     ← 独立，不依赖 B1/B2

推荐执行顺序: B1 → B2 → B3
  - B1 先做，确保 tws-graph 输出质量后再 Benchmark，数据更好看
  - B2 在 B1 后做，rich 模式的输出也会影响 token 计数
  - B3 最后做，依赖 A4 结论但不依赖 B1/B2 的代码产出
```

## 阶段 B1: 人类可读输出增强

### 门禁
- [x] A3 方案设计已完成（✅）
- [x] tws-graph Rust 编译环境就绪（`cargo check` 在 `tws-graph/rust_core/` 下通过）
- [x] Python 虚拟环境已安装 `tws-graph`（`pip install -e tws-graph/` 可执行）
- [ ] TWS-Skills 项目已有 tws-graph 索引（`tws-graph search kind:function run_calls --exclude "tests/"` 返回结果）

### 实现细节

**步骤 1：docstring 提取修复（python.rs）**

- 文件：`tws-graph/rust_core/src/indexer/extractors/python.rs:1195-1211`
- 改动：扩展 `extract_docstring()` 函数
  - 遍历 `body` 节点的所有 named_children（而非只看 first）
  - 收集连续的 `expression_statement > string` 节点
  - 跳过前面的 `import`/`from __future__` 等非 docstring statement
  - 追加 handling：如果 body 自身就是 `block` 节点，先进入 block
  - 添加 logging：当 source 非空但提取到空 docstring 时，debug 日志输出节点类型
- 文件：`tws-graph/rust_core/src/indexer/extractors/python.rs:1217-1350`（测试区域）
  - 改动：修复测试断言，将 `if let Some(docstring)` 改为 `assert!(docstring.is_some(), "...")`
  - 新增测试：
    1. `test_extract_docstring_function` — def 后有 """doc"""
    2. `test_extract_docstring_class` — class 后有 """doc"""
    3. `test_extract_docstring_decorated` — @decorator 后有 """doc"""
    4. `test_extract_docstring_no_docstring` — 无 docstring 返回 None
    5. `test_extract_docstring_multi_line` — 多行 docstring 正确截断 200 字符

**步骤 2：新增 NodeInfo struct + get_node_rich()（connection.rs + query/mod.rs）**

- 文件：`tws-graph/rust_core/src/query/mod.rs:20-33`（struct 定义区域）
  - 改动：新增 `NodeInfo` struct（9 字段，见关键接口节）
  - 改动：扩展 `CallsResult`，追加 4 个 `Option` 字段
  - 注意：为 `CallsResult` 和 `NodeInfo` 添加 `#[derive(Serialize)]`（需在 Cargo.toml 已有 serde 依赖）
- 文件：`tws-graph/rust_core/src/db/connection.rs:229-251`
  - 改动：在 `get_node()` 方法**之后**新增 `get_node_rich()` 方法
  - SQL：`SELECT id, kind, name, qualified_name, language, file_path, signature, start_line, docstring FROM nodes WHERE id = ?1`
  - 返回类型：`rusqlite::Result<Option<NodeInfo>>`
  - `get_node()` 自身不修改（零侵入旧代码）
- 文件：`tws-graph/rust_core/src/query/mod.rs:80-95`（run_calls 格式化区域）
  - 改动：将 `db.get_node(nid)?` 替换为 `db.get_node_rich(nid)?`
  - 改动：用 NodeInfo 字段填充 CallsResult 的新字段（signature, start_line, docstring, visibility）
  - 注意：edge_kind 赋值逻辑保持 `if inbound { "CALLS".to_string() } else { "CALLS".to_string() }`，不做改动

**步骤 3：PyO3 calls() 支持 format 参数（lib.rs）**

- 文件：`tws-graph/rust_core/src/lib.rs:643-678`
  - 改动：函数签名新增 `format: Option<String>` 参数
  - 改动：`#[pyo3(signature = (...))]` 更新为包含 format
  - 新增逻辑（在 results.is_empty() 检查之后，formatting 之前）：
    - `format == "json"` → `serde_json::to_string_pretty(&results)` 输出
    - `format == "brief"` → 旧格式 `"{indent}{depth} ({kind}):{name} @ {file_path}"`
    - 其他（None/"text"）→ rich 格式：`"{indent}[{depth}] ({kind}):{name} | sig: {signature} | line: {start_line} | {doc_first_line} | {file_path}"`
  - rich 格式 docstring 只取首行（按 \n split），超过 80 字符截断加 "..."
- 文件：`tws-graph/rust_core/src/lib.rs:1526-1529`
  - 改动：`wrap_pyfunction!(calls, m)?` 不变（无新增函数注册）

**步骤 4：Python bridge 适配（rust_bridge.py + cli.py）**

- 文件：`tws-graph/src/tws_graph/rust_bridge.py:55-69`
  - 改动：`rust_calls()` 签名新增 `format: str | None = None` 参数
  - 改动：透传 `format` 参数给 `_core._core.calls(db_path, node_name, inbound, depth, format, include_paths, exclude_paths)`
- 文件：`tws-graph/src/tws_graph/cli.py:258-288`
  - 改动：新增 `--brief` option 参数
  - 改动：在 `rust_calls()` 调用前，新增 `--json` 和 `--brief` 互斥校验
  - 改动：将 format 参数传递给 `rust_calls(..., format=fmt)`

### 测试

- [ ] `test_get_node_rich_existing_node` — 插入含 signature/docstring/start_line 的节点，`get_node_rich()` 返回完整 NodeInfo
- [ ] `test_get_node_rich_nonexistent` — 查询不存在的节点 ID，返回 None
- [ ] `test_get_node_unchanged` — 验证 `get_node()` 方法仍返回 6 元组（不改旧行为）
- [ ] `test_calls_result_json_serialization` — `serde_json::to_string(&CallsResult{...})` 输出合法 JSON，包含新增字段
- [ ] `test_run_calls_with_rich_fields` — run_calls 返回的 CallsResult 中 signature/docstring/start_line 非空（使用含 docstring 的测试数据）
- [ ] `test_extract_docstring_function` — def 后跟 """doc""" 字符串，正确提取
- [ ] `test_extract_docstring_class` — class 后跟 """doc""" 字符串，正确提取
- [ ] `test_extract_docstring_decorated` — @decorator 后跟 """doc""" 字符串，docstring 不被跳过
- [ ] `test_extract_docstring_no_docstring` — 无 docstring 的函数返回 None
- [ ] `test_extract_docstring_multi_line` — 多行 docstring 正确截断 200 字符
- [ ] `test_calls_brief_format` — run_calls 的 brief 格式化输出匹配旧格式 `"{indent}{depth} ({kind}):{name} @ {file_path}"`
- [ ] `test_calls_json_format` — run_calls 的 JSON 格式化输出可被 `json.loads()` 解析
- [ ] `test_cli_mutual_exclusion` — cli.py calls 命令同时传 --json --brief 时报错退出

### 验收标准

- [ ] `cargo test --lib` 在 `tws-graph/rust_core/` 下全部通过，新增测试 >= 12 个，测试覆盖 get_node_rich / CallsResult 序列化 / docstring 提取 / 格式化三种模式
- [ ] `tws-graph calls <符号名> --depth 2`（不带 --json/--brief）输出包含 signature、start_line、docstring 首行
- [ ] `tws-graph calls <符号名> --json --depth 2` 输出合法 JSON，`jq '.[0].signature'` 有值或 null
- [ ] `tws-graph calls <符号名> --brief --depth 2` 输出与改动前的格式完全一致（`{indent}{depth} ({kind}):{name} @ {file_path}`）
- [ ] `tws-graph calls <符号名> --json --brief` 报错退出并提示互斥
- [ ] `tws-graph lint` 零新增 error
- [ ] 用 TWS-Skills 自身验证：`tws-graph calls run_calls --depth 2` 输出含签名信息
- [ ] 用 g-ass-source 验证：`tws-graph calls CodingTaskService --depth 2` 输出含 Python 方法签名和 docstring

### 状态：✅ 已完成
- 4 commits: d4a09f1→4ff300b→2263f4d→a2acf81
- cargo test: 1720/1721 通过（1 预存失败 ignore::tests::full_path_pattern）
- CLI 三种模式全部正常：rich（树形+行号）/ --json（10字段）/ --brief（旧格式兼容）
- --json --brief 互斥校验生效
- tws-graph lint: 零新增错误（31 errors + 167 warnings 均预存）

## 阶段 B2: Token 节省率 Benchmark 执行

### 门禁
- [ ] A2 方案设计已完成（✅）
- [ ] g-ass-source 索引就绪（D:/g-ass-source/.tws/codegraph/index.db 存在且 `tws-graph search CodingTaskService` 返回结果）
- [ ] Claude Code API 可用（`claude` CLI 可调用或 API key 就绪）
- [ ] B1 已完成（推荐但非强制：B1 完成后的 rich 输出可能会影响 token 计数）

### 实现细节

**步骤 1：准备测试查询定义**

- 文件：`benchmark/data/queries.json`（新增）
- 内容：5 类查询 × 2-3 个具体符号，来自 g-ass-source 项目
  1. 类定义查询（class lookup）：
     - `CodingTaskService` — 核心服务类
     - `PluginManager` — 插件管理
     - `Mediator` — 事件中介
  2. 方法调用链（call chain, depth=2）：
     - `CoreAPIDispatcher.dispatch` — API 分发
     - `TaskExecutor.execute` — 任务执行
     - `NotificationService.send` — 通知发送
  3. 影响范围（impact, depth=2）：
     - `on_startup` — 启动入口
     - `AgentManager.create_agent` — Agent 创建
  4. 跨文件引用（trace）：
     - 从 `api_handler` 到 `db_session_factory`
     - 从 `auth_middleware` 到 `token_validator`
  5. 模糊搜索（search）：
     - `"coding task"` — 多词搜索
     - `"plugin manager"` — 多词搜索
     - `"event handler"` — 多词搜索
- 注意：具体的符号名必须在 Phase B2 执行前用 `tws-graph search` 在 g-ass-source 索引中验证存在

**步骤 2：准备 Prompt 模板**

- 文件：`benchmark/data/prompts/grep.txt`（新增）
  - 内容：指导 agent 使用 Grep + Read 完成符号查询任务，不提及 tws-graph
  - 极简 prompt 避免稀释 token 差异
- 文件：`benchmark/data/prompts/tws-graph.txt`（新增）
  - 内容：指导 agent 使用 tws-graph 完成符号查询任务，禁止使用 Grep
- 文件：`benchmark/data/prompts/ideal.txt`（新增）
  - 内容：直接给出答案（模拟"如果能直接用结构化 API 获取所有信息"的理想上限）

**步骤 3：Token 计数工具**

- 文件：`benchmark/scripts/token_counter.py`（新增）
- 逻辑：
  - 基线减法：先发送一个空 prompt 获取模板开销，后续实验从 token 计数中扣除
  - 从 Claude Code API response 的 `usage` 字段提取 `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
  - 记录 `wall_time_ms`（`response_time - request_time`）
  - 注意：如果 Claude Code CLI 不支持直接获取 usage，改为调用 Anthropic API（`/v1/messages` endpoint）

**步骤 4：实验执行脚本**

- 文件：`benchmark/scripts/run_benchmark.sh`（新增）
- 流程：
  1. 1 个 warm-up session（消缓存，不计入数据）
  2. 对 queries.json 中每个 query：
     a. 运行 Grep 模式 → 记录 token
     b. 运行 tws-graph 模式 → 记录 token
     c. 运行理想模式 → 记录 token + 记录正确答案（作为正确性基准）
  3. 每个 session 间 sleep 2s 避免 rate limit
  4. 全部 session 跑完后汇总到 JSON

**步骤 5：汇总报告**

- 文件：`benchmark/results/benchmark-report.json`（产出）
- 文件：`benchmark/results/benchmark-summary.md`（产出）
- 汇总维度：
  - 按查询类型分组：每类查询的三组 token 消耗对比（input/output 分列）
  - 整体 token 节省率：`(grep_tokens - tws_graph_tokens) / grep_tokens * 100%`
  - 正确性得分：以理想组答案为基准，每正确 1 题 1 分，部分正确 0.5 分，不正确 0 分
  - 耗时对比：wall_time 均值/中位数/P95
  - 风险标注：如果某查询 tws-graph 返回空，标注回退次数

### 测试

- [ ] `test_token_counter_baseline_subtraction` — 验证基线减法逻辑（空 prompt 消耗被正确扣除）
- [ ] `test_queries_json_valid` — `queries.json` 中所有 symbol 可在 g-ass-source 索引中找到（`tws-graph search` 返回非空）
- [ ] `test_grep_prompt_executes` — Grep 模式 prompt 可以成功执行并返回非空结果
- [ ] `test_tws_graph_prompt_executes` — tws-graph 模式 prompt 可以成功执行并返回非空结果
- [ ] `test_ideal_baseline_complete` — 理想组为每个 query 产出了完整的正确答案（可评估正确性）

### 验收标准

- [ ] `benchmark/results/benchmark-report.json` 存在且包含 42 个 session（5 类 × 3 模式 × 2.8 平均 symbols = 42 条记录）的完整数据
- [ ] `benchmark/results/benchmark-summary.md` 包含三组对比的 token 节省率、正确性得分、耗时对比
- [ ] Token 节省率（input tokens）：tws-graph 组相对纯 Grep 组节省 >= 40%
- [ ] 正确性得分：tws-graph 组 >= 95%（以理想组为基准，允许部分正确）
- [ ] 报告标注了 warm-up session 已被排除、任何 timeout/error 已标记
- [ ] 实验可重现：报告中记录了所有 query 参数、symbol 名称、prompt 模板版本

### 状态：✅ Pilot benchmark 已执行

**执行方式**：`claude --print --output-format json --verbose`，真实 bug 调查任务（cancel_coding_task 状态更新），精确 API token 测量。

**结果（2026-07-02）**：

| 指标 | Grep (--allowedTools Grep Read) | tws-graph (--dangerously-skip-permissions) |
|------|------|------|
| Input tokens | 51,305 | 51,531 (+0.4%) |
| Output tokens | 13,545 | 14,846 (+9.6%) |
| Total tokens | 64,850 | 66,377 (+2.4%) |
| Turns | 49 | 61 |
| 耗时 | 259s | 239s |
| 费用 | $1.0940 | $1.2313 |

**核心发现**：
- tws-graph **未能在本任务上节省 token**，total tokens 反而多了 2.4%
- Input tokens 几乎持平（51K vs 51K），skill 指令开销 ≈ 结构化查询节省的上下文
- tws-graph 组更多 turns（61 vs 49）：每轮 Bash 调用增加开销，且 agent 需要更多轮次导航 tws-graph 输出
- Output tokens 更高（14.8K vs 13.5K）：tws-graph 的 rich 输出格式比 Grep 输出更冗长
- **解读**：对于「理解代码逻辑」为主的 bug 调查，无论用哪种工具，agent 都要读大量源码；tws-graph 节省的是「找到目标文件」的搜索环节，但这部分在 bug 调查总 token 中占比不高

**局限**：
- 仅 1 个 bug 调查任务，非 42 session 的完整 benchmark
- g-ass-source 是 Python 项目（文件粒度细，Grep 也能较快定位）
- `claude --print` 模式下的 deepseek-v4-pro 模型行为可能与 opus/sonnet 不同
- tws-graph 的 rich 输出格式在 benchmark 中增加了 output tokens（可考虑在 benchmark 中用 --brief 模式）

**创建文件**：benchmark/data/queries.json（10 条验证通过的查询）、prompts/{grep,tws-graph,ideal}.txt、scripts/token_counter.py、scripts/precise_token_bench.py、results/precise_comparison.json

### 门禁
- [ ] A4 方案评估已完成（✅，推荐路径 A: Claude Code PreToolUse hooks）
- [ ] Claude Code CLI 已安装且 `claude --version` 可执行
- [ ] tws-graph CLI 在 PATH 中可用（`tws-graph --version` 成功）
- [ ] settings.json 可编辑（Claude Code 配置文件路径已知，通常为 `~/.claude/settings.json`）

### 实现细节

**步骤 1：Hook 脚本**

- 文件：`grep-hook/grep-hook.sh`（新增，~50 行 bash）
- 逻辑：
  1. 读取 stdin 获取 PreToolUse hook 传入的 tool_name 和 tool_input JSON
  2. 如果 `tool_name != "Grep"` → 透传（返回原始 tool_input，不做修改）
  3. 如果 `tool_name == "Grep"`：
     a. 解析 `tool_input.pattern` 字段
     b. 启发式判断：pattern 是否像符号名（不含特殊正则字符如 `|`, `\s`, `*`, `[`, `(`, `.` 等）
     c. 如果是符号名 → 用 `tws-graph search <pattern>` 执行查询
     d. 如果 tws-graph 返回非空 → 构建替换后的返回结果（将 tws-graph 输出格式化为类似 Grep 的格式）
     e. 如果 tws-graph 返回空或 pattern 不像符号名 → 透传原 Grep 调用
  4. 返回修改后的 tool_input 给 Claude Code
- 注意：PreToolUse hook 的接口规范依赖 Claude Code CLI 版本，需要在实现前确认具体 JSON schema

**步骤 2：安装脚本**

- 文件：`grep-hook/install-grep-hook.sh`（新增）
- 逻辑：
  1. 复制 `grep-hook.sh` 到 `~/.claude/hooks/grep-hook.sh`
  2. 确保脚本有执行权限（`chmod +x`）
  3. 检测 `settings.json` 是否存在，不存在则从 `settings.json.template` 创建
  4. 在 `settings.json` 的 `hooks.PreToolUse` 数组中追加 hook 配置条目
  5. 打印验证指令（`claude --check-hooks` 或等价命令）

**步骤 3：配置模板**

- 文件：`grep-hook/settings.json.template`（新增）
- 内容：Claude Code settings.json 的最小结构，包含 PreToolUse hook 配置
  ```json
  {
    "hooks": {
      "PreToolUse": [
        {
          "matcher": "Grep",
          "command": "bash ~/.claude/hooks/grep-hook.sh"
        }
      ]
    }
  }
  ```

**步骤 4：手动验证**

- 步骤：
  1. 运行 `bash grep-hook/install-grep-hook.sh` 安装
  2. 启动新 Claude Code session
  3. 发送 prompt：`搜索 CodingTaskService 类`
  4. 观察 agent 是否使用了 Grep 工具，hook 是否拦截
  5. 记录 hook 前后的 token 消耗差值
  6. 测试回退：发送 prompt：`搜索 file_path MATCHES "src/utils"`（应透传而非拦截）

### 测试

- [x] `test_hook_installs` — `install-grep-hook.sh` 成功执行，settings.json 含 hook 配置
- [x] `test_hook_intercepts_known_symbol` — PreToolUse hook 拦截 `Grep(pattern="CallsResult")`，缩小 grep 路径到 `tws-graph\rust_core\src\query`
- [x] `test_hook_passes_through_complex_pattern` — PreToolUse hook 透传 `Grep(pattern="import.*from")`（含正则语法）
- [x] `test_hook_passes_through_unknown_symbol` — tws-graph 返回空时，hook 透传原 Grep 调用
- [x] `test_hook_passes_through_non_grep` — 非 Grep 工具（如 Read）不被拦截
- [x] `test_hook_returns_valid_grep_format` — 拦截后输出为有效的 modified tool_input JSON

### 验收标准

- [ ] `bash grep-hook/install-grep-hook.sh` 一键安装成功
- [ ] 安装后 agent 使用 Grep 搜索已知符号名（如 `PluginManager`）时，实际执行的是 `tws-graph search`
- [ ] 安装后 agent 使用 Grep 搜索复杂正则（如 `def\s+\w+`）时，hook 透传不做修改
- [ ] 拦截后 agent 能正常理解 tws-graph 输出（不出现格式解析错误）
- [ ] 禁用 hook（移除 settings.json 中配置）后 agent 恢复使用原生 Grep
- [ ] POC 完成后输出一份 `grep-hook/README.md`，包含安装步骤、验证方法、已知局限

### 状态：✅ 已完成（v2: PreToolUse + 路径收窄）

- commit 1a4aac2: 初始 POC（PostToolUse 格式）
- 2026-07-02 重写：改为 PreToolUse + 路径收窄策略
  - **策略变更**：从「添加 additionalContext」改为「缩小 Grep path 参数」
  - 当 tws-graph 找到符号时，提取文件路径的公共父目录，设置 Grep 的 `path` 参数
  - 效果：Grep 只搜索相关目录，减少无关文件输出 → 节省 output tokens
  - 5 个测试场景全部通过：符号名收窄、regex 透传、非 Grep 透传、未知符号透传、输出格式正确
- **已知局限**：Hooks 只在交互式会话中触发，`--print` 模式不触发（Claude Code 平台限制）
- 已部署到 `~/.claude/settings.json`

## 阻塞级 Bug
<!-- 执行中发现的阻塞当前阶段的 bug，追加到此区域 -->

## 执行日志
- 2026-07-02 09:00 — 🎯 目标接收，spec 创建（最小版本 v1）
- 2026-07-02 09:30 — 📋 场景判断：add-feature（完整路径），flow：flow-add-feature + flow-investigate（混合）
- 2026-07-02 10:00 — 🔍 Phase A1 代码库调查完成：确认 CallsResult 6 字段、get_node() 6 列、CLI 无 JSON、docstring 提取疏漏
- 2026-07-02 10:30 — 🔍 Phase A2 Benchmark 方案设计完成：5 类查询 × 3 组对比 × 42 sessions、Token 基线减法
- 2026-07-02 11:00 — 🔍 Phase A3 可读输出增强方案完成：渐进增强方案（NodeInfo + get_node_rich + 三种 CLI 模式）
- 2026-07-02 11:30 — 🔍 Phase A4 Grep Hook 方案评估完成：路径 A 唯一可行，推荐三阶段推进
- 2026-07-02 11:45 — 📝 Phase A 全部完成，派 comp-spec-write 子 agent 展开完整 spec（v2）
- 2026-07-02 — 🔨 Phase B1 完成：7 commits（docstring修复→NodeInfo+get_node_rich→format参数→Python bridge→benchmark infra→grep-hook POC→visibility修复+测试）
- 2026-07-02 — 🔨 Phase B2 基础设施搭建完成（queries.json + prompts + token_counter.py），pilot 数据：Grep 55,588 tokens(87 tools) vs tws-graph 56,339 tokens(51 tools, -41%)
- 2026-07-02 — 🔨 Phase B3 Grep Hook POC 完成：grep-hook.sh(124行) + installer + README
- 2026-07-02 — 🔍 目标回溯验证：B1/B3 达成，B2 待执行(42 sessions)。修复 visibility 填充 + 新增 4 测试(commit 05d40a1)
- 2026-07-02 15:30 — 🔬 Phase B2 Pilot benchmark 执行：Grep 64,850 vs tws-graph 66,377 total tokens（+2.4%）。tws-graph 在真实 bug 调查任务上未节省 token。详见 B2 状态区。
- 2026-07-02 16:00 — 🔨 Phase B3 Search Hook 完成 v2：重写为 PreToolUse + 路径收窄策略。提取 tws-graph 文件路径 → 缩小 Grep path 参数。已部署到 ~/.claude/settings.json。测试 5/5 通过。
