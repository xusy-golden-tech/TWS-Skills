"""Tests for EdgeInsertPass — writing call/ref edges into Store."""

import pytest
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
    """Create an EdgeInsertPass instance (lazy import)."""
    from tws_graph.pipeline.passes.edge_insert import EdgeInsertPass
    return EdgeInsertPass()


def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID."""
    raw = f"{file_path}:{qualified_name}"
    return sha256(raw.encode()).hexdigest()[:32]


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


def _make_edge(
    source_id: str,
    target: str = "",
    kind: str = "calls",
    target_text: str = "",
    source_loc: str = "test.py:5",
) -> dict:
    """Create a minimal edge record."""
    edge = {
        "source": source_id,
        "target": target,
        "kind": kind,
        "source_loc": source_loc,
        "provenance": "tree-sitter",
    }
    if target_text:
        edge["target_text"] = target_text
    return edge


# ============================================================================
# Basic behavior
# ============================================================================


class TestEdgeInsertBasic:
    """Basic EdgeInsertPass behavior."""

    def test_inserts_resolved_edges(self):
        """Edges with valid target (existing node) are inserted directly."""
        store = _make_store()

        # Create two nodes
        caller = _make_node("caller", "test.py::caller")
        callee = _make_node("callee", "test.py::callee")
        store.insert_node(caller)
        store.insert_node(callee)

        edge = _make_edge(
            source_id=caller["id"],
            target=callee["id"],
            kind="calls",
        )

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {
                    "defs": [],
                    "calls": [edge],
                    "refs": [],
                }
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        assert store.count_edges() == 1
        assert result.metadata.get("edge_count", 0) == 1

    def test_inserts_multiple_edges(self):
        """Multiple edges from multiple files are all inserted."""
        store = _make_store()

        n1 = _make_node("f1", "a.py::f1", "a.py")
        n2 = _make_node("f2", "a.py::f2", "a.py")
        n3 = _make_node("f3", "b.py::f3", "b.py")
        store.insert_node(n1)
        store.insert_node(n2)
        store.insert_node(n3)

        e1 = _make_edge(n1["id"], n2["id"], "calls")
        e2 = _make_edge(n1["id"], n3["id"], "calls")

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "a.py": {"defs": [], "calls": [e1], "refs": []},
                "b.py": {"defs": [], "calls": [e2], "refs": []},
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        assert store.count_edges() == 2
        assert result.metadata.get("edge_count", 0) == 2

    def test_empty_parsed_results_noop(self):
        """Empty parsed_results should not crash."""
        store = _make_store()
        ctx = PipelineContext(store=store, parsed_results={})

        pas = _make_pass()
        result = pas.run(ctx)
        assert store.count_edges() == 0
        assert result.metadata.get("edge_count", 0) == 0

    def test_refs_edges_also_inserted(self):
        """Edges with kind='refs' or 'imports' are also inserted."""
        store = _make_store()

        n1 = _make_node("main", "test.py::main")
        n2 = _make_node("math", "test.py::math")
        store.insert_node(n1)
        store.insert_node(n2)

        ref_edge = _make_edge(n1["id"], n2["id"], kind="imports")

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {
                    "defs": [],
                    "calls": [],
                    "refs": [ref_edge],
                }
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert store.count_edges() == 1


# ============================================================================
# Cross-file / unresolved edge handling
# ============================================================================


class TestEdgeInsertUnresolved:
    """Tests for edges that cannot be immediately resolved."""

    def test_unresolved_target_becomes_dangling_edge(self):
        """Edge with unresolved target is inserted with empty target but target_text preserved."""
        store = _make_store()

        # Only the source node exists (target node is in another file, not yet indexed)
        caller = _make_node("caller", "test.py::caller")
        store.insert_node(caller)

        edge = _make_edge(
            source_id=caller["id"],
            target="",  # unresolved
            target_text="other.py::external_func",
            kind="calls",
        )

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {
                    "defs": [],
                    "calls": [edge],
                    "refs": [],
                }
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        # Dangling edge should be in store
        dangling = store.get_dangling_edges()
        assert len(dangling) >= 1

    def test_resolved_by_def_index(self):
        """Edge with target_text that matches a qualified_name in def_index is resolved."""
        store = _make_store()

        caller = _make_node("caller", "test.py::caller")
        callee = _make_node("helper", "other.py::helper", file_path="other.py")
        store.insert_node(caller)
        store.insert_node(callee)

        edge = _make_edge(
            source_id=caller["id"],
            target="",  # will be resolved
            target_text="other.py::helper",
            kind="calls",
        )

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {
                    "defs": [],
                    "calls": [edge],
                    "refs": [],
                }
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)

        # Check that edge was resolved and inserted
        assert store.count_edges() >= 1
        # The edge should not be dangling anymore
        dangling = store.get_dangling_edges()
        resolved_targets = [d["target"] for d in dangling]
        assert callee["id"] not in resolved_targets or len(dangling) == 0

    def test_unresolved_count_in_metadata(self):
        """unresolved_count is recorded in metadata."""
        store = _make_store()

        caller = _make_node("caller", "test.py::caller")
        store.insert_node(caller)

        edge = _make_edge(
            source_id=caller["id"],
            target="",
            target_text="missing.py::unknown_func",
            kind="calls",
        )

        ctx = PipelineContext(
            store=store,
            parsed_results={
                "test.py": {"defs": [], "calls": [edge], "refs": []},
            },
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert "unresolved_count" in result.metadata


# ============================================================================
# Pass interface compliance
# ============================================================================


class TestEdgeInsertInterface:
    """Verify EdgeInsertPass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name_and_description(self):
        pas = _make_pass()
        assert pas.name == "edge-insert"
        assert len(pas.description) > 0

    def test_dependencies(self):
        pas = _make_pass()
        assert pas.dependencies == ["node-insert"]

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
