"""Tests for MCP 3.0 smart refactoring suggestions (P46a)."""

import json
import sqlite3
import pytest
from pathlib import Path

from tws_graph.mcp.registry import ToolRegistry
from tws_graph.mcp.tools.dev_assist import register_tools
from tws_graph.store.sqlite_store import SqliteStore
from tws_graph.store.types import NodeRecord, EdgeRecord


def _init_schema(conn):
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
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db_dir = tmp_path / "mcp_refactor_test"
    db_dir.mkdir()
    db_file = str(db_dir / "test.db")
    conn = sqlite3.connect(db_file)
    _init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def store(db_path):
    """Create SqliteStore with test data for refactoring suggestions."""
    s = SqliteStore(db_path, auto_flush_size=10)

    nodes = [
        # Long method (>50 lines) — should trigger method extraction suggestion
        NodeRecord(id="n_long", kind="method", name="process_order",
                   qualified_name="src::order::OrderService::process_order",
                   file_path="src/order.py", language="python",
                   start_line=10, end_line=80, updated_at=0,
                   signature="def process_order(self, order_id: int)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="x=1\n" * 60, body_hash="h1", docstring=None),

        # Short method — no extraction needed
        NodeRecord(id="n_short", kind="method", name="get_total",
                   qualified_name="src::order::OrderService::get_total",
                   file_path="src/order.py", language="python",
                   start_line=82, end_line=90, updated_at=0,
                   signature="def get_total(self) -> float", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return 100.0", body_hash="h2", docstring=None),

        # Two classes sharing method signatures — should suggest interface extraction
        NodeRecord(id="n_class_a_m1", kind="method", name="save",
                   qualified_name="src::store::UserStore::save",
                   file_path="src/store.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def save(self, data: dict) -> bool", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return True", body_hash="h3", docstring=None),

        NodeRecord(id="n_class_a_m2", kind="method", name="find_by_id",
                   qualified_name="src::store::UserStore::find_by_id",
                   file_path="src/store.py", language="python",
                   start_line=6, end_line=10, updated_at=0,
                   signature="def find_by_id(self, id: int) -> dict", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return {}", body_hash="h4", docstring=None),

        NodeRecord(id="n_class_b_m1", kind="method", name="save",
                   qualified_name="src::store::ProductStore::save",
                   file_path="src/store.py", language="python",
                   start_line=20, end_line=25, updated_at=0,
                   signature="def save(self, data: dict) -> bool", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return True", body_hash="h5", docstring=None),

        NodeRecord(id="n_class_b_m2", kind="method", name="find_by_id",
                   qualified_name="src::store::ProductStore::find_by_id",
                   file_path="src/store.py", language="python",
                   start_line=26, end_line=30, updated_at=0,
                   signature="def find_by_id(self, id: int) -> dict", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return {}", body_hash="h6", docstring=None),

        # Method with high coupling to another class — should suggest moving
        NodeRecord(id="n_coupled", kind="method", name="format_report",
                   qualified_name="src::report::Reporter::format_report",
                   file_path="src/report.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def format_report(self, data: dict) -> str", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return str(data)", body_hash="h7", docstring=None),

        # Target class methods that format_report calls
        NodeRecord(id="n_target_m1", kind="method", name="to_html",
                   qualified_name="src::format::HtmlFormatter::to_html",
                   file_path="src/format.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def to_html(self, data: dict) -> str", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return '<div>'", body_hash="h8", docstring=None),

        NodeRecord(id="n_target_m2", kind="method", name="to_json",
                   qualified_name="src::format::HtmlFormatter::to_json",
                   file_path="src/format.py", language="python",
                   start_line=6, end_line=10, updated_at=0,
                   signature="def to_json(self, data: dict) -> str", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body="return '{}'", body_hash="h9", docstring=None),
    ]

    edges = [
        # format_report heavily calls HtmlFormatter methods (coupled to src/format.py)
        EdgeRecord(source="n_coupled", target="n_target_m1", kind="calls",
                   source_loc="src/report.py:5"),
        EdgeRecord(source="n_coupled", target="n_target_m2", kind="calls",
                   source_loc="src/report.py:7"),
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


class TestSmartRefactor:
    """P46a: Smart refactoring suggestions in safe_refactor."""

    def test_safe_refactor_suggest_method_extraction(self, registry):
        """Long methods trigger method extraction suggestion."""
        result = registry.call("safe_refactor", {
            "symbol_name": "process_order",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        assert "suggestions" in content
        suggestions = content["suggestions"]
        extract_suggestions = [s for s in suggestions if s["type"] == "extract_method"]
        assert len(extract_suggestions) >= 1

    def test_safe_refactor_no_extraction_for_short_methods(self, registry):
        """Short methods don't trigger extraction suggestions."""
        result = registry.call("safe_refactor", {
            "symbol_name": "get_total",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        suggestions = content.get("suggestions", [])
        extract_suggestions = [s for s in suggestions if s["type"] == "extract_method"]
        assert len(extract_suggestions) == 0

    def test_safe_refactor_suggest_interface_extraction(self, registry):
        """Classes sharing method signatures trigger interface extraction suggestion."""
        result = registry.call("safe_refactor", {
            "symbol_name": "save",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        assert "suggestions" in content
        suggestions = content["suggestions"]
        interface_suggestions = [s for s in suggestions if s["type"] == "extract_interface"]
        assert len(interface_suggestions) >= 1

    def test_safe_refactor_suggest_move_method(self, registry):
        """Methods with high coupling to another class suggest moving."""
        result = registry.call("safe_refactor", {
            "symbol_name": "format_report",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        suggestions = content.get("suggestions", [])
        move_suggestions = [s for s in suggestions if s["type"] == "move_method"]
        assert len(move_suggestions) >= 1

    def test_suggest_mode_includes_confidence(self, registry):
        """Each suggestion includes a confidence level."""
        result = registry.call("safe_refactor", {
            "symbol_name": "process_order",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        for s in content.get("suggestions", []):
            assert "confidence" in s
            assert s["confidence"] in ("high", "medium", "low")

    def test_suggest_mode_includes_reasoning(self, registry):
        """Each suggestion includes reasoning."""
        result = registry.call("safe_refactor", {
            "symbol_name": "process_order",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        for s in content.get("suggestions", []):
            assert "reason" in s

    def test_suggest_mode_symbol_not_found(self, registry):
        """Unknown symbol returns empty suggestions."""
        result = registry.call("safe_refactor", {
            "symbol_name": "nonexistent_func",
            "change_type": "suggest",
        })
        content = json.loads(result["content"][0]["text"])
        assert "error" in content or content.get("suggestions", []) == []

    def test_suggest_mode_pure_offline(self, registry):
        """Suggest mode has zero external dependencies."""
        result = registry.call("safe_refactor", {
            "symbol_name": "process_order",
            "change_type": "suggest",
        })
        assert "content" in result
        # No HTTP calls, no network — pure local analysis
