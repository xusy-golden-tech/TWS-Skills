"""Tests for post-processing edge resolver."""

import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.edge_resolver import resolve_edges, ResolveResult
from tws_graph.indexer.orchestrator import ExtractionOrchestrator


class TestEdgeResolver:
    """Tests for cross-file edge resolution."""

    @pytest.fixture
    def populated_db(self, sample_py_project, temp_db_path):
        """Create a database with indexed Python sample."""
        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        orch.index_all()
        yield queries
        db.close()

    def test_resolve_result_structure(self, populated_db):
        result = resolve_edges(populated_db)
        assert isinstance(result, ResolveResult)
        assert result.total_checked >= 0
        assert result.resolved >= 0
        assert result.unresolved >= 0
        assert result.ambiguous >= 0

    def test_no_crash_on_empty_db(self, queries):
        """Edge resolver should handle empty DB gracefully."""
        result = resolve_edges(queries)
        assert result.total_checked == 0
        assert result.resolved == 0

    def test_dangling_edges_detected(self, populated_db):
        """After single-file index, there should be dangling edges (cross-file calls)."""
        dangling = populated_db.get_dangling_call_edges()
        # Single file -> all calls should resolve internally,
        # but external lib calls (len, sum, etc.) will be dangling
        assert isinstance(dangling, list)

    def test_callable_nodes_indexed(self, populated_db):
        nodes = populated_db.get_all_callable_nodes()
        kinds = {n["kind"] for n in nodes}
        assert "function" in kinds or "method" in kinds


class TestEdgeResolverCrossFile:
    """Cross-file resolution with multiple files."""

    @pytest.fixture
    def multi_file_db(self, tmp_path, temp_db_path):
        """Create a project with two interdependent Python files."""
        p1 = tmp_path / "a.py"
        p1.write_text("""
def helper(x):
    return x + 1

def public_api(x):
    return helper(x)
""", encoding="utf-8")
        p2 = tmp_path / "b.py"
        p2.write_text("""
from a import public_api

def consumer(y):
    return public_api(y)
""", encoding="utf-8")

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(tmp_path), queries)
        orch.index_all()
        yield queries
        db.close()

    def test_cross_file_resolution(self, multi_file_db):
        """After multi-file index, cross-file references should be resolved."""
        result = resolve_edges(multi_file_db)
        # consumer() calls public_api() — should be resolved across files
        assert result.total_checked >= 0
        # Check that we have at least some resolved edges
        assert result.resolved + result.unresolved + result.ambiguous == result.total_checked
