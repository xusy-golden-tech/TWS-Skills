"""Tests against real-world projects — MDRead (Kotlin) and TWS-Skills (TS+Python)."""

import pytest
from pathlib import Path
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.indexer.orchestrator import ExtractionOrchestrator


# ---------------------------------------------------------------------------
# Known project paths (these are on xusy's machine)
# ---------------------------------------------------------------------------

MDREAD_PATH = Path(r"D:\MDRead")
TWS_SKILLS_PATH = Path(r"D:\TWS-Skills")


def _require_project(path: Path, name: str):
    if not path.is_dir():
        pytest.skip(f"{name} not found at {path}")


class TestRealProjectMDRead:
    """Index the MDRead Kotlin project and verify basic stats."""

    @pytest.fixture(scope="class")
    def mdread_result(self, tmp_path_factory):
        _require_project(MDREAD_PATH, "MDRead")
        db_path = str(tmp_path_factory.mktemp("mdread") / "codegraph" / "index.db")
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(MDREAD_PATH), queries)
        result = orch.index_all()
        db.close()
        return result

    def test_indexes_without_crashing(self, mdread_result):
        """MDRead should index without errors (individual file errors are OK)."""
        assert mdread_result.files_indexed > 0
        # 54 Kotlin files — should index most of them
        assert mdread_result.files_indexed >= 40

    def test_produces_nodes(self, mdread_result):
        assert mdread_result.nodes_created > 50

    def test_produces_edges(self, mdread_result):
        assert mdread_result.edges_created > 100

    def test_resolve_completes(self, mdread_result):
        assert mdread_result.resolve_result is not None
        assert mdread_result.resolve_result.total_checked > 0


class TestRealProjectTWS:
    """Index TWS-Skills itself (Python + TypeScript SKILL files)."""

    @pytest.fixture(scope="class")
    def tws_result(self, tmp_path_factory):
        _require_project(TWS_SKILLS_PATH, "TWS-Skills")
        db_path = str(tmp_path_factory.mktemp("tws") / "codegraph" / "index.db")
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(TWS_SKILLS_PATH), queries)
        result = orch.index_all()
        db.close()
        return result

    def test_indexes_python_files(self, tws_result):
        assert tws_result.files_indexed > 0

    def test_produces_nodes(self, tws_result):
        assert tws_result.nodes_created > 10

    def test_produces_edges(self, tws_result):
        assert tws_result.edges_created > 0
