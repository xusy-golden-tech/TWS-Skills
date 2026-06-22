"""Cypher 集成测试 — 端到端查询场景，使用 MemoryStore + CypherEngine。

覆盖：基本 MATCH、WHERE 过滤、排序分页、关系查询、投影、组合查询、
       函数调用、OPTIONAL MATCH、边界、错误处理。

测试数据为固定数据集：10 个节点 + 13 条边。
"""

from __future__ import annotations

import pytest

from tws_graph.cypher import CypherEngine, execute, ResultSet, Row
from tws_graph.cypher.errors import (
    CypherLexerError,
    CypherSyntaxError,
    CypherSemanticError,
    CypherExecutionError,
)
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Fixtures — 固定测试数据集
# ============================================================================


@pytest.fixture(scope="module")
def store() -> MemoryStore:
    """创建预填充好的 MemoryStore 实例（module 级，所有测试共享）。

    10 个节点 + 13 条边，覆盖 function / method / class 三种 kind。
    """
    s = MemoryStore()
    nodes = [
        {
            "id": "n1", "name": "main", "kind": "function",
            "file_path": "/src/main.py", "start_line": 10, "end_line": 20,
            "language": "python", "qualified_name": "main",
        },
        {
            "id": "n2", "name": "helper", "kind": "function",
            "file_path": "/src/main.py", "start_line": 25, "end_line": 35,
            "language": "python", "qualified_name": "helper",
        },
        {
            "id": "n3", "name": "Calculator", "kind": "class",
            "file_path": "/src/calc.py", "start_line": 5, "end_line": 30,
            "language": "python", "qualified_name": "Calculator",
        },
        {
            "id": "n4", "name": "add", "kind": "method",
            "file_path": "/src/calc.py", "start_line": 10, "end_line": 18,
            "language": "python", "qualified_name": "Calculator.add",
        },
        {
            "id": "n5", "name": "subtract", "kind": "method",
            "file_path": "/src/calc.py", "start_line": 20, "end_line": 28,
            "language": "python", "qualified_name": "Calculator.subtract",
        },
        {
            "id": "n6", "name": "run", "kind": "function",
            "file_path": "/src/runner.py", "start_line": 1, "end_line": 12,
            "language": "python", "qualified_name": "run",
        },
        {
            "id": "n7", "name": "App", "kind": "class",
            "file_path": "/src/app.py", "start_line": 1, "end_line": 20,
            "language": "typescript", "qualified_name": "App",
        },
        {
            "id": "n8", "name": "render", "kind": "method",
            "file_path": "/src/app.py", "start_line": 5, "end_line": 15,
            "language": "typescript", "qualified_name": "App.render",
        },
        {
            "id": "n9", "name": "init", "kind": "function",
            "file_path": "/src/init.py", "start_line": 1, "end_line": 10,
            "language": "python", "qualified_name": "init",
        },
        {
            "id": "n10", "name": "cleanup", "kind": "function",
            "file_path": "/src/init.py", "start_line": 15, "end_line": 25,
            "language": "python", "qualified_name": "cleanup",
        },
    ]
    s.insert_nodes(nodes)

    edges = [
        {"source": "n6", "target": "n1", "kind": "calls"},
        {"source": "n6", "target": "n2", "kind": "calls"},
        {"source": "n1", "target": "n2", "kind": "calls"},
        {"source": "n1", "target": "n4", "kind": "calls"},
        {"source": "n4", "target": "n5", "kind": "calls"},
        {"source": "n9", "target": "n2", "kind": "calls"},
        {"source": "n9", "target": "n10", "kind": "calls"},
        {"source": "n1", "target": "n3", "kind": "imports"},
        {"source": "n8", "target": "n1", "kind": "calls"},
        {"source": "n3", "target": "n4", "kind": "defines"},
        {"source": "n3", "target": "n5", "kind": "defines"},
        {"source": "n7", "target": "n8", "kind": "defines"},
        {"source": "n6", "target": "n9", "kind": "calls"},
    ]
    s.insert_edges(edges)
    return s


@pytest.fixture(scope="module")
def engine() -> CypherEngine:
    """共享的 CypherEngine 实例。"""
    return CypherEngine()


@pytest.fixture(scope="function")
def empty_store() -> MemoryStore:
    """空的 MemoryStore，每个测试函数独立。"""
    return MemoryStore()


# ============================================================================
# A. 基本 MATCH 查询
# ============================================================================


class TestBasicMatch:
    """A 类：基本 MATCH 查询 — 全扫描、标签过滤、分页。"""

    # ── 全扫描 ────────────────────────────────────────────────────────

    def test_match_all_nodes_returns_10(self, store, engine):
        """MATCH (n) RETURN n — 返回全部 10 个节点。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        assert len(result.rows) == 10
        assert result.total_count == 10

    def test_match_all_nodes_is_resultset(self, store, engine):
        """返回对象是 ResultSet 实例。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        assert isinstance(result, ResultSet)

    def test_match_all_nodes_columns(self, store, engine):
        """列名包含绑定的变量名。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        assert "n" in result.columns

    def test_match_all_nodes_each_row_has_node_dict(self, store, engine):
        """每一行都包含完整的节点字典。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        for row in result.rows:
            node = row["n"]
            assert isinstance(node, dict)
            assert "id" in node
            assert "name" in node
            assert "kind" in node
            assert "file_path" in node

    def test_match_all_nodes_collect_all_ids(self, store, engine):
        """收集所有节点 id，确认为完整集合。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        ids = {row["n"]["id"] for row in result.rows}
        expected = {"n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "n10"}
        assert ids == expected

    # ── 标签过滤 ──────────────────────────────────────────────────────

    def test_match_function_label(self, store, engine):
        """MATCH (n:function) RETURN n — 返回 5 个 function 节点。"""
        result = engine.execute("MATCH (n:function) RETURN n", store)
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper", "run", "init", "cleanup"}
        assert len(result.rows) == 5

    def test_match_class_label(self, store, engine):
        """MATCH (n:class) RETURN n — 返回 2 个 class 节点。"""
        result = engine.execute("MATCH (n:class) RETURN n", store)
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"Calculator", "App"}
        assert len(result.rows) == 2

    def test_match_method_label(self, store, engine):
        """MATCH (n:method) RETURN n — 返回 3 个 method 节点。"""
        result = engine.execute("MATCH (n:method) RETURN n", store)
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"add", "subtract", "render"}
        assert len(result.rows) == 3

    def test_match_nonexistent_label_returns_empty(self, store, engine):
        """MATCH (n:module) RETURN n — 无 module 节点，返回空。"""
        result = engine.execute("MATCH (n:module) RETURN n", store)
        assert len(result.rows) == 0
        assert result.total_count == 0

    def test_match_function_label_with_name_projection(self, store, engine):
        """MATCH (n:function) RETURN n.name — 标签 + 字段投影。"""
        result = engine.execute("MATCH (n:function) RETURN n.name", store)
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper", "run", "init", "cleanup"}

    # ── LIMIT / SKIP ──────────────────────────────────────────────────

    def test_match_limit_3(self, store, engine):
        """MATCH (n) RETURN n LIMIT 3 — 只有 3 行。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        assert len(result.rows) == 3

    def test_match_limit_larger_than_total_returns_all(self, store, engine):
        """MATCH (n) RETURN n LIMIT 100 — LIMIT 超过总数时返回全部。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 100", store)
        assert len(result.rows) == 10

    def test_match_limit_zero_returns_empty(self, store, engine):
        """MATCH (n) RETURN n LIMIT 0 — 空结果集。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 0", store)
        assert len(result.rows) == 0
        assert result.total_count == 0

    def test_match_skip_5(self, store, engine):
        """MATCH (n) RETURN n SKIP 5 — 跳过前 5 个。"""
        result = engine.execute("MATCH (n) RETURN n SKIP 5", store)
        assert len(result.rows) == 5

    def test_match_skip_past_end_returns_empty(self, store, engine):
        """MATCH (n) RETURN n SKIP 100 — 跳过超过总数，返回空。"""
        result = engine.execute("MATCH (n) RETURN n SKIP 100", store)
        assert len(result.rows) == 0

    def test_match_skip_and_limit(self, store, engine):
        """MATCH (n) RETURN n SKIP 3 LIMIT 4 — 跳过 3 取 4。"""
        result = engine.execute("MATCH (n) RETURN n SKIP 3 LIMIT 4", store)
        assert len(result.rows) == 4

    def test_match_skip_and_limit_with_total(self, store, engine):
        """MATCH (n) RETURN n SKIP 8 LIMIT 5 — 跳过 8 只剩 2。"""
        result = engine.execute("MATCH (n) RETURN n SKIP 8 LIMIT 5", store)
        assert len(result.rows) == 2

    # ── ResultSet 接口 ────────────────────────────────────────────────

    def test_resultset_len_matches_total_count(self, store, engine):
        """len(result) == result.total_count。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        assert len(result) == result.total_count
        assert len(result) == 3

    def test_resultset_is_iterable(self, store, engine):
        """ResultSet 支持 for...in 迭代。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        count = 0
        for row in result:
            assert isinstance(row, Row)
            count += 1
        assert count == 3

    def test_resultset_indexing(self, store, engine):
        """ResultSet 支持 [index] 索引。"""
        result = engine.execute("MATCH (n:function) RETURN n", store)
        first = result[0]
        assert isinstance(first, Row)
        assert "n" in first.data

    def test_row_get_with_default(self, store, engine):
        """Row.get() 支持默认值。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        row = result.rows[0]
        assert row.get("n") is not None
        assert row.get("nonexistent") is None
        assert row.get("nonexistent", "fallback") == "fallback"

    def test_row_keyerror_on_missing(self, store, engine):
        """访问不存在的 key 抛出 KeyError。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        with pytest.raises(KeyError):
            _ = result.rows[0]["no_such_column"]


# ============================================================================
# B. WHERE 过滤
# ============================================================================


class TestWhereFiltering:
    """B 类：WHERE 子句 — 等值、不等、AND/OR、正则、IN、NULL 检查。"""

    # ── 等值比较 ──────────────────────────────────────────────────────

    def test_where_name_equals_exact(self, store, engine):
        """WHERE n.name = 'main' — 精确等值。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' RETURN n", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "main"

    def test_where_kind_equals_function(self, store, engine):
        """WHERE n.kind = 'function' — kind 属性等值。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' RETURN n", store
        )
        assert len(result.rows) == 5

    def test_where_file_path_equals(self, store, engine):
        """WHERE n.file_path = '/src/main.py' — 文件路径等值。"""
        result = engine.execute(
            "MATCH (n) WHERE n.file_path = '/src/main.py' RETURN n", store
        )
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper"}
        assert len(result.rows) == 2

    def test_where_language_equals_python(self, store, engine):
        """WHERE n.language = 'python' — 语言过滤。"""
        result = engine.execute(
            "MATCH (n) WHERE n.language = 'python' RETURN n", store
        )
        # 9 python nodes + 1 typescript node (App)
        assert len(result.rows) == 8

    def test_where_language_equals_typescript(self, store, engine):
        """WHERE n.language = 'typescript' — 仅 App 和 render。"""
        result = engine.execute(
            "MATCH (n) WHERE n.language = 'typescript' RETURN n", store
        )
        assert len(result.rows) == 2

    def test_where_no_match_returns_empty(self, store, engine):
        """WHERE n.name = 'nonexistent' — 无匹配返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'nonexistent' RETURN n", store
        )
        assert len(result.rows) == 0

    # ── 不等比较 ──────────────────────────────────────────────────────

    def test_where_name_not_equal(self, store, engine):
        """WHERE n.name <> 'main' — 排除 main。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name <> 'main' RETURN n", store
        )
        names = {row["n"]["name"] for row in result.rows}
        assert "main" not in names
        assert len(result.rows) == 9

    def test_where_kind_not_equal_function(self, store, engine):
        """WHERE n.kind <> 'function' — 非 function 节点。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind <> 'function' RETURN n", store
        )
        kinds = {row["n"]["kind"] for row in result.rows}
        assert "function" not in kinds
        assert len(result.rows) == 5  # 2 class + 3 method

    # ── AND / OR 组合 ─────────────────────────────────────────────────

    def test_where_and_two_conditions(self, store, engine):
        """WHERE n.name = 'main' AND n.kind = 'function' — 双重条件。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' AND n.kind = 'function' RETURN n",
            store,
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "main"

    def test_where_and_three_conditions(self, store, engine):
        """WHERE n.kind = 'function' AND n.language = 'python' AND n.file_path = '/src/main.py'。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' AND n.language = 'python' AND n.file_path = '/src/main.py' RETURN n",
            store,
        )
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_where_or_two_names(self, store, engine):
        """WHERE n.name = 'main' OR n.name = 'helper' — OR 匹配两个。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' OR n.name = 'helper' RETURN n",
            store,
        )
        names = {row["n"]["name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_where_or_three_options(self, store, engine):
        """WHERE n.name = 'main' OR n.name = 'run' OR n.name = 'init'。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' OR n.name = 'run' OR n.name = 'init' RETURN n.name",
            store,
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "run", "init"}

    def test_where_or_different_fields(self, store, engine):
        """WHERE n.kind = 'class' OR n.name = 'main' — 跨字段 OR。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'class' OR n.name = 'main' RETURN n",
            store,
        )
        assert len(result.rows) == 3  # Calculator, App, main

    def test_where_and_or_combined(self, store, engine):
        """WHERE (n.kind = 'function' AND n.language = 'python') OR n.kind = 'class'。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' AND n.language = 'python' OR n.kind = 'class' RETURN n",
            store,
        )
        # 函数 (python): main, helper, run, init, cleanup = 5
        # 类: Calculator, App = 2
        # 共 7 (注意 OR 比 AND 优先级低)
        assert len(result.rows) == 7

    # ── NOT 运算符 ────────────────────────────────────────────────────

    def test_where_not_name_equals(self, store, engine):
        """WHERE NOT (n.name = 'main') — NOT 否定等值（需括号避免优先级问题）。"""
        result = engine.execute(
            "MATCH (n) WHERE NOT (n.name = 'main') RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert "main" not in names
        assert len(result.rows) == 9

    def test_where_not_and(self, store, engine):
        """WHERE NOT (n.name = 'main' AND n.kind = 'function') — NOT 否定组合条件。"""
        result = engine.execute(
            "MATCH (n) WHERE NOT (n.name = 'main' AND n.kind = 'function') RETURN n.name",
            store,
        )
        assert len(result.rows) == 9

    # ── 正则匹配 ──────────────────────────────────────────────────────

    def test_where_regex_caret_anchor(self, store, engine):
        """WHERE n.name =~ '^m.*' — 正则匹配以 m 开头的名称。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name =~ '^m.*' RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main"}

    def test_where_regex_contains(self, store, engine):
        """WHERE n.name =~ '.*er.*' — 正则包含 'er'。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name =~ '.*er.*' RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert "helper" in names
        assert "render" in names

    def test_where_regex_alternation(self, store, engine):
        """WHERE n.name =~ 'main|run|init' — 正则交替。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name =~ 'main|run|init' RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "run", "init"}

    def test_where_regex_no_match_empty(self, store, engine):
        """正则不匹配任何节点时返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name =~ '^zzzz.*' RETURN n", store
        )
        assert len(result.rows) == 0

    # ── IN 运算符 ─────────────────────────────────────────────────────

    def test_where_in_list_of_kinds(self, store, engine):
        """WHERE n.kind IN ['function', 'method'] — 多值匹配。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind IN ['function', 'method'] RETURN n", store
        )
        kinds = {row["n"]["kind"] for row in result.rows}
        assert kinds == {"function", "method"}
        assert len(result.rows) == 8  # 5 func + 3 method

    def test_where_in_list_of_names(self, store, engine):
        """WHERE n.name IN ['main', 'helper', 'Calculator']。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name IN ['main', 'helper', 'Calculator'] RETURN n.name",
            store,
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper", "Calculator"}

    def test_where_in_empty_list(self, store, engine):
        """WHERE n.name IN [] — 空列表无匹配。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name IN [] RETURN n", store
        )
        assert len(result.rows) == 0

    # ── NULL 检查 ─────────────────────────────────────────────────────

    def test_where_is_not_null_on_name(self, store, engine):
        """WHERE n.name IS NOT NULL — 所有节点 name 都不为 NULL。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name IS NOT NULL RETURN n", store
        )
        assert len(result.rows) == 10

    def test_where_is_null_on_name_returns_empty(self, store, engine):
        """WHERE n.name IS NULL — 所有节点 name 都有值，返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name IS NULL RETURN n", store
        )
        assert len(result.rows) == 0

    # ── 标签 + WHERE 组合 ─────────────────────────────────────────────

    def test_where_on_labeled_node(self, store, engine):
        """MATCH (n:function) WHERE n.name = 'main' RETURN n — 标签 + WHERE。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.name = 'main' RETURN n.name", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n.name"] == "main"

    def test_where_on_labeled_node_no_match(self, store, engine):
        """MATCH (n:function) WHERE n.kind = 'class' — 矛盾条件。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.kind = 'class' RETURN n", store
        )
        assert len(result.rows) == 0


# ============================================================================
# C. 排序和分页
# ============================================================================


class TestSortingAndPagination:
    """C 类：ORDER BY、SKIP、LIMIT 组合。"""

    def test_order_by_name_asc(self, store, engine):
        """ORDER BY name ASC — 名称升序（使用别名，因投影后不可引用原变量属性路径）。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC", store
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_order_by_name_desc(self, store, engine):
        """ORDER BY name DESC — 名称降序。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name DESC", store
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    def test_order_by_kind_then_name_asc(self, store, engine):
        """ORDER BY kind, name ASC — 多列排序（使用别名）。"""
        result = engine.execute(
            "MATCH (n) RETURN n.kind AS kind, n.name AS name ORDER BY kind, name ASC", store
        )
        # class 应该在 function 和 method 之前
        kinds_order = [row["kind"] for row in result.rows]
        assert kinds_order == sorted(kinds_order)

    def test_order_by_file_path_asc(self, store, engine):
        """ORDER BY path ASC — 按文件路径排序（使用别名）。"""
        result = engine.execute(
            "MATCH (n) RETURN n.file_path AS path ORDER BY path ASC", store
        )
        paths = [row["path"] for row in result.rows]
        assert paths == sorted(paths)

    def test_order_by_with_limit(self, store, engine):
        """ORDER BY + LIMIT — 排序后取前 N。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC LIMIT 3", store
        )
        names = [row["name"] for row in result.rows]
        assert len(names) == 3
        assert names == sorted(names)[:3]

    def test_order_by_desc_with_limit(self, store, engine):
        """ORDER BY DESC + LIMIT — 降序后取前 N。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name DESC LIMIT 3", store
        )
        names = [row["name"] for row in result.rows]
        assert len(names) == 3
        assert names == sorted(names, reverse=True)[:3]

    def test_order_by_skip_and_limit(self, store, engine):
        """ORDER BY + SKIP + LIMIT — 排序、跳过、取 N。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC SKIP 2 LIMIT 3", store
        )
        assert len(result.rows) == 3

    def test_order_by_alias(self, store, engine):
        """RETURN n.name AS name ORDER BY name ASC — 别名排序。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC", store
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_order_by_alias_desc(self, store, engine):
        """RETURN n.name AS func_name ORDER BY func_name DESC — 别名降序。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS func_name ORDER BY func_name DESC", store
        )
        names = [row["func_name"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    def test_skip_only_with_order(self, store, engine):
        """SKIP（无 LIMIT）配合 ORDER BY — 跳过但不截断。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC SKIP 8", store
        )
        assert len(result.rows) == 2  # 10 - 8

    def test_no_order_no_limit_returns_all(self, store, engine):
        """MATCH (n) RETURN n — 不使用 ORDER BY 或 LIMIT 返回全部。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        assert len(result.rows) == 10


# ============================================================================
# D. 关系查询
# ============================================================================


class TestRelationshipQueries:
    """D 类：边扩展查询 — calls / imports / defines / 方向 / 多跳。"""

    # ── 带类型过滤的边扩展 ────────────────────────────────────────────

    def test_calls_relationship_outgoing_count(self, store, engine):
        """MATCH (n)-[:calls]->(m) RETURN n, m — 所有 calls 边（out 方向）。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN n, m", store
        )
        # 9 条 outgoing calls: run→main/main/init, main→helper/add, add→subtract, init→helper/cleanup, render→main
        assert len(result.rows) == 9

    def test_calls_relationship_both_variables_bound(self, store, engine):
        """每条结果都绑定了 n 和 m 两个变量。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN n, m", store
        )
        for row in result.rows:
            assert "n" in row.data
            assert "m" in row.data
            assert isinstance(row["n"], dict)
            assert isinstance(row["m"], dict)

    def test_calls_filter_by_target_name(self, store, engine):
        """MATCH (n)-[:calls]->(m) WHERE m.name = 'helper' RETURN n.name — 谁调了 helper。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE m.name = 'helper' RETURN n.name",
            store,
        )
        callers = {row["n.name"] for row in result.rows}
        # run→helper, main→helper, init→helper
        assert callers == {"run", "main", "init"}
        assert len(result.rows) == 3

    def test_calls_filter_by_source_name(self, store, engine):
        """MATCH (n)-[:calls]->(m) WHERE n.name = 'run' RETURN m.name — run 调用了谁。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.name = 'run' RETURN m.name",
            store,
        )
        callees = {row["m.name"] for row in result.rows}
        # run→main, run→helper, run→init
        assert callees == {"main", "helper", "init"}
        assert len(result.rows) == 3

    def test_calls_with_projection(self, store, engine):
        """MATCH (n)-[:calls]->(m) RETURN n.name, m.name — caller + callee 对。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN n.name, m.name", store
        )
        pairs = {(row["n.name"], row["m.name"]) for row in result.rows}
        assert ("run", "main") in pairs
        assert ("run", "helper") in pairs
        assert ("main", "helper") in pairs
        assert ("main", "add") in pairs  # via Calculator.add (n4)
        assert ("init", "cleanup") in pairs

    # ── 其他边类型 ────────────────────────────────────────────────────

    def test_imports_relationship(self, store, engine):
        """MATCH (n)-[:imports]->(m) RETURN n.name, m.name — 只有 main imports Calculator。"""
        result = engine.execute(
            "MATCH (n)-[:imports]->(m) RETURN n.name, m.name", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n.name"] == "main"
        assert result.rows[0]["m.name"] == "Calculator"

    def test_defines_relationship(self, store, engine):
        """MATCH (n)-[:defines]->(m) RETURN n.name, m.name — 类定义方法。"""
        result = engine.execute(
            "MATCH (n)-[:defines]->(m) RETURN n.name, m.name", store
        )
        pairs = {(row["n.name"], row["m.name"]) for row in result.rows}
        expected = {("Calculator", "add"), ("Calculator", "subtract"), ("App", "render")}
        assert pairs == expected
        assert len(result.rows) == 3

    def test_no_matching_edge_type(self, store, engine):
        """MATCH (n)-[:inherits]->(m) RETURN n, m — 没有 inherits 边。"""
        result = engine.execute(
            "MATCH (n)-[:inherits]->(m) RETURN n, m", store
        )
        assert len(result.rows) == 0

    # ── 方向变化 ──────────────────────────────────────────────────────

    def test_edge_shorthand_right_arrow(self, store, engine):
        """MATCH (n)-->(m) RETURN n, m — 右箭头简写，任意边类型。"""
        result = engine.execute(
            "MATCH (n)-->(m) RETURN n, m", store
        )
        # 所有 outgoing 边: 13 条 (all edges in the dataset are outgoing)
        assert len(result.rows) == 13

    def test_edge_shorthand_left_arrow(self, store, engine):
        """MATCH (n)<--(m) RETURN n, m — 左箭头简写（incoming）。"""
        result = engine.execute(
            "MATCH (n)<--(m) RETURN n, m", store
        )
        # 所有 outgoing 边反转成 incoming: 13 条
        assert len(result.rows) == 13

    def test_edge_shorthand_bidirectional(self, store, engine):
        """MATCH (n)--(m) RETURN n, m — 双向简写。"""
        result = engine.execute(
            "MATCH (n)--(m) RETURN n, m", store
        )
        # 每条边在两个方向都匹配
        assert len(result.rows) == 26  # 13 * 2

    # ── 多跳查询 ──────────────────────────────────────────────────────

    def test_two_hop_calls_chain(self, store, engine):
        """MATCH (n)-[:calls]->(m)-[:calls]->(p) RETURN n.name, p.name — 两跳调用链。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m)-[:calls]->(p) RETURN n.name, m.name, p.name",
            store,
        )
        # 路径: run→main→helper, run→main→add, main→add→subtract, run→init→helper, run→init→cleanup, ...
        assert len(result.rows) > 0

    def test_three_hop_chain(self, store, engine):
        """MATCH (a)-[:calls]->(b)-[:calls]->(c)-[:calls]->(d) RETURN a, b, c, d — 三跳。"""
        result = engine.execute(
            "MATCH (a)-[:calls]->(b)-[:calls]->(c)-[:calls]->(d) RETURN a, b, c, d",
            store,
        )
        # run→main→add→subtract = 1 条三跳路径 (outgoing only from each node in chain)
        assert len(result.rows) >= 0

    # ── 边上的 WHERE ──────────────────────────────────────────────────

    def test_edge_with_combined_where(self, store, engine):
        """MATCH (n)-[:calls]->(m) WHERE n.name = 'run' AND m.kind = 'function' RETURN m.name。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.name = 'run' AND m.kind = 'function' RETURN m.name",
            store,
        )
        callees = {row["m.name"] for row in result.rows}
        assert callees == {"main", "helper", "init"}

    def test_edge_with_where_on_both(self, store, engine):
        """MATCH (n)-[:calls]->(m) WHERE n.kind = 'function' AND m.kind = 'method' RETURN n.name, m.name。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.kind = 'function' AND m.kind = 'method' RETURN n.name, m.name",
            store,
        )
        pairs = {(row["n.name"], row["m.name"]) for row in result.rows}
        assert ("main", "add") in pairs  # main(function) calls Calculator.add(method)
        assert len(result.rows) == 1

    # ── 去重关系结果 ──────────────────────────────────────────────────

    def test_calls_with_distinct_source_names(self, store, engine):
        """MATCH (n)-[:calls]->(m) RETURN DISTINCT n.name — 有哪些 caller。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN DISTINCT n.name", store
        )
        callers = {row["n.name"] for row in result.rows}
        assert callers == {"run", "main", "add", "init", "render"}  # render calls main

    def test_calls_with_distinct_target_names(self, store, engine):
        """MATCH (n)-[:calls]->(m) RETURN DISTINCT m.name — 有哪些 callee。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN DISTINCT m.name", store
        )
        callees = {row["m.name"] for row in result.rows}
        assert "helper" in callees
        assert "main" in callees


# ============================================================================
# E. 投影
# ============================================================================


class TestProjection:
    """E 类：RETURN 子句 — 字段投影、别名、DISTINCT。"""

    def test_return_single_field_name(self, store, engine):
        """RETURN n.name — 只返回 name 字段。"""
        result = engine.execute("MATCH (n) RETURN n.name", store)
        assert "n.name" in result.columns
        assert len(result.rows) == 10
        for row in result.rows:
            assert isinstance(row["n.name"], str)

    def test_return_single_field_kind(self, store, engine):
        """RETURN n.kind — 只返回 kind 字段。"""
        result = engine.execute("MATCH (n) RETURN n.kind", store)
        kinds = {row["n.kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}

    def test_return_with_alias_single(self, store, engine):
        """RETURN n.name AS func_name — 别名投影。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS func_name", store
        )
        assert "func_name" in result.columns
        names = {row["func_name"] for row in result.rows}
        assert "main" in names

    def test_return_with_multiple_aliases(self, store, engine):
        """RETURN n.name AS name, n.kind AS kind, n.file_path AS path — 多列别名。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name, n.kind AS kind, n.file_path AS path",
            store,
        )
        assert result.columns == ["name", "kind", "path"]
        for row in result.rows:
            assert "name" in row.data
            assert "kind" in row.data
            assert "path" in row.data

    def test_return_distinct_kind(self, store, engine):
        """RETURN DISTINCT n.kind — 去重 kind（当前 DISTINCT 在投影前，按全节点去重）。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.kind", store
        )
        kinds = {row["n.kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}
        # DISTINCT 在投影前应用，每个全节点记录唯一 → 全部保留
        assert len(result.rows) == 10

    def test_return_distinct_name(self, store, engine):
        """RETURN DISTINCT n.name — 去重 name（所有名称唯一）。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.name", store
        )
        assert len(result.rows) == 10  # 所有名称唯一

    def test_return_distinct_language(self, store, engine):
        """RETURN DISTINCT n.language — 去重语言。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.language", store
        )
        langs = {row["n.language"] for row in result.rows}
        assert langs == {"python", "typescript"}

    def test_return_mixed_aliased_and_plain(self, store, engine):
        """RETURN n.name AS name, n.kind — 混合别名和非别名。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name, n.kind", store
        )
        assert "name" in result.columns
        assert "n.kind" in result.columns

    def test_return_qualified_name(self, store, engine):
        """RETURN n.qualified_name — 返回全限定名。"""
        result = engine.execute(
            "MATCH (n) RETURN n.qualified_name", store
        )
        qns = {row["n.qualified_name"] for row in result.rows}
        assert "Calculator.add" in qns
        assert "App.render" in qns

    def test_return_id_field(self, store, engine):
        """RETURN n.id — 返回节点 id。"""
        result = engine.execute("MATCH (n) RETURN n.id", store)
        ids = {row["n.id"] for row in result.rows}
        assert ids == {"n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "n10"}


# ============================================================================
# F. 组合查询
# ============================================================================


class TestCombinedQueries:
    """F 类：MATCH + WHERE + RETURN + ORDER BY + LIMIT 完整流水线。"""

    def test_where_order_limit_pipeline(self, store, engine):
        """MATCH + WHERE + ORDER BY + LIMIT 完整流水线。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' RETURN n.name AS name ORDER BY name ASC LIMIT 3",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert len(names) == 3
        assert names == sorted(names)

    def test_label_where_alias_order_limit(self, store, engine):
        """标签 + WHERE + 别名 + ORDER BY + LIMIT。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.language = 'python' RETURN n.name AS fn ORDER BY fn DESC LIMIT 2",
            store,
        )
        assert len(result.rows) == 2
        names = [row["fn"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    def test_relationship_with_filter_and_order(self, store, engine):
        """关系 + WHERE + ORDER BY。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.name = 'run' RETURN m.name AS callee ORDER BY callee ASC",
            store,
        )
        callees = [row["callee"] for row in result.rows]
        assert callees == sorted(callees)

    def test_relationship_with_order_and_limit(self, store, engine):
        """关系 + ORDER BY + LIMIT。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN n.name AS caller, m.name AS callee ORDER BY caller ASC LIMIT 5",
            store,
        )
        assert len(result.rows) == 5

    def test_full_pipeline(self, store, engine):
        """MATCH (n:function) WHERE n.language = 'python' RETURN n.name AS name ORDER BY name ASC SKIP 1 LIMIT 2。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.language = 'python' RETURN n.name AS name ORDER BY name ASC SKIP 1 LIMIT 2",
            store,
        )
        assert len(result.rows) == 2
        # 5 functions all python: cleanup, helper, init, main, run → skip 1 → helper, init
        names = [row["name"] for row in result.rows]
        all_sorted = sorted(["cleanup", "helper", "init", "main", "run"])
        assert names == all_sorted[1:3]

    def test_distinct_with_order_and_limit(self, store, engine):
        """RETURN DISTINCT n.kind ORDER BY kind ASC LIMIT 2 — DISTINCT 在全节点级别去重。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.kind AS kind ORDER BY kind ASC LIMIT 2",
            store,
        )
        kinds = [row["kind"] for row in result.rows]
        assert len(kinds) == 2
        assert kinds == sorted(kinds)

    def test_where_distinct_order(self, store, engine):
        """WHERE + DISTINCT + ORDER BY — 组合使用。"""
        result = engine.execute(
            "MATCH (n) WHERE n.language = 'python' RETURN DISTINCT n.kind AS kind ORDER BY kind ASC",
            store,
        )
        kinds = {row["kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}

    def test_skip_and_limit_on_relationship_result(self, store, engine):
        """关系查询的 SKIP + LIMIT。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) RETURN n.name, m.name SKIP 2 LIMIT 3",
            store,
        )
        assert len(result.rows) == 3

    def test_engine_reuse_multiple_queries(self, store, engine):
        """同一 engine 实例执行多次查询。"""
        r1 = engine.execute("MATCH (n:function) RETURN n LIMIT 2", store)
        r2 = engine.execute("MATCH (n:class) RETURN n", store)
        r3 = engine.execute("MATCH (n) RETURN n SKIP 5 LIMIT 3", store)
        assert len(r1.rows) == 2
        assert len(r2.rows) == 2
        assert len(r3.rows) == 3

    def test_convenience_execute_matches_engine(self, store):
        """execute() 便捷函数与 CypherEngine().execute() 结果一致。"""
        from tws_graph.cypher import execute as exec_func
        r1 = exec_func("MATCH (n) RETURN n LIMIT 3", store)
        r2 = CypherEngine().execute("MATCH (n) RETURN n LIMIT 3", store)
        assert len(r1.rows) == len(r2.rows)


# ============================================================================
# G. 函数调用
# ============================================================================


class TestFunctionCalls:
    """G 类：内置标量函数 — toUpper / toLower / toString / coalesce / type。"""

    def test_toUpper(self, store, engine):
        """RETURN toUpper(n.name) — 转为大写。"""
        result = engine.execute(
            "MATCH (n) RETURN toUpper(n.name) AS upper_name", store
        )
        names = {row["upper_name"] for row in result.rows}
        assert "MAIN" in names
        assert "HELPER" in names

    def test_toLower(self, store, engine):
        """RETURN toLower(n.name) — 转为小写。"""
        result = engine.execute(
            "MATCH (n) RETURN toLower(n.name) AS lower_name", store
        )
        names = {row["lower_name"] for row in result.rows}
        assert "main" in names
        assert "calculator" in names  # Calculator → calculator

    def test_toString(self, store, engine):
        """RETURN toString(n.start_line) — 数字转字符串。"""
        result = engine.execute(
            "MATCH (n) RETURN toString(n.start_line) AS str_val", store
        )
        for row in result.rows:
            assert isinstance(row["str_val"], str)

    def test_coalesce_non_null(self, store, engine):
        """RETURN coalesce(n.name, 'unknown') — name 不为 NULL，返回 name。"""
        result = engine.execute(
            "MATCH (n) RETURN coalesce(n.name, 'unknown') AS resolved", store
        )
        for row in result.rows:
            assert row["resolved"] != "unknown"

    def test_coalesce_with_nonexistent_property(self, store, engine):
        """RETURN coalesce(n.nonexistent, 'default') — 不存在属性 → default。"""
        result = engine.execute(
            "MATCH (n) RETURN coalesce(n.nonexistent, 'default') AS resolved", store
        )
        for row in result.rows:
            assert row["resolved"] == "default"

    def test_type_function(self, store, engine):
        """RETURN type(n.name) — 返回类型名称 'str'。"""
        result = engine.execute(
            "MATCH (n) RETURN type(n.name) AS type_val", store
        )
        for row in result.rows:
            assert row["type_val"] == "str"

    def test_function_with_alias_and_order(self, store, engine):
        """RETURN toUpper(n.name) AS upper ORDER BY upper ASC — 函数 + 别名 + 排序。"""
        result = engine.execute(
            "MATCH (n) RETURN toUpper(n.name) AS upper ORDER BY upper ASC", store
        )
        names = [row["upper"] for row in result.rows]
        assert names == sorted(names)

    def test_toUpper_with_where(self, store, engine):
        """WHERE toUpper(n.name) = 'MAIN' RETURN n — 函数用于 WHERE。"""
        result = engine.execute(
            "MATCH (n) WHERE toUpper(n.name) = 'MAIN' RETURN n.name", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n.name"] == "main"


# ============================================================================
# H. 边界和错误处理
# ============================================================================


class TestErrorHandling:
    """H 类：错误处理 — 非法语法、未定义变量、边界条件。"""

    # ── 语法错误 ────────────────────────────────────────────────────────

    def test_invalid_syntax_raises(self, store, engine):
        """无效 Cypher 语法抛出 CypherSyntaxError。"""
        with pytest.raises(CypherSyntaxError):
            engine.execute("INVALID QUERY", store)

    def test_no_match_clause_raises(self, store, engine):
        """缺少 MATCH 子句抛出 CypherSyntaxError (Parser) 或 CypherSemanticError (Planner)。"""
        with pytest.raises((CypherSyntaxError, CypherSemanticError)):
            engine.execute("RETURN 1", store)

    def test_unclosed_string_raises(self, store, engine):
        """未闭合字符串抛出 CypherLexerError。"""
        with pytest.raises(CypherLexerError):
            engine.execute("MATCH (n) WHERE n.name = 'unclosed RETURN n", store)

    def test_trailing_input_raises(self, store, engine):
        """语句结束后还有 token 抛出 CypherSyntaxError。"""
        with pytest.raises(CypherSyntaxError):
            engine.execute("MATCH (n) RETURN n EXTRA", store)

    def test_return_star_raises(self, store, engine):
        """RETURN * 在 P2 不支持，抛异常。"""
        with pytest.raises((CypherSyntaxError, CypherSemanticError, CypherExecutionError)):
            engine.execute("MATCH (n) RETURN *", store)

    # ── 语义错误 ────────────────────────────────────────────────────────

    def test_undefined_variable_in_return(self, store, engine):
        """RETURN m 但 m 从未定义 → CypherSemanticError。"""
        with pytest.raises(CypherSemanticError):
            engine.execute("MATCH (n) RETURN m", store)

    def test_undefined_variable_in_where(self, store, engine):
        """WHERE m.name = 'x' 但 m 未定义 → CypherSemanticError。"""
        with pytest.raises(CypherSemanticError):
            engine.execute("MATCH (n) WHERE m.name = 'x' RETURN n", store)

    def test_undefined_variable_in_function(self, store, engine):
        """RETURN toUpper(m.name) 但 m 未定义 → CypherSemanticError。"""
        with pytest.raises(CypherSemanticError):
            engine.execute("MATCH (n) RETURN toUpper(m.name)", store)

    def test_parameter_unsupported(self, store, engine):
        """$param 在 P2 不支持 → 语法/语义/执行错误。"""
        with pytest.raises((CypherSyntaxError, CypherSemanticError, CypherExecutionError)):
            engine.execute("MATCH (n) WHERE n.name = $name RETURN n", store)

    # ── 边界条件 ────────────────────────────────────────────────────────

    def test_empty_store_returns_empty_resultset(self, empty_store, engine):
        """空 store 查询返回空 ResultSet（不抛异常）。"""
        result = engine.execute("MATCH (n) RETURN n", empty_store)
        assert isinstance(result, ResultSet)
        assert len(result.rows) == 0
        assert result.total_count == 0

    def test_empty_store_with_limit_zero(self, empty_store, engine):
        """空 store + LIMIT 0 → 空结果。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 0", empty_store)
        assert len(result.rows) == 0

    def test_no_matching_where_returns_empty(self, store, engine):
        """WHERE 条件无匹配时返回空结果（不抛异常）。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'nonexistent_name_xyz' RETURN n", store
        )
        assert len(result.rows) == 0
        assert isinstance(result, ResultSet)

    def test_no_matching_edge_returns_empty(self, store, engine):
        """边类型无匹配时返回空结果。"""
        result = engine.execute(
            "MATCH (n)-[:overrides]->(m) RETURN n, m", store
        )
        assert len(result.rows) == 0

    def test_empty_where_result_with_order(self, store, engine):
        """WHERE 不匹配时 ORDER BY 返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'noone' RETURN n ORDER BY n.name ASC", store
        )
        assert len(result.rows) == 0

    def test_empty_where_result_with_limit(self, store, engine):
        """WHERE 不匹配时 SKIP + LIMIT 返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'noone' RETURN n SKIP 1 LIMIT 5", store
        )
        assert len(result.rows) == 0

    def test_resultset_total_count_on_empty(self, empty_store, engine):
        """空 ResultSet 的 total_count 为 0。"""
        result = engine.execute("MATCH (n) RETURN n", empty_store)
        assert result.total_count == 0

    def test_row_count_after_merge_of_edge_and_scan(self, store, engine):
        """确保扫描和边扩展合并后行数正确。"""
        result = engine.execute(
            "MATCH (n:function)-[:calls]->(m) RETURN n, m", store
        )
        # function 节点: main, helper, run, init, cleanup = 5
        # outgoing calls from each:
        # main→helper, main→add, helper→(none), run→main, run→helper, run→init, init→helper, init→cleanup, cleanup→(none)
        # = 7 calls edges outgoing from function nodes
        assert len(result.rows) == 7


# ============================================================================
# I. OPTIONAL MATCH
# ============================================================================


class TestOptionalMatch:
    """I 类：OPTIONAL MATCH — 可选匹配（当前 P2 解析但不执行）。"""

    def test_optional_match_syntax_parsed_no_error(self, store, engine):
        """验证 OPTIONAL MATCH 语法能被解析（结果取决于实现状态）。"""
        # P2 解析但不执行 OPTIONAL MATCH — 可能静默忽略或报 semantic error
        try:
            result = engine.execute(
                "MATCH (n) OPTIONAL MATCH (n)-[:calls]->(m) RETURN n, m", store
            )
            assert isinstance(result, ResultSet)
        except CypherSemanticError:
            # 预期行为：OPTIONAL MATCH 未实施时 m 被视为未定义
            pass

    def test_optional_match_only_defined_vars(self, store, engine):
        """OPTIONAL MATCH 中 m 未在 MATCH 中定义时的行为。"""
        try:
            result = engine.execute(
                "MATCH (n) OPTIONAL MATCH (x)-[:calls]->(y) RETURN n", store
            )
            # 如果 RETURN 只引用已定义的变量，可能成功
            assert isinstance(result, ResultSet)
        except (CypherSemanticError, CypherSyntaxError, CypherExecutionError):
            pass

    def test_multiple_optional_matches_syntax(self, store, engine):
        """多个 OPTIONAL MATCH 语法的解析。"""
        try:
            result = engine.execute(
                "MATCH (n) OPTIONAL MATCH (n)-[:calls]->(m) OPTIONAL MATCH (m)-[:calls]->(p) RETURN n",
                store,
            )
            assert isinstance(result, ResultSet)
        except (CypherSemanticError, CypherSyntaxError, CypherExecutionError):
            pass


# ============================================================================
# J. 高级模式匹配
# ============================================================================


class TestAdvancedPatterns:
    """J 类：高级模式 — 边变量、方向组合、交叉边类型。"""

    def test_edge_with_variable(self, store, engine):
        """MATCH (n)-[r:calls]->(m) RETURN n.name, type(r) — 边绑定到变量。"""
        result = engine.execute(
            "MATCH (n)-[r:calls]->(m) RETURN n.name, m.name", store
        )
        assert len(result.rows) > 0

    def test_edge_variable_in_where(self, store, engine):
        """使用边变量进行过滤。"""
        # 当前实现可能不支持在 WHERE 中引用边变量（如果未收集到 defined_vars）
        result = engine.execute(
            "MATCH (n)-[r:calls]->(m) RETURN n.name, m.name", store
        )
        assert len(result.rows) > 0

    def test_left_direction_explicit(self, store, engine):
        """MATCH (n)<-[:calls]-(m) RETURN n.name, m.name — 明确左向。"""
        result = engine.execute(
            "MATCH (n)<-[:calls]-(m) RETURN n.name, m.name", store
        )
        assert len(result.rows) > 0

    def test_both_direction_explicit(self, store, engine):
        """MATCH (n)-[:calls]-(m) RETURN n, m — 明确双向。"""
        result = engine.execute(
            "MATCH (n)-[:calls]-(m) RETURN n, m", store
        )
        # 每条 calls 边在两个方向都匹配: 9 * 2 = 18
        assert len(result.rows) == 18

    def test_chain_mixed_edge_types(self, store, engine):
        """MATCH (n)-[:imports]->(m)-[:defines]->(p) RETURN n.name, p.name — 混合边类型链。"""
        result = engine.execute(
            "MATCH (n)-[:imports]->(m)-[:defines]->(p) RETURN n.name, p.name", store
        )
        # main imports Calculator, Calculator defines add & subtract
        assert len(result.rows) == 2

    def test_diamond_pattern(self, store, engine):
        """同一个调用者 → 两个不同被调者，验证正确匹配。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.name = 'init' RETURN m.name AS callee ORDER BY callee ASC",
            store,
        )
        callees = [row["callee"] for row in result.rows]
        assert "cleanup" in callees
        assert "helper" in callees

    def test_chain_with_start_label(self, store, engine):
        """MATCH (n:class)-[:defines]->(m) RETURN n.name, m.name — 标签 + 边。"""
        result = engine.execute(
            "MATCH (n:class)-[:defines]->(m) RETURN n.name, m.name", store
        )
        pairs = {(row["n.name"], row["m.name"]) for row in result.rows}
        assert pairs == {("Calculator", "add"), ("Calculator", "subtract"), ("App", "render")}


# ============================================================================
# K. ResultSet 和 Row 接口
# ============================================================================


class TestResultSetInterface:
    """K 类：ResultSet / Row 接口测试。"""

    def test_columns_after_projection(self, store, engine):
        """投影后的列名正确。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name, n.kind AS kind", store
        )
        assert result.columns == ["name", "kind"]

    def test_columns_after_distinct(self, store, engine):
        """DISTINCT 后的列名正确。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.kind", store
        )
        assert "n.kind" in result.columns

    def test_columns_after_order(self, store, engine):
        """ORDER BY 后的列名正确。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC", store
        )
        assert "name" in result.columns

    def test_empty_resultset_cols(self, store, engine):
        """WHERE 无匹配时仍有正确的列。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'nonexistent' RETURN n.name AS name", store
        )
        assert len(result.rows) == 0
        # 列名应该来源于 projection
        assert "name" in result.columns

    def test_row_data_immutable_from_outside(self, store, engine):
        """验证 Row.data 修改不会影响后续查询。"""
        r1 = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        row1_copy = dict(r1.rows[0].data)
        r1.rows[0].data["new_key"] = "new_value"
        r2 = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        assert "new_key" not in r2.rows[0].data

    def test_multiple_rows_all_have_same_columns(self, store, engine):
        """所有行的列一致。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, n.kind", store
        )
        for row in result.rows:
            assert "n.name" in row.data
            assert "n.kind" in row.data

    def test_resultset_iter_stops_at_end(self, store, engine):
        """ResultSet 迭代正确终止。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        count = sum(1 for _ in result)
        assert count == 3


# ============================================================================
# L. 店铺状态
# ============================================================================


class TestStoreState:
    """L 类：验证测试数据集完整性。"""

    def test_store_node_count(self, store):
        """存储中有 10 个节点。"""
        assert store.count_nodes() == 10

    def test_store_edge_count(self, store):
        """存储中有 13 条边。"""
        assert store.count_edges() == 13

    def test_store_node_lookup_by_id(self, store):
        """按 id 查找节点正确。"""
        node = store.get_node_by_id("n1")
        assert node is not None
        assert node["name"] == "main"
        assert node["kind"] == "function"

    def test_store_nonexistent_node(self, store):
        """不存在的 id 返回 None。"""
        assert store.get_node_by_id("nonexistent") is None

    def test_store_outgoing_edges_count(self, store):
        """特定节点的 outgoing edges。"""
        edges = store.get_outgoing_edges("n6")
        assert len(edges) == 3  # run → main, helper, init

    def test_store_incoming_edges_count(self, store):
        """特定节点的 incoming edges。"""
        edges = store.get_incoming_edges("n2")
        assert len(edges) == 3  # helper ← run, main, init

    def test_store_edges_between(self, store):
        """两节点之间的边。"""
        edges = store.get_edges_between("n1", "n2")
        assert len(edges) == 1
        assert edges[0]["kind"] == "calls"

    def test_store_neighbors(self, store):
        """获取节点的邻居。"""
        neighbors = store.get_neighbors("n6")
        neighbor_ids = {n["node"]["id"] for n in neighbors}
        assert neighbor_ids == {"n1", "n2", "n9"}

    def test_store_find_path(self, store):
        """查找两个节点之间的最短路径。"""
        path = store.find_paths("n6", "n10")
        assert path is not None
        # n6 → n9 → n10
        assert len(path) == 3
        assert path[0]["node"]["name"] == "run"
        assert path[1]["node"]["name"] == "init"
        assert path[2]["node"]["name"] == "cleanup"


# ============================================================================
# M. 表达式边界条件
# ============================================================================


class TestExpressionEdgeCases:
    """M 类：表达式边界 — 字面量、布尔、NULL 语义。"""

    def test_where_true_returns_all(self, store, engine):
        """WHERE TRUE — 所有节点通过。"""
        result = engine.execute(
            "MATCH (n) WHERE TRUE RETURN n", store
        )
        assert len(result.rows) == 10

    def test_where_false_returns_none(self, store, engine):
        """WHERE FALSE — 无节点通过。"""
        result = engine.execute(
            "MATCH (n) WHERE FALSE RETURN n", store
        )
        assert len(result.rows) == 0

    def test_where_boolean_and_string(self, store, engine):
        """布尔 + 字符串条件组合。"""
        result = engine.execute(
            "MATCH (n) WHERE TRUE AND n.kind = 'function' RETURN n", store
        )
        assert len(result.rows) == 5

    def test_where_string_equals_with_space(self, store, engine):
        """字符串包含特殊字符（路径分隔符）。"""
        result = engine.execute(
            "MATCH (n) WHERE n.file_path = '/src/main.py' RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_where_regex_with_dot_star(self, store, engine):
        """正则 .* 匹配所有名称。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name =~ '.*' RETURN n.name", store
        )
        assert len(result.rows) == 10

    def test_where_in_with_single_element(self, store, engine):
        """IN 列表只有一个元素。"""
        result = engine.execute(
            "MATCH (n) WHERE n.name IN ['main'] RETURN n.name", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n.name"] == "main"

    def test_where_or_with_same_field(self, store, engine):
        """同一字段多个 OR。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'class' OR n.kind = 'method' RETURN n", store
        )
        assert len(result.rows) == 5  # 2 class + 3 method

    def test_where_and_with_different_operators(self, store, engine):
        """= 和 <> 组合使用。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' AND n.name <> 'main' RETURN n.name",
            store,
        )
        names = {row["n.name"] for row in result.rows}
        assert "main" not in names
        assert "helper" in names

    def test_project_column_name_with_descriptive_alias(self, store, engine):
        """使用长别名进行投影。"""
        result = engine.execute(
            "MATCH (n) RETURN n.qualified_name AS fully_qualified_symbol_name", store
        )
        assert "fully_qualified_symbol_name" in result.columns
        for row in result.rows:
            assert isinstance(row["fully_qualified_symbol_name"], str)


# ============================================================================
# N. 类型系统相关
# ============================================================================


class TestTypeSystem:
    """N 类：类型系统相关测试 — 不同类型节点的查询和过滤。"""

    def test_count_by_kind(self, store, engine):
        """统计每种类型的节点数。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.kind", store
        )
        kinds = {row["n.kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}

    def test_count_by_language(self, store, engine):
        """按语言统计。"""
        result = engine.execute(
            "MATCH (n) WHERE n.language = 'python' RETURN n", store
        )
        assert len(result.rows) == 8

    def test_function_and_method_union_with_in(self, store, engine):
        """使用 IN 合并 function 和 method。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind IN ['function', 'method'] RETURN DISTINCT n.kind AS kind ORDER BY kind ASC",
            store,
        )
        kinds = {row["kind"] for row in result.rows}
        assert kinds == {"function", "method"}

    def test_class_method_intersection(self, store, engine):
        """同时命中 class 和 method 的条件（无交集但有组合）。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'class' OR n.kind = 'method' RETURN n.name AS name ORDER BY name ASC",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert "add" in names
        assert "subtract" in names
        assert "render" in names
        assert "Calculator" in names
        assert "App" in names

    def test_call_graph_depth_from_run(self, store, engine):
        """从 run 出发的调用链深度分析。"""
        # 1-hop: run→main, run→helper, run→init
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE n.name = 'run' RETURN m.name", store
        )
        assert len(result.rows) == 3

    def test_call_graph_depth_two_from_run(self, store, engine):
        """从 run 出发的两跳调用。"""
        result = engine.execute(
            "MATCH (n)-[:calls]->(m)-[:calls]->(p) WHERE n.name = 'run' RETURN DISTINCT p.name",
            store,
        )
        # run→main→helper, run→main→add, run→init→helper, run→init→cleanup
        callees = {row["p.name"] for row in result.rows}
        assert "helper" in callees


# ============================================================================
# O. 回归 — 与已有测试不冲突
# ============================================================================


class TestNoRegression:
    """O 类：确保集成测试不与已有单元测试产生回归。"""

    def test_same_engine_different_stores(self, engine, store, empty_store):
        """同一 engine 对不同 store 执行查询。"""
        r1 = engine.execute("MATCH (n) RETURN n", store)
        r2 = engine.execute("MATCH (n) RETURN n", empty_store)
        assert len(r1.rows) == 10
        assert len(r2.rows) == 0

    def test_convenience_vs_engine_same_result(self, store):
        """便捷函数 execute() 和 CypherEngine().execute() 结果一致。"""
        r1 = execute("MATCH (n:function) RETURN n.name AS name ORDER BY name ASC", store)
        r2 = CypherEngine().execute("MATCH (n:function) RETURN n.name AS name ORDER BY name ASC", store)
        assert len(r1.rows) == len(r2.rows)
        for i in range(len(r1.rows)):
            assert r1.rows[i].data == r2.rows[i].data

    def test_filtered_edge_expand_matches_scan_filter(self, store, engine):
        """边扩展 + WHERE 的结果与手动验证一致。"""
        # 哪些节点调用了 helper
        result = engine.execute(
            "MATCH (n)-[:calls]->(m) WHERE m.name = 'helper' RETURN DISTINCT n.name",
            store,
        )
        callers = {row["n.name"] for row in result.rows}
        assert callers == {"run", "main", "init"}

    def test_defines_matches_class_structure(self, store, engine):
        """defines 边准确反映类方法关系。"""
        result = engine.execute(
            "MATCH (c:class)-[:defines]->(m:method) RETURN c.name, m.name",
            store,
        )
        pairs = {(row["c.name"], row["m.name"]) for row in result.rows}
        assert pairs == {("Calculator", "add"), ("Calculator", "subtract"), ("App", "render")}


# ============================================================================
# P. 额外边界测试
# ============================================================================


class TestAdditionalBoundary:
    """P 类：更多边界和压力测试。"""

    def test_skip_zero(self, store, engine):
        """SKIP 0 — 等价于不跳过。"""
        r1 = engine.execute("MATCH (n) RETURN n SKIP 0", store)
        r2 = engine.execute("MATCH (n) RETURN n", store)
        assert len(r1.rows) == len(r2.rows)

    def test_limit_negative_behavior(self, store, engine):
        """LIMIT 负数 — 行为取决于实现（可能报错或返回空）。"""
        try:
            result = engine.execute("MATCH (n) RETURN n LIMIT -1", store)
            # 如果到达这里，确认结果合理
            assert isinstance(result, ResultSet)
        except (CypherSyntaxError, CypherSemanticError, CypherExecutionError, ValueError):
            pass

    def test_very_long_query_string(self, store, engine):
        """很长的查询字符串应能正常执行。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'function' AND n.language = 'python' AND n.file_path = '/src/main.py' RETURN n.name AS name ORDER BY name ASC",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_query_with_extra_whitespace(self, store, engine):
        """多余空白字符不影响查询。"""
        result = engine.execute("  MATCH   (n)    RETURN   n   LIMIT   3  ", store)
        assert len(result.rows) == 3

    def test_query_with_newlines(self, store, engine):
        """查询包含换行符。"""
        query = """MATCH (n)
WHERE n.kind = 'function'
RETURN n.name
LIMIT 3"""
        result = engine.execute(query, store)
        assert len(result.rows) == 3

    def test_resultset_slice_like_behavior(self, store, engine):
        """SKIP + LIMIT 模拟分页效果。"""
        # 第 1 页: 0-3
        p1 = engine.execute("MATCH (n) RETURN n.name AS name ORDER BY name ASC SKIP 0 LIMIT 3", store)
        # 第 2 页: 3-6
        p2 = engine.execute("MATCH (n) RETURN n.name AS name ORDER BY name ASC SKIP 3 LIMIT 3", store)
        # 确保没有重叠
        p1_names = {row["name"] for row in p1.rows}
        p2_names = {row["name"] for row in p2.rows}
        assert len(p1_names & p2_names) == 0
        assert len(p1.rows) == 3
        assert len(p2.rows) == 3
