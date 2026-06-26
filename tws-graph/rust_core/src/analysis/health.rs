//! Code health scoring — aggregates coverage, complexity, coupling, and dead code
//! into a single health score per file.
//!
//! The score is a weighted average:
//!   score = 0.35 * coverage + 0.25 * (1 - norm_complexity) + 0.25 * (1 - coupling) +
//!           0.15 * (1 - dead_code_ratio)

use crate::db::Database;
use std::collections::{HashMap, HashSet};

/// Compute health scores for all files in the codebase.
///
/// Returns `Vec<(file_path, score, coverage, avg_complexity, coupling, dead_code_ratio)>`
/// sorted by score ascending (worst first).
pub fn compute_health(
    db: &Database,
) -> Vec<(String, f64, f64, f64, f64, f64)> {
    let conn = db.connection();

    // Get all unique file paths
    let mut stmt = conn
        .prepare("SELECT DISTINCT file_path FROM nodes")
        .expect("failed to prepare file list query");
    let file_paths: Vec<String> = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("failed to query file paths")
        .filter_map(|r| r.ok())
        .collect();

    if file_paths.is_empty() {
        return Vec::new();
    }

    // Pre-compute per-file stats: (node_count, dead_count)
    let mut file_node_count: HashMap<String, usize> = HashMap::new();
    let mut file_dead_count: HashMap<String, usize> = HashMap::new();

    let mut node_stmt = conn
        .prepare("SELECT id, file_path, name, kind FROM nodes")
        .expect("failed to prepare nodes query");
    let all_nodes: Vec<(String, String, String, String)> = node_stmt
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

    // Build set of node IDs with inbound references (for dead code detection)
    let mut has_inbound: HashSet<String> = HashSet::new();
    let mut edge_stmt = conn
        .prepare("SELECT target FROM edges WHERE kind IN ('CALLS', 'REFERENCES')")
        .expect("failed to prepare edges query");
    let targets: Vec<String> = edge_stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();
    for t in targets {
        has_inbound.insert(t);
    }

    for (id, fp, name, kind) in &all_nodes {
        *file_node_count.entry(fp.clone()).or_default() += 1;

        // Simple dead code detection: no inbound, not "main"
        let is_dead = !has_inbound.contains(id)
            && name != "main"
            && !kind.contains("file")
            && !kind.contains("module");
        if is_dead {
            *file_dead_count.entry(fp.clone()).or_default() += 1;
        }
    }

    // Compute coupling: for each file, ratio of external edges
    let mut file_external_edges: HashMap<String, usize> = HashMap::new();
    let mut file_internal_edges: HashMap<String, usize> = HashMap::new();

    // Build node_id -> file_path map
    let node_file: HashMap<String, String> = all_nodes
        .iter()
        .map(|(id, fp, _, _)| (id.clone(), fp.clone()))
        .collect();

    let mut all_edges_stmt = conn
        .prepare("SELECT source, target FROM edges")
        .expect("failed to prepare all edges query");
    let all_edges: Vec<(String, String)> = all_edges_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query all edges")
        .filter_map(|r| r.ok())
        .collect();

    for (src, tgt) in &all_edges {
        let src_file = node_file.get(src).cloned().unwrap_or_default();
        let tgt_file = node_file.get(tgt).cloned().unwrap_or_default();

        if src_file == tgt_file {
            *file_internal_edges.entry(src_file).or_default() += 1;
        } else {
            *file_external_edges.entry(src_file).or_default() += 1;
        }
    }

    // Estimate test coverage per file
    let mut file_test_coverage: HashMap<String, f64> = HashMap::new();

    // Simple heuristic: look for test files that contain the target file name
    for fp in &file_paths {
        let base_name = std::path::Path::new(fp)
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();

        // Check if a corresponding test file exists
        let test_file_candidates: Vec<String> = file_paths
            .iter()
            .filter(|other| {
                let other_name = std::path::Path::new(other.as_str())
                    .file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_default();
                // test_file.py matches file_test.py, test.py, etc.
                other_name.contains("test")
                    && (other_name.contains(&base_name) || base_name.contains("test"))
            })
            .cloned()
            .collect();

        if !test_file_candidates.is_empty() {
            file_test_coverage.insert(fp.clone(), 1.0);
        } else {
            file_test_coverage.insert(fp.clone(), 0.0);
        }
    }

    // Assemble results
    let mut results: Vec<(String, f64, f64, f64, f64, f64)> = Vec::new();

    for fp in &file_paths {
        let total = *file_node_count.get(fp).unwrap_or(&0) as f64;
        if total == 0.0 {
            continue;
        }

        // Coverage: 0.0 or 1.0 (binary heuristic)
        let coverage = *file_test_coverage.get(fp).unwrap_or(&0.0);

        // Complexity: estimate from node count (more nodes = more complexity)
        // Normalize: cap at 50 nodes, then invert so 1.0 = simple
        let raw_complexity = (total / 50.0).min(1.0);
        let norm_complexity = 1.0 - raw_complexity;

        // Coupling: external edges ratio, 0.0 = fully decoupled
        let ext = *file_external_edges.get(fp).unwrap_or(&0) as f64;
        let int = *file_internal_edges.get(fp).unwrap_or(&0) as f64;
        let total_edges = ext + int;
        let coupling = if total_edges > 0.0 {
            ext / total_edges
        } else {
            0.0
        };
        let norm_coupling = 1.0 - coupling;

        // Dead code ratio
        let dead = *file_dead_count.get(fp).unwrap_or(&0) as f64;
        let dead_ratio = if total > 0.0 { dead / total } else { 0.0 };
        let norm_dead = 1.0 - dead_ratio;

        // Weighted score
        let score = 0.35 * coverage + 0.25 * norm_complexity + 0.25 * norm_coupling
            + 0.15 * norm_dead;

        results.push((fp.clone(), score, coverage, raw_complexity, coupling, dead_ratio));
    }

    // Sort by score ascending (worst first)
    results.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
    results
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
        let path = std::env::temp_dir().join(format!("tws_health_test_{}.db", name));
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
        kind: &str,
    ) -> String {
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
    fn test_empty_database() {
        let (db, path) = setup_db("empty_health");
        let scores = compute_health(&db);
        assert!(scores.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_single_file_all_healthy() {
        let (db, path) = setup_db("all_healthy");
        let conn = db.connection();

        let a = insert_node(conn, "func_a", "src/module.py", "function");
        let b = insert_node(conn, "func_b", "src/module.py", "function");
        // Both call each other (internal edges)
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &a, "CALLS");
        // Test file exists
        insert_node(conn, "test_func", "tests/test_module.py", "function");

        let scores = compute_health(&db);
        assert_eq!(scores.len(), 2, "Expected 2 files, got {:?}", scores);

        // Main file should have high health (has test, internal edges only)
        let main_score = scores.iter().find(|(f, _, _, _, _, _)| f == "src/module.py").unwrap();
        assert!(main_score.1 > 0.5, "Healthy file should have score > 0.5, got {}", main_score.1);
        assert!((main_score.2 - 1.0).abs() < 0.01, "Should have test coverage = 1.0");

        cleanup(&path);
    }

    #[test]
    fn test_file_with_dead_code() {
        let (db, path) = setup_db("dead_code_health");
        let conn = db.connection();

        let used = insert_node(conn, "used_func", "src/lib.py", "function");
        let dead = insert_node(conn, "dead_func", "src/lib.py", "function");
        // Only used_func has an inbound reference from itself
        insert_edge(conn, &used, &used, "CALLS");

        let scores = compute_health(&db);
        let lib_score = scores.iter().find(|(f, _, _, _, _, _)| f == "src/lib.py").unwrap();
        // dead_ratio should be 0.5 (1 of 2 nodes is dead)
        assert!((lib_score.5 - 0.5).abs() < 0.01, "Dead ratio should be 0.5, got {}", lib_score.5);

        cleanup(&path);
    }

    #[test]
    fn test_scores_sorted_worst_first() {
        let (db, path) = setup_db("sorted_health");
        let conn = db.connection();

        // Healthy file: has tests, internal calls
        let a1 = insert_node(conn, "fn1", "src/healthy.py", "function");
        let a2 = insert_node(conn, "fn2", "src/healthy.py", "function");
        insert_edge(conn, &a1, &a2, "CALLS");
        insert_edge(conn, &a2, &a1, "CALLS");
        insert_node(conn, "test_healthy", "tests/test_healthy.py", "function");

        // Unhealthy file: dead code, external coupling, no tests
        let b1 = insert_node(conn, "fn3", "src/unhealthy.py", "function");
        let ext = insert_node(conn, "ext_lib", "vendor/external.py", "function");
        insert_edge(conn, &b1, &ext, "CALLS");

        let scores = compute_health(&db);
        assert!(scores.len() >= 2);

        // First item should be the worst (lowest score)
        let first_score = scores[0].1;
        let last_score = scores.last().unwrap().1;
        assert!(first_score <= last_score, "Scores should be sorted worst-first");

        cleanup(&path);
    }

    #[test]
    fn test_multiple_files() {
        let (db, path) = setup_db("multi_health");
        let conn = db.connection();

        // File 1
        insert_node(conn, "f1", "src/file1.py", "function");
        // File 2
        insert_node(conn, "f2", "src/file2.py", "function");
        // File 3
        insert_node(conn, "f3", "src/file3.py", "function");

        let scores = compute_health(&db);
        assert_eq!(scores.len(), 3);

        for (fp, score, cov, _, _, _) in &scores {
            assert!(*score >= 0.0 && *score <= 1.0, "Score out of range for {}: {}", fp, score);
            assert!(*cov >= 0.0 && *cov <= 1.0, "Coverage out of range for {}: {}", fp, cov);
        }

        cleanup(&path);
    }
}
