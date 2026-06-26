//! Schema definitions and 8 migrations for the code-graph database.
//!
//! Migration history is tracked in the `schema_versions` meta-table.
//! When creating a new database, the initial schema (v001) is executed first,
//! then v002–v008 are applied in order. Each migration runs inside a SAVEPOINT
//! for atomic application.
//!
//! # Safe column addition
//!
//! Several migrations use `ALTER TABLE ADD COLUMN`. Because the initial
//! schema already includes the latest columns (body_hash, properties, body)
//! to match `schema.sql`, those ALTERs are wrapped in `add_column_if_not_exists`
//! so they are no-ops on freshly-created databases while still working on
//! databases created by older versions.
//!
//! Only one migration table is used: ``schema_versions``, which mirrors the
//! Python ``schema_versions`` table from ``v001_initial.py``.

use rusqlite::{params, Connection, Result};

// ---------------------------------------------------------------------------
// Migration trait
// ---------------------------------------------------------------------------

/// A single schema migration.
pub trait Migration {
    /// Numeric version (1-based, matches Python migration module name).
    fn version(&self) -> i64;

    /// Human-readable description.
    fn description(&self) -> &'static str;

    /// Apply this migration.
    ///
    /// Called inside a SAVEPOINT by `MigrationRunner`.
    fn up(&self, conn: &Connection) -> Result<()>;

    /// Optional rollback (default: no-op).
    fn down(&self, _conn: &Connection) -> Result<()> {
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helper: add a column only if it does not already exist
// ---------------------------------------------------------------------------

/// Safely add a column to *table*.
///
/// Queries `pragma_table_info` first; if the column already exists the
/// ALTER is skipped entirely.  This allows migrations to be idempotent
/// when the initial schema already includes the column.
fn add_column_if_not_exists(
    conn: &Connection,
    table: &str,
    column: &str,
    col_def: &str,
) -> Result<()> {
    let exists: bool = conn
        .query_row(
            "SELECT COUNT(*) FROM pragma_table_info(?1) WHERE name = ?2",
            params![table, column],
            |row| row.get::<_, i64>(0),
        )
        .map(|c| c > 0)?;

    if !exists {
        conn.execute_batch(&format!(
            "ALTER TABLE {} ADD COLUMN {} {}",
            table, column, col_def
        ))?;
    }
    Ok(())
}

/// Batch-execute SQL with error context.
fn exec_batch(conn: &Connection, sql: &str) -> Result<()> {
    conn.execute_batch(sql)
}

// ---------------------------------------------------------------------------
// v001 — initial schema (matches schema.sql)
// ---------------------------------------------------------------------------

struct V001Initial;

impl Migration for V001Initial {
    fn version(&self) -> i64 {
        1
    }
    fn description(&self) -> &'static str {
        "Initial schema: nodes, edges, files, unresolved_refs, FTS5, indexes"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        exec_batch(
            conn,
            "\
-- =============================================================================
-- Schema version tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL,
    description TEXT
);

-- =============================================================================
-- Nodes: every code symbol
-- =============================================================================
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    signature       TEXT,
    docstring       TEXT,
    visibility      TEXT,
    is_abstract     INTEGER DEFAULT 0,
    is_exported     INTEGER DEFAULT 0,
    decorators      TEXT,
    framework       TEXT,
    properties      TEXT DEFAULT '{}',
    body            TEXT,
    body_hash       TEXT,
    updated_at      INTEGER NOT NULL
);

-- =============================================================================
-- Edges: relationships between symbols
-- =============================================================================
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target      TEXT NOT NULL,
    target_text TEXT,
    kind        TEXT NOT NULL,
    source_loc  TEXT,
    provenance  TEXT DEFAULT 'tree-sitter',
    properties  TEXT DEFAULT '{}'
);

-- =============================================================================
-- Files: tracked source files
-- =============================================================================
CREATE TABLE IF NOT EXISTS files (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    language     TEXT NOT NULL,
    node_count   INTEGER DEFAULT 0,
    indexed_at   INTEGER NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,
    modified_at  INTEGER NOT NULL DEFAULT 0
);

-- =============================================================================
-- Unresolved references
-- =============================================================================
CREATE TABLE IF NOT EXISTS unresolved_refs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    reference_name  TEXT NOT NULL,
    reference_kind  TEXT NOT NULL DEFAULT 'call',
    line            INTEGER NOT NULL DEFAULT 0,
    col             INTEGER NOT NULL DEFAULT 0,
    candidates      TEXT,
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL,
    is_external     INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'tree-sitter'
);

-- =============================================================================
-- Indexes
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_framework ON nodes(framework);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source, target);
CREATE INDEX IF NOT EXISTS idx_edges_kind_provenance ON edges(kind, provenance);
CREATE INDEX IF NOT EXISTS idx_files_size_mtime ON files(size, modified_at);

CREATE INDEX IF NOT EXISTS idx_urefs_from_node ON unresolved_refs(from_node_id);
CREATE INDEX IF NOT EXISTS idx_urefs_name ON unresolved_refs(reference_name);
CREATE INDEX IF NOT EXISTS idx_urefs_file ON unresolved_refs(file_path);
CREATE INDEX IF NOT EXISTS idx_urefs_lang ON unresolved_refs(language);
CREATE INDEX IF NOT EXISTS idx_urefs_is_external ON unresolved_refs(is_external);

-- =============================================================================
-- FTS5: full-text search
-- =============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;
",
        )
    }
}

// ---------------------------------------------------------------------------
// v002 — add is_external column to unresolved_refs
// ---------------------------------------------------------------------------

struct V002IsExternal;

impl Migration for V002IsExternal {
    fn version(&self) -> i64 {
        2
    }
    fn description(&self) -> &'static str {
        "Add is_external column to unresolved_refs"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        add_column_if_not_exists(
            conn,
            "unresolved_refs",
            "is_external",
            "INTEGER NOT NULL DEFAULT 0",
        )?;
        conn.execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_urefs_is_external ON unresolved_refs(is_external);",
        )?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// v003 — add JSON properties columns to nodes and edges
// ---------------------------------------------------------------------------

struct V003JsonProperties;

impl Migration for V003JsonProperties {
    fn version(&self) -> i64 {
        3
    }
    fn description(&self) -> &'static str {
        "Add JSON properties columns to nodes and edges"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        add_column_if_not_exists(conn, "nodes", "properties", "TEXT DEFAULT '{}'")?;
        add_column_if_not_exists(conn, "edges", "properties", "TEXT DEFAULT '{}'")?;

        conn.execute_batch(
            "\
            CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
            CREATE INDEX IF NOT EXISTS idx_nodes_framework ON nodes(framework);
            CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source, target);
            CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
            CREATE INDEX IF NOT EXISTS idx_files_size_mtime ON files(size, modified_at);
            ",
        )?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// v004 — create lsp_defs table for per-module definition index
// ---------------------------------------------------------------------------

struct V004LspDefs;

impl Migration for V004LspDefs {
    fn version(&self) -> i64 {
        4
    }
    fn description(&self) -> &'static str {
        "Create lsp_defs table for per-module definition index"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        exec_batch(
            conn,
            "\
            CREATE TABLE IF NOT EXISTS lsp_defs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                module_path     TEXT NOT NULL,
                qualified_name  TEXT NOT NULL,
                simple_name     TEXT NOT NULL,
                node_id         TEXT NOT NULL,
                kind            TEXT NOT NULL,
                parent_scope    TEXT,
                UNIQUE(module_path, qualified_name)
            );

            CREATE INDEX IF NOT EXISTS idx_lsp_defs_module ON lsp_defs(module_path);
            CREATE INDEX IF NOT EXISTS idx_lsp_defs_simple_name ON lsp_defs(simple_name);
            CREATE INDEX IF NOT EXISTS idx_lsp_defs_qualified ON lsp_defs(qualified_name);
            CREATE INDEX IF NOT EXISTS idx_lsp_defs_node ON lsp_defs(node_id);
            ",
        )
    }
}

// ---------------------------------------------------------------------------
// v005 — create community_assignments table
// ---------------------------------------------------------------------------

struct V005Community;

impl Migration for V005Community {
    fn version(&self) -> i64 {
        5
    }
    fn description(&self) -> &'static str {
        "Create community_assignments table for community detection results"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        exec_batch(
            conn,
            "\
            CREATE TABLE IF NOT EXISTS community_assignments (
                node_id         TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                community_id    INTEGER NOT NULL,
                modularity      REAL,
                computed_at     INTEGER NOT NULL,
                algorithm       TEXT NOT NULL DEFAULT 'louvain',
                PRIMARY KEY (node_id, community_id)
            );

            CREATE INDEX IF NOT EXISTS idx_community_nodes ON community_assignments(node_id);
            CREATE INDEX IF NOT EXISTS idx_community_id ON community_assignments(community_id);
            ",
        )
    }
}

// ---------------------------------------------------------------------------
// v006 — enhance schema_versions with checksum
// ---------------------------------------------------------------------------

struct V006SchemaEnhance;

impl Migration for V006SchemaEnhance {
    fn version(&self) -> i64 {
        6
    }
    fn description(&self) -> &'static str {
        "Enhance schema_versions with checksum column and enrich descriptions"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        add_column_if_not_exists(conn, "schema_versions", "checksum", "TEXT")?;

        conn.execute_batch(
            "\
            UPDATE schema_versions
            SET description = 'Initial schema v1'
            WHERE version = 1 AND (description IS NULL OR description = '');

            UPDATE schema_versions
            SET description = 'Add is_external to unresolved_refs'
            WHERE version = 2 AND (description IS NULL OR description = '');
            ",
        )?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// v007 — add source column to unresolved_refs
// ---------------------------------------------------------------------------

struct V007SourceColumn;

impl Migration for V007SourceColumn {
    fn version(&self) -> i64 {
        7
    }
    fn description(&self) -> &'static str {
        "Add source column to unresolved_refs for provenance tracking"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        add_column_if_not_exists(
            conn,
            "unresolved_refs",
            "source",
            "TEXT NOT NULL DEFAULT 'tree-sitter'",
        )
    }
}

// ---------------------------------------------------------------------------
// v008 — add body column to nodes
// ---------------------------------------------------------------------------

struct V008FunctionBody;

impl Migration for V008FunctionBody {
    fn version(&self) -> i64 {
        8
    }
    fn description(&self) -> &'static str {
        "Add body column to nodes for function body text storage"
    }
    fn up(&self, conn: &Connection) -> Result<()> {
        add_column_if_not_exists(conn, "nodes", "body", "TEXT")
    }
}

// ---------------------------------------------------------------------------
// MigrationRunner
// ---------------------------------------------------------------------------

/// Manages schema version tracking and migration application.
pub struct MigrationRunner {
    migrations: Vec<Box<dyn Migration>>,
}

impl MigrationRunner {
    /// Create a new runner with all 8 registered migrations (v001–v008).
    pub fn new() -> Self {
        let migrations: Vec<Box<dyn Migration>> = vec![
            Box::new(V001Initial),
            Box::new(V002IsExternal),
            Box::new(V003JsonProperties),
            Box::new(V004LspDefs),
            Box::new(V005Community),
            Box::new(V006SchemaEnhance),
            Box::new(V007SourceColumn),
            Box::new(V008FunctionBody),
        ];
        Self { migrations }
    }

    /// Get the highest migration version.
    pub fn latest_version(&self) -> i64 {
        self.migrations
            .last()
            .map(|m| m.version())
            .unwrap_or(0)
    }

    /// Return the current schema version from the database (0 if none).
    pub fn current_version(conn: &Connection) -> Result<i64> {
        // Check if schema_versions table exists
        let table_exists: bool = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_versions'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map(|c| c > 0)?;

        if !table_exists {
            return Ok(0);
        }

        conn.query_row(
            "SELECT COALESCE(MAX(version), 0) FROM schema_versions",
            [],
            |row| row.get(0),
        )
    }

    /// Apply all pending migrations.
    ///
    /// Each migration runs inside a SAVEPOINT. If a migration fails,
    /// the SAVEPOINT is rolled back and the error is propagated.
    pub fn apply(&self, conn: &Connection) -> Result<()> {
        let current = Self::current_version(conn)?;

        for migration in &self.migrations {
            let version = migration.version();
            if version <= current {
                continue;
            }

            let sp_name = format!("_migrate_v{:03}", version);

            // Wrap in SAVEPOINT for atomicity
            conn.execute_batch(&format!("SAVEPOINT {};", sp_name))?;

            match migration.up(conn) {
                Ok(()) => {
                    conn.execute(
                        "INSERT INTO schema_versions (version, applied_at, description) \
                         VALUES (?1, CAST(strftime('%s', 'now') AS INTEGER) * 1000, ?2)",
                        params![version, migration.description()],
                    )?;
                    conn.execute_batch(&format!("RELEASE {};", sp_name))?;
                }
                Err(e) => {
                    let _ = conn.execute_batch(&format!("ROLLBACK TO {};", sp_name));
                    let _ = conn.execute_batch(&format!("RELEASE {};", sp_name));
                    return Err(e);
                }
            }
        }

        Ok(())
    }

    /// Initialize a brand-new database with the initial schema plus all
    /// subsequent migrations applied in order.
    ///
    /// This is equivalent to running `apply()` on an empty database,
    /// since v001 creates all tables with the latest columns and v002–v008
    /// use `add_column_if_not_exists` for idempotency.
    pub fn initialize(&self, conn: &Connection) -> Result<()> {
        self.apply(conn)
    }
}

impl Default for MigrationRunner {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    fn in_memory_connection() -> Connection {
        Connection::open_in_memory().unwrap()
    }

    #[test]
    fn test_migration_runner_apply_all() {
        let conn = in_memory_connection();
        let runner = MigrationRunner::new();

        runner.apply(&conn).unwrap();

        let version = MigrationRunner::current_version(&conn).unwrap();
        assert_eq!(version, 8);

        // Verify nodes table has all columns (including those added by
        // v003 properties, v008 body, and the initial schema's body_hash).
        let columns: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM pragma_table_info('nodes') ORDER BY cid")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };

        assert!(columns.contains(&"id".to_string()));
        assert!(columns.contains(&"kind".to_string()));
        assert!(columns.contains(&"name".to_string()));
        assert!(columns.contains(&"qualified_name".to_string()));
        assert!(columns.contains(&"file_path".to_string()));
        assert!(columns.contains(&"language".to_string()));
        assert!(columns.contains(&"start_line".to_string()));
        assert!(columns.contains(&"end_line".to_string()));
        assert!(columns.contains(&"signature".to_string()));
        assert!(columns.contains(&"docstring".to_string()));
        assert!(columns.contains(&"visibility".to_string()));
        assert!(columns.contains(&"is_abstract".to_string()));
        assert!(columns.contains(&"is_exported".to_string()));
        assert!(columns.contains(&"decorators".to_string()));
        assert!(columns.contains(&"framework".to_string()));
        assert!(columns.contains(&"properties".to_string()));
        assert!(columns.contains(&"body".to_string()));
        assert!(columns.contains(&"body_hash".to_string()));
        assert!(columns.contains(&"updated_at".to_string()));

        // Verify edges table has properties column
        let edge_cols: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM pragma_table_info('edges') ORDER BY cid")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        assert!(edge_cols.contains(&"properties".to_string()));

        // Verify unresolved_refs has is_external and source
        let uref_cols: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM pragma_table_info('unresolved_refs') ORDER BY cid")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        assert!(uref_cols.contains(&"is_external".to_string()));
        assert!(uref_cols.contains(&"source".to_string()));

        // Verify extra tables exist
        let tables: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        assert!(tables.contains(&"lsp_defs".to_string()));
        assert!(tables.contains(&"community_assignments".to_string()));

        // Verify FTS5 virtual table exists
        assert!(tables.contains(&"nodes_fts".to_string()));

        // Verify schema_versions has checksum column (v006)
        let sv_cols: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM pragma_table_info('schema_versions') ORDER BY cid")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        assert!(sv_cols.contains(&"checksum".to_string()));

        // Verify schema_versions rows
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM schema_versions", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 8);
    }

    #[test]
    fn test_migration_runner_idempotent() {
        let conn = in_memory_connection();
        let runner = MigrationRunner::new();

        // Apply twice — second call should be a no-op
        runner.apply(&conn).unwrap();
        runner.apply(&conn).unwrap();

        let version = MigrationRunner::current_version(&conn).unwrap();
        assert_eq!(version, 8);

        // Only one row per version
        let counts: Vec<(i64, i64)> = {
            let mut stmt = conn
                .prepare("SELECT version, COUNT(*) FROM schema_versions GROUP BY version ORDER BY version")
                .unwrap();
            let rows = stmt
                .query_map([], |row| {
                    Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))
                })
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        for (_version, cnt) in &counts {
            assert_eq!(*cnt, 1);
        }
    }

    #[test]
    fn test_fts5_trigger_exists() {
        let conn = in_memory_connection();
        let runner = MigrationRunner::new();
        runner.apply(&conn).unwrap();

        let triggers: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };

        assert!(triggers.contains(&"nodes_fts_ai".to_string()));
        assert!(triggers.contains(&"nodes_fts_ad".to_string()));
        assert!(triggers.contains(&"nodes_fts_au".to_string()));
    }

    #[test]
    fn test_add_column_if_not_exists_skip() {
        let conn = in_memory_connection();
        conn.execute_batch(
            "CREATE TABLE test_tbl (id INTEGER PRIMARY KEY, name TEXT);",
        )
        .unwrap();

        // First add — success
        add_column_if_not_exists(&conn, "test_tbl", "extra", "TEXT DEFAULT 'hello'").unwrap();

        // Second add of same column — should be no-op (not error)
        add_column_if_not_exists(&conn, "test_tbl", "extra", "TEXT DEFAULT 'hello'").unwrap();

        let cols: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM pragma_table_info('test_tbl') ORDER BY cid")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };
        assert_eq!(cols, vec!["id", "name", "extra"]);
    }

    #[test]
    fn test_current_version_zero_on_empty_db() {
        let conn = in_memory_connection();
        let version = MigrationRunner::current_version(&conn).unwrap();
        assert_eq!(version, 0);
    }

    #[test]
    fn test_v001_initial_schema_has_all_tables() {
        let conn = in_memory_connection();
        let migration = V001Initial;
        migration.up(&conn).unwrap();

        let tables: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                .unwrap();
            let rows = stmt
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap();
            rows.filter_map(|r| r.ok()).collect()
        };

        assert!(tables.contains(&"nodes".to_string()));
        assert!(tables.contains(&"edges".to_string()));
        assert!(tables.contains(&"files".to_string()));
        assert!(tables.contains(&"unresolved_refs".to_string()));
        assert!(tables.contains(&"schema_versions".to_string()));
        assert!(tables.contains(&"nodes_fts".to_string()));
    }
}
