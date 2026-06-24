# tws-graph v3.0.0 Specification

> 2026-06-23 | 全面赶超 codebase-memory-mcp
> 设计原则：完全自研、离线可用、深度解耦、TDD 驱动

---

## 一、背景与目标

### 1.1 现状

tws-graph v0.2.0 已完成 P0-P13 全部 phase 的实现（3377 tests, 110 test files），具备以下能力：

| 能力 | 状态 |
|------|------|
| 26 语言 tree-sitter 提取（含节点+边） | 完成 |
| 21 种 EdgeKind 边类型 | 完成 |
| LSP 类型解析（Python/TS/Java/C/C++/C#/PHP/Ruby） | 完成 |
| 数据流分析（VariableUsage + DataFlow + DataFlowPass） | 完成 |
| 语义搜索（11-signal 排序） | 完成 |
| 分析套件（DeadCode, EntryPoint, Complexity, TestEdges, ConfigLinks, GitDiff） | 完成 |
| 跨服务检测（Route, Channel, gRPC） | 完成 |
| IoC/Infra 索引（YAML, K8s, HCL, JSON, Kustomize） | 完成 |
| Cypher 查询引擎 | 完成 |
| 代码克隆检测（MinHash LSH） | 完成 |
| InvalidationTracker + 增量更新 | 完成 |
| SQLite 性能调优 + 基准测试套件 | 完成 |
| Git hooks 自动同步 | 完成 |
| 15 个 CLI 命令 | 完成 |

### 1.2 与 CBM 的关键差距

| 维度 | tws-graph v0.2.0 | CBM | 差距 |
|------|-----------------|-----|------|
| MCP 服务器 | **无** | 15 个 MCP 工具 | 致命 |
| 索引速度 | 52.2s (串行) | 3.18s (20 workers) | 16x |
| 语言数 | 26 | 158 | 6x |
| MCP 工具数 | 0 | 15 | ∞ |

### 1.3 v3.0.0 目标

**全面赶超 CBM**，补齐三个致命差距，成为最强的离线代码图引擎。

---

## 二、架构总览

```
                    ┌──────────────────────────────┐
                    │       MCP Server (P15)        │
                    │   JSON-RPC 2.0 / stdio       │
                    │   15+ tools / 3 resources     │
                    └──────────┬───────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐  ┌──────▼──────┐  ┌─────▼─────┐
     │ Search/Query  │  │  Analysis   │  │  Impact   │
     │ FTS5/Cypher   │  │  Dead/Comp  │  │ Trace/Call│
     └───────┬───────┘  └──────┬──────┘  └─────┬─────┘
             │                 │                │
     ┌───────▼─────────────────▼────────────────▼──────┐
     │              Store Layer (SQLite)                │
     │   SqliteStore + MemoryStore + QueryBuilder       │
     └──────────────────────┬──────────────────────────┘
                            │
     ┌──────────────────────▼──────────────────────────┐
     │         Parallel Pipeline (P14)                  │
     │   20 workers × ProcessPoolExecutor              │
     │   File → Parse → Extract → Dedup → Insert       │
     └──────────────────────┬──────────────────────────┘
                            │
     ┌──────────────────────▼──────────────────────────┐
     │          Extractors (26 languages)               │
     │   Tree-sitter + LSP adapters + dataflow         │
     └─────────────────────────────────────────────────┘
```

---

## 三、P14: 并行提取管线（性能）

### 3.1 目标

索引速度从 52.2s 降至 ≤ 4s（目标 13x 提升），在 4 核机器上不超过 CBM 的 1.5x。

### 3.2 设计

```
File List
    │
    ▼
┌─────────────────────────────────┐
│  Stat Pre-filter (main process) │  ← mtime/size 跳过未改文件
│  Content Hash Filter            │  ← SHA256 跳过相同内容
└─────────────┬───────────────────┘
              │ files_to_reindex
              ▼
┌─────────────────────────────────┐
│  Chunk files → batches of 50    │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  ProcessPoolExecutor(N=20)      │
│  ┌─────┐ ┌─────┐ ... ┌─────┐   │
│  │ W1  │ │ W2  │     │ W20 │   │
│  │parse│ │parse│     │parse│   │
│  │extr.│ │extr.│     │extr.│   │
│  └──┬──┘ └──┬──┘     └──┬──┘   │
│     │       │           │       │
│     └───────┴─────┬─────┘       │
│                   ▼             │
│         ResultCollector         │
│     (dedup + merge nodes/edges) │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│  Batch INSERT (main process)    │  ← 单线程写 SQLite
│  Cross-file resolve             │
│  FTS rebuild                    │
└─────────────────────────────────┘
```

### 3.3 关键技术决策

1. **ProcessPoolExecutor 而非 ThreadPoolExecutor** — Python GIL 使线程对 CPU 密集任务无用
2. **pickle 序列化 worker 结果** — 标准库，零外部依赖
3. **批量写入** — worker 结果收集后在主进程批量 INSERT，避免 SQLite 并发写锁
4. **chunk size = 50** — 平衡进程创建开销与负载均衡
5. **worker count = min(20, cpu_count)** — 不超过 CBM 的并发度上限

### 3.4 测试门禁

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| 正确性 | 串行/并行结果一致（节点数、边数、文件数） | `test_parallel_correctness` |
| 速度 | ≤ 4s on 200-file project | `test_benchmark_parallel_index` |
| 错误隔离 | 单文件解析失败不影响其他文件 | `test_parallel_error_isolation` |
| 空项目 | 0 文件不崩溃 | `test_parallel_empty_project` |
| 单文件 | 1 文件正常完成 | `test_parallel_single_file` |
| Worker 数上限 | workers ≤ 20 | `test_parallel_max_workers` |
| 增量模式 | stat pre-filter 仍然生效 | `test_parallel_incremental` |

---

## 四、P15: MCP 服务器（离线原生）

### 4.1 目标

实现完整的 MCP (Model Context Protocol) 服务器，纯 Python 自研，零外部依赖，完全离线可用。

### 4.2 协议实现

MCP 基于 JSON-RPC 2.0，在 stdio 上运行：

```
┌───────────────────────────────────┐
│         MCP Client (Claude)       │
│     stdin/stdout JSON-RPC 2.0    │
└───────────────┬───────────────────┘
                │
┌───────────────▼───────────────────┐
│       MCP Server (tws-graph)      │
│  ┌─────────────────────────────┐  │
│  │  Transport (stdio)          │  │
│  ├─────────────────────────────┤  │
│  │  Router (method dispatch)   │  │
│  ├─────────────────────────────┤  │
│  │  Tool Registry              │  │
│  │  Resource Registry          │  │
│  ├─────────────────────────────┤  │
│  │  Tool Handlers (15+ tools)  │  │
│  │  Resource Handlers (3 res)  │  │
│  └─────────────────────────────┘  │
└───────────────────────────────────┘
```

### 4.3 MCP 工具映射

以下 15 个工具覆盖 CBM 的全部能力，并利用 tws-graph 的 Semantic Search + Cypher + Analysis Suite 超越其能力范围：

| # | 工具名 | 对应 CLI | 描述 |
|---|--------|---------|------|
| 1 | `search_symbols` | `tws-graph search` | FTS5 全文搜索符号（支持 kind:, lang:, path: qualifier） |
| 2 | `semantic_search` | `tws-graph search --semantic` | 11-signal 语义排序搜索 |
| 3 | `get_code` | `tws-graph search` + Read | 获取符号源码片段（文件:行号范围） |
| 4 | `get_dependencies` | `tws-graph calls --inbound` | 查调用者/被调用者依赖关系 |
| 5 | `get_impact` | `tws-graph impact` | 变更影响范围分析 |
| 6 | `trace_path` | `tws-graph trace` | 两个符号间调用路径追踪 |
| 7 | `get_complexity` | `tws-graph analyze --run complexity` | 圈复杂度 + 认知复杂度 + Halstead |
| 8 | `find_dead_code` | `tws-graph analyze --run dead-code` | 死代码检测（degree=0 非入口） |
| 9 | `get_test_coverage` | `tws-graph analyze --run test-edges` | test↔source 关联矩阵 |
| 10 | `get_entry_points` | `tws-graph analyze --run entry-point` | 入口点检测 |
| 11 | `find_clones` | `tws-graph analyze --algorithm clone` | MinHash LSH 代码克隆检测 |
| 12 | `get_git_diff_impact` | `tws-graph analyze --run git-diff` | Git diff 影响分析 |
| 13 | `get_config_links` | `tws-graph analyze --run config-links` | 代码→配置文件关联 |
| 14 | `query_cypher` | `tws-graph query` | Cypher 图查询（超越 CBM） |
| 15 | `detect_cross_service` | `tws-graph analyze --run all` | 跨服务 HTTP/gRPC 检测 |

### 4.4 MCP 资源

| 资源 URI | 描述 |
|----------|------|
| `tws://stats` | 项目索引统计（节点数、边数、语言分布） |
| `tws://languages` | 支持的语言列表及索引状态 |
| `tws://health` | 服务健康状态（索引就绪、DB 连接数） |

### 4.5 启动方式

```bash
# 作为 MCP 服务器启动（stdio 传输）
tws-graph serve

# 指定项目目录
tws-graph serve --root /path/to/project

# 指定数据库路径
tws-graph serve --db /path/to/index.db
```

### 4.6 MCP 配置（Claude Code settings.json）

```json
{
  "mcpServers": {
    "tws-graph": {
      "command": "tws-graph",
      "args": ["serve", "--root", "${workspaceFolder}"],
      "env": {}
    }
  }
}
```

### 4.7 测试门禁

**单元测试：**

| 门禁 | 标准 | 测试文件 |
|------|------|---------|
| JSON-RPC parse | 正确解析 request/notification/response | `tests/mcp/test_protocol.py` |
| Tool 注册 | 15 个工具全注册，无重复 | `tests/mcp/test_registry.py` |
| Tool 调用 | 每个工具至少 1 个成功+失败场景 | `tests/mcp/test_tools.py` |
| Resource 读取 | 3 个资源均可读取 | `tests/mcp/test_resources.py` |
| initialize handshake | 完整 MCP 握手流程 | `tests/mcp/test_lifecycle.py` |
| 错误处理 | 无效 method → -32601, 无效 params → -32602 | `tests/mcp/test_errors.py` |
| 并发请求 | 顺序处理，不交错 | `tests/mcp/test_concurrency.py` |

**集成测试场景：**

| 场景 | 描述 |
|------|------|
| 端到端搜索流程 | initialize → tools/list → tools/call search_symbols → 验证 JSON 响应格式 |
| 端到端分析流程 | tools/call get_impact → 验证影响范围 JSON → tools/call get_complexity → 验证复杂度值 |
| 资源读取流程 | resources/list → resources/read tws://stats → 验证统计数据 |
| 错误恢复 | 发送格式错误的 JSON → 验证返回 PARSE_ERROR → 后续正常请求不受影响 |
| 大结果集 | 搜索返回 500+ 结果时不分页，完整返回 |

---

## 五、P16: 版本规范化与文档同步

### 5.1 版本号

| 位置 | 当前值 | 新值 |
|------|--------|------|
| `pyproject.toml` | `0.2.0` | `3.0.0` |
| `src/tws_graph/__init__.py` | `0.2.0` | `3.0.0` |

**理由**：P5-P15 累计新增 15+ 功能模块，版本号应反映功能成熟度。3.0.0 表示完整产品。

### 5.2 Skill 文档更新

| Skill | 更新内容 |
|-------|---------|
| `found-tws-graph-usage` | 全部 CLI 命令、21 种 EdgeKind、semantic search qualifier、MCP 工具参考 |
| `tws-graph-init` | 更新完成标准、添加 MCP 配置步骤 |
| `using-tws` | 2a 环境检查中添加 MCP 可用性提示 |

### 5.3 测试门禁

| 门禁 | 标准 |
|------|------|
| 版本一致性 | `tws-graph --version` 输出 `3.0.0`，与 `pyproject.toml` 一致 |
| `tws-graph lint` | 0 errors, 0 warnings |
| Skill 交叉引用 | 所有 skill 文件中的命令名在 CLI --help 中存在 |

---

## 六、集成测试场景（全量）

### 6.1 性能回归门禁

```
场景: 200 文件项目全量索引
  Given: TWS-Skills 项目（~200 源文件）
  When: tws-graph index --force
  Then: 耗时 ≤ 4s
    And: 节点数与串行模式一致
    And: 边数与串行模式一致
```

### 6.2 MCP + 图查询端到端

```
场景: 通过 MCP 完成一次完整的影响分析
  Given: tws-graph serve 已启动
  When: 发送 initialize request → 收到 capabilities
    And: 发送 tools/call search_symbols {query: "kind:class Skill"}
    And: 从结果中选择一个符号 ID
    And: 发送 tools/call get_impact {symbol_id: "..."}
  Then: 收到完整的影响范围 JSON
    And: JSON 包含 depth 0/1/2 的影响节点
```

### 6.3 MCP + 语义搜索 + 克隆检测端到端

```
场景: 语义搜索 + 克隆检测组合查询
  Given: tws-graph serve 已启动
  When: tools/call semantic_search {query: "user authentication handler"}
    And: tools/call find_clones {threshold: 0.7}
  Then: 两个查询结果可在 JSON 层面关联
    And: 语义搜索结果包含相关性分数
```

### 6.4 全量回归

```
场景: 全部 3377 现有测试通过
  Given: 所有新模块已实现
  When: pytest --tb=short
  Then: ALL PASSED, 0 FAILED
```

### 6.5 降级场景

```
场景: LSP 不可用时的优雅降级
  Given: pyright 未安装
  When: tws-graph index
  Then: 索引正常完成（tree-sitter 模式）
    And: LSP 相关警告输出到 stderr
    And: 索引结果中包含 "lsp_unavailable" 标记

场景: MCP 启动时索引不存在
  Given: .tws/codegraph/index.db 不存在
  When: tws-graph serve
  Then: 返回错误 "index not found, run tws-graph index first"
    And: 进程正常退出（exit code 1）
```

---

## 七、实现顺序

```
P14 (并行提取) ─┐
                ├── 并行实施
P15 (MCP 服务器) ┘
                │
                ▼
P16 (版本/文档) ─┐
                 ├── 顺序实施
集成测试        ┘
```

P14 和 P15 互不依赖，可并行实施。
P14 完成后 P15 可利用其快速索引结果。
P16 在所有功能稳定后执行。

---

## 八、完成定义 (Definition of Done)

- [ ] `spec.md` 所有门禁通过
- [ ] P14 并行提取: 7/7 测试通过 + benchmark 达标 (≤ 4s)
- [ ] P15 MCP 服务器: 7/7 单元测试通过 + 5/5 集成场景通过
- [ ] P16 版本 3.0.0: 3/3 门禁通过
- [ ] 全量回归: 全部现有测试通过 (≥ 3377 passed)
- [ ] 基准测试回归: 7 个 benchmark 无 REGRESSION
- [ ] `tws-graph lint`: 0 errors, 0 warnings
- [ ] 设计书同步: 所有修改有对应的设计文档
