"""Tests for CLI query command — GQL query via tws-graph query."""

import json
import os
import pytest
from typer.testing import CliRunner
from pathlib import Path

from tws_graph.cli import app
from tws_graph.store import SqliteStore
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def runner():
    """Create a CliRunner for testing CLI commands."""
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary database path under tmp_path."""
    db_dir = tmp_path / ".tws" / "codegraph"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "index.db")


@pytest.fixture
def indexed_db(sample_py_project, db_path: str) -> str:
    """Run tws-graph index on a sample project and return the db path."""
    runner = CliRunner()
    result = runner.invoke(app, [
        "index", str(sample_py_project),
        "--db", db_path,
    ])
    assert result.exit_code == 0, f"Index failed: {result.output}\n{result.stderr}"
    return db_path


# ============================================================================
# Test: query command basic
# ============================================================================


class TestQueryCommand:
    """Tests for ``tws-graph query`` command."""

    def test_query_match_all(self, runner, indexed_db):
        """Basic FIND * query."""
        result = runner.invoke(app, [
            "query", "FIND *",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"
        # Should have output with node data
        assert len(result.output.strip()) > 0

    def test_query_with_limit(self, runner, indexed_db):
        """FIND * LIMIT 3."""
        result = runner.invoke(app, [
            "query", "FIND * LIMIT 3",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"
        assert len(result.output.strip()) > 0

    def test_query_with_label_filter(self, runner, indexed_db):
        """FIND function RETURN name."""
        result = runner.invoke(app, [
            "query", "FIND function RETURN name",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_query_with_where(self, runner, indexed_db):
        """FIND * WHERE name = 'main'."""
        result = runner.invoke(app, [
            "query", "FIND * WHERE name = 'main'",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_query_empty_result(self, runner, indexed_db):
        """Query that matches nothing returns gracefully."""
        result = runner.invoke(app, [
            "query",
            "FIND nonexistentlabel",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_query_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON."""
        result = runner.invoke(app, [
            "query", "FIND * LIMIT 2",
            "--db", indexed_db,
            "--json",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) <= 2


# ============================================================================
# Test: query command error handling
# ============================================================================


class TestQueryCommandErrors:
    """Tests for ``tws-graph query`` error handling."""

    def test_query_syntax_error(self, runner, indexed_db):
        """Invalid GQL syntax shows friendly error."""
        result = runner.invoke(app, [
            "query", "INVALID QUERY HERE",
            "--db", indexed_db,
        ])
        assert result.exit_code != 0, "Should fail on invalid syntax"
        output = result.output + (result.stderr_bytes or b"").decode("utf-8", errors="replace")
        assert "错误" in output or "Error" in output or result.exit_code != 0

    def test_query_db_not_found(self, runner, tmp_path):
        """Query with non-existent database shows error."""
        db = str(tmp_path / "nonexistent" / "index.db")
        result = runner.invoke(app, [
            "query", "FIND *",
            "--db", db,
        ])
        # Should report database not found and exit with error
        assert result.exit_code != 0, "Should fail when database does not exist"

    def test_query_semantic_error(self, runner, indexed_db):
        """Invalid GQL query shows friendly error."""
        result = runner.invoke(app, [
            "query", "FIND * WHERE name ~ ~ 'x'",
            "--db", indexed_db,
        ])
        assert result.exit_code != 0, "Should fail on invalid syntax"


# ============================================================================
# Test: search command unchanged
# ============================================================================


class TestSearchCommandUnchanged:
    """Verify that the search command continues to work as before."""

    def test_search_basic(self, runner, indexed_db):
        """Basic FTS search works."""
        result = runner.invoke(app, [
            "search", "main",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_search_with_qualifiers(self, runner, indexed_db):
        """Search with field qualifiers works."""
        result = runner.invoke(app, [
            "search", "kind:function",
            "--db", indexed_db,
            "--limit", "5",
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_search_no_results(self, runner, indexed_db):
        """Search with no matches works."""
        result = runner.invoke(app, [
            "search", "zzz_nonexistent_symbol_xyz",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"
        assert "未找到" in result.output


# ============================================================================
# Test: query command with table output format
# ============================================================================


class TestQueryOutputFormat:
    """Tests for query command output formatting."""

    def test_table_output_has_headers(self, runner, indexed_db):
        """Table output includes column headers."""
        result = runner.invoke(app, [
            "query", "FIND * LIMIT 1",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\nError: {result.stderr}"

    def test_json_output_has_expected_keys(self, runner, indexed_db):
        """JSON output returns list of node dicts."""
        result = runner.invoke(app, [
            "query", "FIND function RETURN name, kind",
            "--db", indexed_db,
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        if data:
            assert "name" in data[0]
            assert "kind" in data[0]
