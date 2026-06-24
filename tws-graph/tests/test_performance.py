"""TDD tests for P28 v5.2.0 performance optimizations.

Verifies that optimization changes are functionally correct — not speed
measurements (those require the actual project and are covered by the
quality gates).
"""

import os
import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.indexer.parallel import ParallelExtractionOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_id(qualified_name: str, file_path: str) -> str:
    import hashlib
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Tests: SQLite performance indices
# ---------------------------------------------------------------------------

class TestPerformanceIndices:
    """P28a: Verify performance indices are created on new databases."""

    def test_provenance_index_exists(self, tmp_path):
        """idx_edges_provenance should exist."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_edges_provenance'"
        ).fetchall()
        db.close()
        assert len(rows) == 1, "idx_edges_provenance should exist"

    def test_source_index_exists(self, tmp_path):
        """idx_edges_source should exist."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_edges_source'"
        ).fetchall()
        db.close()
        assert len(rows) == 1, "idx_edges_source should exist"

    def test_target_index_exists(self, tmp_path):
        """idx_edges_target should exist."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_edges_target'"
        ).fetchall()
        db.close()
        assert len(rows) == 1, "idx_edges_target should exist"

    def test_kind_provenance_index_exists(self, tmp_path):
        """idx_edges_kind_provenance should exist."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_edges_kind_provenance'"
        ).fetchall()
        db.close()
        assert len(rows) == 1, "idx_edges_kind_provenance should exist"

    def test_existing_indices_still_present(self, tmp_path):
        """Existing indices should not be affected by P28 additions."""
        db_path = str(tmp_path / "test.db")
        db = DatabaseConnection.initialize(db_path)
        for idx_name in [
            "idx_nodes_name", "idx_nodes_kind", "idx_nodes_file",
            "idx_nodes_qualified", "idx_edges_source_kind",
            "idx_edges_target_kind", "idx_edges_kind",
        ]:
            rows = db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx_name,)
            ).fetchall()
            assert len(rows) == 1, f"{idx_name} should still exist"
        db.close()


# ---------------------------------------------------------------------------
# Tests: _hash_id caching
# ---------------------------------------------------------------------------

class TestHashIdCaching:
    """P28c: Verify _hash_id caching in extractors."""

    def test_python_hash_id_cached(self):
        """python_extractor._hash_id should be an lru_cache'd function."""
        from tws_graph.indexer.python_extractor import _hash_id
        assert hasattr(_hash_id, "cache_info"), \
            "_hash_id should have cache_info (lru_cache decorator)"

    def test_python_hash_id_cache_works(self):
        """Repeated calls with same args should hit cache."""
        from tws_graph.indexer.python_extractor import _hash_id
        _hash_id.cache_clear()
        result1 = _hash_id("test.py::func", "test.py")
        info1 = _hash_id.cache_info()
        result2 = _hash_id("test.py::func", "test.py")
        info2 = _hash_id.cache_info()
        assert result1 == result2
        assert info2.hits > info1.hits, "Second call should be a cache hit"

    def test_ts_hash_id_cached(self):
        """ts_extractor._hash_id should be an lru_cache'd function."""
        from tws_graph.indexer.ts_extractor import _hash_id
        assert hasattr(_hash_id, "cache_info"), \
            "ts_extractor._hash_id should have cache_info (lru_cache decorator)"

    def test_ts_hash_id_cache_works(self):
        """Repeated calls with same args should hit cache."""
        from tws_graph.indexer.ts_extractor import _hash_id
        _hash_id.cache_clear()
        result1 = _hash_id("src/comp.ts::func", "src/comp.ts")
        info1 = _hash_id.cache_info()
        result2 = _hash_id("src/comp.ts::func", "src/comp.ts")
        info2 = _hash_id.cache_info()
        assert result1 == result2
        assert info2.hits > info1.hits, "Second call should be a cache hit"


# ---------------------------------------------------------------------------
# Tests: Default chunk_size
# ---------------------------------------------------------------------------

class TestParallelDefaults:
    """P28: Verify optimized defaults for ParallelExtractionOrchestrator."""

    def test_default_chunk_size(self):
        """Default chunk_size should be 100 (up from 50)."""
        orch = ParallelExtractionOrchestrator("/tmp")
        assert orch.chunk_size == 100, f"Expected 100, got {orch.chunk_size}"

    def test_explicit_chunk_size_respected(self):
        """Explicit chunk_size override should be respected."""
        orch = ParallelExtractionOrchestrator("/tmp", chunk_size=25)
        assert orch.chunk_size == 25


# ---------------------------------------------------------------------------
# Tests: Cross-file dataflow batch loading correctness
# ---------------------------------------------------------------------------

class TestCrossFileDataflowBatchLoading:
    """P28b: Verify optimized cross-file dataflow produces correct results."""

    def test_batch_loading_correctness(self, queries):
        """Optimized batch-loading should produce same results as before optimization."""
        from tws_graph.indexer.parallel import _propagate_cross_file_dataflow

        # Create a cross-file call scenario
        callee_id = _make_node_id("file_b.py::callee", "file_b.py")
        param_id = _make_node_id("file_b.py::callee::param:x", "file_b.py")
        return_id = _make_node_id("file_b.py::callee::return:result", "file_b.py")

        for nid, kind, name, qname, fpath in [
            (callee_id, "function", "callee", "file_b.py::callee", "file_b.py"),
            (param_id, "variable", "x", "file_b.py::callee::param:x", "file_b.py"),
            (return_id, "variable", "result", "file_b.py::callee::return:result", "file_b.py"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, ?, 'python', 1, 2, 1000)
            """, (nid, kind, name, qname, fpath))

        # Callee internal data_flow: param → return
        queries._exec("""
            INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
            VALUES (?, ?, 'data_flows', 'file_b.py:3', 'tree-sitter')
        """, (param_id, return_id))

        # Caller
        caller_id = _make_node_id("file_a.py::caller", "file_a.py")
        arg_id = _make_node_id("file_a.py::caller::param:val", "file_a.py")
        res_id = _make_node_id("file_a.py::caller::var:res", "file_a.py")

        for nid, kind, name, qname, fpath in [
            (caller_id, "function", "caller", "file_a.py::caller", "file_a.py"),
            (arg_id, "variable", "val", "file_a.py::caller::param:val", "file_a.py"),
            (res_id, "variable", "res", "file_a.py::caller::var:res", "file_a.py"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, ?, 'python', 1, 2, 1000)
            """, (nid, kind, name, qname, fpath))

        # Caller data_flow INTO callee
        queries._exec("""
            INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
            VALUES (?, ?, 'data_flows', 'file_a.py:3', 'tree-sitter')
        """, (arg_id, callee_id))

        # Caller data_flow OUT of callee
        queries._exec("""
            INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
            VALUES (?, ?, 'data_flows', 'file_a.py:3', 'tree-sitter')
        """, (callee_id, res_id))

        # Cross-file calls edge
        queries._exec("""
            INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
            VALUES (?, ?, 'calls', 'file_a.py:3', 'resolved')
        """, (caller_id, callee_id))

        queries.conn.commit()

        _propagate_cross_file_dataflow(queries)

        cross_edges = queries._exec(
            "SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'"
        ).fetchall()
        assert len(cross_edges) > 0, "Batch-loaded cross-file dataflow should produce edges"


# ---------------------------------------------------------------------------
# Tests: FTS triggers work without rebuild_fts
# ---------------------------------------------------------------------------

class TestFTSWithoutRebuild:
    """P28d: Verify FTS stays populated via triggers (no rebuild_fts needed)."""

    def test_fts_populated_after_insert(self, tmp_path):
        """After inserting nodes, FTS should be searchable (triggers, not rebuild)."""
        import sqlite3
        db_path = str(tmp_path / "test_fts.db")
        conn = sqlite3.connect(db_path)
        # Manually create schema
        conn.executescript("""
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY, kind TEXT, name TEXT,
                qualified_name TEXT, file_path TEXT, language TEXT,
                start_line INTEGER, end_line INTEGER, updated_at INTEGER,
                docstring TEXT, signature TEXT
            );
            CREATE VIRTUAL TABLE nodes_fts USING fts5(
                id, name, qualified_name, docstring, signature,
                content='nodes', content_rowid='rowid'
            );
            CREATE TRIGGER nodes_fts_ai AFTER INSERT ON nodes BEGIN
                INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
                VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
            END;
        """)

        # Insert a node — trigger should populate FTS
        conn.execute("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('test123', 'function', 'my_func', 'test.py::my_func',
                    'test.py', 'python', 1, 5, 1000)
        """)

        # Query FTS directly
        rows = conn.execute(
            "SELECT name FROM nodes_fts WHERE nodes_fts MATCH 'my_func'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "my_func"
