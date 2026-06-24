"""Tests for store.migrations — versioned database migration system."""

import sqlite3
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# MigrationRunner — basic lifecycle
# ---------------------------------------------------------------------------

class TestMigrationRunnerInit:
    """Tests for MigrationRunner initialisation."""

    def test_init_with_connection(self):
        from tws_graph.store.migrations import MigrationRunner
        conn = create_test_db()
        runner = MigrationRunner(conn)
        assert runner.conn is conn

    def test_get_current_version_empty_db(self):
        """On an empty DB with no schema_versions table, version is 0."""
        from tws_graph.store.migrations import MigrationRunner
        conn = create_test_db()
        runner = MigrationRunner(conn)
        assert runner._get_current_version() == 0

    def test_get_current_version_with_data(self):
        """When schema_versions has records, returns the max version."""
        from tws_graph.store.migrations import MigrationRunner
        conn = create_test_db()
        conn.execute("CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at INTEGER, description TEXT)")
        conn.execute("INSERT INTO schema_versions VALUES (1, 1000, 'v1')")
        conn.execute("INSERT INTO schema_versions VALUES (3, 3000, 'v3')")
        runner = MigrationRunner(conn)
        assert runner._get_current_version() == 3


# ---------------------------------------------------------------------------
# migrate() — execution
# ---------------------------------------------------------------------------

class TestMigrationRunnerMigrate:
    """Tests for the migrate() method."""

    def test_migrate_runs_all_when_empty(self, tmp_path):
        """When DB is empty, migrate() applies all available migrations."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        count = runner.migrate()
        assert count == 8

        # Verify schema_versions has 8 rows
        rows = conn.execute("SELECT version, description FROM schema_versions ORDER BY version").fetchall()
        assert len(rows) == 8
        assert rows[0][0] == 1
        assert rows[7][0] == 8

        conn.close()

    def test_migrate_is_idempotent(self, tmp_path):
        """Running migrate() twice applies nothing the second time."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        count1 = runner.migrate()
        assert count1 == 8

        count2 = runner.migrate()
        assert count2 == 0

        conn.close()

    def test_migrate_resumes_from_partial(self, tmp_path, monkeypatch):
        """When some migrations are already applied, only pending ones run."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # Manually apply v1 through v3
        from tws_graph.store.migrations.v001_initial import UP_SQL as SQL1
        from tws_graph.store.migrations.v002_is_external import UP_SQL as SQL2
        from tws_graph.store.migrations.v003_json_properties import UP_SQL as SQL3
        conn.executescript(SQL1)
        conn.executescript(SQL2)
        conn.executescript(SQL3)
        now = 1000
        conn.execute(
            "INSERT INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
            (1, now, "v1")
        )
        conn.execute(
            "INSERT INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
            (2, now + 1, "v2")
        )
        conn.execute(
            "INSERT INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
            (3, now + 2, "v3")
        )

        runner = MigrationRunner(conn)
        count = runner.migrate()
        # v4-v8 should be applied
        assert count == 5

        rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
        versions = [r[0] for r in rows]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8]

        conn.close()

    def test_migrate_creates_initial_schema(self, tmp_path):
        """After v1 migration, nodes/edges/files/schema_versions tables exist."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        # Verify core tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in tables]

        assert "nodes" in table_names
        assert "edges" in table_names
        assert "files" in table_names
        assert "schema_versions" in table_names
        assert "unresolved_refs" in table_names

        conn.close()

    def test_migrate_v2_adds_is_external(self, tmp_path):
        """After v2, unresolved_refs has is_external column."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        # Check is_external column exists in unresolved_refs
        cols = conn.execute("PRAGMA table_info('unresolved_refs')").fetchall()
        col_names = [c[1] for c in cols]
        assert "is_external" in col_names

        conn.close()

    def test_migrate_v3_adds_json_properties(self, tmp_path):
        """After v3, nodes and edges have properties columns."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        for table in ["nodes", "edges"]:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            col_names = [c[1] for c in cols]
            assert "properties" in col_names

        conn.close()

    def test_migrate_v4_creates_lsp_defs(self, tmp_path):
        """After v4, lsp_defs table exists with correct schema."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        cols = conn.execute("PRAGMA table_info('lsp_defs')").fetchall()
        col_names = [c[1] for c in cols]
        assert "module_path" in col_names
        assert "qualified_name" in col_names
        assert "simple_name" in col_names
        assert "node_id" in col_names
        assert "kind" in col_names
        assert "parent_scope" in col_names

        # Check indexes
        idxs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_lsp_defs%'"
        ).fetchall()
        idx_names = [r[0] for r in idxs]
        assert "idx_lsp_defs_module" in idx_names
        assert "idx_lsp_defs_simple_name" in idx_names

        conn.close()

    def test_migrate_v5_creates_community_assignments(self, tmp_path):
        """After v5, community_assignments table exists."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        cols = conn.execute("PRAGMA table_info('community_assignments')").fetchall()
        col_names = [c[1] for c in cols]
        assert "node_id" in col_names
        assert "community_id" in col_names
        assert "modularity" in col_names
        assert "computed_at" in col_names
        assert "algorithm" in col_names

        # Check indexes
        idxs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_community%'"
        ).fetchall()
        idx_names = [r[0] for r in idxs]
        assert "idx_community_nodes" in idx_names
        assert "idx_community_id" in idx_names

        conn.close()

    def test_migrate_v6_adds_checksum(self, tmp_path):
        """After v6, schema_versions has a checksum column."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        cols = conn.execute("PRAGMA table_info('schema_versions')").fetchall()
        col_names = [c[1] for c in cols]
        assert "checksum" in col_names

        conn.close()

    def test_migrate_v7_adds_source_column(self, tmp_path):
        """After v7, unresolved_refs has a source column."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        cols = conn.execute("PRAGMA table_info('unresolved_refs')").fetchall()
        col_names = [c[1] for c in cols]
        assert "source" in col_names

        conn.close()


# ---------------------------------------------------------------------------
# Transaction / rollback behaviour
# ---------------------------------------------------------------------------

class TestMigrationRunnerRollback:
    """Tests for rollback on failed migrations."""

    def test_failed_migration_rolls_back(self, tmp_path):
        """When a migration fails, that migration's changes are rolled back."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # Manually set up v1-v3 so only v4 is pending
        from tws_graph.store.migrations.v001_initial import UP_SQL as SQL1
        from tws_graph.store.migrations.v002_is_external import UP_SQL as SQL2
        from tws_graph.store.migrations.v003_json_properties import UP_SQL as SQL3
        conn.executescript(SQL1)
        conn.executescript(SQL2)
        conn.executescript(SQL3)
        now = 1000
        conn.execute(
            "INSERT INTO schema_versions (version, applied_at, description) VALUES (?,?,?), (?,?,?), (?,?,?)",
            (1, now, "v1", 2, now, "v2", 3, now, "v3"),
        )

        # Inject a broken migration module by monkeypatching _find_pending
        # to return a fake migration with bad SQL
        runner = MigrationRunner(conn)
        original_find_pending = runner._find_pending

        class FakeMigration:
            VERSION = 99
            DESCRIPTION = "bad migration"
            UP_SQL = "THIS IS NOT VALID SQL"

        def fake_find_pending(current):
            return [(99, "fake_bad_migration", FakeMigration())]

        runner._find_pending = fake_find_pending

        with pytest.raises(Exception):
            runner.migrate()

        # schema_versions should still have only v1-v3
        rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
        versions = [r[0] for r in rows]
        assert versions == [1, 2, 3]

        conn.close()

    def test_failed_migration_does_not_affect_previous(self, tmp_path):
        """Previous successful migrations remain applied after a later failure."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # Set up v1 only
        from tws_graph.store.migrations.v001_initial import UP_SQL as SQL1
        conn.executescript(SQL1)
        conn.execute(
            "INSERT INTO schema_versions (version, applied_at, description) VALUES (?, ?, ?)",
            (1, 1000, "v1")
        )

        runner = MigrationRunner(conn)
        original_find_pending = runner._find_pending

        # Make v2 broken, v3 should not execute either (ordered execution)
        class FakeV2:
            VERSION = 2
            DESCRIPTION = "broken"
            UP_SQL = "INVALID SQL SYNTAX ERROR"

        class FakeV3:
            VERSION = 3
            DESCRIPTION = "would be ok"
            UP_SQL = "SELECT 1"

        def fake_find_pending(current):
            return [
                (2, "fake_broken", FakeV2()),
                (3, "fake_ok", FakeV3()),
            ]

        runner._find_pending = fake_find_pending

        with pytest.raises(Exception):
            runner.migrate()

        # v1 still applied, v2 not
        rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
        versions = [r[0] for r in rows]
        assert versions == [1]

        conn.close()


# ---------------------------------------------------------------------------
# Migration module structure
# ---------------------------------------------------------------------------

class TestMigrationModules:
    """Verify each migration module has the required attributes."""

    # List of (module_name, expected_version, must_have_down_sql)
    MIGRATION_MODULES = [
        ("v001_initial", 1),
        ("v002_is_external", 2),
        ("v003_json_properties", 3),
        ("v004_lsp_defs", 4),
        ("v005_community", 5),
        ("v006_schema_enhance", 6),
        ("v007_source_column", 7),
    ]

    @pytest.mark.parametrize("module_name,expected_version", MIGRATION_MODULES)
    def test_module_has_required_attributes(self, module_name, expected_version):
        """Each migration module must export VERSION, DESCRIPTION, UP_SQL."""
        import importlib
        mod = importlib.import_module(f"tws_graph.store.migrations.{module_name}")

        assert hasattr(mod, "VERSION"), f"{module_name} missing VERSION"
        assert mod.VERSION == expected_version, \
            f"{module_name}: expected VERSION={expected_version}, got {mod.VERSION}"
        assert hasattr(mod, "DESCRIPTION"), f"{module_name} missing DESCRIPTION"
        assert isinstance(mod.DESCRIPTION, str) and len(mod.DESCRIPTION) > 0, \
            f"{module_name}: DESCRIPTION must be non-empty string"
        assert hasattr(mod, "UP_SQL"), f"{module_name} missing UP_SQL"
        assert isinstance(mod.UP_SQL, str) and len(mod.UP_SQL) > 0, \
            f"{module_name}: UP_SQL must be non-empty string"
        assert hasattr(mod, "DOWN_SQL"), f"{module_name} missing DOWN_SQL"

    @pytest.mark.parametrize("module_name,expected_version", MIGRATION_MODULES)
    def test_up_sql_is_executable(self, module_name, expected_version):
        """Each migration's UP_SQL executes without error on an appropriate DB."""
        import importlib
        import importlib.util as iu

        # For initial migration, use empty DB
        # For later migrations, prep the DB to the prior state
        conn = create_test_db()

        # Execute all previous migrations first
        prev_modules = [
            m for m, v in self.MIGRATION_MODULES if v < expected_version
        ]
        for prev_name in prev_modules:
            prev_mod = importlib.import_module(
                f"tws_graph.store.migrations.{prev_name}"
            )
            conn.executescript(prev_mod.UP_SQL)

        # Execute the target migration
        mod = importlib.import_module(
            f"tws_graph.store.migrations.{module_name}"
        )
        conn.executescript(mod.UP_SQL)
        conn.close()


# ---------------------------------------------------------------------------
# _find_pending — ordering
# ---------------------------------------------------------------------------

class TestMigrationRunnerFindPending:
    """Tests for _find_pending ordering and discovery."""

    def test_find_pending_returns_sorted_list(self):
        """Pending migrations are sorted by VERSION ascending."""
        from tws_graph.store.migrations import MigrationRunner
        conn = create_test_db()

        # Mimic v1-v2 already applied
        conn.execute(
            "CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at INTEGER, description TEXT)"
        )
        conn.execute("INSERT INTO schema_versions VALUES (1, 1000, 'v1')")
        conn.execute("INSERT INTO schema_versions VALUES (2, 2000, 'v2')")

        runner = MigrationRunner(conn)
        pending = runner._find_pending(current=2)
        versions = [v for v, _n, _m in pending]
        assert versions == sorted(versions), f"Expected sorted, got {versions}"
        # All versions should be > 2
        assert all(v > 2 for v in versions)

        conn.close()

    def test_find_pending_on_new_db_returns_all(self):
        """On a new empty DB, all migrations are pending."""
        from tws_graph.store.migrations import MigrationRunner
        conn = create_test_db()
        runner = MigrationRunner(conn)
        pending = runner._find_pending(current=0)
        versions = [v for v, _n, _m in pending]
        assert versions == sorted(versions)
        assert len(versions) == 8
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8]
        conn.close()


# ---------------------------------------------------------------------------
# DB schema integrity after full migration
# ---------------------------------------------------------------------------

class TestFullMigrationSchemaIntegrity:
    """End-to-end checks after applying all migrations."""

    def test_all_tables_have_expected_schema(self, tmp_path):
        """After full migration, all tables match the v7 design."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        # Verify all expected tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [r[0] for r in tables]
        expected = [
            "community_assignments",
            "edges",
            "files",
            "lsp_defs",
            "nodes",
            "nodes_fts",
            "schema_versions",
            "unresolved_refs",
        ]
        for t in expected:
            assert t in table_names, f"Table {t} not found"

        # Verify nodes has properties
        node_cols = [c[1] for c in conn.execute("PRAGMA table_info('nodes')").fetchall()]
        assert "properties" in node_cols

        # Verify edges has properties
        edge_cols = [c[1] for c in conn.execute("PRAGMA table_info('edges')").fetchall()]
        assert "properties" in edge_cols

        # Verify unresolved_refs has is_external, source
        uref_cols = [c[1] for c in conn.execute("PRAGMA table_info('unresolved_refs')").fetchall()]
        assert "is_external" in uref_cols
        assert "source" in uref_cols

        # Verify schema_versions has checksum
        sv_cols = [c[1] for c in conn.execute("PRAGMA table_info('schema_versions')").fetchall()]
        assert "checksum" in sv_cols

        conn.close()

    def test_can_insert_data_after_migration(self, tmp_path):
        """After full migration, basic CRUD operations work."""
        from tws_graph.store.migrations import MigrationRunner
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        runner = MigrationRunner(conn)
        runner.migrate()

        # Insert a node
        conn.execute(
            """INSERT INTO nodes (id, kind, name, qualified_name, file_path,
               language, start_line, end_line, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test_id", "function", "foo", "mod.foo", "mod.py",
             "python", 1, 5, 1000),
        )

        # Insert an edge
        conn.execute(
            """INSERT INTO edges (source, target, kind)
               VALUES (?, ?, ?)""",
            ("test_id", "other_id", "calls"),
        )

        # Insert unresolved_ref with source
        conn.execute(
            """INSERT INTO unresolved_refs (from_node_id, reference_name,
               reference_kind, line, col, candidates, source, file_path,
               language, is_external)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test_id", "bar", "call", 3, 10, '["c1","c2"]',
             "tree-sitter", "mod.py", "python", 0),
        )

        conn.close()
