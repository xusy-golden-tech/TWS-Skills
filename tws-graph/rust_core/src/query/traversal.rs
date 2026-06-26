//! Graph traverser — BFS-based path finding and impact radius computation.
//!
//! DB-backed: edges are loaded from the `Database` on construction.
//! Supports edge-type filtering (e.g. exclude CONTAINS for impact, include
//! all for trace) and bidirectional traversal (outbound for calls, inbound
//! for impact/callers).

use crate::db::Database;
use std::collections::{HashMap, HashSet, VecDeque};

/// Which direction to traverse.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraversalDirection {
    /// Follow edges source → target (forward).
    Outbound,
    /// Follow edges target → source (reverse).
    Inbound,
    /// Follow edges both ways.
    Bidirectional,
}

/// A DB-backed BFS graph traverser.
///
/// On construction, loads all edges from the database and builds adjacency
/// lists for both outbound and inbound directions.
pub struct GraphTraverser {
    /// outbound[source] = [(target, kind)]
    outbound: HashMap<String, Vec<(String, String)>>,
    /// inbound[target] = [(source, kind)]
    inbound: HashMap<String, Vec<(String, String)>>,
}

impl GraphTraverser {
    /// Build a traverser from a `Database`, loading all edges.
    ///
    /// Optionally filter edges by `kind_filter` (IN clause).
    /// Set `exclude_kinds` to filter OUT specific edge kinds (e.g. `CONTAINS`).
    pub fn from_db(
        db: &Database,
        kind_filter: Option<&[&str]>,
        exclude_kinds: Option<&[&str]>,
    ) -> rusqlite::Result<Self> {
        let edges = db.get_all_edges(None)?;
        let mut outbound: HashMap<String, Vec<(String, String)>> = HashMap::new();
        let mut inbound: HashMap<String, Vec<(String, String)>> = HashMap::new();

        for (source, target, kind, _target_text) in &edges {
            // Apply kind filter (include only) if specified
            if let Some(allowed) = kind_filter {
                if !allowed.contains(&kind.as_str()) {
                    continue;
                }
            }

            // Apply exclude filter (exclude) if specified
            if let Some(excluded) = exclude_kinds {
                if excluded.contains(&kind.as_str()) {
                    continue;
                }
            }

            outbound
                .entry(source.clone())
                .or_default()
                .push((target.clone(), kind.clone()));
            inbound
                .entry(target.clone())
                .or_default()
                .push((source.clone(), kind.clone()));
        }

        Ok(Self { outbound, inbound })
    }

    /// Build a traverser from in-memory edge tuples (for testing).
    /// Edge format: `(source, target, kind)`.
    pub fn from_edges(edges: &[(String, String, String)]) -> Self {
        let mut outbound: HashMap<String, Vec<(String, String)>> = HashMap::new();
        let mut inbound: HashMap<String, Vec<(String, String)>> = HashMap::new();

        for (source, target, kind) in edges {
            outbound
                .entry(source.clone())
                .or_default()
                .push((target.clone(), kind.clone()));
            inbound
                .entry(target.clone())
                .or_default()
                .push((source.clone(), kind.clone()));
        }

        Self { outbound, inbound }
    }

    // ------------------------------------------------------------------
    // impact_radius — all nodes reachable within max_depth hops
    // ------------------------------------------------------------------

    /// Find all nodes reachable from `start` within `max_depth` hops,
    /// following edges in the specified direction.
    pub fn impact_radius(
        &self,
        start: &str,
        max_depth: usize,
        direction: TraversalDirection,
    ) -> HashSet<String> {
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back((start.to_string(), 0));
        visited.insert(start.to_string());

        while let Some((node, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }

            let mut push_neighbor = |nbr: &str| {
                if visited.insert(nbr.to_string()) {
                    queue.push_back((nbr.to_string(), depth + 1));
                }
            };

            match direction {
                TraversalDirection::Outbound => {
                    if let Some(neighbors) = self.outbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            push_neighbor(nbr);
                        }
                    }
                }
                TraversalDirection::Inbound => {
                    if let Some(neighbors) = self.inbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            push_neighbor(nbr);
                        }
                    }
                }
                TraversalDirection::Bidirectional => {
                    if let Some(neighbors) = self.outbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            push_neighbor(nbr);
                        }
                    }
                    if let Some(neighbors) = self.inbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            push_neighbor(nbr);
                        }
                    }
                }
            }
        }

        // Exclude the start node itself from the result
        visited.remove(start);
        visited
    }

    // ------------------------------------------------------------------
    // outbound_calls — forward traversal with depth limit
    // ------------------------------------------------------------------

    /// Return all nodes within `depth` hops along outbound edges.
    ///
    /// Result is a Vec of (depth, node_id) pairs in BFS order.
    pub fn outbound_calls(
        &self,
        node_id: &str,
        depth: usize,
    ) -> Vec<(usize, String)> {
        let mut result = Vec::new();
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back((node_id.to_string(), 0));
        visited.insert(node_id.to_string());

        while let Some((node, d)) = queue.pop_front() {
            if d > 0 {
                result.push((d, node.clone()));
            }
            if d >= depth {
                continue;
            }
            if let Some(neighbors) = self.outbound.get(&node) {
                for (nbr, _kind) in neighbors {
                    if visited.insert(nbr.clone()) {
                        queue.push_back((nbr.clone(), d + 1));
                    }
                }
            }
        }

        result
    }

    // ------------------------------------------------------------------
    // inbound_callers — reverse traversal with depth limit
    // ------------------------------------------------------------------

    /// Return all nodes within `depth` hops along inbound edges (callers).
    ///
    /// Result is a Vec of (depth, node_id) pairs in BFS order.
    pub fn inbound_callers(
        &self,
        node_id: &str,
        depth: usize,
    ) -> Vec<(usize, String)> {
        let mut result = Vec::new();
        let mut visited = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back((node_id.to_string(), 0));
        visited.insert(node_id.to_string());

        while let Some((node, d)) = queue.pop_front() {
            if d > 0 {
                result.push((d, node.clone()));
            }
            if d >= depth {
                continue;
            }
            if let Some(neighbors) = self.inbound.get(&node) {
                for (nbr, _kind) in neighbors {
                    if visited.insert(nbr.clone()) {
                        queue.push_back((nbr.clone(), d + 1));
                    }
                }
            }
        }

        result
    }

    // ------------------------------------------------------------------
    // shortest_path — BFS from src to tgt
    // ------------------------------------------------------------------

    /// Find the shortest path between `src` and `tgt` using BFS.
    ///
    /// Returns `Some(Vec<node_id>)` if a path exists, `None` otherwise.
    /// The path includes both endpoints.
    pub fn shortest_path(
        &self,
        src: &str,
        tgt: &str,
        direction: TraversalDirection,
    ) -> Option<Vec<String>> {
        if src == tgt {
            return Some(vec![src.to_string()]);
        }

        let mut queue = VecDeque::new();
        let mut parent: HashMap<String, String> = HashMap::new();
        let mut visited = HashSet::new();

        queue.push_back(src.to_string());
        visited.insert(src.to_string());

        while let Some(node) = queue.pop_front() {
            let mut check_neighbor = |nbr: &String| -> bool {
                if !visited.contains(nbr) {
                    visited.insert(nbr.clone());
                    parent.insert(nbr.clone(), node.clone());
                    if nbr == tgt {
                        return true; // found
                    }
                    queue.push_back(nbr.clone());
                }
                false
            };

            let found = match direction {
                TraversalDirection::Outbound | TraversalDirection::Bidirectional => {
                    if let Some(neighbors) = self.outbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            if check_neighbor(nbr) {
                                // found — reconstruct path
                                let mut path = vec![tgt.to_string()];
                                let mut cur = tgt.to_string();
                                while cur != *src {
                                    cur = parent[&cur].clone();
                                    path.push(cur.clone());
                                }
                                path.reverse();
                                return Some(path);
                            }
                        }
                    }
                    false
                }
                _ => false,
            };

            if found {
                // handled inside loop above
            }

            if direction == TraversalDirection::Inbound || direction == TraversalDirection::Bidirectional {
                if let Some(neighbors) = self.inbound.get(&node) {
                    for (nbr, _kind) in neighbors {
                        if check_neighbor(nbr) {
                            let mut path = vec![tgt.to_string()];
                            let mut cur = tgt.to_string();
                            while cur != *src {
                                cur = parent[&cur].clone();
                                path.push(cur.clone());
                            }
                            path.reverse();
                            return Some(path);
                        }
                    }
                }
            }
        }

        None
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /// Number of nodes with outgoing edges.
    pub fn node_count(&self) -> usize {
        let mut all: HashSet<&String> = HashSet::new();
        for k in self.outbound.keys() {
            all.insert(k);
        }
        for k in self.inbound.keys() {
            all.insert(k);
        }
        all.len()
    }

    /// Number of edges (unduplicated).
    pub fn edge_count(&self) -> usize {
        let mut count = 0usize;
        for v in self.outbound.values() {
            count += v.len();
        }
        count
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::Database;
    use crate::db::connection::hash_id;
    use rusqlite::params;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_trav_{}.db", name));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));

        let db = Database::initialize(&path).unwrap();
        (db, path)
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn insert_node(db: &Database, name: &str, qualified: &str, file_path: &str) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, 'python', 1, 1, ?5)",
            params![id, name, qualified, file_path, ts],
        )
        .unwrap();
        id
    }

    fn insert_edge(db: &Database, src: &str, tgt: &str, kind: &str) {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![src, tgt, kind],
        )
        .unwrap();
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // from_edges tests (in-memory, no DB needed)
    // ------------------------------------------------------------------

    #[test]
    fn test_from_edges_impact_radius() {
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("b".to_string(), "c".to_string(), "CALLS".to_string()),
            ("a".to_string(), "d".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        let radius = t.impact_radius("a", 2, TraversalDirection::Outbound);
        assert!(radius.contains("b"));
        assert!(radius.contains("c"));
        assert!(radius.contains("d"));
        assert!(!radius.contains("a"));
    }

    #[test]
    fn test_shortest_path_in_memory() {
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("b".to_string(), "c".to_string(), "CALLS".to_string()),
            ("c".to_string(), "d".to_string(), "CALLS".to_string()),
            ("a".to_string(), "d".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        // Direct path a → d
        let path = t.shortest_path("a", "d", TraversalDirection::Outbound);
        assert!(path.is_some());
        let p = path.unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!(p[0], "a");
        assert_eq!(p[1], "d");
    }

    #[test]
    fn test_no_path_returns_none() {
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("c".to_string(), "d".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        let path = t.shortest_path("a", "d", TraversalDirection::Outbound);
        assert!(path.is_none());
    }

    #[test]
    fn test_outbound_calls() {
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("a".to_string(), "c".to_string(), "CALLS".to_string()),
            ("b".to_string(), "d".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        let calls = t.outbound_calls("a", 2);
        assert_eq!(calls.len(), 3); // b, c, d
        assert!(calls.iter().any(|(d, id)| *d == 1 && id == "b"));
        assert!(calls.iter().any(|(d, id)| *d == 1 && id == "c"));
        assert!(calls.iter().any(|(d, id)| *d == 2 && id == "d"));
    }

    #[test]
    fn test_inbound_callers() {
        let edges = vec![
            ("a".to_string(), "c".to_string(), "CALLS".to_string()),
            ("b".to_string(), "c".to_string(), "CALLS".to_string()),
            ("d".to_string(), "a".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        let callers = t.inbound_callers("c", 2);
        assert_eq!(callers.len(), 3); // a, b (depth 1), d (depth 2)
        assert!(callers.iter().any(|(d, id)| *d == 1 && id == "a"));
        assert!(callers.iter().any(|(d, id)| *d == 1 && id == "b"));
        assert!(callers.iter().any(|(d, id)| *d == 2 && id == "d"));
    }

    #[test]
    fn test_inbound_impact_radius() {
        let edges = vec![
            ("a".to_string(), "x".to_string(), "CALLS".to_string()),
            ("b".to_string(), "x".to_string(), "CALLS".to_string()),
            ("c".to_string(), "a".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        // Who impacts x?
        let radius = t.impact_radius("x", 2, TraversalDirection::Inbound);
        assert!(radius.contains("a"));
        assert!(radius.contains("b"));
        assert!(radius.contains("c")); // c → a → x
        assert!(!radius.contains("x"));
    }

    #[test]
    fn test_bidirectional_path() {
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("c".to_string(), "b".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        // a → b, and c → b, so a can reach c via bidirectional?
        // Outbound: a → b. Inbound from b: a ← b, c ← b. So no.
        // One hop outbound from a gets to b, and b's inbound callers are a and c.
        // So path a → b ← c exists bidirectionally.
        let path = t.shortest_path("a", "c", TraversalDirection::Bidirectional);
        assert!(path.is_some());
        let p = path.unwrap();
        assert_eq!(p.len(), 3);
        assert_eq!(p[0], "a");
        assert_eq!(p[1], "b");
        assert_eq!(p[2], "c");
    }

    // ------------------------------------------------------------------
    // from_db tests
    // ------------------------------------------------------------------

    #[test]
    fn test_from_db_basic_traversal() {
        let (db, path) = temp_db("basic_traversal");

        let n_a = insert_node(&db, "func_a", "mod::func_a", "a.py");
        let n_b = insert_node(&db, "func_b", "mod::func_b", "b.py");
        let n_c = insert_node(&db, "func_c", "mod::func_c", "c.py");
        insert_edge(&db, &n_a, &n_b, "CALLS");
        insert_edge(&db, &n_b, &n_c, "CALLS");

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        let calls = t.outbound_calls(&n_a, 2);
        assert_eq!(calls.len(), 2);

        let callers = t.inbound_callers(&n_c, 2);
        assert_eq!(callers.len(), 2);

        cleanup(&path);
    }

    #[test]
    fn test_from_db_with_exclude_kinds() {
        let (db, path) = temp_db("exclude_kinds");

        let n_a = insert_node(&db, "func_a", "mod::func_a", "a.py");
        let n_b = insert_node(&db, "func_b", "mod::func_b", "b.py");
        let n_c = insert_node(&db, "func_c", "mod::func_c", "c.py");
        insert_edge(&db, &n_a, &n_b, "CALLS");
        insert_edge(&db, &n_a, &n_c, "CONTAINS"); // should be excluded

        let t = GraphTraverser::from_db(&db, None, Some(&["CONTAINS"])).unwrap();

        let calls = t.outbound_calls(&n_a, 1);
        assert_eq!(calls.len(), 1);
        assert_eq!(&calls[0].1, &n_b);

        cleanup(&path);
    }

    #[test]
    fn test_from_db_with_kind_filter() {
        let (db, path) = temp_db("kind_filter");

        let n_a = insert_node(&db, "func_a", "mod::func_a", "a.py");
        let n_b = insert_node(&db, "func_b", "mod::func_b", "b.py");
        let n_c = insert_node(&db, "func_c", "mod::func_c", "c.py");
        insert_edge(&db, &n_a, &n_b, "CALLS");
        insert_edge(&db, &n_a, &n_c, "IMPORTS");

        let t = GraphTraverser::from_db(&db, Some(&["CALLS"]), None).unwrap();

        let calls = t.outbound_calls(&n_a, 1);
        assert_eq!(calls.len(), 1);
        assert_eq!(&calls[0].1, &n_b);

        cleanup(&path);
    }

    #[test]
    fn test_impact_radius_from_db() {
        let (db, path) = temp_db("impact_radius");

        let n_a = insert_node(&db, "func_a", "mod::func_a", "a.py");
        let n_b = insert_node(&db, "func_b", "mod::func_b", "b.py");
        let n_c = insert_node(&db, "func_c", "mod::func_c", "c.py");
        let n_d = insert_node(&db, "func_d", "mod::func_d", "d.py");
        insert_edge(&db, &n_a, &n_b, "CALLS");
        insert_edge(&db, &n_b, &n_c, "CALLS");
        insert_edge(&db, &n_b, &n_d, "CALLS");

        let t = GraphTraverser::from_db(&db, None, None).unwrap();
        let radius = t.impact_radius(&n_a, 2, TraversalDirection::Outbound);
        assert_eq!(radius.len(), 3); // b, c, d
        assert!(!radius.contains(&n_a));

        cleanup(&path);
    }

    #[test]
    fn test_shortest_path_from_db() {
        let (db, path) = temp_db("shortest_path");

        let n_a = insert_node(&db, "func_a", "mod::func_a", "a.py");
        let n_b = insert_node(&db, "func_b", "mod::func_b", "b.py");
        let n_c = insert_node(&db, "func_c", "mod::func_c", "c.py");
        insert_edge(&db, &n_a, &n_b, "CALLS");
        insert_edge(&db, &n_b, &n_c, "CALLS");

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        let s_path = t.shortest_path(&n_a, &n_c, TraversalDirection::Outbound);
        assert!(s_path.is_some());
        let p = s_path.unwrap();
        assert_eq!(p.len(), 3);
        assert_eq!(p[0], n_a);
        assert_eq!(p[2], n_c);

        cleanup(&path);
    }
}
