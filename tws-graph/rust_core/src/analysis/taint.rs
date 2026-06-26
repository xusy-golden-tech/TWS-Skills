//! Taint analysis — finds all paths from source nodes to sink nodes.
//!
//! Sources and sinks are specified as node kind filters (e.g. `"ENV_ACCESSES"` sources
//! to `"HTTP_CALLS"` sinks). Uses DFS/BFS to find all paths between them.

use crate::db::Database;
use std::collections::{HashMap, HashSet, VecDeque};

/// Find all paths from source nodes to sink nodes.
///
/// # Arguments
/// * `db` - The database handle.
/// * `sources` - Edge kind names for source relationships (e.g. `["ENV_ACCESSES"]`).
///               Nodes that have outbound edges of these kinds are sources.
/// * `sinks` - Edge kind names for sink relationships (e.g. `["HTTP_CALLS"]`).
///             Nodes that have outbound edges of these kinds are sinks.
///
/// # Returns
/// A list of paths, where each path is a list of `(node_id, node_name)` tuples
/// from source to sink.
pub fn taint_analysis(
    db: &Database,
    sources: &[&str],
    sinks: &[&str],
) -> Vec<Vec<(String, String)>> {
    if sources.is_empty() || sinks.is_empty() {
        return Vec::new();
    }

    let conn = db.connection();

    // Build adjacency list from all CALLS edges
    let mut stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    let mut adj: HashMap<String, Vec<String>> = HashMap::new();
    for (src, tgt) in &edges {
        adj.entry(src.clone()).or_default().push(tgt.clone());
    }

    // Find source nodes: nodes that appear as the source of edges with source kinds
    let source_ids: HashSet<String> = find_nodes_with_edge_kinds(conn, sources);
    // Find sink nodes: nodes that appear as the source of edges with sink kinds
    let sink_ids: HashSet<String> = find_nodes_with_edge_kinds(conn, sinks);

    if source_ids.is_empty() || sink_ids.is_empty() {
        return Vec::new();
    }

    // Build name lookup for all nodes in the graph
    let name_map: HashMap<String, String> = {
        let mut stmt = conn
            .prepare("SELECT id, name FROM nodes")
            .expect("failed to prepare nodes query");
        stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
            .expect("failed to query nodes")
            .filter_map(|r| r.ok())
            .collect()
    };

    let mut all_paths: Vec<Vec<(String, String)>> = Vec::new();

    // For each source, BFS to all reachable sinks
    for source_id in &source_ids {
        // BFS to find shortest paths (one representative path per source->sink pair)
        let mut queue: VecDeque<(String, Vec<(String, String)>)> = VecDeque::new();
        let mut visited: HashSet<String> = HashSet::new();

        let start_node = (
            source_id.clone(),
            name_map.get(source_id).cloned().unwrap_or_else(|| source_id.clone()),
        );
        let start_path = vec![start_node];
        queue.push_back((source_id.clone(), start_path));
        visited.insert(source_id.clone());

        while let Some((current, path)) = queue.pop_front() {
            if sink_ids.contains(&current) && path.len() > 1 {
                // Found a sink (and the path has more than just the source itself,
                // unless source == sink)
                all_paths.push(path.clone());
                continue; // Don't explore further from sinks
            }

            if let Some(neighbors) = adj.get(&current) {
                for neighbor in neighbors {
                    if !visited.contains(neighbor) || sink_ids.contains(neighbor) {
                        if !sink_ids.contains(neighbor) {
                            visited.insert(neighbor.clone());
                        }
                        let mut new_path = path.clone();
                        let name = name_map
                            .get(neighbor)
                            .cloned()
                            .unwrap_or_else(|| neighbor.clone());
                        new_path.push((neighbor.clone(), name));
                        queue.push_back((neighbor.clone(), new_path));
                    }
                }
            }
        }
    }

    all_paths
}

/// Find nodes that have outbound edges of the given kinds.
fn find_nodes_with_edge_kinds(
    conn: &rusqlite::Connection,
    kinds: &[&str],
) -> HashSet<String> {
    if kinds.is_empty() {
        return HashSet::new();
    }

    let placeholders: String = kinds.iter().map(|_| "?").collect::<Vec<_>>().join(",");
    let sql = format!(
        "SELECT DISTINCT source FROM edges WHERE kind IN ({})",
        placeholders
    );

    let params: Vec<&dyn rusqlite::types::ToSql> = kinds
        .iter()
        .map(|k| k as &dyn rusqlite::types::ToSql)
        .collect();

    let mut stmt = conn.prepare(&sql).expect("failed to prepare query");
    stmt.query_map(params.as_slice(), |row| row.get::<_, String>(0))
        .expect("failed to query")
        .filter_map(|r| r.ok())
        .collect()
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
        let path = std::env::temp_dir().join(format!("tws_taint_test_{}.db", name));
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

    fn insert_node(conn: &rusqlite::Connection, name: &str, file: &str) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, 'python', 1, 1, ?5)",
            params![nid, name, qname, file, ts],
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
    fn test_simple_source_to_sink() {
        let (db, path) = setup_db("simple_taint");
        let conn = db.connection();

        // Source: env_reader (reads env vars)
        // Sink: http_caller (makes HTTP calls)
        // Path: env_reader -> processor -> http_caller
        let env_reader = insert_node(conn, "env_reader", "src/env.py");
        let processor = insert_node(conn, "processor", "src/proc.py");
        let http_caller = insert_node(conn, "http_caller", "src/http.py");

        // Mark env_reader as source (has ENV_ACCESSES edge)
        insert_edge(conn, &env_reader, &env_reader, "ENV_ACCESSES");
        // Mark http_caller as sink (has HTTP_CALLS edge)
        insert_edge(conn, &http_caller, &http_caller, "HTTP_CALLS");
        // Call chain
        insert_edge(conn, &env_reader, &processor, "CALLS");
        insert_edge(conn, &processor, &http_caller, "CALLS");

        let paths = taint_analysis(&db, &["ENV_ACCESSES"], &["HTTP_CALLS"]);
        assert!(!paths.is_empty(), "Expected at least one taint path");
        // Check that at least one path goes from env_reader to http_caller
        let found = paths.iter().any(|p| {
            p.first().map(|n| &n.1) == Some(&"env_reader".to_string())
                && p.last().map(|n| &n.1) == Some(&"http_caller".to_string())
        });
        assert!(found, "Expected a path from env_reader to http_caller");
        assert!(paths[0].len() >= 3, "Path should have at least 3 nodes");

        cleanup(&path);
    }

    #[test]
    fn test_no_sources() {
        let (db, path) = setup_db("taint_no_sources");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py");
        insert_edge(conn, &a, &a, "CALLS");

        let paths = taint_analysis(&db, &[], &["HTTP_CALLS"]);
        assert!(paths.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_no_sinks() {
        let (db, path) = setup_db("taint_no_sinks");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py");
        insert_edge(conn, &a, &a, "ENV_ACCESSES");

        let paths = taint_analysis(&db, &["ENV_ACCESSES"], &[]);
        assert!(paths.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_no_path_between_source_and_sink() {
        let (db, path) = setup_db("taint_no_path");
        let conn = db.connection();

        let src_node = insert_node(conn, "src_fn", "src/src.py");
        let sink_node = insert_node(conn, "sink_fn", "src/sink.py");

        // Source and sink exist but are not connected
        insert_edge(conn, &src_node, &src_node, "ENV_ACCESSES");
        insert_edge(conn, &sink_node, &sink_node, "HTTP_CALLS");

        let paths = taint_analysis(&db, &["ENV_ACCESSES"], &["HTTP_CALLS"]);
        assert!(paths.is_empty(), "No path should exist between unconnected nodes");

        cleanup(&path);
    }

    #[test]
    fn test_multiple_paths() {
        let (db, path) = setup_db("taint_multi_paths");
        let conn = db.connection();

        // setup:
        // src -> a -> sink
        // src -> b -> sink
        let src_node = insert_node(conn, "src_fn", "src/src.py");
        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        let sink_node = insert_node(conn, "sink_fn", "src/sink.py");

        insert_edge(conn, &src_node, &src_node, "ENV_ACCESSES");
        insert_edge(conn, &sink_node, &sink_node, "HTTP_CALLS");
        insert_edge(conn, &src_node, &a, "CALLS");
        insert_edge(conn, &a, &sink_node, "CALLS");
        insert_edge(conn, &src_node, &b, "CALLS");
        insert_edge(conn, &b, &sink_node, "CALLS");

        let paths = taint_analysis(&db, &["ENV_ACCESSES"], &["HTTP_CALLS"]);
        // We expect 2 paths (one through a, one through b)
        assert!(paths.len() >= 1, "Expected at least 1 path, got {}", paths.len());

        cleanup(&path);
    }

    #[test]
    fn test_source_is_sink() {
        let (db, path) = setup_db("taint_source_is_sink");
        let conn = db.connection();

        // Same node is both source and sink
        let node = insert_node(conn, "dual_fn", "src/dual.py");
        insert_edge(conn, &node, &node, "ENV_ACCESSES");
        insert_edge(conn, &node, &node, "HTTP_CALLS");

        let paths = taint_analysis(&db, &["ENV_ACCESSES"], &["HTTP_CALLS"]);
        // The node is both source and sink, but we need CALLS edges to traverse
        // No CALLS edges here, so no paths
        assert!(paths.is_empty(), "No CALLS edges => no path");

        cleanup(&path);
    }
}
