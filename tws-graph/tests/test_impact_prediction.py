"""TDD tests for P40 v5.5.0 — Impact Prediction Engine."""

import hashlib
import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test_impact.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


@pytest.fixture
def populated_queries(queries):
    """Build a call chain: entry → mid → target → leaf, with tests."""
    nodes = [
        # Production code
        ("entry_func", "function", "src/main.py::entry_func", "src/main.py"),
        ("middle_func", "function", "src/logic/mid.py::middle_func", "src/logic/mid.py"),
        ("target_func", "function", "src/logic/core.py::target_func", "src/logic/core.py"),
        ("leaf_func", "function", "src/logic/core.py::leaf_func", "src/logic/core.py"),
        ("helper_func", "function", "src/utils.py::helper_func", "src/utils.py"),
        ("orphan_func", "function", "src/dead.py::orphan_func", "src/dead.py"),
        # Test code
        ("test_entry", "function", "tests/test_main.py::test_entry", "tests/test_main.py"),
        ("test_target", "function", "tests/test_core.py::test_target", "tests/test_core.py"),
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

    # Call edges: entry → mid → target → leaf
    for src, tgt in [
        ("entry_func", "middle_func"),
        ("middle_func", "target_func"),
        ("target_func", "leaf_func"),
        ("target_func", "helper_func"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
            "VALUES (?,?,'calls','test.py:1')", (ids[src], ids[tgt]))

    # Test edges: test_entry → entry_func, test_target → target_func
    for test_node, prod_node in [
        ("test_entry", "entry_func"),
        ("test_target", "target_func"),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc) "
            "VALUES (?,?,'calls','test.py:1')", (ids[test_node], ids[prod_node]))

    queries.conn.commit()
    return queries, ids


# ---------------------------------------------------------------------------
# P40a: Impact radius calculation
# ---------------------------------------------------------------------------

class TestImpactRadius:
    """Calculate impact radius of changing a symbol."""

    def test_direct_impact(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"], depth=1)
        assert result["direct_count"] > 0, "Should have direct dependents"
        direct_ids = [d["id"] for d in result["direct_dependents"]]
        assert ids["middle_func"] in direct_ids

    def test_indirect_impact(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"], depth=2)
        indirect_ids = [d["id"] for d in result["indirect_dependents"]]
        # entry → mid → target: entry is indirect dependent of target at depth 2
        assert ids["entry_func"] in indirect_ids

    def test_depth_limits_transitive_closure(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        r1 = predict_impact(queries, ids["target_func"], depth=1)
        r3 = predict_impact(queries, ids["target_func"], depth=3)
        assert r3["total_affected"] > r1["total_affected"], \
            "Deeper depth should find more affected nodes"

    def test_symbol_not_found(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, "nonexistent_id", depth=2)
        assert result["direct_count"] == 0
        assert result["total_affected"] == 0


# ---------------------------------------------------------------------------
# P40b: Test coverage integration
# ---------------------------------------------------------------------------

class TestImpactTestCoverage:
    """Impact prediction includes relevant tests."""

    def test_affected_tests_listed(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"], depth=2)
        assert "affected_tests" in result
        affected_test_names = [t["name"] for t in result["affected_tests"]]
        # test_target directly calls target_func
        assert "test_target" in affected_test_names

    def test_no_tests_returns_empty(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["orphan_func"], depth=1)
        assert result["affected_tests"] == []


# ---------------------------------------------------------------------------
# P40c: Risk scoring
# ---------------------------------------------------------------------------

class TestRiskScoring:
    """Risk score calculation."""

    def test_risk_score_present(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"], depth=3)
        assert "risk_score" in result
        assert 0 <= result["risk_score"] <= 100

    def test_higher_fanout_higher_risk(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        # target_func has 2 callees (leaf, helper) → fan-out 2
        r_target = predict_impact(queries, ids["target_func"], depth=2)
        # leaf_func has 0 callees → fan-out 0
        r_leaf = predict_impact(queries, ids["leaf_func"], depth=2)
        assert r_target["risk_score"] > r_leaf["risk_score"], \
            "Higher fan-out should mean higher risk"

    def test_affected_files_listed(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"], depth=2)
        assert "affected_files" in result
        assert len(result["affected_files"]) > 0


# ---------------------------------------------------------------------------
# P40d: Empty / edge cases
# ---------------------------------------------------------------------------

class TestImpactEdgeCases:
    """Edge case handling."""

    def test_empty_graph(self, queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        result = predict_impact(queries, "nonexistent", depth=2)
        assert result["direct_count"] == 0
        assert result["total_affected"] == 0
        assert result["risk_score"] == 0

    def test_default_depth(self, populated_queries):
        from tws_graph.analysis.impact_prediction import predict_impact
        queries, ids = populated_queries
        result = predict_impact(queries, ids["target_func"])
        # Default depth should be 3
        assert result["direct_count"] > 0


# ---------------------------------------------------------------------------
# P40e: CLI
# ---------------------------------------------------------------------------

class TestPredictImpactCLI:
    """CLI integration."""

    def test_predict_impact_help(self):
        import subprocess, sys
        r = subprocess.run(
            [sys.executable, "-m", "tws_graph", "predict-impact", "--help"],
            capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0
        assert "predict-impact" in (r.stdout + r.stderr).lower()
