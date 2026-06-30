//! Cross-file reference resolver framework.
//!
//! Phase 0 (architecture foundation) provides:
//! - [`ModuleResolver`] trait — language-specific module resolution
//! - [`ModuleIndex`] — bidirectional module-name / file-path mapping
//! - [`resolve`] — the main resolution engine that scans dangling edges,
//!   matches targets using language-specific resolvers, updates the graph,
//!   and writes unresolved references to the `unresolved_refs` table.
//!
//! Future phases will implement concrete [`ModuleResolver`]s for each
//! supported language in the [`language`] module.

pub mod module_index;
pub mod language;

use crate::db::Database;
use crate::db::models::UnresolvedRefRecord;
use module_index::ModuleIndex;
use std::path::Path;

// ---------------------------------------------------------------------------
// ModuleResolver trait
// ---------------------------------------------------------------------------

/// Language-specific module resolver.
///
/// Each implementation understands a single language's module system
/// conventions: how import statements map to file paths, how to derive a
/// module name from a file path, and which names belong to external
/// libraries (stdlib, third-party packages, frameworks).
pub trait ModuleResolver {
    /// Map a module name to candidate file paths (in priority order, relative
    /// to `project_root`).
    ///
    /// For example, in Python `from foo.bar import Baz` would call
    /// `resolve_module("foo.bar", ...)` and return `["foo/bar.py",
    /// "foo/bar/__init__.py"]`.
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String>;

    /// Derive a module name from a file path (the inverse of `resolve_module`).
    ///
    /// Returns `None` if the file does not represent a module (e.g. non-code
    /// files, unknown extension).
    fn file_to_module_name(
        &self,
        file_path: &str,
        project_root: &Path,
    ) -> Option<String>;

    /// Returns `true` if `module_name` is an external dependency (stdlib or
    /// well-known third-party package).  External references are **not**
    /// resolved; they are recorded in `unresolved_refs` with `is_external = 1`.
    fn is_external(&self, module_name: &str) -> bool;

    /// The language identifier this resolver handles (e.g. `"python"`).
    fn language(&self) -> &'static str;
}

// ---------------------------------------------------------------------------
// Resolution statistics
// ---------------------------------------------------------------------------

/// Statistics returned by [`resolve`].
#[derive(Debug, Clone, Default)]
pub struct ResolveStats {
    /// Number of dangling edges that were successfully resolved.
    pub resolved: usize,
    /// Number of dangling edges that could not be resolved (written to
    /// `unresolved_refs` as internal).
    pub unresolved: usize,
    /// Number of dangling edges whose target was already a valid node (no-op).
    pub already_valid: usize,
    /// Number of dangling edges that refer to external dependencies.
    pub external: usize,
}

impl std::fmt::Display for ResolveStats {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Resolved: {}, Unresolved: {}, Already valid: {}, External: {}",
            self.resolved, self.unresolved, self.already_valid, self.external
        )
    }
}

// ---------------------------------------------------------------------------
// Main resolution engine
// ---------------------------------------------------------------------------

/// Run the cross-file reference resolution pass.
///
/// # Process
///
/// 1. Build a [`ModuleIndex`] from all distinct file paths in `nodes`.
/// 2. Find dangling edges: edges whose `target` does not correspond to any
///    existing node id.
/// 3. For each dangling edge:
///    a. Parse `target_text` to extract module name and symbol name.
///    b. Use the source file's language to find the appropriate resolver.
///    c. Resolve the module to candidate file paths.
///    d. Search for a matching symbol in the candidate files.
///    e. If found → update `edges.target` to the real node id, set
///       `provenance = 'resolved'`.
///    f. If not found → check `is_external` → insert into `unresolved_refs`.
/// 4. Return [`ResolveStats`].
pub fn resolve(db: &Database, project_root: &Path) -> anyhow::Result<ResolveStats> {
    let conn = db.connection();
    let index = ModuleIndex::build(db)?;

    // Step 2: find dangling edges.
    // An edge is "dangling" when its `target` value does not exist as an `id`
    // in the `nodes` table.
    let mut stmt = conn.prepare(
        "SELECT e.rowid, e.source, e.target, e.target_text, e.kind, e.source_loc \
         FROM edges e \
         WHERE e.target NOT IN (SELECT id FROM nodes)",
    )?;

    let edges: Vec<DanglingEdge> = stmt
        .query_map([], |row| {
            Ok(DanglingEdge {
                rowid: row.get(0)?,
                source: row.get(1)?,
                target: row.get(2)?,
                target_text: row.get(3)?,
                kind: row.get(4)?,
                source_loc: row.get(5)?,
            })
        })?
        .filter_map(|r| r.ok())
        .filter(|e| e.target_text.is_some()) // skip edges without target_text
        .collect();

    let mut stats = ResolveStats::default();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    for edge in &edges {
        let target_text = edge.target_text.as_ref().unwrap();

        // Step 3a: parse target_text → (module_name, symbol_name)
        let parsed = parse_target_text(target_text);
        let (module_part, symbol_name) = match parsed {
            Some(p) => p,
            None => {
                // Cannot parse → treat as unresolved
                record_unresolved(conn, edge, target_text, "internal", ts)?;
                stats.unresolved += 1;
                continue;
            }
        };

        // Step 3b: find resolver for the source file's language
        let source_lang = get_node_language(conn, &edge.source).unwrap_or_default();
        let resolver = language::LanguageRegistry::get(&source_lang);
        let resolver: Box<dyn ModuleResolver> = match resolver {
            Some(r) => r,
            None => {
                // No resolver for this language → record as unresolved
                record_unresolved(conn, edge, target_text, "internal", ts)?;
                stats.unresolved += 1;
                continue;
            }
        };

        // Step 3c: resolve module to candidate files
        let source_file = get_node_file_path(conn, &edge.source).unwrap_or_default();
        let candidates = resolver.resolve_module(
            &module_part,
            &source_file,
            project_root,
            &index,
        );

        if candidates.is_empty() && !module_part.is_empty() {
            // No candidate file found — check if external
            if resolver.is_external(&module_part) {
                record_unresolved(conn, edge, target_text, "external", ts)?;
                stats.external += 1;
            } else {
                // Try to match by symbol name across all files (fallback)
                if let Some(node_id) = find_node_by_name_in_files(conn, &symbol_name, &[]) {
                    update_edge_target(conn, edge.rowid, &node_id)?;
                    stats.resolved += 1;
                } else {
                    record_unresolved(conn, edge, target_text, "internal", ts)?;
                    stats.unresolved += 1;
                }
            }
            continue;
        }

        // Step 3d-e: search for symbol in candidate files
        let node_id = if symbol_name.is_empty() {
            // No symbol part — just match by module name (e.g. `import foo.bar`)
            find_node_by_module(conn, &module_part, &candidates)
        } else {
            find_node_by_name_in_files(conn, &symbol_name, &candidates)
        };

        match node_id {
            Some(nid) => {
                update_edge_target(conn, edge.rowid, &nid)?;
                stats.resolved += 1;
            }
            None => {
                if resolver.is_external(&module_part) {
                    record_unresolved(conn, edge, target_text, "external", ts)?;
                    stats.external += 1;
                } else {
                    record_unresolved(conn, edge, target_text, "internal", ts)?;
                    stats.unresolved += 1;
                }
            }
        }
    }

    Ok(stats)
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

/// A dangling edge (target node does not exist in `nodes`).
#[allow(dead_code)]
struct DanglingEdge {
    rowid: i64,
    source: String,
    target: String,
    target_text: Option<String>,
    kind: String,
    source_loc: Option<String>,
}

// ---------------------------------------------------------------------------
// Parsing helpers
// ---------------------------------------------------------------------------

/// Parse `target_text` into (module_part, symbol_part).
///
/// For Python-style qualified names like `foo.bar.Baz`, the module part is
/// `foo.bar` and the symbol part is `Baz`.  This heuristic looks for the
/// last dot-separated segment that starts with an uppercase letter as the
/// symbol boundary.  If no uppercase segment is found, the entire string is
/// treated as the module name.
///
/// Also handles `::` separator: `file_path::symbol` → module = `file_path`,
/// symbol = `symbol`.
fn parse_target_text(target_text: &str) -> Option<(String, String)> {
    if target_text.is_empty() {
        return None;
    }

    // Handle :: separator (qualified_name format)
    if let Some(pos) = target_text.rfind("::") {
        let module_part = target_text[..pos].to_string();
        let symbol_part = target_text[pos + 2..].to_string();
        return Some((module_part, symbol_part));
    }

    // Handle . separator: find the boundary between module path and symbol name
    let segments: Vec<&str> = target_text.split('.').collect();
    if segments.len() <= 1 {
        // Single name — could be a module or a simple symbol
        // Treat as symbol-only for generic unqualified references
        return Some((String::new(), segments[0].to_string()));
    }

    // Find the last segment that looks like a symbol (starts uppercase or is
    // single-segment).  For dotted paths, the convention is that module
    // segments are lowercase and class/func names are UpperCamelCase or
    // snake_case.  We use a simple heuristic: the last segment is always the
    // symbol.
    let last = segments.last().unwrap();
    let module_part = segments[..segments.len() - 1].join(".");
    Some((module_part, last.to_string()))
}

// ---------------------------------------------------------------------------
// Database helpers
// ---------------------------------------------------------------------------

/// Get the language of a node by its id.
fn get_node_language(conn: &rusqlite::Connection, node_id: &str) -> Option<String> {
    conn.query_row(
        "SELECT language FROM nodes WHERE id = ?1",
        [node_id],
        |row| row.get(0),
    )
    .ok()
}

/// Get the file path of a node by its id.
fn get_node_file_path(conn: &rusqlite::Connection, node_id: &str) -> Option<String> {
    conn.query_row(
        "SELECT file_path FROM nodes WHERE id = ?1",
        [node_id],
        |row| row.get(0),
    )
    .ok()
}

/// Find a node by `name` within a set of candidate file paths.
///
/// Returns the node's `id` if found, or `None`.
fn find_node_by_name_in_files(
    conn: &rusqlite::Connection,
    name: &str,
    file_paths: &[String],
) -> Option<String> {
    if file_paths.is_empty() {
        // Global search — try name match across all nodes
        return conn
            .query_row(
                "SELECT id FROM nodes WHERE name = ?1 LIMIT 1",
                [name],
                |row| row.get(0),
            )
            .ok();
    }

    // Build IN clause
    let placeholders: Vec<String> = file_paths.iter().enumerate()
        .map(|(i, _)| format!("?{}", i + 2))
        .collect();
    let sql = format!(
        "SELECT id FROM nodes WHERE name = ?1 AND file_path IN ({}) LIMIT 1",
        placeholders.join(", ")
    );

    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![Box::new(name.to_string())];
    for fp in file_paths {
        params.push(Box::new(fp.clone()));
    }

    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    conn.query_row(&sql, param_refs.as_slice(), |row| row.get(0)).ok()
}

/// Find a node that represents a module (kind='module') in candidate files.
fn find_node_by_module(
    conn: &rusqlite::Connection,
    module_name: &str,
    file_paths: &[String],
) -> Option<String> {
    if file_paths.is_empty() {
        return conn
            .query_row(
                "SELECT id FROM nodes WHERE qualified_name LIKE ?1 LIMIT 1",
                [format!("%::{}", module_name)],
                |row| row.get(0),
            )
            .ok();
    }

    let placeholders: Vec<String> = file_paths.iter().enumerate()
        .map(|(i, _)| format!("?{}", i + 2))
        .collect();
    let sql = format!(
        "SELECT id FROM nodes WHERE file_path IN ({}) AND (qualified_name LIKE ?1) LIMIT 1",
        placeholders.join(", ")
    );

    let pattern = format!("%::{}", module_name);
    let mut params: Vec<Box<dyn rusqlite::types::ToSql>> = vec![Box::new(pattern)];
    for fp in file_paths {
        params.push(Box::new(fp.clone()));
    }

    let param_refs: Vec<&dyn rusqlite::types::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    conn.query_row(&sql, param_refs.as_slice(), |row| row.get(0)).ok()
}

/// Update an edge's target to point to a resolved node.
fn update_edge_target(
    conn: &rusqlite::Connection,
    edge_rowid: i64,
    new_target: &str,
) -> rusqlite::Result<()> {
    conn.execute(
        "UPDATE edges SET target = ?1, provenance = 'resolved' WHERE rowid = ?2",
        rusqlite::params![new_target, edge_rowid],
    )?;
    Ok(())
}

/// Record an unresolved reference in the `unresolved_refs` table.
fn record_unresolved(
    conn: &rusqlite::Connection,
    edge: &DanglingEdge,
    ref_name: &str,
    classification: &str,
    ts: i64,
) -> rusqlite::Result<()> {
    let is_external = if classification == "external" { 1 } else { 0 };
    let lang = get_node_language(conn, &edge.source).unwrap_or_default();

    // Parse source_loc to get line/col
    let (line, col) = parse_source_loc(edge.source_loc.as_deref());

    conn.execute(
        "INSERT INTO unresolved_refs (from_node_id, reference_name, reference_kind, \
         line, col, candidates, source, file_path, language, is_external) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        rusqlite::params![
            edge.source,
            ref_name,
            edge.kind,
            line,
            col,
            None::<String>,
            Some("resolver"),
            get_node_file_path(conn, &edge.source).unwrap_or_default(),
            lang,
            is_external,
        ],
    )?;
    Ok(())
}

/// Parse `source_loc` into (line, col).  Format: `"file:line:col"`.
fn parse_source_loc(source_loc: Option<&str>) -> (i32, i32) {
    match source_loc {
        Some(loc) => {
            let parts: Vec<&str> = loc.rsplitn(3, ':').collect::<Vec<_>>().into_iter().rev().collect();
            let line = parts.get(1).and_then(|s| s.parse().ok()).unwrap_or(1);
            let col = parts.get(2).and_then(|s| s.parse().ok()).unwrap_or(1);
            (line, col)
        }
        None => (1, 1),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::connection::hash_id;
    use std::path::Path;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_res_{}.db", name));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));

        let db = Database::initialize(&path).unwrap();
        (db, path)
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn insert_node(db: &Database, name: &str, qualified: &str, file_path: &str, lang: &str, kind: &str) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, 1, ?7)",
            rusqlite::params![id, kind, name, qualified, file_path, lang, ts],
        )
        .unwrap();
        id
    }

    fn insert_edge(db: &Database, source: &str, target: &str, target_text: &str, kind: &str) {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO edges (source, target, target_text, kind, source_loc, provenance) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![source, target, target_text, kind, Some("src/test.py:10:5"), Some("tree-sitter")],
        )
        .unwrap();
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // parse_target_text
    // ------------------------------------------------------------------

    #[test]
    fn test_parse_target_with_double_colon() {
        let result = parse_target_text("src/utils.py::helper_func");
        assert!(result.is_some());
        let (module, symbol) = result.unwrap();
        assert_eq!(module, "src/utils.py");
        assert_eq!(symbol, "helper_func");
    }

    #[test]
    fn test_parse_target_dotted_module_symbol() {
        let result = parse_target_text("foo.bar.Baz");
        assert!(result.is_some());
        let (module, symbol) = result.unwrap();
        assert_eq!(module, "foo.bar");
        assert_eq!(symbol, "Baz");
    }

    #[test]
    fn test_parse_target_single_name() {
        let result = parse_target_text("my_func");
        assert!(result.is_some());
        let (module, symbol) = result.unwrap();
        assert_eq!(module, "");
        assert_eq!(symbol, "my_func");
    }

    #[test]
    fn test_parse_target_empty() {
        assert!(parse_target_text("").is_none());
    }

    // ------------------------------------------------------------------
    // resolve engine
    // ------------------------------------------------------------------

    #[test]
    fn test_resolve_resolvable_edge() {
        let (db, path) = setup_db("resolve_ok");

        // Source node in src/main.py
        let src_id = insert_node(&db, "main", "src.main::main", "src/main.py", "python", "function");
        // Target node in src/utils.py (the symbol being called)
        let tgt_id = insert_node(&db, "helper", "src.utils::helper", "src/utils.py", "python", "function");

        // Insert a dangling edge: src/main.py::main calls helper,
        // but the edge target is a hash that does NOT match any node.
        let fake_target = hash_id("nonexistent.py", "nonexistent::fake");
        // The target_text contains the qualified name we can parse.
        insert_edge(&db, &src_id, &fake_target, "src.utils::helper", "CALLS");

        let stats = resolve(&db, Path::new(".")).unwrap();
        assert_eq!(stats.resolved, 1);
        assert_eq!(stats.unresolved, 0);

        // Verify edge was updated
        let conn = db.connection();
        let updated_target: String = conn
            .query_row(
                "SELECT target FROM edges WHERE source = ?1",
                [&src_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(updated_target, tgt_id);

        // Verify provenance
        let provenance: String = conn
            .query_row(
                "SELECT provenance FROM edges WHERE source = ?1",
                [&src_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(provenance, "resolved");

        cleanup(&path);
    }

    #[test]
    fn test_resolve_external_reference() {
        let (db, path) = setup_db("resolve_ext");

        let src_id = insert_node(&db, "main", "src.main::main", "src/main.py", "python", "function");
        let fake_target = hash_id("nonexistent.py", "nonexistent::os_path_join");
        insert_edge(&db, &src_id, &fake_target, "os.path.join", "IMPORTS");

        let stats = resolve(&db, Path::new(".")).unwrap();
        assert_eq!(stats.resolved, 0);
        assert_eq!(stats.external, 1);

        // Verify unresolved_refs table
        let conn = db.connection();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM unresolved_refs", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 1);

        let is_ext: i32 = conn
            .query_row("SELECT is_external FROM unresolved_refs LIMIT 1", [], |row| row.get(0))
            .unwrap();
        assert_eq!(is_ext, 1);

        cleanup(&path);
    }

    #[test]
    fn test_resolve_internal_unresolved() {
        let (db, path) = setup_db("resolve_int_unresolved");

        let src_id = insert_node(&db, "main", "src.main::main", "src/main.py", "python", "function");
        // No matching node in the DB for this symbol
        let fake_target = hash_id("nonexistent.py", "nonexistent::my_internal_lib_configure");
        insert_edge(&db, &src_id, &fake_target, "my_internal_lib.configure", "CALLS");

        let stats = resolve(&db, Path::new(".")).unwrap();
        assert_eq!(stats.unresolved, 1);

        // Verify unresolved_refs has an entry with is_external = 0
        let conn = db.connection();
        let is_ext: i32 = conn
            .query_row(
                "SELECT is_external FROM unresolved_refs WHERE from_node_id = ?1",
                [&src_id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(is_ext, 0);

        cleanup(&path);
    }

    #[test]
    fn test_resolve_unsupported_language_graceful() {
        let (db, path) = setup_db("resolve_unsupported_lang");

        // Insert a typescript node (no resolver yet)
        let src_id = insert_node(&db, "App", "src.components::App", "src/components/App.tsx", "typescript", "class");
        let fake_target = hash_id("unknown.ts", "unknown::Header");
        insert_edge(&db, &src_id, &fake_target, "Header", "IMPORTS");

        let stats = resolve(&db, Path::new(".")).unwrap();
        // No resolver for typescript → should go to unresolved
        assert_eq!(stats.unresolved, 1);

        cleanup(&path);
    }

    #[test]
    fn test_resolve_empty_graph() {
        let (db, path) = setup_db("resolve_empty");
        let stats = resolve(&db, Path::new(".")).unwrap();
        assert_eq!(stats.resolved, 0);
        assert_eq!(stats.unresolved, 0);
        assert_eq!(stats.external, 0);
        cleanup(&path);
    }

    #[test]
    fn test_resolve_stats_display() {
        let stats = ResolveStats {
            resolved: 5,
            unresolved: 3,
            already_valid: 0,
            external: 2,
        };
        let s = format!("{}", stats);
        assert!(s.contains("Resolved: 5"));
        assert!(s.contains("Unresolved: 3"));
        assert!(s.contains("External: 2"));
    }

    #[test]
    fn test_parse_source_loc() {
        assert_eq!(parse_source_loc(Some("src/main.py:10:5")), (10, 5));
        assert_eq!(parse_source_loc(Some("src/main.py:42:99")), (42, 99));
        assert_eq!(parse_source_loc(None), (1, 1));
        assert_eq!(parse_source_loc(Some("bad_format")), (1, 1));
    }
}
