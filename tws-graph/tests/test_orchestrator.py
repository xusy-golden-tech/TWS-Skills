"""Integration tests for ExtractionOrchestrator — full scan→parse→store pipeline."""

import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.indexer.orchestrator import ExtractionOrchestrator, hash_content


class TestOrchestrator:
    """Full pipeline tests: scan, parse, store, skip, force."""

    @pytest.fixture
    def queries(self, sample_py_project, temp_db_path):
        db = DatabaseConnection.initialize(temp_db_path)
        qb = QueryBuilder(db.conn)
        yield qb
        db.close()

    def test_index_all_python(self, sample_py_project, queries):
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        result = orch.index_all()

        assert result.files_indexed >= 1
        assert result.nodes_created > 0
        assert result.edges_created > 0
        assert len(result.errors) == 0

        # verify DB has data
        stats = queries.get_stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0
        assert stats["file_count"] >= 1

    def test_index_typescript(self, sample_ts_project, temp_db_path):
        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_ts_project), queries)
        result = orch.index_all()

        assert result.files_indexed >= 1
        assert result.nodes_created > 0
        stats = queries.get_stats()
        assert stats["node_count"] > 0
        db.close()

    def test_index_kotlin(self, sample_kt_project, temp_db_path):
        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_kt_project), queries)
        result = orch.index_all()

        assert result.files_indexed >= 1
        assert result.nodes_created > 0
        stats = queries.get_stats()
        assert stats["node_count"] > 0
        db.close()

    def test_content_hash_skip(self, sample_py_project, queries):
        orch = ExtractionOrchestrator(str(sample_py_project), queries)

        result1 = orch.index_all()
        assert result1.files_indexed >= 1

        result2 = orch.index_all()  # second run should skip
        assert result2.files_skipped >= result1.files_indexed
        assert result2.files_indexed == 0

    def test_force_reindex(self, sample_py_project, queries):
        orch = ExtractionOrchestrator(str(sample_py_project), queries)

        orch.index_all()
        result = orch.index_all(force=True)
        assert result.files_indexed >= 1
        assert result.files_skipped == 0

    def test_no_source_files(self, tmp_path, temp_db_path):
        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(tmp_path), queries)
        result = orch.index_all()

        assert result.files_indexed == 0
        assert len(result.errors) >= 1  # warning about no source files
        db.close()


class TestHashContent:
    """SHA-256 content hashing tests."""

    def test_deterministic(self):
        h1 = hash_content("hello")
        h2 = hash_content("hello")
        assert h1 == h2

    def test_different_content(self):
        h1 = hash_content("hello")
        h2 = hash_content("world")
        assert h1 != h2

    def test_length(self):
        h = hash_content("test")
        assert len(h) == 64  # SHA-256 hex string
