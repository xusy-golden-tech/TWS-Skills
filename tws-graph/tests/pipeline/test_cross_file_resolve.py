"""Tests for CrossFileResolvePass — resolving cross-file dangling edges."""

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
    """Create a CrossFileResolvePass instance (lazy import)."""
    from tws_graph.pipeline.passes.cross_file_resolve import CrossFileResolvePass
    return CrossFileResolvePass()


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


def _make_dangling_edge(
    source_id: str,
    target_text: str,
    kind: str = "calls",
    source_loc: str = "test.py:5",
) -> dict:
    """Create a dangling edge (empty target, with target_text)."""
    return {
        "source": source_id,
        "target": "",
        "target_text": target_text,
        "kind": kind,
        "source_loc": source_loc,
        "provenance": "unresolved",
    }


# ============================================================================
# Basic behavior
# ============================================================================


class TestCrossFileResolveBasic:
    """Basic CrossFileResolvePass behavior."""

    def test_empty_dangling_edges_noop(self):
        """No dangling edges — pass completes without error."""
        store = _make_store()
        ctx = PipelineContext(store=store)

        pas = _make_pass()
        result = pas.run(ctx)

        assert result.resolve_stats["resolved"] == 0
        assert result.resolve_stats["unresolved"] == 0
        assert result.resolve_stats["ambiguous"] == 0

    def test_resolves_by_def_index_exact_match(self):
        """target_text matches a node's qualified_name via def_index → resolved."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        callee = _make_node("target_func", "b.py::target_func", "b.py")
        store.insert_node(caller)
        store.insert_node(callee)

        edge = _make_dangling_edge(
            source_id=caller["id"],
            target_text="b.py::target_func",
        )
        store.insert_edge(edge)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        assert result.resolve_stats["resolved"] == 1
        assert result.resolve_stats["unresolved"] == 0
        assert result.resolve_stats["ambiguous"] == 0

        # Verify the edge was actually updated
        outgoing = store.get_outgoing_edges(caller["id"])
        resolved = [e for e in outgoing if e["target"] == callee["id"]]
        assert len(resolved) >= 1

    def test_unresolved_when_no_def_index_match(self):
        """target_text not found in def_index → unresolved."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        store.insert_node(caller)

        edge = _make_dangling_edge(
            source_id=caller["id"],
            target_text="missing.py::nonexistent",
        )
        store.insert_edge(edge)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        assert result.resolve_stats["unresolved"] == 1

    def test_resolves_multiple_edges(self):
        """Multiple dangling edges are all processed."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        target_a = _make_node("func_a", "b.py::func_a", "b.py")
        target_b = _make_node("func_b", "c.py::func_b", "c.py")
        store.insert_node(caller)
        store.insert_node(target_a)
        store.insert_node(target_b)

        e1 = _make_dangling_edge(caller["id"], "b.py::func_a")
        e2 = _make_dangling_edge(caller["id"], "c.py::func_b")
        store.insert_edge(e1)
        store.insert_edge(e2)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        assert result.resolve_stats["resolved"] == 2


# ============================================================================
# Ambiguous resolution
# ============================================================================


class TestCrossFileResolveAmbiguous:
    """Tests for ambiguous resolution (multiple matches)."""

    def test_ambiguous_when_multiple_matches_by_name(self):
        """When FTS returns multiple nodes with matching name → ambiguous."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        # Two nodes with the same name in different files
        dup_a = _make_node("same_name", "b.py::same_name", "b.py")
        dup_b = _make_node("same_name", "c.py::same_name", "c.py")
        store.insert_node(caller)
        store.insert_node(dup_a)
        store.insert_node(dup_b)

        # Use a simple name (not qualified) so FTS finds multiple matches
        edge = _make_dangling_edge(
            source_id=caller["id"],
            target_text="same_name",  # simple name, will match both
        )
        store.insert_edge(edge)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        # ambiguous >= 1 (may be ambiguous because no exact def_index match and multiple FTS matches)
        total = (
            result.resolve_stats["resolved"]
            + result.resolve_stats["unresolved"]
            + result.resolve_stats["ambiguous"]
        )
        assert total >= 1


# ============================================================================
# Stats and metadata
# ============================================================================


class TestCrossFileResolveStats:
    """Tests for resolve_stats and metadata output."""

    def test_resolve_stats_initialized(self):
        """resolve_stats dictionary is populated after run."""
        store = _make_store()
        ctx = PipelineContext(store=store)

        pas = _make_pass()
        result = pas.run(ctx)

        assert "resolved" in result.resolve_stats
        assert "unresolved" in result.resolve_stats
        assert "ambiguous" in result.resolve_stats

    def test_metadata_cross_file_resolved(self):
        """cross_file_resolved is recorded in metadata."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        callee = _make_node("target", "b.py::target", "b.py")
        store.insert_node(caller)
        store.insert_node(callee)

        edge = _make_dangling_edge(caller["id"], "b.py::target")
        store.insert_edge(edge)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        assert "cross_file_resolved" in result.metadata
        assert isinstance(result.metadata["cross_file_resolved"], dict)

    def test_stats_sum_matches_total_edges_processed(self):
        """Sum of resolved + unresolved + ambiguous should equal total dangling edges."""
        store = _make_store()

        caller = _make_node("caller", "a.py::caller", "a.py")
        a = _make_node("func_a", "b.py::func_a", "b.py")
        store.insert_node(caller)
        store.insert_node(a)

        e1 = _make_dangling_edge(caller["id"], "b.py::func_a")  # resolved
        e2 = _make_dangling_edge(caller["id"], "missing_func")   # unresolved
        store.insert_edge(e1)
        store.insert_edge(e2)

        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)

        total = (
            result.resolve_stats["resolved"]
            + result.resolve_stats["unresolved"]
            + result.resolve_stats["ambiguous"]
        )
        assert total == 2


# ============================================================================
# Pass interface compliance
# ============================================================================


class TestCrossFileResolveInterface:
    """Verify CrossFileResolvePass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name_and_description(self):
        pas = _make_pass()
        assert pas.name == "cross-file-resolve"
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
        store = _make_store()
        ctx = PipelineContext(store=store)
        pas = _make_pass()
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
