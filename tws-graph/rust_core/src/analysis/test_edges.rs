//! Test edge detection — finds links between test code and production code.
//!
//! Uses three strategies:
//! 1. Naming convention (test_xxx -> xxx) — confidence 0.9
//! 2. Import analysis (test file imports from source module) — confidence 0.8
//! 3. Call graph (test function calls source function) — confidence 0.5

use crate::db::Database;
use std::collections::{HashMap, HashSet};

/// Find test-to-production-code linkages.
///
/// Returns `(code_id, code_name, test_id, test_name, confidence)` tuples sorted
/// by confidence descending.
pub fn find_test_edges(
    db: &Database,
) -> Vec<(String, String, String, String, f64)> {
    let conn = db.connection();

    // Load all function/method nodes
    let mut stmt = conn
        .prepare("SELECT id, name, qualified_name, file_path FROM nodes WHERE kind IN ('function', 'method')")
        .expect("failed to prepare nodes query");
    let all_nodes: Vec<(String, String, String, String)> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    // Separate into test and non-test nodes
    let test_nodes: Vec<(String, String, String, String)> = all_nodes
        .iter()
        .filter(|(_, _, _, fp)| is_test_file(fp))
        .cloned()
        .collect();

    let prod_nodes: Vec<(String, String, String, String)> = all_nodes
        .iter()
        .filter(|(_, _, _, fp)| !is_test_file(fp))
        .cloned()
        .collect();

    if test_nodes.is_empty() || prod_nodes.is_empty() {
        return Vec::new();
    }

    // Load imports edges
    let mut imports: HashMap<String, HashSet<String>> = HashMap::new();
    let mut edge_stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'IMPORTS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = edge_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();
    for (src, tgt) in &edges {
        imports.entry(src.clone()).or_default().insert(tgt.clone());
    }

    // Build node_id -> file_path lookup
    let mut node_file: HashMap<String, String> = HashMap::new();
    for (id, _, _, fp) in all_nodes.iter() {
        node_file.insert(id.clone(), fp.clone());
    }

    // Load calls edges
    let mut calls_map: HashMap<String, HashSet<String>> = HashMap::new();
    let mut call_stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare calls query");
    let calls: Vec<(String, String)> = call_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query calls")
        .filter_map(|r| r.ok())
        .collect();
    for (src, tgt) in &calls {
        calls_map.entry(src.clone()).or_default().insert(tgt.clone());
    }

    let mut results: Vec<(String, String, String, String, f64)> = Vec::new();
    let mut seen: HashSet<(String, String)> = HashSet::new();

    for (test_id, test_name, test_qn, test_file) in &test_nodes {
        for (prod_id, prod_name, _prod_qn, prod_file) in &prod_nodes {
            let key = (test_id.clone(), prod_id.clone());
            if seen.contains(&key) {
                continue;
            }

            let mut confidence = 0.0f64;

            // Strategy 1: Naming convention (highest confidence)
            if is_naming_match(test_name, prod_name) {
                confidence = 0.9;
            }

            // Strategy 2: Import analysis
            // Check if test node's file imports from prod node's file
            if confidence < 0.9 {
                let test_file_imports = imports.get(test_id);
                if let Some(import_targets) = test_file_imports {
                    // Check if any import target is in the same file as the prod node
                    for import_target in import_targets {
                        if let Some(imported_file) = node_file.get(import_target) {
                            if imported_file == prod_file {
                                confidence = confidence.max(0.8);
                                break;
                            }
                        }
                    }
                }
            }

            // Strategy 3: Call graph (lowest confidence)
            if confidence < 0.8 {
                let test_calls = calls_map.get(test_id);
                if let Some(callees) = test_calls {
                    if callees.contains(prod_id) {
                        confidence = confidence.max(0.5);
                    }
                }
            }

            if confidence > 0.0 {
                results.push((
                    prod_id.clone(),
                    prod_name.clone(),
                    test_id.clone(),
                    test_name.clone(),
                    confidence,
                ));
                seen.insert(key);
            }
        }
    }

    results.sort_by(|a, b| b.4.partial_cmp(&a.4).unwrap_or(std::cmp::Ordering::Equal));
    results
}

/// Check if a file path looks like a test file.
fn is_test_file(path: &str) -> bool {
    let lower = path.to_lowercase().replace('\\', "/");
    lower.contains("test") || lower.contains("_test") || lower.contains("spec")
        || lower.contains("__test__")
}

/// Check if a test name matches a production name via convention.
///
/// Matches: test_xxx -> xxx, xxx_test -> xxx, TestXxx -> Xxx, SpecXxx -> Xxx
fn is_naming_match(test_name: &str, prod_name: &str) -> bool {
    let test_lower = test_name.to_lowercase();
    let prod_lower = prod_name.to_lowercase();

    // Direct prefix/suffix matches
    if test_lower == format!("test_{}", prod_lower) {
        return true;
    }
    if test_lower == format!("test{}", prod_lower) {
        return true;
    }
    if test_lower == format!("spec_{}", prod_lower) {
        return true;
    }
    if test_lower == format!("{}_test", prod_lower) {
        return true;
    }
    // Test prefix with underscore
    if test_lower.starts_with("test_") {
        let remainder = &test_lower[5..];
        if remainder == prod_lower {
            return true;
        }
    }
    // TestXxx -> xxx (case insensitive after stripping test/spec prefix)
    if test_lower.starts_with("test") {
        let remainder = &test_lower[4..];
        if remainder.to_lowercase() == prod_lower {
            return true;
        }
    }

    false
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

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
        let path = std::env::temp_dir().join(format!("tws_testedge_test_{}.db", name));
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

    fn insert_node(
        conn: &rusqlite::Connection,
        name: &str,
        file: &str,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![nid, name, qname, file, "python", 1, 1, ts],
        )
        .unwrap();
        nid
    }

    fn insert_edge(
        conn: &rusqlite::Connection,
        src: &str,
        tgt: &str,
        kind: &str,
    ) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![src, tgt, kind],
        )
        .unwrap();
    }

    #[test]
    fn test_naming_convention_match() {
        let (db, path) = setup_db("naming_match");
        let conn = db.connection();

        let prod = insert_node(conn, "calculate_total", "src/calc.py");
        let test_fn = insert_node(conn, "test_calculate_total", "tests/test_calc.py");

        let edges = find_test_edges(&db);
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].1, "calculate_total");
        assert_eq!(edges[0].3, "test_calculate_total");
        assert!((edges[0].4 - 0.9).abs() < 0.01, "Expected confidence ~0.9, got {}", edges[0].4);

        cleanup(&path);
    }

    #[test]
    fn test_import_analysis_match() {
        let (db, path) = setup_db("import_match");
        let conn = db.connection();

        let prod = insert_node(conn, "helper", "src/utils.py");
        let test_fn = insert_node(conn, "verify_output", "tests/test_app.py");
        // Test file imports from production file (edge from test_fn to helper)
        insert_edge(conn, &test_fn, &prod, "IMPORTS");

        let edges = find_test_edges(&db);
        assert_eq!(edges.len(), 1);
        assert!((edges[0].4 - 0.8).abs() < 0.01, "Expected confidence ~0.8, got {}", edges[0].4);

        cleanup(&path);
    }

    #[test]
    fn test_call_graph_match() {
        let (db, path) = setup_db("call_match");
        let conn = db.connection();

        let prod = insert_node(conn, "compute", "src/engine.py");
        let test_fn = insert_node(conn, "check_result", "tests/test_engine.py");
        insert_edge(conn, &test_fn, &prod, "CALLS");

        let edges = find_test_edges(&db);
        assert_eq!(edges.len(), 1);
        assert!((edges[0].4 - 0.5).abs() < 0.01, "Expected confidence ~0.5, got {}", edges[0].4);

        cleanup(&path);
    }

    #[test]
    fn test_no_test_files() {
        let (db, path) = setup_db("no_tests");
        let conn = db.connection();

        insert_node(conn, "main", "src/main.py");
        insert_node(conn, "helper", "src/utils.py");

        let edges = find_test_edges(&db);
        assert!(edges.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_empty_database() {
        let (db, path) = setup_db("empty_testedge");
        let edges = find_test_edges(&db);
        assert!(edges.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_best_confidence_used() {
        let (db, path) = setup_db("best_confidence");
        let conn = db.connection();

        let prod = insert_node(conn, "authenticate", "src/auth.py");
        // Naming match AND call graph — should use 0.9 (naming)
        let test_fn = insert_node(conn, "test_authenticate", "tests/test_auth.py");
        insert_edge(conn, &test_fn, &prod, "CALLS");

        let edges = find_test_edges(&db);
        assert_eq!(edges.len(), 1);
        assert!((edges[0].4 - 0.9).abs() < 0.01, "Should use highest confidence (0.9 from naming)");

        cleanup(&path);
    }
}
