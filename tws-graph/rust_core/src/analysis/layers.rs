//! Layer violation detection — checks whether lower-layer modules depend on
//! higher-layer modules, which violates architectural layering rules.
//!
//! A layer map maps path prefixes (e.g. `"src/core/"`) to layer numbers (0 = lowest).
//! A violation occurs when a lower-layer module (smaller number) depends on a
//! higher-layer module (larger number).

use crate::db::Database;
use std::collections::HashMap;

/// Detect layer violations.
///
/// # Arguments
/// * `db` - The database handle.
/// * `layer_map` - Maps path prefixes to layer numbers. Lower numbers = lower layers.
///
/// # Returns
/// A list of violations: `(source_id, source_name, source_layer, target_id, target_name, target_layer)`.
pub fn detect_layer_violations(
    db: &Database,
    layer_map: &HashMap<String, usize>,
) -> Vec<(String, String, usize, String, String, usize)> {
    if layer_map.is_empty() {
        return Vec::new();
    }

    let conn = db.connection();

    // Get all IMPORTS and CALLS edges
    let mut stmt = conn
        .prepare("SELECT e.source, e.target, n1.name as src_name, n2.name as tgt_name, \
                  n1.file_path as src_file, n2.file_path as tgt_file \
                  FROM edges e \
                  JOIN nodes n1 ON n1.id = e.source \
                  JOIN nodes n2 ON n2.id = e.target \
                  WHERE e.kind IN ('IMPORTS', 'CALLS')")
        .expect("failed to prepare edges query");

    let violations: Vec<(String, String, usize, String, String, usize)> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
            ))
        })
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .filter_map(
            |(src_id, tgt_id, src_name, tgt_name, src_file, tgt_file)| {
                // Find which layer each belongs to
                let src_layer = find_layer(&src_file, layer_map)?;
                let tgt_layer = find_layer(&tgt_file, layer_map)?;

                // Violation: lower layer (smaller number) depends on higher layer (larger number)
                if src_layer < tgt_layer {
                    Some((src_id, src_name, src_layer, tgt_id, tgt_name, tgt_layer))
                } else {
                    None
                }
            },
        )
        .collect();

    violations
}

/// Find the layer number for a file path based on the layer map.
/// Returns `None` if the file does not match any prefix.
fn find_layer(file_path: &str, layer_map: &HashMap<String, usize>) -> Option<usize> {
    layer_map
        .iter()
        .filter(|(prefix, _)| file_path.starts_with(prefix.as_str()))
        .max_by_key(|(prefix, _)| prefix.len())
        .map(|(_, layer)| *layer)
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
        let path = std::env::temp_dir().join(format!("tws_layers_test_{}.db", name));
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

    fn insert_node(conn: &rusqlite::Connection, name: &str, file: &str, kind: &str) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![nid, kind, name, qname, file, "python", 1, 1, ts],
        )
        .unwrap();
        nid
    }

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str, kind: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![src, tgt, kind],
        )
        .unwrap();
    }

    #[test]
    fn test_no_violations_proper_layering() {
        let (db, path) = setup_db("proper_layers");
        let conn = db.connection();

        // src/core/ -> layer 0, src/services/ -> layer 1, src/api/ -> layer 2
        // Proper: api -> services -> core (higher depends on lower, OK)
        let core_fn = insert_node(conn, "core_fn", "src/core/util.py", "function");
        let svc_fn = insert_node(conn, "svc_fn", "src/services/user.py", "function");
        let api_fn = insert_node(conn, "api_fn", "src/api/routes.py", "function");

        insert_edge(conn, &api_fn, &svc_fn, "IMPORTS"); // layer 2 -> 1, OK
        insert_edge(conn, &svc_fn, &core_fn, "CALLS");  // layer 1 -> 0, OK

        let mut layer_map = HashMap::new();
        layer_map.insert("src/core/".to_string(), 0);
        layer_map.insert("src/services/".to_string(), 1);
        layer_map.insert("src/api/".to_string(), 2);

        let violations = detect_layer_violations(&db, &layer_map);
        assert!(violations.is_empty(), "Expected no violations, got {:?}", violations);

        cleanup(&path);
    }

    #[test]
    fn test_violation_lower_depends_on_higher() {
        let (db, path) = setup_db("violation_lower_higher");
        let conn = db.connection();

        // src/core/ -> layer 0, src/api/ -> layer 2
        // Violation: core depends on api (layer 0 -> 2)
        let core_fn = insert_node(conn, "core_fn", "src/core/util.py", "function");
        let api_fn = insert_node(conn, "api_fn", "src/api/routes.py", "function");

        insert_edge(conn, &core_fn, &api_fn, "CALLS"); // layer 0 -> 2, VIOLATION

        let mut layer_map = HashMap::new();
        layer_map.insert("src/core/".to_string(), 0);
        layer_map.insert("src/api/".to_string(), 2);

        let violations = detect_layer_violations(&db, &layer_map);
        assert_eq!(violations.len(), 1, "Expected 1 violation, got {:?}", violations);
        assert_eq!(violations[0].1, "core_fn"); // source name
        assert_eq!(violations[0].4, "api_fn");  // target name
        assert_eq!(violations[0].2, 0); // source layer
        assert_eq!(violations[0].5, 2); // target layer

        cleanup(&path);
    }

    #[test]
    fn test_multiple_violations() {
        let (db, path) = setup_db("multi_violations");
        let conn = db.connection();

        let core_a = insert_node(conn, "core_a", "src/core/a.py", "function");
        let core_b = insert_node(conn, "core_b", "src/core/b.py", "function");
        let svc_fn = insert_node(conn, "svc_fn", "src/services/svc.py", "function");
        let api_fn = insert_node(conn, "api_fn", "src/api/route.py", "function");

        // Two violations: core -> api and core -> services
        insert_edge(conn, &core_a, &api_fn, "IMPORTS");  // 0 -> 2, VIOLATION
        insert_edge(conn, &core_b, &svc_fn, "CALLS");    // 0 -> 1, VIOLATION
        // OK: api -> svc
        insert_edge(conn, &api_fn, &svc_fn, "CALLS");    // 2 -> 1, OK

        let mut layer_map = HashMap::new();
        layer_map.insert("src/core/".to_string(), 0);
        layer_map.insert("src/services/".to_string(), 1);
        layer_map.insert("src/api/".to_string(), 2);

        let violations = detect_layer_violations(&db, &layer_map);
        assert_eq!(violations.len(), 2, "Expected 2 violations, got {:?}", violations);

        cleanup(&path);
    }

    #[test]
    fn test_empty_layer_map() {
        let (db, path) = setup_db("empty_map");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        insert_edge(conn, &a, &b, "CALLS");

        let layer_map = HashMap::new();
        let violations = detect_layer_violations(&db, &layer_map);
        assert!(violations.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_no_violations_same_layer() {
        let (db, path) = setup_db("same_layer");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/core/x.py", "function");
        let b = insert_node(conn, "b", "src/core/y.py", "function");
        insert_edge(conn, &a, &b, "CALLS"); // layer 0 -> 0, OK

        let mut layer_map = HashMap::new();
        layer_map.insert("src/core/".to_string(), 0);

        let violations = detect_layer_violations(&db, &layer_map);
        assert!(violations.is_empty(), "Same-layer deps should not be violations");

        cleanup(&path);
    }

    #[test]
    fn test_find_layer_longest_prefix_match() {
        let mut layer_map = HashMap::new();
        layer_map.insert("src/".to_string(), 1);
        layer_map.insert("src/core/".to_string(), 0);

        // Should match the longer prefix "src/core/" -> layer 0
        assert_eq!(find_layer("src/core/util.py", &layer_map), Some(0));
        // Should match "src/" -> layer 1
        assert_eq!(find_layer("src/api/routes.py", &layer_map), Some(1));
        // No match
        assert_eq!(find_layer("tests/test.py", &layer_map), None);
    }
}
