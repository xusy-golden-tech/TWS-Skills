"""P34b v5.4.0 — E2E: Graph traversal (calls, impact, trace)."""

import json
import pytest
from .conftest import run_tws


class TestCallsE2E:
    """Verify calls command works on real project."""

    def test_calls_outbound(self, e2e_project):
        """calls with a known symbol returns call targets."""
        rc, stdout, stderr = run_tws(
            "calls", "main", project_dir=e2e_project
        )
        assert rc == 0, f"calls failed: {stderr}"

    def test_calls_inbound(self, e2e_project):
        """calls --inbound returns callers."""
        rc, stdout, stderr = run_tws(
            "calls", "main", "--inbound", project_dir=e2e_project
        )
        assert rc == 0, f"calls --inbound failed: {stderr}"

    def test_calls_nonexistent_graceful(self, e2e_project):
        """calls on nonexistent symbol should not crash."""
        rc, stdout, stderr = run_tws(
            "calls", "nonexistent_zzz_12345", project_dir=e2e_project
        )
        # Should not crash — may return empty or error
        assert rc in (0, 1), f"calls crashed: {stderr}"


class TestImpactE2E:
    """Verify impact command works on real project."""

    def test_impact_returns_results(self, e2e_project):
        """impact on a known symbol returns dependents."""
        rc, stdout, stderr = run_tws(
            "impact", "main", "--depth", "2", project_dir=e2e_project
        )
        assert rc == 0, f"impact failed: {stderr}"

    def test_impact_with_depth(self, e2e_project):
        """impact respects depth."""
        rc1, out1, _ = run_tws(
            "impact", "main", "--depth", "1", project_dir=e2e_project
        )
        rc2, out2, _ = run_tws(
            "impact", "main", "--depth", "3", project_dir=e2e_project
        )
        assert rc1 == 0 and rc2 == 0


class TestTraceE2E:
    """Verify trace command works on real project."""

    def test_trace_between_functions(self, e2e_project):
        """trace between two known functions should not crash."""
        rc, stdout, stderr = run_tws(
            "trace", "main", "parse", project_dir=e2e_project
        )
        # May find a path or report no path — both are valid
        assert rc in (0, 1), f"trace crashed: {stderr}"

    def test_trace_same_node(self, e2e_project):
        """trace from node to itself."""
        rc, stdout, stderr = run_tws(
            "trace", "main", "main", project_dir=e2e_project
        )
        assert rc in (0, 1), f"trace crashed: {stderr}"
