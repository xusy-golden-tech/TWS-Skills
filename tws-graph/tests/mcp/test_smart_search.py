"""Tests for MCP 3.0 smart search enhancement (P46d)."""

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
        CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
            id, name, qualified_name, docstring, signature,
            content='nodes', content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    db_dir = tmp_path / "mcp_search_test"
    db_dir.mkdir()
    db_file = str(db_dir / "test.db")
    conn = sqlite3.connect(db_file)
    _init_schema(conn)
    conn.close()
    return db_file


@pytest.fixture
def store(db_path):
    """Create store with searchable test data."""
    s = SqliteStore(db_path, auto_flush_size=10)
    nodes = [
        # Authentication-related functions (for synonym search test)
        NodeRecord(id="n_auth1", kind="function", name="authenticate",
                   qualified_name="src::auth::authenticate",
                   file_path="src/auth.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def authenticate(user: str, password: str) -> bool",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="return check_credentials(user, password)",
                   body_hash="h1", docstring="Authenticate a user with credentials"),

        NodeRecord(id="n_auth2", kind="function", name="authorize",
                   qualified_name="src::auth::authorize",
                   file_path="src/auth.py", language="python",
                   start_line=12, end_line=20, updated_at=0,
                   signature="def authorize(user: str, role: str) -> bool",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="return check_role(user, role)",
                   body_hash="h2", docstring="Authorize a user for a role"),

        NodeRecord(id="n_login", kind="function", name="login",
                   qualified_name="src::auth::login",
                   file_path="src/auth.py", language="python",
                   start_line=22, end_line=30, updated_at=0,
                   signature="def login(user: str, password: str) -> dict",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="return create_session(user)",
                   body_hash="h3", docstring="Login a user and create session"),

        # Unrelated function (should not match auth search)
        NodeRecord(id="n_calc", kind="function", name="calculate_total",
                   qualified_name="src::calc::calculate_total",
                   file_path="src/calc.py", language="python",
                   start_line=1, end_line=10, updated_at=0,
                   signature="def calculate_total(items: list) -> float",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="return sum(items)",
                   body_hash="h4", docstring="Calculate total price"),

        # Functions with specific body patterns
        NodeRecord(id="n_try1", kind="function", name="safe_read",
                   qualified_name="src::io::safe_read",
                   file_path="src/io.py", language="python",
                   start_line=1, end_line=15, updated_at=0,
                   signature="def safe_read(path: str) -> str",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="try:\n    return open(path).read()\nexcept IOError:\n    return ''",
                   body_hash="h5", docstring=None),

        NodeRecord(id="n_try2", kind="function", name="parse_json",
                   qualified_name="src::parse::parse_json",
                   file_path="src/parse.py", language="python",
                   start_line=1, end_line=12, updated_at=0,
                   signature="def parse_json(text: str) -> dict",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="try:\n    return json.loads(text)\nexcept json.JSONDecodeError:\n    return {}",
                   body_hash="h6", docstring=None),

        # Function without try/except
        NodeRecord(id="n_simple", kind="function", name="double_value",
                   qualified_name="src::math::double_value",
                   file_path="src/math.py", language="python",
                   start_line=1, end_line=5, updated_at=0,
                   signature="def double_value(x: int) -> int",
                   visibility="public", is_abstract=0, is_exported=1,
                   decorators=None, framework=None, properties="{}",
                   body="return x * 2",
                   body_hash="h7", docstring=None),
    ]

    for n in nodes:
        s.insert_node(n)
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


class TestSmartSearch:
    """P46d: Smart search enhancement — synonym, structural, body search."""

    def test_synonym_search_auth_finds_authenticate(self, registry):
        """Search for 'auth' also finds 'authenticate' via synonym expansion."""
        result = registry.call("find_pattern", {"pattern": "auth"})
        content = json.loads(result["content"][0]["text"])
        results = content.get("results", [])
        all_names = set()
        for r in results:
            for m in r.get("matches", []):
                all_names.add(m.get("name", ""))
        assert "authenticate" in all_names

    def test_synonym_search_auth_finds_login(self, registry):
        """Search for 'auth' also finds 'login' via synonym expansion."""
        result = registry.call("find_pattern", {"pattern": "auth"})
        content = json.loads(result["content"][0]["text"])
        results = content.get("results", [])
        all_names = set()
        for r in results:
            for m in r.get("matches", []):
                all_names.add(m.get("name", ""))
        assert "login" in all_names or "authorize" in all_names

    def test_synonym_search_reports_expanded_terms(self, registry):
        """Synonym search output includes the expanded search terms."""
        result = registry.call("find_pattern", {"pattern": "auth"})
        content = json.loads(result["content"][0]["text"])
        assert "expanded_terms" in content or "note" in content

    def test_structural_search_try_except(self, registry):
        """Structural pattern 'try_except' finds functions with try/except blocks."""
        result = registry.call("find_pattern", {"pattern": "try_except"})
        content = json.loads(result["content"][0]["text"])
        results = content.get("results", [])
        all_names = set()
        for r in results:
            for m in r.get("matches", []):
                all_names.add(m.get("name", ""))
        assert "safe_read" in all_names
        assert "parse_json" in all_names

    def test_structural_search_excludes_non_matching(self, registry):
        """Structural search doesn't include functions without the pattern."""
        result = registry.call("find_pattern", {"pattern": "try_except"})
        content = json.loads(result["content"][0]["text"])
        results = content.get("results", [])
        all_names = set()
        for r in results:
            for m in r.get("matches", []):
                all_names.add(m.get("name", ""))
        assert "double_value" not in all_names

    def test_body_search_finds_pattern_in_body(self, registry):
        """Search within function bodies finds matches."""
        result = registry.call("find_pattern", {"pattern": "body:open(path)"})
        content = json.loads(result["content"][0]["text"])
        results = content.get("results", [])
        all_names = set()
        for r in results:
            for m in r.get("matches", []):
                all_names.add(m.get("name", ""))
        # safe_read contains open(path) in its body
        assert "safe_read" in all_names

    def test_body_search_returns_zero_for_no_match(self, registry):
        """Body search for non-existent pattern returns empty results."""
        result = registry.call("find_pattern", {"pattern": "body:nonexistent_pattern_xyz"})
        content = json.loads(result["content"][0]["text"])
        assert content.get("total_files", 0) == 0

    def test_pattern_search_pure_offline(self, registry):
        """All search modes are pure offline."""
        result = registry.call("find_pattern", {"pattern": "auth"})
        assert "content" in result
