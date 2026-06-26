//! Centrality metrics — PageRank and Betweenness centrality.
//!
//! PageRank uses the iterative power method with a configurable damping factor.
//! Betweenness centrality uses Brandes' algorithm with BFS from each node.

use crate::db::Database;
use std::collections::{HashMap, HashSet, VecDeque};

/// Compute PageRank scores for all nodes in the call graph.
///
/// Uses the iterative power method with damping factor (default 0.85) and
/// the specified number of iterations (default 50). Returns a map from
/// node ID to PageRank score.
pub fn compute_pagerank(db: &Database, damping: f64, iterations: usize) -> HashMap<String, f64> {
    let conn = db.connection();

    // Load all nodes
    let mut node_stmt = conn
        .prepare("SELECT id FROM nodes")
        .expect("failed to prepare nodes query");
    let node_ids: Vec<String> = node_stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    if node_ids.is_empty() {
        return HashMap::new();
    }

    let n = node_ids.len();
    let mut idx: HashMap<String, usize> = node_ids
        .iter()
        .enumerate()
        .map(|(i, id)| (id.clone(), i))
        .collect();

    // Build adjacency: outbound edges per node
    let mut out_degree: Vec<usize> = vec![0; n];
    let mut out_edges: Vec<Vec<usize>> = vec![Vec::new(); n];

    let mut edge_stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = edge_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    for (src, tgt) in &edges {
        if let (Some(&si), Some(&ti)) = (idx.get(src), idx.get(tgt)) {
            out_edges[si].push(ti);
            out_degree[si] += 1;
        }
    }

    // Initialize: uniform distribution
    let init_score = 1.0 / n as f64;
    let mut scores: Vec<f64> = vec![init_score; n];
    let damping_term = (1.0 - damping) / n as f64;

    for _iter in 0..iterations {
        let mut new_scores = vec![damping_term; n];

        for i in 0..n {
            if out_degree[i] == 0 {
                // Dangling node: distribute evenly
                let share = damping * scores[i] / n as f64;
                for j in 0..n {
                    new_scores[j] += share;
                }
            } else {
                let share = damping * scores[i] / out_degree[i] as f64;
                for &target in &out_edges[i] {
                    new_scores[target] += share;
                }
            }
        }

        // Re-normalize
        let sum: f64 = new_scores.iter().sum();
        if sum > 0.0 {
            for s in &mut new_scores {
                *s /= sum;
            }
        }

        scores = new_scores;
    }

    node_ids
        .into_iter()
        .enumerate()
        .map(|(i, id)| (id, scores[i]))
        .collect()
}

/// Compute Betweenness centrality for all nodes using Brandes' algorithm.
///
/// Returns a map from node ID to betweenness score. The score represents
/// the fraction of shortest paths between all node pairs that pass through
/// the given node.
pub fn compute_betweenness(db: &Database) -> HashMap<String, f64> {
    let conn = db.connection();

    // Load all nodes
    let mut node_stmt = conn
        .prepare("SELECT id FROM nodes")
        .expect("failed to prepare nodes query");
    let node_ids: Vec<String> = node_stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    if node_ids.is_empty() {
        return HashMap::new();
    }

    let n = node_ids.len();
    let idx: HashMap<String, usize> = node_ids
        .iter()
        .enumerate()
        .map(|(i, id)| (id.clone(), i))
        .collect();

    // Build adjacency (undirected for betweenness on code graph)
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
        if let (Some(&si), Some(&ti)) = (idx.get(src), idx.get(tgt)) {
            adj[si].push(ti);
            adj[ti].push(si); // undirected for betweenness
        }
    }

    let mut cb: Vec<f64> = vec![0.0; n];

    // Brandes' algorithm: BFS from each node
    for s in 0..n {
        if adj[s].is_empty() {
            continue;
        }

        let mut stack: Vec<usize> = Vec::new();
        let mut sigma: Vec<f64> = vec![0.0; n];
        let mut dist: Vec<Option<i64>> = vec![None; n];
        let mut delta: Vec<f64> = vec![0.0; n];
        let mut pred: Vec<Vec<usize>> = vec![Vec::new(); n];

        sigma[s] = 1.0;
        dist[s] = Some(0);

        let mut queue: VecDeque<usize> = VecDeque::new();
        queue.push_back(s);

        while let Some(v) = queue.pop_front() {
            stack.push(v);
            let dv = dist[v].unwrap();

            for &w in &adj[v] {
                // First visit?
                if dist[w].is_none() {
                    dist[w] = Some(dv + 1);
                    queue.push_back(w);
                }
                // Shortest path to w via v?
                if dist[w] == Some(dv + 1) {
                    sigma[w] += sigma[v];
                    pred[w].push(v);
                }
            }
        }

        // Back-propagation
        while let Some(w) = stack.pop() {
            for &v in &pred[w] {
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w]);
            }
            if w != s {
                cb[w] += delta[w];
            }
        }
    }

    // Normalize for undirected graph: divide by 2 * (n-1)(n-2)/2
    if n > 2 {
        let norm = 1.0 / ((n - 1) as f64 * (n - 2) as f64);
        for c in &mut cb {
            *c *= norm;
        }
    }

    node_ids
        .into_iter()
        .enumerate()
        .map(|(i, id)| (id, cb[i]))
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
        let path = std::env::temp_dir().join(format!("tws_centrality_test_{}.db", name));
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
    fn test_pagerank_empty_graph() {
        let (db, path) = setup_db("pr_empty");
        let scores = compute_pagerank(&db, 0.85, 50);
        assert!(scores.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_pagerank_single_node() {
        let (db, path) = setup_db("pr_single");
        let conn = db.connection();

        insert_node(conn, "only_func", "src/main.py");

        let scores = compute_pagerank(&db, 0.85, 50);
        assert_eq!(scores.len(), 1);
        for (_, &score) in &scores {
            assert!((score - 1.0).abs() < 0.001, "Single node PR should be ~1.0");
        }

        cleanup(&path);
    }

    #[test]
    fn test_pagerank_distribution() {
        let (db, path) = setup_db("pr_dist");
        let conn = db.connection();

        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        let c = insert_node(conn, "c", "src/c.py");
        // a -> b, a -> c, b -> c
        insert_edge(conn, &a, &b);
        insert_edge(conn, &a, &c);
        insert_edge(conn, &b, &c);

        let scores = compute_pagerank(&db, 0.85, 50);
        assert_eq!(scores.len(), 3);

        // c gets most PR (inbound from a and b)
        let score_c = scores.get(&c).copied().unwrap_or(0.0);
        let score_a = scores.get(&a).copied().unwrap_or(0.0);
        assert!(score_c > score_a, "c should have higher PR than a (more inlinks)");

        // Sum should be ~1.0
        let total: f64 = scores.values().sum();
        assert!((total - 1.0).abs() < 0.01, "PR should sum to ~1.0, got {}", total);

        cleanup(&path);
    }

    #[test]
    fn test_betweenness_empty_graph() {
        let (db, path) = setup_db("btw_empty");
        let scores = compute_betweenness(&db);
        assert!(scores.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_betweenness_line_graph() {
        let (db, path) = setup_db("btw_line");
        let conn = db.connection();

        // Line: a -- b -- c (undirected via edges)
        let a = insert_node(conn, "a", "src/a.py");
        let b = insert_node(conn, "b", "src/b.py");
        let c = insert_node(conn, "c", "src/c.py");
        insert_edge(conn, &a, &b);
        insert_edge(conn, &b, &c);

        let scores = compute_betweenness(&db);
        assert_eq!(scores.len(), 3);

        // b should have highest betweenness (bridges a and c)
        let score_b = scores.get(&b).copied().unwrap_or(0.0);
        let score_a = scores.get(&a).copied().unwrap_or(0.0);
        let score_c = scores.get(&c).copied().unwrap_or(0.0);
        assert!(score_b > score_a, "b should have higher betweenness than a");
        assert!(score_b > score_c, "b should have higher betweenness than c");

        cleanup(&path);
    }

    #[test]
    fn test_betweenness_star_graph() {
        let (db, path) = setup_db("btw_star");
        let conn = db.connection();

        // Star: center connected to 4 leaves
        let center = insert_node(conn, "center", "src/center.py");
        let leaves: Vec<String> = (0..4)
            .map(|i| {
                let leaf = insert_node(conn, &format!("leaf_{}", i), &format!("src/l{}.py", i));
                insert_edge(conn, &center, &leaf);
                leaf
            })
            .collect();

        let scores = compute_betweenness(&db);
        assert_eq!(scores.len(), 5);

        // Center should have highest betweenness
        let score_center = scores.get(&center).copied().unwrap_or(0.0);
        for leaf in &leaves {
            let score_leaf = scores.get(leaf).copied().unwrap_or(0.0);
            assert!(score_center > score_leaf, "Center should have higher betweenness than leaf");
        }

        cleanup(&path);
    }
}
