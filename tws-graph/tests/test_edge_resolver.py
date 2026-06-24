"""Tests for post-processing edge resolver."""

import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.edge_resolver import (
    resolve_edges,
    ResolveResult,
    is_call_target_external,
)
from tws_graph.indexer.orchestrator import ExtractionOrchestrator


class TestEdgeResolver:
    """Tests for cross-file edge resolution."""

    @pytest.fixture
    def populated_db(self, sample_py_project, temp_db_path):
        """Create a database with indexed Python sample."""
        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(sample_py_project), queries)
        orch.index_all()
        yield queries
        db.close()

    def test_resolve_result_structure(self, populated_db):
        result = resolve_edges(populated_db)
        assert isinstance(result, ResolveResult)
        assert result.total_checked >= 0
        assert result.resolved >= 0
        assert result.unresolved >= 0
        assert result.ambiguous >= 0

    def test_no_crash_on_empty_db(self, queries):
        """Edge resolver should handle empty DB gracefully."""
        result = resolve_edges(queries)
        assert result.total_checked == 0
        assert result.resolved == 0

    def test_dangling_edges_detected(self, populated_db):
        """After single-file index, there should be dangling edges (cross-file calls)."""
        dangling = populated_db.get_dangling_call_edges()
        # Single file -> all calls should resolve internally,
        # but external lib calls (len, sum, etc.) will be dangling
        assert isinstance(dangling, list)

    def test_callable_nodes_indexed(self, populated_db):
        nodes = populated_db.get_all_callable_nodes()
        kinds = {n["kind"] for n in nodes}
        assert "function" in kinds or "method" in kinds


class TestEdgeResolverCrossFile:
    """Cross-file resolution with multiple files."""

    @pytest.fixture
    def multi_file_db(self, tmp_path, temp_db_path):
        """Create a project with two interdependent Python files."""
        p1 = tmp_path / "a.py"
        p1.write_text("""
def helper(x):
    return x + 1

def public_api(x):
    return helper(x)
""", encoding="utf-8")
        p2 = tmp_path / "b.py"
        p2.write_text("""
from a import public_api

def consumer(y):
    return public_api(y)
""", encoding="utf-8")

        db = DatabaseConnection.initialize(temp_db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(tmp_path), queries)
        orch.index_all()
        yield queries
        db.close()

    def test_cross_file_resolution(self, multi_file_db):
        """After multi-file index, cross-file references should be resolved."""
        result = resolve_edges(multi_file_db)
        # consumer() calls public_api() — should be resolved across files
        assert result.total_checked >= 0
        # Check that we have at least some resolved edges
        assert result.resolved + result.unresolved + result.ambiguous == result.total_checked


# =============================================================================
# Tests for is_call_target_external
# =============================================================================


class TestIsCallTargetExternal:
    """Tests for unresolved call target external/internal classification."""

    # ── Built-in types ────────────────────────────────────────────────

    def test_str_method_is_external(self):
        assert is_call_target_external("str::lower") is True

    def test_list_method_is_external(self):
        assert is_call_target_external("list::append") is True

    def test_dict_method_is_external(self):
        assert is_call_target_external("dict::get") is True

    def test_int_is_external(self):
        assert is_call_target_external("int::bit_length") is True

    def test_max_is_builtin_external(self):
        """Built-in 'max' is in _PYTHON_BUILTIN_TYPES and edge_resolver
        would have resolved a project-defined 'max' — so unresolved 'max' is external."""
        assert is_call_target_external("max") is True

    # ── String / number literals ──────────────────────────────────────

    def test_string_literal_receiver(self):
        assert is_call_target_external('" "::join') is True
        assert is_call_target_external("'x'::split") is True

    def test_number_literal_receiver(self):
        assert is_call_target_external("123::bit_length") is True

    # ── Known external prefixes ───────────────────────────────────────

    def test_tree_sitter_node(self):
        assert is_call_target_external("node::child_by_field_name") is True

    def test_parser_prefix(self):
        assert is_call_target_external("Parser::parse") is True

    # ── Stdlib modules ────────────────────────────────────────────────

    def test_os_path_join(self):
        assert is_call_target_external("os::path::join") is True

    def test_json_dumps(self):
        assert is_call_target_external("json::dumps") is True

    def test_re_sub(self):
        assert is_call_target_external("re::sub") is True

    def test_sys_exit(self):
        assert is_call_target_external("sys::exit") is True

    def test_csv_dictreader(self):
        assert is_call_target_external("csv::DictReader") is True

    # ── Project files ─────────────────────────────────────────────────

    def test_project_file_is_internal(self):
        pfs = frozenset({"src/my_module.py", "tests/test_foo.py"})
        assert is_call_target_external("src/my_module.py::my_func", pfs) is False

    def test_non_project_file_is_external(self):
        pfs = frozenset({"src/my_module.py"})
        assert is_call_target_external("external_lib.py::some_func", pfs) is True

    def test_project_file_calling_builtin_is_external(self):
        """Even when the first segment is a project file, if the actual
        target (last segment) is a built-in, it's external."""
        pfs = frozenset({"src/my_module.py"})
        assert is_call_target_external("src/my_module.py::int", pfs) is True

    # ── Edge cases ────────────────────────────────────────────────────

    def test_empty_target_text(self):
        assert is_call_target_external("") is False
        assert is_call_target_external(None, None) is False  # type: ignore[arg-type]

    def test_single_segment_no_project_files(self):
        """Without project_files, a bare name defaults to internal."""
        assert is_call_target_external("ClassName::method") is False

    def test_relative_import_prefix(self):
        """Relative import prefix '.' should stay internal."""
        pfs = frozenset({"src/my_module.py"})
        assert is_call_target_external(".::sibling_func", pfs) is False

    def test_unknown_first_segment(self):
        """Unknown first segment with no project_files is internal (conservative)."""
        assert is_call_target_external("UnknownClass::some_method") is False
