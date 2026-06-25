"""Tests for MCP 3.0 security scan tool (P46b)."""

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
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
        CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
        INSERT INTO schema_versions (version, applied_at, description) VALUES (1, 0, 'v1');
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db_dir = tmp_path / "mcp_sec_test"
    db_dir.mkdir()
    db_file = str(db_dir / "test.db")
    conn = sqlite3.connect(db_file)
    _init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def store(db_path):
    """Create SqliteStore with security-relevant test data."""
    s = SqliteStore(db_path, auto_flush_size=10)
    nodes = [
        # Secure code
        NodeRecord(id="n1", kind="function", name="safe_query",
                   qualified_name="src::service::safe_query",
                   file_path="src/service.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def safe_query(user_id: int)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
                   body_hash="h1", docstring=None),
        # SQL injection vulnerable
        NodeRecord(id="n2", kind="function", name="unsafe_query",
                   qualified_name="src::service::unsafe_query",
                   file_path="src/service.py", language="python",
                   start_line=20, end_line=30, updated_at=0,
                   signature="def unsafe_query(username: str)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='query = "SELECT * FROM users WHERE name = \'" + username + "\'"; cursor.execute(query)',
                   body_hash="h2", docstring=None),
        # Hardcoded secret
        NodeRecord(id="n3", kind="function", name="connect_db",
                   qualified_name="src::db::connect_db",
                   file_path="src/db.py", language="python",
                   start_line=1, end_line=8, updated_at=0,
                   signature="def connect_db()", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='password = "admin123"; conn = mysql.connect(password=password)',
                   body_hash="h3", docstring=None),
        # Path traversal risk
        NodeRecord(id="n4", kind="function", name="read_user_file",
                   qualified_name="src::api::read_user_file",
                   file_path="src/api.py", language="python",
                   start_line=10, end_line=18, updated_at=0,
                   signature="def read_user_file(filename: str)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='path = "/data/" + filename; return open(path).read()',
                   body_hash="h4", docstring=None),
        # Command injection risk
        NodeRecord(id="n5", kind="function", name="ping_host",
                   qualified_name="src::util::ping_host",
                   file_path="src/util.py", language="python",
                   start_line=5, end_line=12, updated_at=0,
                   signature="def ping_host(host: str)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='os.system("ping " + host)',
                   body_hash="h5", docstring=None),
        # Deserialization risk
        NodeRecord(id="n6", kind="function", name="load_state",
                   qualified_name="src::state::load_state",
                   file_path="src/state.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def load_state(data: bytes)", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='return pickle.loads(data)',
                   body_hash="h6", docstring=None),
        # Safe code (no issues)
        NodeRecord(id="n7", kind="function", name="calculate",
                   qualified_name="src::math::calculate",
                   file_path="src/math.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def calculate(x: int, y: int) -> int", visibility="public",
                   is_abstract=0, is_exported=1, decorators=None, framework=None,
                   properties="{}", body='return x + y',
                   body_hash="h7", docstring=None),
    ]
    edges = [
        EdgeRecord(source="n2", target="n8", kind="calls", source_loc="src/service.py:25"),
        EdgeRecord(source="n4", target="n9", kind="calls", source_loc="src/api.py:15"),
        EdgeRecord(source="n5", target="n10", kind="calls", source_loc="src/util.py:8"),
        EdgeRecord(source="n6", target="n11", kind="calls", source_loc="src/state.py:3"),
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


class TestSecurityScan:
    """P46b: Security vulnerability detection."""

    def test_security_scan_detects_sql_injection(self, registry):
        """Detects string concatenation in SQL query."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        findings = content.get("findings", [])
        sql_findings = [f for f in findings if f["category"] == "sql_injection"]
        assert len(sql_findings) >= 1

    def test_security_scan_detects_hardcoded_secret(self, registry):
        """Detects hardcoded passwords/secrets."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        findings = content.get("findings", [])
        secret_findings = [f for f in findings if f["category"] == "hardcoded_secret"]
        assert len(secret_findings) >= 1

    def test_security_scan_detects_path_traversal(self, registry):
        """Detects path concatenation with user input."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        findings = content.get("findings", [])
        path_findings = [f for f in findings if f["category"] == "path_traversal"]
        assert len(path_findings) >= 1

    def test_security_scan_detects_command_injection(self, registry):
        """Detects shell command concatenation."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        findings = content.get("findings", [])
        cmd_findings = [f for f in findings if f["category"] == "command_injection"]
        assert len(cmd_findings) >= 1

    def test_security_scan_detects_deserialization(self, registry):
        """Detects unsafe deserialization."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        findings = content.get("findings", [])
        deser_findings = [f for f in findings if f["category"] == "deserialization"]
        assert len(deser_findings) >= 1

    def test_security_scan_returns_severity(self, registry):
        """Each finding has a severity level."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        for f in content.get("findings", []):
            assert "severity" in f
            assert f["severity"] in ("critical", "high", "medium", "low", "info")

    def test_security_scan_returns_file_and_line(self, registry):
        """Each finding includes file path and line number."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        for f in content.get("findings", []):
            assert "file_path" in f
            assert "line" in f

    def test_security_scan_with_language_filter(self, registry):
        """Language filter works."""
        result = registry.call("security_scan", {"language": "python"})
        content = json.loads(result["content"][0]["text"])
        assert len(content["findings"]) >= 1

    def test_security_scan_empty_db_no_crash(self):
        """Empty database returns empty findings, no crash."""
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            _init_schema(conn)
            conn.close()
            s = SqliteStore(path, auto_flush_size=10)
            reg = ToolRegistry()
            register_tools(reg, lambda: s)
            result = reg.call("security_scan", {})
            content = json.loads(result["content"][0]["text"])
            assert content["total_findings"] == 0
            assert content["findings"] == []
            s.close()
        finally:
            os.unlink(path)

    def test_security_scan_filters_safe_code(self, registry):
        """Safe functions (like calculate) should not be flagged."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        flagged_files = {f["file_path"] for f in content["findings"]}
        assert "src/math.py" not in flagged_files

    def test_security_scan_summary_stats(self, registry):
        """Summary stats include counts by category."""
        result = registry.call("security_scan", {})
        content = json.loads(result["content"][0]["text"])
        assert "total_findings" in content
        assert content["total_findings"] >= 5  # At least one per category
        assert "summary" in content
        assert "sql_injection" in content["summary"]


class TestToolRegistration:
    """Verify security_scan tool is registered."""

    def test_security_scan_tool_registered(self, registry):
        tools = registry.list_tools()
        tool_names = {t["name"] for t in tools}
        assert "security_scan" in tool_names
        assert "review_changes" in tool_names
        assert "safe_refactor" in tool_names
        assert "api_compat_check" in tool_names
        assert "find_pattern" in tool_names

    def test_security_scan_has_input_schema(self, registry):
        tools = registry.list_tools()
        for t in tools:
            if t["name"] == "security_scan":
                assert "inputSchema" in t
                assert t["description"]
                break
        else:
            pytest.fail("security_scan tool not found")
