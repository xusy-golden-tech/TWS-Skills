"""Tests for MCP 2.0 development assistant tools (P44).

Tests the four new tools: review_changes, safe_refactor, api_compat_check, find_pattern.
Uses SqliteStore (temp database) — the real MCP server scenario.
"""

import json
import sqlite3
import pytest
from pathlib import Path

from tws_graph.mcp.registry import ToolRegistry
from tws_graph.mcp.tools.dev_assist import register_tools
from tws_graph.store.sqlite_store import SqliteStore
from tws_graph.store.types import NodeRecord, EdgeRecord


def _init_schema(conn):
    """Create the full database schema (matches sqlite_store.py expectations)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            checksum TEXT
        );
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
        CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
            VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
            VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
            VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
            INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
            VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
        END;
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
        CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source, target);
        CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        INSERT INTO schema_versions (version, applied_at, description) VALUES (1, 0, 'v1');
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temp SQLite database with full schema."""
    db_dir = tmp_path / "mcp_test"
    db_dir.mkdir()
    db_file = str(db_dir / "test.db")
    conn = sqlite3.connect(db_file)
    _init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def store(db_path):
    """Create SqliteStore with sample data."""
    s = SqliteStore(db_path, auto_flush_size=10)
    nodes = [
        NodeRecord(
            id="n1", kind="function", name="calculate_total",
            qualified_name="src::utils::calculate_total",
            file_path="src/utils.py", language="python",
            start_line=10, end_line=25, updated_at=0,
            signature="def calculate_total(items: list) -> float",
            visibility="public", is_abstract=0, is_exported=1,
            decorators=None, framework=None, properties="{}",
            body="...", body_hash="h1", docstring="Calculate total price",
        ),
        NodeRecord(
            id="n2", kind="function", name="validate_input",
            qualified_name="src::utils::validate_input",
            file_path="src/utils.py", language="python",
            start_line=30, end_line=40, updated_at=0,
            signature="def validate_input(data: dict) -> bool",
            visibility="public", is_abstract=0, is_exported=1,
            decorators=None, framework=None, properties="{}",
            body="...", body_hash="h2", docstring="Validate input data",
        ),
        NodeRecord(
            id="n3", kind="function", name="process_order",
            qualified_name="src::service::process_order",
            file_path="src/service.py", language="python",
            start_line=5, end_line=50, updated_at=0,
            signature="def process_order(order_id: str) -> dict",
            visibility="public", is_abstract=0, is_exported=1,
            decorators=None, framework=None, properties="{}",
            body="...", body_hash="h3", docstring="Process an order",
        ),
        NodeRecord(
            id="n4", kind="class", name="OrderHandler",
            qualified_name="src::service::OrderHandler",
            file_path="src/service.py", language="python",
            start_line=1, end_line=60, updated_at=0,
            signature="class OrderHandler",
            visibility="public", is_abstract=0, is_exported=1,
            decorators=None, framework=None, properties="{}",
            body="...", body_hash="h4", docstring="Order handler class",
        ),
        NodeRecord(
            id="n5", kind="function", name="test_calculate",
            qualified_name="tests::test_utils::test_calculate",
            file_path="tests/test_utils.py", language="python",
            start_line=5, end_line=15, updated_at=0,
            signature="def test_calculate()",
            visibility="public", is_abstract=0, is_exported=0,
            decorators=None, framework="pytest", properties="{}",
            body="...", body_hash="h5", docstring=None,
        ),
        NodeRecord(
            id="n6", kind="function", name="test_process",
            qualified_name="tests::test_service::test_process",
            file_path="tests/test_service.py", language="python",
            start_line=1, end_line=20, updated_at=0,
            signature="def test_process()",
            visibility="public", is_abstract=0, is_exported=0,
            decorators=None, framework="pytest", properties="{}",
            body="...", body_hash="h6", docstring=None,
        ),
    ]
    edges = [
        EdgeRecord(source="n3", target="n1", kind="calls", source_loc="src/service.py:10"),
        EdgeRecord(source="n3", target="n2", kind="calls", source_loc="src/service.py:15"),
        EdgeRecord(source="n3", target="n4", kind="references", source_loc="src/service.py:3"),
        EdgeRecord(source="n5", target="n1", kind="calls", source_loc="tests/test_utils.py:8"),
        EdgeRecord(source="n6", target="n3", kind="calls", source_loc="tests/test_service.py:10"),
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
    """Create a ToolRegistry with dev_assist tools."""
    reg = ToolRegistry()
    register_tools(reg, lambda: store)
    return reg


class TestReviewChanges:
    """Tests for review_changes tool."""

    def test_review_by_file_paths(self, registry):
        result = registry.call("review_changes", {
            "file_paths": ["src/utils.py"],
        })
        content = json.loads(result["content"][0]["text"])
        assert content["count"] > 0
        assert content["risk_summary"]["high"] + content["risk_summary"]["medium"] + content["risk_summary"]["low"] > 0

    def test_review_by_symbol_names(self, registry):
        result = registry.call("review_changes", {
            "symbol_names": ["calculate_total"],
        })
        content = json.loads(result["content"][0]["text"])
        assert content["count"] > 0

    def test_review_finds_test_files(self, registry):
        result = registry.call("review_changes", {
            "file_paths": ["src/utils.py"],
        })
        content = json.loads(result["content"][0]["text"])
        suggested = content.get("suggested_tests", [])
        assert any("test" in t.lower() for t in suggested)

    def test_review_empty_input_returns_error(self, registry):
        result = registry.call("review_changes", {})
        content = json.loads(result["content"][0]["text"])
        assert "error" in content


class TestSafeRefactor:
    """Tests for safe_refactor tool."""

    def test_refactor_rename(self, registry):
        result = registry.call("safe_refactor", {
            "symbol_name": "calculate_total",
            "change_type": "rename",
        })
        content = json.loads(result["content"][0]["text"])
        assert content["change_type"] == "rename"
        assert content["total_dependents"] > 0

    def test_refactor_delete_with_dependents(self, registry):
        result = registry.call("safe_refactor", {
            "symbol_name": "calculate_total",
            "change_type": "delete",
        })
        content = json.loads(result["content"][0]["text"])
        assert not content["safe"]  # has dependents → not safe
        assert len(content["issues"]) > 0

    def test_refactor_symbol_not_found(self, registry):
        result = registry.call("safe_refactor", {
            "symbol_name": "nonexistent_func",
            "change_type": "rename",
        })
        content = json.loads(result["content"][0]["text"])
        assert not content["safe"]
        assert "not found" in str(content.get("reason", "")).lower()

    def test_refactor_returns_checklist(self, registry):
        result = registry.call("safe_refactor", {
            "symbol_name": "process_order",
            "change_type": "rename",
        })
        content = json.loads(result["content"][0]["text"])
        assert "checklist" in content
        assert len(content["checklist"]) > 0

    def test_refactor_missing_required_params(self, registry):
        result = registry.call("safe_refactor", {})
        content = json.loads(result["content"][0]["text"])
        assert "error" in content


class TestApiCompatCheck:
    """Tests for api_compat_check tool."""

    def test_compat_check_basic(self, registry):
        result = registry.call("api_compat_check", {
            "symbol_name": "calculate_total",
        })
        content = json.loads(result["content"][0]["text"])
        assert "compatibility" in content
        assert "bump" in content

    def test_compat_check_with_signatures(self, registry):
        result = registry.call("api_compat_check", {
            "symbol_name": "process_order",
            "old_signature": "def process_order(order_id: str) -> dict",
            "new_signature": "def process_order(order_id: str, tax: float = 0.0) -> dict",
        })
        content = json.loads(result["content"][0]["text"])
        assert content["kind"] == "function"

    def test_compat_check_returns_semver_guidance(self, registry):
        result = registry.call("api_compat_check", {
            "symbol_name": "calculate_total",
        })
        content = json.loads(result["content"][0]["text"])
        assert "semver_guidance" in content

    def test_compat_check_symbol_not_found(self, registry):
        result = registry.call("api_compat_check", {
            "symbol_name": "nonexistent_func",
        })
        content = json.loads(result["content"][0]["text"])
        assert content.get("compatibility") == "unknown"


class TestFindPattern:
    """Tests for find_pattern tool."""

    def test_find_pattern_basic(self, registry):
        result = registry.call("find_pattern", {
            "pattern": "function",
        })
        content = json.loads(result["content"][0]["text"])
        assert content.get("total_files", 0) >= 0

    def test_find_pattern_with_language_filter(self, registry):
        result = registry.call("find_pattern", {
            "pattern": "function",
            "language": "python",
        })
        content = json.loads(result["content"][0]["text"])
        assert "results" in content

    def test_find_pattern_empty_returns_error(self, registry):
        result = registry.call("find_pattern", {})
        content = json.loads(result["content"][0]["text"])
        assert "error" in content

    def test_find_pattern_with_limit(self, registry):
        result = registry.call("find_pattern", {
            "pattern": "function",
            "limit": 5,
        })
        content = json.loads(result["content"][0]["text"])
        assert "results" in content


class TestMCP2ToolRegistration:
    """Verify all 4 new tools are registered."""

    def test_all_four_tools_registered(self, registry):
        tools = registry.list_tools()
        tool_names = {t["name"] for t in tools}
        assert "review_changes" in tool_names
        assert "safe_refactor" in tool_names
        assert "api_compat_check" in tool_names
        assert "find_pattern" in tool_names

    def test_each_tool_has_input_schema(self, registry):
        tools = registry.list_tools()
        for t in tools:
            assert "inputSchema" in t
            assert t["description"]
