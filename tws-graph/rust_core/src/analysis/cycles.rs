//! Cycle detection in the call graph using DFS 3-color algorithm.
//!
//! Colors: WHITE (0) = unvisited, GRAY (1) = in-progress, BLACK (2) = done.
//! A back edge (GRAY -> GRAY) indicates a cycle.

use crate::db::Database;
use std::collections::HashMap;

/// Detect cycles in the call graph.
///
/// Returns a list of cycles. Each cycle is a list of `(node_id, node_name)` tuples
/// representing the nodes involved in the cycle.
pub fn detect_cycles(db: &Database) -> Vec<Vec<(String, String)>> {
    let conn = db.connection();

    // Load all nodes and their names
    let mut stmt = conn
        .prepare("SELECT id, name FROM nodes")
        .expect("failed to prepare nodes query");
    let nodes: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    // Build adjacency list: node_id -> [(target_id, edge_kind)]
    let mut adj: HashMap<String, Vec<(String, String)>> = HashMap::new();
    for (nid, _) in &nodes {
        adj.entry(nid.clone()).or_default();
    }

    // Load all CALLS edges
    let mut edge_stmt = conn
        .prepare("SELECT source, target, kind FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String, String)> = edge_stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    for (src, tgt, kind) in edges {
        adj.entry(src).or_default().push((tgt, kind));
    }

    // Build name lookup
    let name_map: HashMap<String, String> = nodes.iter().map(|(id, name)| (id.clone(), name.clone())).collect();

    // DFS state
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum Color {
        White,
        Gray,
        Black,
    }

    let mut colors: HashMap<String, Color> = HashMap::new();
    for (nid, _) in &nodes {
        colors.insert(nid.clone(), Color::White);
    }

    let mut cycles: Vec<Vec<(String, String)>> = Vec::new();
    let mut stack: Vec<String> = Vec::new(); // current DFS path (for cycle extraction)

    // Use an iterative approach with explicit stack to avoid recursion limit issues
    // and to extract cycles properly.
    // We use a stack of (node_id, iterator_index) pairs.
    for (start_id, _) in &nodes {
        if colors[start_id] != Color::White {
            continue;
        }

        // Start DFS from this node
        let mut dfs_stack: Vec<(String, usize)> = Vec::new();
        colors.insert(start_id.clone(), Color::Gray);
        stack.push(start_id.clone());
        dfs_stack.push((start_id.clone(), 0));

        while let Some((ref current_id, ref mut idx)) = dfs_stack.last_mut() {
            let current = current_id.clone();
            let neighbors = adj.get(&current).cloned().unwrap_or_default();

            if *idx >= neighbors.len() {
                // Done exploring all neighbors
                colors.insert(current.clone(), Color::Black);
                dfs_stack.pop();
                stack.pop();
                continue;
            }

            let (ref neighbor_id, _) = neighbors[*idx];
            *idx += 1;

            match colors.get(neighbor_id) {
                None => {
                    // Node not in colors map (might be target-only), treat as White
                }
                Some(Color::White) => {
                    colors.insert(neighbor_id.clone(), Color::Gray);
                    stack.push(neighbor_id.clone());
                    dfs_stack.push((neighbor_id.clone(), 0));
                }
                Some(Color::Gray) => {
                    // Found a back edge: current -> neighbor forms a cycle
                    // Extract the cycle from the stack
                    if let Some(pos) = stack.iter().position(|id| id == neighbor_id) {
                        let cycle_nodes: Vec<(String, String)> = stack[pos..]
                            .iter()
                            .map(|id| {
                                let name = name_map.get(id).cloned().unwrap_or_else(|| id.clone());
                                (id.clone(), name)
                            })
                            .collect();
                        cycles.push(cycle_nodes);
                    }
                }
                Some(Color::Black) => {
                    // Cross edge, ignore
                }
            }
        }
    }

    cycles
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
        let path = std::env::temp_dir().join(format!("tws_cycles_test_{}.db", name));
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
    fn test_no_cycle_graph() {
        let (db, path) = setup_db("no_cycle");
        let conn = db.connection();

        // A -> B -> C (no cycle)
        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        let c = insert_node(conn, "c", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &c, "CALLS");

        let cycles = detect_cycles(&db);
        assert!(cycles.is_empty(), "Expected no cycles, got {:?}", cycles);

        cleanup(&path);
    }

    #[test]
    fn test_simple_cycle() {
        let (db, path) = setup_db("simple_cycle");
        let conn = db.connection();

        // A -> B -> C -> A (cycle)
        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        let c = insert_node(conn, "c", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &c, "CALLS");
        insert_edge(conn, &c, &a, "CALLS");

        let cycles = detect_cycles(&db);
        assert!(!cycles.is_empty(), "Expected at least one cycle");
        // The cycle should contain a, b, c
        let names: Vec<String> = cycles[0].iter().map(|(_, n)| n.clone()).collect();
        assert!(names.contains(&"a".to_string()));
        assert!(names.contains(&"b".to_string()));
        assert!(names.contains(&"c".to_string()));

        cleanup(&path);
    }

    #[test]
    fn test_self_loop() {
        let (db, path) = setup_db("self_loop");
        let conn = db.connection();

        // A -> A (self-loop)
        let a = insert_node(conn, "self_fn", "src/self_fn.py", "function");
        insert_edge(conn, &a, &a, "CALLS");

        let cycles = detect_cycles(&db);
        assert!(!cycles.is_empty(), "Expected at least one cycle for self-loop");
        assert_eq!(cycles[0].len(), 1);
        assert_eq!(cycles[0][0].1, "self_fn");

        cleanup(&path);
    }

    #[test]
    fn test_multi_cycle() {
        let (db, path) = setup_db("multi_cycle");
        let conn = db.connection();

        // Two cycles: A->B->A and C->D->E->C
        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        let c = insert_node(conn, "c", "src/c.py", "function");
        let d = insert_node(conn, "d", "src/d.py", "function");
        let e = insert_node(conn, "e", "src/e.py", "function");

        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &a, "CALLS");
        insert_edge(conn, &c, &d, "CALLS");
        insert_edge(conn, &d, &e, "CALLS");
        insert_edge(conn, &e, &c, "CALLS");

        let cycles = detect_cycles(&db);
        assert_eq!(cycles.len(), 2, "Expected 2 cycles, got {}: {:?}", cycles.len(), cycles);

        cleanup(&path);
    }

    #[test]
    fn test_cycle_with_diamond() {
        let (db, path) = setup_db("diamond");
        let conn = db.connection();

        // A -> B -> D, A -> C -> D, D -> A (cycle through diamond)
        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        let c = insert_node(conn, "c", "src/c.py", "function");
        let d = insert_node(conn, "d", "src/d.py", "function");

        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &a, &c, "CALLS");
        insert_edge(conn, &b, &d, "CALLS");
        insert_edge(conn, &c, &d, "CALLS");
        insert_edge(conn, &d, &a, "CALLS");

        let cycles = detect_cycles(&db);
        assert!(!cycles.is_empty(), "Expected at least one cycle in diamond graph");

        cleanup(&path);
    }
}
