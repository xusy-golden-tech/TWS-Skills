//! Community detection using a simplified Louvain algorithm.
//!
//! Builds an undirected graph from all edge types, then applies greedy modularity
//! optimization for up to 20 refinement rounds.

use crate::db::Database;
use std::collections::{HashMap, HashSet};

/// Detect communities in the code graph.
///
/// Returns `Vec<(node_id, node_name, community_id)>` — each node assigned to
/// a community. Community IDs are dense integers starting at 0.
pub fn detect_communities(db: &Database) -> Vec<(String, String, usize)> {
    let conn = db.connection();

    // Load all nodes
    let mut node_stmt = conn
        .prepare("SELECT id, name FROM nodes")
        .expect("failed to prepare nodes query");
    let nodes: Vec<(String, String)> = node_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    if nodes.is_empty() {
        return Vec::new();
    }

    // Build node index: node_id -> position
    let node_index: HashMap<String, usize> = nodes
        .iter()
        .enumerate()
        .map(|(i, (id, _))| (id.clone(), i))
        .collect();

    let n = nodes.len();

    // Build adjacency list (undirected, with edge weights)
    let mut adj: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut edge_stmt = conn
        .prepare("SELECT source, target FROM edges")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = edge_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    for (src, tgt) in &edges {
        if let (Some(&si), Some(&ti)) = (node_index.get(src), node_index.get(tgt)) {
            adj[si].push(ti);
            adj[ti].push(si); // undirected
        }
    }

    // Total degree sum (2m for undirected)
    let total_degree: usize = adj.iter().map(|nb| nb.len()).sum();
    let two_m = if total_degree == 0 { 1.0f64 } else { total_degree as f64 };

    // Initialize: each node is in its own community
    let mut community: Vec<usize> = (0..n).collect();
    let mut changed = true;
    let max_rounds = 20;
    let mut round = 0;

    while changed && round < max_rounds {
        changed = false;
        round += 1;

        // Process nodes in random-ish order (just use sequential for determinism)
        for node in 0..n {
            let current_comm = community[node];
            let mut best_comm = current_comm;
            let mut best_delta = 0.0f64;

            // Collect neighbor communities
            let mut neighbor_comms: HashSet<usize> = HashSet::new();
            for &neighbor in &adj[node] {
                neighbor_comms.insert(community[neighbor]);
            }

            // Compute k_i (degree of node)
            let k_i = adj[node].len() as f64;

            // Compute sigma_tot for current community (simplified: just count edges within)
            for &comm in &neighbor_comms {
                if comm == current_comm {
                    continue;
                }

                // Simplified modularity gain: k_i_in(comm) - k_i * sigma_tot(comm) / 2m
                let k_i_in_comm = adj[node]
                    .iter()
                    .filter(|&&nb| community[nb] == comm)
                    .count() as f64;

                let sigma_tot_comm = nodes
                    .iter()
                    .enumerate()
                    .filter(|&(i, _)| community[i] == comm)
                    .map(|(i, _)| adj[i].len() as f64)
                    .sum::<f64>();

                let delta = k_i_in_comm - (k_i * sigma_tot_comm) / two_m;

                if delta > best_delta {
                    best_delta = delta;
                    best_comm = comm;
                }
            }

            if best_comm != current_comm && best_delta > 0.0 {
                community[node] = best_comm;
                changed = true;
            }
        }
    }

    // Renumber community IDs to be dense
    let mut comm_id_map: HashMap<usize, usize> = HashMap::new();
    let mut next_id = 0usize;
    for &c in &community {
        if !comm_id_map.contains_key(&c) {
            comm_id_map.insert(c, next_id);
            next_id += 1;
        }
    }

    nodes
        .iter()
        .enumerate()
        .map(|(i, (id, name))| (id.clone(), name.clone(), comm_id_map[&community[i]]))
        .collect()
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
        let path = std::env::temp_dir().join(format!("tws_community_test_{}.db", name));
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
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![nid, name, qname, file, "python", 1, 1, ts],
        )
        .unwrap();
        nid
    }

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, 'CALLS')",
            params![src, tgt],
        )
        .unwrap();
    }

    #[test]
    fn test_empty_graph() {
        let (db, path) = setup_db("empty_community");
        let communities = detect_communities(&db);
        assert!(communities.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_single_node_is_own_community() {
        let (db, path) = setup_db("single_community");
        let conn = db.connection();

        insert_node(conn, "only_func", "src/main.py");

        let communities = detect_communities(&db);
        assert_eq!(communities.len(), 1);
        assert_eq!(communities[0].2, 0, "Single node should be community 0");

        cleanup(&path);
    }

    #[test]
    fn test_two_connected_nodes_same_community() {
        let (db, path) = setup_db("two_connected");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        insert_edge(conn, &a, &b);

        let communities = detect_communities(&db);
        assert_eq!(communities.len(), 2);

        // They should be in the same community
        let comm_a = communities.iter().find(|(id, _, _)| *id == a).unwrap().2;
        let comm_b = communities.iter().find(|(id, _, _)| *id == b).unwrap().2;
        assert_eq!(comm_a, comm_b, "Connected nodes should be in same community");

        cleanup(&path);
    }

    #[test]
    fn test_disconnected_clusters() {
        let (db, path) = setup_db("disconnected");
        let conn = db.connection();

        // Cluster 1: a <-> b <-> c
        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        let c = insert_node(conn, "c", "src/c.py");
        insert_edge(conn, &a, &b);
        insert_edge(conn, &b, &c);

        // Cluster 2: x <-> y
        let x = insert_node(conn, "x", "src/x.py");
        let y = insert_node(conn, "y", "src/y.py");
        insert_edge(conn, &x, &y);

        // Isolated node
        let z = insert_node(conn, "z", "src/z.py");

        let communities = detect_communities(&db);
        assert_eq!(communities.len(), 6);

        // Cluster 1 nodes should share community
        let comm_a = communities.iter().find(|(id, _, _)| *id == a).unwrap().2;
        let comm_b = communities.iter().find(|(id, _, _)| *id == b).unwrap().2;
        let comm_c = communities.iter().find(|(id, _, _)| *id == c).unwrap().2;
        assert_eq!(comm_a, comm_b);
        assert_eq!(comm_b, comm_c);

        // Cluster 2 nodes should share different community
        let comm_x = communities.iter().find(|(id, _, _)| *id == x).unwrap().2;
        let comm_y = communities.iter().find(|(id, _, _)| *id == y).unwrap().2;
        assert_eq!(comm_x, comm_y);
        assert_ne!(comm_a, comm_x, "Clusters should have different communities");

        // Isolated node should be its own
        let comm_z = communities.iter().find(|(id, _, _)| *id == z).unwrap().2;
        assert_ne!(comm_z, comm_a);
        assert_ne!(comm_z, comm_x);

        cleanup(&path);
    }

    #[test]
    fn test_all_nodes_returned() {
        let (db, path) = setup_db("all_returned");
        let conn = db.connection();

        let ids: Vec<String> = (0..10)
            .map(|i| insert_node(conn, &format!("func_{}", i), &format!("src/f{}.py", i)))
            .collect();

        let communities = detect_communities(&db);
        assert_eq!(communities.len(), 10);

        // Every node ID should appear exactly once
        let returned_ids: HashSet<String> = communities.iter().map(|(id, _, _)| id.clone()).collect();
        for id in &ids {
            assert!(returned_ids.contains(id), "Node {} not in results", id);
        }

        cleanup(&path);
    }
}
