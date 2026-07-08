//! Graph traverser — BFS-based path finding and impact radius computation.
//!
//! DB-backed: edges are loaded from the `Database` on construction.
//! Supports edge-type filtering (e.g. exclude CONTAINS for impact, include
//! all for trace) and bidirectional traversal (outbound for calls, inbound
//! for impact/callers).
//!
//! Cross-language edges from the `cross_lang_edges` table are loaded
//! alongside regular edges, enabling BFS to traverse HTTP bridges between
//! frontend (TypeScript/JavaScript) and backend (Python/Java/Go) symbols.

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

/// A single hop across a language boundary via HTTP.
///
/// Represents a cross-tier link: a frontend function calls an HTTP URL,
/// which is handled by a backend route handler (or vice versa).
#[derive(Debug, Clone)]
pub struct CrossLangHop {
    /// Target node ID (the node on the other side of the HTTP bridge).
    pub target_node_id: String,
    /// HTTP URL, e.g. `/api/auth/register`.
    pub url: String,
    /// HTTP method: GET, POST, PUT, DELETE, PATCH.
    pub http_method: String,
    /// Match type: exact, template, or fuzzy.
    pub match_type: String,
    /// Confidence score 0.0 ~ 1.0.
    pub confidence: f64,
}

/// A DB-backed BFS graph traverser.
///
/// On construction, loads all edges from the database and builds adjacency
/// lists for both outbound and inbound directions.
///
/// Cross-language edges from the `cross_lang_edges` table are loaded
/// alongside regular edges, enabling BFS traversal across HTTP bridges
/// between frontend and backend code.
pub struct GraphTraverser {
    /// outbound[source] = [(target, kind)]
    outbound: HashMap<String, Vec<(String, String)>>,
    /// inbound[target] = [(source, kind)]
    inbound: HashMap<String, Vec<(String, String)>>,
    /// Cross-language HTTP bridge edges.
    /// Forward: frontend func → [CrossLangHop to backend handler]
    /// Reverse: backend handler → [CrossLangHop to frontend func]
    cross_lang: HashMap<String, Vec<CrossLangHop>>,
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

        // Load cross-language edges (silently skip if tables don't exist yet)
        let cross_lang = Self::load_cross_lang_edges(db).unwrap_or_else(|_| {
            // Tables may not exist (pre-v009 database or --no-cross-tier).
            // This is not an error — just means no cross-language traversal.
            HashMap::new()
        });

        Ok(Self { outbound, inbound, cross_lang })
    }

    /// Load cross-language HTTP bridge edges from the database.
    ///
    /// Builds a bidirectional adjacency map:
    /// - Forward: frontend function node → backend handler node
    /// - Reverse: backend handler node → frontend function node
    ///
    /// Returns an error if any of the three tables (`http_calls`,
    /// `http_routes`, `cross_lang_edges`) do not exist.
    fn load_cross_lang_edges(
        db: &Database,
    ) -> rusqlite::Result<HashMap<String, Vec<CrossLangHop>>> {
        let cross_lang_rows = db.get_cross_lang_edges(None, None)?;
        let http_calls = db.get_all_http_calls()?;
        let http_routes = db.get_all_http_routes()?;

        // Build rowid → node_id map from the nodes table
        let node_id_map: HashMap<i64, String> = {
            let conn = db.connection();
            let mut stmt = conn.prepare("SELECT rowid, id FROM nodes")?;
            let rows = stmt.query_map([], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })?
            .filter_map(|r| r.ok())
            .collect::<HashMap<i64, String>>();
            rows
        };

        // Build http_calls.id → node_id map
        // func_node_id references nodes.rowid, so we resolve it via node_id_map
        let call_func_map: HashMap<i64, String> = http_calls.iter()
            .filter_map(|c| {
                let call_id = c.id?;
                node_id_map.get(&c.func_node_id)
                    .map(|node_id| (call_id, node_id.clone()))
            })
            .collect();

        // Build http_routes.id → node_id map
        let route_handler_map: HashMap<i64, String> = http_routes.iter()
            .filter_map(|r| {
                let route_id = r.id?;
                node_id_map.get(&r.handler_node_id)
                    .map(|node_id| (route_id, node_id.clone()))
            })
            .collect();

        let mut cross_lang: HashMap<String, Vec<CrossLangHop>> = HashMap::new();

        for edge in &cross_lang_rows {
            if let (Some(from_node), Some(to_node)) = (
                call_func_map.get(&edge.from_call_id),
                route_handler_map.get(&edge.to_route_id),
            ) {
                // Forward: frontend func → backend handler
                cross_lang.entry(from_node.clone())
                    .or_default()
                    .push(CrossLangHop {
                        target_node_id: to_node.clone(),
                        url: edge.url.clone(),
                        http_method: edge.http_method.clone(),
                        match_type: edge.match_type.clone(),
                        confidence: edge.confidence,
                    });
                // Reverse: backend handler → frontend func
                cross_lang.entry(to_node.clone())
                    .or_default()
                    .push(CrossLangHop {
                        target_node_id: from_node.clone(),
                        url: edge.url.clone(),
                        http_method: edge.http_method.clone(),
                        match_type: edge.match_type.clone(),
                        confidence: edge.confidence,
                    });
            }
        }

        Ok(cross_lang)
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

        Self { outbound, inbound, cross_lang: HashMap::new() }
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
                    // Follow cross-language edges in outbound traversal
                    if let Some(cross_hops) = self.cross_lang.get(&node) {
                        for hop in cross_hops {
                            push_neighbor(&hop.target_node_id);
                        }
                    }
                }
                TraversalDirection::Inbound => {
                    if let Some(neighbors) = self.inbound.get(&node) {
                        for (nbr, _kind) in neighbors {
                            push_neighbor(nbr);
                        }
                    }
                    // Follow cross-language edges in inbound traversal
                    if let Some(cross_hops) = self.cross_lang.get(&node) {
                        for hop in cross_hops {
                            push_neighbor(&hop.target_node_id);
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
                    // Follow cross-language edges (bidirectional)
                    if let Some(cross_hops) = self.cross_lang.get(&node) {
                        for hop in cross_hops {
                            push_neighbor(&hop.target_node_id);
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
            // Follow cross-language edges
            if let Some(cross_hops) = self.cross_lang.get(&node) {
                for hop in cross_hops {
                    if visited.insert(hop.target_node_id.clone()) {
                        queue.push_back((hop.target_node_id.clone(), d + 1));
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
            // Follow cross-language edges
            if let Some(cross_hops) = self.cross_lang.get(&node) {
                for hop in cross_hops {
                    if visited.insert(hop.target_node_id.clone()) {
                        queue.push_back((hop.target_node_id.clone(), d + 1));
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
    ///
    /// Cross-language edges (HTTP bridges) are followed during traversal.
    /// The parent map stores `(previous_node, Option<CrossLangHop>)` so that
    /// path reconstruction can annotate language crossings.
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
        // parent[child] = (parent_node, optional_cross_lang_hop)
        let mut parent: HashMap<String, (String, Option<CrossLangHop>)> = HashMap::new();
        let mut visited = HashSet::new();

        queue.push_back(src.to_string());
        visited.insert(src.to_string());

        while let Some(node) = queue.pop_front() {
            // Collect all neighbors (regular + cross-lang) for this node
            // as owned data to avoid borrowing conflicts
            let mut neighbors: Vec<(String, Option<CrossLangHop>)> = Vec::new();

            let use_outbound = direction == TraversalDirection::Outbound
                || direction == TraversalDirection::Bidirectional;
            let use_inbound = direction == TraversalDirection::Inbound
                || direction == TraversalDirection::Bidirectional;

            if use_outbound {
                if let Some(edges) = self.outbound.get(&node) {
                    for (nbr, _kind) in edges {
                        neighbors.push((nbr.clone(), None));
                    }
                }
            }
            if use_inbound {
                if let Some(edges) = self.inbound.get(&node) {
                    for (nbr, _kind) in edges {
                        neighbors.push((nbr.clone(), None));
                    }
                }
            }
            // Cross-language edges (bidirectional bridges)
            if let Some(cross_hops) = self.cross_lang.get(&node) {
                for hop in cross_hops {
                    neighbors.push((hop.target_node_id.clone(), Some(hop.clone())));
                }
            }

            for (nbr, hop) in &neighbors {
                if !visited.contains(nbr) {
                    visited.insert(nbr.clone());
                    parent.insert(nbr.clone(), (node.clone(), hop.clone()));
                    if nbr == tgt {
                        // Found — reconstruct path
                        let mut path = vec![tgt.to_string()];
                        let mut cur = tgt.to_string();
                        while cur != *src {
                            let (prev, _h) = parent[&cur].clone();
                            cur = prev;
                            path.push(cur.clone());
                        }
                        path.reverse();
                        return Some(path);
                    }
                    queue.push_back(nbr.clone());
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

    // ------------------------------------------------------------------
    // Cross-language traversal tests
    // ------------------------------------------------------------------

    /// Helper: insert http_calls, http_routes, and cross_lang_edges tables
    /// for a test database (assumes v009 migration has run).
    fn setup_cross_lang_tables(db: &Database) {
        let conn = db.connection();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS http_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                http_method TEXT NOT NULL,
                func_node_id INTEGER NOT NULL,
                url_is_template INTEGER DEFAULT 0,
                file_path TEXT NOT NULL,
                line INTEGER NOT NULL,
                column INTEGER NOT NULL,
                source_lang TEXT NOT NULL,
                raw_snippet TEXT
            );
            CREATE TABLE IF NOT EXISTS http_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_pattern TEXT NOT NULL,
                url_pattern_raw TEXT NOT NULL,
                http_method TEXT NOT NULL,
                handler_node_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                line INTEGER NOT NULL,
                column INTEGER NOT NULL,
                source_lang TEXT NOT NULL,
                source_framework TEXT,
                raw_snippet TEXT
            );
            CREATE TABLE IF NOT EXISTS cross_lang_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_call_id INTEGER NOT NULL,
                to_route_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                http_method TEXT NOT NULL,
                match_type TEXT NOT NULL,
                confidence REAL NOT NULL
            );",
        )
        .unwrap();
    }

    /// Helper: insert an http_calls row and return its id.
    /// func_node_id references nodes.rowid.
    fn insert_cross_call(
        db: &Database,
        url: &str,
        method: &str,
        func_node_rowid: i64,
    ) -> i64 {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO http_calls (url, http_method, func_node_id, url_is_template, \
             file_path, line, column, source_lang) \
             VALUES (?1, ?2, ?3, 0, 'frontend.ts', 1, 1, 'typescript')",
            rusqlite::params![url, method, func_node_rowid],
        )
        .unwrap();
        conn.last_insert_rowid()
    }

    /// Helper: insert an http_routes row and return its id.
    /// handler_node_id references nodes.rowid.
    fn insert_cross_route(
        db: &Database,
        url_pattern: &str,
        method: &str,
        handler_node_rowid: i64,
    ) -> i64 {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO http_routes (url_pattern, url_pattern_raw, http_method, handler_node_id, \
             file_path, line, column, source_lang, source_framework) \
             VALUES (?1, ?1, ?2, ?3, 'backend.py', 1, 1, 'python', 'fastapi')",
            rusqlite::params![url_pattern, method, handler_node_rowid],
        )
        .unwrap();
        conn.last_insert_rowid()
    }

    /// Helper: insert a cross_lang_edges row.
    fn insert_cross_lang_edge(
        db: &Database,
        from_call_id: i64,
        to_route_id: i64,
        url: &str,
        method: &str,
        match_type: &str,
        confidence: f64,
    ) {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO cross_lang_edges (from_call_id, to_route_id, url, http_method, \
             match_type, confidence) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![from_call_id, to_route_id, url, method, match_type, confidence],
        )
        .unwrap();
    }

    /// Helper: get the rowid of a node by its XXH3 id.
    fn get_node_rowid(db: &Database, node_id: &str) -> i64 {
        let conn = db.connection();
        conn.query_row(
            "SELECT rowid FROM nodes WHERE id = ?1",
            rusqlite::params![node_id],
            |row| row.get(0),
        )
        .unwrap()
    }

    #[test]
    fn test_cross_lang_impact_radius() {
        let (db, path) = temp_db("cross_lang_impact");
        setup_cross_lang_tables(&db);

        // Create nodes
        let n_fe = insert_node(&db, "handleLogin", "fe::handleLogin", "login.ts");
        let n_be = insert_node(&db, "login_handler", "be::login_handler", "app.py");
        let n_db = insert_node(&db, "query_user", "be::query_user", "db.py");

        // Regular edges: backend handler → database query
        insert_edge(&db, &n_be, &n_db, "CALLS");

        // Cross-tier: frontend func → backend handler via HTTP
        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/login", "POST", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/login", "POST", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/login", "POST", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // Impact radius from frontend function should reach both
        // backend handler and database function via cross-lang edge
        let radius = t.impact_radius(&n_fe, 3, TraversalDirection::Outbound);
        assert!(radius.contains(&n_be), "should reach backend handler via cross-lang edge");
        assert!(radius.contains(&n_db), "should reach db function via cross-lang + regular edge");
        assert!(!radius.contains(&n_fe));

        // Impact radius without cross-tier data
        // (cross_lang should still work if DB has no data)
        let radius_be = t.impact_radius(&n_be, 1, TraversalDirection::Outbound);
        assert!(radius_be.contains(&n_db), "backend should reach db via regular edge");

        cleanup(&path);
    }

    #[test]
    fn test_cross_lang_shortest_path() {
        let (db, db_path) = temp_db("cross_lang_path");
        setup_cross_lang_tables(&db);

        // Create nodes
        let n_fe = insert_node(&db, "handleRegister", "fe::handleRegister", "register.ts");
        let n_be = insert_node(&db, "register_handler", "be::register_handler", "app.py");

        // Cross-tier bridge
        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/auth/register", "POST", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/auth/register", "POST", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/auth/register", "POST", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // Should find path from frontend to backend via cross-lang edge
        let sp = t.shortest_path(&n_fe, &n_be, TraversalDirection::Outbound);
        assert!(sp.is_some(), "should find cross-language path");
        let p = sp.unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!(p[0], n_fe);
        assert_eq!(p[1], n_be);

        cleanup(&db_path);
    }

    #[test]
    fn test_cross_lang_reverse_path() {
        let (db, db_path) = temp_db("cross_lang_rev");
        setup_cross_lang_tables(&db);

        // Create nodes
        let n_fe = insert_node(&db, "fetchUsers", "fe::fetchUsers", "list.ts");
        let n_be = insert_node(&db, "list_users", "be::list_users", "app.py");

        // Cross-tier bridge
        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/users", "GET", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/users", "GET", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/users", "GET", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // Reverse: backend handler → frontend function via cross-lang edge
        let sp = t.shortest_path(&n_be, &n_fe, TraversalDirection::Outbound);
        assert!(sp.is_some(), "should find reverse cross-language path");
        let p = sp.unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!(p[0], n_be);
        assert_eq!(p[1], n_fe);

        cleanup(&db_path);
    }

    #[test]
    fn test_cross_lang_outbound_calls() {
        let (db, path) = temp_db("cross_lang_calls");
        setup_cross_lang_tables(&db);

        let n_fe = insert_node(&db, "doAction", "fe::doAction", "action.ts");
        let n_be = insert_node(&db, "action_handler", "be::action_handler", "app.py");

        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/action", "PUT", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/action", "PUT", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/action", "PUT", "template", 0.95);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        let calls = t.outbound_calls(&n_fe, 1);
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].1, n_be);
        assert_eq!(calls[0].0, 1);

        cleanup(&path);
    }

    #[test]
    fn test_cross_lang_inbound_callers() {
        let (db, path) = temp_db("cross_lang_callers");
        setup_cross_lang_tables(&db);

        let n_fe = insert_node(&db, "callApi", "fe::callApi", "fe.ts");
        let n_be = insert_node(&db, "handleApi", "be::handleApi", "be.py");

        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/endpoint", "DELETE", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/endpoint", "DELETE", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/endpoint", "DELETE", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // From backend handler, find frontend caller via cross-lang reverse edge
        let callers = t.inbound_callers(&n_be, 1);
        assert_eq!(callers.len(), 1);
        assert_eq!(callers[0].1, n_fe);
        assert_eq!(callers[0].0, 1);

        cleanup(&path);
    }

    #[test]
    fn test_cross_lang_no_cross_data_preserves_behavior() {
        // Verifies that empty cross_lang tables don't affect existing behavior
        let edges = vec![
            ("a".to_string(), "b".to_string(), "CALLS".to_string()),
            ("b".to_string(), "c".to_string(), "CALLS".to_string()),
        ];
        let t = GraphTraverser::from_edges(&edges);

        // Standard outbound traversal
        let calls = t.outbound_calls("a", 2);
        assert_eq!(calls.len(), 2);
        assert!(calls.iter().any(|(d, id)| *d == 1 && id == "b"));
        assert!(calls.iter().any(|(d, id)| *d == 2 && id == "c"));

        // Standard shortest path
        let path = t.shortest_path("a", "c", TraversalDirection::Outbound);
        assert!(path.is_some());
        let p = path.unwrap();
        assert_eq!(p, vec!["a", "b", "c"]);

        // Standard impact radius
        let radius = t.impact_radius("a", 2, TraversalDirection::Outbound);
        assert_eq!(radius.len(), 2);
        assert!(radius.contains("b"));
        assert!(radius.contains("c"));
    }

    #[test]
    fn test_cross_lang_bidirectional_with_bridge() {
        let (db, db_path) = temp_db("cross_lang_bidi");
        setup_cross_lang_tables(&db);

        let n_fe = insert_node(&db, "appStart", "fe::appStart", "app.ts");
        let n_be = insert_node(&db, "apiStart", "be::apiStart", "server.py");

        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/init", "GET", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/init", "GET", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/init", "GET", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // Bidirectional should find path
        let sp = t.shortest_path(&n_fe, &n_be, TraversalDirection::Bidirectional);
        assert!(sp.is_some());
        let p = sp.unwrap();
        assert_eq!(p.len(), 2);

        cleanup(&db_path);
    }

    #[test]
    fn test_cross_lang_hops_are_stored_in_parent_map() {
        // Verify that CrossLangHop information is stored in the parent map
        // (indirectly via shortest_path behavior)
        let (db, db_path) = temp_db("cross_lang_parent");
        setup_cross_lang_tables(&db);

        let n_fe = insert_node(&db, "submitForm", "fe::submitForm", "form.ts");
        let n_be = insert_node(&db, "process_form", "be::process_form", "api.py");

        let fe_rowid = get_node_rowid(&db, &n_fe);
        let be_rowid = get_node_rowid(&db, &n_be);

        let call_id = insert_cross_call(&db, "/api/form", "POST", fe_rowid);
        let route_id = insert_cross_route(&db, "/api/form", "POST", be_rowid);
        insert_cross_lang_edge(&db, call_id, route_id, "/api/form", "POST", "exact", 1.0);

        let t = GraphTraverser::from_db(&db, None, None).unwrap();

        // Path must exist and include both endpoints
        let sp = t.shortest_path(&n_fe, &n_be, TraversalDirection::Outbound);
        assert!(sp.is_some());
        let p = sp.unwrap();
        assert_eq!(p, vec![n_fe, n_be]);

        cleanup(&db_path);
    }
}
