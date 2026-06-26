//! Impact prediction — estimates the blast radius and risk of changing a symbol.
//!
//! Uses BFS traversal from the given symbol to compute the impact radius, risk level,
//! affected files, and estimated churn.

use crate::db::Database;
use std::collections::{HashMap, HashSet, VecDeque};

/// Predicted impact of changing a code symbol.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ImpactPrediction {
    /// Maximum BFS depth (impact radius).
    pub radius: usize,
    /// Risk level: "low", "medium", "high", or "critical".
    pub risk_level: String,
    /// File paths affected by the change.
    pub affected_files: Vec<String>,
    /// Estimated number of nodes in the impact closure.
    pub estimated_churn: usize,
}

/// Predict the impact of changing a given symbol (matched by name or qualified name).
///
/// Returns an `ImpactPrediction` with radius, risk_level, affected_files, and
/// estimated_churn. If the symbol is not found, returns default values.
pub fn predict_impact(db: &Database, node_name: &str) -> ImpactPrediction {
    let conn = db.connection();

    // Find the target node by name or qualified_name
    let mut stmt = conn
        .prepare("SELECT id, name, file_path FROM nodes WHERE name = ?1 OR qualified_name = ?1")
        .expect("failed to prepare node lookup");
    let matching: Vec<(String, String, String)> = stmt
        .query_map([node_name], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    if matching.is_empty() {
        return ImpactPrediction::default();
    }

    // Use the first match
    let (node_id, _name, start_file) = &matching[0];

    // Build adjacency graph (outbound CALLS edges for forward impact)
    let mut adj: HashMap<String, Vec<String>> = HashMap::new();
    let mut edge_stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = edge_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    // Also load all node_id -> file_path mappings
    let mut node_file: HashMap<String, String> = HashMap::new();
    let mut file_stmt = conn
        .prepare("SELECT id, file_path FROM nodes")
        .expect("failed to prepare file map");
    let all_files: Vec<(String, String)> = file_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query file map")
        .filter_map(|r| r.ok())
        .collect();
    for (nid, fp) in &all_files {
        node_file.insert(nid.clone(), fp.clone());
        adj.entry(nid.clone()).or_default();
    }

    for (src, tgt) in &edges {
        adj.entry(src.clone()).or_default().push(tgt.clone());
        // Ensure target node exists in adjacency too
        adj.entry(tgt.clone()).or_default();
    }

    // BFS from the target node (outbound traversal)
    let mut visited: HashSet<String> = HashSet::new();
    let mut max_depth: usize = 0;
    let mut total_nodes: usize = 0;
    let mut affected_files_set: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();

    queue.push_back((node_id.clone(), 0));
    visited.insert(node_id.clone());

    while let Some((current, depth)) = queue.pop_front() {
        max_depth = depth;
        total_nodes += 1;

        // Track affected file
        if let Some(fp) = node_file.get(&current) {
            affected_files_set.insert(fp.clone());
        }

        if let Some(neighbors) = adj.get(&current) {
            for neighbor in neighbors {
                if !visited.contains(neighbor) {
                    visited.insert(neighbor.clone());
                    queue.push_back((neighbor.clone(), depth + 1));
                }
            }
        }
    }

    // Determine risk level
    let risk_level = match max_depth {
        0 => "low",
        1..=3 => "medium",
        4..=10 => "high",
        _ => "critical",
    };

    let mut affected_files: Vec<String> = affected_files_set.into_iter().collect();
    affected_files.sort();

    ImpactPrediction {
        radius: max_depth,
        risk_level: risk_level.to_string(),
        affected_files,
        estimated_churn: total_nodes,
    }
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
        let path = std::env::temp_dir().join(format!("tws_impact_test_{}.db", name));
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
    ) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, 'CALLS')",
            params![src, tgt],
        )
        .unwrap();
    }

    #[test]
    fn test_symbol_not_found() {
        let (db, path) = setup_db("not_found");
        let pred = predict_impact(&db, "nonexistent_func");
        assert_eq!(pred.radius, 0);
        assert_eq!(pred.risk_level, "");
        assert!(pred.affected_files.is_empty());
        assert_eq!(pred.estimated_churn, 0);
        cleanup(&path);
    }

    #[test]
    fn test_isolated_node_low_risk() {
        let (db, path) = setup_db("isolated_low");
        let conn = db.connection();

        insert_node(conn, "isolated_func", "src/solo.py");

        let pred = predict_impact(&db, "isolated_func");
        assert_eq!(pred.radius, 0);
        assert_eq!(pred.risk_level, "low");
        assert_eq!(pred.estimated_churn, 1);
        assert_eq!(pred.affected_files.len(), 1);

        cleanup(&path);
    }

    #[test]
    fn test_shallow_chain_medium_risk() {
        let (db, path) = setup_db("shallow_medium");
        let conn = db.connection();

        // a -> b -> c (radius 2)
        let a = insert_node(conn, "entry", "src/a.py");
        let b = insert_node(conn, "helper1", "src/b.py");
        let c = insert_node(conn, "helper2", "src/b.py");
        insert_edge(conn, &a, &b);
        insert_edge(conn, &b, &c);

        let pred = predict_impact(&db, "entry");
        assert_eq!(pred.radius, 2);
        assert_eq!(pred.risk_level, "medium");
        assert_eq!(pred.estimated_churn, 3);
        // Affected files: a.py, b.py
        assert_eq!(pred.affected_files.len(), 2);

        cleanup(&path);
    }

    #[test]
    fn test_deep_chain_high_risk() {
        let (db, path) = setup_db("deep_high");
        let conn = db.connection();

        // Chain of 6 nodes (radius 5)
        let mut prev = insert_node(conn, "node_0", "src/layer0.py");
        for i in 1..6 {
            let next = insert_node(conn, &format!("node_{}", i), &format!("src/layer{}.py", i));
            insert_edge(conn, &prev, &next);
            prev = next;
        }

        let pred = predict_impact(&db, "node_0");
        assert_eq!(pred.radius, 5);
        assert_eq!(pred.risk_level, "high");
        assert_eq!(pred.estimated_churn, 6);
        assert_eq!(pred.affected_files.len(), 6);

        cleanup(&path);
    }

    #[test]
    fn test_wide_tree_critical_risk() {
        let (db, path) = setup_db("wide_critical");
        let conn = db.connection();

        // Root calls 12 children (radius 1 but 13 total nodes)
        let root = insert_node(conn, "root", "src/root.py");
        for i in 0..12 {
            let child = insert_node(conn, &format!("child_{}", i), &format!("src/child{}.py", i));
            insert_edge(conn, &root, &child);
        }

        let pred = predict_impact(&db, "root");
        // radius = 1 but estimated_churn = 13
        assert_eq!(pred.radius, 1);
        assert_eq!(pred.estimated_churn, 13);
        assert_eq!(pred.affected_files.len(), 13);

        // For very deep: > 10
        let mut prev = root;
        for i in 0..12 {
            let next = insert_node(conn, &format!("deep_{}", i), "src/deep.py");
            insert_edge(conn, &prev, &next);
            prev = next;
        }

        let pred2 = predict_impact(&db, "root");
        // Now radius should be 12
        assert_eq!(pred2.risk_level, "critical");
        assert!(pred2.radius > 10);

        cleanup(&path);
    }
}
