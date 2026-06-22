"""Tests for CLI analyze command — graph analysis algorithms via CLI."""

import json
import os
import pytest
from typer.testing import CliRunner
from pathlib import Path

from tws_graph.cli import app
from tws_graph.store import SqliteStore


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
# Test: analyze --help
# ============================================================================


class TestAnalyzeHelp:
    """Tests for ``tws-graph analyze --help``."""

    def test_analyze_help_shows(self, runner):
        """analyze --help should show usage and options."""
        result = runner.invoke(app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output.lower() or "analyze" in result.output
        assert "algorithm" in result.output.lower() or "algorithm" in result.output

    def test_analyze_appears_in_main_help(self, runner):
        """Main help should list the analyze command."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output.lower() or "analyze" in result.output


# ============================================================================
# Test: analyze clone
# ============================================================================


class TestAnalyzeClone:
    """Tests for ``tws-graph analyze --algorithm clone``."""

    def test_analyze_clone_success(self, runner, indexed_db):
        """Clone detection against an indexed project succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "clone",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        # Output should contain clone detection description
        assert "MinHash" in result.output or "clone" in result.output.lower()

    def test_analyze_clone_custom_threshold(self, runner, indexed_db):
        """Clone detection with custom threshold succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "clone",
            "--threshold", "0.5",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"

    def test_analyze_clone_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON for clone detection."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "clone",
            "--json",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        data = json.loads(result.output)
        assert "clone" in data
        assert "data" in data["clone"]
        assert "similar_pairs" in data["clone"]["data"]


# ============================================================================
# Test: analyze community
# ============================================================================


class TestAnalyzeCommunity:
    """Tests for ``tws-graph analyze --algorithm community``."""

    def test_analyze_community_success(self, runner, indexed_db):
        """Community detection against an indexed project succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "community",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        assert "Community" in result.output or "community" in result.output.lower()

    def test_analyze_community_with_edge_kinds(self, runner, indexed_db):
        """Community detection with custom edge kinds succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "community",
            "--edge-kinds", "calls,imports",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"

    def test_analyze_community_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON for community detection."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "community",
            "--json",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        data = json.loads(result.output)
        assert "community" in data
        assert "data" in data["community"]
        assert "communities" in data["community"]["data"]


# ============================================================================
# Test: analyze centrality
# ============================================================================


class TestAnalyzeCentrality:
    """Tests for ``tws-graph analyze --algorithm centrality``."""

    def test_analyze_centrality_success(self, runner, indexed_db):
        """Centrality computation against an indexed project succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "centrality",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        assert "pagerank" in result.output.lower() or "centrality" in result.output.lower()

    def test_analyze_centrality_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON for centrality computation."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "centrality",
            "--json",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        data = json.loads(result.output)
        assert "centrality" in data
        assert "data" in data["centrality"]
        assert "scores" in data["centrality"]["data"]


# ============================================================================
# Test: analyze cycle
# ============================================================================


class TestAnalyzeCycle:
    """Tests for ``tws-graph analyze --algorithm cycle``."""

    def test_analyze_cycle_success(self, runner, indexed_db):
        """Cycle detection against an indexed project succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "cycle",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        assert "Cycle" in result.output or "cycle" in result.output.lower()

    def test_analyze_cycle_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON for cycle detection."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "cycle",
            "--json",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        data = json.loads(result.output)
        assert "cycle" in data
        assert "data" in data["cycle"]
        assert "cycles" in data["cycle"]["data"]


# ============================================================================
# Test: analyze all
# ============================================================================


class TestAnalyzeAll:
    """Tests for ``tws-graph analyze --algorithm all``."""

    def test_analyze_all_success(self, runner, indexed_db):
        """Running all algorithms succeeds."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "all",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        # Should contain all four algorithm outputs
        assert "clone" in result.output.lower()
        assert "community" in result.output.lower()
        assert "centrality" in result.output.lower() or "page" in result.output.lower()
        assert "cycle" in result.output.lower()

    def test_analyze_all_json_output(self, runner, indexed_db):
        """--json flag produces valid JSON with all four algorithms."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "all",
            "--json",
            "--db", indexed_db,
        ])
        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.stderr}"
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "clone" in data
        assert "community" in data
        assert "centrality" in data
        assert "cycle" in data
        # Each entry should have standard fields
        for key in ("clone", "community", "centrality", "cycle"):
            entry = data[key]
            assert "success" in entry
            assert entry["success"] is True
            assert "duration_ms" in entry
            assert isinstance(entry["duration_ms"], (int, float))


# ============================================================================
# Test: analyze error cases
# ============================================================================


class TestAnalyzeErrors:
    """Tests for ``tws-graph analyze`` error handling."""

    def test_analyze_no_db(self, runner, db_path):
        """Analyze without indexed DB should fail gracefully."""
        nonexistent = str(Path(db_path).parent / "nonexistent.db")
        result = runner.invoke(app, [
            "analyze", "--algorithm", "clone",
            "--db", nonexistent,
        ])
        assert result.exit_code == 1
        assert "索引数据库不存在" in result.output

    def test_analyze_invalid_algorithm(self, runner, indexed_db):
        """Invalid algorithm name should fail with helpful message."""
        result = runner.invoke(app, [
            "analyze", "--algorithm", "nonexistent",
            "--db", indexed_db,
        ])
        assert result.exit_code == 1
        assert "未知算法" in result.output

    def test_analyze_each_algorithm_individually(self, runner, indexed_db):
        """Each algorithm should succeed when run individually."""
        for algo in ["clone", "community", "centrality", "cycle"]:
            result = runner.invoke(app, [
                "analyze", "--algorithm", algo,
                "--db", indexed_db,
            ])
            assert result.exit_code == 0, \
                f"Algorithm '{algo}' failed: {result.output}\n{result.stderr}"
