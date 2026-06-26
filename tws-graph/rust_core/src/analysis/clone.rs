//! Clone detection — finds similar code nodes using token bigram Jaccard similarity.
//!
//! Uses a simplified MinHash-inspired approach: token extraction from node names
//! and signatures, bigram generation, and pairwise Jaccard comparison. For production
//! use, this can be upgraded to full MinHash + LSH with 128 permutations and 16 bands.

use crate::db::Database;
use std::collections::HashSet;

/// Find similar code node pairs above a given similarity threshold.
///
/// Returns `Vec<(node_id_1, name_1, node_id_2, name_2, similarity)>` sorted by
/// similarity descending.
pub fn find_clones(db: &Database, threshold: f64) -> Vec<(String, String, String, String, f64)> {
    let conn = db.connection();

    // Load all function/method nodes
    let mut stmt = conn
        .prepare(
            "SELECT id, name, qualified_name, signature FROM nodes \
             WHERE kind IN ('function', 'method')",
        )
        .expect("failed to prepare nodes query");
    let nodes: Vec<(String, String, String, Option<String>)> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    if nodes.len() < 2 {
        return Vec::new();
    }

    // Pre-compute token sets for each node
    let token_sets: Vec<HashSet<String>> = nodes
        .iter()
        .map(|(_id, name, _qn, sig)| tokenize(name, sig.as_deref()))
        .collect();

    // Compare all pairs
    let mut results: Vec<(String, String, String, String, f64)> = Vec::new();
    let n = nodes.len();
    for i in 0..n {
        for j in (i + 1)..n {
            let sim = jaccard_similarity(&token_sets[i], &token_sets[j]);
            if sim >= threshold {
                results.push((
                    nodes[i].0.clone(),
                    nodes[i].1.clone(),
                    nodes[j].0.clone(),
                    nodes[j].1.clone(),
                    sim,
                ));
            }
        }
    }

    // Sort by similarity descending
    results.sort_by(|a, b| b.4.partial_cmp(&a.4).unwrap_or(std::cmp::Ordering::Equal));
    results
}

/// Extract tokens from a node name and optional signature.
///
/// Splits on non-alphanumeric characters and converts to lowercase.
fn tokenize(name: &str, signature: Option<&str>) -> HashSet<String> {
    let mut tokens = HashSet::new();
    let combined = if let Some(sig) = signature {
        format!("{} {}", name, sig)
    } else {
        name.to_string()
    };
    for token in combined.split(|c: char| !c.is_alphanumeric()) {
        let t = token.to_lowercase();
        if !t.is_empty() && t.len() > 1 {
            tokens.insert(t);
        }
    }
    // Also add bigrams of characters for structural similarity
    let chars: Vec<char> = combined.to_lowercase().chars().filter(|c| c.is_alphanumeric()).collect();
    for window in chars.windows(2) {
        let bigram: String = window.iter().collect();
        tokens.insert(format!("__bigram_{}", bigram));
    }
    tokens
}

/// Compute Jaccard similarity between two token sets.
fn jaccard_similarity(a: &HashSet<String>, b: &HashSet<String>) -> f64 {
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let intersection = a.intersection(b).count();
    let union = a.union(b).count();
    intersection as f64 / union as f64
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
        let path = std::env::temp_dir().join(format!("tws_clone_test_{}.db", name));
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

    fn insert_fn(
        conn: &rusqlite::Connection,
        name: &str,
        file: &str,
        signature: Option<&str>,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, signature, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![nid, name, qname, file, "python", 1, 1, signature, ts],
        )
        .unwrap();
        nid
    }

    #[test]
    fn test_no_clones_with_distinct_nodes() {
        let (db, path) = setup_db("no_clones");
        let conn = db.connection();

        insert_fn(conn, "calculate_taxes", "src/a.py", Some("def calculate_taxes(income) -> float"));
        insert_fn(conn, "render_html", "src/b.py", Some("def render_html(template) -> str"));
        insert_fn(conn, "connect_database", "src/c.py", Some("def connect_database(url) -> Connection"));

        let clones = find_clones(&db, 0.3);
        // These three should not be similar enough
        assert!(clones.is_empty(), "Expected no clones, got {:?}", clones);

        cleanup(&path);
    }

    #[test]
    fn test_identical_nodes_detected() {
        let (db, path) = setup_db("identical_clones");
        let conn = db.connection();

        insert_fn(conn, "calculate_total", "src/a.py", Some("def calculate_total(items) -> float"));
        insert_fn(conn, "calculate_total", "src/b.py", Some("def calculate_total(items) -> float"));

        let clones = find_clones(&db, 0.5);
        assert!(!clones.is_empty(), "Expected clones for identical functions");
        assert_eq!(clones[0].4, 1.0, "Expected similarity of 1.0, got {}", clones[0].4);

        cleanup(&path);
    }

    #[test]
    fn test_similar_functions_detected() {
        let (db, path) = setup_db("similar_clones");
        let conn = db.connection();

        // Same algorithm, different name
        insert_fn(conn, "process_items", "src/a.py", Some("def process_items(data_list) -> list"));
        insert_fn(conn, "process_records", "src/b.py", Some("def process_records(data_list) -> list"));

        let clones = find_clones(&db, 0.2);
        assert!(!clones.is_empty(), "Expected clones for similar functions");

        cleanup(&path);
    }

    #[test]
    fn test_threshold_filters() {
        let (db, path) = setup_db("threshold_filter");
        let conn = db.connection();

        insert_fn(conn, "compute_hash", "src/a.py", Some("def compute_hash(input_str) -> str"));
        insert_fn(conn, "compute_hash", "src/b.py", Some("def compute_hash(input_str) -> str"));
        // This one is very different
        insert_fn(conn, "launch_missile", "src/c.py", Some("def launch_missile(target) -> bool"));

        let all = find_clones(&db, 0.0);
        assert!(!all.is_empty());

        // At high threshold, only the identical pair survives
        let high = find_clones(&db, 0.9);
        assert_eq!(high.len(), 1, "Expected exactly 1 pair at threshold 0.9");

        cleanup(&path);
    }

    #[test]
    fn test_empty_database() {
        let (db, path) = setup_db("empty_db");
        let clones = find_clones(&db, 0.5);
        assert!(clones.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_single_node_no_clones() {
        let (db, path) = setup_db("single_node");
        let conn = db.connection();

        insert_fn(conn, "only_function", "src/main.py", Some("def only_function() -> None"));

        let clones = find_clones(&db, 0.0);
        assert!(clones.is_empty(), "Single node should not produce clones");

        cleanup(&path);
    }
}
