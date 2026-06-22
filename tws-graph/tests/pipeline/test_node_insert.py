"""Tests for NodeInsertPass — writing extracted symbols as nodes into Store."""

import pytest
from pathlib import Path
from hashlib import sha256

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.store.memory_store import MemoryStore


# ============================================================================
# Helpers
# ============================================================================


def _make_store() -> MemoryStore:
    """Create a fresh MemoryStore."""
    return MemoryStore()


def _make_pass():
    """Create a NodeInsertPass instance (lazy import)."""
    from tws_graph.pipeline.passes.node_insert import NodeInsertPass
    return NodeInsertPass()


def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID matching the extractor convention."""
    raw = f"{file_path}:{qualified_name}"
    return sha256(raw.encode()).hexdigest()[:32]


def _make_def(
    name: str = "my_func",
    kind: str = "function",
    qualified_name: str = "test.py::my_func",
    file_path: str = "test.py",
    language: str = "python",
    start_line: int = 1,
    end_line: int = 3,
) -> dict:
    """Create a minimal valid node/def record."""
    return {
        "id": _hash_id(qualified_name, file_path),
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "file_path": file_path,
        "language": language,
        "start_line": start_line,
        "end_line": end_line,
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 0,
    }


# ============================================================================
# Basic behavior
# ============================================================================


class TestNodeInsertBasic:
    """Basic NodeInsertPass behavior."""

    def test_inserts_defs_into_store(self):
        """Defs from parsed_results are inserted as nodes into Store."""
        store = _make_store()
        def1 = _make_def(name="func_a", qualified_name="test.py::func_a")
        def2 = _make_def(name="func_b", qualified_name="test.py::func_b")

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {
                    "defs": [def1, def2],
                    "calls": [],
                    "refs": [],
                }
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        # Nodes should now be in the store
        assert store.get_node_by_id(def1["id"]) is not None
        assert store.get_node_by_id(def2["id"]) is not None
        assert store.count_nodes() == 2

    def test_records_node_count_in_metadata(self):
        """node_count is written to metadata."""
        store = _make_store()
        def1 = _make_def(name="f1", qualified_name="a.py::f1")
        def2 = _make_def(name="f2", qualified_name="a.py::f2")
        def3 = _make_def(name="f3", qualified_name="b.py::f3", file_path="b.py")

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "a.py": {"defs": [def1, def2], "calls": [], "refs": []},
                "b.py": {"defs": [def3], "calls": [], "refs": []},
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.metadata.get("node_count") == 3

    def test_empty_parsed_results_noop(self):
        """Empty parsed_results should not crash."""
        store = _make_store()
        ctx = PipelineContext(store=store, parsed_results={})

        pas = _make_pass()
        result = pas.run(ctx)
        assert store.count_nodes() == 0
        assert result.metadata.get("node_count", 0) == 0

    def test_file_with_no_defs_skipped(self):
        """A file entry with empty defs list should be skipped gracefully."""
        store = _make_store()
        ctx = PipelineContext(
            store=store,
            parsed_results={
                "empty.py": {"defs": [], "calls": [], "refs": []},
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert store.count_nodes() == 0


# ============================================================================
# Replace-on-insert behavior
# ============================================================================


class TestNodeInsertReplace:
    """Tests for delete+insert (replace) semantics."""

    def test_deletes_existing_nodes_before_insert(self):
        """When a file already has nodes, old ones are deleted before new insert."""
        store = _make_store()

        # Pre-populate store with an old node for this file
        old_def = _make_def(
            name="old_func",
            qualified_name="test.py::old_func",
        )
        store.insert_node(old_def)

        # Now insert new (different) defs for the same file
        new_def = _make_def(
            name="new_func",
            qualified_name="test.py::new_func",
        )

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {"defs": [new_def], "calls": [], "refs": []},
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        # Old node should be gone
        assert store.get_node_by_id(old_def["id"]) is None
        # New node should exist
        assert store.get_node_by_id(new_def["id"]) is not None
        assert store.count_nodes() == 1

    def test_insert_then_reinsert_updates_correctly(self):
        """Running twice with different defs for same file should replace, not duplicate."""
        store = _make_store()
        def_v1 = _make_def(name="v1", qualified_name="test.py::v1")

        ctx1 = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {"defs": [def_v1], "calls": [], "refs": []},
            },
        )

        pas = _make_pass()
        pas.run(ctx1)
        assert store.count_nodes() == 1

        # Second run with different def
        def_v2 = _make_def(name="v2", qualified_name="test.py::v2")
        ctx2 = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {"defs": [def_v2], "calls": [], "refs": []},
            },
        )
        pas.run(ctx2)
        # Should still have 1 node (old deleted, new inserted)
        assert store.count_nodes() == 1
        assert store.get_node_by_id(def_v1["id"]) is None
        assert store.get_node_by_id(def_v2["id"]) is not None

    def test_multiple_files_each_replaced_independently(self):
        """Files are processed independently — deleting nodes for one file
        does not affect nodes from another file."""
        store = _make_store()

        # Pre-populate store with nodes from two files
        old_a = _make_def(qualified_name="a.py::old_a", file_path="a.py")
        old_b = _make_def(qualified_name="b.py::old_b", file_path="b.py")
        store.insert_node(old_a)
        store.insert_node(old_b)

        # Only update file a.py
        new_a = _make_def(qualified_name="a.py::new_a", file_path="a.py")

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "a.py": {"defs": [new_a], "calls": [], "refs": []},
            },
        )

        pas = _make_pass()
        pas.run(ctx)

        # a.py: old gone, new present
        assert store.get_node_by_id(old_a["id"]) is None
        assert store.get_node_by_id(new_a["id"]) is not None
        # b.py: untouched
        assert store.get_node_by_id(old_b["id"]) is not None
        assert store.count_nodes() == 2


# ============================================================================
# Pass interface compliance
# ============================================================================


class TestNodeInsertInterface:
    """Verify NodeInsertPass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name_and_description(self):
        pas = _make_pass()
        assert pas.name == "node-insert"
        assert len(pas.description) > 0

    def test_dependencies(self):
        pas = _make_pass()
        assert pas.dependencies == ["parse-extract"]

    def test_supports_incremental(self):
        pas = _make_pass()
        assert pas.supports_incremental is True

    def test_enabled_default(self):
        pas = _make_pass()
        assert pas.enabled(PipelineContext()) is True

    def test_run_returns_pipeline_context(self):
        store = _make_store()
        ctx = PipelineContext(store=store, parsed_results={})
        pas = _make_pass()
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
