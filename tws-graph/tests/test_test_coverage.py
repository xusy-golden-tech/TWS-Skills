"""TDD tests for P35 v5.4.0 — Test-to-code mapping."""

import hashlib
import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


@pytest.fixture
def populated_queries(queries):
    """Graph with test→code edges:
    test_calc() calls add() and multiply()
    test_utils() calls helper()
    orphan() is never called from tests
    """
    test_id = _make_id("tests/test_math.py::test_calc", "tests/test_math.py")
    test2_id = _make_id("tests/test_utils.py::test_utils", "tests/test_utils.py")
    add_id = _make_id("src/math.py::add", "src/math.py")
    mul_id = _make_id("src/math.py::multiply", "src/math.py")
    helper_id = _make_id("src/utils.py::helper", "src/utils.py")
    orphan_id = _make_id("src/utils.py::orphan", "src/utils.py")

    for nid, kind, name, qname, fpath in [
        (test_id, "function", "test_calc", "tests/test_math.py::test_calc", "tests/test_math.py"),
        (test2_id, "function", "test_utils", "tests/test_utils.py::test_utils", "tests/test_utils.py"),
        (add_id, "function", "add", "src/math.py::add", "src/math.py"),
        (mul_id, "function", "multiply", "src/math.py::multiply", "src/math.py"),
        (helper_id, "function", "helper", "src/utils.py::helper", "src/utils.py"),
        (orphan_id, "function", "orphan", "src/utils.py::orphan", "src/utils.py"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',1,2,1000)",
            (nid, kind, name, qname, fpath))

    # test_calc → add, multiply
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','test_math.py:3','resolved')", (test_id, add_id))
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','test_math.py:4','resolved')", (test_id, mul_id))
    # test_utils → helper
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','test_utils.py:3','resolved')", (test2_id, helper_id))
    queries.conn.commit()
    return queries


# ---------------------------------------------------------------------------
# P35a: Test file detection
# ---------------------------------------------------------------------------

class TestFileDetection:
    """Identify test files by path pattern."""

    def test_test_py_detected(self):
        from tws_graph.analysis.test_coverage import is_test_file

        assert is_test_file("tests/test_math.py") is True
        assert is_test_file("test_calc.py") is True
        assert is_test_file("src/test_utils.py") is True

    def test_prod_py_not_detected(self):
        from tws_graph.analysis.test_coverage import is_test_file

        assert is_test_file("src/math.py") is False
        assert is_test_file("lib/helper.py") is False
        assert is_test_file("main.py") is False

    def test_java_test_detected(self):
        from tws_graph.analysis.test_coverage import is_test_file

        assert is_test_file("src/test/CalcTest.java") is True
        assert is_test_file("MathTest.java") is True

    def test_ts_test_detected(self):
        from tws_graph.analysis.test_coverage import is_test_file

        assert is_test_file("calc.test.ts") is True
        assert is_test_file("calc.spec.ts") is True

    def test_empty_path_graceful(self):
        from tws_graph.analysis.test_coverage import is_test_file

        assert is_test_file("") is False
        assert is_test_file(None) is False


# ---------------------------------------------------------------------------
# P35b: Coverage mapping
# ---------------------------------------------------------------------------

class TestCoverageMapping:
    """Map test functions → production functions via calls edges."""

    def test_coverage_map_built(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)

        assert "coverage" in cov
        assert "test_to_code" in cov
        assert len(cov["coverage"]) >= 2  # add and multiply covered

    def test_add_is_covered(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)
        add_id = _make_id("src/math.py::add", "src/math.py")
        mul_id = _make_id("src/math.py::multiply", "src/math.py")

        assert add_id in cov["coverage"], "add should be covered by tests"
        assert mul_id in cov["coverage"], "multiply should be covered by tests"

    def test_orphan_is_uncovered(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)
        orphan_id = _make_id("src/utils.py::orphan", "src/utils.py")

        assert orphan_id in cov.get("uncovered", []), \
            "orphan should be in uncovered list"

    def test_test_to_code_mapping(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)
        test_id = _make_id("tests/test_math.py::test_calc", "tests/test_math.py")
        add_id = _make_id("src/math.py::add", "src/math.py")

        assert test_id in cov["test_to_code"], "test_calc should be in mapping"
        assert add_id in cov["test_to_code"][test_id], \
            "test_calc should cover add"

    def test_empty_graph(self, queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(queries)
        assert cov["coverage"] == {}
        assert cov["uncovered"] == []


# ---------------------------------------------------------------------------
# P35c: Gap report
# ---------------------------------------------------------------------------

class TestGapReport:
    """Generate report of uncovered production functions."""

    def test_gap_report_has_uncovered(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)

        assert "uncovered" in cov
        orphan_id = _make_id("src/utils.py::orphan", "src/utils.py")
        uncovered_ids = [u["id"] if isinstance(u, dict) else u for u in cov["uncovered"]]
        assert orphan_id in uncovered_ids

    def test_gap_report_has_file_path(self, populated_queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(populated_queries)
        for item in cov.get("uncovered", []):
            if isinstance(item, dict):
                assert "file_path" in item or "name" in item

    def test_gap_report_empty_graph(self, queries):
        from tws_graph.analysis.test_coverage import build_coverage_map

        cov = build_coverage_map(queries)
        assert cov["uncovered"] == []
        assert cov["total_production_functions"] == 0
