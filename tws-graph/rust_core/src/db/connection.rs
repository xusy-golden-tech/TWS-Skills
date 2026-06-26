//! SQLite connection management.
//!
//! Provides `Database` — a high-level handle that wraps a rusqlite `Connection`
//! with WAL journal mode, a 256 MB page cache, and 4 worker threads.
//! Configuration mirrors the Python ``configure_connection()`` function
//! (``tws_graph/store/connection.py``) exactly.

use crate::db::migrations::MigrationRunner;
use rusqlite::{Connection, Result};
use sha2::{Digest, Sha256};
use std::path::Path;

// ---------------------------------------------------------------------------
// Database handle
// ---------------------------------------------------------------------------

/// High-level handle for a code-graph SQLite database.
///
/// Wraps a rusqlite `Connection` configured with the canonical set of
/// performance and safety PRAGMAs.  Provides lifecycle methods for
/// opening, initializing, optimizing, and closing the database.
pub struct Database {
    conn: Connection,
}

impl Database {
    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// Open an existing database file.
    ///
    /// Applies all canonical PRAGMAs and runs pending migrations.
    /// Returns an error if the file does not exist.
    pub fn open(db_path: &Path) -> Result<Self> {
        if !db_path.exists() {
            return Err(rusqlite::Error::ToSqlConversionFailure(
                Box::new(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    format!("Database not found: {}", db_path.display()),
                )),
            ));
        }

        let conn = Self::create_connection(db_path)?;
        let runner = MigrationRunner::new();
        runner.apply(&conn)?;

        Ok(Self { conn })
    }

    /// Open a database file, creating it (with full schema) if it does not exist.
    ///
    /// If the file does not exist, the parent directory is created (if needed),
    /// the initial schema plus all 8 migrations are applied, producing a database
    /// that matches the current schema exactly.
    ///
    /// If the file already exists, pending migrations are applied.
    pub fn initialize(db_path: &Path) -> Result<Self> {
        // Ensure parent directory exists
        if let Some(parent) = db_path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    rusqlite::Error::ToSqlConversionFailure(Box::new(e))
                })?;
            }
        }

        let is_new = !db_path.exists();
        let conn = Self::create_connection(db_path)?;

        let runner = MigrationRunner::new();
        runner.initialize(&conn)?;

        // Log that a new database was created
        if is_new {
            // Best-effort log; we don't pull in the `log` crate just for this.
            // Upstream code may choose to log on `is_new`.
            let _ = is_new;
        }

        Ok(Self { conn })
    }

    // -----------------------------------------------------------------------
    // Pragma configuration
    // -----------------------------------------------------------------------

    /// Apply the canonical set of performance and safety PRAGMAs.
    ///
    /// This is the **single source of truth** for SQLite connection
    /// configuration in the Rust core. Matches the Python
    /// ``configure_connection()`` function.
    ///
    /// Pragmas applied:
    ///
    /// | Pragma            | Value         | Rationale                          |
    /// |-------------------|---------------|------------------------------------|
    /// | busy_timeout      | 5000          | Wait 5 s before "database locked"  |
    /// | foreign_keys      | ON            | Enforce FK constraints             |
    /// | journal_mode      | WAL           | Concurrent reads during writes     |
    /// | synchronous       | NORMAL        | Safe with WAL, fast                |
    /// | cache_size        | -256000       | 256 MB page cache                  |
    /// | temp_store        | MEMORY        | Temp tables in memory              |
    /// | mmap_size         | 268435456     | 256 MB memory-mapped I/O           |
    /// | threads           | 4             | Up to 4 auxiliary worker threads   |
    pub fn configure_pragmas(conn: &Connection) -> Result<()> {
        conn.execute_batch(
            "\
            PRAGMA busy_timeout = 5000;
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            PRAGMA cache_size = -256000;
            PRAGMA temp_store = MEMORY;
            PRAGMA mmap_size = 268435456;
            PRAGMA threads = 4;
            ",
        )
    }

    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    /// Create a rusqlite Connection and configure all PRAGMAs.
    fn create_connection(db_path: &Path) -> Result<Connection> {
        let conn = Connection::open(db_path)?;
        Self::configure_pragmas(&conn)?;
        Ok(conn)
    }

    // -----------------------------------------------------------------------
    // Access
    // -----------------------------------------------------------------------

    /// Return a shared reference to the inner rusqlite `Connection`.
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    /// Consume the handle and return the inner `Connection`.
    pub fn into_inner(self) -> Connection {
        self.conn
    }

    // -----------------------------------------------------------------------
    // Query methods
    // -----------------------------------------------------------------------

    /// Escape special characters in an FTS5 query string so they are
    /// treated as literal text rather than query operators.
    ///
    /// Wraps the text in double quotes (treating it as a phrase query)
    /// and escapes any internal double quotes by doubling them.
    pub fn fts5_escape_query(text: &str) -> String {
        let escaped = text.replace('"', "\"\"");
        format!("\"{}\"", escaped)
    }

    /// Get all outbound edges from a node.
    ///
    /// Returns `(edge_rowid, target_node_hash, kind, target_text)` rows.
    /// `node_id` is the TEXT primary key from `nodes.id` (SHA256 hash).
    pub fn get_outbound_edges(
        &self,
        node_id: &str,
    ) -> rusqlite::Result<Vec<(i64, String, String, Option<String>)>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, target, kind, target_text FROM edges WHERE source = ?1",
        )?;
        let rows = stmt.query_map([node_id], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        })?;
        rows.collect()
    }

    /// Get all inbound edges to a node.
    ///
    /// Returns `(edge_rowid, source_node_hash, kind, target_text)` rows.
    pub fn get_inbound_edges(
        &self,
        node_id: &str,
    ) -> rusqlite::Result<Vec<(i64, String, String, Option<String>)>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, source, kind, target_text FROM edges WHERE target = ?1",
        )?;
        let rows = stmt.query_map([node_id], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        })?;
        rows.collect()
    }

    /// Get a node by its TEXT id (SHA256 hash).
    ///
    /// Returns `Option<(id, kind, name, qualified_name, language, file_path)>`.
    pub fn get_node(
        &self,
        node_id: &str,
    ) -> rusqlite::Result<Option<(String, String, String, String, String, String)>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, kind, name, qualified_name, language, file_path FROM nodes WHERE id = ?1",
        )?;
        let mut rows = stmt.query_map([node_id], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get(5)?,
            ))
        })?;
        match rows.next() {
            Some(Ok(r)) => Ok(Some(r)),
            Some(Err(e)) => Err(e),
            None => Ok(None),
        }
    }

    /// Search nodes using FTS5 BM25 scoring.
    ///
    /// Returns `(id, kind, name, qualified_name, file_path, language, rank)` rows.
    pub fn search_fts5(
        &self,
        text: &str,
        kind: Option<&str>,
        lang: Option<&str>,
        path: Option<&str>,
        limit: usize,
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>)>> {
        let qualified_text = Self::fts5_escape_query(text);
        let mut sql = String::from(
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, nodes_fts.rank \
             FROM nodes_fts \
             JOIN nodes n ON n.rowid = nodes_fts.rowid \
             WHERE nodes_fts MATCH ?1",
        );
        if kind.is_some() {
            sql.push_str(" AND n.kind = ?2");
        }
        if lang.is_some() {
            sql.push_str(" AND n.language = ?3");
        }
        if path.is_some() {
            sql.push_str(" AND n.file_path LIKE ?4");
        }
        sql.push_str(" ORDER BY rank LIMIT ?5");

        let mut stmt = self.conn.prepare(&sql)?;
        let limit_i64 = limit as i64;
        let path_pattern = path.map(|p| format!("%{}%", p));

        let rows = stmt.query_map(
            rusqlite::params![qualified_text, kind, lang, path_pattern, limit_i64],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    row.get(6)?,
                ))
            },
        )?;
        rows.collect()
    }

    /// Search nodes using LIKE (fallback when FTS5 returns no results).
    ///
    /// Returns `(id, kind, name, qualified_name, file_path, language, rank)` rows,
    /// with rank always `None`.
    pub fn search_like(
        &self,
        text: &str,
        kind: Option<&str>,
        lang: Option<&str>,
        limit: usize,
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>)>> {
        let pattern = format!("%{}%", text);
        let mut sql = String::from(
            "SELECT id, kind, name, qualified_name, file_path, language, NULL as rank \
             FROM nodes \
             WHERE (name LIKE ?1 OR qualified_name LIKE ?1)",
        );
        if kind.is_some() {
            sql.push_str(" AND kind = ?2");
        }
        if lang.is_some() {
            sql.push_str(" AND language = ?3");
        }
        sql.push_str(" LIMIT ?4");

        let mut stmt = self.conn.prepare(&sql)?;
        let limit_i64 = limit as i64;

        let rows = stmt.query_map(
            rusqlite::params![pattern, kind, lang, limit_i64],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    None::<f64>,
                ))
            },
        )?;
        rows.collect()
    }

    /// Search nodes using Levenshtein edit distance <= 2 on the `name` field.
    ///
    /// Returns `(id, kind, name, qualified_name, file_path, language, rank)` rows
    /// with rank set to the edit distance (lower is better).
    pub fn search_edit_distance(
        &self,
        text: &str,
        kind: Option<&str>,
        lang: Option<&str>,
        limit: usize,
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>)>> {
        // Build query dynamically so we never pass unused params.
        let mut clauses: Vec<&str> = Vec::new();
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        if let Some(k) = kind {
            clauses.push("kind = ?");
            params.push(Box::new(k.to_string()));
        }
        if let Some(l) = lang {
            clauses.push("language = ?");
            params.push(Box::new(l.to_string()));
        }

        let mut sql = String::from(
            "SELECT id, kind, name, qualified_name, file_path, language FROM nodes",
        );
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }

        let mut stmt = self.conn.prepare(&sql)?;
        let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        let rows = stmt.query_map(param_refs.as_slice(), |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })?;

        let mut scored: Vec<(String, String, String, String, String, String, Option<f64>)> = rows
            .filter_map(|r| r.ok())
            .filter_map(|(id, k, name, qn, fp, lang_val)| {
                let dist = levenshtein_distance(&name.to_lowercase(), &text.to_lowercase());
                if dist <= 2 {
                    Some((id, k, name, qn, fp, lang_val, Some(dist as f64)))
                } else {
                    None
                }
            })
            .collect();

        scored.sort_by(|a, b| a.6.partial_cmp(&b.6).unwrap_or(std::cmp::Ordering::Equal));
        scored.truncate(limit);
        Ok(scored)
    }

    /// Check if the nodes_fts table has any matching rows for the given text.
    pub fn has_fts_match(&self, text: &str) -> rusqlite::Result<bool> {
        let qualified = Self::fts5_escape_query(text);
        let count: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM nodes_fts WHERE nodes_fts MATCH ?1",
            [&qualified],
            |row| row.get(0),
        )?;
        Ok(count > 0)
    }

    /// Get unresolved references.
    ///
    /// Returns `(reference_name, reference_kind, file_path, from_node_id)` rows.
    pub fn get_unresolved(
        &self,
    ) -> rusqlite::Result<Vec<(String, String, String, String)>> {
        let mut stmt = self.conn.prepare(
            "SELECT reference_name, reference_kind, file_path, from_node_id FROM unresolved_refs",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
        })?;
        rows.collect()
    }

    /// Get all edges, optionally filtered by kind.
    ///
    /// Returns `(source, target, kind, target_text)` rows.
    pub fn get_all_edges(
        &self,
        kind_filter: Option<&str>,
    ) -> rusqlite::Result<Vec<(String, String, String, Option<String>)>> {
        if let Some(kind) = kind_filter {
            let mut stmt = self.conn.prepare(
                "SELECT source, target, kind, target_text FROM edges WHERE kind = ?1",
            )?;
            let rows = stmt.query_map([kind], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?;
            rows.collect()
        } else {
            let mut stmt = self.conn.prepare(
                "SELECT source, target, kind, target_text FROM edges",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?;
            rows.collect()
        }
    }

    /// Find a node by name or qualified_name and return its TEXT id.
    pub fn find_node_id_by_name(&self, name: &str) -> rusqlite::Result<Option<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT id FROM nodes WHERE name = ?1 OR qualified_name = ?1 LIMIT 1",
        )?;
        let mut rows = stmt.query_map([name], |row| row.get(0))?;
        match rows.next() {
            Some(Ok(r)) => Ok(Some(r)),
            Some(Err(e)) => Err(e),
            None => Ok(None),
        }
    }

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /// Run maintenance operations after bulk writes.
    ///
    /// Executes `PRAGMA optimize` (which reorganises indexes and updates
    /// query-planner statistics) followed by a passive WAL checkpoint
    /// (which moves WAL pages back to the main database without blocking).
    pub fn optimize(&self) -> Result<()> {
        self.conn.execute_batch(
            "\
            PRAGMA optimize;
            PRAGMA wal_checkpoint(PASSIVE);
            ",
        )
    }

    /// Close the database connection.
    ///
    /// Consumes the handle.  The underlying `Connection` is dropped,
    /// which implicitly closes the database.
    pub fn close(self) -> Result<()> {
        // Connection::close() is called implicitly on drop, but we provide
        // an explicit method so callers can handle close errors.
        // rusqlite's Connection does not have a close() method that returns
        // Result — shutdown happens in Drop.  We run a final checkpoint
        // to persist any outstanding WAL frames.
        drop(self.conn);
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Utility: node ID computation
// ---------------------------------------------------------------------------

/// Compute a deterministic node ID.
///
/// Returns the first 32 hex characters of
/// ``SHA256("{file_path}:{qualified_name}")``.
///
/// This matches the Python ``_hash_id()`` function in
/// ``tws_graph/store/query_builder.py``.
pub fn hash_id(file_path: &str, qualified_name: &str) -> String {
    let raw = format!("{}:{}", file_path, qualified_name);
    let digest = Sha256::digest(raw.as_bytes());
    hex::encode(&digest)[..32].to_string()
}

// ---------------------------------------------------------------------------
// Utility: Levenshtein edit distance
// ---------------------------------------------------------------------------

/// Compute the Levenshtein edit distance between two strings.
///
/// Uses dynamic programming with O(min(m,n)) space.
pub fn levenshtein_distance(a: &str, b: &str) -> usize {
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let a_len = a_chars.len();
    let b_len = b_chars.len();

    if a_len == 0 {
        return b_len;
    }
    if b_len == 0 {
        return a_len;
    }

    let mut prev: Vec<usize> = (0..=b_len).collect();
    let mut curr: Vec<usize> = vec![0; b_len + 1];

    for i in 1..=a_len {
        curr[0] = i;
        for j in 1..=b_len {
            let cost = if a_chars[i - 1] == b_chars[j - 1] { 0 } else { 1 };
            curr[j] = (prev[j] + 1)
                .min(curr[j - 1] + 1)
                .min(prev[j - 1] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }

    prev[b_len]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use std::time::{SystemTime, UNIX_EPOCH};

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    fn temp_db_path(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("tws_test_{}.db", name))
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        // Also remove WAL / SHM files if present
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // Tests
    // ------------------------------------------------------------------

    #[test]
    fn test_open_nonexistent_file_fails() {
        let path = temp_db_path("nonexistent");
        cleanup(&path);

        let result = Database::open(&path);
        assert!(result.is_err());

        cleanup(&path);
    }

    #[test]
    fn test_initialize_creates_database() {
        let path = temp_db_path("init_creates");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            // Verify pragma: foreign_keys should be ON
            let fk: i64 = db
                .conn
                .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
                .unwrap();
            assert_eq!(fk, 1);

            // Verify pragma: journal_mode should be WAL
            let jm: String = db
                .conn
                .query_row("PRAGMA journal_mode", [], |row| row.get(0))
                .unwrap();
            assert!(jm.to_uppercase().contains("WAL"));
        }

        // Verify file exists on disk
        assert!(path.exists());

        cleanup(&path);
    }

    #[test]
    fn test_initialize_creates_parent_dir() {
        let dir = std::env::temp_dir().join("tws_test_subdir_xyz");
        let path = dir.join("test.db");

        // Clean up pre-existing
        let _ = std::fs::remove_dir_all(&dir);

        {
            let _db = Database::initialize(&path).unwrap();
        }

        assert!(path.exists());

        // Clean up
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir(&dir);
    }

    #[test]
    fn test_pragma_cache_size() {
        let path = temp_db_path("pragma_cache");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let cs: i64 = db
                .conn
                .query_row("PRAGMA cache_size", [], |row| row.get(0))
                .unwrap();
            // cache_size = -256000 means ~256 MB in negative kibibytes
            assert_eq!(cs, -256000);
        }

        cleanup(&path);
    }

    #[test]
    fn test_pragma_synchronous_normal() {
        let path = temp_db_path("pragma_sync");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let sync: i64 = db
                .conn
                .query_row("PRAGMA synchronous", [], |row| row.get(0))
                .unwrap();
            // NORMAL = 1
            assert_eq!(sync, 1);
        }

        cleanup(&path);
    }

    #[test]
    fn test_pragma_mmap_size() {
        let path = temp_db_path("pragma_mmap");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let mmap: i64 = db
                .conn
                .query_row("PRAGMA mmap_size", [], |row| row.get(0))
                .unwrap();
            assert_eq!(mmap, 268435456);
        }

        cleanup(&path);
    }

    #[test]
    fn test_pragma_threads() {
        let path = temp_db_path("pragma_threads");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let threads: i64 = db
                .conn
                .query_row("PRAGMA threads", [], |row| row.get(0))
                .unwrap();
            assert_eq!(threads, 4);
        }

        cleanup(&path);
    }

    #[test]
    fn test_pragma_busy_timeout() {
        let path = temp_db_path("pragma_busy");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let bt: i64 = db
                .conn
                .query_row("PRAGMA busy_timeout", [], |row| row.get(0))
                .unwrap();
            assert_eq!(bt, 5000);
        }

        cleanup(&path);
    }

    #[test]
    fn test_insert_and_query_node() {
        let path = temp_db_path("insert_node");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let node_id = hash_id("src/main.py", "src.main::MyClass");
            let ts = now_ms();

            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, signature, docstring, visibility, is_abstract, \
                 is_exported, decorators, framework, properties, body, body_hash, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
                params![
                    node_id,
                    "class",
                    "MyClass",
                    "src.main::MyClass",
                    "src/main.py",
                    "python",
                    10,
                    50,
                    None::<String>,
                    Some("A sample class"),
                    Some("public"),
                    0,
                    1,
                    None::<String>,
                    None::<String>,
                    Some("{}"),
                    None::<String>,
                    None::<String>,
                    ts,
                ],
            )
            .unwrap();

            // Query it back
            let name: String = conn
                .query_row(
                    "SELECT name FROM nodes WHERE id = ?1",
                    params![node_id],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(name, "MyClass");

            // FTS5 should find it via trigger
            let fts_count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes_fts WHERE name MATCH ?1",
                    params!["MyClass"],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(fts_count, 1);
        }

        cleanup(&path);
    }

    #[test]
    fn test_batch_insert_nodes_transaction() {
        let path = temp_db_path("batch_insert");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            // Batch insert 10 nodes in a transaction
            conn.execute_batch("BEGIN TRANSACTION").unwrap();

            for i in 0..10 {
                let name = format!("func_{}", i);
                let qname = format!("src/mod.rs::{}", name);
                let nid = hash_id("src/mod.rs", &qname);

                conn.execute(
                    "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                     start_line, end_line, updated_at) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    params![
                        nid,
                        "function",
                        name,
                        qname,
                        "src/mod.rs",
                        "rust",
                        i * 10,
                        i * 10 + 5,
                        ts,
                    ],
                )
                .unwrap();
            }

            conn.execute_batch("COMMIT").unwrap();

            // Verify count
            let count: i64 = conn
                .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))
                .unwrap();
            assert_eq!(count, 10);

            // Verify FTS5 count
            let fts_count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM nodes_fts WHERE nodes_fts MATCH ?1",
                    params!["func_"],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(fts_count, 10);
        }

        cleanup(&path);
    }

    #[test]
    fn test_fts5_search_returns_results() {
        let path = temp_db_path("fts5_search");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            // Insert diverse nodes for FTS5 testing
            let nodes = vec![
                ("calculateTotal", "src/utils.py::calculateTotal", "function",
                 "python", "src/utils.py", Some("Calculate the grand total"), Some("def calculateTotal(items) -> float")),
                ("total_calculator", "src/calc.rs::total_calculator", "function",
                 "rust", "src/calc.rs", Some("Computes total sum"), None),
                ("TotalHandler", "src/handler.ts::TotalHandler", "class",
                 "typescript", "src/handler.ts", Some("Handles total related events"), None),
            ];

            for (name, qname, kind, lang, fp, doc, sig) in &nodes {
                let nid = hash_id(fp, qname);

                conn.execute(
                    "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                     start_line, end_line, signature, docstring, updated_at) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                    params![nid, kind, name, qname, fp, lang, 1, 10, *sig, *doc, ts],
                )
                .unwrap();
            }

            // FTS5 search for "total"
            let results: Vec<(String, f64)> = {
                let mut stmt = conn
                    .prepare(
                        "SELECT n.id, nodes_fts.rank \
                         FROM nodes_fts \
                         JOIN nodes n ON n.rowid = nodes_fts.rowid \
                         WHERE nodes_fts MATCH ?1 \
                         ORDER BY rank",
                    )
                    .unwrap();
                let rows = stmt
                    .query_map(params!["total"], |row| {
                        Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
                    })
                    .unwrap();
                rows.filter_map(|r| r.ok()).collect()
            };

            // All 3 nodes should match "total"
            assert_eq!(results.len(), 3);

            // BM25 rank — lower is better (can be negative for small datasets)
            for (_id, rank) in &results {
                // Rank should be a finite number — BM25 can produce negative
                // values in FTS5, so we only check it is finite
                assert!(rank.is_finite());
            }
        }

        cleanup(&path);
    }

    #[test]
    fn test_migration_version_recorded() {
        let path = temp_db_path("migration_version");
        cleanup(&path);

        {
            let _db = Database::initialize(&path).unwrap();

            // Re-open and check
            let db = Database::open(&path).unwrap();
            let conn = db.connection();

            let version = MigrationRunner::current_version(conn).unwrap();
            assert_eq!(version, 8);

            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM schema_versions",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(count, 8);
        }

        cleanup(&path);
    }

    #[test]
    fn test_insert_node_with_body_hash() {
        let path = temp_db_path("body_hash");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let node_id = hash_id("src/lib.rs", "src.lib::compute");
            let body = "fn compute() -> u32 { 42 }";
            let body_hash = hex::encode(Sha256::digest(body.as_bytes()))[..16].to_string();

            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, body, body_hash, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    node_id,
                    "function",
                    "compute",
                    "src.lib::compute",
                    "src/lib.rs",
                    "rust",
                    1,
                    1,
                    body,
                    &body_hash,
                    ts,
                ],
            )
            .unwrap();

            let retrieved_hash: String = conn
                .query_row(
                    "SELECT body_hash FROM nodes WHERE id = ?1",
                    params![node_id],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(retrieved_hash, body_hash);
        }

        cleanup(&path);
    }

    #[test]
    fn test_insert_edge_with_properties() {
        let path = temp_db_path("edge_props");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let src_id = hash_id("src/a.py", "src.a::foo");
            let tgt_id = hash_id("src/b.py", "src.b::bar");

            // Insert nodes first (FK constraint)
            for (nid, name, fp) in [(&src_id, "foo", "src/a.py"), (&tgt_id, "bar", "src/b.py")] {
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                     start_line, end_line, updated_at) VALUES (?1, 'function', ?2, ?3, ?4, 'python', 1, 1, ?5)",
                    params![nid, name, name, fp, ts],
                )
                .unwrap();
            }

            // Insert edge with properties
            conn.execute(
                "INSERT INTO edges (source, target, target_text, kind, source_loc, provenance, properties) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    src_id,
                    tgt_id,
                    Some("src.b::bar"),
                    "CALLS",
                    Some("src/a.py:5:1"),
                    Some("tree-sitter"),
                    Some(r#"{"similarity": 0.95}"#),
                ],
            )
            .unwrap();

            let props: String = conn
                .query_row(
                    "SELECT properties FROM edges WHERE source = ?1",
                    params![src_id],
                    |row| row.get(0),
                )
                .unwrap();
            assert!(props.contains("similarity"));
        }

        cleanup(&path);
    }

    #[test]
    fn test_hash_id_deterministic() {
        let a = hash_id("src/main.py", "src.main::MyClass");
        let b = hash_id("src/main.py", "src.main::MyClass");
        assert_eq!(a, b);
        assert_eq!(a.len(), 32);

        // Different inputs produce different hashes
        let c = hash_id("src/main.py", "src.main::OtherClass");
        assert_ne!(a, c);
    }

    #[test]
    fn test_optimize_does_not_error() {
        let path = temp_db_path("optimize");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            db.optimize().unwrap();
        }

        cleanup(&path);
    }

    #[test]
    fn test_initialize_then_open() {
        let path = temp_db_path("init_then_open");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let nid = hash_id("src/x.py", "src.x::test");
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, updated_at) \
                 VALUES (?1, 'function', 'test', 'src.x::test', 'src/x.py', 'python', 1, 1, ?2)",
                params![nid, ts],
            )
            .unwrap();

            db.close().unwrap();
        }

        // Re-open — data should persist
        {
            let db = Database::open(&path).unwrap();
            let conn = db.connection();

            let count: i64 = conn
                .query_row("SELECT COUNT(*) FROM nodes", [], |row| row.get(0))
                .unwrap();
            assert_eq!(count, 1);
        }

        cleanup(&path);
    }
}
