# tws-graph 用户手册 v6.0.0

> 面向人类的完整 CLI + MCP 工具参考。Agent 使用指南见 `found-tws-graph-usage`。

---

## 概述

tws-graph 是一个基于 tree-sitter 的代码符号关系图引擎。它预建 SQLite 索引，让你通过 CLI 或 MCP 工具查询代码库的调用关系、影响范围、架构结构。

- 支持 30+ 编程语言和配置格式
- 纯本地运行，零网络依赖
- 提供 17 个 CLI 命令 + 21 个 MCP 工具

---

## 安装与设置

```bash
pip install -e tws-graph/
tws-graph --version

# 安装 git hooks（自动增量索引）
tws-graph hooks install

# 首次索引
tws-graph index
```

---

## CLI 命令参考

### 索引与同步

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph index [PATH]` | 全量/增量索引 | `tws-graph index` / `tws-graph index --force` |
| `tws-graph sync` | 快速增量同步（仅检查变更文件） | `tws-graph sync` |
| `tws-graph hooks install` | 安装 git hooks 自动同步 | `tws-graph hooks install` |
| `tws-graph watch` | 文件变更监听，自动增量同步 | `tws-graph watch --path . --interval 2.0` |

### 符号查询（Agent 也会用）

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph search <query>` | 全文搜索符号 | `tws-graph search kind:function auth` |
| `tws-graph calls <node>` | 查调用目标 | `tws-graph calls my_func` |
| `tws-graph calls <node> --inbound` | 查调用者 | `tws-graph calls my_func --inbound` |
| `tws-graph impact <node>` | 查变更影响范围 | `tws-graph impact MyClass --depth 2` |
| `tws-graph trace <src> <tgt>` | 查调用路径 | `tws-graph trace main auth_handler` |
| `tws-graph unresolved` | 列出未解析引用 | `tws-graph unresolved` |

### 快照与差异

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph snapshot <name>` | 创建命名快照 | `tws-graph snapshot before-refactor` |
| `tws-graph diff <a> <b>` | 对比两个快照 | `tws-graph diff before after` |
| `tws-graph diff <a> <b> --json` | JSON 格式差异输出 | `tws-graph diff before after --json` |

### 图查询与分析

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph query <gql>` | GQL 图查询语言 | `tws-graph query "FIND function WHERE name MATCHES 'auth'"` |
| `tws-graph cycles` | 检测循环依赖 | `tws-graph cycles` |
| `tws-graph layers` | 检测架构层次违规 | `tws-graph layers` |
| `tws-graph metrics` | 计算模块内聚/耦合度量 | `tws-graph metrics` |
| `tws-graph taint` | 安全污点分析 | `tws-graph taint` |
| `tws-graph predict-impact` | 预测修改影响 | `tws-graph predict-impact my_func` |
| `tws-graph health` | 代码健康评分 | `tws-graph health --worst 10` |
| `tws-graph analyze` | 图分析（死代码/入口点等） | `tws-graph analyze --run dead-code` |

### 导出

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph export dot` | 导出 Graphviz DOT | `tws-graph export dot --from main --depth 2` |
| `tws-graph export mermaid` | 导出 Mermaid 图 | `tws-graph export mermaid --from MyClass` |
| `tws-graph export json` | 导出 JSON | `tws-graph export json --kind calls` |

### 工具

| 命令 | 用途 | 示例 |
|------|------|------|
| `tws-graph serve` | 启动 MCP 服务器 | `tws-graph serve --root .` |
| `tws-graph lint` | 校验 skill 文件 | `tws-graph lint` |
| `tws-graph lsp setup` | 检测 LSP server 可用性 | `tws-graph lsp setup` |

---

## MCP 工具参考

### 搜索类
- `search_symbols` — FTS5 全文搜索符号
- `semantic_search` — 11-signal 融合语义搜索

### 代码类
- `get_code` — 获取符号源码和元数据
- `get_dependencies` — 获取调用者/被调用者
- `get_impact` — 计算影响半径和风险等级
- `trace_path` — 查找调用路径

### 分析类
- `get_complexity` — 代码复杂度分析
- `find_dead_code` — 死代码检测
- `get_test_coverage` — 测试覆盖分析
- `get_entry_points` — 识别项目入口点

### 高级分析
- `find_clones` — MinHash + LSH 代码克隆检测
- `get_git_diff_impact` — Git diff 影响分析
- `get_config_links` — 配置-代码关联发现

### 图查询
- `query_cypher` — Cypher/GQL 图查询
- `get_edge_distribution` — 边类型分布统计
- `detect_cross_service` — 跨服务通信检测

### 开发辅助
- `review_changes` — 代码审查辅助
- `safe_refactor` — 重构安全检查
- `api_compat_check` — API 兼容性检查
- `find_pattern` — AST 结构模式搜索
- `security_scan` — 安全漏洞检测

### MCP 资源
- `tws://stats` — 代码图统计
- `tws://languages` — 语言分布
- `tws://health` — 健康状态

---

## 典型工作流

### 理解新项目
```bash
tws-graph index
tws-graph search kind:class
tws-graph metrics
tws-graph analyze --run entry-point
```

### 修改代码前
```bash
tws-graph snapshot before-change
tws-graph impact <要改的符号> --depth 2
```

### 修改代码后
```bash
tws-graph index  # 或 sync
tws-graph diff before-change after-change
```

### 架构审查
```bash
tws-graph cycles
tws-graph layers
tws-graph health --worst 20
tws-graph analyze --run dead-code
```

---

## Kind 类型参考

编程语言通用: `class`, `function`, `method`, `module`, `interface`, `struct`, `enum`, `variable`, `constant`, `type_alias`, `namespace`, `file`, `field`

详见 `found-tws-graph-usage` 中的 kind 注册表。

## EdgeKind 参考

结构关系: `CALLS`, `IMPORTS`, `REFERENCES`, `EXTENDS`, `IMPLEMENTS`, `OVERRIDES`, `INSTANTIATES`, `DECORATES`, `TYPE_REF`, `CONTAINS`

数据流: `DATA_FLOWS`, `READS`, `WRITES`, `THROWS`

环境/事件: `ENV_ACCESSES`, `EMITS`, `LISTENS_ON`

跨服务: `HTTP_CALLS`, `GRPC_SERVICE`, `GRPC_CLIENT`, `GRPC_SERVER`

分析: `SIMILAR_TO`, `TEST_EDGE`, `CONFIG_LINK`

---

## 未来规划（前端 UI）

此手册为人类用户而写。后续将开发 Web 前端 UI，直接读取 `.tws/codegraph/index.db` 提供：
- 交互式代码关系图浏览
- 项目架构可视化
- 代码热点和健康趋势
- 一键导出报告

前端开发时以此手册中的命令为后端 API 参考。
