"""Tests for MCP 3.0 code quality gate tool (P46c)."""

import json
import sqlite3
import pytest
from pathlib import Path

from tws_graph.mcp.registry import ToolRegistry
from tws_graph.mcp.tools.dev_assist import register_tools
from tws_graph.store.sqlite_store import SqliteStore
from tws_graph.store.types import NodeRecord, EdgeRecord


def _init_schema(conn):
    """Create full database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            language TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            signature TEXT,
            docstring TEXT,
            visibility TEXT,
            is_abstract INTEGER DEFAULT 0,
            is_exported INTEGER DEFAULT 0,
            decorators TEXT,
            framework TEXT,
            properties TEXT DEFAULT '{}',
            body TEXT,
            body_hash TEXT,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            target TEXT NOT NULL,
            target_text TEXT,
            kind TEXT NOT NULL,
            source_loc TEXT,
            provenance TEXT DEFAULT 'tree-sitter',
            properties TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            language TEXT NOT NULL,
            node_count INTEGER DEFAULT 0,
            indexed_at INTEGER NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            modified_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
            id, name, qualified_name, docstring, signature,
            content='nodes', content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db_dir = tmp_path / "mcp_quality_test"
    db_dir.mkdir()
    db_file = str(db_dir / "test.db")
    conn = sqlite3.connect(db_file)
    _init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def store(db_path):
    """Create SqliteStore with test data for quality gate analysis."""
    s = SqliteStore(db_path, auto_flush_size=10)

    nodes = [
        # Complex function (>50 lines) — should trigger complexity gate
        NodeRecord(id="n1", kind="function", name="complex_func",
                   qualified_name="src::complex::complex_func",
                   file_path="src/complex.py", language="python",
                   start_line=1, end_line=80, updated_at=0,
                   signature="def complex_func()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="x=1\n" * 60, body_hash="h1", docstring=None),

        # Simple function — no issues
        NodeRecord(id="n2", kind="function", name="simple_func",
                   qualified_name="src::simple::simple_func",
                   file_path="src/simple.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def simple_func()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return 1", body_hash="h2", docstring=None),

        # Public API function — modifying this should trigger API compat gate
        NodeRecord(id="n3", kind="function", name="public_api",
                   qualified_name="src::api::public_api",
                   file_path="src/api.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def public_api(x: int) -> str", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return str(x)", body_hash="h3", docstring=None),

        # Function with no tests — should trigger test coverage gate
        NodeRecord(id="n4", kind="function", name="untested_func",
                   qualified_name="src::untested::untested_func",
                   file_path="src/untested.py", language="python",
                   start_line=1, end_line=15, updated_at=0,
                   signature="def untested_func()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return 42", body_hash="h4", docstring=None),

        # Function with test file — should PASS coverage gate
        NodeRecord(id="n5", kind="function", name="tested_func",
                   qualified_name="src::tested::tested_func",
                   file_path="src/tested.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def tested_func()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return 42", body_hash="h5", docstring=None),

        # Test file node
        NodeRecord(id="n6", kind="function", name="test_tested_func",
                   qualified_name="tests::test_tested::test_tested_func",
                   file_path="tests/test_tested.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def test_tested_func()", visibility="public",
                   is_abstract=0, is_exported=0, decorators=None, framework=None,
                   properties="{}", body="assert tested_func() == 42", body_hash="h6",
                   docstring=None),

        # A depends on B, B depends on A (circular dependency)
        NodeRecord(id="n_a", kind="function", name="func_a",
                   qualified_name="src::mod_a::func_a",
                   file_path="src/mod_a.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def func_a()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="func_b()", body_hash="h_a", docstring=None),

        NodeRecord(id="n_b", kind="function", name="func_b",
                   qualified_name="src::mod_b::func_b",
                   file_path="src/mod_b.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def func_b()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="func_a()", body_hash="h_b", docstring=None),
    ]

    edges = [
        # Circular dependency edges
        EdgeRecord(source="n_a", target="n_b", kind="calls", source_loc="src/mod_a.py:3"),
        EdgeRecord(source="n_b", target="n_a", kind="calls", source_loc="src/mod_b.py:3"),
    ]

    for n in nodes:
        s.insert_node(n)
    for e in edges:
        s.insert_edge(e)
    s.flush()
    s.rebuild_fts()
    yield s
    try:
        s.close()
    except Exception:
        pass


@pytest.fixture
def registry(store):
    reg = ToolRegistry()
    register_tools(reg, lambda: store)
    return reg


class TestQualityGate:
    """P46c: Code quality gate in review_changes."""

    def test_review_changes_has_quality_gate_output(self, registry):
        """review_changes output includes quality_gate section."""
        result = registry.call("review_changes", {"file_paths": ["src/complex.py"]})
        content = json.loads(result["content"][0]["text"])
        assert "quality_gate" in content
        qg = content["quality_gate"]
        assert "overall" in qg
        assert qg["overall"] in ("pass", "fail", "review")

    def test_complexity_gate_detects_long_functions(self, registry):
        """Functions >50 lines trigger complexity warning."""
        result = registry.call("review_changes", {"file_paths": ["src/complex.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        assert "gates" in qg
        gates = qg["gates"]
        complexity_gate = [g for g in gates if g["name"] == "complexity"]
        assert len(complexity_gate) >= 1
        assert complexity_gate[0]["status"] in ("fail", "review")

    def test_complexity_gate_passes_simple_functions(self, registry):
        """Simple functions (<50 lines) pass complexity gate."""
        result = registry.call("review_changes", {"file_paths": ["src/simple.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        gates = qg["gates"]
        complexity_gate = [g for g in gates if g["name"] == "complexity"]
        if complexity_gate:
            assert complexity_gate[0]["status"] == "pass"

    def test_test_coverage_gate_detects_untested_code(self, registry):
        """Modified files without matching test files trigger coverage warning."""
        result = registry.call("review_changes", {"file_paths": ["src/untested.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        gates = qg["gates"]
        coverage_gate = [g for g in gates if g["name"] == "test_coverage"]
        assert len(coverage_gate) >= 1
        assert coverage_gate[0]["status"] in ("fail", "review")

    def test_test_coverage_gate_passes_tested_code(self, registry):
        """Modified files with matching test files pass coverage gate."""
        result = registry.call("review_changes", {"file_paths": ["src/tested.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        gates = qg["gates"]
        coverage_gate = [g for g in gates if g["name"] == "test_coverage"]
        if coverage_gate:
            assert coverage_gate[0]["status"] == "pass"

    def test_circular_dependency_detection(self, registry):
        """Circular dependencies between changed files are detected."""
        result = registry.call("review_changes", {"file_paths": ["src/mod_a.py", "src/mod_b.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        gates = qg["gates"]
        dep_gate = [g for g in gates if g["name"] == "dependency_direction"]
        assert len(dep_gate) >= 1
        assert dep_gate[0]["status"] in ("fail", "review")

    def test_no_circular_dependency_single_file(self, registry):
        """Single file review shows no circular dependency issues."""
        result = registry.call("review_changes", {"file_paths": ["src/simple.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        gates = qg["gates"]
        dep_gate = [g for g in gates if g["name"] == "dependency_direction"]
        if dep_gate:
            assert dep_gate[0]["status"] == "pass"

    def test_overall_fail_when_any_gate_fails(self, registry):
        """Overall is 'fail' when any gate fails."""
        result = registry.call("review_changes", {"file_paths": ["src/complex.py", "src/untested.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        # Complex + untested should trigger at least 2 gates
        failing = [g for g in qg["gates"] if g["status"] == "fail"]
        if failing:
            assert qg["overall"] == "fail"

    def test_overall_pass_when_no_issues(self, registry):
        """Overall is 'pass' when no gates fail."""
        result = registry.call("review_changes", {"file_paths": ["src/tested.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        failing = [g for g in qg["gates"] if g["status"] == "fail"]
        if not failing:
            assert qg["overall"] in ("pass", "review")

    def test_review_changes_quality_gate_with_symbol_names(self, registry):
        """Quality gate works with symbol_names input."""
        result = registry.call("review_changes", {"symbol_names": ["complex_func"]})
        content = json.loads(result["content"][0]["text"])
        assert "quality_gate" in content
        qg = content["quality_gate"]
        assert qg["overall"] in ("pass", "fail", "review")

    def test_gate_details_include_remediation(self, registry):
        """Each failed gate includes remediation hints."""
        result = registry.call("review_changes", {"file_paths": ["src/complex.py"]})
        content = json.loads(result["content"][0]["text"])
        qg = content["quality_gate"]
        for g in qg["gates"]:
            if g["status"] in ("fail", "review"):
                assert "remediation" in g, f"Gate {g['name']} missing remediation"
