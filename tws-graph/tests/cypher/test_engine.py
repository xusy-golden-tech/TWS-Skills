"""Tests for cypher/__init__.py — CypherEngine facade."""

from __future__ import annotations

import pytest

from tws_graph.cypher import CypherEngine, execute, ResultSet, Row
from tws_graph.cypher.errors import (
    CypherSyntaxError,
    CypherSemanticError,
    CypherLexerError,
    CypherExecutionError,
)
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Helpers
# ============================================================================


def _make_node(node_id: str, kind: str, name: str, **kwargs) -> dict:
    """Create a minimal node record for tests."""
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
    """Create a minimal edge record for tests."""
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
    }
    edge.update(kwargs)
    return edge


def _build_simple_store() -> MemoryStore:
    """Create a MemoryStore with 5 nodes (mixed kinds)."""
    store = MemoryStore()
    nodes = [
        _make_node("n1", "function", "main"),
        _make_node("n2", "function", "helper"),
        _make_node("n3", "class", "MyClass"),
        _make_node("n4", "function", "calculateTotal"),
        _make_node("n5", "module", "utils"),
    ]
    store.insert_nodes(nodes)
    return store


def _build_empty_store() -> MemoryStore:
    """Create an empty MemoryStore."""
    return MemoryStore()


# ============================================================================
# CypherEngine basic tests
# ============================================================================


class TestCypherEngineBasic:
    """Tests for CypherEngine basic query execution."""

    def test_execute_match_return_all(self):
        """MATCH (n) RETURN n returns all nodes."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n", store)
        assert isinstance(result, ResultSet)
        assert len(result.rows) == 5
        assert result.total_count == 5
        assert result.columns == ["n"]
        # Each row has a node dict under key "n"
        for row in result.rows:
            assert "n" in row.data
            node = row["n"]
            assert isinstance(node, dict)
            assert "id" in node
            assert "name" in node
            assert "kind" in node

    def test_execute_match_return_specific_field(self):
        """MATCH (n) RETURN n.name projects a specific field."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n.name", store)
        assert len(result.rows) == 5
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper", "MyClass", "calculateTotal", "utils"}

    def test_execute_match_with_label_filter(self):
        """MATCH (n:function) RETURN n.name filters by label (case-sensitive to store data)."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n:function) RETURN n.name", store)
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper", "calculateTotal"}

    def test_execute_match_with_where_equality(self):
        """MATCH (n) WHERE n.name = 'main' RETURN n filters correctly."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' RETURN n", store
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n"]["name"] == "main"

    def test_execute_match_with_where_and_label(self):
        """MATCH (n:function) WHERE n.name = 'helper' RETURN n.name."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n:function) WHERE n.name = 'helper' RETURN n.name",
            store,
        )
        assert len(result.rows) == 1
        assert result.rows[0]["n.name"] == "helper"

    def test_execute_with_limit(self):
        """MATCH (n) RETURN n LIMIT 3 limits results."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        assert len(result.rows) == 3

    def test_execute_with_skip_and_limit(self):
        """MATCH (n) RETURN n SKIP 2 LIMIT 2."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n SKIP 2 LIMIT 2", store)
        assert len(result.rows) == 2

    def test_execute_with_order_by_asc(self):
        """MATCH (n) RETURN n ORDER BY n.name ASC — project n then sort by n.name."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN n ORDER BY n.name ASC", store
        )
        names = [row["n"]["name"] for row in result.rows]
        assert names == sorted(names)

    def test_execute_with_order_by_desc(self):
        """MATCH (n) RETURN n ORDER BY n.name DESC."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN n ORDER BY n.name DESC", store
        )
        names = [row["n"]["name"] for row in result.rows]
        assert names == sorted(names, reverse=True)

    def test_execute_with_order_by_aliased(self):
        """MATCH (n) RETURN n.name AS name ORDER BY name ASC — alias-based sort."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name ORDER BY name ASC", store
        )
        names = [row["name"] for row in result.rows]
        assert names == sorted(names)

    def test_execute_with_distinct(self):
        """MATCH (n) RETURN DISTINCT n.kind should deduplicate."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN DISTINCT n.kind", store
        )
        kinds = {row["n.kind"] for row in result.rows}
        assert kinds == {"function", "class", "module"}

    def test_row_iteration_and_access(self):
        """ResultSet is iterable, Row supports dict access."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        for row in result:
            assert isinstance(row, Row)
            node = row["n"]
            assert "name" in node
        # Index access
        first = result[0]
        assert isinstance(first, Row)

    def test_row_get_with_default(self):
        """Row.get() returns default for missing keys."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n LIMIT 1", store)
        row = result.rows[0]
        assert row.get("n") is not None
        assert row.get("missing") is None
        assert row.get("missing", "default") == "default"

    def test_execute_multiple_return_items(self):
        """MATCH (n) RETURN n.name, n.kind, n.file_path."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN n.name, n.kind, n.file_path", store
        )
        assert len(result.rows) == 5
        row = result.rows[0]
        assert "n.name" in row.data
        assert "n.kind" in row.data
        assert "n.file_path" in row.data

    def test_execute_with_alias(self):
        """MATCH (n) RETURN n.name AS name, n.kind AS kind."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) RETURN n.name AS name, n.kind AS kind", store
        )
        assert len(result.rows) == 5
        row = result.rows[0]
        assert "name" in row.data
        assert "kind" in row.data

    def test_execute_where_and_operator(self):
        """MATCH (n) WHERE n.name = 'main' AND n.language = 'python' RETURN n."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' AND n.language = 'python' RETURN n",
            store,
        )
        assert len(result.rows) == 1

    def test_execute_where_not_equal(self):
        """MATCH (n:function) WHERE n.name <> 'main' RETURN n.name."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n:function) WHERE n.name <> 'main' RETURN n.name", store
        )
        names = {row["n.name"] for row in result.rows}
        assert "main" not in names
        assert "helper" in names
        assert "calculateTotal" in names

    def test_execute_where_or(self):
        """MATCH (n) WHERE n.name = 'main' OR n.name = 'helper' RETURN n.name."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute(
            "MATCH (n) WHERE n.name = 'main' OR n.name = 'helper' RETURN n.name",
            store,
        )
        names = {row["n.name"] for row in result.rows}
        assert names == {"main", "helper"}


# ============================================================================
# CypherEngine error handling
# ============================================================================


class TestCypherEngineErrors:
    """Tests for CypherEngine error handling."""

    def test_invalid_syntax_raises_syntax_error(self):
        """Invalid Cypher syntax raises CypherSyntaxError."""
        store = _build_simple_store()
        engine = CypherEngine()
        with pytest.raises(CypherSyntaxError):
            engine.execute("INVALID QUERY HERE", store)

    def test_unknown_keyword_raises_syntax_error(self):
        """Unknown keyword raises CypherSyntaxError."""
        store = _build_simple_store()
        engine = CypherEngine()
        with pytest.raises(CypherSyntaxError):
            engine.execute("FOOBAR (n) RETURN n", store)

    def test_semantic_error_undefined_variable(self):
        """RETURN referencing undefined variable raises CypherSemanticError."""
        store = _build_simple_store()
        engine = CypherEngine()
        with pytest.raises(CypherSemanticError):
            engine.execute("MATCH (n) RETURN m", store)

    def test_lexer_error(self):
        """Unclosed string raises CypherLexerError."""
        store = _build_simple_store()
        engine = CypherEngine()
        with pytest.raises(CypherLexerError):
            engine.execute("MATCH (n) WHERE n.name = 'unclosed RETURN n", store)


# ============================================================================
# CypherEngine boundary conditions
# ============================================================================


class TestCypherEngineBoundary:
    """Tests for CypherEngine boundary conditions."""

    def test_execute_on_empty_store(self):
        """Query on empty store returns empty ResultSet (no error)."""
        store = _build_empty_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n", store)
        assert isinstance(result, ResultSet)
        assert len(result.rows) == 0
        assert result.total_count == 0

    def test_execute_limit_zero(self):
        """LIMIT 0 returns empty ResultSet."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n LIMIT 0", store)
        assert len(result.rows) == 0

    def test_execute_multiple_queries_same_engine(self):
        """Same CypherEngine instance can execute multiple queries."""
        store = _build_simple_store()
        engine = CypherEngine()
        r1 = engine.execute("MATCH (n) RETURN n LIMIT 2", store)
        r2 = engine.execute("MATCH (n) RETURN n LIMIT 3", store)
        assert len(r1.rows) == 2
        assert len(r2.rows) == 3

    def test_convenience_function_execute(self):
        """The execute() convenience function works identically."""
        store = _build_simple_store()
        result = execute("MATCH (n) RETURN n LIMIT 2", store)
        assert isinstance(result, ResultSet)
        assert len(result.rows) == 2

    def test_resultset_len(self):
        """ResultSet.__len__ returns correct count."""
        store = _build_simple_store()
        engine = CypherEngine()
        result = engine.execute("MATCH (n) RETURN n", store)
        assert len(result) == 5


# ============================================================================
# CypherEngine public API
# ============================================================================


class TestCypherEnginePublicAPI:
    """Verify that CypherEngine, ResultSet, Row, execute are exported."""

    def test_cypherengine_is_class(self):
        assert isinstance(CypherEngine, type)

    def test_resultset_is_class(self):
        assert isinstance(ResultSet, type)

    def test_row_is_class(self):
        assert isinstance(Row, type)

    def test_execute_is_callable(self):
        assert callable(execute)
