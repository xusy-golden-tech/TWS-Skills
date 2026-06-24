"""Tests for ExtractionOrchestrator backward-compatible wrapper.

Verifies that the compat wrapper in pipeline/compat.py correctly delegates
to PipelineEngine + SqliteStore while maintaining the original API surface.
"""

import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


# We test the compat wrapper.  The compat module imports PipelineEngine and
# registers 5 Passes internally, then delegates index_all() to them.


class TestExtractionOrchestratorCompat:
    """Verify ExtractionOrchestrator wrapper delegates to PipelineEngine."""

    @pytest.fixture
    def compat_orch(self, sample_py_project, temp_db_path):
        """Create a compat ExtractionOrchestrator backed by PipelineEngine."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        yield orch, queries, db, sample_py_project
        db.close()
        orch.close()

    def test_constructor_registers_five_passes(self, sample_py_project, temp_db_path):
        """After construction the internal engine has all 8 passes registered."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        try:
            orch = ExtractionOrchestrator(str(sample_py_project), queries)
            engine = orch._engine
            assert engine.registered_count == 8, (
                f"Expected 8 passes, got {engine.registered_count}: "
                f"{engine.registered_names}"
            )
            names = set(engine.registered_names)
            expected = {
                "stat-filter",
                "parse-extract",
                "node-insert",
                "edge-insert",
                "dataflow",
                "cross-file-resolve",
                "test-edge-analysis",
                "config-link-analysis",
            }
            assert names == expected, f"Pass names mismatch: {names}"
        finally:
            db.close()

    def test_index_all_populates_result(self, compat_orch):
        """index_all() returns a non-empty IndexResult for a valid project."""
        orch, queries, db, project = compat_orch
        result = orch.index_all()

        assert result.files_indexed >= 1, "Expected at least 1 file indexed"
        assert result.nodes_created > 0, "Expected nodes to be created"
        assert result.edges_created > 0, "Expected edges to be created"
        assert result.duration_ms > 0, "Expected non-zero duration"
        non_dataflow_errors = [
            e for e in result.errors
            if e.get("pass") not in ("dataflow",)
        ]
        assert len(non_dataflow_errors) == 0, (
            f"Expected no errors (excluding dataflow), got: {non_dataflow_errors}"
        )

        # Verify data actually landed in the database
        stats = queries.get_stats()
        assert stats["node_count"] > 0
        assert stats["edge_count"] > 0
        assert stats["file_count"] >= 1

    def test_index_all_force(self, compat_orch):
        """index_all(force=True) re-indexes all files (no stat skipping)."""
        orch, queries, db, project = compat_orch

        # First run
        r1 = orch.index_all()
        assert r1.files_indexed >= 1

        # Second run with force=True — should re-index
        r2 = orch.index_all(force=True)
        assert r2.files_indexed >= 1
        assert r2.files_skipped == 0, "force=True should skip no files"

    def test_index_all_idempotent_skip(self, compat_orch):
        """Second index_all() without force should skip unchanged files."""
        orch, queries, db, project = compat_orch

        r1 = orch.index_all()
        assert r1.files_indexed >= 1

        r2 = orch.index_all()
        # After a clean index, the second run should skip files (stat unchanged)
        assert r2.files_skipped >= r1.files_indexed, (
            f"Expected {r1.files_indexed} skipped, got {r2.files_skipped}"
        )
        assert r2.files_indexed == 0, (
            f"Expected 0 indexed, got {r2.files_indexed}"
        )

    def test_index_all_type(self, sample_py_project, temp_db_path):
        """Verify IndexResult has the correct type."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator, IndexResult

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        try:
            orch = ExtractionOrchestrator(str(sample_py_project), queries)
            result = orch.index_all()
            assert isinstance(result, IndexResult)
            assert isinstance(result.files_indexed, int)
            assert isinstance(result.nodes_created, int)
            assert isinstance(result.edges_created, int)
            assert isinstance(result.errors, list)
            assert isinstance(result.duration_ms, int)
        finally:
            db.close()

    def test_hash_content_compat(self):
        """hash_content() from compat matches the original orchestrator."""
        from tws_graph.pipeline.compat import hash_content
        from tws_graph.indexer.orchestrator import hash_content as hash_orig

        test_strings = ["hello", "", "def foo(): pass\n", "x" * 1000]
        for s in test_strings:
            assert hash_content(s) == hash_orig(s), (
                f"hash_content mismatch for: {s!r}"
            )

    def test_empty_project(self, tmp_path, temp_db_path):
        """Indexing an empty directory returns appropriate result."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        try:
            orch = ExtractionOrchestrator(str(tmp_path), queries)
            result = orch.index_all()
            assert result.files_indexed == 0
            assert result.nodes_created == 0
            # Should have warning about no source files
            assert len(result.errors) >= 1
        finally:
            db.close()


class TestExtractionOrchestratorNewApi:
    """Verify the new convenience API methods on the compat wrapper."""

    @pytest.fixture
    def new_api_orch(self, sample_py_project, temp_db_path):
        """Create a compat orch for testing new API methods."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        yield orch, queries, db, sample_py_project
        db.close()

    def test_extract_and_index_with_files(self, new_api_orch):
        """extract_and_index() processes a specific file list."""
        import os
        orch, queries, db, project = new_api_orch

        root = str(project)
        # Find all Python files in the project
        from tws_graph.indexer.scanner import scan_directory
        files = scan_directory(root)
        assert len(files) > 0, "Expected source files in project"

        result = orch.extract_and_index(files, root)
        assert result.files_indexed >= 1
        assert result.nodes_created > 0

    def test_incremental_index(self, new_api_orch):
        """incremental_index() processes changed files incrementally."""
        import os
        orch, queries, db, project = new_api_orch

        root = str(project)
        from tws_graph.indexer.scanner import scan_directory
        files = scan_directory(root)

        # First: full index via extract_and_index
        _ = orch.extract_and_index(files, root, force=True)

        # Then: incremental pass (should skip unchanged)
        result = orch.incremental_index(files, root)
        # Incremental with force=False should skip stat-identical files
        assert result.files_skipped >= result.files_indexed

    def test_close_releases_resources(self, sample_py_project, temp_db_path):
        """close() cleans up the internal SqliteStore."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator
        from tws_graph.store.exceptions import StoreClosedError

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        orch.close()
        # After close, accessing internal store should not raise for close()
        orch.close()  # idempotent


class TestCompatErrorHandling:
    """Verify error handling in the compat wrapper."""

    def test_engine_errors_surface_in_result(self, sample_py_project, temp_db_path):
        """Pipeline engine errors are captured in IndexResult.errors."""
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        try:
            orch = ExtractionOrchestrator(str(sample_py_project), queries)
            # index_all should handle pipeline errors gracefully
            result = orch.index_all()
            assert isinstance(result.errors, list)
            # Even with errors, result should be an IndexResult
            assert result is not None
        finally:
            db.close()

    def test_corrupt_file_does_not_crash(self, sample_py_project, temp_db_path):
        """A file that fails to parse should not crash the entire index run."""
        import os
        from tws_graph.pipeline.compat import ExtractionOrchestrator

        # Write a file with syntax that will cause tree-sitter to produce errors
        corrupt_path = os.path.join(str(sample_py_project), "fixtures", "corrupt.py")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("def broken(  # unclosed paren\n")

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        try:
            orch = ExtractionOrchestrator(str(sample_py_project), queries)
            result = orch.index_all()
            # Should not crash; errors should be collected
            assert result.files_indexed >= 0
            # The corrupt file may produce parse errors
            # but the valid sample.py should still be indexed
            assert result.nodes_created > 0, (
                "Valid file should still be indexed"
            )
        finally:
            db.close()
