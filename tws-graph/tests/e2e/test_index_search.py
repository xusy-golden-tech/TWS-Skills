"""P34a v5.4.0 — E2E: Index and search on real project."""

import json
import pytest
from .conftest import run_tws


class TestIndexE2E:
    """Verify index pipeline works end-to-end."""

    def test_index_command_exists(self, e2e_project):
        """tws-graph --version works."""
        rc, stdout, stderr = run_tws("--version")
        assert rc == 0, f"tws-graph --version failed: {stderr}"
        assert "tws-graph" in stdout

    def test_sync_does_not_crash(self, e2e_project):
        """tws-graph sync completes without error."""
        rc, stdout, stderr = run_tws("sync", project_dir=e2e_project, timeout=300)
        assert rc == 0, f"sync failed: {stderr}"

    def test_indexed_db_has_nodes(self, e2e_db):
        """The indexed DB should have nodes."""
        import sqlite3
        conn = sqlite3.connect(e2e_db)
        count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        conn.close()
        assert count > 10000, f"Expected >10000 nodes, got {count}"

    def test_indexed_db_has_edges(self, e2e_db):
        """The indexed DB should have edges."""
        import sqlite3
        conn = sqlite3.connect(e2e_db)
        count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        conn.close()
        assert count > 10000, f"Expected >10000 edges, got {count}"


class TestSearchE2E:
    """Verify search works on real data."""

    def test_search_function(self, e2e_project):
        """Search should return results for a function query."""
        rc, stdout, stderr = run_tws(
            "search", "kind:function", project_dir=e2e_project
        )
        assert rc == 0, f"search failed: {stderr}"
        assert len(stdout.strip()) > 0, "Search returned empty output"

    def test_search_class(self, e2e_project):
        """Search should return results for a class query."""
        rc, stdout, stderr = run_tws(
            "search", "kind:class", project_dir=e2e_project
        )
        assert rc == 0, f"search failed: {stderr}"
        assert len(stdout.strip()) > 0, "Search returned empty output"

    def test_search_with_lang_filter(self, e2e_project):
        """Search with lang filter should work."""
        rc, stdout, stderr = run_tws(
            "search", "lang:python", project_dir=e2e_project
        )
        assert rc == 0, f"search failed: {stderr}"

    def test_search_with_path_filter(self, e2e_project):
        """Search with path filter should work."""
        rc, stdout, stderr = run_tws(
            "search", "path:src", project_dir=e2e_project
        )
        assert rc == 0, f"search failed: {stderr}"
