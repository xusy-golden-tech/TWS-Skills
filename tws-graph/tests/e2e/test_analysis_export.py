"""P34c v5.4.0 — E2E: Analysis (taint, cycles, metrics) and export."""

import json
import os
import tempfile
import pytest
from .conftest import run_tws


class TestTaintE2E:
    """Verify taint analysis works on real project."""

    def test_taint_does_not_crash(self, e2e_project):
        """taint command should complete without error."""
        rc, stdout, stderr = run_tws(
            "taint", project_dir=e2e_project, timeout=180
        )
        assert rc == 0, f"taint failed: {stderr}"

    def test_taint_json_output(self, e2e_project):
        """taint --json should produce valid JSON."""
        rc, stdout, stderr = run_tws(
            "taint", "--json", project_dir=e2e_project, timeout=180
        )
        assert rc == 0, f"taint --json failed: {stderr}"
        if stdout.strip():
            data = json.loads(stdout)
            assert isinstance(data, (list, dict)), "JSON output should be list or dict"


class TestCyclesE2E:
    """Verify cycle detection works on real project."""

    def test_cycles_does_not_crash(self, e2e_project):
        """cycles command should complete without error."""
        rc, stdout, stderr = run_tws(
            "cycles", project_dir=e2e_project, timeout=120
        )
        assert rc == 0, f"cycles failed: {stderr}"

    def test_cycles_with_max(self, e2e_project):
        """cycles --max should work."""
        rc, stdout, stderr = run_tws(
            "cycles", "--max", "5", project_dir=e2e_project, timeout=120
        )
        assert rc == 0, f"cycles --max failed: {stderr}"


class TestMetricsE2E:
    """Verify module metrics work on real project."""

    def test_metrics_does_not_crash(self, e2e_project):
        """metrics command should complete without error."""
        rc, stdout, stderr = run_tws(
            "metrics", project_dir=e2e_project, timeout=120
        )
        assert rc == 0, f"metrics failed: {stderr}"

    def test_metrics_with_limit(self, e2e_project):
        """metrics --limit should constrain output."""
        rc, stdout, stderr = run_tws(
            "metrics", "--limit", "3", project_dir=e2e_project, timeout=120
        )
        assert rc == 0, f"metrics --limit failed: {stderr}"


class TestLayersE2E:
    """Verify layer violation detection works on real project."""

    def test_layers_does_not_crash(self, e2e_project):
        """layers command with a simple layer spec should not crash."""
        layers_json = '{"ui":{"pattern":"src/**","level":1},"lib":{"pattern":"lib/**","level":2}}'
        rc, stdout, stderr = run_tws(
            "layers", "--layers", layers_json, project_dir=e2e_project, timeout=120
        )
        assert rc == 0, f"layers failed: {stderr}"


class TestExportE2E:
    """Verify graph export works on real project."""

    def test_export_dot(self, e2e_project):
        """export dot should produce dot output."""
        rc, stdout, stderr = run_tws(
            "export", "dot", "--limit", "50", project_dir=e2e_project
        )
        assert rc == 0, f"export dot failed: {stderr}"
        assert "digraph" in stdout or "graph" in stdout.lower(), \
            "DOT output should contain digraph/graph"

    def test_export_json(self, e2e_project):
        """export json should produce valid JSON."""
        rc, stdout, stderr = run_tws(
            "export", "json", "--limit", "50", project_dir=e2e_project
        )
        assert rc == 0, f"export json failed: {stderr}"
        data = json.loads(stdout)
        assert "nodes" in data
        assert "edges" in data

    def test_export_json_to_file(self, e2e_project):
        """export json -o writes to file."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            rc, stdout, stderr = run_tws(
                "export", "json", "--limit", "10", "-o", tmp, project_dir=e2e_project
            )
            assert rc == 0, f"export json -o failed: {stderr}"
            assert os.path.exists(tmp), "Output file should exist"
            with open(tmp) as f:
                data = json.load(f)
            assert "nodes" in data
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class TestSnapshotDiffE2E:
    """Verify snapshot and diff work on real project."""

    def test_snapshot_and_diff(self, e2e_project):
        """Snapshot create + diff should work."""
        # Create snapshot
        rc1, _, err1 = run_tws(
            "snapshot", "e2e_test_snap", project_dir=e2e_project, timeout=60
        )
        # Diff (even if only one snapshot, should not crash)
        rc2, out2, err2 = run_tws(
            "diff", project_dir=e2e_project, timeout=60
        )
        # Both operations should not crash
        assert rc1 in (0, 1), f"snapshot failed: {err1}"
        assert rc2 in (0, 1), f"diff failed: {err2}"
