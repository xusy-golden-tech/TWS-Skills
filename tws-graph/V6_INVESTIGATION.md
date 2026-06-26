# tws-graph v6.0.0 调研报告

> 2026-06-26 | 基于真实代码和数据，非推测

---

## 1. CBM 基准 (codebase-memory-mcp)

### 架构
- **语言**: 纯 C，零依赖，单静态二进制
- **索引引擎**: RAM-first pipeline (LZ4 压缩 + 内存 SQLite + 末尾 dump)
- **解析器**: 158 个 vendored tree-sitter 语法（编译进二进制）
- **语义增强**: Hybrid LSP（9 语言：Python, TS/JS, PHP, C#, Go, C, C++, Java, Kotlin, Rust）
- **测试**: 5604 个

### 性能 (M3 Pro)
| 操作 | 耗时 |
|------|------|
| Linux kernel 全量索引 (28M LOC, 75K files) | 3 min → 4.81M nodes, 7.72M edges |
| Linux kernel 快速索引 | 1m 12s |
| Django 全量索引 | ~6s |
| Cypher 查询 | <1ms |
| 死代码检测 | ~150ms |

### MCP 工具: 14 个
- get_architecture, semantic_query, search_graph, search_code
- trace_path, get_dependencies, detect_changes, manage_adr
- find_dead_code, query_cypher, find_similar_code, find_best_export
- get_cross_service_links, graph_export

### 独有能力
- 团队共享图 artifact (.codebase-memory/graph.db.zst)
- 自动更新 (codebase-memory-mcp update)
- 3D 图可视化 UI (localhost:9749)
- 11 agent 自动检测安装
- npm/PyPI/Homebrew/Scoop/Winget/Chocolatey/AUR 多渠道分发

---

## 2. tws-graph v5.7.0 现状

### 架构
- **语言**: Python，依赖 tree-sitter + tree-sitter-language-pack + typer
- **索引引擎**: 文件 → parse → extract (Python AST walk) → SQLite
- **解析器**: 34 extractor 文件 (~30 语言)，通过 tree-sitter-language-pack 动态加载
- **测试**: 4043 个（14% 覆盖率）

### Extractor 深度对比

| Extractor | 行数 | 边产出次数 | 产出边类型 |
|-----------|------|-----------|-----------|
| Python | 605 | 18 | calls, imports, extends, implements, decorates, type_ref, references, reads, writes, instantiates, throws, http_calls, grpc_*, emits, listens_on |
| Go | 237 | 5 | calls, contains |
| Rust | 260 | 4 | calls, contains (推测) |
| C++ | 404 | 14 | 中等深度 |
| C# | 331 | 10 | 中等深度 |
| PHP | 376 | 12 | 中等深度 |

**结论**: Go 和 Rust extractor 深度严重不足（仅 calls + contains）。C++/C#/PHP 中等但未达到 Python 级别。

### CLI 命令: 17 个
index, sync, hooks, watch, search, calls, impact, trace, snapshot, diff, unresolved, lint, query, cycles, layers, metrics, analyze, predict-impact, health, taint, export (dot/mermaid/json), serve, lsp

### MCP 工具: 21 个
- 搜索类 (2): search_symbols, semantic_search
- 代码类 (4): get_code, get_dependencies, get_impact, trace_path
- 分析类 (4): get_complexity, find_dead_code, get_test_coverage, get_entry_points
- 高级类 (3): find_clones, get_git_diff_impact, get_config_links
- 查询类 (3): query_cypher, get_edge_distribution, detect_cross_service
- 开发辅助类 (5): review_changes, safe_refactor, api_compat_check, find_pattern, security_scan

### watch 命令
已存在，使用 PollingObserver / WatchdogObserver，支持防抖、忽略规则。

---

## 3. 关键差距分析

### 3.1 性能 — 最大差距
- CBM: 纯 C，Linux kernel 28M LOC / 3 min
- tws-graph: Python，g-ass-source 项目 289s（规模未知）
- 语言级差距：Python vs C 通常 10-100x
- **Python 内优化空间有限**：v5.2.0-v5.7.0 四轮优化已耗尽低挂果实
- **P50 目标 289s→60s 不切实际**：即使达到，仍远超 CBM

### 3.2 语言覆盖 — 中等差距
- CBM: 158 语言（所有 tree-sitter 语法）
- tws-graph: ~30 语言
- 但 tws-graph 覆盖了主要编程语言 + 配置格式，差距主要在长尾

### 3.3 Extractor 质量 — 需要升级
- Python extractor 深度可对标 CBM
- Go/Rust extractor 严重不足
- C++/C#/PHP 中等深度，有提升空间

### 3.4 MCP 工具 — 数量多但需整理
- tws-graph: 21 tools > CBM: 14 tools
- 但 found-tws-graph-usage 已膨胀
- Agent 实际不需要全部 21 个工具

### 3.5 实时守护 — 已有基础
- watch 命令已实现，但稳定性未知（覆盖率 0%）

### 3.6 多仓库联邦 — CBM 已有
- CBM 有 CROSS_* edges + 多仓库架构

---

## 4. 修订建议

### 对 v6.0.0 draft 的修正

1. **P50 性能**: 
   - 原始目标: 289s→60s (5x)
   - 修正: Python 内优化目标 2x（289s→~140s），作为 Python 性能天花板验证
   - 如果 2x 达不到 → 输出「Python 性能瓶颈报告」，推荐 Rust/Go 重写方案
   - 重写方案不应在 v6.0.0 执行，而应作为 v7.0.0 的战略决策

2. **P51 Extractor 深度**:
   - 保留，但以 CBM 为基准（测试用真实 GitHub 项目对比产出）
   - 优先 Go 和 Rust（差距最大），然后 C++/C#/PHP

3. **P52 新语言**:
   - 降低优先级。tree-sitter-swift/dart/groovy 需要先确认语法可用性
   - 考虑：这 6 个语言是否比深化现有语言更重要？

4. **P53 实时守护**:
   - 当前已有基础实现，重点是稳定性+测试（当前覆盖率 0%）

5. **P54 多仓库联邦**:
   - 保留但重新设计。CBM 已有此能力

6. **P55 MCP 4.0**:
   - **关键修正**：不新增工具，而是：
     a. 将 21 个工具分为 agent 用（≤12 个）和人用（其余）
     b. 编写 `docs/tws-graph-manual.md`（人类说明书）
     c. 精简 `found-tws-graph-usage` 只保留 agent 工具

### 推荐执行优先级
```
P51 (Extractor 深度) — 最先，可独立验证，效果可量化
  ├── P52 (新语言) — 在 P51 完成后，有明确模式可复用
  ├── P55 (工具分离+文档) — 解决膨胀问题，可并行
  ├── P50 (性能) — 设定合理预期，Python 内 2x 为天花板验证
  ├── P53 (watch 加固) — 补测试，提升稳定性
  └── P54 (多仓库) — 最后，或延迟到 v7.0
```
