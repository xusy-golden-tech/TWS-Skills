//! Graph export — DOT, Mermaid, and JSON serialization.
//!
//! Uses BFS traversal from the query engine to extract subgraphs, then renders
//! them in the requested format. All functions take `&Database` as the first
//! parameter, matching the pattern used by the query and analysis modules.

use crate::db::Database;
use crate::query::traversal::{GraphTraverser, TraversalDirection};
use std::collections::HashSet;

/// Export a subgraph starting from `from_node` in Graphviz DOT format.
///
/// Traverses `depth` hops outbound from the named node, then renders the
/// reachable nodes and edges as a directed graph. If `kind` is provided,
/// only edges of that kind are included in the traversal.
///
/// If `allowed_nodes` is provided, only nodes in the set are included in the output.
pub fn export_dot(
    db: &Database,
    from_node: &str,
    depth: usize,
    kind: Option<&str>,
    allowed_nodes: Option<&HashSet<String>>,
) -> String {
    let node_id = match db.find_node_id_by_name(from_node).unwrap_or(None) {
        Some(id) => id,
        None => return String::from("digraph G {\n  // node not found\n}\n"),
    };

    let kind_filter: Option<Vec<&str>> = kind.map(|k| vec![k]);
    let kind_slice: Option<&[&str]> = kind_filter.as_ref().map(|v| v.as_slice());

    let traverser = match GraphTraverser::from_db(db, kind_slice, None) {
        Ok(t) => t,
        Err(_) => return String::from("digraph G {\n  // traversal error\n}\n"),
    };

    let reachable = traverser.impact_radius(&node_id, depth, TraversalDirection::Outbound);

    // Collect all relevant nodes (start + reachable)
    let mut all_nodes: HashSet<String> = HashSet::new();
    all_nodes.insert(node_id.clone());
    for nid in &reachable {
        all_nodes.insert(nid.clone());
    }

    // Apply node-level scope filtering if allowed_nodes is specified
    if let Some(ref allowed) = allowed_nodes {
        all_nodes.retain(|nid| allowed.contains(nid));
    }

    // Collect edges between the nodes in the set
    let edges = match db.get_all_edges(kind) {
        Ok(e) => e,
        Err(_) => return String::from("digraph G {\n  // edge query error\n}\n"),
    };

    let mut dot = String::new();
    dot.push_str("digraph G {\n");
    dot.push_str("  rankdir=LR;\n");
    dot.push_str("  node [shape=box, style=rounded];\n\n");

    // Render nodes
    for nid in &all_nodes {
        if let Ok(Some((_, node_kind, name, _, _, _))) = db.get_node(nid) {
            let escaped_name = name.replace('"', "\\\"");
            let escaped_kind = node_kind.replace('"', "\\\"");
            dot.push_str(&format!(
                "  \"{}\" [label=\"{}\\n({})\"];\n",
                nid, escaped_name, escaped_kind
            ));
        }
    }

    dot.push('\n');

    // Render edges
    for (src, tgt, edge_kind, target_text) in &edges {
        if all_nodes.contains(src) && all_nodes.contains(tgt) {
            let label = if let Some(tt) = target_text {
                format!(" [label=\"{}\"]", edge_kind)
            } else {
                format!(" [label=\"{}\"]", edge_kind)
            };
            let _ = label; // Use edge_kind for label
            dot.push_str(&format!(
                "  \"{}\" -> \"{}\" [label=\"{}\"];\n",
                src, tgt, edge_kind
            ));
        }
    }

    dot.push_str("}\n");
    dot
}

/// Export a subgraph starting from `from_node` in Mermaid format.
///
/// Traverses `depth` hops outbound from the named node and renders as a
/// Mermaid flowchart (graph TD), suitable for embedding in Markdown.
///
/// If `allowed_nodes` is provided, only nodes in the set are included in the output.
pub fn export_mermaid(
    db: &Database,
    from_node: &str,
    depth: usize,
    kind: Option<&str>,
    allowed_nodes: Option<&HashSet<String>>,
) -> String {
    let node_id = match db.find_node_id_by_name(from_node).unwrap_or(None) {
        Some(id) => id,
        None => return String::from("graph TD\n  %% node not found\n"),
    };

    let kind_filter: Option<Vec<&str>> = kind.map(|k| vec![k]);
    let kind_slice: Option<&[&str]> = kind_filter.as_ref().map(|v| v.as_slice());

    let traverser = match GraphTraverser::from_db(db, kind_slice, None) {
        Ok(t) => t,
        Err(_) => return String::from("graph TD\n  %% traversal error\n"),
    };

    let reachable = traverser.impact_radius(&node_id, depth, TraversalDirection::Outbound);

    let mut all_nodes: HashSet<String> = HashSet::new();
    all_nodes.insert(node_id.clone());
    for nid in &reachable {
        all_nodes.insert(nid.clone());
    }

    // Apply node-level scope filtering if allowed_nodes is specified
    if let Some(ref allowed) = allowed_nodes {
        all_nodes.retain(|nid| allowed.contains(nid));
    }

    let edges = match db.get_all_edges(kind) {
        Ok(e) => e,
        Err(_) => return String::from("graph TD\n  %% edge query error\n"),
    };

    let mut mmd = String::new();
    mmd.push_str("graph TD\n");

    // Generate short aliases for nodes (n0, n1, n2, ...)
    let mut node_aliases: Vec<(String, String, String)> = Vec::new();
    let mut idx = 0;
    for nid in &all_nodes {
        if let Ok(Some((_, node_kind, name, _, _, _))) = db.get_node(nid) {
            let alias = format!("n{}", idx);
            let escaped_name = name.replace('(', "[").replace(')', "]");
            mmd.push_str(&format!(
                "  {}[\"{}<br/>({})\"]\n",
                alias, escaped_name, node_kind
            ));
            node_aliases.push((nid.clone(), alias, name));
            idx += 1;
        }
    }

    mmd.push('\n');

    // Render edges using aliases
    let alias_map: std::collections::HashMap<String, String> = node_aliases
        .iter()
        .map(|(nid, alias, _)| (nid.clone(), alias.clone()))
        .collect();

    for (src, tgt, edge_kind, _target_text) in &edges {
        if let (Some(src_alias), Some(tgt_alias)) =
            (alias_map.get(src), alias_map.get(tgt))
        {
            mmd.push_str(&format!(
                "  {} -->|{}| {}\n",
                src_alias, edge_kind, tgt_alias
            ));
        }
    }

    mmd
}

/// Export nodes and edges as JSON.
///
/// If `kind` is provided, only edges of that kind are included.
/// If `limit` is > 0, at most `limit` nodes are returned.
/// If `allowed_nodes` is provided, only nodes in the set are included in the output.
pub fn export_json(
    db: &Database,
    kind: Option<&str>,
    limit: usize,
    allowed_nodes: Option<&HashSet<String>>,
) -> serde_json::Value {
    let edges = db.get_all_edges(kind).unwrap_or_default();

    // Collect unique node IDs from edges
    let mut node_ids: HashSet<String> = HashSet::new();
    for (src, tgt, _, _) in &edges {
        node_ids.insert(src.clone());
        node_ids.insert(tgt.clone());
    }

    // Get node details
    let mut nodes_json: Vec<serde_json::Value> = Vec::new();
    let mut count = 0;
    for nid in &node_ids {
        if limit > 0 && count >= limit {
            break;
        }
        // Apply node-level scope filtering if allowed_nodes is specified
        if let Some(ref allowed) = allowed_nodes {
            if !allowed.contains(nid) {
                continue;
            }
        }
        if let Ok(Some((_, node_kind, name, qualified_name, language, file_path))) =
            db.get_node(nid)
        {
            nodes_json.push(serde_json::json!({
                "id": nid,
                "kind": node_kind,
                "name": name,
                "qualified_name": qualified_name,
                "language": language,
                "file_path": file_path,
            }));
            count += 1;
        }
    }

    // Build edge JSON
    let edges_json: Vec<serde_json::Value> = edges
        .iter()
        .map(|(src, tgt, ek, tt)| {
            serde_json::json!({
                "source": src,
                "target": tgt,
                "kind": ek,
                "target_text": tt,
            })
        })
        .collect();

    serde_json::json!({
        "nodes": nodes_json,
        "edges": edges_json,
    })
}

/// Convenience: export a DOT subgraph (same as export_dot but matches legacy API).
pub fn to_dot(
    nodes: &[(i64, &str)],
    edges: &[(i64, i64, &str)],
) -> String {
    let mut dot = String::new();
    dot.push_str("digraph G {\n  rankdir=LR;\n");
    for (id, label) in nodes {
        dot.push_str(&format!("  n{} [label=\"{}\"];\n", id, label));
    }
    for (src, tgt, label) in edges {
        dot.push_str(&format!("  n{} -> n{} [label=\"{}\"];\n", src, tgt, label));
    }
    dot.push_str("}\n");
    dot
}

/// Convenience: export a Mermaid subgraph (same as export_mermaid but matches legacy API).
pub fn to_mermaid(
    nodes: &[(i64, &str)],
    edges: &[(i64, i64, &str)],
) -> String {
    let mut mmd = String::new();
    mmd.push_str("graph TD\n");
    for (id, label) in nodes {
        mmd.push_str(&format!("  n{}[\"{}\"]\n", id, label));
    }
    for (src, tgt, label) in edges {
        mmd.push_str(&format!("  n{} -->|{}| n{}\n", src, label, tgt));
    }
    mmd
}

/// Convenience: export a JSON subgraph (same as export_json but matches legacy API).
pub fn to_json(
    nodes: &[(i64, &str)],
    edges: &[(i64, i64, &str)],
) -> serde_json::Value {
    let nodes_json: Vec<serde_json::Value> = nodes
        .iter()
        .map(|(id, label)| serde_json::json!({"id": id, "label": label}))
        .collect();
    let edges_json: Vec<serde_json::Value> = edges
        .iter()
        .map(|(src, tgt, label)| serde_json::json!({"source": src, "target": tgt, "label": label}))
        .collect();
    serde_json::json!({"nodes": nodes_json, "edges": edges_json})
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::connection::hash_id;
    use rusqlite::params;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_export_test_{}.db", name));
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

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str, kind: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![src, tgt, kind],
        )
        .unwrap();
    }

    // ------------------------------------------------------------------
    // DOT export tests
    // ------------------------------------------------------------------

    #[test]
    fn test_export_dot_not_found() {
        let (db, path) = setup_db("dot_not_found");
        let result = export_dot(&db, "nonexistent", 2, None, None);
        assert!(result.contains("node not found"));
        cleanup(&path);
    }

    #[test]
    fn test_export_dot_single_node() {
        let (db, path) = setup_db("dot_single");
        let conn = db.connection();
        let nid = insert_node(conn, "main_func", "src/main.py", "function");

        let result = export_dot(&db, "main_func", 1, None, None);
        assert!(result.contains("digraph G"));
        assert!(result.contains(&nid));
        assert!(result.contains("main_func"));
        assert!(result.contains("function"));
        cleanup(&path);
    }

    #[test]
    fn test_export_dot_with_edges() {
        let (db, path) = setup_db("dot_edges");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function");
        let b = insert_node(conn, "func_b", "src/b.py", "function");
        insert_edge(conn, &a, &b, "CALLS");

        let result = export_dot(&db, "func_a", 2, None, None);
        assert!(result.contains("CALLS"));
        assert!(result.contains("func_b"));
        cleanup(&path);
    }

    #[test]
    fn test_export_dot_depth_limit() {
        let (db, path) = setup_db("dot_depth");
        let conn = db.connection();
        let a = insert_node(conn, "alpha_func", "src/a.py", "function");
        let b = insert_node(conn, "beta_func", "src/b.py", "function");
        let c = insert_node(conn, "gamma_func", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &c, "CALLS");

        // depth=1 should only reach beta, not gamma
        let result = export_dot(&db, "alpha_func", 1, None, None);
        assert!(result.contains("beta_func"));
        assert!(!result.contains("gamma_func")); // gamma is at depth 2

        // depth=2 should reach both
        let result2 = export_dot(&db, "alpha_func", 2, None, None);
        assert!(result2.contains("beta_func"));
        assert!(result2.contains("gamma_func"));
        cleanup(&path);
    }

    #[test]
    fn test_export_dot_with_kind_filter() {
        let (db, path) = setup_db("dot_kind");
        let conn = db.connection();
        let a = insert_node(conn, "alpha", "src/a.py", "function");
        let b = insert_node(conn, "beta", "src/b.py", "function");
        let c = insert_node(conn, "gamma", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &a, &c, "IMPORTS");

        let result = export_dot(&db, "alpha", 2, Some("CALLS"), None);
        assert!(result.contains("beta"));
        assert!(!result.contains("gamma")); // Only CALLS edges traversed
        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // Mermaid export tests
    // ------------------------------------------------------------------

    #[test]
    fn test_export_mermaid_not_found() {
        let (db, path) = setup_db("mmd_not_found");
        let result = export_mermaid(&db, "nonexistent", 2, None, None);
        assert!(result.contains("node not found"));
        cleanup(&path);
    }

    #[test]
    fn test_export_mermaid_single_node() {
        let (db, path) = setup_db("mmd_single");
        let conn = db.connection();
        insert_node(conn, "main_func", "src/main.py", "function");

        let result = export_mermaid(&db, "main_func", 1, None, None);
        assert!(result.contains("graph TD"));
        assert!(result.contains("main_func"));
        cleanup(&path);
    }

    #[test]
    fn test_export_mermaid_with_edges() {
        let (db, path) = setup_db("mmd_edges");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function");
        let b = insert_node(conn, "func_b", "src/b.py", "function");
        insert_edge(conn, &a, &b, "CALLS");

        let result = export_mermaid(&db, "func_a", 2, None, None);
        assert!(result.contains("graph TD"));
        assert!(result.contains("CALLS"));
        cleanup(&path);
    }

    #[test]
    fn test_export_mermaid_depth_limit() {
        let (db, path) = setup_db("mmd_depth");
        let conn = db.connection();
        let a = insert_node(conn, "first", "src/a.py", "function");
        let b = insert_node(conn, "second", "src/b.py", "function");
        let c = insert_node(conn, "third", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &b, &c, "CALLS");

        let result = export_mermaid(&db, "first", 1, None, None);
        assert!(result.contains("second"));
        assert!(!result.contains("third"));
        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // JSON export tests
    // ------------------------------------------------------------------

    #[test]
    fn test_export_json_empty() {
        let (db, path) = setup_db("json_empty");
        let result = export_json(&db, None, 100, None);
        let nodes = result["nodes"].as_array().unwrap();
        assert!(nodes.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_export_json_with_data() {
        let (db, path) = setup_db("json_data");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function");
        let b = insert_node(conn, "func_b", "src/b.py", "function");
        insert_edge(conn, &a, &b, "CALLS");

        let result = export_json(&db, None, 100, None);
        let nodes = result["nodes"].as_array().unwrap();
        let edges = result["edges"].as_array().unwrap();
        assert!(!nodes.is_empty());
        assert!(!edges.is_empty());
        // Should have at least one CALLS edge
        assert!(edges.iter().any(|e| e["kind"] == "CALLS"));
        cleanup(&path);
    }

    #[test]
    fn test_export_json_kind_filter() {
        let (db, path) = setup_db("json_kind");
        let conn = db.connection();
        let a = insert_node(conn, "a", "src/a.py", "function");
        let b = insert_node(conn, "b", "src/b.py", "function");
        let c = insert_node(conn, "c", "src/c.py", "function");
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &a, &c, "IMPORTS");

        let result = export_json(&db, Some("CALLS"), 100, None);
        let edges = result["edges"].as_array().unwrap();
        // All edges should be CALLS
        for edge in edges {
            assert_eq!(edge["kind"], "CALLS");
        }
        cleanup(&path);
    }

    #[test]
    fn test_export_json_limit() {
        let (db, path) = setup_db("json_limit");
        let conn = db.connection();
        for i in 0..5 {
            insert_node(conn, &format!("func_{}", i), &format!("src/f{}.py", i), "function");
        }

        let result = export_json(&db, None, 2, None);
        let nodes = result["nodes"].as_array().unwrap();
        assert!(nodes.len() <= 2, "Should limit to 2 nodes, got {}", nodes.len());
        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // Legacy API tests
    // ------------------------------------------------------------------

    #[test]
    fn test_legacy_to_dot() {
        let nodes = vec![(1i64, "main"), (2i64, "helper")];
        let edges = vec![(1i64, 2i64, "calls")];
        let result = to_dot(&nodes, &edges);
        assert!(result.contains("digraph G"));
        assert!(result.contains("main"));
        assert!(result.contains("helper"));
        assert!(result.contains("calls"));
    }

    #[test]
    fn test_legacy_to_mermaid() {
        let nodes = vec![(1i64, "main"), (2i64, "helper")];
        let edges = vec![(1i64, 2i64, "calls")];
        let result = to_mermaid(&nodes, &edges);
        assert!(result.contains("graph TD"));
        assert!(result.contains("main"));
        assert!(result.contains("calls"));
    }

    #[test]
    fn test_legacy_to_json() {
        let nodes = vec![(1i64, "main")];
        let edges = vec![(1i64, 1i64, "self")];
        let result = to_json(&nodes, &edges);
        assert_eq!(result["nodes"].as_array().unwrap().len(), 1);
        assert_eq!(result["edges"].as_array().unwrap().len(), 1);
    }
}
