"""P34 v5.4.0 — Shared fixtures for E2E integration tests.

Uses g-ass-source as the primary E2E target (env var TWS_E2E_PROJECT).
Auto-skips if the project is not available.
"""

import os
import subprocess
import sys
import pytest


def _find_tws_graph():
    """Find tws-graph executable."""
    # Prefer the one in current venv
    exe = os.path.join(os.path.dirname(sys.executable), "tws-graph")
    if os.path.exists(exe) or os.path.exists(exe + ".exe"):
        return "tws-graph"
    return "tws-graph"


def _get_e2e_project():
    """Get E2E project path from env or default locations."""
    env = os.environ.get("TWS_E2E_PROJECT", "")
    if env and os.path.isdir(env):
        return env

    # Default paths
    candidates = [
        "/d/g-ass-source",
        "/d/gass",
        os.path.expanduser("~/g-ass-source"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            db_path = os.path.join(c, ".tws", "codegraph", "index.db")
            if os.path.exists(db_path):
                return c
    return None


def _run_tws(*args, project_dir=None, timeout=120):
    """Run tws-graph with given args. Returns (returncode, stdout, stderr)."""
    exe = _find_tws_graph()
    cmd = [exe] + list(args)
    if project_dir:
        cmd.extend(["--db", os.path.join(project_dir, ".tws", "codegraph", "index.db")])
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "tws-graph not found"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_project():
    """Path to the E2E test project (g-ass-source)."""
    proj = _get_e2e_project()
    if proj is None:
        pytest.skip("E2E project not found. Set TWS_E2E_PROJECT env var.")
    return proj


@pytest.fixture(scope="session")
def e2e_db(e2e_project):
    """Path to the indexed DB for the E2E project."""
    db = os.path.join(e2e_project, ".tws", "codegraph", "index.db")
    if not os.path.exists(db):
        pytest.skip(f"Index DB not found at {db}. Run tws-graph index first.")
    return db


@pytest.fixture(scope="session")
def e2e_indexed(e2e_project):
    """Ensure the E2E project is indexed. Returns project path."""
    db_path = os.path.join(e2e_project, ".tws", "codegraph", "index.db")
    if os.path.exists(db_path):
        return e2e_project

    # Try to index
    rc, stdout, stderr = _run_tws("index", project_dir=None)
    if rc != 0:
        pytest.skip(f"Could not index E2E project: {stderr}")
    return e2e_project


def run_tws(*args, project_dir=None, timeout=120):
    """Convenience wrapper for tests."""
    return _run_tws(*args, project_dir=project_dir, timeout=timeout)
