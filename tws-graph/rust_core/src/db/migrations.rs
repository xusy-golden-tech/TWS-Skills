//! Schema definitions and 8 migrations for the code-graph database.
//!
//! Migration history is tracked in the `_migrations` meta-table.

use rusqlite::{Connection, Result};

const MIGRATIONS: &[&str] = &[
    // M1 — initial schema: nodes, edges, files
    "CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT,
        file_path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        start_col INTEGER NOT NULL,
        end_col INTEGER NOT NULL,
        language TEXT NOT NULL,
        docstring TEXT,
        signature TEXT,
        body_hash TEXT
    );",
    // M2 — edges table
    "CREATE TABLE IF NOT EXISTS edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        provenance TEXT,
        FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE
    );",
    // M3 — files table
    "CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        language TEXT NOT NULL,
        checksum TEXT NOT NULL,
        last_indexed_at TEXT NOT NULL,
        node_count INTEGER NOT NULL DEFAULT 0,
        edge_count INTEGER NOT NULL DEFAULT 0
    );",
    // M4 — FTS5 index on node names
    "CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
        name,
        qualified_name,
        kind,
        language,
        file_path,
        docstring,
        signature,
        content='nodes',
        content_rowid='id'
    );",
    // M5 — indexes
    "CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);",
    "CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);",
    "CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);",
    "CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);",
    // M6 — snapshots table
    "CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        node_count INTEGER NOT NULL,
        edge_count INTEGER NOT NULL
    );",
    // M7 — snapshot_nodes / snapshot_edges
    "CREATE TABLE IF NOT EXISTS snapshot_nodes (
        snapshot_id INTEGER NOT NULL,
        node_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        qualified_name TEXT,
        file_path TEXT NOT NULL,
        language TEXT NOT NULL,
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
    );",
    "CREATE TABLE IF NOT EXISTS snapshot_edges (
        snapshot_id INTEGER NOT NULL,
        edge_id INTEGER NOT NULL,
        source_name TEXT NOT NULL,
        target_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
    );",
    // M8 — migrations tracking
    "CREATE TABLE IF NOT EXISTS _migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (datetime('now')),
        description TEXT
    );",
];

/// Run all pending migrations.
pub fn run(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS _migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            description TEXT
        );",
    )?;

    let current: i32 = conn
        .query_row(
            "SELECT COALESCE(MAX(version), 0) FROM _migrations",
            [],
            |row| row.get(0),
        )
        .unwrap_or(0);

    for (i, sql) in MIGRATIONS.iter().enumerate() {
        let version = (i + 1) as i32;
        if version <= current {
            continue;
        }
        conn.execute_batch(sql)?;
        conn.execute(
            "INSERT INTO _migrations (version, description) VALUES (?1, ?2)",
            rusqlite::params![version, format!("Migration {}", version)],
        )?;
    }

    Ok(())
}
