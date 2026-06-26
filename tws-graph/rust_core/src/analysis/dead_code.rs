//! Dead code detection — identifies symbols with zero inbound CALLS or REFERENCES
//! that are not entry points.
//!
//! A node is considered "dead" if:
//! - in-degree (CALLS + REFERENCES) == 0
//! - AND it is NOT an entry point (not main, not test, not exported, not route handler)

use crate::db::Database;
use std::collections::HashSet;

/// Find potentially dead code.
///
/// Returns a list of `(node_id, name, kind, file_path)` tuples for nodes that
/// appear to be unused.
pub fn find_dead_code(db: &Database) -> Vec<(String, String, String, String)> {
    let conn = db.connection();

    // Get all nodes
    let mut node_stmt = conn
        .prepare("SELECT id, name, kind, file_path, is_exported, language FROM nodes")
        .expect("failed to prepare nodes query");
    let all_nodes: Vec<(String, String, String, String, i32, String)> = node_stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, i32>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    // Build set of node IDs that have inbound CALLS or REFERENCES
    let mut has_inbound: HashSet<String> = HashSet::new();

    let mut edge_stmt = conn
        .prepare("SELECT target FROM edges WHERE kind IN ('CALLS', 'REFERENCES')")
        .expect("failed to prepare edges query");
    let targets: Vec<String> = edge_stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    for target in targets {
        has_inbound.insert(target);
    }

    // Filter: dead if no inbound AND not excluded
    all_nodes
        .into_iter()
        .filter(|(id, name, kind, file_path, is_exported, language)| {
            // Has inbound references, not dead
            if has_inbound.contains(id) {
                return false;
            }

            // Skip entry points and special cases
            if is_excluded(name, kind, file_path, *is_exported, language) {
                return false;
            }

            true
        })
        .map(|(id, name, kind, file_path, _, _)| (id, name, kind, file_path))
        .collect()
}

/// Check whether a node should be excluded from dead code detection.
fn is_excluded(name: &str, kind: &str, file_path: &str, is_exported: i32, language: &str) -> bool {
    // Skip test files
    if file_path.contains("test") || file_path.contains("__test__") {
        return true;
    }

    // Skip __init__.py files
    if file_path.ends_with("__init__.py") {
        return true;
    }

    // Skip main functions
    let name_lower = name.to_lowercase();
    if name_lower == "main" || name_lower.contains("main") && kind == "function" {
        return true;
    }

    // Skip route handlers (naming convention heuristics)
    if name_lower.starts_with("handle_")
        || name_lower.ends_with("_handler")
        || name_lower.ends_with("_route")
        || name_lower.ends_with("_endpoint")
    {
        return true;
    }

    // Skip lifecycle hooks
    if name_lower == "startup" || name_lower == "shutdown" || name_lower == "init" {
        return true;
    }

    // Skip exported symbols (library public API)
    if is_exported == 1 {
        return true;
    }

    // Skip __main__ or test_ prefixed
    if name.starts_with("__") && name.ends_with("__") {
        return true;
    }
    if name.starts_with("test_") || name.ends_with("_test") {
        return true;
    }

    // Skip variable/constant declarations in certain contexts
    if kind == "variable" || kind == "constant" {
        return true;
    }

    // Skip HTML/CSS/Markdown structural elements
    if kind == "html_element" || kind == "css_rule" || kind == "md_heading" || kind == "md_link" {
        return true;
    }

    let _ = language;

    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::hash_id;
    use rusqlite::params;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_deadcode_test_{}.db", name));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
        let db = Database::initialize(&path).unwrap();
        (db, path)
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    fn insert_node_full(
        conn: &rusqlite::Connection,
        name: &str,
        file: &str,
        kind: &str,
        is_exported: i32,
        lang: &str,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, is_exported, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![nid, kind, name, qname, file, lang, 1, 1, is_exported, ts],
        )
        .unwrap();
        nid
    }

    fn insert_node(conn: &rusqlite::Connection, name: &str, file: &str) -> String {
        insert_node_full(conn, name, file, "function", 0, "python")
    }

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str, kind: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![src, tgt, kind],
        )
        .unwrap();
    }

    #[test]
    fn test_no_dead_code_when_all_used() {
        let (db, path) = setup_db("all_used");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        insert_edge(conn, &a, &b, "CALLS"); // b is called by a

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(dead_names.contains(&"a"), "a should be dead (no inbound calls), dead list: {:?}", dead_names);
        assert!(!dead_names.contains(&"b"), "b should not be dead (called by a)");

        cleanup(&path);
    }

    #[test]
    fn test_main_function_excluded() {
        let (db, path) = setup_db("main_excluded");
        let conn = db.connection();

        let main_fn = insert_node(conn, "main", "src/app.py");
        // main has no inbound references, but should be excluded

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(!dead_names.contains(&"main"), "main should be excluded from dead code");

        cleanup(&path);
    }

    #[test]
    fn test_test_file_excluded() {
        let (db, path) = setup_db("test_excluded");
        let conn = db.connection();

        let test_helper = insert_node(conn, "helper", "tests/test_utils.py");

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(!dead_names.contains(&"helper"), "test file nodes should be excluded");

        cleanup(&path);
    }

    #[test]
    fn test_exported_symbol_excluded() {
        let (db, path) = setup_db("exported_excluded");
        let conn = db.connection();

        let pub_fn = insert_node_full(conn, "public_api", "src/lib.py", "function", 1, "python");

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(!dead_names.contains(&"public_api"), "exported symbols should be excluded");

        cleanup(&path);
    }

    #[test]
    fn test_init_py_excluded() {
        let (db, path) = setup_db("init_excluded");
        let conn = db.connection();

        let init_fn = insert_node(conn, "setup", "src/core/__init__.py");

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(!dead_names.contains(&"setup"), "__init__.py files should be excluded");

        cleanup(&path);
    }

    #[test]
    fn test_truly_dead_code_detected() {
        let (db, path) = setup_db("truly_dead");
        let conn = db.connection();

        // active_fn is called by main -> alive
        // dead_fn is never called -> dead
        let main_fn = insert_node(conn, "main", "src/app.py");
        let active_fn = insert_node(conn, "active_fn", "src/lib.py");
        let dead_fn = insert_node(conn, "dead_fn", "src/lib.py");
        insert_edge(conn, &main_fn, &active_fn, "CALLS");

        let dead = find_dead_code(&db);
        let dead_names: Vec<&str> = dead.iter().map(|(_, n, _, _)| n.as_str()).collect();
        assert!(dead_names.contains(&"dead_fn"), "dead_fn should be detected as dead, dead list: {:?}", dead_names);
        // main is excluded, active_fn has inbound
        assert!(!dead_names.contains(&"active_fn"), "active_fn should not be dead");

        cleanup(&path);
    }
}
