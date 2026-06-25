"""Tests against real-world projects — MDRead (Kotlin), TWS-Skills (TS+Python),
and spring-petclinic (Java P49 verification).
"""

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
SPRING_PETCLINIC_PATH = Path(r"D:\spring-petclinic")


def _require_project(path: Path, name: str):
    if not path.is_dir():
        pytest.skip(f"{name} not found at {path}")


# ---------------------------------------------------------------------------
# P49: Quality gate verification helpers
# ---------------------------------------------------------------------------

def _check_quality_gates(queries: QueryBuilder) -> dict:
    """Run P49 quality gate checks and return a results dict."""
    conn = queries.conn

    # Node count
    node_count = conn.execute("SELECT COUNT(*) as cnt FROM nodes").fetchone()["cnt"]

    # Edge count
    edge_count = conn.execute("SELECT COUNT(*) as cnt FROM edges").fetchone()["cnt"]

    # File count (only files with nodes)
    file_count = conn.execute(
        "SELECT COUNT(DISTINCT file_path) as cnt FROM nodes"
    ).fetchone()["cnt"]

    # Edge type coverage
    edge_kinds = conn.execute(
        "SELECT COUNT(DISTINCT kind) as cnt FROM edges"
    ).fetchone()["cnt"]

    # Per-kind breakdown
    kind_breakdown = {}
    for row in conn.execute(
        "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
    ).fetchall():
        kind_breakdown[row["kind"]] = row["cnt"]

    # Node density
    node_density = round(node_count / max(file_count, 1), 2)

    # Edge density
    edge_density = round(edge_count / max(file_count, 1), 2)

    return {
        "files": file_count,
        "nodes": node_count,
        "edges": edge_count,
        "edge_kinds": edge_kinds,
        "node_density": node_density,
        "edge_density": edge_density,
        "kind_breakdown": kind_breakdown,
        "gates": {
            "node_density_gte_3": node_density >= 3,
            "edge_density_gte_5": edge_density >= 5,
            "edge_kinds_gte_15": edge_kinds >= 15,
            "has_imports": "imports" in kind_breakdown,
            "has_decorates": "decorates" in kind_breakdown,
            "has_instantiates": "instantiates" in kind_breakdown,
            "has_type_ref": "type_ref" in kind_breakdown,
        },
    }


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


class TestRealProjectSpringPetclinic:
    """P49: Java verification pipeline — spring-petclinic quality gates.

    Download: git clone https://github.com/spring-projects/spring-petclinic.git
    Expected path: D:\\spring-petclinic

    Quality gates (v5.7.0):
      - Node density ≥ 3/file
      - Edge density ≥ 5/file
      - Edge type count ≥ 15
      - Key edge kinds present: imports, decorates, instantiates, type_ref
    """

    @pytest.fixture(scope="class")
    def petclinic_result(self, tmp_path_factory):
        _require_project(SPRING_PETCLINIC_PATH, "spring-petclinic")
        db_path = str(
            tmp_path_factory.mktemp("petclinic") / "codegraph" / "index.db"
        )
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.conn)
        orch = ExtractionOrchestrator(str(SPRING_PETCLINIC_PATH), queries)
        result = orch.index_all()
        self._queries = queries
        yield result
        try:
            db.close()
        except Exception:
            pass

    def test_indexes_without_crashing(self, petclinic_result):
        """spring-petclinic should index without crashes."""
        assert petclinic_result.files_indexed > 0

    def test_produces_nodes(self, petclinic_result):
        assert petclinic_result.nodes_created > 100

    def test_produces_edges(self, petclinic_result):
        assert petclinic_result.edges_created > 200

    def test_resolve_completes(self, petclinic_result):
        assert petclinic_result.resolve_result is not None
        assert petclinic_result.resolve_result.total_checked > 0

    def test_node_density_gte_3(self, petclinic_result):
        """P49 quality gate: node density ≥ 3 per file."""
        _require_project(SPRING_PETCLINIC_PATH, "spring-petclinic")
        db = DatabaseConnection.initialize(
            str(Path(petclinic_result._db_path or "")),
        )
        # Re-read from result — nodes/file ratio
        files = petclinic_result.files_indexed
        nodes = petclinic_result.nodes_created
        density = nodes / max(files, 1)
        assert density >= 3, f"Node density {density:.1f} < 3"

    def test_edge_density_gte_5(self, petclinic_result):
        """P49 quality gate: edge density ≥ 5 per file."""
        files = petclinic_result.files_indexed
        edges = petclinic_result.edges_created
        density = edges / max(files, 1)
        assert density >= 5, f"Edge density {density:.1f} < 5"

    def test_resolve_result_summary(self, petclinic_result):
        """Resolution results are reasonable."""
        rr = petclinic_result.resolve_result
        assert rr is not None
        # Most edges should be resolved
        total = rr.resolved + rr.unresolved + rr.ambiguous
        if total > 0:
            resolve_rate = rr.resolved / total
            assert resolve_rate > 0.3, f"Resolve rate {resolve_rate:.1%} too low"
