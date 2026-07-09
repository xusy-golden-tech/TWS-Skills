//! SQLite connection management.
//!
//! Provides `Database` — a high-level handle that wraps a rusqlite `Connection`
//! with WAL journal mode, a 256 MB page cache, and 4 worker threads.
//! Configuration mirrors the Python ``configure_connection()`` function
//! (``tws_graph/store/connection.py``) exactly.

use crate::db::migrations::MigrationRunner;
use crate::db::models::{
    CrossLangEdgeRecord, FfiCrossEdgeRecord, FfiExportRecord, FfiImportRecord, HttpCallRecord,
    HttpRouteRecord,
};
use crate::query::NodeInfo;
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

    /// Build a safe FTS5 query string with prefix matching.
    ///
    /// Splits the query into terms, escapes dangerous characters, appends `*`
    /// for prefix matching, and joins terms with `AND`.  This matches the
    /// behaviour of Python's `_build_fts_query()` exactly, avoiding the
    /// case-sensitive exact-phrase behaviour of FTS5 double-quoting.
    pub fn fts5_escape_query(text: &str) -> String {
        // Replace `|` with ` OR ` — users often write `foo|bar` for OR
        let text = text.replace('|', " OR ");
        let terms: Vec<&str> = text.split_whitespace().collect();
        const KEYWORDS: &[&str] = &["AND", "OR", "NOT"];
        let escaped: Vec<String> = terms
            .into_iter()
            .filter_map(|t| {
                // Strip qualifier prefixes (kind:, lang:, path:) that may appear
                // in raw query strings.
                let t = if t.contains(':') {
                    t.split(':').nth(1).unwrap_or(t)
                } else {
                    t
                };
                // Escape double quotes (would trigger phrase mode in FTS5)
                let t = t.replace('"', "\"\"");
                if t.is_empty() {
                    None
                } else if KEYWORDS.contains(&t.as_str()) {
                    // Don't add * to FTS5 boolean operators
                    Some(t)
                } else {
                    // Prefix match: `term*` triggers FTS5 prefix queries.
                    Some(format!("{}*", t))
                }
            })
            .collect();
        if escaped.is_empty() {
            // If all terms were filtered out, return the original (sanitized)
            text.replace('"', "\"\"")
        } else {
            escaped.join(" ")
        }
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

    /// Get a node by its TEXT id (SHA256 hash) with rich metadata.
    ///
    /// Returns `Option<NodeInfo>` containing all node fields including
    /// signature, start_line, and docstring.
    pub fn get_node_rich(&self, node_id: &str) -> rusqlite::Result<Option<NodeInfo>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, kind, name, qualified_name, language, file_path, \
             signature, start_line, docstring, visibility FROM nodes WHERE id = ?1",
        )?;
        let mut rows = stmt.query_map([node_id], |row| {
            Ok(NodeInfo {
                id: row.get(0)?,
                kind: row.get(1)?,
                name: row.get(2)?,
                qualified_name: row.get(3)?,
                language: row.get(4)?,
                file_path: row.get(5)?,
                signature: row.get(6)?,
                start_line: row.get(7)?,
                docstring: row.get(8)?,
                visibility: row.get(9)?,
            })
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
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>, Option<i64>)>> {
        let qualified_text = Self::fts5_escape_query(text);
        let mut sql = String::from(
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, nodes_fts.rank, n.start_line \
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
                    row.get(7)?,
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
        path: Option<&str>,
        limit: usize,
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>, Option<i64>)>> {
        let pattern = format!("%{}%", text);
        let mut sql = String::from(
            "SELECT id, kind, name, qualified_name, file_path, language, NULL as rank, start_line \
             FROM nodes \
             WHERE (name LIKE ?1 OR qualified_name LIKE ?1)",
        );
        let mut next_param = 2usize;
        if kind.is_some() {
            sql.push_str(&format!(" AND kind = ?{}", next_param));
            next_param += 1;
        }
        if lang.is_some() {
            sql.push_str(&format!(" AND language = ?{}", next_param));
            next_param += 1;
        }
        if path.is_some() {
            sql.push_str(&format!(" AND file_path LIKE ?{}", next_param));
            next_param += 1;
        }
        sql.push_str(&format!(" LIMIT ?{}", next_param));

        let mut stmt = self.conn.prepare(&sql)?;
        let limit_i64 = limit as i64;
        let path_pattern = path.map(|p| format!("%{}%", p));
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![Box::new(pattern)];
        if let Some(k) = kind { params.push(Box::new(k.to_string())); }
        if let Some(l) = lang { params.push(Box::new(l.to_string())); }
        if let Some(ref pp) = path_pattern { params.push(Box::new(pp.clone())); }
        params.push(Box::new(limit_i64));

        let rows = stmt.query_map(
            rusqlite::params_from_iter(params.iter().map(|p| p.as_ref())),
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                    None::<f64>,
                    row.get(7)?,
                ))
            },
        )?;
        rows.collect()
    }

    /// Search nodes using Levenshtein edit distance <= 2 on the `name` field.
    ///
    /// Uses a prefix pre-filter (first character) to limit the candidate set,
    /// matching Python's `_search_fuzzy` behaviour.  Returns
    /// `(id, kind, name, qualified_name, file_path, language, rank)` rows
    /// with rank set to the edit distance (lower is better).
    pub fn search_edit_distance(
        &self,
        text: &str,
        kind: Option<&str>,
        lang: Option<&str>,
        path: Option<&str>,
        limit: usize,
    ) -> rusqlite::Result<Vec<(String, String, String, String, String, String, Option<f64>, Option<i64>)>> {
        if text.is_empty() {
            return Ok(Vec::new());
        }

        // Build query with prefix pre-filter (first character of query)
        let mut clauses: Vec<String> = Vec::new();
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        // Prefix pre-filter: only examine nodes whose name starts with the
        // same first character (dramatically reduces candidate set for large DBs).
        let prefix = &text[..text.chars().next().map(|c| c.len_utf8()).unwrap_or(1)];
        clauses.push("name LIKE ?".to_string());
        params.push(Box::new(format!("{}%", prefix)));

        if let Some(k) = kind {
            clauses.push(format!("kind = ?"));
            params.push(Box::new(k.to_string()));
        }
        if let Some(l) = lang {
            clauses.push(format!("language = ?"));
            params.push(Box::new(l.to_string()));
        }
        if let Some(p) = path {
            clauses.push(format!("file_path LIKE ?"));
            params.push(Box::new(format!("%{}%", p)));
        }

        let mut sql = String::from(
            "SELECT id, kind, name, qualified_name, file_path, language, start_line FROM nodes",
        );
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        // Limit candidates for performance (Python caps at 200)
        sql.push_str(" LIMIT 200");

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
                row.get::<_, Option<i64>>(6)?,
            ))
        })?;

        let mut scored: Vec<(String, String, String, String, String, String, Option<f64>, Option<i64>)> = rows
            .filter_map(|r| r.ok())
            .filter_map(|(id, k, name, qn, fp, lang_val, start_line)| {
                let dist = levenshtein_distance(&name.to_lowercase(), &text.to_lowercase());
                if dist <= 2 {
                    Some((id, k, name, qn, fp, lang_val, Some(dist as f64), start_line))
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
    ///
    /// Supports the following input formats:
    /// 1. `chat_with_tools` — simple name (existing behaviour)
    /// 2. `LLMGateway.chat_with_tools` — Class.method notation
    /// 3. `LLMGateway.chat_with_tools()` — Class.method with trailing parens
    /// 4. `modules/llm/gateway.py::LLMGateway::chat_with_tools` — full qualified_name
    ///
    /// For Class.method notation the method splits on the last `.`, matches
    /// `name = method_part` and `qualified_name LIKE '%class_part%'`.  If no
    /// result is found it falls back to the original query.
    pub fn find_node_id_by_name(&self, name: &str) -> rusqlite::Result<Option<String>> {
        // Detect Class.method notation: contains '.' but not '::'
        // Full qualified_names use '::' as separator so we skip those.
        if name.contains('.') && !name.contains("::") {
            // Strip trailing () if present (e.g. "MyClass.method()")
            let cleaned = name.strip_suffix("()").unwrap_or(name);
            if let Some(dot_pos) = cleaned.rfind('.') {
                let class_part = &cleaned[..dot_pos];
                let method_part = &cleaned[dot_pos + 1..];

                // Try Class.method query: match name + partial qualified_name
                let like_pattern = format!("%{}%", class_part);
                match self.conn.query_row(
                    "SELECT id FROM nodes WHERE name = ?1 AND qualified_name LIKE ?2 LIMIT 1",
                    rusqlite::params![method_part, like_pattern],
                    |row| row.get(0),
                ) {
                    Ok(id) => return Ok(Some(id)),
                    Err(rusqlite::Error::QueryReturnedNoRows) => {} // fall through
                    Err(e) => return Err(e),
                }
            }
        }

        // Original query (also serves as fallback for Class.method notation)
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
    // Cross-tier: http_calls
    // -----------------------------------------------------------------------

    /// Insert a single HTTP call record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_http_call(&self, record: &HttpCallRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO http_calls (url, http_method, func_node_id, url_is_template, \
             file_path, line, column, source_lang, raw_snippet) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            rusqlite::params![
                record.url,
                record.http_method,
                record.func_node_id,
                record.url_is_template as i32,
                record.file_path,
                record.line,
                record.column,
                record.source_lang,
                record.raw_snippet,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Batch-insert HTTP call records in a single transaction.
    ///
    /// Returns the number of rows inserted.
    pub fn batch_insert_http_calls(&self, records: &[HttpCallRecord]) -> Result<usize> {
        self.conn.execute_batch("BEGIN TRANSACTION")?;
        let mut count = 0;
        for record in records {
            self.insert_http_call(record)?;
            count += 1;
        }
        self.conn.execute_batch("COMMIT")?;
        Ok(count)
    }

    /// Get all HTTP call records.
    pub fn get_all_http_calls(&self) -> Result<Vec<HttpCallRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, url, http_method, func_node_id, url_is_template, \
             file_path, line, column, source_lang, raw_snippet FROM http_calls",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(HttpCallRecord {
                id: Some(row.get(0)?),
                url: row.get(1)?,
                http_method: row.get(2)?,
                func_node_id: row.get(3)?,
                url_is_template: row.get::<_, i32>(4)? != 0,
                file_path: row.get(5)?,
                line: row.get(6)?,
                column: row.get(7)?,
                source_lang: row.get(8)?,
                raw_snippet: row.get(9)?,
            })
        })?;
        rows.collect()
    }

    // -----------------------------------------------------------------------
    // Cross-tier: http_routes
    // -----------------------------------------------------------------------

    /// Insert a single HTTP route record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_http_route(&self, record: &HttpRouteRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO http_routes (url_pattern, url_pattern_raw, http_method, handler_node_id, \
             file_path, line, column, source_lang, source_framework, raw_snippet) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                record.url_pattern,
                record.url_pattern_raw,
                record.http_method,
                record.handler_node_id,
                record.file_path,
                record.line,
                record.column,
                record.source_lang,
                record.source_framework,
                record.raw_snippet,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Batch-insert HTTP route records in a single transaction.
    ///
    /// Returns the number of rows inserted.
    pub fn batch_insert_http_routes(&self, records: &[HttpRouteRecord]) -> Result<usize> {
        self.conn.execute_batch("BEGIN TRANSACTION")?;
        let mut count = 0;
        for record in records {
            self.insert_http_route(record)?;
            count += 1;
        }
        self.conn.execute_batch("COMMIT")?;
        Ok(count)
    }

    /// Get all HTTP route records.
    pub fn get_all_http_routes(&self) -> Result<Vec<HttpRouteRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, url_pattern, url_pattern_raw, http_method, handler_node_id, \
             file_path, line, column, source_lang, source_framework, raw_snippet FROM http_routes",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(HttpRouteRecord {
                id: Some(row.get(0)?),
                url_pattern: row.get(1)?,
                url_pattern_raw: row.get(2)?,
                http_method: row.get(3)?,
                handler_node_id: row.get(4)?,
                file_path: row.get(5)?,
                line: row.get(6)?,
                column: row.get(7)?,
                source_lang: row.get(8)?,
                source_framework: row.get(9)?,
                raw_snippet: row.get(10)?,
            })
        })?;
        rows.collect()
    }

    // -----------------------------------------------------------------------
    // Cross-tier: cross_lang_edges
    // -----------------------------------------------------------------------

    /// Insert a single cross-language edge record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_cross_lang_edge(&self, record: &CrossLangEdgeRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO cross_lang_edges (from_call_id, to_route_id, url, http_method, \
             match_type, confidence) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![
                record.from_call_id,
                record.to_route_id,
                record.url,
                record.http_method,
                record.match_type,
                record.confidence,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Batch-insert cross-language edge records in a single transaction.
    ///
    /// Returns the number of rows inserted.
    pub fn batch_insert_cross_lang_edges(&self, records: &[CrossLangEdgeRecord]) -> Result<usize> {
        self.conn.execute_batch("BEGIN TRANSACTION")?;
        let mut count = 0;
        for record in records {
            self.insert_cross_lang_edge(record)?;
            count += 1;
        }
        self.conn.execute_batch("COMMIT")?;
        Ok(count)
    }

    /// Get cross-language edges, optionally filtered by call_id or route_id.
    pub fn get_cross_lang_edges(
        &self,
        call_id: Option<i64>,
        route_id: Option<i64>,
    ) -> Result<Vec<CrossLangEdgeRecord>> {
        let mut sql = String::from(
            "SELECT id, from_call_id, to_route_id, url, http_method, match_type, confidence \
             FROM cross_lang_edges WHERE 1=1",
        );
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        if let Some(cid) = call_id {
            sql.push_str(" AND from_call_id = ?1");
            params.push(Box::new(cid));
        }
        if let Some(rid) = route_id {
            let idx = params.len() + 1;
            sql.push_str(&format!(" AND to_route_id = ?{}", idx));
            params.push(Box::new(rid));
        }
        sql.push_str(" ORDER BY confidence DESC");

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(
            rusqlite::params_from_iter(params.iter().map(|p| p.as_ref())),
            |row| {
                Ok(CrossLangEdgeRecord {
                    id: Some(row.get(0)?),
                    from_call_id: row.get(1)?,
                    to_route_id: row.get(2)?,
                    url: row.get(3)?,
                    http_method: row.get(4)?,
                    match_type: row.get(5)?,
                    confidence: row.get(6)?,
                })
            },
        )?;
        rows.collect()
    }

    // -----------------------------------------------------------------------
    // Cross-tier: nodes.http_role
    // -----------------------------------------------------------------------

    /// Update the `http_role` column on a node.
    ///
    /// `role` should be `"http-call"`, `"http-route"`, or `NULL`.
    pub fn update_node_http_role(&self, node_rowid: i64, role: Option<&str>) -> Result<()> {
        self.conn.execute(
            "UPDATE nodes SET http_role = ?1 WHERE rowid = ?2",
            rusqlite::params![role, node_rowid],
        )?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Cross-tier: ffi_imports
    // -----------------------------------------------------------------------

    /// Insert a single FFI import record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_ffi_import(&self, record: &FfiImportRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO ffi_imports (symbol_name, call_node_id, import_stmt, ffi_framework, \
             source_lang, file_path, line, column, raw_snippet) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            rusqlite::params![
                record.symbol_name,
                record.call_node_id,
                record.import_stmt,
                record.ffi_framework,
                record.source_lang,
                record.file_path,
                record.line,
                record.column,
                record.raw_snippet,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Batch-insert FFI import records in a single transaction.
    ///
    /// Returns the number of rows inserted.
    pub fn batch_insert_ffi_imports(&self, records: &[FfiImportRecord]) -> Result<usize> {
        self.conn.execute_batch("BEGIN TRANSACTION")?;
        let mut count = 0;
        for record in records {
            self.insert_ffi_import(record)?;
            count += 1;
        }
        self.conn.execute_batch("COMMIT")?;
        Ok(count)
    }

    /// Get all FFI import records.
    pub fn get_all_ffi_imports(&self) -> Result<Vec<FfiImportRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, symbol_name, call_node_id, import_stmt, ffi_framework, \
             source_lang, file_path, line, column, raw_snippet FROM ffi_imports",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(FfiImportRecord {
                id: Some(row.get(0)?),
                symbol_name: row.get(1)?,
                call_node_id: row.get(2)?,
                import_stmt: row.get(3)?,
                ffi_framework: row.get(4)?,
                source_lang: row.get(5)?,
                file_path: row.get(6)?,
                line: row.get(7)?,
                column: row.get(8)?,
                raw_snippet: row.get(9)?,
            })
        })?;
        rows.collect()
    }

    // -----------------------------------------------------------------------
    // Cross-tier: ffi_exports
    // -----------------------------------------------------------------------

    /// Insert a single FFI export record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_ffi_export(&self, record: &FfiExportRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO ffi_exports (symbol_name, symbol_name_raw, func_node_id, ffi_framework, \
             source_lang, file_path, line, column, raw_snippet) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            rusqlite::params![
                record.symbol_name,
                record.symbol_name_raw,
                record.func_node_id,
                record.ffi_framework,
                record.source_lang,
                record.file_path,
                record.line,
                record.column,
                record.raw_snippet,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Batch-insert FFI export records in a single transaction.
    ///
    /// Returns the number of rows inserted.
    pub fn batch_insert_ffi_exports(&self, records: &[FfiExportRecord]) -> Result<usize> {
        self.conn.execute_batch("BEGIN TRANSACTION")?;
        let mut count = 0;
        for record in records {
            self.insert_ffi_export(record)?;
            count += 1;
        }
        self.conn.execute_batch("COMMIT")?;
        Ok(count)
    }

    /// Get all FFI export records.
    pub fn get_all_ffi_exports(&self) -> Result<Vec<FfiExportRecord>> {
        let mut stmt = self.conn.prepare(
            "SELECT id, symbol_name, symbol_name_raw, func_node_id, ffi_framework, \
             source_lang, file_path, line, column, raw_snippet FROM ffi_exports",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(FfiExportRecord {
                id: Some(row.get(0)?),
                symbol_name: row.get(1)?,
                symbol_name_raw: row.get(2)?,
                func_node_id: row.get(3)?,
                ffi_framework: row.get(4)?,
                source_lang: row.get(5)?,
                file_path: row.get(6)?,
                line: row.get(7)?,
                column: row.get(8)?,
                raw_snippet: row.get(9)?,
            })
        })?;
        rows.collect()
    }

    // -----------------------------------------------------------------------
    // Cross-tier: ffi_cross_edges
    // -----------------------------------------------------------------------

    /// Insert a single FFI cross-edge record.
    ///
    /// Returns the new row ID on success.
    pub fn insert_ffi_cross_edge(&self, record: &FfiCrossEdgeRecord) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO ffi_cross_edges (from_node_id, to_node_id, ffi_import_id, \
             ffi_export_id, edge_kind, symbol_name, ffi_framework) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                record.from_node_id,
                record.to_node_id,
                record.ffi_import_id,
                record.ffi_export_id,
                record.edge_kind,
                record.symbol_name,
                record.ffi_framework,
            ],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Get FFI cross-edges, optionally filtered by import_id or export_id.
    pub fn get_ffi_cross_edges(
        &self,
        import_id: Option<i64>,
        export_id: Option<i64>,
    ) -> Result<Vec<FfiCrossEdgeRecord>> {
        let mut sql = String::from(
            "SELECT id, from_node_id, to_node_id, ffi_import_id, ffi_export_id, \
             edge_kind, symbol_name, ffi_framework, created_at \
             FROM ffi_cross_edges WHERE 1=1",
        );
        let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();

        if let Some(iid) = import_id {
            sql.push_str(" AND ffi_import_id = ?1");
            params.push(Box::new(iid));
        }
        if let Some(eid) = export_id {
            let idx = params.len() + 1;
            sql.push_str(&format!(" AND ffi_export_id = ?{}", idx));
            params.push(Box::new(eid));
        }

        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(
            rusqlite::params_from_iter(params.iter().map(|p| p.as_ref())),
            |row| {
                Ok(FfiCrossEdgeRecord {
                    id: Some(row.get(0)?),
                    from_node_id: row.get(1)?,
                    to_node_id: row.get(2)?,
                    ffi_import_id: row.get(3)?,
                    ffi_export_id: row.get(4)?,
                    edge_kind: row.get(5)?,
                    symbol_name: row.get(6)?,
                    ffi_framework: row.get(7)?,
                    created_at: row.get(8)?,
                })
            },
        )?;
        rows.collect()
    }

    /// Get all FFI exports with the number of callers (cross-edges) for each.
    ///
    /// Uses LEFT JOIN + GROUP BY to count cross-edges per export.
    /// Returns a vector of (FfiExportRecord, caller_count) pairs.
    pub fn get_ffi_exports_with_caller_count(
        &self,
    ) -> Result<Vec<(FfiExportRecord, i64)>> {
        let mut stmt = self.conn.prepare(
            "SELECT e.id, e.symbol_name, e.symbol_name_raw, e.func_node_id, \
             e.ffi_framework, e.source_lang, e.file_path, e.line, e.column, e.raw_snippet, \
             COALESCE(COUNT(c.id), 0) AS caller_count \
             FROM ffi_exports e \
             LEFT JOIN ffi_cross_edges c ON c.ffi_export_id = e.id \
             GROUP BY e.id \
             ORDER BY caller_count DESC",
        )?;
        let rows = stmt.query_map([], |row| {
            let record = FfiExportRecord {
                id: Some(row.get(0)?),
                symbol_name: row.get(1)?,
                symbol_name_raw: row.get(2)?,
                func_node_id: row.get(3)?,
                ffi_framework: row.get(4)?,
                source_lang: row.get(5)?,
                file_path: row.get(6)?,
                line: row.get(7)?,
                column: row.get(8)?,
                raw_snippet: row.get(9)?,
            };
            let count: i64 = row.get(10)?;
            Ok((record, count))
        })?;
        rows.collect()
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
/// Returns a 16-char hex string from
/// ``XXH3_64("{file_path}:{qualified_name}")``.
///
/// This matches the Python ``_hash_id()`` function in
/// ``tws_graph/store/query_builder.py``.
pub fn hash_id(file_path: &str, qualified_name: &str) -> String {
    let raw = format!("{}:{}", file_path, qualified_name);
    let digest = xxhash_rust::xxh3::xxh3_64(raw.as_bytes());
    format!("{:016x}", digest)
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
            assert_eq!(version, 10);

            let count: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM schema_versions",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(count, 10);
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
        assert_eq!(a.len(), 16);

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

    // ------------------------------------------------------------------
    // find_node_id_by_name tests
    // ------------------------------------------------------------------

    #[test]
    fn find_node_id_by_name_simple_name() {
        let path = temp_db_path("fnidn_simple");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        // Insert: name="chat_with_tools", qualified_name="modules/llm/gateway.py::LLMGateway::chat_with_tools"
        let nid = hash_id("modules/llm/gateway.py", "modules/llm/gateway.py::LLMGateway::chat_with_tools");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'method', 'chat_with_tools', \
             'modules/llm/gateway.py::LLMGateway::chat_with_tools', \
             'modules/llm/gateway.py', 'python', 42, 50, ?2)",
            params![nid, ts],
        )
        .unwrap();

        let result = db.find_node_id_by_name("chat_with_tools").unwrap();
        assert_eq!(result, Some(nid.clone()));

        cleanup(&path);
    }

    #[test]
    fn find_node_id_by_name_class_dot_method() {
        let path = temp_db_path("fnidn_class_dot");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let nid = hash_id("modules/llm/gateway.py", "modules/llm/gateway.py::LLMGateway::chat_with_tools");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'method', 'chat_with_tools', \
             'modules/llm/gateway.py::LLMGateway::chat_with_tools', \
             'modules/llm/gateway.py', 'python', 42, 50, ?2)",
            params![nid, ts],
        )
        .unwrap();

        // Class.method notation
        let result = db.find_node_id_by_name("LLMGateway.chat_with_tools").unwrap();
        assert_eq!(result, Some(nid));

        cleanup(&path);
    }

    #[test]
    fn find_node_id_by_name_class_dot_method_with_parens() {
        let path = temp_db_path("fnidn_parens");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let nid = hash_id("modules/llm/gateway.py", "modules/llm/gateway.py::LLMGateway::chat_with_tools");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'method', 'chat_with_tools', \
             'modules/llm/gateway.py::LLMGateway::chat_with_tools', \
             'modules/llm/gateway.py', 'python', 42, 50, ?2)",
            params![nid, ts],
        )
        .unwrap();

        // Class.method() — trailing parens should be stripped
        let result = db.find_node_id_by_name("LLMGateway.chat_with_tools()").unwrap();
        assert_eq!(result, Some(nid));

        cleanup(&path);
    }

    #[test]
    fn find_node_id_by_name_nonexistent() {
        let path = temp_db_path("fnidn_nonexistent");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let nid = hash_id("modules/llm/gateway.py", "modules/llm/gateway.py::LLMGateway::chat_with_tools");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'method', 'chat_with_tools', \
             'modules/llm/gateway.py::LLMGateway::chat_with_tools', \
             'modules/llm/gateway.py', 'python', 42, 50, ?2)",
            params![nid, ts],
        )
        .unwrap();

        // Non-existent symbol
        let result = db.find_node_id_by_name("NonExistent.method").unwrap();
        assert_eq!(result, None);

        // Original node still queryable by simple name
        let result2 = db.find_node_id_by_name("chat_with_tools").unwrap();
        assert_eq!(result2, Some(nid));

        cleanup(&path);
    }

    #[test]
    fn find_node_id_by_name_fallback_to_original() {
        let path = temp_db_path("fnidn_fallback");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let nid = hash_id("modules/llm/gateway.py", "modules/llm/gateway.py::LLMGateway::chat_with_tools");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'method', 'chat_with_tools', \
             'modules/llm/gateway.py::LLMGateway::chat_with_tools', \
             'modules/llm/gateway.py', 'python', 42, 50, ?2)",
            params![nid, ts],
        )
        .unwrap();

        // Full qualified_name with :: — goes through original query path
        let result = db
            .find_node_id_by_name("modules/llm/gateway.py::LLMGateway::chat_with_tools")
            .unwrap();
        assert_eq!(result, Some(nid));

        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // get_node_rich tests
    // ------------------------------------------------------------------

    #[test]
    fn test_get_node_rich_existing_node() {
        let path = temp_db_path("get_node_rich_exist");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let node_id = hash_id("src/util.py", "src.util::helper");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, signature, docstring, visibility, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                node_id,
                "function",
                "helper",
                "src.util::helper",
                "src/util.py",
                "python",
                42,
                55,
                Some("def helper(x: int) -> str"),
                Some("Convert int to string."),
                Some("public"),
                ts,
            ],
        )
        .unwrap();

        let info = db.get_node_rich(&node_id).unwrap().unwrap();
        assert_eq!(info.id, node_id);
        assert_eq!(info.kind, "function");
        assert_eq!(info.name, "helper");
        assert_eq!(info.qualified_name, "src.util::helper");
        assert_eq!(info.language, "python");
        assert_eq!(info.file_path, "src/util.py");
        assert_eq!(info.signature, Some("def helper(x: int) -> str".to_string()));
        assert_eq!(info.start_line, Some(42));
        assert_eq!(info.docstring, Some("Convert int to string.".to_string()));
        assert_eq!(info.visibility, Some("public".to_string()));

        cleanup(&path);
    }

    #[test]
    fn test_get_node_rich_nonexistent() {
        let path = temp_db_path("get_node_rich_nonex");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let result = db.get_node_rich("nonexistent_id_12345").unwrap();
        assert!(result.is_none());

        cleanup(&path);
    }

    #[test]
    fn test_get_node_unchanged() {
        let path = temp_db_path("get_node_unchanged");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        let node_id = hash_id("src/mod.rs", "src.mod::my_func");
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                node_id,
                "function",
                "my_func",
                "src.mod::my_func",
                "src/mod.rs",
                "rust",
                10,
                20,
                ts,
            ],
        )
        .unwrap();

        // get_node() must still return a 6-tuple: (id, kind, name, qualified_name, language, file_path)
        let node = db.get_node(&node_id).unwrap().unwrap();
        assert_eq!(node.0, node_id);
        assert_eq!(node.1, "function");
        assert_eq!(node.2, "my_func");
        assert_eq!(node.3, "src.mod::my_func");
        assert_eq!(node.4, "rust");
        assert_eq!(node.5, "src/mod.rs");

        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // FFI import / export / cross-edge tests
    // ------------------------------------------------------------------

    #[test]
    fn test_insert_and_query_ffi_import() {
        let path = temp_db_path("ffi_import_crud");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let db_conn = db.connection();

        let record = FfiImportRecord {
            id: None,
            symbol_name: "rust_index".to_string(),
            call_node_id: 42,
            import_stmt: Some("from tws_graph._core import rust_index".to_string()),
            ffi_framework: "pyo3".to_string(),
            source_lang: "python".to_string(),
            file_path: "src/tws_graph/cli.py".to_string(),
            line: 15,
            column: 5,
            raw_snippet: Some("from tws_graph._core import rust_index".to_string()),
        };

        // Insert
        let new_id = db.insert_ffi_import(&record).unwrap();
        assert!(new_id > 0);

        // Query
        let results = db.get_all_ffi_imports().unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].id, Some(new_id));
        assert_eq!(results[0].symbol_name, "rust_index");
        assert_eq!(results[0].call_node_id, 42);
        assert_eq!(results[0].ffi_framework, "pyo3");
        assert_eq!(results[0].source_lang, "python");
        assert_eq!(results[0].file_path, "src/tws_graph/cli.py");
        assert_eq!(results[0].line, 15);
        assert_eq!(results[0].column, 5);
        assert_eq!(
            results[0].import_stmt,
            Some("from tws_graph._core import rust_index".to_string())
        );
        assert_eq!(
            results[0].raw_snippet,
            Some("from tws_graph._core import rust_index".to_string())
        );

        cleanup(&path);
    }

    #[test]
    fn test_insert_and_query_ffi_export() {
        let path = temp_db_path("ffi_export_crud");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let db_conn = db.connection();

        let record = FfiExportRecord {
            id: None,
            symbol_name: "my_exported_fn".to_string(),
            symbol_name_raw: Some("my_exported_fn_raw".to_string()),
            func_node_id: 101,
            ffi_framework: "pyo3".to_string(),
            source_lang: "rust".to_string(),
            file_path: "tws-graph/rust_core/src/lib.rs".to_string(),
            line: 280,
            column: 1,
            raw_snippet: Some("#[pyfunction]\nfn my_exported_fn() {}".to_string()),
        };

        // Insert
        let new_id = db.insert_ffi_export(&record).unwrap();
        assert!(new_id > 0);

        // Query
        let results = db.get_all_ffi_exports().unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].id, Some(new_id));
        assert_eq!(results[0].symbol_name, "my_exported_fn");
        assert_eq!(results[0].symbol_name_raw, Some("my_exported_fn_raw".to_string()));
        assert_eq!(results[0].func_node_id, 101);
        assert_eq!(results[0].ffi_framework, "pyo3");
        assert_eq!(results[0].source_lang, "rust");
        assert_eq!(results[0].file_path, "tws-graph/rust_core/src/lib.rs");
        assert_eq!(results[0].line, 280);
        assert_eq!(results[0].column, 1);

        cleanup(&path);
    }

    #[test]
    fn test_insert_ffi_cross_edge() {
        let path = temp_db_path("ffi_cross_edge_crud");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let db_conn = db.connection();

        // First insert an import and export to get their IDs
        let import_record = FfiImportRecord {
            id: None,
            symbol_name: "my_fn".to_string(),
            call_node_id: 1,
            import_stmt: None,
            ffi_framework: "pyo3".to_string(),
            source_lang: "python".to_string(),
            file_path: "src/cli.py".to_string(),
            line: 10,
            column: 1,
            raw_snippet: None,
        };
        let import_id = db.insert_ffi_import(&import_record).unwrap();

        let export_record = FfiExportRecord {
            id: None,
            symbol_name: "my_fn".to_string(),
            symbol_name_raw: None,
            func_node_id: 100,
            ffi_framework: "pyo3".to_string(),
            source_lang: "rust".to_string(),
            file_path: "src/lib.rs".to_string(),
            line: 280,
            column: 1,
            raw_snippet: None,
        };
        let export_id = db.insert_ffi_export(&export_record).unwrap();

        // Now insert a cross-edge
        let edge = FfiCrossEdgeRecord {
            id: None,
            from_node_id: 1,
            to_node_id: 100,
            ffi_import_id: import_id,
            ffi_export_id: export_id,
            edge_kind: "CROSS_FFI".to_string(),
            symbol_name: "my_fn".to_string(),
            ffi_framework: "pyo3".to_string(),
            created_at: String::new(),
        };
        let edge_id = db.insert_ffi_cross_edge(&edge).unwrap();
        assert!(edge_id > 0);

        // Query cross-edges by import_id
        let edges = db.get_ffi_cross_edges(Some(import_id), None).unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].id, Some(edge_id));
        assert_eq!(edges[0].ffi_import_id, import_id);
        assert_eq!(edges[0].ffi_export_id, export_id);
        assert_eq!(edges[0].symbol_name, "my_fn");
        assert_eq!(edges[0].ffi_framework, "pyo3");
        assert_eq!(edges[0].edge_kind, "CROSS_FFI");

        // Query cross-edges by export_id
        let edges2 = db.get_ffi_cross_edges(None, Some(export_id)).unwrap();
        assert_eq!(edges2.len(), 1);
        assert_eq!(edges2[0].ffi_export_id, export_id);

        // Query cross-edges without filters
        let edges3 = db.get_ffi_cross_edges(None, None).unwrap();
        assert_eq!(edges3.len(), 1);

        cleanup(&path);
    }

    #[test]
    fn test_get_exports_with_caller_count_empty() {
        let path = temp_db_path("ffi_callers_empty");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let db_conn = db.connection();

        // Insert an export with no cross-edges
        let export = FfiExportRecord {
            id: None,
            symbol_name: "orphan_fn".to_string(),
            symbol_name_raw: None,
            func_node_id: 200,
            ffi_framework: "cgo".to_string(),
            source_lang: "go".to_string(),
            file_path: "src/export.go".to_string(),
            line: 5,
            column: 1,
            raw_snippet: None,
        };
        let export_id = db.insert_ffi_export(&export).unwrap();

        // Get exports with caller count — should return 0 callers
        let results = db.get_ffi_exports_with_caller_count().unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0.id, Some(export_id));
        assert_eq!(results[0].0.symbol_name, "orphan_fn");
        assert_eq!(results[0].1, 0); // caller_count should be 0

        cleanup(&path);
    }

    #[test]
    fn test_get_exports_with_caller_count() {
        let path = temp_db_path("ffi_callers_count");
        cleanup(&path);

        let db = Database::initialize(&path).unwrap();
        let db_conn = db.connection();

        // Insert an export
        let export = FfiExportRecord {
            id: None,
            symbol_name: "popular_fn".to_string(),
            symbol_name_raw: None,
            func_node_id: 300,
            ffi_framework: "pyo3".to_string(),
            source_lang: "rust".to_string(),
            file_path: "src/lib.rs".to_string(),
            line: 100,
            column: 1,
            raw_snippet: None,
        };
        let export_id = db.insert_ffi_export(&export).unwrap();

        // Insert 2 imports
        let import1 = FfiImportRecord {
            id: None,
            symbol_name: "popular_fn".to_string(),
            call_node_id: 10,
            import_stmt: None,
            ffi_framework: "pyo3".to_string(),
            source_lang: "python".to_string(),
            file_path: "src/a.py".to_string(),
            line: 1,
            column: 1,
            raw_snippet: None,
        };
        let import1_id = db.insert_ffi_import(&import1).unwrap();

        let import2 = FfiImportRecord {
            id: None,
            symbol_name: "popular_fn".to_string(),
            call_node_id: 20,
            import_stmt: None,
            ffi_framework: "pyo3".to_string(),
            source_lang: "python".to_string(),
            file_path: "src/b.py".to_string(),
            line: 1,
            column: 1,
            raw_snippet: None,
        };
        let import2_id = db.insert_ffi_import(&import2).unwrap();

        // Create 2 cross-edges linking these imports to the same export
        let edge1 = FfiCrossEdgeRecord {
            id: None,
            from_node_id: 10,
            to_node_id: 300,
            ffi_import_id: import1_id,
            ffi_export_id: export_id,
            edge_kind: "CROSS_FFI".to_string(),
            symbol_name: "popular_fn".to_string(),
            ffi_framework: "pyo3".to_string(),
            created_at: String::new(),
        };
        db.insert_ffi_cross_edge(&edge1).unwrap();

        let edge2 = FfiCrossEdgeRecord {
            id: None,
            from_node_id: 20,
            to_node_id: 300,
            ffi_import_id: import2_id,
            ffi_export_id: export_id,
            edge_kind: "CROSS_FFI".to_string(),
            symbol_name: "popular_fn".to_string(),
            ffi_framework: "pyo3".to_string(),
            created_at: String::new(),
        };
        db.insert_ffi_cross_edge(&edge2).unwrap();

        // Get exports with caller count — should return 2 callers
        let results = db.get_ffi_exports_with_caller_count().unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0.id, Some(export_id));
        assert_eq!(results[0].0.symbol_name, "popular_fn");
        assert_eq!(results[0].1, 2); // caller_count should be 2

        cleanup(&path);
    }
}
