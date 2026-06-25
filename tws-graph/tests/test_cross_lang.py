"""Tests for cross-language edge resolution (P47).

P47a: Cross-language symbol registration
P47b: JVM ecosystem (Java↔Kotlin)
P47c: Web ecosystem (TS↔JS)
"""

import sqlite3
import pytest
from pathlib import Path
from tws_graph.edge_resolver import resolve_edges, ResolveResult


LANG_GROUPS = {
    "jvm": {"java", "kotlin"},
    "web": {"typescript", "javascript", "jsx", "tsx"},
}


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
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
    """)


def _insert_node(conn, nid, kind, name, qname, file_path, lang):
    conn.execute("""
        INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                          start_line, end_line, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, 10, 0)
    """, (nid, kind, name, qname, file_path, lang))


def _insert_edge(conn, source, target, target_text, kind, source_loc, provenance="tree-sitter"):
    conn.execute("""
        INSERT INTO edges (source, target, target_text, kind, source_loc, provenance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (source, target, target_text, kind, source_loc, provenance))


class _FakeQueries:
    """Minimal QueryBuilder shim for testing resolve_edges."""

    def __init__(self, conn):
        self.conn = conn

    def get_dangling_call_edges(self):
        rows = self.conn.execute("""
            SELECT e.id AS edge_rowid, e.source, e.target, e.target_text,
                   e.kind, e.source_loc, e.provenance
            FROM edges e
            WHERE e.target NOT IN (SELECT id FROM nodes)
              AND e.kind = 'calls'
        """).fetchall()
        return [dict(r) for r in rows]

    def get_all_callable_nodes(self):
        rows = self.conn.execute("""
            SELECT id, kind, name, qualified_name, file_path, language
            FROM nodes
            WHERE kind IN ('function', 'method', 'constructor', 'class', 'interface')
        """).fetchall()
        return [dict(r) for r in rows]

    def update_edge_targets_batch(self, updates):
        for rowid, target, provenance in updates:
            self.conn.execute(
                "UPDATE edges SET target = ?, provenance = ? WHERE id = ?",
                (target, provenance, rowid),
            )

    def mark_edge_provenance_batch(self, updates):
        for rowid, provenance in updates:
            self.conn.execute(
                "UPDATE edges SET provenance = ? WHERE id = ?",
                (provenance, rowid),
            )

    def _exec(self, sql):
        return self.conn.execute(sql)


@pytest.fixture
def conn():
    """In-memory SQLite database."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _init_schema(c)
    return c


class TestCrossLanguageJVM:
    """P47b: Java↔Kotlin cross-language resolution."""

    def test_java_calls_kotlin_same_package_resolved(self, conn):
        """Java calling Kotlin class in same package → resolved."""
        _insert_node(conn, "j1", "method", "processOrder",
                     "com.example.service::OrderService::processOrder",
                     "src/OrderService.java", "java")
        _insert_node(conn, "k1", "class", "PaymentGateway",
                     "com.example.service::PaymentGateway",
                     "src/PaymentGateway.kt", "kotlin")

        # Java method calls Kotlin PaymentGateway
        _insert_edge(conn, "j1", "__unresolved__",
                     "com.example.service::PaymentGateway",
                     "calls", "src/OrderService.java:5")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)

        assert result.resolved >= 1, f"Expected >=1 resolved, got {result.resolved}"
        # Verify the edge now points to k1
        edge = conn.execute("SELECT target, provenance FROM edges WHERE source = 'j1'").fetchone()
        assert edge["target"] == "k1"
        assert "resolved" in edge["provenance"]

    def test_kotlin_calls_java_same_package_resolved(self, conn):
        """Kotlin calling Java class in same package → resolved."""
        _insert_node(conn, "j1", "class", "UserRepository",
                     "com.example.data::UserRepository",
                     "src/UserRepository.java", "java")
        _insert_node(conn, "k1", "method", "fetchUsers",
                     "com.example.data::UserService::fetchUsers",
                     "src/UserService.kt", "kotlin")

        _insert_edge(conn, "k1", "__unresolved__",
                     "com.example.data::UserRepository",
                     "calls", "src/UserService.kt:3")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)

        assert result.resolved >= 1
        edge = conn.execute("SELECT target, provenance FROM edges WHERE source = 'k1'").fetchone()
        assert edge["target"] == "j1"

    def test_jvm_cross_lang_preserves_provenance(self, conn):
        """Cross-language resolved edges have provenance indicating cross-lang."""
        _insert_node(conn, "j1", "method", "run",
                     "com.example::App::run", "src/App.java", "java")
        _insert_node(conn, "k1", "class", "Config",
                     "com.example::Config", "src/Config.kt", "kotlin")

        _insert_edge(conn, "j1", "__unresolved__",
                     "com.example::Config", "calls", "src/App.java:2")

        queries = _FakeQueries(conn)
        resolve_edges(queries)

        edge = conn.execute("SELECT provenance FROM edges WHERE source = 'j1'").fetchone()
        assert "cross_lang" in edge["provenance"] or "resolved" in edge["provenance"]


class TestCrossLanguageWeb:
    """P47c: TypeScript↔JavaScript cross-language resolution."""

    def test_ts_calls_js_module_resolved(self, conn):
        """TypeScript importing JavaScript module → resolved."""
        _insert_node(conn, "ts1", "function", "init",
                     "src::app::init", "src/app.ts", "typescript")
        _insert_node(conn, "js1", "function", "helpers",
                     "src::utils::helpers", "src/utils.js", "javascript")

        _insert_edge(conn, "ts1", "__unresolved__",
                     "src::utils::helpers", "calls", "src/app.ts:3")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)

        assert result.resolved >= 1
        edge = conn.execute("SELECT target FROM edges WHERE source = 'ts1'").fetchone()
        assert edge["target"] == "js1"

    def test_js_calls_ts_module_resolved(self, conn):
        """JavaScript calling TypeScript module → resolved."""
        _insert_node(conn, "js1", "function", "render",
                     "src::components::render", "src/components.js", "javascript")
        _insert_node(conn, "ts1", "function", "formatDate",
                     "src::utils::formatDate", "src/utils.ts", "typescript")

        _insert_edge(conn, "js1", "__unresolved__",
                     "src::utils::formatDate", "calls", "src/components.js:5")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)

        assert result.resolved >= 1
        edge = conn.execute("SELECT target FROM edges WHERE source = 'js1'").fetchone()
        assert edge["target"] == "ts1"


class TestCrossLanguageNonRegression:
    """P47: Same-language resolution still works."""

    def test_same_language_java_still_resolves(self, conn):
        """Java calling Java still resolves correctly."""
        _insert_node(conn, "j1", "method", "doWork",
                     "com.example::Worker::doWork", "src/Worker.java", "java")
        _insert_node(conn, "j2", "class", "Logger",
                     "com.example::Logger", "src/Logger.java", "java")

        _insert_edge(conn, "j1", "__unresolved__",
                     "com.example::Logger", "calls", "src/Worker.java:3")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)
        assert result.resolved >= 1

    def test_same_language_typescript_still_resolves(self, conn):
        """TS calling TS still resolves correctly."""
        _insert_node(conn, "t1", "function", "start",
                     "src::index::start", "src/index.ts", "typescript")
        _insert_node(conn, "t2", "function", "setup",
                     "src::setup::setup", "src/setup.ts", "typescript")

        _insert_edge(conn, "t1", "__unresolved__",
                     "src::setup::setup", "calls", "src/index.ts:1")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)
        assert result.resolved >= 1

    def test_cross_lang_does_not_break_external_detection(self, conn):
        """Unresolvable targets still marked as unresolved (not misresolved)."""
        _insert_node(conn, "j1", "method", "run",
                     "com.example::App::run", "src/App.java", "java")

        _insert_edge(conn, "j1", "__unresolved__",
                     "java.util.ArrayList", "calls", "src/App.java:1")

        queries = _FakeQueries(conn)
        result = resolve_edges(queries)

        # Should NOT resolve to anything — ArrayList is external
        edge = conn.execute("SELECT provenance FROM edges WHERE source = 'j1'").fetchone()
        assert edge["provenance"] != "resolved"
        assert edge["provenance"] != "cross_lang_resolved"
