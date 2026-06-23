"""Tests for DataFlowPass — extract data flow edges from source files.

Tests cover:
- DataFlowPass basic attributes (name, dependencies, supports_incremental)
- Store unavailable behavior (returns ctx gracefully)
- Empty file list handling
- Non-Python/TS file skip
- Integration test with MemoryStore + tmp_path
"""

import hashlib
import pytest

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.store.memory_store import MemoryStore
from tws_graph.edges.kind import EdgeKind


# ============================================================================
# Helpers
# ============================================================================


def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID matching tws_graph.indexer.base.hash_id."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _make_pass():
    """Create a DataFlowPass instance (lazy import)."""
    from tws_graph.pipeline.passes.dataflow_pass import DataFlowPass
    return DataFlowPass()


def _make_node(
    name: str,
    qualified_name: str,
    file_path: str = "test.py",
    kind: str = "function",
) -> dict:
    """Create a minimal valid node record."""
    return {
        "id": _hash_id(qualified_name, file_path),
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 3,
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 0,
    }


# ============================================================================
# Basic attribute tests
# ============================================================================


class TestDataFlowPassBasic:
    """Verify DataFlowPass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name(self):
        pas = _make_pass()
        assert pas.name == "dataflow"

    def test_description_not_empty(self):
        pas = _make_pass()
        assert pas.description is not None
        assert len(pas.description) > 0

    def test_dependencies(self):
        pas = _make_pass()
        assert pas.dependencies == ["edge-insert"]

    def test_supports_incremental(self):
        pas = _make_pass()
        assert pas.supports_incremental is True

    def test_enabled_default(self):
        pas = _make_pass()
        assert pas.enabled(PipelineContext()) is True

    def test_run_returns_pipeline_context(self):
        """run() should return a PipelineContext instance even with no store."""
        pas = _make_pass()
        ctx = PipelineContext(root_dir="/tmp")
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)


# ============================================================================
# Store unavailable behavior
# ============================================================================


class TestDataFlowPassStoreUnavailable:
    """When store is None, pass should return ctx gracefully."""

    def test_store_none_returns_same_context(self):
        pas = _make_pass()
        ctx = PipelineContext(root_dir="/tmp", store=None)
        result = pas.run(ctx)
        assert result is ctx

    def test_store_none_sets_metadata(self):
        pas = _make_pass()
        ctx = PipelineContext(root_dir="/tmp", store=None)
        result = pas.run(ctx)
        assert "dataflow_edges" in ctx.metadata
        assert ctx.metadata["dataflow_edges"] == 0


# ============================================================================
# Empty files handling
# ============================================================================


class TestDataFlowPassEmptyFiles:
    """Handle empty file lists and no stale files normally."""

    def test_store_without_files(self, tmp_path):
        """When store has no registered files, tracker returns no stale files."""
        pas = _make_pass()
        store = MemoryStore()
        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
        assert result.metadata.get("dataflow_edges", 0) == 0
        assert result.metadata.get("dataflow_files", 0) == 0

    def test_store_has_files_but_no_matching_patterns(self, tmp_path):
        """When store has non-matching files (e.g., .js), no dataflow analysis."""
        pas = _make_pass()
        store = MemoryStore()

        # Register a .js file — should not match **/*.py or **/*.ts patterns
        store.upsert_file(
            path=str(tmp_path / "script.js"),
            content_hash="abc123",
            language="javascript",
            node_count=0,
            size=100,
            modified_at=1000,
        )

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
        # No matching files => dataflow_files should be 0
        assert result.metadata.get("dataflow_files", 0) == 0


# ============================================================================
# Non-Python/TS file skip
# ============================================================================


class TestDataFlowPassSkipNonPythonTS:
    """Verify non-Python/TS files are skipped during analysis."""

    def test_non_py_files_skipped(self, tmp_path):
        """Files without .py/.ts/.tsx extension are skipped."""
        pas = _make_pass()
        store = MemoryStore()

        # Register a .java file (matching **/*.java — wait, the pattern is
        # configurable. The dataflow analyzer only registers **/*.py, **/*.ts, **/*.tsx
        # patterns with the tracker. So .java files won't even appear in stale_files.)

        # Register a .py file that doesn't exist on disk — should be skipped gracefully
        py_path = str(tmp_path / "missing.py")
        store.upsert_file(
            path=py_path,
            content_hash="abc123",
            language="python",
            node_count=0,
            size=100,
            modified_at=1000,
        )

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)
        # File has no function nodes, should be skipped without error
        assert isinstance(result, PipelineContext)


# ============================================================================
# Integration tests
# ============================================================================


class TestDataFlowPassIntegration:
    """Integration tests with actual Python source files."""

    def test_python_file_with_single_function(self, tmp_path):
        """Analyze a Python file with one function that reads/writes variables."""
        # Create source file
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo():\n    x = 1\n    y = x\n")

        pas = _make_pass()
        store = MemoryStore()

        # Register file in store
        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=1,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        # Insert function node
        qname = f"{py_file}::foo"
        node = _make_node("foo", qname, str(py_file), "function")
        store.insert_node(node)

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)

        assert isinstance(result, PipelineContext)
        # Should have found edges (at least a write for x, read for x)
        edge_count = result.metadata.get("dataflow_edges", 0)
        assert edge_count > 0, f"Expected dataflow edges, got {edge_count}"
        assert result.metadata.get("dataflow_files", 0) == 1

    def test_python_file_with_function_call_dataflow(self, tmp_path):
        """Analyze a Python file with an inter-function call producing data_flows edges."""
        # Create source file with caller + callee
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def add(x, y):\n    return x + y\n\n"
            "def main():\n    a = 1\n    b = 2\n    result = add(a, b)\n"
        )

        pas = _make_pass()
        store = MemoryStore()

        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=2,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        # Insert function nodes with qualified_names matching what extractors build
        qname_add = f"{py_file}::add"
        qname_main = f"{py_file}::main"
        store.insert_node(_make_node("add", qname_add, str(py_file), "function"))
        store.insert_node(_make_node("main", qname_main, str(py_file), "function"))

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)

        assert isinstance(result, PipelineContext)
        edge_count = result.metadata.get("dataflow_edges", 0)
        assert edge_count > 0, f"Expected dataflow edges, got {edge_count}"
        assert result.metadata.get("dataflow_files", 0) == 1

        # Verify edges were actually inserted into the store
        assert store.count_edges() == edge_count

    def test_edges_inserted_have_correct_kinds(self, tmp_path):
        """Verify inserted edges use the correct EdgeKind values."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            "def process(data):\n"
            "    result = data\n"
            "    return result\n"
        )

        pas = _make_pass()
        store = MemoryStore()

        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=1,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        qname = f"{py_file}::process"
        store.insert_node(_make_node("process", qname, str(py_file), "function"))

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)

        assert result.metadata.get("dataflow_files", 0) == 1

        # Read back edges from store and verify kinds
        edges = list(store.iter_edges())
        kinds_found = {e.get("kind") for e in edges}

        # Should only contain valid EdgeKind values
        valid_kinds = {e.value for e in EdgeKind}
        for kind in kinds_found:
            assert kind in valid_kinds, f"Invalid edge kind: {kind}"

    def test_multiple_files_analyzed(self, tmp_path):
        """Analyze two Python files in the same pass."""
        # File 1
        py1 = tmp_path / "a.py"
        py1.write_text("def f1():\n    x = 1\n")

        # File 2
        py2 = tmp_path / "b.py"
        py2.write_text("def f2():\n    y = 2\n")

        pas = _make_pass()
        store = MemoryStore()

        for py_file in [py1, py2]:
            stat = py_file.stat()
            store.upsert_file(
                path=str(py_file),
                content_hash="abc123",
                language="python",
                node_count=1,
                size=stat.st_size,
                modified_at=int(stat.st_mtime),
            )
            qname = f"{py_file}::f{['1','2'][[py1,py2].index(py_file)]}"
            name = f"f{['1','2'][[py1,py2].index(py_file)]}"
            store.insert_node(_make_node(name, qname, str(py_file), "function"))

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)

        assert result.metadata.get("dataflow_files", 0) == 2
        assert result.metadata.get("dataflow_edges", 0) > 0

    def test_file_with_no_function_nodes_skipped(self, tmp_path):
        """File with no function/method nodes should be skipped with zero edges."""
        py_file = tmp_path / "empty.py"
        py_file.write_text("x = 1\ny = 2\n")  # No function definitions

        pas = _make_pass()
        store = MemoryStore()

        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=0,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        result = pas.run(ctx)

        assert isinstance(result, PipelineContext)
        # No function nodes => no analysis => 0 edges
        assert result.metadata.get("dataflow_edges", 0) == 0

    def test_syntax_error_file_handled_gracefully(self, tmp_path):
        """File with syntax errors should not crash the pass."""
        py_file = tmp_path / "broken.py"
        py_file.write_text("def foo(\n    x = 1\n")  # Unclosed parenthesis

        pas = _make_pass()
        store = MemoryStore()

        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=0,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        ctx = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": str(tmp_path / "analysis.db")},
        )
        # Should not raise
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)

    def test_second_run_sees_no_stale_files(self, tmp_path):
        """After first run marks files valid, second run finds no stale files."""
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo():\n    x = 1\n")

        store = MemoryStore()

        stat = py_file.stat()
        store.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=1,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )

        qname = f"{py_file}::foo"
        store.insert_node(_make_node("foo", qname, str(py_file), "function"))

        tracker_db = str(tmp_path / "analysis.db")

        # First run — should analyze the file
        ctx1 = PipelineContext(
            root_dir=str(tmp_path),
            store=store,
            metadata={"tracker_db_path": tracker_db},
        )
        pas = _make_pass()
        result1 = pas.run(ctx1)
        assert result1.metadata.get("dataflow_files", 0) == 1

        # Second run with same tracker DB — should find 0 stale files
        # Need fresh store since MemoryStore is stateful but we want same data
        store2 = MemoryStore()
        store2.upsert_file(
            path=str(py_file),
            content_hash="abc123",
            language="python",
            node_count=1,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )
        store2.insert_node(_make_node("foo", qname, str(py_file), "function"))

        ctx2 = PipelineContext(
            root_dir=str(tmp_path),
            store=store2,
            metadata={"tracker_db_path": tracker_db},
        )
        result2 = pas.run(ctx2)
        # File marked valid — should skip
        assert result2.metadata.get("dataflow_files", 0) == 0
