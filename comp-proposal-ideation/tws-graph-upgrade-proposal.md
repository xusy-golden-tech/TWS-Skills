# tws-graph 升级方案构思报告

---

## 一、探索的思路

本任务已明确方向偏好：**完全自研、极致解耦、极致可扩展**。按照 `comp-proposal-ideation` 方法论，我们先确认探索的思路：

- **唯一方向**：完全自研。核心逻辑（Cypher 引擎、LSP 类型解析、图算法、社区发现、克隆检测）全部自己实现，不依赖第三方开源项目。依赖只限于 Python 标准库 + tree-sitter（解析层） + SQLite（内置） + watchdog/NetworkX（可选后端，通过接口解耦）。
- **不走其他方向**：用户已明确排除"依赖第三方 Cypher parser""依赖 pygls""依赖克隆检测库"等路径。不重复对比。

以下深入设计这个方向的完整架构、模块契约、解耦策略和扩展性设计。

---

## 二、顶层架构设计

### 2.1 模块划分总图

```
┌──────────────────────────────────────────────────────────────────────┐
│                              CLI (薄层)                               │
│         只做参数解析和模块装配，不包含任何业务逻辑                           │
└──────┬───────┬───────┬───────┬───────┬───────┬───────┬──────────────┘
       │       │       │       │       │       │       │
       ▼       ▼       ▼       ▼       ▼       ▼       ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ Cypher   │ │ Pipeline │ │ Graph    │ │ LSP      │ │ File     │ │ Store    │
│ Engine   │ │ Engine   │ │Algorithms│ │Resolver  │ │Watcher   │ │Interface │
│          │ │          │ │          │ │          │ │          │ │(抽象层)   │
│ 查询引擎  │ │ 索引管线  │ │ 图算法   │ │ 类型解析  │ │ 文件监听  │ │          │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │            │            │            │
     │            │            │            │            │            │
     ▼            ▼            ▼            ▼            ▼            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        Store Implementations                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │ SqliteStore  │  │ MemoryStore  │  │ DuckDBStore  │  (可扩展)       │
│  │ (磁盘持久化)  │  │ (内存图)     │  │ (OLAP 分析)  │                │
│  └──────────────┘  └──────────────┘  └──────────────┘                │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        基础层 (保留现有实现)                            │
│  scanner.py  │  parser.py   │  base.py (BaseExtractor + ctx)         │
│  registry.py │  connection.py  │  schema.sql  │  hooks.py            │
│  diff.py     │  skill_linter.py                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责一句话描述

| 模块 | 职责 |
|------|------|
| **Store** | 数据访问抽象层，定义图数据的 CRUD + 遍历 + 搜索接口，隔离存储实现细节 |
| **CypherEngine** | 完整的 Cypher 查询引擎：Lexer → Parser → AST → Planner → Executor 五层 |
| **Pipeline** | 多 Pass 索引管线引擎，管理 Pass 注册、编排、执行、数据依赖 |
| **Extractor** | 基于 tree-sitter 的符号提取（保留现有接口，评估是否需要小扩展） |
| **LSPResolver** | 基于 JSON-RPC 的类型解析和跨文件引用补全（自己实现 LSP 协议，不依赖 pygls） |
| **GraphAlgorithms** | 社区发现、代码克隆检测、中心性分析等图算法 |
| **FileWatcher** | 文件变更监听，触发增量索引，通过接口解耦允许替换监听机制 |
| **CLI** | 命令行接口薄层，仅做参数解析和模块装配，所有逻辑委托给上述模块 |

### 2.3 模块间依赖关系（严格单向，只允许接口依赖）

```
CLI
 ├──→ CypherEngine (通过 Store)
 ├──→ Pipeline (通过接口)
 ├──→ GraphAlgorithms (通过 Store)
 ├──→ LSPResolver (通过接口)
 ├──→ FileWatcher (通过接口)
 └──→ Store (通过接口)

CypherEngine
 └──→ Store (接口)                    # 仅依赖 Store 接口，不依赖任何具体实现

Pipeline
 ├──→ Extractor (注册表)              # 通过 registry 获取 extractor，不直接 import
 ├──→ LSPResolver (接口)              # LSP 解析作为管线中的一个 Pass
 └──→ Store (接口)                    # 写入索引结果

GraphAlgorithms
 └──→ Store (接口)                    # 通过 Store 接口获取图数据，不直接读 DB

LSPResolver
 └──→ Store (接口)                    # 读 def index，写解析结果

FileWatcher
 └──→ Pipeline (接口)                 # 检测到变更时触发管线重新索引

# 禁止的依赖方向：
#  - Store 不依赖任何上层模块
#  - CypherEngine 不依赖 Pipeline/CLI
#  - Pipeline 不依赖 CLI
#  - 任何模块不依赖 CLI（CLI 是最外层）
```

### 2.4 数据流：从文件扫描到查询执行

```
阶段 1: 索引构建
=================
File System
    │
    ▼
[FileScanner] ─────────── 列出所有源文件 (git ls-files / os.walk)
    │
    ▼
[Pipeline Engine] ─────── 按顺序执行 Pass 列表
    │
    ├── Pass 1: StatFilter ───── 通过 mtime/size 跳过未变化的文件
    │
    ├── Pass 2: ParseExtract ─── tree-sitter 解析 + Extractor 提取符号/边
    │     │                       每文件独立，可并行
    │     └── 输出: per-file {nodes, edges, errors}
    │
    ├── Pass 3: DefIndexBuild ── 构建 per-module def 索引（内存 HashMap）
    │     │                       供后续 Pass 快速查找
    │     └── 输出: {qualified_name → node_id} 映射
    │
    ├── Pass 4: CrossFileResolve ─ 使用 def index 补全跨文件调用边
    │     │                       (替代现有 edge_resolver.py 的 O(n*m) 后缀匹配)
    │     └── 输出: resolved edges + unresolved refs
    │
    ├── Pass 5: LSPTypeResolve ── 对仍未解析的引用启动 LSP 类型解析
    │     │                       只加载相关模块的 defs（per-module index 加速）
    │     └── 输出: type-resolved edges + type annotations on nodes
    │
    ├── Pass 6: ImportClassify ── 分类 import 为 internal/external
    │
    ├── Pass 7: FrameworkDetect ─ 框架检测（FastAPI, Spring 等）
    │
    ├── Pass 8: SkillIndex ────── TWS skill 文件索引（特定场景）
    │
    ├── Pass 9: FTSRebuild ────── 重建全文搜索索引
    │
    └── Pass 10: CommitToStore ── 批量写入 Store（事务保护）

阶段 2: 查询执行
=================
CLI 接收用户查询 (Cypher 字符串)
    │
    ▼
[CypherEngine]
    ├── Lexer   ──→ Token Stream
    ├── Parser  ──→ AST
    ├── Planner ──→ Logical Plan (基于 Store 接口的操作序列)
    │               优化: 谓词下推、索引选择、join 重排
    └── Executor──→ 执行 plan，通过 Store 接口读取数据
                     │
                     ▼
                  格式化输出 (终端 / JSON)
```

---

## 三、核心模块深度设计

### 3.1 Cypher 引擎模块

#### 3.1.1 五层架构

```
Cypher Query String
        │
        ▼
┌──────────────────────────────────────────────────┐
│ Lexer (cypher/lexer.py)                          │
│  输入: str                                       │
│  输出: Iterator[Token]                           │
│  职责: 关键词识别、标识符、字面量、运算符、标点      │
│        Token 类型: KEYWORD, IDENTIFIER, STRING,   │
│        INTEGER, FLOAT, BOOLEAN, NULL, OPERATOR,   │
│        PUNCTUATION, PARAMETER                      │
└───────────────────┬──────────────────────────────┘
                    ▼
┌──────────────────────────────────────────────────┐
│ Parser (cypher/parser.py)                        │
│  输入: Iterator[Token]                           │
│  输出: AST (Statement)                           │
│  职责: 递归下降解析，构建 AST                      │
│        支持: MATCH, OPTIONAL MATCH, CREATE,       │
│        MERGE, DELETE, SET, RETURN, ORDER BY,      │
│        SKIP, LIMIT, UNION, UNWIND, WITH, CALL {}  │
└───────────────────┬──────────────────────────────┘
                    ▼
┌──────────────────────────────────────────────────┐
│ AST (cypher/ast.py)                              │
│  纯数据类，不可变                                 │
│  节点类型: Statement, Query, MatchClause,         │
│  WhereClause, ReturnClause, OrderByClause,        │
│  UnionClause, UnwindClause, CaseExpression,        │
│  FunctionCall, AggregationCall, Subquery,          │
│  PatternPart, NodePattern, RelPattern,             │
│  PropertyAccess, ListComprehension, Parameter,     │
│  Literal, Variable, Operator                       │
│                                                    │
│  AST 与 Planner/Executor 完全解耦：                │
│  任何产生 AST 的东西都可以对接 Planner              │
└───────────────────┬──────────────────────────────┘
                    ▼
┌──────────────────────────────────────────────────┐
│ Planner (cypher/planner.py)                      │
│  输入: AST (Statement)                           │
│  输出: LogicalPlan                               │
│  职责: AST → 逻辑计划转换                          │
│        - 解析符号名 → node_id (通过 Store 接口)    │
│        - 选择遍历策略 (BFS/DFS/索引查找)            │
│        - 谓词下推 (WHERE 条件尽早过滤)              │
│        - OPTIONAL MATCH → LEFT JOIN               │
│        - 聚合分解 (WITH + RETURN 中的聚合)          │
│        - ORDER BY / SKIP / LIMIT 位置确定          │
│                                                    │
│  LogicalPlan 是一种操作符树:                        │
│  NodeScan, EdgeExpand, Filter, Project, Sort,      │
│  Limit, Aggregate, Union, Unwind, Optional,        │
│  Subquery                                         │
└───────────────────┬──────────────────────────────┘
                    ▼
┌──────────────────────────────────────────────────┐
│ Executor (cypher/executor.py)                    │
│  输入: LogicalPlan + Store (接口)                │
│  输出: ResultSet (支持流式)                       │
│  职责: 按 plan 逐操作符执行                         │
│        - 每个操作符通过 Store 接口获取数据           │
│        - 支持 lazy evaluation (迭代器模式)          │
│        - 支持 early termination (LIMIT 提前停止)    │
│        - 聚合函数通过注册表 dispatch                │
│                                                    │
│  操作符实现:                                       │
│  - NodeScan: Store.get_nodes_by_kind() / FTS       │
│  - EdgeExpand: Store.get_neighbors(node_id, kinds) │
│  - Filter: 内存中逐行判断 (Python lambda)           │
│  - Project: 字段投影                               │
│  - Sort: 内存排序 (小数据集) / Store level (大数据)  │
│  - Aggregate: HashMap 分组聚合                      │
│  - Union: 迭代器拼接                               │
│  - Unwind: 列表展开                                │
│  - Optional: 类似 LEFT JOIN，空行保留               │
└──────────────────────────────────────────────────┘
```

#### 3.1.2 AST 节点类型设计

```python
# cypher/ast.py

class Node(ABC):
    """AST 基类。所有节点不可变（dataclass frozen）。"""
    span: Span  # 源码位置，用于错误报告

@dataclass(frozen=True)
class Statement(Node):
    """顶层语句。一个 Cypher 查询可以包含多个 Statement (UNION)。"""
    queries: list[Query]

@dataclass(frozen=True)
class Query(Node):
    """单条查询：MATCH ... WHERE ... RETURN ..."""
    match: Optional[MatchClause]
    optional_matches: list[MatchClause]
    where: Optional[WhereClause]
    with_clause: Optional[WithClause]
    return_clause: ReturnClause
    order_by: Optional[OrderByClause]
    skip: Optional[int]
    limit: Optional[int]

@dataclass(frozen=True)
class MatchClause(Node):
    pattern: PatternPart
    optional: bool  # True for OPTIONAL MATCH

@dataclass(frozen=True)
class PatternPart(Node):
    """(node)-[edge]->(node) 的链式表示"""
    elements: list[PatternElement]  # NodePattern → RelPattern → NodePattern → ...

@dataclass(frozen=True)
class NodePattern(Node):
    variable: Optional[str]        # MATCH (n) 中的 n
    labels: list[str]              # MATCH (n:Function:Python)
    properties: dict[str, Expression]  # MATCH (n {kind: 'function'})

@dataclass(frozen=True)
class RelPattern(Node):
    variable: Optional[str]
    kinds: list[str]               # :calls|:references
    direction: Direction           # LEFT | RIGHT | BOTH
    properties: dict[str, Expression]
    min_hops: int = 1
    max_hops: int = 1

@dataclass(frozen=True)
class WhereClause(Node):
    expression: Expression

@dataclass(frozen=True)
class ReturnClause(Node):
    items: list[ReturnItem]
    distinct: bool = False

@dataclass(frozen=True)
class ReturnItem(Node):
    expression: Expression
    alias: Optional[str]

@dataclass(frozen=True)
class OrderByClause(Node):
    items: list[OrderByItem]

@dataclass(frozen=True)
class OrderByItem(Node):
    expression: Expression
    direction: Literal["ASC", "DESC"]

@dataclass(frozen=True)
class UnionClause(Node):
    all: bool  # UNION vs UNION ALL
    left: Statement
    right: Statement

@dataclass(frozen=True)
class UnwindClause(Node):
    expression: Expression
    variable: str

@dataclass(frozen=True)
class CaseExpression(Node):
    """CASE WHEN ... THEN ... ELSE ... END"""
    case_operand: Optional[Expression]  # CASE x WHEN 1 ... 中的 x
    when_clauses: list[tuple[Expression, Expression]]  # [(when, then), ...]
    else_clause: Optional[Expression]

@dataclass(frozen=True)
class FunctionCall(Node):
    name: str  # e.g. "count", "collect", "toUpper"
    arguments: list[Expression]
    distinct: bool = False

@dataclass(frozen=True)
class AggregationCall(Node):
    """聚合函数调用：count(*), collect(n.name), avg(x)"""
    function: str  # count, sum, avg, min, max, collect, stDev, percentile
    argument: Optional[Expression]
    distinct: bool = False

@dataclass(frozen=True)
class Subquery(Node):
    """CALL { MATCH ... RETURN ... } 子查询"""
    statement: Statement

@dataclass(frozen=True)
class PropertyAccess(Node):
    expression: Expression
    property_name: str

# Expression 类型还包括:
# Literal, Variable, Parameter, BinaryOp, UnaryOp, 
# ListComprehension, MapLiteral, ListLiteral, IsNull, InList
```

#### 3.1.3 Executor 与 Store 解耦

```python
# cypher/executor.py

class Executor:
    """通过 Store 接口操作，不依赖任何具体存储实现。
    
    Executor 只知道 Store 接口中定义的 graph 操作方法。
    不管是 SqliteStore 还是 MemoryStore 还是 DuckDBStore，
    只要实现了 Store 接口，Executor 就能工作。
    """
    
    def __init__(self, store: Store):
        self.store = store  # Store 是接口/协议，不是具体类
    
    def execute(self, plan: LogicalPlan) -> ResultSet:
        """执行一个逻辑计划，返回流式结果集。"""
        for operator in plan.operators:
            if isinstance(operator, NodeScan):
                yield from self._execute_node_scan(operator)
            elif isinstance(operator, EdgeExpand):
                yield from self._execute_edge_expand(operator)
            # ... etc
    
    def _execute_node_scan(self, op: NodeScan) -> Iterator[Row]:
        """NodeScan 只调用 Store 的公开方法。"""
        if op.fts_query:
            return self.store.search(op.fts_query, limit=op.limit)
        elif op.node_ids:
            return self.store.get_nodes_by_ids(op.node_ids)
        elif op.kind_filter:
            return self.store.iter_nodes_by_kind(op.kind_filter)
        else:
            return self.store.iter_all_nodes()
    
    def _execute_edge_expand(self, op: EdgeExpand) -> Iterator[Row]:
        """EdgeExpand 只调用 Store.get_neighbors()。"""
        for row in op.input_rows:
            neighbors = self.store.get_neighbors(
                row[op.source_var],
                kinds=op.edge_kinds,
                direction=op.direction,
            )
            for neighbor in neighbors:
                yield row.extend(neighbor)
```

#### 3.1.4 扩展性设计

**新增语法子句（如 CALL {} 子查询）**：
1. 在 `ast.py` 中添加对应的 AST 节点类（1 个 dataclass）
2. 在 `parser.py` 中添加解析规则（1-2 个方法）
3. 在 `planner.py` 中添加 AST → LogicalPlan 转换规则（1 个 visitor 方法）
4. 在 `executor.py` 中添加新的操作符执行逻辑（1 个类）

不需要修改 Lexer（子查询用的都是已有 token），不需要修改其他子句的处理逻辑。

**新增聚合函数（如 percentile）**：
1. 在 `cypher/aggregates.py` 注册表中添加函数名 → 实现函数映射
2. 实现新函数（纯函数，输入 list，输出标量）
3. 不需要修改 parser/planner/executor 的任何代码

```python
# cyppher/aggregates.py
_AGGREGATES: dict[str, Callable[[list[Any]], Any]] = {}

def register_aggregate(name: str, func: Callable):
    _AGGREGATES[name] = func

def get_aggregate(name: str) -> Callable:
    return _AGGREGATES[name]

# 内置注册
register_aggregate("count", lambda vals: len([v for v in vals if v is not None]))
register_aggregate("sum", lambda vals: sum(v for v in vals if v is not None))
register_aggregate("avg", lambda vals: ...)
register_aggregate("collect", lambda vals: list(vals))
register_aggregate("min", lambda vals: min(vals))
register_aggregate("max", lambda vals: max(vals))

# 新增 percentile 只需:
def _percentile(vals, p):
    sorted_vals = sorted(v for v in vals if v is not None)
    if not sorted_vals: return None
    idx = int(len(sorted_vals) * p / 100)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]

register_aggregate("percentile", lambda vals: _percentile(vals, 50))
```

---

### 3.2 Store 抽象层

#### 3.2.1 Store 接口设计

```python
# store/interface.py

from abc import ABC, abstractmethod
from typing import Iterator, Optional, Protocol

class Store(ABC):
    """图数据存储的抽象接口。
    
    所有的查询和算法只依赖这个接口，不依赖任何具体存储实现。
    替换存储后端只需要实现这个接口的所有方法。
    """

    # ── 连接管理 ──────────────────────────────
    @abstractmethod
    def close(self) -> None: ...

    # ── 事务 ──────────────────────────────────
    @abstractmethod
    def begin(self) -> None: ...
    @abstractmethod
    def commit(self) -> None: ...
    @abstractmethod
    def rollback(self) -> None: ...

    # ── Node CRUD ─────────────────────────────
    @abstractmethod
    def insert_node(self, node: dict) -> None: ...
    @abstractmethod
    def insert_nodes(self, nodes: list[dict]) -> None: ...
    @abstractmethod
    def get_node_by_id(self, node_id: str) -> Optional[dict]: ...
    @abstractmethod
    def get_nodes_by_ids(self, ids: list[str]) -> dict[str, dict]: ...
    @abstractmethod
    def delete_nodes_by_file(self, file_path: str) -> None: ...
    @abstractmethod
    def iter_nodes_by_kind(self, kind: str, batch_size: int = 1000) -> Iterator[dict]: ...
    @abstractmethod
    def iter_all_nodes(self, batch_size: int = 1000) -> Iterator[dict]: ...
    @abstractmethod
    def count_nodes(self) -> int: ...

    # ── Edge CRUD ─────────────────────────────
    @abstractmethod
    def insert_edge(self, edge: dict) -> None: ...
    @abstractmethod
    def insert_edges(self, edges: list[dict]) -> None: ...
    @abstractmethod
    def get_outgoing_edges(self, source_id: str, kinds: Optional[list[str]] = None) -> list[dict]: ...
    @abstractmethod
    def get_incoming_edges(self, target_id: str, kinds: Optional[list[str]] = None) -> list[dict]: ...
    @abstractmethod
    def get_edges_between(self, source_id: str, target_id: str) -> list[dict]: ...
    @abstractmethod
    def update_edge_target(self, edge_id: int, new_target: str) -> None: ...
    @abstractmethod
    def delete_edges_by_source(self, source_id: str) -> None: ...
    @abstractmethod
    def count_edges(self) -> int: ...

    # ── 图遍历（高层操作）──────────────────────
    @abstractmethod
    def get_neighbors(
        self,
        node_id: str,
        kinds: Optional[list[str]] = None,
        direction: Literal["in", "out", "both"] = "both",
    ) -> list[dict]: ...
    
    @abstractmethod
    def get_neighbors_batch(
        self,
        node_ids: list[str],
        kinds: Optional[list[str]] = None,
        direction: Literal["in", "out", "both"] = "both",
    ) -> dict[str, list[dict]]: ...
    
    @abstractmethod
    def find_paths(
        self,
        from_id: str,
        to_id: str,
        kinds: Optional[list[str]] = None,
        max_depth: int = 5,
    ) -> Optional[list[dict]]: ...

    # ── File CRUD ─────────────────────────────
    @abstractmethod
    def upsert_file(self, path: str, content_hash: str, language: str,
                    node_count: int, size: int, modified_at: int) -> None: ...
    @abstractmethod
    def get_file(self, path: str) -> Optional[dict]: ...
    @abstractmethod
    def get_all_files(self) -> list[dict]: ...
    @abstractmethod
    def delete_file(self, path: str) -> None: ...

    # ── 搜索 ──────────────────────────────────
    @abstractmethod
    def fts_search(self, query: str, limit: int = 20,
                   kind_filter: Optional[str] = None,
                   language_filter: Optional[str] = None) -> list[dict]: ...
    
    @abstractmethod
    def search_by_def_index(self, qualified_name: str) -> Optional[str]:
        """通过 def index 精确查找 node_id。O(1) HashMap 查找。"""
        ...

    # ── 未解析引用 ────────────────────────────
    @abstractmethod
    def insert_unresolved_ref(self, ref: dict) -> None: ...
    @abstractmethod
    def get_unresolved_refs(self, file_path: Optional[str] = None) -> list[dict]: ...
    @abstractmethod
    def clear_unresolved_refs(self) -> None: ...

    # ── 批量操作 ──────────────────────────────
    @abstractmethod
    def flush(self) -> None:
        """将内存缓冲的数据批量写入持久存储。"""
        ...

    # ── 统计/维护 ─────────────────────────────
    @abstractmethod
    def optimize(self) -> None: ...
    @abstractmethod
    def clear(self) -> None: ...
    @abstractmethod
    def stats(self) -> dict: ...
```

#### 3.2.2 两个核心实现

**SqliteStore（磁盘持久化，默认后端）**：
- 封装现有 `db/queries.py` 的逻辑，但重构为面向接口
- 使用 WAL mode + prepared statement cache
- def index 维护在内存 SQLite 临时表或 Python dict 中
- 提供批量写入缓冲区（accumulate nodes/edges in memory, flush in one transaction）

**MemoryStore（内存图，查询执行 / 算法计算后端）**：
- 基于 Python dict 构建邻接表
- 加载：从 SqliteStore 导入全部节点和边到内存
- 遍历：O(1) 邻接查找，无需 SQL
- 用于 Cypher Executor 的查询执行（快速遍历）
- 用于图算法（社区发现等需要多次遍历的操作）
- 可选使用 NetworkX 作为内部图表示（通过接口，可替换）

```python
# store/memory_store.py 示意

class MemoryStore(Store):
    def __init__(self):
        self._nodes: dict[str, dict] = {}          # id → node
        self._outgoing: dict[str, list[tuple[str, dict]]] = {}  # source → [(target, edge)]
        self._incoming: dict[str, list[tuple[str, dict]]] = {}  # target → [(source, edge)]
        self._def_index: dict[str, str] = {}       # qualified_name → node_id

    def load_from(self, other: Store):
        """从另一个 Store 加载全部数据到内存。"""
        for node in other.iter_all_nodes():
            self._nodes[node["id"]] = node
            self._def_index[node["qualified_name"]] = node["id"]
        # 加载边...
    
    def get_neighbors(self, node_id, kinds=None, direction="both"):
        result = []
        if direction in ("out", "both"):
            for target_id, edge in self._outgoing.get(node_id, []):
                if kinds is None or edge["kind"] in kinds:
                    result.append({**self._nodes.get(target_id, {}), "_edge": edge})
        if direction in ("in", "both"):
            for source_id, edge in self._incoming.get(node_id, []):
                if kinds is None or edge["kind"] in kinds:
                    result.append({**self._nodes.get(source_id, {}), "_edge": edge})
        return result
```

#### 3.2.3 查询能力分层

```
层 3: 查询层 (Cypher)
  │   CypherEngine 将 Cypher 查询编译为 LogicalPlan
  │   用户/Agent 直接写 Cypher
  │   示例: MATCH (f:Function)-[:calls]->(g) WHERE f.language = 'python' RETURN f.name, g.name
  
层 2: 逻辑层 (图操作)
  │   Store 接口提供图遍历原语
  │   get_neighbors(), find_paths(), fts_search()
  │   示例: store.get_neighbors("abc123", kinds=["calls"], direction="out")
  
层 1: 物理层 (存储操作)
  │   SqliteStore 内部: SQL 语句
  │   MemoryStore 内部: dict 查找
  │   示例: SELECT * FROM edges WHERE source = ? AND kind IN ('calls', 'references')
```

#### 3.2.4 扩展性：换用其他存储后端

**换用 DuckDB**：实现一个 `DuckDBStore(Store)`，覆盖所有抽象方法。其余代码（CypherEngine, Pipeline, GraphAlgorithms, CLI）**零修改**。

**换用 Neo4j**：实现 `Neo4jStore(Store)`，底层将图操作翻译为 Cypher 发送到 Neo4j（注意：不是自己解析 Cypher，而是把 Store 接口方法翻译为 Cypher 字符串发给 Neo4j）。其余代码**零修改**。

**混合模式**：查询执行时，Cypher Executor 内部创建 MemoryStore，从 SqliteStore 加载数据，在内存中执行遍历，结果返回。对 Executor 来说，两个 Store 都是 Store 接口的实现。

---

### 3.3 多 Pass 管线

#### 3.3.1 Pipeline 引擎设计

```python
# pipeline/engine.py

class PipelineEngine:
    """多 Pass 管线引擎。
    
    管理 Pass 的注册、编排、执行和数据传递。
    每个 Pass 是独立的转换单元，可独立测试和启用/禁用。
    
    设计原则：
    - Pass 之间不直接耦合，只通过 Context 传递数据
    - Pass 的执行顺序显式配置，不在 Pass 内部硬编码
    - 新 Pass 只需注册即可加入管线
    """

    def __init__(self, store: Store):
        self.store = store
        self._passes: list[Pass] = []
        self._registry: dict[str, type[Pass]] = {}  # Pass 注册表

    def register_pass(self, pass_class: type[Pass]):
        """注册一个 Pass 类（在管线构建前调用）。"""
        self._registry[pass_class.name] = pass_class

    def build_pipeline(self, pass_names: list[str]) -> "PipelineEngine":
        """按名称列表实例化 Pass，构建执行管线。"""
        self._passes = [self._registry[name](self.store) for name in pass_names]
        return self

    def execute(self, files: list[str]) -> PipelineContext:
        """顺序执行所有 Pass，Pass 间通过 PipelineContext 传递数据。
        
        Args:
            files: 需要索引的文件列表
        
        Returns:
            PipelineContext 包含所有 Pass 的产出
        """
        ctx = PipelineContext(files=files, store=self.store)
        for i, pas in enumerate(self._passes):
            try:
                ctx = pas.run(ctx)
                if ctx.abort:
                    break
            except Exception as e:
                # 单个 Pass 失败不崩溃整个管线
                ctx.errors.append({
                    "pass": pas.name,
                    "error": str(e),
                })
        return ctx


@dataclass
class PipelineContext:
    """管线上下文，在 Pass 间传递。
    
    每个 Pass 可以从 ctx 读取上游 Pass 的产出，
    也可以将自己的产出写入 ctx 供下游 Pass 使用。
    """
    files: list[str]
    store: Store
    
    # Pass 产出（类型安全的命名空间）
    parsed_results: dict[str, ExtractionResult] = field(default_factory=dict)
    def_index: dict[str, str] = field(default_factory=dict)  # qualified_name → node_id
    node_file_map: dict[str, str] = field(default_factory=dict)  # node_id → file_path
    unresolved_refs: list[dict] = field(default_factory=list)
    type_info: dict[str, TypeInfo] = field(default_factory=dict)
    
    # 控制标志
    abort: bool = False
    errors: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
```

#### 3.3.2 Pass 接口契约

```python
# pipeline/pass_interface.py

class Pass(ABC):
    """单个管线步骤的抽象接口。
    
    契约：
    - 输入: PipelineContext
    - 输出: PipelineContext (可修改后返回同一个实例)
    - 行为: 幂等（同一输入 → 同一输出）
    - 副作用: 只能通过 store 写入，不直接操作文件系统
    - 错误处理: 抛异常由 PipelineEngine 捕获，不静默失败
    """

    name: str = ""           # Pass 唯一标识
    description: str = ""    # 人类可读描述

    def __init__(self, store: Store):
        self.store = store

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        """执行此 Pass，返回更新后的上下文。"""
```

#### 3.3.3 当前需要的 Pass 列表

| 序号 | Pass 名称 | 职责 | 输入 | 输出 | 是否可跳过 |
|------|----------|------|------|------|-----------|
| 1 | `StatFilterPass` | 通过 mtime/size 过滤未变化的文件 | ctx.files | ctx.files (精简) | 否 |
| 2 | `ParseExtractPass` | tree-sitter 解析 + 符号提取 | ctx.files | ctx.parsed_results | 否 |
| 3 | `NodeInsertPass` | 将提取的符号写入 Store | ctx.parsed_results | Store 中有节点 | 否 |
| 4 | `DefIndexBuildPass` | 构建 per-module def index | ctx.parsed_results | ctx.def_index | 否 |
| 5 | `CrossFileResolvePass` | 使用 def index 补全跨文件引用 | ctx.def_index | 边的 target 被更新 | 是 |
| 6 | `LSPTypeResolvePass` | 通过 LSP 解析类型和未解引用 | ctx.def_index | ctx.type_info + 边补全 | 是 |
| 7 | `ImportClassifyPass` | 分类 import 为 internal/external | Store 中的 import 边 | unresolved_refs 填充 | 否 |
| 8 | `FrameworkDetectPass` | 检测框架并提取路由 | Store 中文件列表 | 框架节点和边 | 是 |
| 9 | `SkillIndexPass` | TWS skill 文件索引 | TWS skill .md 文件 | 技能节点和边 | 是 |
| 10 | `FTSRebuildPass` | 重建全文搜索索引 | Store 中的节点 | FTS 索引就绪 | 否 |
| 11 | `StatsCollectPass` | 收集统计信息 | ctx + Store | ctx.stats | 否 |

#### 3.3.4 扩展性：新增 Pass 需要几步

**新增一个 Pass（如 ClangTidyPass）**：
1. 创建 `pipeline/passes/clang_tidy.py`，实现 `Pass` 接口（~30 行代码）
2. 在 Pass 注册表中注册：`engine.register_pass(ClangTidyPass)`（1 行）
3. 在构建管线时按需加入 pass_names 列表（1 行）

**不需要修改**：PipelineEngine、PipelineContext、其他 Pass、CLI 代码。

---

### 3.4 LSP 类型解析模块

#### 3.4.1 设计决策：自己实现 LSP 协议

不依赖 pygls、python-lsp-server 等第三方 LSP 框架。LSP 协议本身很简单：JSON-RPC over stdio/TCP。我们只需要实现解析所需的最小协议子集。

```python
# lsp/protocol.py

import json
import subprocess
import threading
from queue import Queue

class LspClient:
    """轻量 LSP JSON-RPC 客户端。
    
    只实现类型解析需要的请求：
    - initialize
    - textDocument/didOpen
    - textDocument/definition
    - textDocument/references
    - textDocument/hover
    - textDocument/documentSymbol
    - shutdown
    """

    def __init__(self, command: list[str], workspace_root: str):
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._id = 0
        self._pending: dict[int, Queue] = {}
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._init_result = self._initialize(workspace_root)

    def _send(self, method: str, params: dict) -> dict:
        """发送 JSON-RPC 请求，阻塞等待响应。"""
        self._id += 1
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params,
        })
        content = msg.encode("utf-8")
        header = f"Content-Length: {len(content)}\r\n\r\n".encode("utf-8")
        self._process.stdin.write(header + content)
        self._process.stdin.flush()
        # 从 _pending 队列中等待响应
        q = Queue()
        self._pending[self._id] = q
        return q.get(timeout=30)

    def _read_loop(self):
        """后台线程持续读取 LSP server 的响应。"""
        # 解析 Content-Length 头部，读取对应长度的 JSON body
        ...

    def get_definition(self, file_path: str, line: int, col: int) -> list[Location]:
        """跳转到定义。"""
        return self._send("textDocument/definition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": col},
        })
    
    def get_references(self, file_path: str, line: int, col: int) -> list[Location]:
        """查找所有引用。"""
        ...
```

#### 3.4.2 Per-module def index 设计

参考 `codebase-memory-mcp` 的设计理念：跨文件类型解析时，不需要加载整个项目的所有符号。只加载当前文件 import 的那些模块的符号定义。

```python
# lsp/def_index.py

class PerModuleDefIndex:
    """per-module 定义索引。
    
    Key: module_path (e.g. "src/utils/helpers.py")
    Value: dict[str, NodeInfo]  # 该模块的所有符号定义
    
    解析文件 A 时：
    1. 从 import 语句中提取 A 依赖的模块列表 [B, C, D]
    2. 从 DefIndex 中只加载 B, C, D 的符号定义
    3. 在 B, C, D 的符号范围内进行类型匹配
    
    相比全项目 O(n) 扫描，这通常是 10-50x 的加速。
    """
    
    def __init__(self, store: Store):
        self._by_module: dict[str, dict[str, str]] = {}  # module → {simple_name → node_id}
        self._by_qualified: dict[str, str] = {}          # qualified_name → node_id

    def build(self, parsed_results: dict[str, ExtractionResult]):
        """从解析结果构建 per-module 索引。"""
        for file_path, result in parsed_results.items():
            module_defs = {}
            for node in result.nodes:
                qualified = node["qualified_name"]
                simple = node["name"]
                module_defs[simple] = node["id"]
                self._by_qualified[qualified] = node["id"]
            self._by_module[file_path] = module_defs

    def resolve(self, import_module: str, symbol_name: str, current_file: str) -> Optional[str]:
        """解析一个 import 的符号。
        
        支持:
        - 直接 import: import foo → foo.bar
        - from import: from foo import bar → bar
        - 相对 import: from . import bar → 相对于 current_file
        """
        target_module = self._resolve_module_path(import_module, current_file)
        if target_module and target_module in self._by_module:
            return self._by_module[target_module].get(symbol_name)
        return None
```

#### 3.4.3 语言适配器接口

```python
# lsp/language_adapters.py

class LspLanguageAdapter(ABC):
    """LSP 语言适配器接口。
    
    每种语言有不同的 LSP server，不同的 import 解析规则，
    不同的类型系统。适配器封装了这些差异。
    """

    language: str = ""  # e.g. "python", "typescript", "java"

    @abstractmethod
    def get_server_command(self, workspace_root: str) -> list[str]:
        """返回启动 LSP server 的命令。"""
    
    @abstractmethod
    def parse_import(self, import_statement: str, current_file: str) -> ImportInfo:
        """解析 import 语句，提取 (module_path, symbol_name)。
        
        不同语言的 import 语法不同：
        - Python: "from foo.bar import Baz" → module="foo.bar", symbol="Baz"
        - TS: "import { foo } from './bar'" → module="./bar", symbol="foo"
        - Java: "import com.foo.Bar;" → module="com.foo", symbol="Bar"
        - Kotlin: "import com.foo.bar" → module="com.foo", symbol="bar"
        """
    
    @abstractmethod
    def resolve_module_path(self, module_name: str, current_file: str) -> Optional[str]:
        """将 import 的模块名解析为项目中的文件路径。"""
    
    @abstractmethod
    def get_language_server_name(self) -> str:
        """LSP server 的可执行文件名。"""

# 示例实现
class PythonLspAdapter(LspLanguageAdapter):
    language = "python"
    
    def get_server_command(self, workspace_root: str) -> list[str]:
        # 自己实现 LSP 客户端，server 用 jedi-language-server 或 pyright
        # jedi-language-server 是 LSP server，不是我们依赖的库
        # 我们通过 subprocess 启动它，通过 stdio JSON-RPC 通信
        return ["pyright-langserver", "--stdio"]
    
    def parse_import(self, stmt: str, current_file: str) -> ImportInfo:
        # 解析 Python import 语句
        ...
```

#### 3.4.4 与索引管线的集成

LSP 解析作为管线中的一个可选 Pass：

```python
class LSPTypeResolvePass(Pass):
    name = "lsp-type-resolve"
    description = "通过 LSP 解析类型和补全跨文件引用"

    def __init__(self, store: Store):
        super().__init__(store)
        self._adapters: dict[str, LspLanguageAdapter] = {
            "python": PythonLspAdapter(),
            "typescript": TypeScriptLspAdapter(),
            "java": JavaLspAdapter(),
            # 新增语言只需添加新的 adapter
        }

    def run(self, ctx: PipelineContext) -> PipelineContext:
        # 1. 获取所有仍未解析的引用
        unresolved = ctx.store.get_unresolved_refs()
        if not unresolved:
            return ctx
        
        # 2. 按语言分组
        by_language: dict[str, list[dict]] = {}
        for ref in unresolved:
            by_language.setdefault(ref["language"], []).append(ref)
        
        # 3. 对每种语言启动 LSP，只加载相关模块的 defs
        for lang, refs in by_language.items():
            adapter = self._adapters.get(lang)
            if not adapter:
                continue
            
            # 找出涉及的所有模块
            involved_modules = {ref["file_path"] for ref in refs}
            involved_modules.update(
                adapter.parse_import(ref["reference_name"], ref["file_path"]).module
                for ref in refs
            )
            
            # 只加载这些模块的 defs 到 LSP 上下文
            def_index = self._build_scoped_def_index(
                ctx.def_index, involved_modules
            )
            
            # 逐引用解析
            for ref in refs:
                resolved = self._resolve_with_lsp(adapter, ref, def_index)
                if resolved:
                    ctx.store.update_edge_target(ref["edge_id"], resolved)
        
        return ctx
```

---

### 3.5 图算法模块

#### 3.5.1 设计决策

| 算法 | 实现策略 | 理由 |
|------|---------|------|
| **社区发现 (Leiden/Louvain)** | 包装 NetworkX | 这些算法的正确实现涉及复杂的模块度优化，自研容易引入 bug。NetworkX 是成熟实现，且只在算法层作为可选后端使用，不进入核心查询路径 |
| **代码克隆检测 (MinHash + LSH)** | 完全自研 | MinHash 和 LSH 的核心逻辑很简单（~50 行 Python），不需要第三方库。datasketch 等库增加依赖但没有提供足够价值 |
| **中心性分析 (PageRank/Betweenness)** | 包装 NetworkX | 同上，成熟算法直接使用 |
| **循环检测** | 自研 (DFS based) | 简单算法，无需依赖 |
| **连通分量** | 自研 (Union-Find) | 经典数据结构，20 行代码 |

#### 3.5.2 MinHash + LSH 自研实现

```python
# algorithms/minhash.py

import hashlib
import struct

class MinHash:
    """MinHash 签名生成器 — 用于代码克隆检测。
    
    原理：
    1. 对代码进行 tokenize（直接复用 tree-sitter 的 AST 节点类型作为 token）
    2. 用 N 个哈希函数计算每个 token 的哈希值
    3. 对每个哈希函数保留最小值 → N 维签名
    4. 比较两份代码的签名：Jaccard ≈ 签名相等的比例
    """

    def __init__(self, num_perm: int = 128, seed: int = 42):
        self.num_perm = num_perm
        # 预计算哈希函数的参数 (a, b)，避免运行时随机
        self._hash_params = [
            (hashlib.sha256(f"{seed}_{i}".encode()).digest()[:8])
            for i in range(num_perm)
        ]

    def compute_signature(self, tokens: list[str]) -> list[int]:
        """计算 token 序列的 MinHash 签名。"""
        sig = [0xFFFFFFFFFFFFFFFF] * self.num_perm
        for token in tokens:
            token_hash = int(hashlib.sha256(token.encode()).hexdigest()[:16], 16)
            for i, seed_bytes in enumerate(self._hash_params):
                seed_int = struct.unpack(">Q", seed_bytes)[0]
                h = token_hash ^ seed_int
                # XOR + Murmur-style mixing
                h = (h ^ (h >> 33)) * 0xFF51AFD7ED558CCD
                h = (h ^ (h >> 33)) * 0xC4CEB9FE1A85EC53
                h = h ^ (h >> 33)
                sig[i] = min(sig[i], h)
        return sig

    @staticmethod
    def jaccard_estimate(sig1: list[int], sig2: list[int]) -> float:
        """估算 Jaccard 相似度。"""
        return sum(1 for a, b in zip(sig1, sig2) if a == b) / len(sig1)


class LSHIndex:
    """LSH 索引 — 快速查找相似代码片段。
    
    使用 Banding 技术：
    - 将 N 维签名分成 B 个 band，每个 band 有 R 行
    - 如果两份代码在任意一个 band 中完全相同，就放入同一个桶
    - 查询时只需要检查同一桶内的候选对（候选集远小于全量组合）
    """

    def __init__(self, num_perm: int = 128, bands: int = 16):
        self.rows_per_band = num_perm // bands
        self._buckets: list[dict[tuple, set[str]]] = [{} for _ in range(bands)]

    def insert(self, node_id: str, signature: list[int]):
        """将签名插入 LSH 索引。"""
        for band_idx in range(len(self._buckets)):
            start = band_idx * self.rows_per_band
            band_key = tuple(signature[start:start + self.rows_per_band])
            self._buckets[band_idx].setdefault(band_key, set()).add(node_id)

    def query(self, signature: list[int]) -> set[str]:
        """查找可能相似的节点 ID 集合。"""
        candidates: set[str] = set()
        for band_idx in range(len(self._buckets)):
            start = band_idx * self.rows_per_band
            band_key = tuple(signature[start:start + self.rows_per_band])
            candidates.update(self._buckets[band_idx].get(band_key, set()))
        return candidates
```

#### 3.5.3 与 Store 层的解耦

```python
# algorithms/interface.py

class GraphAlgorithm(ABC):
    """图算法基类 — 只依赖 Store 接口和 NetworkX（可选）。"""

    def __init__(self, store: Store):
        self.store = store

    @abstractmethod
    def execute(self, **kwargs) -> dict:
        """执行算法，返回结果字典。"""

# algorithms/community.py

class CommunityDetection(GraphAlgorithm):
    """社区发现 — 使用 NetworkX 的 Louvain 实现。
    
    NetworkX 作为可选后端：如果安装了 NetworkX，使用其优化实现；
    否则回退到自研的简化版 Louvain。
    """

    def execute(self, edge_kinds: Optional[list[str]] = None) -> dict:
        # 1. 从 Store 接口读取图数据（不直接操作 SQLite）
        edges = []
        # 使用 MemoryStore 将数据加载到 NetworkX
        G = nx.Graph()
        for node in self.store.iter_all_nodes():
            G.add_node(node["id"], **node)
        # 遍历边（通过 Store 接口，也可优化为直接 query）
        # ...
        
        # 2. 执行 Louvain 算法
        try:
            import networkx.algorithms.community as nx_comm
            communities = nx_comm.louvain_communities(G)
        except ImportError:
            communities = self._simple_louvain(G)
        
        return {
            "community_count": len(communities),
            "modularity": ...,
            "communities": [...],
        }
```

---

### 3.6 文件监听模块

#### 3.6.1 设计

```python
# watcher/interface.py

class FileWatcher(ABC):
    """文件变更监听器接口。

    watchdog 是默认实现，但通过接口解耦，允许替换为其他监听机制
    （如 polling、inotify、fsevents、git hooks only）。
    """

    @abstractmethod
    def start(self, root_dir: str, callback: Callable[[FileChangeEvent], None]) -> None:
        """开始监听文件变更。"""

    @abstractmethod
    def stop(self) -> None:
        """停止监听。"""

    @abstractmethod
    def is_running(self) -> bool:
        """是否正在运行。"""


@dataclass
class FileChangeEvent:
    path: str
    change_type: Literal["created", "modified", "deleted", "moved"]
    src_path: Optional[str] = None  # for moved


class WatchdogFileWatcher(FileWatcher):
    """基于 watchdog 的默认实现。"""
    
    def start(self, root_dir, callback):
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        
        class Handler(FileSystemEventHandler):
            def on_modified(self, event): ...
            def on_created(self, event): ...
            def on_deleted(self, event): ...
            def on_moved(self, event): ...
        
        self._observer = Observer()
        self._observer.schedule(Handler(), root_dir, recursive=True)
        self._observer.start()
    
    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()


class PollingFileWatcher(FileWatcher):
    """纯 polling 实现 — 不依赖 watchdog。
    
    适配无法安装 watchdog 的环境（如某些 CI 环境）。
    使用自适应 polling interval。
    """

    def __init__(self, base_interval: float = 2.0):
        self._base_interval = base_interval
        ...
```

#### 3.6.2 与 git hooks 的协作

```
┌─────────────────────────────────────────────────────┐
│            文件变更检测机制                            │
│                                                     │
│  ┌─────────────┐     ┌──────────────────┐           │
│  │ git hooks   │     │ FileWatcher      │           │
│  │ post-commit │     │ (watchdog/poll)  │           │
│  │ post-merge  │     │                  │           │
│  │ post-checkout│    │                  │           │
│  └──────┬──────┘     └────────┬─────────┘           │
│         │                     │                      │
│         └──────────┬──────────┘                      │
│                    ▼                                 │
│           ┌────────────────┐                        │
│           │ Debounce Queue │  (合并 500ms 内的变更)   │
│           └───────┬────────┘                        │
│                   ▼                                  │
│          ┌─────────────────┐                        │
│          │ IncrementalSync │  (只重建变化的文件)       │
│          └────────┬────────┘                        │
│                   ▼                                  │
│            Pipeline.execute(changed_files)            │
└─────────────────────────────────────────────────────┘
```

协作机制：
- **git hooks** 是即时触发（每次 commit/merge/checkout 后）
- **FileWatcher** 是后台持续监听（用于实时编辑场景）
- 两者都通过同一个 `DebounceQueue` 进入 `Pipeline.execute(changed_files)`
- 去重：如果 hook 触发和 FileWatcher 检测到同一批文件，DebounceQueue 合并为一次执行

---

## 四、模块契约定义

### 4.1 Store

| 维度 | 内容 |
|------|------|
| **提供的能力** | 图数据的完整 CRUD：节点增删改查（单条 + 批量）、边增删改查、图遍历（邻居查询/路径查找）、全文搜索（FTS5 BM25 + LIKE + fuzzy）、文件记录管理、未解析引用管理、事务管理、def index 查询 |
| **需要的输入** | 数据库路径（SqliteStore）/ 无（MemoryStore）；数据通过方法参数传入 |
| **输出** | dict / list[dict] 形式的节点和边数据；统计信息；遍历结果 |
| **不依赖哪些模块** | 不依赖 CypherEngine、Pipeline、CLI、GraphAlgorithms、FileWatcher。只依赖 Python 标准库 + sqlite3 |

**行为约束**：
- 所有写操作支持事务（begin/commit/rollback）
- `insert_nodes` 和 `insert_edges` 是批量操作，内部使用 executemany
- `get_neighbors` 返回的邻居信息中不包含被标记为 deleted 的节点
- `fts_search` 返回结果按 BM25 相关性排序
- `get_nodes_by_ids` 对不存在的 ID 返回空 dict（不抛异常）

### 4.2 CypherEngine

| 维度 | 内容 |
|------|------|
| **提供的能力** | 完整 Cypher 子集支持：MATCH、OPTIONAL MATCH、WHERE、RETURN (含 AS 别名)、ORDER BY、SKIP、LIMIT、UNION、UNWIND、CASE WHEN THEN ELSE END、聚合函数 (count/sum/avg/min/max/collect)、DISTINCT、属性访问 (n.name)、子查询 (CALL {}) |
| **需要的输入** | Cypher 查询字符串 + Store 实例 |
| **输出** | `ResultSet`：包含列名和行数据的流式迭代器。每行是 `dict[str, Any]` |
| **不依赖哪些模块** | 不依赖 Pipeline、CLI、FileWatcher、GraphAlgorithms、LSPResolver。只依赖 Store 接口 |

**行为约束**：
- 查询计划优化：WHERE 条件尽可能下推到 Store 层（谓词下推）
- ORDER BY 如果可以在 Store 层完成（如 SQLite 有索引），下推到 Store；否则在 Executor 内存中排序
- LIMIT 提前终止迭代（短路求值）
- 对不存在的符号名返回空结果集（不抛异常）
- 语法错误抛 `CypherSyntaxError`（包含位置信息）

### 4.3 Pipeline

| 维度 | 内容 |
|------|------|
| **提供的能力** | Pass 注册（register_pass）、管线构建（build_pipeline）、管线执行（execute）。管理 Pass 的执行顺序和数据传递，处理 Pass 失败时的错误恢复 |
| **需要的输入** | Store 实例 + 注册的 Pass 类列表 + 文件列表 |
| **输出** | `PipelineContext`（包含所有 Pass 的产出、统计信息、错误列表） |
| **不依赖哪些模块** | 不依赖 CypherEngine、CLI、GraphAlgorithms。Pass 实现可以依赖 Store、LSPResolver、Extractor（通过接口） |

**行为约束**：
- Pass 按注册顺序顺序执行（不并行，保持简单）
- 单个 Pass 失败不终止整个管线（Continue-on-Error 策略）
- PipelineContext 是唯一的数据传递通道——Pass 之间不直接引用彼此
- execute() 是幂等的：同一文件列表多次执行产生相同结果

### 4.4 Extractor（保留现有接口，评估是否需要小扩展）

| 维度 | 内容 |
|------|------|
| **提供的能力** | 从 tree-sitter AST 中提取符号定义（function/class/method/variable/interface 等）和关系边（calls/imports/extends/implements/contains） |
| **需要的输入** | source (bytes) + tree (tree-sitter Tree) + ctx (ExtractionContext) |
| **输出** | 修改 ctx.result：添加 nodes 和 edges 列表 |
| **不依赖哪些模块** | 不依赖 Store、CypherEngine、Pipeline（只被它们调用）、CLI。唯一外部依赖是 tree-sitter |

**现有接口是否需要扩展**：建议增加一个 `extract_imports()` 可选方法，专门提取 import 语句（供 LSP 解析 Pass 使用），以避免 LSP Pass 需要重新解析文件。

```python
def extract_imports(self, source: bytes, tree, ctx: ExtractionContext) -> list[ImportInfo]:
    """可选的 hook：提取 import 语句信息（module + symbols）。"""
    return []  # 默认空
```

### 4.5 LSPResolver

| 维度 | 内容 |
|------|------|
| **提供的能力** | 通过 LSP 协议（JSON-RPC over stdio）与 language server 通信，解析类型定义、查找引用、补全未解析的跨文件调用边 |
| **需要的输入** | 未解析引用列表 + per-module def index + 语言适配器注册表 |
| **输出** | 解析结果：`{ref_id → resolved_node_id}` 映射；类型注解信息 |
| **不依赖哪些模块** | 不依赖 CypherEngine、Pipeline（作为 Pass 被调用）、CLI、GraphAlgorithms。只依赖 Store 接口和 subprocess |

**行为约束**：
- 每个 language server 作为独立进程启动，通过 stdio 通信
- 超时机制：单个 LSP 请求 30 秒超时，超时视为解析失败
- 只解析项目中未解决的内部引用（is_external=False 的引用）
- 按需加载 per-module def index（只加载相关模块）

### 4.6 GraphAlgorithms

| 维度 | 内容 |
|------|------|
| **提供的能力** | 社区发现（Louvain/Leiden）、代码克隆检测（MinHash + LSH）、中心性分析（PageRank/Betweenness）、循环检测、连通分量 |
| **需要的输入** | Store 实例 + 算法参数（边类型过滤、深度限制等） |
| **输出** | 结构化结果字典：算法名称 + 度量值 + 成员/分组列表 |
| **不依赖哪些模块** | 不依赖 CypherEngine、Pipeline、CLI、LSPResolver。Store 接口是唯一数据源。NetworkX 作为可选内部引擎（运行时检查是否安装） |

**行为约束**：
- 所有算法接收 Store 接口，不直接访问 SQLite 或其他存储细节
- 大图（> 10万节点）使用迭代器/分批处理，避免全量加载到内存
- 克隆检测的 MinHash 签名在索引构建时预计算并存储在节点属性中

### 4.7 FileWatcher

| 维度 | 内容 |
|------|------|
| **提供的能力** | 监听文件系统变更（created/modified/deleted/moved），触发回调通知 |
| **需要的输入** | root_dir + callback 函数 + 可选的监听模式（watchdog / polling） |
| **输出** | `FileChangeEvent` 事件流（通过 callback 传递） |
| **不依赖哪些模块** | 不依赖 Store、CypherEngine、Pipeline、CLI。callback 由调用者提供，调用者负责桥接到 Pipeline |

**行为约束**：
- 启动/停止是可重复的（第二次 start 不创建重复 observer）
- 变更事件在 500ms debounce 窗口内合并（避免同一文件的连续保存触发多次索引）
- 自动忽略 `.git/`、`node_modules/`、`__pycache__/`、`.tws/` 等目录
- 如果没有安装 watchdog，自动回退到 polling 模式

### 4.8 CLI

| 维度 | 内容 |
|------|------|
| **提供的能力** | index、sync、calls、impact、trace、search、unresolved、snapshot、diff、lint、hooks、query（新增 Cypher 查询命令） |
| **需要的输入** | 命令行参数（通过 typer 解析） |
| **输出** | 格式化文本（终端）或 JSON（`--json` 标志） |
| **不依赖哪些模块** | 不包含业务逻辑。所有逻辑委托给 Store、CypherEngine、Pipeline、GraphAlgorithms、FileWatcher、LSPResolver |

**行为约束**：
- CLI 是系统中唯一允许直接创建具体类实例的地方（依赖注入的组装点）
- CLI 命令函数不超过 20 行——逻辑在模块中，CLI 只做参数转换和输出格式化
- 所有命令支持 `--json` 标志输出结构化数据
- 错误处理：业务异常转为用户友好的错误消息 + 合适的退出码

---

## 五、解耦策略

### 5.1 解耦的三个层次

```
层次 1: 接口隔离
  每个模块定义自己的抽象接口（ABC 或 Protocol）。
  所有跨模块调用只通过接口，不通过具体类。

层次 2: 依赖注入
  CLI 是唯一的组装点。CLI 负责创建具体实现类的实例，
  通过构造函数注入给依赖方。
  模块内部不执行 import 具体实现 —— 只 import 接口。

层次 3: 事件/回调
  FileWatcher → Pipeline 的通知通过 callback 传递。
  不要求 FileWatcher 知道 Pipeline 的存在。
```

### 5.2 具体解耦措施

```python
# ❌ 坏：直接依赖具体实现
from tws_graph.db.queries import QueryBuilder
from tws_graph.graph.traversal import GraphTraverser

def do_query():
    qb = QueryBuilder(conn)
    gt = GraphTraverser(qb)
    return gt.get_calls("abc123")

# ✅ 好：通过接口 + 依赖注入
from tws_graph.store.interface import Store
from tws_graph.cypher.engine import CypherEngine

def do_query(store: Store):
    engine = CypherEngine(store)
    return engine.execute("MATCH (n)-[:calls]->(m) WHERE n.id = 'abc123' RETURN m")
```

### 5.3 依赖方向规则

```
CLI ──→ 所有模块（薄层，只做装配）
CypherEngine ──→ Store (接口)
Pipeline ──→ Store (接口), Extractor (注册表), LSPResolver (接口)
GraphAlgorithms ──→ Store (接口)
LSPResolver ──→ Store (接口)
FileWatcher ──→ Pipeline (callback，不是 import)
Extractor ──→ (无依赖，纯数据转换)
Store ──→ (无依赖，最底层)

禁止：
- Store ──→ 任何其他模块
- Extractor ──→ Store / Pipeline
- 任何模块 ──→ CLI
```

---

## 六、扩展性设计

### 6.1 新增一种语言

| 需要做的事 | 修改范围 | 工作量 |
|-----------|---------|-------|
| 1. 创建 extractor 类（`extractors/ruby.py`） | 新增 1 个文件 | ~200 行 |
| 2. 创建 LSP 适配器（`lsp/adapters/ruby.py`） | 新增 1 个文件 | ~80 行 |
| 3. 确保 tree-sitter-language-pack 包含该语言 | 外部依赖 | 无代码 |
| **不需要修改的文件** | registry.py, parser.py, scanner.py, pipeline/*, cypher/*, store/*, cli.py | **0 处修改** |

### 6.2 新增一个 Pass

| 需要做的事 | 修改范围 | 工作量 |
|-----------|---------|-------|
| 1. 创建 Pass 类（`pipeline/passes/my_pass.py`） | 新增 1 个文件 | ~50 行 |
| 2. 在 CLI 中注册/使用 | 修改 1 行 | 1 行 |
| **不需要修改的文件** | PipelineEngine, PipelineContext, 其他 Pass, Store, CypherEngine | **0 处修改** |

### 6.3 新增 Cypher 语法子句

| 需要做的事 | 修改范围 | 工作量 |
|-----------|---------|-------|
| 1. 在 `ast.py` 中添加 AST 节点类 | 新增 1 个 dataclass | ~10 行 |
| 2. 在 `parser.py` 中添加解析规则 | 新增 1-2 个方法 | ~30 行 |
| 3. 在 `planner.py` 中添加转换规则 | 新增 1 个 visitor 方法 | ~20 行 |
| 4. 在 `executor.py` 中添加执行逻辑 | 新增 1 个操作符类 | ~40 行 |
| **不需要修改的文件** | lexer.py, Store 接口, CLI | **0 处修改** |

### 6.4 新增存储后端

| 需要做的事 | 修改范围 | 工作量 |
|-----------|---------|-------|
| 1. 创建 `DuckDBStore(Store)` 实现 | 新增 1 个文件 | ~300 行 |
| 2. 在 CLI 中添加 `--store-backend duckdb` 选项 | 修改 2 行 | 2 行 |
| **不需要修改的文件** | CypherEngine, Pipeline, GraphAlgorithms, 所有其他模块 | **0 处修改** |

### 6.5 新增查询能力（通过 Cypher 扩展实现）

| 查询场景 | Cypher 写法 | 是否需要改代码 |
|---------|-----------|-------------|
| 按模块分组统计 | `MATCH (n) RETURN n.file_path, count(*) ORDER BY count(*) DESC` | 否（标准 Cypher） |
| 查找入口点 | `MATCH (n) WHERE NOT (m)-[:calls]->(n) RETURN n` | 否 |
| 查找循环依赖 | 使用 GraphAlgorithms 的循环检测 API | 否（算法模块独立） |
| 查找最复杂的函数 | `MATCH (n:Function) RETURN n.name, n.end_line - n.start_line AS len ORDER BY len DESC LIMIT 10` | 否 |

---

## 七、与参考项目的设计对比

### 7.1 借鉴思路但自研实现

| 参考项目设计 | 我们的实现 | 为何借鉴但自研 |
|-------------|-----------|-------------|
| 多 Pass 管线 | 自研 PipelineEngine + Pass 接口 | 理念相同（可组合的管线），但我们定义了严格的 Pass 契约和 PipelineContext 数据传递协议，比参考项目的隐式数据传递更可测试 |
| RAM-first 策略 | MemoryStore + SqliteStore 双层架构 | 理念相同（内存构建，一次 dump），但我们通过 Store 接口抽象，使得算法和查询引擎自动享受内存加速，无需感知存储细节 |
| JSON properties | 保留 relational schema + JSON 扩展字段 | SQLite 的有类型列 + JSON extension 比纯 JSON columns 更适合查询优化（索引 + 谓词下推）。关键字段保持列级，扩展属性用 JSON |
| Per-module def index | PerModuleDefIndex 类 | 理念相同（按需加载），但我们将其作为 Pipeline 的一个 Pass 产出，而非全局单例，便于测试和生命周期管理 |
| 自适应 polling | PollingFileWatcher 接口 + 多种实现 | 理念相同，但通过 FileWatcher 接口解耦，不强制绑定 watchdog |

### 7.2 我们做得更好的地方

| 维度 | 参考项目 | 我们的方案 | 优势来源 |
|------|---------|-----------|---------|
| **查询语言** | 自定义查询语法（非标准） | 完整 Cypher 子集 | 标准化：任何熟悉 Neo4j 的人可直接使用 |
| **存储解耦** | 直接依赖 SQLite | Store 接口 + 多种实现 | 存储后端可替换而不影响任何查询/算法代码 |
| **类型解析** | 纯文本后缀匹配 | LSP 协议（自研客户端）+ per-module def index | 100x 精确度提升：类型级别匹配 vs 字符串后缀启发式 |
| **扩展性** | 新功能需要改核心文件 | 新语言/Pass/算法只需添加新文件 | 开闭原则：对扩展开放，对修改关闭 |
| **解耦程度** | 模块间直接 import | 接口隔离 + 依赖注入 | 任意模块可被独立替换和测试 |

### 7.3 参考项目有但我们不做的设计

| 设计 | 不做原因 |
|------|---------|
| TypeScript 实现（参考项目用 TypeScript） | tws-graph 是 Python 项目，团队使用 Python，无需跨语言 |
| 向 MCP 协议暴露 API | tws-graph 定位是 CLI 工具 + Python 库，Agent 通过 CLI 调用。MCP 层可以作为未来的可选扩展，但不属于核心 |
| 函数文摘（LLM generated summary） | 设计上可能有用，但 LLM 生成成本高（按 token 计费），且总结可能过时。保留为可选 Pass |
| 图可视化导出 | 不是 CLI 工具的核心功能。如果有需求，可以通过 `--json` 输出 + 外部工具可视化 |

---

## 八、关键假设（≤5 个）

| # | 假设 | 如果不成立会怎样 | 成立概率 |
|---|------|----------------|---------|
| 1 | SQLite 3.38+ 的 JSON 函数和 FTS5 在目标 Python 环境中可用 | 需要用 Python 代码实现 JSON 属性过滤，FTS 降级为 LIKE，性能下降 3-10x | 高（Python 3.12+ 内置的 sqlite3 模块链接 SQLite 3.45+） |
| 2 | tree-sitter 的 Python binding 和 language pack 保持维护 | 解析层基础设施丧失，需要切换到 tree-sitter 的替代品或直接用 tree-sitter CLI | 高（tree-sitter 是 GitHub/Microsoft 维护的主流项目，社区活跃） |
| 3 | language server 可以通过 subprocess + stdio JSON-RPC 稳定通信 | LSP 解析不可靠，回退到纯文本后缀匹配（现有方案），损失精确度但不损失可用性 | 中高（LSP 协议就是为 stdio 设计的，但部分 language server 的跨平台行为有差异） |
| 4 | 项目规模在 1 万文件 / 100 万符号以内，MemoryStore 可将全图加载到内存 | 超大项目需要流式处理或分区索引，MemoryStore 需要分页/淘汰策略 | 高（TWS 目标项目通常不是百万级代码库；即使是大项目，符号数通常远小于代码行数） |
| 5 | MinHash + LSH 的克隆检测精度可满足实用需求（不需要更高级的 AST 比较） | 假阳性/假阴性率超出可接受范围，需要引入更复杂的 AST diff 算法 | 中高（MinHash 对 token-level 相似度的估计在文献中被证明有效；可以通过调整 num_perm 参数调优） |

---

## 九、风险清单

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| **Cypher 引擎实现复杂度超标** | 完整 Cypher 子集需要约 3000-5000 行代码，可能超出预期工作量 | 分阶段交付：Phase 1 先实现 MATCH/WHERE/RETURN 核心子集（~1500 行），Phase 2 加 OPTIONAL MATCH/ORDER BY/聚合（~1000 行），Phase 3 加 UNION/UNWIND/CASE/子查询（~1000 行） |
| **LSP server 进程管理复杂** | 多语言多进程的启动/通信/超时/清理容易出现资源泄漏 | 使用进程池 + with 语句管理生命周期；每个 LSP session 有明确的最大存活时间；超时后强制 kill |
| **MemoryStore 和 SqliteStore 的数据一致性** | 如果 MemoryStore 用于算法但 SqliteStore 有并发更新，数据不一致 | 算法执行期间使用快照隔离：MemoryStore.load_from() 时创建一致性快照，不对 SqliteStore 的后续更新可见 |
| **现有 CLI 用户接口变化** | 现有 `tws-graph calls/impact/trace` 等命令行为改变可能影响下游工具 | 保留所有现有 CLI 命令作为兼容层，内部迁移到新架构；新增 `tws-graph query` 命令作为 Cypher 入口；旧命令标记为 `[deprecated]` 但不移除 |
| **性能回退** | 新架构增加了抽象层（Store 接口、PipelineContext），比原来直接 SQL 可能有性能开销 | 关键路径（get_neighbors, get_nodes_by_ids）在 SqliteStore 中使用 prepared statements + 批量化；MemoryStore 使用 Python dict 直接查找（O(1)）；性能测试成为 CI 的一部分 |

---

## 十、推荐进入方案设计

**推荐方案**：完全自研的模块化架构，以 Store 接口为数据访问的统一抽象层，以 CypherEngine 为查询的统一入口，以 Pipeline + Pass 为索引的可组合管线。

这个方案的核心赌注是：**通过严格的接口隔离和依赖注入，使得每个模块可以独立开发、测试、替换和扩展，从而在 AI 编程的加持下能以极快的速度构建出功能远超现有实现的系统**。

方案设计阶段需要产出：
1. 每个模块的详细类图和函数签名
2. Cypher 语法的完整 BNF 定义
3. Store 接口的每个方法的详细行为规范（pre/post conditions）
4. Pipeline 中每个 Pass 的详细算法伪代码
5. 数据库 schema 的完整 DDL（含新增表和索引）
6. 分阶段交付计划（Phase 1/2/3 的功能边界）
