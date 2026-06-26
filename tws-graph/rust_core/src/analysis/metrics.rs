//! Module metrics — computes cohesion, coupling, and instability for each module.
//!
//! Nodes are grouped into modules by directory/package prefix.
//! - **Cohesion**: internal edges / total edges (higher is better, max 1.0)
//! - **Coupling**: external edges / total edges (lower is better, min 0.0)
//! - **Instability**: outbound / (inbound + outbound) (0 = stable, 1 = unstable)

use crate::db::Database;
use std::collections::{HashMap, HashSet};

/// Metrics for a single module.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ModuleMetrics {
    /// Internal edges / total edges (0.0 to 1.0, higher is better).
    pub cohesion: f64,
    /// External edges / total edges (0.0 to 1.0, lower is better).
    pub coupling: f64,
    /// Outbound edges / (inbound + outbound) (0.0 = stable, 1.0 = unstable).
    pub instability: f64,
    /// Number of nodes in this module.
    pub node_count: usize,
    /// Number of internal edges.
    pub internal_edges: usize,
    /// Number of external edges.
    pub external_edges: usize,
    /// Number of inbound edges from other modules.
    pub inbound: usize,
    /// Number of outbound edges to other modules.
    pub outbound: usize,
}

/// Compute module-level metrics: cohesion, coupling, and instability.
///
/// Nodes are grouped by module prefix extracted from their file path.
/// The module prefix is the first two path segments (e.g. `"src/core/"`).
pub fn compute_metrics(db: &Database) -> HashMap<String, ModuleMetrics> {
    let conn = db.connection();

    // Collect all nodes with their file path
    let mut stmt = conn
        .prepare("SELECT id, file_path FROM nodes")
        .expect("failed to prepare nodes query");
    let node_modules: HashMap<String, String> = stmt
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .map(|(id, fp)| (id, module_prefix(&fp)))
        .collect();

    // Get all edges
    let mut edge_stmt = conn
        .prepare("SELECT source, target, kind FROM edges")
        .expect("failed to prepare edges query");
    let all_edges: Vec<(String, String, String)> = edge_stmt
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

    // Build module sets: module_name -> set of node_ids
    let mut module_nodes: HashMap<String, HashSet<String>> = HashMap::new();
    for (nid, module) in &node_modules {
        module_nodes
            .entry(module.clone())
            .or_default()
            .insert(nid.clone());
    }

    // Calculate metrics per module
    let mut metrics_map: HashMap<String, ModuleMetrics> = HashMap::new();
    for module in module_nodes.keys() {
        metrics_map.insert(module.clone(), ModuleMetrics::default());
    }

    // For each module, compute edge statistics
    for module in module_nodes.keys() {
        let nodes = module_nodes.get(module).unwrap();
        let node_count = nodes.len();

        let mut internal = 0usize;
        let mut external = 0usize;
        let mut outbound = 0usize;
        let mut inbound = 0usize;

        for (src, tgt, _kind) in &all_edges {
            let src_module = node_modules.get(src).cloned().unwrap_or_default();
            let tgt_module = node_modules.get(tgt).cloned().unwrap_or_default();

            // Only count edges where source is in this module
            if src_module == *module {
                if tgt_module == *module {
                    internal += 1;
                } else {
                    external += 1;
                    outbound += 1;
                }
            }

            // Count inbound: target is in this module, source is outside
            if tgt_module == *module && src_module != *module {
                inbound += 1;
            }
        }

        let total = internal + external;
        let cohesion = if total > 0 {
            internal as f64 / total as f64
        } else {
            0.0
        };
        let coupling = if total > 0 {
            external as f64 / total as f64
        } else {
            0.0
        };
        let instability = if inbound + outbound > 0 {
            outbound as f64 / (inbound + outbound) as f64
        } else {
            0.0
        };

        metrics_map.insert(
            module.clone(),
            ModuleMetrics {
                cohesion,
                coupling,
                instability,
                node_count,
                internal_edges: internal,
                external_edges: external,
                inbound,
                outbound,
            },
        );
    }

    metrics_map
}

/// Extract the module prefix from a file path.
///
/// Uses the first two directory segments (e.g. `"src/core/"` from `"src/core/util.py"`).
/// If there is only one segment, uses just that (e.g. `"src/"` from `"src/main.py"`).
fn module_prefix(file_path: &str) -> String {
    let parts: Vec<&str> = file_path.split('/').collect();
    if parts.len() >= 2 {
        // Strip file extension from the last part to get a clean module prefix
        let last = strip_extension(parts[1]);
        format!("{}/{}/", parts[0], last)
    } else if parts.len() == 1 {
        file_path.to_string()
    } else {
        file_path.to_string()
    }
}

/// Strip the file extension from a path component.
fn strip_extension(s: &str) -> &str {
    match s.rfind('.') {
        Some(pos) => &s[..pos],
        None => s,
    }
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
        let path = std::env::temp_dir().join(format!("tws_metrics_test_{}.db", name));
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

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, 'CALLS')",
            params![src, tgt],
        )
        .unwrap();
    }

    #[test]
    fn test_single_module_no_edges() {
        let (db, path) = setup_db("single_module");
        let conn = db.connection();

        insert_node(conn, "a", "src/core/a.py");
        insert_node(conn, "b", "src/core/b.py");

        let metrics = compute_metrics(&db);
        assert_eq!(metrics.len(), 1);
        let m = metrics.get("src/core/").unwrap();
        assert_eq!(m.node_count, 2);
        assert!((m.cohesion - 0.0).abs() < 0.001, "Cohesion should be 0 with no edges");
        assert!((m.coupling - 0.0).abs() < 0.001, "Coupling should be 0 with no edges");
        assert!((m.instability - 0.0).abs() < 0.001, "Instability should be 0 with no edges");

        cleanup(&path);
    }

    #[test]
    fn test_high_cohesion() {
        let (db, path) = setup_db("high_cohesion");
        let conn = db.connection();

        // All edges within same module: src/core/
        let a = insert_node(conn, "a", "src/core/a.py");
        let b = insert_node(conn, "b", "src/core/b.py");
        let c = insert_node(conn, "c", "src/core/c.py");
        insert_edge(conn, &a, &b);
        insert_edge(conn, &b, &c);
        insert_edge(conn, &c, &a);

        let metrics = compute_metrics(&db);
        let m = metrics.get("src/core/").unwrap();
        assert_eq!(m.internal_edges, 3);
        assert_eq!(m.external_edges, 0);
        assert!((m.cohesion - 1.0).abs() < 0.001, "All internal edges => cohesion=1.0");
        assert!((m.coupling - 0.0).abs() < 0.001, "No external edges => coupling=0.0");

        cleanup(&path);
    }

    #[test]
    fn test_high_coupling() {
        let (db, path) = setup_db("high_coupling");
        let conn = db.connection();

        // Two modules: src/core/ and src/api/
        let core_a = insert_node(conn, "core_a", "src/core/a.py");
        let api_b = insert_node(conn, "api_b", "src/api/b.py");
        let api_c = insert_node(conn, "api_c", "src/api/c.py");

        // All edges cross module boundaries
        insert_edge(conn, &core_a, &api_b);
        insert_edge(conn, &core_a, &api_c);
        insert_edge(conn, &api_b, &core_a);

        let metrics = compute_metrics(&db);

        // core module: 2 outbound, 1 inbound
        let core_m = metrics.get("src/core/").unwrap();
        assert_eq!(core_m.node_count, 1);
        assert_eq!(core_m.internal_edges, 0);
        assert_eq!(core_m.external_edges, 2);
        assert!((core_m.cohesion - 0.0).abs() < 0.001);
        assert!((core_m.coupling - 1.0).abs() < 0.001);
        // instability = outbound / (inbound + outbound) = 2 / (1 + 2) = 0.666...
        assert!((core_m.instability - 2.0 / 3.0).abs() < 0.01);

        // api module: 1 outbound, 2 inbound
        let api_m = metrics.get("src/api/").unwrap();
        assert_eq!(api_m.node_count, 2);
        assert_eq!(api_m.internal_edges, 0);
        assert_eq!(api_m.external_edges, 1);
        assert!((api_m.instability - 1.0 / 3.0).abs() < 0.01);

        cleanup(&path);
    }

    #[test]
    fn test_instability() {
        let (db, path) = setup_db("instability_test");
        let conn = db.connection();

        // Module with only outbound edges => instability = 1.0 (unstable)
        let mod_a = insert_node(conn, "a", "src/unstable/a.py");
        let mod_b = insert_node(conn, "b", "src/stable/b.py");

        insert_edge(conn, &mod_a, &mod_b); // a -> b (outbound for unstable, inbound for stable)

        let metrics = compute_metrics(&db);

        let unstable = metrics.get("src/unstable/").unwrap();
        assert!((unstable.instability - 1.0).abs() < 0.001,
            "Only outbound => instability=1.0, got {}", unstable.instability);

        let stable = metrics.get("src/stable/").unwrap();
        assert!((stable.instability - 0.0).abs() < 0.001,
            "Only inbound => instability=0.0, got {}", stable.instability);

        cleanup(&path);
    }

    #[test]
    fn test_module_prefix() {
        assert_eq!(module_prefix("src/core/util.py"), "src/core/");
        assert_eq!(module_prefix("src/main.py"), "src/main/");
        assert_eq!(module_prefix("main.py"), "main.py");
    }
}
