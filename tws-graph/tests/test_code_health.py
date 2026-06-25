"""TDD tests for P41 v5.5.0 — Code Health Scores."""

import hashlib
import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test_health.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


@pytest.fixture
def populated_queries(queries):
    """3 files with varying health characteristics."""
    nodes = [
        # file_a: well-tested, no dead code
        ("a_func1", "function", "src/a.py::a_func1", "src/a.py"),
        ("a_func2", "function", "src/a.py::a_func2", "src/a.py"),
        ("test_a_func1", "function", "tests/test_a.py::test_a_func1", "tests/test_a.py"),
        ("test_a_func2", "function", "tests/test_a.py::test_a_func2", "tests/test_a.py"),
        # file_b: partially tested, some dead code
        ("b_func1", "function", "src/b.py::b_func1", "src/b.py"),
        ("b_func2", "function", "src/b.py::b_func2", "src/b.py"),
        ("b_dead", "function", "src/b.py::b_dead", "src/b.py"),
        ("test_b_func1", "function", "tests/test_b.py::test_b_func1", "tests/test_b.py"),
        # file_c: no tests, entirely dead
        ("c_func1", "function", "src/c.py::c_func1", "src/c.py"),
        ("c_func2", "function", "src/c.py::c_func2", "src/c.py"),
    ]

    ids = {}
    for name, kind, qname, fpath in nodes:
        nid = _make_id(qname, fpath)
        ids[name] = nid
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',1,10,1000)",
            (nid, kind, name, qname, fpath))

    # Test calls: test functions call production functions
    for test_node, prod_node in [
        ("test_a_func1", "a_func1"),
        ("test_a_func2", "a_func2"),
        ("test_b_func1", "b_func1"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
            "VALUES (?,?,'calls','test.py:1')", (ids[test_node], ids[prod_node]))

    # Internal calls within file
    queries._exec(
        "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
        "VALUES (?,?,'calls','a.py:1')", (ids["a_func1"], ids["a_func2"]))

    # Cross-file calls (adds coupling)
    queries._exec(
        "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
        "VALUES (?,?,'calls','a.py:1')", (ids["a_func1"], ids["b_func1"]))
    queries._exec(
        "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
        "VALUES (?,?,'imports','a.py:1')", (ids["a_func2"], ids["b_func2"]))

    queries.conn.commit()
    return queries, ids


# ---------------------------------------------------------------------------
# P41a: Health score calculation
# ---------------------------------------------------------------------------

class TestHealthScoreCalculation:
    """Per-file health score computation."""

    def test_all_files_have_score(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        prod_files = {s["file_path"] for s in scores}
        assert "src/a.py" in prod_files
        assert "src/b.py" in prod_files
        assert "src/c.py" in prod_files

    def test_score_range(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        for s in scores:
            assert 0 <= s["score"] <= 100, \
                f"Score out of range for {s['file_path']}: {s['score']}"

    def test_well_tested_scores_higher(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        score_map = {s["file_path"]: s["score"] for s in scores}
        # a.py (100% tested) should score higher than c.py (0% tested)
        assert score_map["src/a.py"] > score_map["src/c.py"], \
            "Well-tested file should score higher"

    def test_excludes_test_files(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        test_files = [s for s in scores if "test" in s["file_path"]]
        assert len(test_files) == 0, "Test files should be excluded"

    def test_empty_graph(self, queries):
        from tws_graph.analysis.code_health import compute_health_scores
        scores = compute_health_scores(queries)
        assert scores == []


# ---------------------------------------------------------------------------
# P41b: Health score components
# ---------------------------------------------------------------------------

class TestHealthComponents:
    """Individual health score components."""

    def test_coverage_component(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        score_map = {s["file_path"]: s for s in scores}
        # a.py has 2/2 functions tested → coverage should be high
        a = score_map["src/a.py"]
        assert a["coverage_pct"] > 50, f"a.py coverage should be >50%, got {a['coverage_pct']}"

    def test_dead_code_component(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        score_map = {s["file_path"]: s for s in scores}
        # b.py has b_dead which has no callers
        b = score_map["src/b.py"]
        assert "dead_count" in b

    def test_coupling_component(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        for s in scores:
            assert "external_deps" in s
            assert isinstance(s["external_deps"], int)


# ---------------------------------------------------------------------------
# P41c: Top/worst report
# ---------------------------------------------------------------------------

class TestHealthReport:
    """Top and worst file reports."""

    def test_scores_sorted_by_score(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        # Should be sorted by score descending
        sorted_scores = [s["score"] for s in scores]
        assert sorted_scores == sorted(sorted_scores, reverse=True), \
            "Scores should be sorted descending"

    def test_worst_files_are_lowest(self, populated_queries):
        from tws_graph.analysis.code_health import compute_health_scores
        queries, ids = populated_queries
        scores = compute_health_scores(queries)
        # c.py should be among the worst
        worst = scores[-1]
        assert worst["file_path"] == "src/c.py", \
            f"Expected c.py as worst, got {worst['file_path']}"


# ---------------------------------------------------------------------------
# P41d: CLI
# ---------------------------------------------------------------------------

class TestHealthCLI:
    """CLI integration."""

    def test_health_help(self):
        import subprocess, sys
        r = subprocess.run(
            [sys.executable, "-m", "tws_graph", "health", "--help"],
            capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0
        assert "health" in (r.stdout + r.stderr).lower()
