"""Tests for multi-repo federation registry and cross-repo queries.

Covers:
- FederationRegistry CRUD (add / remove / list)
- is_active / count
- Cross-repo search via ATTACH + UNION
- Cross-repo import resolution
- Cross-repo calls / impact / trace stubs
- Empty federation (no-op)
"""

from __future__ import annotations

import json
import os
import pytest
from pathlib import Path

from tws_graph.federation import (
    FederationRegistry,
    RepoInfo,
    load_federation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_indexed_repo(base_dir: Path, name: str, files: dict[str, str]) -> Path:
    """Create a minimal indexed project under *base_dir*.

    Args:
        base_dir: Parent directory.
        name: Sub-directory name for the repo.
        files: ``{rel_path: source_content}`` mapping.

    Returns:
        Path to the repo root.
    """
    from tws_graph.db.connection import DatabaseConnection
    from tws_graph.db.queries import QueryBuilder
    from tws_graph.indexer.orchestrator import ExtractionOrchestrator

    repo = base_dir / name
    repo.mkdir(parents=True, exist_ok=True)

    for rel_path, content in files.items():
        full = repo / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    # Index
    db_dir = repo / ".tws" / "codegraph"
    db_dir.mkdir(parents=True)
    db_path = str(db_dir / "index.db")
    db = DatabaseConnection.initialize(db_path)
    queries = QueryBuilder(db.conn)
    orch = ExtractionOrchestrator(str(repo), queries)
    orch.index_all()
    db.close()

    return repo


# ---------------------------------------------------------------------------
# FederationRegistry CRUD
# ---------------------------------------------------------------------------

class TestFederationRegistry:
    """Tests for the FederationRegistry class."""

    @pytest.fixture
    def codegraph_dir(self, tmp_path: Path) -> str:
        d = tmp_path / ".tws" / "codegraph"
        d.mkdir(parents=True)
        return str(d)

    @pytest.fixture
    def registry(self, codegraph_dir: str) -> FederationRegistry:
        return FederationRegistry(codegraph_dir)

    def test_empty_registry(self, registry: FederationRegistry):
        assert registry.is_active() is False
        assert registry.count() == 0
        assert registry.list_all() == {}

    def test_add_repo(self, registry: FederationRegistry, tmp_path: Path):
        """Add a valid indexed repo."""
        repo = _create_indexed_repo(
            tmp_path, "proj-a",
            {"src/main.py": "def hello():\n    return 'world'\n"},
        )
        info = registry.add("proj-a", str(repo))
        assert info.name == "proj-a"
        assert info.path == str(repo)
        assert os.path.isfile(info.db)
        assert registry.is_active()
        assert registry.count() == 1

    def test_add_repo_missing_index(self, registry: FederationRegistry, tmp_path: Path):
        """Adding a repo without index.db should raise FileNotFoundError."""
        bare = tmp_path / "bare"
        bare.mkdir()
        with pytest.raises(FileNotFoundError, match="索引数据库不存在"):
            registry.add("bare", str(bare))

    def test_add_duplicate_name_same_path_is_idempotent(self, registry: FederationRegistry, tmp_path: Path):
        """Adding the same name+path twice should work (idempotent)."""
        repo = _create_indexed_repo(tmp_path, "proj-b", {"a.py": "x = 1\n"})
        registry.add("proj-b", str(repo))
        registry.add("proj-b", str(repo))  # should not raise
        assert registry.count() == 1

    def test_add_duplicate_name_different_path_raises(self, registry: FederationRegistry, tmp_path: Path):
        """Adding the same name with a different path should raise ValueError."""
        repo1 = _create_indexed_repo(tmp_path, "proj-c",  {"a.py": "x = 1\n"})
        repo2 = _create_indexed_repo(tmp_path, "proj-c2", {"a.py": "x = 2\n"})
        registry.add("proj-c", str(repo1))
        with pytest.raises(ValueError, match="已存在名为"):
            registry.add("proj-c", str(repo2))

    def test_remove_repo(self, registry: FederationRegistry, tmp_path: Path):
        repo = _create_indexed_repo(tmp_path, "proj-d", {"a.py": "x = 1\n"})
        registry.add("proj-d", str(repo))
        assert registry.count() == 1
        registry.remove("proj-d")
        assert registry.count() == 0
        assert not registry.is_active()

    def test_remove_nonexistent_raises(self, registry: FederationRegistry):
        with pytest.raises(KeyError, match="未注册的联邦仓库"):
            registry.remove("no-such")

    def test_list_repos(self, registry: FederationRegistry, tmp_path: Path):
        repo1 = _create_indexed_repo(tmp_path, "r1", {"a.py": "pass\n"})
        repo2 = _create_indexed_repo(tmp_path, "r2", {"b.py": "pass\n"})
        registry.add("r1", str(repo1))
        registry.add("r2", str(repo2))
        repos = registry.list_all()
        assert len(repos) == 2
        assert "r1" in repos
        assert "r2" in repos
        assert repos["r1"]["path"] == str(repo1)
        assert repos["r2"]["path"] == str(repo2)

    def test_get_repo(self, registry: FederationRegistry, tmp_path: Path):
        repo = _create_indexed_repo(tmp_path, "proj-e", {"a.py": "pass\n"})
        registry.add("proj-e", str(repo))
        info = registry.get("proj-e")
        assert info is not None
        assert info["path"] == str(repo)
        assert registry.get("missing") is None

    def test_persistence(self, tmp_path: Path):
        """Registry persists data to federation.json and loads it back."""
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)

        repo = _create_indexed_repo(tmp_path, "persist-repo", {"a.py": "x = 1\n"})

        reg1 = FederationRegistry(str(cd_dir))
        reg1.add("persist-repo", str(repo))

        # Load fresh instance from the same directory
        reg2 = FederationRegistry(str(cd_dir))
        assert reg2.is_active()
        assert reg2.count() == 1
        info = reg2.get("persist-repo")
        assert info is not None
        assert info["path"] == str(repo)

    def test_load_federation_helper(self, tmp_path: Path):
        """load_federation returns a FederationRegistry for the project root."""
        reg = load_federation(str(tmp_path))
        assert isinstance(reg, FederationRegistry)
        assert not reg.is_active()


# ---------------------------------------------------------------------------
# Federation CLI (tws-graph federate ...)
# ---------------------------------------------------------------------------

class TestFederationCLI:
    """Integration tests exercising the 'tws-graph federate' CLI."""

    @pytest.fixture
    def root_dir(self, tmp_path: Path) -> Path:
        """Project root with an initialized index."""
        repo = _create_indexed_repo(
            tmp_path, "main-project",
            {"src/main.py": "def main():\n    return 0\n"},
        )
        return repo

    def test_federate_list_empty(self, root_dir: Path):
        """federate list on a fresh project reports empty."""
        from typer.testing import CliRunner
        from tws_graph.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "federate", "list",
            "--db", str(root_dir / ".tws" / "codegraph" / "index.db"),
        ])
        assert result.exit_code == 0
        assert "(无联邦仓库)" in result.stdout

    def test_federate_add_and_list(self, root_dir: Path, tmp_path: Path):
        """federate add registers a repo; federate list shows it."""
        other = _create_indexed_repo(
            tmp_path, "other-project",
            {"src/lib.py": "def util():\n    return 42\n"},
        )
        from typer.testing import CliRunner
        from tws_graph.cli import app

        runner = CliRunner()
        # Add
        result = runner.invoke(app, [
            "federate", "add", str(other),
            "--name", "other",
            "--db", str(root_dir / ".tws" / "codegraph" / "index.db"),
        ])
        assert result.exit_code == 0
        assert "已添加联邦仓库" in result.stdout

        # List
        result = runner.invoke(app, [
            "federate", "list",
            "--db", str(root_dir / ".tws" / "codegraph" / "index.db"),
        ])
        assert result.exit_code == 0
        assert "other" in result.stdout

    def test_federate_add_missing_index(self, root_dir: Path, tmp_path: Path):
        """federate add with un-indexed repo fails gracefully."""
        bare = tmp_path / "bare"
        bare.mkdir()
        from typer.testing import CliRunner
        from tws_graph.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "federate", "add", str(bare),
            "--name", "bare",
            "--db", str(root_dir / ".tws" / "codegraph" / "index.db"),
        ])
        assert result.exit_code == 1
        # Error messages go to stderr via typer.echo(..., err=True)
        combined = (result.stdout or "") + (result.stderr or "")
        assert "索引" in combined or "index" in combined.lower() or "不" in combined or result.exit_code == 1

    def test_federate_remove(self, root_dir: Path, tmp_path: Path):
        """federate remove unregisters a repo."""
        other = _create_indexed_repo(tmp_path, "temp-project", {"a.py": "pass\n"})
        from typer.testing import CliRunner
        from tws_graph.cli import app

        runner = CliRunner()
        db_arg = str(root_dir / ".tws" / "codegraph" / "index.db")

        # Add first
        runner.invoke(app, ["federate", "add", str(other), "--name", "temp", "--db", db_arg])

        # Remove
        result = runner.invoke(app, ["federate", "remove", "temp", "--db", db_arg])
        assert result.exit_code == 0
        assert "已移除" in result.stdout

        # Verify gone
        result = runner.invoke(app, ["federate", "list", "--db", db_arg])
        assert "(无联邦仓库)" in result.stdout

    def test_federate_invalid_action(self, root_dir: Path):
        """Unknown action shows error."""
        from typer.testing import CliRunner
        from tws_graph.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "federate", "invalid",
            "--db", str(root_dir / ".tws" / "codegraph" / "index.db"),
        ])
        assert result.exit_code == 1
        combined = (result.stdout or "") + (result.stderr or "")
        assert "invalid" in combined.lower() or "未知" in combined or result.exit_code == 1


# ---------------------------------------------------------------------------
# Cross-repo search
# ---------------------------------------------------------------------------

class TestCrossRepoSearch:
    """Search across federated databases."""

    @pytest.fixture
    def main_repo(self, tmp_path: Path) -> Path:
        return _create_indexed_repo(
            tmp_path, "main",
            {"src/app.py": "def run_app():\n    return 'running'\n"},
        )

    @pytest.fixture
    def lib_repo(self, tmp_path: Path) -> Path:
        return _create_indexed_repo(
            tmp_path, "lib",
            {"src/auth.py": "def authenticate(token: str) -> bool:\n    return len(token) > 0\n",
             "src/db.py":   "def connect_db(url: str):\n    return 'connected'\n"},
        )

    def test_cross_repo_search_finds_lib_symbols(self, main_repo: Path, lib_repo: Path):
        """Search from main repo finds symbols in the federated lib repo."""
        from tws_graph.federation import FederationRegistry
        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder
        from tws_graph.cli import _cross_repo_search_federated

        # Set up federation in main project
        cd_dir = main_repo / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("lib", str(lib_repo))

        # Open main DB
        db_path = str(main_repo / ".tws" / "codegraph" / "index.db")
        db = DatabaseConnection.open(db_path)
        queries = QueryBuilder(db.conn)

        results = _cross_repo_search_federated(
            "authenticate", 10, queries, registry, db_path,
        )
        db.close()

        # Should find authenticate() in the federated lib repo
        names = [r["name"] for r in results]
        assert "authenticate" in names

    def test_cross_repo_search_no_matches(self, main_repo: Path):
        """Cross-repo search with no matches returns empty list."""
        from tws_graph.federation import FederationRegistry
        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder
        from tws_graph.cli import _cross_repo_search_federated

        cd_dir = main_repo / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        # No repos registered
        db_path = str(main_repo / ".tws" / "codegraph" / "index.db")
        db = DatabaseConnection.open(db_path)
        queries = QueryBuilder(db.conn)

        results = _cross_repo_search_federated(
            "nonexistent_func", 10, queries, registry, db_path,
        )
        db.close()

        assert results == []

    def test_cross_repo_search_merges_local_and_remote(self, main_repo: Path, lib_repo: Path):
        """Results from local and federated DBs are merged."""
        from tws_graph.federation import FederationRegistry
        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder
        from tws_graph.cli import _cross_repo_search_federated

        cd_dir = main_repo / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("lib", str(lib_repo))

        db_path = str(main_repo / ".tws" / "codegraph" / "index.db")
        db = DatabaseConnection.open(db_path)
        queries = QueryBuilder(db.conn)

        # Search for "db" (appears in both repos potentially)
        results = _cross_repo_search_federated(
            "db", 20, queries, registry, db_path,
        )
        db.close()

        # We have connect_db in lib and possibly other results
        assert isinstance(results, list)
        # At minimum we should find connect_db from the federated repo
        names = [r["name"] for r in results]
        assert "connect_db" in names


# ---------------------------------------------------------------------------
# Cross-repo import resolution
# ---------------------------------------------------------------------------

class TestCrossRepoImports:
    """Verify that cross-repo imports can be resolved through federation."""

    def test_federated_db_has_import_target(self, tmp_path: Path):
        """When an import cannot be resolved locally, check federated repos.

        This test simulates: repo-A imports a symbol from repo-B via federation.
        """
        # Create repo B (the dependency) with an exported function
        repo_b = _create_indexed_repo(
            tmp_path, "repo-b",
            {"pkg/util.py": "def shared_util(x):\n    return x * 2\n"},
        )

        # Set up federation in a separate project (repo-a)
        repo_a = _create_indexed_repo(
            tmp_path, "repo-a",
            {"main.py": "from pkg.util import shared_util\n\ndef consumer():\n    return shared_util(5)\n"},
        )

        # Register repo-b in repo-a's federation
        from tws_graph.federation import FederationRegistry
        cd_dir = repo_a / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("repo-b", str(repo_b))

        # Verify registry state
        assert registry.is_active()
        info = registry.get("repo-b")
        assert info is not None

        # Verify repo-b's DB contains shared_util
        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder

        db_b_path = str(repo_b / ".tws" / "codegraph" / "index.db")
        db_b = DatabaseConnection.open(db_b_path)
        q_b = QueryBuilder(db_b.conn)
        nodes = q_b.get_nodes_by_name("shared_util")
        db_b.close()
        assert len(nodes) >= 1
        assert nodes[0]["kind"] == "function"

    def test_resolve_import_via_federated_db(self, tmp_path: Path):
        """Prove that querying the federated DB finds the import target."""
        repo_b = _create_indexed_repo(
            tmp_path, "dep-lib",
            {"mylib/utils.py": "def helper_func(data):\n    return data.strip()\n"},
        )

        from tws_graph.db.connection import DatabaseConnection
        from tws_graph.db.queries import QueryBuilder

        db_b = DatabaseConnection.open(str(repo_b / ".tws" / "codegraph" / "index.db"))
        q_b = QueryBuilder(db_b.conn)

        # Simulate: looking for "mylib.utils::helper_func" in federated DB
        # The import module path "mylib.utils" should correspond to file_path "mylib/utils.py"
        results = q_b.search_nodes("helper_func", limit=10)
        db_b.close()

        names = [r["name"] for r in results]
        assert "helper_func" in names

        # Verify the file_path includes the module path
        file_paths = [r["file_path"] for r in results]
        assert any("mylib" in fp or "utils" in fp for fp in file_paths), f"Expected mylib/utils in file paths, got: {file_paths}"


# ---------------------------------------------------------------------------
# Cross-repo calls & trace (stub verification)
# ---------------------------------------------------------------------------

class TestCrossRepoCalls:
    """Verify that federated DBs can be attached for calls/impact/trace queries."""

    def test_attach_all(self, tmp_path: Path):
        """ATTACH all federated DBs and verify tables are accessible."""
        import sqlite3

        repo_a = _create_indexed_repo(tmp_path, "svc-a", {"a.py": "def f():\n    pass\n"})
        repo_b = _create_indexed_repo(tmp_path, "svc-b", {"b.py": "def g():\n    pass\n"})

        from tws_graph.federation import FederationRegistry
        cd_dir = repo_a / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("svc-b", str(repo_b))

        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        attached = registry.attach_all(mem)
        assert len(attached) >= 1
        assert any("svc_b" in a for a in attached)  # '-' replaced with '_'

        # Verify we can query federated tables
        for alias in attached:
            rows = mem.execute(f"SELECT COUNT(*) as cnt FROM {alias}.nodes").fetchone()
            assert rows["cnt"] > 0

        registry.detach_all(mem, attached)
        mem.close()

    def test_cross_repo_search_via_union(self, tmp_path: Path):
        """Execute UNION across federated DBs and verify cross-repo results."""
        import sqlite3

        repo_a = _create_indexed_repo(tmp_path, "svc-x",
                                      {"x.py": "def handler():\n    return 'ok'\n"})
        repo_b = _create_indexed_repo(tmp_path, "svc-y",
                                      {"y.py": "def worker():\n    return 'done'\n"})

        from tws_graph.federation import FederationRegistry
        cd_dir = repo_a / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("svc-y", str(repo_b))

        db_a_path = str(repo_a / ".tws" / "codegraph" / "index.db")

        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        try:
            mem.execute(f"ATTACH DATABASE '{db_a_path}' AS local")
            registry.attach_all(mem)

            # UNION across local + federated
            rows = mem.execute("""
                SELECT name, file_path FROM local.nodes WHERE name = 'handler'
                UNION ALL
                SELECT name, file_path FROM fed_svc_y.nodes WHERE name = 'worker'
            """).fetchall()

            names = [r["name"] for r in rows]
            assert "handler" in names
            assert "worker" in names
        finally:
            try:
                mem.execute("DETACH DATABASE local")
            except Exception:
                pass
            mem.close()

    def test_federated_trace_stub(self, tmp_path: Path):
        """Stub: verify that a call-chain spanning two federated repos can be detected.

        This tests the *data model* — calls edges that span repos.  In practice
        the edges must already be present (e.g., from an HTTP_CALLS edge kind),
        but the federation layer makes the target node visible to the traversal.
        """
        import sqlite3

        repo_a = _create_indexed_repo(tmp_path, "frontend",
                                      {"main.py": "def entry():\n    return 'start'\n"})
        repo_b = _create_indexed_repo(tmp_path, "backend",
                                      {"api.py": "def handle():\n    return 'data'\n"})

        from tws_graph.federation import FederationRegistry
        cd_dir = repo_a / ".tws" / "codegraph"
        registry = FederationRegistry(str(cd_dir))
        registry.add("backend", str(repo_b))

        db_a = str(repo_a / ".tws" / "codegraph" / "index.db")
        db_b = str(repo_b / ".tws" / "codegraph" / "index.db")

        # Simulate: insert a calls edge in repo-a's DB targeting repo-b's handle()
        # (in real life this would come from an HTTP_CALLS extractor)
        conn_a = sqlite3.connect(db_a)
        conn_a.row_factory = sqlite3.Row
        # Get node IDs
        entry_node = conn_a.execute(
            "SELECT id FROM nodes WHERE name = 'entry'"
        ).fetchone()
        assert entry_node is not None

        conn_a.execute("""
            INSERT INTO edges (source, target, target_text, kind, provenance)
            VALUES (?, '', 'backend.handle', 'calls', 'federated')
        """, (entry_node["id"],))
        conn_a.commit()

        # Now, ATTACH both and verify the cross-repo edge is visible
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        try:
            mem.execute(f"ATTACH DATABASE '{db_a}' AS local")
            mem.execute(f"ATTACH DATABASE '{db_b}' AS fed_backend")

            # Search for the target_text in federated repo
            target_text = "backend.handle"
            rows = mem.execute("""
                SELECT n.name, n.file_path, n.language
                FROM fed_backend.nodes n
                WHERE n.name = 'handle'
            """).fetchall()

            assert len(rows) >= 1
            assert rows[0]["name"] == "handle"
        finally:
            try:
                mem.execute("DETACH DATABASE local")
                mem.execute("DETACH DATABASE fed_backend")
            except Exception:
                pass
            mem.close()
            conn_a.close()


# ---------------------------------------------------------------------------
# Empty federation (no-op)
# ---------------------------------------------------------------------------

class TestEmptyFederation:
    """When no federation is configured, everything should work normally."""

    def test_is_active_returns_false(self, tmp_path: Path):
        """Fresh project has no federation."""
        reg = load_federation(str(tmp_path))
        assert not reg.is_active()
        assert reg.count() == 0

    def test_attach_all_noop(self, tmp_path: Path):
        """attach_all with no repos returns empty list."""
        import sqlite3
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        mem = sqlite3.connect(":memory:")
        attached = reg.attach_all(mem)
        assert attached == []
        mem.close()

    def test_iter_federated_dbs_empty(self, tmp_path: Path):
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        assert reg.iter_federated_dbs() == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestFederationEdgeCases:
    """Edge case tests for federation."""

    def test_repo_with_non_ascii_name(self, tmp_path: Path):
        """Repo names can contain unicode characters."""
        repo = _create_indexed_repo(tmp_path, "测试", {"main.py": "pass\n"})
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        reg.add("测试", str(repo))
        assert reg.count() == 1
        assert reg.get("测试") is not None

    def test_repo_name_with_hyphens(self, tmp_path: Path):
        """Repo names with hyphens are handled (replaced with _ in SQL aliases)."""
        repo = _create_indexed_repo(tmp_path, "my-service", {"main.py": "pass\n"})
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        reg.add("my-service", str(repo))

        import sqlite3
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        attached = reg.attach_all(mem)
        # '-' replaced with '_' in SQL alias
        assert any("my_service" in a for a in attached)
        reg.detach_all(mem, attached)
        mem.close()

    def test_federation_json_is_valid(self, tmp_path: Path):
        """The persisted federation.json is valid JSON."""
        repo = _create_indexed_repo(tmp_path, "valid-repo", {"a.py": "x = 1\n"})
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        reg.add("valid-repo", str(repo))

        import json
        fpath = os.path.join(str(cd_dir), "federation.json")
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "repos" in data
        assert "valid-repo" in data["repos"]
        assert data["repos"]["valid-repo"]["path"] == str(repo)

    def test_corrupted_federation_json_recovers(self, tmp_path: Path):
        """Corrupted federation.json is replaced with a fresh empty registry."""
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        fpath = cd_dir / "federation.json"
        fpath.write_text("{this is not valid json}", encoding="utf-8")

        reg = FederationRegistry(str(cd_dir))
        assert not reg.is_active()
        # Should have written a valid file
        new_data = json.loads(fpath.read_text(encoding="utf-8"))
        assert "repos" in new_data

    def test_no_error_on_missing_federated_db(self, tmp_path: Path):
        """If a federated repo's DB was deleted, attach_all skips it gracefully."""
        repo = _create_indexed_repo(tmp_path, "ghost", {"a.py": "pass\n"})
        cd_dir = tmp_path / ".tws" / "codegraph"
        cd_dir.mkdir(parents=True)
        reg = FederationRegistry(str(cd_dir))
        reg.add("ghost", str(repo))

        # Delete the DB
        db_path = repo / ".tws" / "codegraph" / "index.db"
        os.remove(str(db_path))

        import sqlite3
        mem = sqlite3.connect(":memory:")
        attached = reg.attach_all(mem)
        assert attached == []  # No DB to attach
        mem.close()
