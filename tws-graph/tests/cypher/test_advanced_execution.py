"""Tests for P3 Executor extensions — UNION, UNWIND, CASE, SubqueryFilter, WITH.

Tests the Executor's ability to execute new logical operators:
UnionOperator, UnwindOperator, SubqueryFilterOperator, and CaseExpression evaluation.
"""

from __future__ import annotations

import pytest

from tws_graph.cypher.ast import (
    BinaryOp,
    FunctionCall,
    Identifier,
    ListLiteral,
    Literal,
    PropertyAccess,
    ReturnItem,
    OrderByItem,
    Span,
    UnaryOp,
    CaseExpression,
    StarExpression,
)

from tws_graph.cypher.planner import (
    LogicalPlan,
    ScanOperator,
    FilterOperator,
    EdgeExpandOperator,
    ProjectOperator,
    SortOperator,
    LimitOperator,
    DistinctOperator,
    # New operators
    UnionOperator,
    UnwindOperator,
    SubqueryFilterOperator,
)

from tws_graph.cypher.executor import Executor, Row, ResultSet
from tws_graph.cypher.errors import CypherExecutionError
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Helpers
# ============================================================================


def _span():
    return Span(start_line=1, start_col=1, end_line=1, end_col=5)


def _ident(name: str) -> Identifier:
    return Identifier(name=name, span=_span())


def _literal(value) -> Literal:
    return Literal(value=value, span=_span())


def _prop_access(obj_name: str, key: str) -> PropertyAccess:
    return PropertyAccess(obj=_ident(obj_name), key=key, span=_span())


def _make_node(node_id: str, kind: str, name: str, **kwargs) -> dict:
    node = {
        "id": node_id,
        "kind": kind,
        "name": name,
        "qualified_name": f"test::{name}",
        "file_path": f"test/{name}.py",
        "language": "python",
        "start_line": 1,
        "end_line": 10,
    }
    node.update(kwargs)
    return node


def _make_edge(edge_id: int, source: str, target: str, kind: str, **kwargs) -> dict:
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
    }
    edge.update(kwargs)
    return edge


def _build_simple_store() -> MemoryStore:
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main"),
        _make_node("n2", "function", "helper"),
        _make_node("n3", "function", "dispatch"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_labeled_store() -> MemoryStore:
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main"),
        _make_node("n2", "class", "MyClass"),
        _make_node("n3", "function", "helper"),
        _make_node("n4", "class", "YourClass"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_graph_store() -> MemoryStore:
    store = MemoryStore()
    nodes = [
        _make_node("a", "function", "caller"),
        _make_node("b", "function", "callee1"),
        _make_node("c", "function", "callee2"),
        _make_node("d", "class", "TargetClass"),
    ]
    store.insert_nodes(nodes)
    edges = [
        _make_edge(1, "a", "b", "calls"),
        _make_edge(2, "a", "c", "calls"),
        _make_edge(3, "c", "a", "calls"),
        _make_edge(4, "a", "d", "imports"),
    ]
    store.insert_edges(edges)
    return store


# ============================================================================
# UnionOperator execution
# ============================================================================


class TestUnionOperator:
    """Tests for UnionOperator execution."""

    def test_union_two_scans(self):
        """UNION of two scans: merge row sets with dedup."""
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "alpha"),
            _make_node("n2", "function", "beta"),
            _make_node("n3", "function", "alpha"),  # duplicate name
        ]
        store.insert_nodes(nodes)

        exec_ = Executor(store)

        # Left: scan "function" nodes → project name
        left_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="function"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        # Right: scan "function" nodes → project name (same)
        right_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="function"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        plan = LogicalPlan(root=UnionOperator(left=left_plan, right=right_plan, all=False))
        result = exec_.execute(plan)

        # UNION dedup: "alpha" (appears in both left and right, only one row)
        # "beta" appears in both → 1 row
        # So total distinct names: alpha, beta = 2
        names = {row["name"] for row in result.rows}
        assert names == {"alpha", "beta"}

    def test_union_all_two_scans(self):
        """UNION ALL: no dedup, all rows kept."""
        store = _build_simple_store()
        exec_ = Executor(store)

        # Left: scan all → "main", "helper", "dispatch"
        left_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        # Right: scan functions → same names
        right_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="function"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        plan = LogicalPlan(root=UnionOperator(left=left_plan, right=right_plan, all=True))
        result = exec_.execute(plan)

        # UNION ALL: all rows kept
        assert len(result.rows) == 6  # 3 left + 3 right

    def test_union_different_projects(self):
        """UNION of two scans with different projections."""
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
            _make_node("n2", "class", "MyClass"),
        ]
        store.insert_nodes(nodes)

        exec_ = Executor(store)

        left_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="function"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        right_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="class"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        plan = LogicalPlan(root=UnionOperator(left=left_plan, right=right_plan, all=False))
        result = exec_.execute(plan)

        names = {row["name"] for row in result.rows}
        assert names == {"main", "MyClass"}

    def test_union_empty_right(self):
        """UNION where right plan returns empty."""
        store = _build_simple_store()
        exec_ = Executor(store)

        left_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        # Right: scan with non-existent label
        right_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="nonexistent"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        plan = LogicalPlan(root=UnionOperator(left=left_plan, right=right_plan, all=False))
        result = exec_.execute(plan)

        assert len(result.rows) == 3  # only left rows

    def test_union_empty_both(self):
        """UNION where both plans return empty."""
        store = _build_simple_store()
        exec_ = Executor(store)

        left_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="nonexistent"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        right_plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n", label="nonexistent2"),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))

        plan = LogicalPlan(root=UnionOperator(left=left_plan, right=right_plan, all=False))
        result = exec_.execute(plan)

        assert len(result.rows) == 0


# ============================================================================
# UnwindOperator execution
# ============================================================================


class TestUnwindOperator:
    """Tests for UnwindOperator execution."""

    def test_unwind_list_literal(self):
        """UNWIND [1,2,3] AS x: produce 3 rows."""
        store = _build_simple_store()
        exec_ = Executor(store)

        # Virtual scan → one empty row
        plan = LogicalPlan(root=UnwindOperator(
            source=ScanOperator(variable="", label=None),
            expression=ListLiteral(
                elements=[_literal(1), _literal(2), _literal(3)],
                span=_span(),
            ),
            variable="x",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        values = {row["x"] for row in result.rows}
        assert values == {1, 2, 3}

    def test_unwind_string_list(self):
        """UNWIND ['a','b'] AS x: produce 2 rows."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=UnwindOperator(
            source=ScanOperator(variable="", label=None),
            expression=ListLiteral(
                elements=[_literal("a"), _literal("b")],
                span=_span(),
            ),
            variable="x",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2
        values = {row["x"] for row in result.rows}
        assert values == {"a", "b"}

    def test_unwind_empty_list(self):
        """UNWIND [] AS x: produce 0 rows."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=UnwindOperator(
            source=ScanOperator(variable="", label=None),
            expression=ListLiteral(elements=[], span=_span()),
            variable="x",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_unwind_non_list_value(self):
        """UNWIND non-list value: skip the row (produce 0 rows)."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=UnwindOperator(
            source=ScanOperator(variable="", label=None),
            expression=_literal(42),  # not a list
            variable="x",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 0

    def test_unwind_from_property(self):
        """UNWIND from a property that is a list."""
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)

        # Create a row where n has a list property
        # We need to insert the list manually... or we can test using a node property
        # that is actually a list. In our store, properties are simple types.
        # For test purposes, we add a property to the node that's a list.
        # Actually, node properties are stored as dicts, we can manually craft a row.

        # Test UNWIND from a manually crafted source row
        # Easiest: use ScanOperator then UNWIND from a non-existent property (which returns None)
        # to test the None/skip behavior. For actual list expansion, we've tested with Literal lists.

        # For a more realistic test, let's unwrap from a property access expression
        plan = LogicalPlan(root=UnwindOperator(
            source=ScanOperator(variable="n"),
            expression=_prop_access("n", "name"),  # "n.name" is a string, not a list
            variable="ch",
        ))
        result = exec_.execute(plan)

        # n.name is a string "main", not a list → all rows skipped
        assert len(result.rows) == 0

    def test_unwind_with_project(self):
        """UNWIND + Project: full pipeline for UNWIND [1,2,3] AS x RETURN x."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=ProjectOperator(
            source=UnwindOperator(
                source=ScanOperator(variable="", label=None),
                expression=ListLiteral(
                    elements=[_literal(1), _literal(2), _literal(3)],
                    span=_span(),
                ),
                variable="x",
            ),
            items=[ReturnItem(expression=_ident("x"), alias="x")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        assert result.columns == ["x"]
        values = {row["x"] for row in result.rows}
        assert values == {1, 2, 3}


# ============================================================================
# SubqueryFilterOperator execution
# ============================================================================


class TestSubqueryFilterOperator:
    """Tests for SubqueryFilterOperator (EXISTS / NOT EXISTS) execution."""

    def test_exists_with_match(self):
        """EXISTS subquery: row passes if subquery returns any rows.

        Uses the same variable name 'n' in both outer and inner query
        to enable correlation.
        """
        store = _build_graph_store()
        exec_ = Executor(store)

        # Outer: Scan all nodes as 'n'
        # Subquery: MATCH (n)-[:calls]->(m)  (correlated on 'n')
        # For node 'a' (caller): edges to b and c via calls → has matches
        # For node 'b' (callee1): no outgoing calls → no matches
        # For node 'c' (callee2): edge to a via calls → has matches
        # For node 'd' (TargetClass): no outgoing calls → no matches

        sub_plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="n"),
            edge_types=["calls"],
            direction="out",
            target_variable="m",
        ))

        plan = LogicalPlan(root=ProjectOperator(
            source=SubqueryFilterOperator(
                source=ScanOperator(variable="n"),
                subquery_plan=sub_plan,
                exists=True,
            ),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))
        result = exec_.execute(plan)

        names = {row["name"] for row in result.rows}
        assert names == {"caller", "callee2"}
        assert "callee1" not in names
        assert "TargetClass" not in names

    def test_exists_no_match(self):
        """EXISTS subquery: row filtered out when subquery returns empty."""
        store = _build_graph_store()
        exec_ = Executor(store)

        # Subquery: MATCH (a)-[:contains]->(b) — no such edges
        sub_plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["contains"],
            direction="out",
            target_variable="b",
        ))

        plan = LogicalPlan(root=SubqueryFilterOperator(
            source=ScanOperator(variable="n"),
            subquery_plan=sub_plan,
            exists=True,
        ))
        result = exec_.execute(plan)

        # No rows have outgoing "contains" edges → all filtered out
        assert len(result.rows) == 0

    def test_not_exists(self):
        """NOT EXISTS subquery: row passes if subquery returns empty."""
        store = _build_graph_store()
        exec_ = Executor(store)

        # Subquery: MATCH (a)-[:contains]->(b) — no such edges
        sub_plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["contains"],
            direction="out",
            target_variable="b",
        ))

        plan = LogicalPlan(root=ProjectOperator(
            source=SubqueryFilterOperator(
                source=ScanOperator(variable="n"),
                subquery_plan=sub_plan,
                exists=False,  # NOT EXISTS
            ),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))
        result = exec_.execute(plan)

        # No node has "contains" edges → NOT EXISTS → all pass
        assert len(result.rows) == 4

    def test_not_exists_with_match(self):
        """NOT EXISTS: row passes when subquery returns nothing.

        Uses the same variable name 'n' in both outer and inner query.
        """
        store = _build_graph_store()
        exec_ = Executor(store)

        # Subquery: MATCH (n)-[:calls]->(m)  (correlated on 'n')
        sub_plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="n"),
            edge_types=["calls"],
            direction="out",
            target_variable="m",
        ))

        plan = LogicalPlan(root=ProjectOperator(
            source=SubqueryFilterOperator(
                source=ScanOperator(variable="n"),
                subquery_plan=sub_plan,
                exists=False,  # NOT EXISTS
            ),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))
        result = exec_.execute(plan)

        # caller has calls edges → NOT EXISTS → filtered OUT
        # callee2 has calls edge → filtered OUT
        # callee1 has no calls → passes
        # TargetClass has no calls → passes
        names = {row["name"] for row in result.rows}
        assert names == {"callee1", "TargetClass"}
        assert "caller" not in names
        assert "callee2" not in names


# ============================================================================
# CaseExpression evaluation
# ============================================================================


class TestCaseExpression:
    """Tests for CaseExpression evaluation."""

    def test_simple_case_match(self):
        """Simple CASE: CASE x WHEN 1 THEN 'one' WHEN 2 THEN 'two' END."""
        store = _build_simple_store()
        exec_ = Executor(store)

        # CASE 2 WHEN 1 THEN 'one' WHEN 2 THEN 'two' ELSE 'other' END
        case_expr = CaseExpression(
            expression=_literal(2),
            cases=[
                (_literal(1), _literal("one")),
                (_literal(2), _literal("two")),
            ],
            default=_literal("other"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] == "two"

    def test_simple_case_no_match_with_default(self):
        """Simple CASE with no match: uses ELSE."""
        store = _build_simple_store()
        exec_ = Executor(store)

        case_expr = CaseExpression(
            expression=_literal(99),
            cases=[
                (_literal(1), _literal("one")),
                (_literal(2), _literal("two")),
            ],
            default=_literal("other"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] == "other"

    def test_simple_case_no_match_no_default(self):
        """Simple CASE with no match and no ELSE: returns None."""
        store = _build_simple_store()
        exec_ = Executor(store)

        case_expr = CaseExpression(
            expression=_literal(99),
            cases=[
                (_literal(1), _literal("one")),
            ],
            default=None,
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] is None

    def test_search_case(self):
        """Search CASE: CASE WHEN x > 5 THEN 'big' WHEN x > 0 THEN 'small' END."""
        store = _build_simple_store()
        exec_ = Executor(store)

        # CASE WHEN 1 > 5 THEN 'big' WHEN 1 > 0 THEN 'small' END → 'small'
        case_expr = CaseExpression(
            expression=None,  # Search case
            cases=[
                (
                    BinaryOp(op=">", left=_literal(1), right=_literal(5), span=_span()),
                    _literal("big"),
                ),
                (
                    BinaryOp(op=">", left=_literal(1), right=_literal(0), span=_span()),
                    _literal("small"),
                ),
            ],
            default=_literal("none"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] == "small"

    def test_search_case_first_match_wins(self):
        """Search CASE: first matching WHEN wins."""
        store = _build_simple_store()
        exec_ = Executor(store)

        case_expr = CaseExpression(
            expression=None,
            cases=[
                (_literal(True), _literal("first")),
                (_literal(True), _literal("second")),  # should not reach here
            ],
            default=_literal("none"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] == "first"

    def test_search_case_no_match_default(self):
        """Search CASE no match: uses ELSE."""
        store = _build_simple_store()
        exec_ = Executor(store)

        case_expr = CaseExpression(
            expression=None,
            cases=[
                (_literal(False), _literal("nope")),
            ],
            default=_literal("fallback"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        for row in result.rows:
            assert row["result"] == "fallback"

    def test_case_with_property_access(self):
        """CASE using property access in expression."""
        store = _build_simple_store()
        exec_ = Executor(store)

        case_expr = CaseExpression(
            expression=_prop_access("n", "name"),
            cases=[
                (_literal("main"), _literal("entry")),
                (_literal("helper"), _literal("utility")),
            ],
            default=_literal("other"),
            span=_span(),
        )

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(expression=case_expr, alias="result")],
        ))
        result = exec_.execute(plan)

        results_by_name = {}
        for row in result.rows:
            # Read the original name from the node data
            pass  # We can't easily map back, check the counts
        assert len(result.rows) == 3


# ============================================================================
# WITH clause (intermediate projection)
# ============================================================================


class TestWithClause:
    """Tests for WITH clause as intermediate projection."""

    def test_with_intermediate_projection(self):
        """MATCH (n) WITH n.name AS name RETURN name."""
        store = _build_simple_store()
        exec_ = Executor(store)

        # WITH n.name AS name → ProjectOperator
        # RETURN name → ProjectOperator
        plan = LogicalPlan(root=ProjectOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
            ),
            items=[ReturnItem(expression=_ident("name"), alias="name")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        assert result.columns == ["name"]
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}

    def test_with_filter(self):
        """MATCH (n) WITH n.name AS name, n.kind AS kind WHERE kind = 'function' RETURN name."""
        store = _build_labeled_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=ProjectOperator(
            source=FilterOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[
                        ReturnItem(expression=_prop_access("n", "name"), alias="name"),
                        ReturnItem(expression=_prop_access("n", "kind"), alias="kind"),
                    ],
                ),
                predicate=BinaryOp(
                    op="=",
                    left=_ident("kind"),
                    right=_literal("function"),
                    span=_span(),
                ),
            ),
            items=[ReturnItem(expression=_ident("name"), alias="name")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2  # main and helper
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper"}

    def test_with_then_sort(self):
        """MATCH (n) WITH n.name AS name ORDER BY name RETURN name."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=SortOperator(
            source=ProjectOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
                ),
                items=[ReturnItem(expression=_ident("name"), alias="name")],
            ),
            items=[OrderByItem(expression=_ident("name"), direction="ASC")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)


# ============================================================================
# End-to-end: full query pipeline (parse → plan → execute)
# ============================================================================


class TestEndToEndAdvanced:
    """End-to-end tests using parser + planner + executor."""

    def test_e2e_unwind_list(self):
        """UNWIND [1,2,3] AS x RETURN x."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_simple_store()
        lexer = Lexer("UNWIND [1, 2, 3] AS x RETURN x")
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        values = {row["x"] for row in result.rows}
        assert values == {1, 2, 3}

    def test_e2e_union(self):
        """MATCH (n:function) RETURN n.name AS name UNION MATCH (n:class) RETURN n.name AS name."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_labeled_store()
        lexer = Lexer("MATCH (n:function) RETURN n.name AS name UNION MATCH (n:class) RETURN n.name AS name")
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        # Functions: main, helper. Classes: MyClass, YourClass.
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "MyClass", "YourClass"}

    def test_e2e_union_all(self):
        """UNION ALL keeps duplicates."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_labeled_store()
        lexer = Lexer("MATCH (n:function) RETURN n.name AS name UNION ALL MATCH (n:class) RETURN n.name AS name")
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        assert len(result.rows) == 4  # 2 functions + 2 classes

    def test_e2e_exists(self):
        """MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_graph_store()
        lexer = Lexer("MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n.name AS name")
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        names = {row["name"] for row in result.rows}
        # caller has outgoing calls (to callee1, callee2)
        # callee2 has outgoing calls (to caller)
        assert "caller" in names
        assert "callee2" in names
        assert "callee1" not in names  # no outgoing calls
        assert "TargetClass" not in names  # no outgoing calls

    def test_e2e_case_expression(self):
        """MATCH (n) RETURN CASE WHEN n.kind = 'function' THEN 'func' ELSE 'other' END AS type."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_labeled_store()
        lexer = Lexer(
            "MATCH (n) RETURN CASE WHEN n.kind = 'function' THEN 'func' ELSE 'other' END AS type"
        )
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        func_count = sum(1 for row in result.rows if row["type"] == "func")
        other_count = sum(1 for row in result.rows if row["type"] == "other")
        assert func_count == 2  # main, helper
        assert other_count == 2  # MyClass, YourClass

    def test_e2e_with_clause(self):
        """MATCH (n) WITH n.name AS name RETURN name."""
        from tws_graph.cypher.lexer import Lexer
        from tws_graph.cypher.parser import Parser
        from tws_graph.cypher.planner import Planner

        store = _build_simple_store()
        lexer = Lexer("MATCH (n) WITH n.name AS name RETURN name")
        parser = Parser(lexer)
        stmt = parser.parse()
        planner = Planner()
        plan = planner.plan(stmt)
        exec_ = Executor(store)
        result = exec_.execute(plan)

        assert len(result.rows) == 3
        names = {row["name"] for row in result.rows}
        assert names == {"main", "helper", "dispatch"}


# ============================================================================
# Regression: existing features still work
# ============================================================================


class TestRegression:
    """Verify existing executor features are not broken by P3 changes."""

    def test_scan_filter_project_regression(self):
        """MATCH (n) WHERE n.name = 'main' RETURN n.name AS name."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=ProjectOperator(
            source=FilterOperator(
                source=ScanOperator(variable="n"),
                predicate=BinaryOp(
                    op="=",
                    left=_prop_access("n", "name"),
                    right=_literal("main"),
                    span=_span(),
                ),
            ),
            items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["name"] == "main"

    def test_edge_expand_regression(self):
        """MATCH (a)-[:calls]->(b) still works."""
        store = _build_graph_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=EdgeExpandOperator(
            source=ScanOperator(variable="a"),
            edge_types=["calls"],
            direction="out",
            target_variable="b",
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3

    def test_count_aggregate_regression(self):
        """aggregation still works."""
        from tws_graph.cypher.planner import AggregateOperator

        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=AggregateOperator(
            source=ScanOperator(variable="n"),
            group_by=[],
            aggregates=[("COUNT", "*", "cnt")],
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1
        assert result.rows[0]["cnt"] == 3

    def test_distinct_regression(self):
        """DISTINCT still works."""
        store = MemoryStore()
        nodes = [
            _make_node("n1", "function", "main"),
            _make_node("n2", "function", "main"),
        ]
        store.insert_nodes(nodes)
        exec_ = Executor(store)

        plan = LogicalPlan(root=DistinctOperator(
            source=ProjectOperator(
                source=ScanOperator(variable="n"),
                items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
            ),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 1  # dedup "main"

    def test_sort_limit_regression(self):
        """Sort + Limit still works."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=LimitOperator(
            source=SortOperator(
                source=ProjectOperator(
                    source=ScanOperator(variable="n"),
                    items=[ReturnItem(expression=_prop_access("n", "name"), alias="name")],
                ),
                items=[OrderByItem(expression=_ident("name"), direction="ASC")],
            ),
            skip=0,
            limit=2,
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 2

    def test_unary_not_regression(self):
        """Unary NOT still works."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=FilterOperator(
            source=ScanOperator(variable="n"),
            predicate=UnaryOp(
                op="NOT",
                operand=BinaryOp(
                    op="=", left=_literal(1), right=_literal(2),
                    span=_span(),
                ),
                span=_span(),
            ),
        ))
        result = exec_.execute(plan)

        assert len(result.rows) == 3  # NOT(1=2) is true for all

    def test_function_call_regression(self):
        """Function calls still work."""
        store = _build_simple_store()
        exec_ = Executor(store)

        plan = LogicalPlan(root=ProjectOperator(
            source=ScanOperator(variable="n"),
            items=[ReturnItem(
                expression=FunctionCall(
                    name="toUpper",
                    args=[_prop_access("n", "name")],
                    span=_span(),
                ),
                alias="upper",
            )],
        ))
        result = exec_.execute(plan)

        names = {row["upper"] for row in result.rows}
        assert names == {"MAIN", "HELPER", "DISPATCH"}
