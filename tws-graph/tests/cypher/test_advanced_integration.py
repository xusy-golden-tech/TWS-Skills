"""P3 高级特性集成测试 — 端到端测试 UNION、UNWIND、CASE WHEN、EXISTS/NOT EXISTS、WITH。

覆盖 P3 Task 5 验收标准：≥ 100 个集成测试，全部通过，无回归。

测试数据：复用 P2 测试数据集（10 节点 + 12 边），预填充 MemoryStore。
"""

from __future__ import annotations

import pytest

from tws_graph.cypher import CypherEngine, ResultSet, Row
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

    10 个节点 + 12 条边，覆盖 function / method / class 三种 kind。
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
# A. UNION (≥ 20 tests)
# ============================================================================


class TestUnion:
    """A 类：UNION / UNION ALL — 结果合并、去重、与各子句组合。"""

    # ── 基本 UNION ────────────────────────────────────────────────────

    def test_union_function_and_class(self, store, engine):
        """MATCH (n:function) RETURN n.name UNION MATCH (n:class) RETURN n.name — 合并函数和类。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "run", "init", "cleanup", "Calculator", "App"}
        assert len(result.rows) == 7

    def test_union_function_and_method(self, store, engine):
        """MATCH (n:function) RETURN n.name UNION MATCH (n:method) RETURN n.name — 合并函数和方法。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:method) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "run", "init", "cleanup", "add", "subtract", "render"}
        assert len(result.rows) == 8

    def test_union_all_function_and_class(self, store, engine):
        """UNION ALL 不去重 — MATCH (n:function) RETURN n.kind UNION ALL MATCH (n:class) RETURN n.kind。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.kind AS kind "
            "UNION ALL MATCH (n:class) RETURN n.kind AS kind",
            store,
        )
        kinds = [row["kind"] for row in result.rows]
        assert len(result.rows) == 7  # 5 functions + 2 classes = 7
        assert kinds.count("function") == 5
        assert kinds.count("class") == 2

    def test_union_deduplication(self, store, engine):
        """UNION 去重验证 — 两侧都有相同 kind 时去重。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.kind AS kind "
            "UNION MATCH (n:class) RETURN n.kind AS kind",
            store,
        )
        kinds = {row["kind"] for row in result.rows}
        assert kinds == {"function", "class"}
        assert len(result.rows) == 2  # 去重后只剩 2 种 kind

    def test_union_all_no_dedup_same_values(self, store, engine):
        """UNION ALL 保留所有重复 — 两侧都有 function 标签的节点。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.kind AS kind "
            "UNION ALL MATCH (n:function) RETURN n.kind AS kind",
            store,
        )
        assert len(result.rows) == 10  # 5 left + 5 right = 10

    def test_union_three_branches(self, store, engine):
        """UNION 3 分支 — function UNION class UNION method。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name "
            "UNION MATCH (n:method) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {
            "main", "helper", "run", "init", "cleanup",
            "Calculator", "App",
            "add", "subtract", "render",
        }
        assert len(result.rows) == 10

    def test_union_all_three_branches(self, store, engine):
        """UNION ALL 3 分支 — 全部保留。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION ALL MATCH (n:class) RETURN n.name AS name "
            "UNION ALL MATCH (n:method) RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 10  # 5 + 2 + 3

    def test_union_columns_match(self, store, engine):
        """UNION 结果的 columns 正确。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name",
            store,
        )
        assert result.columns == ["name"]

    def test_union_resultset_is_valid(self, store, engine):
        """UNION 返回的结果是 ResultSet。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name",
            store,
        )
        assert isinstance(result, ResultSet)

    def test_union_empty_left_branch(self, store, engine):
        """UNION 左侧无匹配时正常返回右侧结果。"""
        result = engine.execute(
            "MATCH (n:module) RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"Calculator", "App"}
        assert len(result.rows) == 2

    def test_union_empty_right_branch(self, store, engine):
        """UNION 右侧无匹配时正常返回左侧结果。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:module) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert len(names) == 5

    def test_union_empty_both_branches(self, store, engine):
        """UNION 两侧都无匹配时返回空。"""
        result = engine.execute(
            "MATCH (n:module) RETURN n.name AS name "
            "UNION MATCH (n:interface) RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 0

    # ── UNION + WHERE ─────────────────────────────────────────────────

    def test_union_with_where_both_sides(self, store, engine):
        """UNION 两侧都有 WHERE。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.language = 'python' RETURN n.name AS name "
            "UNION MATCH (n:class) WHERE n.language = 'typescript' RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "run", "init", "cleanup", "App"}
        assert len(result.rows) == 6

    def test_union_where_left_only(self, store, engine):
        """UNION 仅左侧有 WHERE。"""
        result = engine.execute(
            "MATCH (n:function) WHERE n.name = 'main' RETURN n.name AS name "
            "UNION MATCH (n:class) RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"main", "Calculator", "App"}

    def test_union_where_right_only(self, store, engine):
        """UNION 仅右侧有 WHERE。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name "
            "UNION MATCH (n:class) WHERE n.name = 'App' RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert "App" in names
        assert "main" in names

    # ── UNION + ORDER BY / LIMIT ──────────────────────────────────────

    def test_union_with_order_by_each_branch(self, store, engine):
        """UNION 每分支有 ORDER BY。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name ORDER BY name ASC "
            "UNION MATCH (n:class) RETURN n.name AS name ORDER BY name ASC",
            store,
        )
        # UNION 去重后，输出顺序是函数先、类后（或反之）
        names = [row["name"] for row in result.rows]
        # 函数按字母排序，类按字母排序
        func_sorted = sorted(["cleanup", "helper", "init", "main", "run"])
        class_sorted = sorted(["App", "Calculator"])
        all_sorted = func_sorted + class_sorted
        assert names == all_sorted or names == class_sorted + func_sorted

    def test_union_with_limit_each_branch(self, store, engine):
        """UNION ALL 每分支有 LIMIT。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name LIMIT 2 "
            "UNION ALL MATCH (n:class) RETURN n.name AS name LIMIT 1",
            store,
        )
        assert len(result.rows) == 3

    def test_union_with_skip_each_branch(self, store, engine):
        """UNION ALL 每分支有 SKIP。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name ORDER BY name ASC SKIP 2 "
            "UNION MATCH (n:method) RETURN n.name AS name SKIP 1",
            store,
        )
        # 5 functions skip 2 → 3 remaining; 3 methods skip 1 → 2 remaining
        assert len(result.rows) <= 5  # UNION 可能去重

    # ── UNION 边界 ────────────────────────────────────────────────────

    def test_union_with_projection_different_expressions(self, store, engine):
        """UNION 两侧用不同的投影表达式但别名相同。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.qualified_name AS qname "
            "UNION MATCH (n:class) RETURN n.qualified_name AS qname",
            store,
        )
        qnames = {row["qname"] for row in result.rows}
        assert "main" in qnames
        assert "Calculator" in qnames

    def test_union_all_with_multiple_columns(self, store, engine):
        """UNION ALL 多列结果。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name, n.kind AS kind LIMIT 2 "
            "UNION ALL MATCH (n:class) RETURN n.name AS name, n.kind AS kind",
            store,
        )
        assert len(result.rows) == 4  # 2 + 2
        assert result.columns == ["name", "kind"]
        for row in result.rows:
            assert "name" in row.data
            assert "kind" in row.data

    def test_union_multiple_columns(self, store, engine):
        """UNION 多列去重。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name, n.language AS lang "
            "UNION MATCH (n:class) RETURN n.name AS name, n.language AS lang",
            store,
        )
        assert len(result.rows) == 7  # 5 + 2, all unique pairs
        assert result.columns == ["name", "lang"]


# ============================================================================
# B. UNWIND (≥ 15 tests)
# ============================================================================


class TestUnwind:
    """B 类：UNWIND — 列表展开为多行、与其他子句组合。"""

    # ── 基本 UNWIND ───────────────────────────────────────────────────

    def test_unwind_literal_list(self, store, engine):
        """UNWIND [1, 2, 3] AS x RETURN x — 展开为 3 行。"""
        result = engine.execute("UNWIND [1, 2, 3] AS x RETURN x", store)
        values = [row["x"] for row in result.rows]
        assert values == [1, 2, 3]
        assert len(result.rows) == 3

    def test_unwind_string_list(self, store, engine):
        """UNWIND ['a', 'b', 'c'] AS x RETURN x — 展开字符串列表。"""
        result = engine.execute("UNWIND ['a', 'b', 'c'] AS x RETURN x", store)
        values = {row["x"] for row in result.rows}
        assert values == {"a", "b", "c"}
        assert len(result.rows) == 3

    def test_unwind_empty_list(self, store, engine):
        """UNWIND [] AS x RETURN x — 空列表返回 0 行。"""
        result = engine.execute("UNWIND [] AS x RETURN x", store)
        assert len(result.rows) == 0

    def test_unwind_single_element(self, store, engine):
        """UNWIND [42] AS x RETURN x — 单元素列表。"""
        result = engine.execute("UNWIND [42] AS x RETURN x", store)
        assert len(result.rows) == 1
        assert result.rows[0]["x"] == 42

    def test_unwind_columns(self, store, engine):
        """UNWIND 结果的列名正确。"""
        result = engine.execute("UNWIND [1, 2, 3] AS x RETURN x", store)
        assert result.columns == ["x"]

    def test_unwind_resultset_type(self, store, engine):
        """UNWIND 返回 ResultSet。"""
        result = engine.execute("UNWIND [1, 2] AS x RETURN x", store)
        assert isinstance(result, ResultSet)

    # ── UNWIND 值类型 ─────────────────────────────────────────────────

    def test_unwind_boolean_list(self, store, engine):
        """UNWIND [TRUE, FALSE, TRUE] AS x RETURN x — 布尔列表。"""
        result = engine.execute("UNWIND [TRUE, FALSE, TRUE] AS x RETURN x", store)
        values = [row["x"] for row in result.rows]
        assert values == [True, False, True]

    def test_unwind_null_containing_list(self, store, engine):
        """UNWIND [1, NULL, 3] AS x RETURN x — 含 NULL 的列表。"""
        result = engine.execute("UNWIND [1, NULL, 3] AS x RETURN x", store)
        values = [row["x"] for row in result.rows]
        assert values == [1, None, 3]

    def test_unwind_large_list(self, store, engine):
        """UNWIND 大列表 — 50 个元素的列表。"""
        elems = ", ".join(str(i) for i in range(50))
        result = engine.execute(f"UNWIND [{elems}] AS x RETURN x", store)
        assert len(result.rows) == 50
        assert result.rows[0]["x"] == 0
        assert result.rows[49]["x"] == 49

    def test_unwind_nested_list(self, store, engine):
        """UNWIND [[1,2], [3,4]] AS x RETURN x — 嵌套列表（内层列表作为一个元素）。"""
        result = engine.execute("UNWIND [[1, 2], [3, 4]] AS x RETURN x", store)
        assert len(result.rows) == 2
        # 内层列表被当作单个元素返回

    def test_unwind_with_alias(self, store, engine):
        """UNWIND [1,2,3] AS val RETURN val AS value — 别名投影。"""
        result = engine.execute("UNWIND [1, 2, 3] AS val RETURN val AS value", store)
        assert result.columns == ["value"]
        values = [row["value"] for row in result.rows]
        assert values == [1, 2, 3]

    # ── UNWIND + ORDER BY / LIMIT ─────────────────────────────────────

    def test_unwind_with_order_by(self, store, engine):
        """UNWIND [3, 1, 2] AS x RETURN x ORDER BY x ASC — UNWIND + 排序。"""
        result = engine.execute("UNWIND [3, 1, 2] AS x RETURN x ORDER BY x ASC", store)
        values = [row["x"] for row in result.rows]
        assert values == [1, 2, 3]

    def test_unwind_with_order_by_desc(self, store, engine):
        """UNWIND + ORDER BY DESC — 降序排序。"""
        result = engine.execute("UNWIND [1, 2, 3] AS x RETURN x ORDER BY x DESC", store)
        values = [row["x"] for row in result.rows]
        assert values == [3, 2, 1]

    def test_unwind_with_limit(self, store, engine):
        """UNWIND [10, 20, 30, 40, 50] AS x RETURN x LIMIT 3 — UNWIND + LIMIT。"""
        result = engine.execute("UNWIND [10, 20, 30, 40, 50] AS x RETURN x LIMIT 3", store)
        assert len(result.rows) == 3

    def test_unwind_with_skip(self, store, engine):
        """UNWIND [1, 2, 3, 4, 5] AS x RETURN x SKIP 2 — UNWIND + SKIP。"""
        result = engine.execute("UNWIND [1, 2, 3, 4, 5] AS x RETURN x SKIP 2", store)
        assert len(result.rows) == 3
        assert result.rows[0]["x"] == 3

    def test_unwind_with_distinct(self, store, engine):
        """UNWIND [1, 2, 2, 3, 3, 3] AS x RETURN DISTINCT x — UNWIND + DISTINCT。"""
        result = engine.execute("UNWIND [1, 2, 2, 3, 3, 3] AS x RETURN DISTINCT x", store)
        values = {row["x"] for row in result.rows}
        assert values == {1, 2, 3}
        assert len(result.rows) == 3

    def test_unwind_non_list_value_string(self, store, engine):
        """UNWIND 非列表值（字符串）— 跳过该行。"""
        result = engine.execute("UNWIND 'hello' AS x RETURN x", store)
        # 字符串不是列表，跳过 → 0 行
        assert len(result.rows) == 0

    def test_unwind_non_list_value_integer(self, store, engine):
        """UNWIND 非列表值（整数）— 跳过该行。"""
        result = engine.execute("UNWIND 42 AS x RETURN x", store)
        assert len(result.rows) == 0

    # ── UNWIND with MATCH ─────────────────────────────────────────────

    def test_unwind_with_match_no_list_property(self, store, engine):
        """MATCH (n) UNWIND n.name AS letter — 名字是字符串不是列表，每行被跳过。"""
        result = engine.execute("MATCH (n) UNWIND n.name AS letter RETURN n.name, letter", store)
        # n.name 是字符串而非列表 → 所有行跳过
        assert len(result.rows) == 0


# ============================================================================
# C. CASE WHEN (≥ 20 tests)
# ============================================================================


class TestCaseWhen:
    """C 类：CASE WHEN / CASE — 搜索式和简单式分支表达。"""

    # ── 搜索式 CASE WHEN ──────────────────────────────────────────────

    def test_case_when_search_basic(self, store, engine):
        """RETURN CASE WHEN kind='function' THEN 'func' ELSE 'other' END。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'function' THEN 'func' ELSE 'other' END AS type",
            store,
        )
        func_count = sum(1 for row in result.rows if row["type"] == "func")
        other_count = sum(1 for row in result.rows if row["type"] == "other")
        assert func_count == 5  # 5 functions
        assert other_count == 5  # 2 class + 3 method

    def test_case_when_search_multiple_when(self, store, engine):
        """CASE 多 WHEN — function/class/method 三分支。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'function' THEN 'func' "
            "WHEN n.kind = 'class' THEN 'cls' "
            "WHEN n.kind = 'method' THEN 'mtd' "
            "ELSE 'unknown' END AS category",
            store,
        )
        categories = {row["category"] for row in result.rows}
        assert categories == {"func", "cls", "mtd"}

    def test_case_when_with_else(self, store, engine):
        """CASE WHEN 有 ELSE 分支。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.language = 'python' THEN 'py' ELSE 'other' END AS lang",
            store,
        )
        py_count = sum(1 for row in result.rows if row["lang"] == "py")
        other_count = sum(1 for row in result.rows if row["lang"] == "other")
        assert py_count == 8  # 8 python nodes
        assert other_count == 2  # 2 typescript nodes

    def test_case_when_no_else_returns_null(self, store, engine):
        """CASE WHEN 无 ELSE — 不匹配返回 NULL。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name, "
            "CASE WHEN n.kind = 'function' THEN 'func' END AS label",
            store,
        )
        # 只有 5 个 function 节点的 label 是 'func'，其余 5 个是 NULL
        func_count = sum(1 for row in result.rows if row["label"] == "func")
        null_count = sum(1 for row in result.rows if row["label"] is None)
        assert func_count == 5
        assert null_count == 5

    def test_case_when_boolean_condition(self, store, engine):
        """CASE WHEN 布尔条件 — WHEN TRUE THEN ...。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN TRUE THEN 'yes' ELSE 'no' END AS answer",
            store,
        )
        for row in result.rows:
            assert row["answer"] == "yes"

    def test_case_when_numeric_comparison(self, store, engine):
        """CASE WHEN 数值比较 — start_line > 15。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.start_line > 15 THEN 'late' ELSE 'early' END AS position",
            store,
        )
        late_names = {row["n.name"] for row in result.rows if row["position"] == "late"}
        early_names = {row["n.name"] for row in result.rows if row["position"] == "early"}
        assert "helper" in late_names  # start_line=25
        assert "subtract" in late_names  # start_line=20
        assert "main" in early_names  # start_line=10

    # ── 简单 CASE ─────────────────────────────────────────────────────

    def test_case_simple_form(self, store, engine):
        """CASE n.kind WHEN 'function' THEN 'func' ... END — 简单式 CASE。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name, "
            "CASE n.language WHEN 'python' THEN 'py' WHEN 'typescript' THEN 'ts' ELSE 'unknown' END AS lang",
            store,
        )
        for row in result.rows:
            assert row["lang"] == "py"  # all functions are python

    def test_case_simple_multiple_values(self, store, engine):
        """CASE n.kind 多值匹配。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE n.kind WHEN 'function' THEN 'F' WHEN 'class' THEN 'C' WHEN 'method' THEN 'M' ELSE '?' END AS code",
            store,
        )
        codes = {row["code"] for row in result.rows}
        assert codes == {"F", "C", "M"}

    def test_case_simple_no_match_default(self, store, engine):
        """CASE 简单式无匹配使用 ELSE。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE n.kind WHEN 'module' THEN 'mod' ELSE 'other' END AS label",
            store,
        )
        for row in result.rows:
            assert row["label"] == "other"

    def test_case_simple_no_match_no_default(self, store, engine):
        """CASE 简单式无匹配且无 ELSE — 返回 NULL。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE n.kind WHEN 'module' THEN 'mod' END AS label",
            store,
        )
        for row in result.rows:
            assert row["label"] is None

    # ── CASE + 其他子句 ───────────────────────────────────────────────

    def test_case_with_order_by(self, store, engine):
        """CASE + ORDER BY — 按 CASE 结果排序。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'function' THEN 1 WHEN n.kind = 'class' THEN 2 ELSE 3 END AS priority "
            "ORDER BY priority ASC",
            store,
        )
        priorities = [row["priority"] for row in result.rows]
        assert priorities == sorted(priorities)
        # 前 5 个是 function (priority=1)
        assert len([p for p in priorities if p == 1]) == 5

    def test_case_with_where(self, store, engine):
        """CASE + WHERE — WHERE 过滤后使用 CASE。"""
        result = engine.execute(
            "MATCH (n) WHERE n.language = 'python' RETURN n.name, "
            "CASE WHEN n.kind = 'function' THEN 'func' ELSE 'non-func' END AS category",
            store,
        )
        assert len(result.rows) == 8  # 8 python nodes
        func_count = sum(1 for row in result.rows if row["category"] == "func")
        assert func_count == 5

    def test_case_with_limit(self, store, engine):
        """CASE + LIMIT — 限制结果行数。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'function' THEN 'func' ELSE 'other' END AS type LIMIT 3",
            store,
        )
        assert len(result.rows) == 3

    def test_case_with_distinct(self, store, engine):
        """CASE + DISTINCT RETURN — 去重。"""
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT "
            "CASE WHEN n.kind = 'function' THEN 'function' WHEN n.kind = 'class' THEN 'class' ELSE 'method' END AS kind",
            store,
        )
        kinds = {row["kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}

    def test_case_nested_conditions(self, store, engine):
        """CASE 复杂条件 — 同时判断 kind 和 language。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'function' AND n.language = 'python' THEN 'py_func' "
            "WHEN n.kind = 'class' THEN 'cls' "
            "ELSE 'other' END AS category",
            store,
        )
        py_func = sum(1 for row in result.rows if row["category"] == "py_func")
        cls = sum(1 for row in result.rows if row["category"] == "cls")
        assert py_func == 5
        assert cls == 2

    def test_case_with_and_or_conditions(self, store, engine):
        """CASE WHEN 使用 OR 条件。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.kind = 'class' OR n.kind = 'method' THEN 'structured' "
            "ELSE 'free' END AS style",
            store,
        )
        structured = sum(1 for row in result.rows if row["style"] == "structured")
        assert structured == 5  # 2 class + 3 method

    def test_case_with_not_condition(self, store, engine):
        """CASE WHEN NOT — 否定条件。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN NOT (n.kind = 'function') THEN 'non_func' "
            "ELSE 'func' END AS type",
            store,
        )
        non_func = sum(1 for row in result.rows if row["type"] == "non_func")
        assert non_func == 5

    def test_case_with_comparison_operators(self, store, engine):
        """CASE WHEN n.start_line < 10 THEN 'top' WHEN n.start_line >= 20 THEN 'bottom' ELSE 'mid' END。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, n.start_line, "
            "CASE WHEN n.start_line < 10 THEN 'top' "
            "WHEN n.start_line >= 20 THEN 'bottom' "
            "ELSE 'mid' END AS position",
            store,
        )
        top = sum(1 for row in result.rows if row["position"] == "top")
        bottom = sum(1 for row in result.rows if row["position"] == "bottom")
        mid = sum(1 for row in result.rows if row["position"] == "mid")
        assert top + bottom + mid == 10
        # start_line < 10: run(1), App(1), render(5), init(1), Calculator(5) = 5
        assert top >= 3  # at least these have start_line < 10

    def test_case_with_is_null(self, store, engine):
        """CASE WHEN n.start_line IS NOT NULL THEN 'has' ELSE 'none' END。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, "
            "CASE WHEN n.start_line IS NOT NULL THEN 'has_line' ELSE 'no_line' END AS status",
            store,
        )
        for row in result.rows:
            assert row["status"] == "has_line"

    def test_case_with_function_call(self, store, engine):
        """CASE WHEN toUpper(n.name) = 'MAIN' THEN 'entry' ELSE 'other' END。"""
        result = engine.execute(
            "MATCH (n) RETURN "
            "CASE WHEN toUpper(n.name) = 'MAIN' THEN 'entry' ELSE 'other' END AS type",
            store,
        )
        entry_count = sum(1 for row in result.rows if row["type"] == "entry")
        assert entry_count == 1  # only main


# ============================================================================
# D. EXISTS / NOT EXISTS (≥ 15 tests)
# ============================================================================


class TestExists:
    """D 类：EXISTS / NOT EXISTS — 子查询过滤器。"""

    # ── EXISTS 基本 ───────────────────────────────────────────────────

    def test_exists_with_calls(self, store, engine):
        """MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name — 有 calls 边的节点。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        # run(→main,helper,init), main(→helper,add), add(→subtract), init(→helper,cleanup), render(→main)
        assert "run" in names
        assert "main" in names
        assert "add" in names
        assert "init" in names
        assert "render" in names
        # helper, cleanup, Calculator, App, subtract have NO outgoing calls → excluded
        assert "helper" not in names
        assert "Calculator" not in names
        assert len(result.rows) == 5

    def test_exists_no_match(self, store, engine):
        """EXISTS 子查询无匹配 — 返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:inherits]->(m) } RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 0

    def test_exists_with_defines(self, store, engine):
        """MATCH (n) WHERE EXISTS { MATCH (n)-[:defines]->(m) } RETURN n.name — 有 defines 边的节点。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:defines]->(m) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"Calculator", "App"}

    def test_exists_resultset_type(self, store, engine):
        """EXISTS 返回 ResultSet。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            store,
        )
        assert isinstance(result, ResultSet)

    def test_exists_columns(self, store, engine):
        """EXISTS 查询后列名正确。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name, n.kind AS kind",
            store,
        )
        assert "name" in result.columns
        assert "kind" in result.columns

    # ── NOT EXISTS ────────────────────────────────────────────────────

    def test_not_exists_with_calls(self, store, engine):
        """MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name — 无 calls 边的节点。"""
        result = engine.execute(
            "MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert "helper" in names  # no outgoing calls
        assert "Calculator" in names  # only defines
        assert "cleanup" in names  # no outgoing calls
        assert "subtract" in names  # no outgoing calls
        assert "App" in names  # only defines
        assert len(result.rows) == 5

    def test_not_exists_all_pass(self, store, engine):
        """NOT EXISTS 无任何节点有 inherits 边 → 所有节点通过。"""
        result = engine.execute(
            "MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:inherits]->(m) } RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 10

    def test_not_exists_no_match(self, store, engine):
        """NOT EXISTS 所有节点都有 calls 边 → 但只有部分节点无 calls，验证准确。"""
        result = engine.execute(
            "MATCH (n:function) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        # functions: main(calls→helper,add), helper(no), run(calls→main,helper,init), init(calls→helper,cleanup), cleanup(no)
        assert names == {"helper", "cleanup"}

    # ── EXISTS 子查询变体 ─────────────────────────────────────────────

    def test_exists_with_two_hop_subquery(self, store, engine):
        """MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m)-[:calls]->(p) } RETURN n.name — 两跳子查询。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m)-[:calls]->(p) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        # run→main→helper, run→main→add, main→add→subtract, run→init→helper, run→init→cleanup
        assert "run" in names
        assert "main" in names

    def test_exists_with_label_in_subquery(self, store, engine):
        """EXISTS 子查询内有标签过滤。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m:function) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        # run→main(function), run→helper(function), main→helper(function), init→helper(function), render→main(function)
        # add→subtract(method, not function) -- add comes from n4 which has outgoing calls to n5 (method)
        assert "run" in names
        assert "main" in names
        assert "init" in names
        assert "render" in names

    def test_exists_subquery_without_return(self, store, engine):
        """EXISTS 子查询无 RETURN — 解析正确执行。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            store,
        )
        assert len(result.rows) > 0

    def test_exists_with_direction_in_subquery(self, store, engine):
        """EXISTS 子查询明确方向（左向）。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (m)-[:calls]->(n) } RETURN n.name AS name",
            store,
        )
        names = {row["name"] for row in result.rows}
        # 被调用者(incoming calls): n1(main), n2(helper), n4(add), n5(subtract), n9(init), n10(cleanup)
        assert "main" in names
        assert "helper" in names

    # ── EXISTS 组合 ──────────────────────────────────────────────────

    def test_exists_with_where_and_order(self, store, engine):
        """EXISTS + WHERE + ORDER BY — 组合使用。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "RETURN n.name AS name ORDER BY name ASC",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_exists_with_limit(self, store, engine):
        """EXISTS + LIMIT — 限制结果数量。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "RETURN n.name AS name LIMIT 2",
            store,
        )
        assert len(result.rows) == 2

    def test_exists_with_project_multiple_columns(self, store, engine):
        """EXISTS 投影多列。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "RETURN n.name AS name, n.kind AS kind",
            store,
        )
        for row in result.rows:
            assert "name" in row.data
            assert "kind" in row.data

    def test_exists_empty_store(self, empty_store, engine):
        """EXISTS 在空 store 上执行 — 返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name",
            empty_store,
        )
        assert len(result.rows) == 0


# ============================================================================
# E. WITH 子句 (≥ 15 tests)
# ============================================================================


class TestWithClause:
    """E 类：WITH — 中间投影、过滤、聚合。"""

    # ── WITH 基本 ─────────────────────────────────────────────────────

    def test_with_basic_projection(self, store, engine):
        """MATCH (n) WITH n.name AS name RETURN name — 中间投影。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name",
            store,
        )
        assert len(result.rows) == 10
        assert result.columns == ["name"]
        names = {row["name"] for row in result.rows}
        assert "main" in names
        assert "helper" in names

    def test_with_multi_field_projection(self, store, engine):
        """MATCH (n) WITH n.name AS name, n.kind AS kind RETURN name, kind — 多字段投影。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name, n.kind AS kind RETURN name, kind",
            store,
        )
        assert len(result.rows) == 10
        assert result.columns == ["name", "kind"]
        for row in result.rows:
            assert "name" in row.data
            assert "kind" in row.data

    def test_with_where_after_with(self, store, engine):
        """MATCH (n) WITH n.name AS name WHERE name = 'main' RETURN name — WITH + WHERE。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name WHERE name = 'main' RETURN name",
            store,
        )
        assert len(result.rows) == 1
        assert result.rows[0]["name"] == "main"

    def test_with_where_multiple_conditions(self, store, engine):
        """WITH ... WHERE 多条件。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name, n.kind AS kind "
            "WHERE kind = 'function' AND name <> 'main' "
            "RETURN name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert "main" not in names
        assert len(result.rows) == 4  # helper, run, init, cleanup

    def test_with_where_or_condition(self, store, engine):
        """WITH ... WHERE 使用 OR。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name WHERE name = 'main' OR name = 'helper' RETURN name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_with_return_distinct(self, store, engine):
        """MATCH (n) WITH n.kind AS kind RETURN DISTINCT kind — WITH 后 DISTINCT RETURN。"""
        result = engine.execute(
            "MATCH (n) WITH n.kind AS kind RETURN DISTINCT kind",
            store,
        )
        kinds = {row["kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}
        assert len(result.rows) == 3

    def test_with_order_by(self, store, engine):
        """MATCH (n) WITH n.name AS name RETURN name ORDER BY name ASC — WITH 后 ORDER BY RETURN。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name ORDER BY name ASC",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_with_limit(self, store, engine):
        """MATCH (n) WITH n.name AS name RETURN name LIMIT 3 — WITH 后 LIMIT。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name LIMIT 3",
            store,
        )
        assert len(result.rows) == 3

    def test_with_skip(self, store, engine):
        """MATCH (n) WITH n.name AS name RETURN name SKIP 5 — WITH 后 SKIP。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name SKIP 5",
            store,
        )
        assert len(result.rows) == 5

    def test_with_skip_limit(self, store, engine):
        """MATCH (n) WITH n.name AS name RETURN name SKIP 2 LIMIT 3 — WITH + SKIP + LIMIT。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name SKIP 2 LIMIT 3",
            store,
        )
        assert len(result.rows) == 3

    # ── WITH + 函数/表达式 ────────────────────────────────────────────

    def test_with_function_expression(self, store, engine):
        """MATCH (n) WITH toUpper(n.name) AS uname RETURN uname — WITH 中使用函数。"""
        result = engine.execute(
            "MATCH (n) WITH toUpper(n.name) AS uname RETURN uname",
            store,
        )
        names = {row["uname"] for row in result.rows}
        assert "MAIN" in names
        assert "HELPER" in names

    def test_with_coalesce_expression(self, store, engine):
        """MATCH (n) WITH coalesce(n.name, 'unknown') AS resolved RETURN resolved — WITH 中使用 coalesce。"""
        result = engine.execute(
            "MATCH (n) WITH coalesce(n.name, 'unknown') AS resolved RETURN resolved",
            store,
        )
        for row in result.rows:
            assert row["resolved"] != "unknown"

    def test_with_numeric_expression(self, store, engine):
        """MATCH (n) WITH n.start_line + 1 AS next_line RETURN next_line。"""
        result = engine.execute(
            "MATCH (n) WITH n.start_line + 1 AS next_line RETURN next_line",
            store,
        )
        for row in result.rows:
            assert isinstance(row["next_line"], (int, float))

    def test_with_where_on_aliased_function(self, store, engine):
        """WITH + WHERE 在函数结果上过滤。"""
        result = engine.execute(
            "MATCH (n) WITH toUpper(n.name) AS uname WHERE uname = 'MAIN' RETURN uname",
            store,
        )
        assert len(result.rows) == 1
        assert result.rows[0]["uname"] == "MAIN"

    def test_with_complex_pipeline(self, store, engine):
        """MATCH + WITH 多字段 + WHERE + ORDER BY + LIMIT 完整流水线。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name, n.kind AS kind "
            "WHERE kind = 'function' "
            "RETURN name ORDER BY name ASC LIMIT 3",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert len(names) == 3
        assert names == sorted(names)[:3]

    def test_with_before_order_by_alias(self, store, engine):
        """WITH 产生的别名可在 RETURN 的 ORDER BY 中引用。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name RETURN name ORDER BY name DESC",
            store,
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names, reverse=True)


# ============================================================================
# F. 组合特性 (≥ 15 tests)
# ============================================================================


class TestCombinedFeatures:
    """F 类：多种 P3 特性组合 — UNION + CASE、UNWIND + CASE、WITH + EXISTS 等。"""

    # ── UNION + CASE ─────────────────────────────────────────────────

    def test_union_with_case_in_each_branch(self, store, engine):
        """UNION 每分支内用 CASE。"""
        result = engine.execute(
            "MATCH (n:function) RETURN CASE WHEN n.name = 'main' THEN 'entry' ELSE 'func' END AS label "
            "UNION MATCH (n:class) RETURN CASE WHEN n.name = 'App' THEN 'app_cls' ELSE 'cls' END AS label",
            store,
        )
        labels = {row["label"] for row in result.rows}
        assert "entry" in labels
        assert "func" in labels
        assert "app_cls" in labels
        assert "cls" in labels

    def test_union_all_with_case(self, store, engine):
        """UNION ALL + CASE 组合。"""
        result = engine.execute(
            "MATCH (n:function) RETURN CASE WHEN n.language = 'python' THEN n.name ELSE 'non_py' END AS name "
            "UNION ALL MATCH (n:method) RETURN CASE WHEN n.language = 'python' THEN n.name ELSE 'non_py' END AS name",
            store,
        )
        assert len(result.rows) == 8  # 5 func + 3 method

    def test_union_with_order_by_and_case(self, store, engine):
        """UNION + ORDER BY + CASE 组合。"""
        result = engine.execute(
            "MATCH (n:function) RETURN CASE WHEN n.name = 'main' THEN 1 ELSE 2 END AS priority "
            "UNION MATCH (n:class) RETURN CASE WHEN n.name = 'App' THEN 0 ELSE 3 END AS priority",
            store,
        )
        priorities = {row["priority"] for row in result.rows}
        assert 0 in priorities or 1 in priorities

    # ── UNWIND + CASE ─────────────────────────────────────────────────

    def test_unwind_with_case(self, store, engine):
        """UNWIND [1,2,3] AS x RETURN CASE WHEN x > 1 THEN 'big' ELSE 'small' END — UNWIND + CASE。"""
        result = engine.execute(
            "UNWIND [1, 2, 3] AS x RETURN CASE WHEN x > 1 THEN 'big' ELSE 'small' END AS size",
            store,
        )
        sizes = [row["size"] for row in result.rows]
        assert sizes == ["small", "big", "big"]

    def test_unwind_with_case_and_order(self, store, engine):
        """UNWIND + CASE + ORDER BY 组合。"""
        result = engine.execute(
            "UNWIND [3, 1, 2] AS x RETURN x, "
            "CASE WHEN x > 1 THEN 'high' ELSE 'low' END AS level ORDER BY x ASC",
            store,
        )
        values = [row["x"] for row in result.rows]
        assert values == [1, 2, 3]

    def test_unwind_with_case_and_limit(self, store, engine):
        """UNWIND + CASE + LIMIT 组合。"""
        result = engine.execute(
            "UNWIND [10, 20, 30, 40, 50] AS x RETURN "
            "CASE WHEN x > 30 THEN 'large' ELSE 'small' END AS size LIMIT 3",
            store,
        )
        assert len(result.rows) == 3

    # ── WITH + EXISTS ─────────────────────────────────────────────────

    def test_with_exists_before_with(self, store, engine):
        """MATCH + EXISTS + WITH 组合 — EXISTS 过滤后 WITH 投影。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "WITH n.name AS name, n.kind AS kind "
            "RETURN name, kind",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert "run" in names
        assert "main" in names
        assert "helper" not in names

    def test_with_not_exists_and_with_where(self, store, engine):
        """NOT EXISTS + WITH + WHERE 组合。"""
        result = engine.execute(
            "MATCH (n) WHERE NOT EXISTS { MATCH (n)-[:calls]->(m) } "
            "WITH n.name AS name WHERE name <> 'subtract' "
            "RETURN name",
            store,
        )
        names = {row["name"] for row in result.rows}
        assert "subtract" not in names

    # ── 复杂查询组合 ──────────────────────────────────────────────────

    def test_complex_union_case_order_limit(self, store, engine):
        """MATCH (n:function) CASE + ORDER BY + LIMIT UNION MATCH (n:class) 复杂组合。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.name AS name ORDER BY name ASC LIMIT 2 "
            "UNION MATCH (n:class) RETURN n.name AS name ORDER BY name ASC LIMIT 1",
            store,
        )
        assert 1 <= len(result.rows) <= 3

    def test_complex_exists_case_with(self, store, engine):
        """EXISTS + CASE + WITH 全组合。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "WITH n.name AS name, n.kind AS kind "
            "RETURN name, "
            "CASE WHEN kind = 'function' THEN 'caller_func' ELSE 'caller_non_func' END AS category "
            "ORDER BY name ASC",
            store,
        )
        for row in result.rows:
            assert "category" in row.data

    def test_complex_unwind_with_exist_style(self, store, engine):
        """UNWIND + CASE + ORDER + SKIP + LIMIT 全组合。"""
        result = engine.execute(
            "UNWIND [5, 3, 8, 1, 9, 2, 7, 4, 6, 0] AS x "
            "RETURN x, CASE WHEN x > 5 THEN 'high' WHEN x < 3 THEN 'low' ELSE 'mid' END AS level "
            "ORDER BY x ASC SKIP 2 LIMIT 5",
            store,
        )
        assert len(result.rows) == 5
        values = [row["x"] for row in result.rows]
        assert values == [2, 3, 4, 5, 6]

    def test_match_with_unwind_case_combined(self, store, engine):
        """MATCH + WITH 投影后再 CASE 分类。"""
        result = engine.execute(
            "MATCH (n:function) WITH n.name AS name, n.start_line AS line "
            "RETURN name, CASE WHEN line < 10 THEN 'top' WHEN line < 20 THEN 'mid' ELSE 'bottom' END AS position",
            store,
        )
        for row in result.rows:
            assert "position" in row.data
            assert row["position"] in ("top", "mid", "bottom")

    def test_exists_with_subquery_filter_and_case(self, store, engine):
        """EXISTS + 多列投影 + CASE 分类。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:defines]->(m) } "
            "RETURN n.name AS name, n.language AS lang, "
            "CASE WHEN n.language = 'python' THEN 'py_cls' ELSE 'ts_cls' END AS category",
            store,
        )
        for row in result.rows:
            assert row["category"] in ("py_cls", "ts_cls")

    def test_complex_query_with_all_features(self, store, engine):
        """MATCH + WHERE NOT EXISTS + WITH + CASE + ORDER BY + LIMIT + UNION 全组合。"""
        result = engine.execute(
            "MATCH (n:function) WHERE EXISTS { MATCH (n)-[:calls]->(m) } "
            "WITH n.name AS name, n.start_line AS line "
            "RETURN name AS name, "
            "CASE WHEN line < 10 THEN 'top' ELSE 'mid' END AS position "
            "ORDER BY name ASC LIMIT 3 "
            "UNION "
            "MATCH (n:class) RETURN n.name AS name, "
            "CASE WHEN n.language = 'python' THEN 'py' ELSE 'ts' END AS position "
            "ORDER BY name ASC",
            store,
        )
        assert isinstance(result, ResultSet)
        assert len(result.rows) > 0


# ============================================================================
# G. 边界条件和错误处理 (≥ 10 tests)
# ============================================================================


class TestBoundaryAndErrors:
    """G 类：P3 特性边界条件和错误处理。"""

    # ── UNION 边界 ────────────────────────────────────────────────────

    def test_union_all_nothing_to_dedup(self, store, engine):
        """UNION ALL 两侧完全不同的数据，验证不丢失。"""
        result = engine.execute(
            "MATCH (n:class) RETURN n.name AS name "
            "UNION ALL MATCH (n:method) RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 5  # 2 class + 3 method

    def test_union_same_query_twice(self, store, engine):
        """UNION 两侧查询完全相同 — 验证去重效果。"""
        result = engine.execute(
            "MATCH (n:function) RETURN n.kind AS kind "
            "UNION MATCH (n:function) RETURN n.kind AS kind",
            store,
        )
        assert len(result.rows) == 1  # 去重后只有一个 "function"

    def test_union_invalid_syntax_raises(self, store, engine):
        """UNION 后无效语法抛出错误。"""
        with pytest.raises((CypherSyntaxError, CypherSemanticError)):
            engine.execute(
                "MATCH (n:function) RETURN n.name AS name "
                "UNION INVALID",
                store,
            )

    # ── UNWIND 边界 ───────────────────────────────────────────────────

    def test_unwind_nested_list_in_element(self, store, engine):
        """UNWIND 嵌套列表 — 内层列表是一个元素。"""
        result = engine.execute("UNWIND [[1, 2], [3, 4, 5]] AS x RETURN x", store)
        assert len(result.rows) == 2

    def test_unwind_zero_limit(self, store, engine):
        """UNWIND + LIMIT 0 — 返回空。"""
        result = engine.execute("UNWIND [1, 2, 3] AS x RETURN x LIMIT 0", store)
        assert len(result.rows) == 0

    # ── CASE 边界 ────────────────────────────────────────────────────

    def test_case_empty_when_no_else(self, store, engine):
        """CASE 有 WHEN 但无 ELSE — 验证 NULL 输出。"""
        result = engine.execute(
            "MATCH (n) WHERE n.kind = 'module' RETURN "
            "CASE WHEN n.kind = 'function' THEN 'found' END AS result",
            store,
        )
        for row in result.rows:
            assert row["result"] is None

    def test_case_with_literal_comparison(self, store, engine):
        """CASE WHEN 1 > 0 THEN 'yes' END — 纯字面量比较。"""
        result = engine.execute(
            "MATCH (n) RETURN n.name, CASE WHEN 1 > 0 THEN 'yes' ELSE 'no' END AS test LIMIT 3",
            store,
        )
        for row in result.rows:
            assert row["test"] == "yes"

    # ── EXISTS 边界 ──────────────────────────────────────────────────

    def test_exists_empty_resultset(self, empty_store, engine):
        """EXISTS 在空 store 返回空 ResultSet。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n",
            empty_store,
        )
        assert isinstance(result, ResultSet)
        assert len(result.rows) == 0

    def test_exists_with_nonexistent_edge_type_in_subquery(self, store, engine):
        """EXISTS 子查询中边类型不存在 — 返回空。"""
        result = engine.execute(
            "MATCH (n) WHERE EXISTS { MATCH (n)-[:contains]->(m) } RETURN n.name AS name",
            store,
        )
        assert len(result.rows) == 0

    # ── WITH 边界 ────────────────────────────────────────────────────

    def test_with_empty_result_after_where(self, store, engine):
        """WITH WHERE 条件排除所有行 — 返回空。"""
        result = engine.execute(
            "MATCH (n) WITH n.name AS name WHERE name = 'nonexistent' RETURN name",
            store,
        )
        assert len(result.rows) == 0

    def test_with_single_field_projection(self, store, engine):
        """WITH 单字段投影 — 列名正确。"""
        result = engine.execute(
            "MATCH (n) WITH n.id AS node_id RETURN node_id",
            store,
        )
        assert result.columns == ["node_id"]
        ids = {row["node_id"] for row in result.rows}
        assert ids == {"n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "n10"}


# ============================================================================
# H. 回归测试 — 验证 P1/P2 功能完整
# ============================================================================


class TestNoRegression:
    """H 类：确保 P3 特性不破坏 P1/P2 功能。"""

    def test_basic_match_works(self, store, engine):
        """MATCH (n) RETURN n — 基本 MATCH 仍正常。"""
        result = engine.execute("MATCH (n) RETURN n", store)
        assert len(result.rows) == 10

    def test_where_filtering_works(self, store, engine):
        """MATCH (n) WHERE n.kind = 'function' RETURN n — WHERE 仍正常。"""
        result = engine.execute("MATCH (n) WHERE n.kind = 'function' RETURN n", store)
        assert len(result.rows) == 5

    def test_order_by_works(self, store, engine):
        """MATCH (n) RETURN n.name AS name ORDER BY name ASC — ORDER BY 仍正常。"""
        result = engine.execute("MATCH (n) RETURN n.name AS name ORDER BY name ASC", store)
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_limit_works(self, store, engine):
        """MATCH (n) RETURN n LIMIT 5 — LIMIT 仍正常。"""
        result = engine.execute("MATCH (n) RETURN n LIMIT 5", store)
        assert len(result.rows) == 5

    def test_distinct_works(self, store, engine):
        """MATCH (n) RETURN DISTINCT n.kind — DISTINCT 仍正常。"""
        result = engine.execute("MATCH (n) RETURN DISTINCT n.kind", store)
        kinds = {row["n.kind"] for row in result.rows}
        assert kinds == {"function", "class", "method"}

    def test_edge_expand_works(self, store, engine):
        """MATCH (n)-[:calls]->(m) RETURN n, m — 边扩展仍正常。"""
        result = engine.execute("MATCH (n)-[:calls]->(m) RETURN n, m", store)
        assert len(result.rows) == 9

    def test_functions_work(self, store, engine):
        """RETURN toUpper(n.name) — 内置函数仍正常。"""
        result = engine.execute("MATCH (n) RETURN toUpper(n.name) AS upper LIMIT 3", store)
        for row in result.rows:
            assert row["upper"] == row["upper"].upper()

    def test_aggregate_work(self, store, engine):
        """聚合函数仍正常（通过 executor 直接测试 COUNT 逻辑）。"""
        from tws_graph.cypher.planner import (
            LogicalPlan, AggregateOperator, ScanOperator, ProjectOperator,
        )
        from tws_graph.cypher.ast import Identifier, ReturnItem, Span
        from tws_graph.cypher.executor import Executor

        plan = LogicalPlan(root=ProjectOperator(
            source=AggregateOperator(
                source=ScanOperator(variable="n"),
                group_by=[],
                aggregates=[("COUNT", "*", "cnt")],
            ),
            items=[ReturnItem(
                expression=Identifier(name="cnt", span=Span(0, 0, 0, 0)),
                alias="cnt",
            )],
        ))
        exec_ = Executor(store)
        result = exec_.execute(plan)
        assert result.rows[0]["cnt"] == 10

    def test_regex_works(self, store, engine):
        """WHERE n.name =~ 'm.*' — 正则仍正常。"""
        result = engine.execute("MATCH (n) WHERE n.name =~ 'm.*' RETURN n.name", store)
        names = {row["n.name"] for row in result.rows}
        assert names == {"main"}

    def test_in_operator_works(self, store, engine):
        """WHERE n.name IN ['main', 'helper'] — IN 仍正常。"""
        result = engine.execute("MATCH (n) WHERE n.name IN ['main', 'helper'] RETURN n.name", store)
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_is_null_works(self, store, engine):
        """WHERE n.name IS NOT NULL — NULL 检查仍正常。"""
        result = engine.execute("MATCH (n) WHERE n.name IS NOT NULL RETURN n", store)
        assert len(result.rows) == 10
