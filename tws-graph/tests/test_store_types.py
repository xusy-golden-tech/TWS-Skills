"""Tests for store/types.py — shared TypedDict definitions."""

import sys
import pytest

# Ensure the module is importable
sys.path.insert(0, "src")

from tws_graph.store.types import (
    NodeRecord,
    EdgeRecord,
    FileRecord,
    UnresolvedRefRecord,
    StoreStats,
    SearchResult,
    Direction,
)


# ---------------------------------------------------------------------------
# 1. Field completeness — each TypedDict must have the exact expected fields
# ---------------------------------------------------------------------------

class TestNodeRecord:
    """Verify NodeRecord has all fields from design doc section 5.1."""

    EXPECTED_FIELDS = {
        "id",
        "kind",
        "name",
        "qualified_name",
        "file_path",
        "language",
        "start_line",
        "end_line",
        "signature",
        "docstring",
        "visibility",
        "is_abstract",
        "is_exported",
        "decorators",
        "framework",
        "properties",
        "updated_at",
    }

    def test_has_all_expected_fields(self):
        actual = set(NodeRecord.__annotations__.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_false(self):
        """total=False means empty constructor does not raise."""
        rec = NodeRecord()
        assert isinstance(rec, dict)

    def test_populated_record_can_be_constructed(self):
        rec = NodeRecord(
            id="hash123",
            kind="function",
            name="calculateTotal",
            qualified_name="src/utils.py::calculateTotal",
            file_path="src/utils.py",
            language="python",
            start_line=10,
            end_line=15,
            signature="() -> float",
            docstring="Calculate the sum.",
            visibility="public",
            is_abstract=0,
            is_exported=1,
            decorators='["@staticmethod"]',
            framework="fastapi",
            properties='{"cyclomatic_complexity": 3}',
            updated_at=1719000000000,
        )
        assert rec["id"] == "hash123"
        assert rec["kind"] == "function"
        assert rec["name"] == "calculateTotal"
        assert rec["signature"] == "() -> float"
        assert rec["is_abstract"] == 0
        assert rec["updated_at"] == 1719000000000


class TestEdgeRecord:
    """Verify EdgeRecord has all fields from design doc section 5.1."""

    EXPECTED_FIELDS = {
        "id",
        "source",
        "target",
        "target_text",
        "kind",
        "source_loc",
        "provenance",
        "properties",
    }

    def test_has_all_expected_fields(self):
        actual = set(EdgeRecord.__annotations__.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_false(self):
        rec = EdgeRecord()
        assert isinstance(rec, dict)

    def test_populated_record_can_be_constructed(self):
        rec = EdgeRecord(
            id=1,
            source="hash_src",
            target="hash_tgt",
            target_text="src/other.py::helperFn",
            kind="calls",
            source_loc="src/main.py:42:10",
            provenance="tree-sitter",
            properties='{"weight": 1}',
        )
        assert rec["id"] == 1
        assert rec["kind"] == "calls"
        assert rec["provenance"] == "tree-sitter"


class TestFileRecord:
    """Verify FileRecord has all fields from design doc section 5.1."""

    EXPECTED_FIELDS = {
        "path",
        "content_hash",
        "language",
        "node_count",
        "indexed_at",
        "size",
        "modified_at",
    }

    def test_has_all_expected_fields(self):
        actual = set(FileRecord.__annotations__.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_true(self):
        """total=True — verified via __total__ attribute (runtime enforcement
        is static-only; TypedDict does not raise TypeError at runtime for
        missing keys)."""
        assert FileRecord.__total__ is True
        # Empty construction succeeds at runtime but type-checkers flag it
        rec = FileRecord()  # type: ignore[call-arg]
        assert isinstance(rec, dict)

    def test_populated_record_can_be_constructed(self):
        rec = FileRecord(
            path="src/main.py",
            content_hash="abcdef123456",
            language="python",
            node_count=42,
            indexed_at=1719000000000,
            size=2048,
            modified_at=1718900000,
        )
        assert rec["path"] == "src/main.py"
        assert rec["node_count"] == 42
        assert rec["size"] == 2048


class TestUnresolvedRefRecord:
    """Verify UnresolvedRefRecord has all fields from design doc section 5.1."""

    EXPECTED_FIELDS = {
        "id",
        "from_node_id",
        "reference_name",
        "reference_kind",
        "line",
        "col",
        "candidates",
        "source",
        "file_path",
        "language",
        "is_external",
    }

    def test_has_all_expected_fields(self):
        actual = set(UnresolvedRefRecord.__annotations__.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_false(self):
        rec = UnresolvedRefRecord()
        assert isinstance(rec, dict)

    def test_populated_record_can_be_constructed(self):
        rec = UnresolvedRefRecord(
            id=1,
            from_node_id="hash_node",
            reference_name="helperFn",
            reference_kind="call",
            line=42,
            col=10,
            candidates='["hash_candidate1"]',
            source="tree-sitter",
            file_path="src/main.py",
            language="python",
            is_external=0,
        )
        assert rec["reference_name"] == "helperFn"
        assert rec["is_external"] == 0


class TestStoreStats:
    """Verify StoreStats has all fields from design doc section 5.1."""

    REQUIRED_FIELDS = {
        "node_count",
        "edge_count",
        "file_count",
        "unresolved_count",
    }
    OPTIONAL_FIELDS = {
        "last_indexed_at",
        "db_size_bytes",
        "memory_usage_bytes",
    }

    def test_has_all_expected_fields(self):
        actual = set(StoreStats.__annotations__.keys())
        expected = self.REQUIRED_FIELDS | self.OPTIONAL_FIELDS
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_true(self):
        """total=True — verified via __total__ attribute. NotRequired fields
        are explicitly opt-out within the total=True context."""
        assert StoreStats.__total__ is True
        # Empty construction succeeds at runtime; type-checkers flag it
        rec = StoreStats()  # type: ignore[call-arg]
        assert isinstance(rec, dict)

    def test_populated_with_required_only(self):
        stats = StoreStats(
            node_count=100,
            edge_count=500,
            file_count=10,
            unresolved_count=5,
        )
        assert stats["node_count"] == 100
        assert stats["edge_count"] == 500

    def test_populated_with_all_fields(self):
        stats = StoreStats(
            node_count=100,
            edge_count=500,
            file_count=10,
            unresolved_count=5,
            last_indexed_at=1719000000000,
            db_size_bytes=1048576,
            memory_usage_bytes=2097152,
        )
        assert stats["last_indexed_at"] == 1719000000000
        assert stats["db_size_bytes"] == 1048576
        assert stats["memory_usage_bytes"] == 2097152


class TestSearchResult:
    """Verify SearchResult has all fields from design doc section 5.1."""

    EXPECTED_FIELDS = {
        "id",
        "name",
        "qualified_name",
        "kind",
        "file_path",
        "language",
        "signature",
        "docstring",
        "rank",
    }

    def test_has_all_expected_fields(self):
        actual = set(SearchResult.__annotations__.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected fields: {extra}"

    def test_total_is_true(self):
        """total=True — verified via __total__ attribute."""
        assert SearchResult.__total__ is True
        rec = SearchResult()  # type: ignore[call-arg]
        assert isinstance(rec, dict)

    def test_populated_record_can_be_constructed(self):
        rec = SearchResult(
            id="hash_node",
            name="calculateTotal",
            qualified_name="src/utils.py::calculateTotal",
            kind="function",
            file_path="src/utils.py",
            language="python",
            signature="(items: List[float]) -> float",
            docstring="Calculate the sum of all items.",
            rank=0.85,
        )
        assert rec["name"] == "calculateTotal"
        assert rec["rank"] == 0.85


# ---------------------------------------------------------------------------
# 2. Direction literal type
# ---------------------------------------------------------------------------

class TestDirection:
    """Verify Direction Literal type."""

    def test_valid_values(self):
        """All three literal values should be assignable."""
        d1: Direction = "in"
        d2: Direction = "out"
        d3: Direction = "both"
        assert d1 == "in"
        assert d2 == "out"
        assert d3 == "both"


# ---------------------------------------------------------------------------
# 3. Interoperability — TypedDicts are dict subclasses
# ---------------------------------------------------------------------------

class TestInteroperability:
    """Verify TypedDicts behave like regular dicts."""

    def test_node_record_is_dict(self):
        rec = NodeRecord(id="x", kind="function", name="f")
        assert isinstance(rec, dict)
        assert dict(rec) == {"id": "x", "kind": "function", "name": "f"}

    def test_edge_record_is_dict(self):
        rec = EdgeRecord(source="a", target="b", kind="calls")
        assert isinstance(rec, dict)

    def test_file_record_is_dict(self):
        rec = FileRecord(
            path="f.py",
            content_hash="abc",
            language="python",
            node_count=0,
            indexed_at=0,
            size=0,
            modified_at=0,
        )
        assert isinstance(rec, dict)

    def test_store_stats_is_dict(self):
        stats = StoreStats(node_count=1, edge_count=2, file_count=1, unresolved_count=0)
        assert isinstance(stats, dict)

    def test_search_result_is_dict(self):
        rec = SearchResult(
            id="x",
            name="f",
            qualified_name="m::f",
            kind="function",
            file_path="m.py",
            language="python",
            signature="() -> None",
            docstring="",
            rank=0.5,
        )
        assert isinstance(rec, dict)
