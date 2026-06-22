"""Shared type definitions for the Store abstraction layer.

These TypedDicts define the data structures exchanged between the Store
interface and its consumers (CypherEngine, Pipeline, GraphAlgorithms, LSPResolver).

Per design-store-schema.md section 5.1.
"""

from typing import Literal, NotRequired, Optional, TypedDict


class NodeRecord(TypedDict, total=False):
    """Store 中返回的节点记录。"""

    id: str  # hash(qualified_name + file_path)
    kind: str  # function/class/method/interface/variable/route/module/...
    name: str  # 简单名: "calculateTotal"
    qualified_name: str  # 全限定名: "src/utils.py::MathHelper.calculateTotal"
    file_path: str  # 相对项目根路径
    language: str  # python/typescript/java/kotlin/go/rust/markdown
    start_line: int
    end_line: int
    signature: Optional[str]
    docstring: Optional[str]
    visibility: Optional[str]  # public/private/protected/internal
    is_abstract: int  # 0/1
    is_exported: int  # 0/1
    decorators: Optional[str]  # JSON array string
    framework: Optional[str]  # fastapi/express/spring
    properties: Optional[str]  # JSON object — 扩展属性
    updated_at: int  # epoch millis


class EdgeRecord(TypedDict, total=False):
    """Store 中返回的边记录。"""

    id: int  # auto-increment primary key
    source: str  # → nodes.id
    target: str  # → nodes.id (may be unresolvable)
    target_text: Optional[str]  # unhashed qualified name for post-processing
    kind: str  # calls/imports/extends/implements/references/contains
    source_loc: Optional[str]  # "file:line:col"
    provenance: str  # tree-sitter | heuristic | resolved | unresolved | ambiguous | cycle_detected
    properties: Optional[str]  # JSON object — 扩展属性


class FileRecord(TypedDict):
    """已索引文件的元数据记录。"""

    path: str
    content_hash: str  # SHA256
    language: str
    node_count: int
    indexed_at: int  # epoch millis
    size: int
    modified_at: int  # epoch seconds (mtime)


class UnresolvedRefRecord(TypedDict, total=False):
    """未解析的跨文件引用记录。"""

    id: int
    from_node_id: str
    reference_name: str
    reference_kind: str  # call/import/reference
    line: int
    col: int
    candidates: Optional[str]  # JSON array
    source: Optional[str]  # 来源标记: tree-sitter/heuristic/lsp_failed/manual
    file_path: str
    language: str
    is_external: int  # 0=internal, 1=external


class StoreStats(TypedDict):
    """存储统计信息。"""

    node_count: int
    edge_count: int
    file_count: int
    unresolved_count: int
    last_indexed_at: NotRequired[int]  # epoch millis of last successful full index
    db_size_bytes: NotRequired[int]  # only for SqliteStore
    memory_usage_bytes: NotRequired[int]  # only for MemoryStore


class SearchResult(TypedDict):
    """FTS 搜索结果。"""

    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    language: str
    signature: Optional[str]
    docstring: Optional[str]
    rank: Optional[float]  # BM25 score, lower is better


Direction = Literal["in", "out", "both"]
"""图遍历方向类型别名。"""
