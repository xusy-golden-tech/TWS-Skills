"""TDD tests for P30 v5.3.0 — architecture analysis engine.

Tests: cycle detection, layer violation, module cohesion/coupling metrics.
All tests use SQLite directly (not the Store interface) to match CLI usage.
"""

import pytest
import hashlib

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


# ---------------------------------------------------------------------------
# P30a: Cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    """DFS-based cycle detection in call graphs."""

    def test_no_cycles_in_linear_graph(self, queries):
        """A → B → C: no cycles."""
        a_id = _make_id("a.py::a", "a.py")
        b_id = _make_id("b.py::b", "b.py")
        c_id = _make_id("c.py::c", "c.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "a", "a.py::a", "a.py"),
            (b_id, "function", "b", "b.py::b", "b.py"),
            (c_id, "function", "c", "c.py::c", "c.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','a.py:1','resolved')", (a_id, b_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','b.py:1','resolved')", (b_id, c_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries)
        assert len(cycles) == 0, f"Linear graph should have no cycles, got {cycles}"

    def test_direct_cycle_detected(self, queries):
        """A → B → A: one cycle of length 2."""
        a_id = _make_id("a.py::a", "a.py")
        b_id = _make_id("b.py::b", "b.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "a", "a.py::a", "a.py"),
            (b_id, "function", "b", "b.py::b", "b.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','a.py:1','resolved')", (a_id, b_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','b.py:1','resolved')", (b_id, a_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries)
        assert len(cycles) >= 1, f"Should detect cycle A→B→A, got {cycles}"

    def test_self_loop_detected(self, queries):
        """A → A: self-loop is a cycle."""
        a_id = _make_id("a.py::a", "a.py")

        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,'function','a','a.py::a','a.py','python',1,2,1000)",
            (a_id,))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','a.py:1','resolved')", (a_id, a_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries)
        assert len(cycles) >= 1, f"Self-loop should be detected, got {cycles}"

    def test_cycle_has_names(self, queries):
        """Each cycle entry should include function names and files."""
        a_id = _make_id("a.py::func_a", "a.py")
        b_id = _make_id("b.py::func_b", "b.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "func_a", "a.py::func_a", "a.py"),
            (b_id, "function", "func_b", "b.py::func_b", "b.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','a.py:1','resolved')", (a_id, b_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','b.py:1','resolved')", (b_id, a_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries)
        assert len(cycles) >= 1
        cycle = cycles[0]
        assert "names" in cycle or "cycle" in cycle, "Cycle should have names or cycle key"
        assert "files" in cycle, "Cycle should have files key"

    def test_empty_graph_no_error(self, queries):
        """Empty graph should return empty cycles list (no crash)."""
        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries)
        assert cycles == []

    def test_cycle_respects_max(self, queries):
        """max_cycles parameter should limit results."""
        a_id = _make_id("a.py::a", "a.py")
        b_id = _make_id("b.py::b", "b.py")
        c_id = _make_id("c.py::c", "c.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "a", "a.py::a", "a.py"),
            (b_id, "function", "b", "b.py::b", "b.py"),
            (c_id, "function", "c", "c.py::c", "c.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # A→B→C→A (one cycle)
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','a.py:1','resolved')", (a_id, b_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','b.py:1','resolved')", (b_id, c_id))
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','c.py:1','resolved')", (c_id, a_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_cycles
        cycles = detect_cycles(queries, max_cycles=0)
        assert len(cycles) == 0, "max_cycles=0 should return empty"


# ---------------------------------------------------------------------------
# P30b: Layer violation detection
# ---------------------------------------------------------------------------

class TestLayerViolation:
    """Detect calls that violate defined architecture layer boundaries."""

    def test_no_violation_when_layers_respected(self, queries):
        """UI → Service → Data: no violations."""
        ui_id = _make_id("src/ui/button.py::on_click", "src/ui/button.py")
        svc_id = _make_id("src/service/auth.py::login", "src/service/auth.py")
        data_id = _make_id("src/data/db.py::query", "src/data/db.py")

        for nid, kind, name, qname, fpath in [
            (ui_id, "function", "on_click", "src/ui/button.py::on_click", "src/ui/button.py"),
            (svc_id, "function", "login", "src/service/auth.py::login", "src/service/auth.py"),
            (data_id, "function", "query", "src/data/db.py::query", "src/data/db.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # UI → Service (allowed: higher → lower)
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','ui/button.py:1','resolved')", (ui_id, svc_id))
        # Service → Data (allowed)
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','service/auth.py:1','resolved')", (svc_id, data_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_layer_violations
        layers = {
            "ui": {"pattern": "src/ui/**", "level": 1},
            "service": {"pattern": "src/service/**", "level": 2},
            "data": {"pattern": "src/data/**", "level": 3},
        }
        violations = detect_layer_violations(queries, layers, direction="higher-to-lower")
        assert len(violations) == 0, f"Expected 0 violations, got {violations}"

    def test_violation_detected_on_reverse_call(self, queries):
        """Data → UI: violation (lower layer calling higher)."""
        ui_id = _make_id("src/ui/button.py::on_click", "src/ui/button.py")
        data_id = _make_id("src/data/db.py::query", "src/data/db.py")

        for nid, kind, name, qname, fpath in [
            (ui_id, "function", "on_click", "src/ui/button.py::on_click", "src/ui/button.py"),
            (data_id, "function", "query", "src/data/db.py::query", "src/data/db.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        # Data → UI (violation: lower calling higher)
        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','data/db.py:1','resolved')", (data_id, ui_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_layer_violations
        layers = {
            "ui": {"pattern": "src/ui/**", "level": 1},
            "data": {"pattern": "src/data/**", "level": 3},
        }
        violations = detect_layer_violations(queries, layers, direction="higher-to-lower")
        assert len(violations) >= 1, f"Should detect violation, got {violations}"

    def test_unmatched_layer_ignored(self, queries):
        """Calls from unmatched layers should be ignored."""
        a_id = _make_id("other/utils.py::helper", "other/utils.py")
        b_id = _make_id("other/utils.py::worker", "other/utils.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "helper", "other/utils.py::helper", "other/utils.py"),
            (b_id, "function", "worker", "other/utils.py::worker", "other/utils.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','utils.py:1','resolved')", (a_id, b_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import detect_layer_violations
        layers = {
            "ui": {"pattern": "src/ui/**", "level": 1},
        }
        violations = detect_layer_violations(queries, layers, direction="higher-to-lower")
        assert len(violations) == 0, "Unmatched layers should produce no violations"


# ---------------------------------------------------------------------------
# P30c: Module cohesion/coupling metrics
# ---------------------------------------------------------------------------

class TestModuleMetrics:
    """Calculate cohesion, coupling, and instability metrics."""

    def test_cohesion_perfect_for_isolated_module(self, queries):
        """Module with only internal calls has cohesion = 1.0."""
        f1_id = _make_id("mymod/utils.py::func1", "mymod/utils.py")
        f2_id = _make_id("mymod/utils.py::func2", "mymod/utils.py")

        for nid, kind, name, qname, fpath in [
            (f1_id, "function", "func1", "mymod/utils.py::func1", "mymod/utils.py"),
            (f2_id, "function", "func2", "mymod/utils.py::func2", "mymod/utils.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','mymod/utils.py:1','resolved')", (f1_id, f2_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import compute_module_metrics
        metrics = compute_module_metrics(queries)
        assert len(metrics) >= 1

        mymod = [m for m in metrics if m["module"] == "mymod/utils.py"][0]
        assert mymod["internal_calls"] >= 1
        assert mymod["external_calls"] == 0
        assert mymod["cohesion"] == 1.0

    def test_instability_range(self, queries):
        """Instability should be in [0.0, 1.0]."""
        a_id = _make_id("mod_a/a.py::fa", "mod_a/a.py")
        b_id = _make_id("mod_b/b.py::fb", "mod_b/b.py")

        for nid, kind, name, qname, fpath in [
            (a_id, "function", "fa", "mod_a/a.py::fa", "mod_a/a.py"),
            (b_id, "function", "fb", "mod_b/b.py::fb", "mod_b/b.py"),
        ]:
            queries._exec(
                "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
                "language, start_line, end_line, updated_at) "
                "VALUES (?,?,?,?,?,'python',1,2,1000)",
                (nid, kind, name, qname, fpath))

        queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                       "VALUES (?,?,'calls','mod_a/a.py:1','resolved')", (a_id, b_id))
        queries.conn.commit()

        from tws_graph.analysis.architecture import compute_module_metrics
        metrics = compute_module_metrics(queries)
        for m in metrics:
            assert 0.0 <= m.get("instability", 0.0) <= 1.0, \
                f"Instability out of range for {m['module']}: {m.get('instability')}"

    def test_empty_graph_metrics_no_error(self, queries):
        """Empty graph should return empty metrics list."""
        from tws_graph.analysis.architecture import compute_module_metrics
        metrics = compute_module_metrics(queries)
        assert metrics == []

    def test_metrics_structure(self, queries):
        """Verify metric output has all required fields."""
        f_id = _make_id("pkg/mod.py::f", "pkg/mod.py")
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,'function','f','pkg/mod.py::f','pkg/mod.py','python',1,2,1000)",
            (f_id,))
        queries.conn.commit()

        from tws_graph.analysis.architecture import compute_module_metrics
        metrics = compute_module_metrics(queries)
        assert len(metrics) >= 1
        m = metrics[0]
        required_fields = ["module", "internal_calls", "external_calls",
                          "afferent_coupling", "efferent_coupling",
                          "cohesion", "instability"]
        for field in required_fields:
            assert field in m, f"Missing field {field} in metrics"
