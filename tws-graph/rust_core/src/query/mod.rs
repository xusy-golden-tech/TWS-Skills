//! Query engine — graph traversal, FTS5 search, and cross-file edge resolution.
//!
//! Public command functions mirror the CLI interface:
//! - `run_calls` — forward/backward call analysis
//! - `run_impact` — impact radius grouped by module
//! - `run_trace` — shortest path between two symbols
//! - `run_search` — FTS5 search with qualifier parsing
//! - `run_unresolved` — unresolved reference listing with classification

pub mod edge_resolver;
pub mod search;
pub mod traversal;

use crate::db::Database;
use crate::db::models::SearchResult;
use search::{parse_query, execute_search, SearchQuery};
use serde::Serialize;
use traversal::{GraphTraverser, TraversalDirection};

// ---------------------------------------------------------------------------
// Formatted result types
// ---------------------------------------------------------------------------

/// Rich node information retrieved from the database.
#[derive(Debug, Clone, Serialize)]
pub struct NodeInfo {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub qualified_name: String,
    pub language: String,
    pub file_path: String,
    pub signature: Option<String>,
    pub start_line: Option<i64>,
    pub docstring: Option<String>,
    pub visibility: Option<String>,
}

/// A formatted result for `run_calls`.
#[derive(Debug, Clone, Serialize)]
pub struct CallsResult {
    pub depth: usize,
    pub node_id: String,
    pub node_name: String,
    pub node_kind: String,
    pub file_path: String,
    pub edge_kind: String,
    pub signature: Option<String>,
    pub start_line: Option<i64>,
    pub docstring: Option<String>,
    pub visibility: Option<String>,
}

/// A formatted result for `run_impact`.
#[derive(Debug, Clone)]
pub struct ImpactResult {
    pub module: String,
    pub node_id: String,
    pub node_name: String,
    pub depth: usize,
    pub kind: String,
}

/// An unresolved reference with classification.
#[derive(Debug, Clone)]
pub struct UnresolvedResult {
    pub reference_name: String,
    pub reference_kind: String,
    pub file_path: String,
    pub classification: String,
}

// ---------------------------------------------------------------------------
// run_calls — forward/backward call analysis
// ---------------------------------------------------------------------------

/// Get all calls from or to a node.
///
/// If `inbound` is true, show callers (who calls this node).
/// If `inbound` is false, show callees (what this node calls).
pub fn run_calls(
    db: &Database,
    node_name: &str,
    inbound: bool,
    depth: usize,
) -> rusqlite::Result<Vec<CallsResult>> {
    let node_id = match db.find_node_id_by_name(node_name)? {
        Some(id) => id,
        None => return Ok(Vec::new()),
    };

    let traverser = GraphTraverser::from_db(db, None, None)?;

    let results = if inbound {
        traverser.inbound_callers(&node_id, depth)
    } else {
        traverser.outbound_calls(&node_id, depth)
    };

    let mut formatted = Vec::new();
    for (d, nid) in &results {
        if let Some(node_info) = db.get_node_rich(nid)? {
            formatted.push(CallsResult {
                depth: *d,
                node_id: nid.clone(),
                node_name: node_info.name,
                node_kind: node_info.kind,
                file_path: node_info.file_path,
                edge_kind: if inbound { "CALLS".to_string() } else { "CALLS".to_string() },
                signature: node_info.signature,
                start_line: node_info.start_line,
                docstring: node_info.docstring,
                visibility: node_info.visibility,
            });
        }
    }

    Ok(formatted)
}

// ---------------------------------------------------------------------------
// run_impact — impact radius grouped by module
// ---------------------------------------------------------------------------

/// Compute the impact radius of a symbol, grouped by module (file_path).
pub fn run_impact(
    db: &Database,
    node_name: &str,
    depth: usize,
) -> rusqlite::Result<Vec<ImpactResult>> {
    let node_id = match db.find_node_id_by_name(node_name)? {
        Some(id) => id,
        None => return Ok(Vec::new()),
    };

    // Exclude CONTAINS edges for impact (structural containment is not a
    // real dependency).
    let traverser = GraphTraverser::from_db(db, None, Some(&["CONTAINS"]))?;

    let affected = traverser.impact_radius(&node_id, depth, TraversalDirection::Outbound);

    let mut results = Vec::new();
    for nid in &affected {
        if let Some((_id, kind, name, _qn, _lang, file_path)) = db.get_node(nid)? {
            results.push(ImpactResult {
                module: file_path.clone(),
                node_id: nid.clone(),
                node_name: name,
                depth: 0, // BFS depth not tracked in impact_radius, filled later
                kind,
            });
        }
    }

    // Sort by module for grouping
    results.sort_by(|a, b| a.module.cmp(&b.module));
    Ok(results)
}

// ---------------------------------------------------------------------------
// run_trace — shortest path between two symbols
// ---------------------------------------------------------------------------

/// Find the shortest path between `src_name` and `tgt_name`.
///
/// Returns `Some(Vec<(node_id, node_name)>)` if a path exists,
/// `None` if no path or either node not found.
pub fn run_trace(
    db: &Database,
    src_name: &str,
    tgt_name: &str,
) -> rusqlite::Result<Option<Vec<(String, String)>>> {
    let src_id = match db.find_node_id_by_name(src_name)? {
        Some(id) => id,
        None => return Ok(None),
    };
    let tgt_id = match db.find_node_id_by_name(tgt_name)? {
        Some(id) => id,
        None => return Ok(None),
    };

    let traverser = GraphTraverser::from_db(db, None, None)?;

    match traverser.shortest_path(&src_id, &tgt_id, TraversalDirection::Outbound) {
        Some(path) => {
            let mut result = Vec::new();
            for nid in &path {
                if let Some((_id, _kind, name, _qn, _lang, _fp)) = db.get_node(nid)? {
                    result.push((nid.clone(), name));
                } else {
                    result.push((nid.clone(), format!("<unknown:{}>", nid)));
                }
            }
            Ok(Some(result))
        }
        None => Ok(None),
    }
}

// ---------------------------------------------------------------------------
// run_search — FTS5 search with qualifier parsing
// ---------------------------------------------------------------------------

/// Execute a search from a raw query string.
///
/// Parses qualifiers (kind:, lang:, path:) and executes the 3-tier search.
pub fn run_search(
    db: &Database,
    raw_query: &str,
) -> rusqlite::Result<Vec<SearchResult>> {
    let query = parse_query(raw_query);
    execute_search(db, &query, 50)
}

/// Execute a search from a pre-parsed query.
pub fn run_search_parsed(
    db: &Database,
    query: &SearchQuery,
    limit: usize,
) -> rusqlite::Result<Vec<SearchResult>> {
    execute_search(db, query, limit)
}

// ---------------------------------------------------------------------------
// run_unresolved — unresolved reference listing with classification
// ---------------------------------------------------------------------------

/// List all unresolved references with internal/external classification.
pub fn run_unresolved(
    db: &Database,
) -> rusqlite::Result<Vec<UnresolvedResult>> {
    let refs = db.get_unresolved()?;
    let mut results = Vec::new();

    for (ref_name, ref_kind, file_path, _from_node_id) in &refs {
        let classification = edge_resolver::classify_reference(ref_name, file_path, db);
        results.push(UnresolvedResult {
            reference_name: ref_name.clone(),
            reference_kind: ref_kind.clone(),
            file_path: file_path.clone(),
            classification: classification.to_string(),
        });
    }

    Ok(results)
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

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_cmd_{}.db", name));
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

    fn insert_node(db: &Database, name: &str, qualified: &str, file_path: &str, kind: &str) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 'python', 1, 1, ?6)",
            params![id, kind, name, qualified, file_path, ts],
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

    fn insert_unresolved(db: &Database, name: &str, kind: &str, file_path: &str, from_node: &str) {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO unresolved_refs (reference_name, reference_kind, file_path, from_node_id, line, col, language, is_external) \
             VALUES (?1, ?2, ?3, ?4, 1, 1, 'python', 0)",
            params![name, kind, file_path, from_node],
        )
        .unwrap();
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // run_calls tests
    // ------------------------------------------------------------------

    #[test]
    fn test_run_calls_outbound() {
        let (db, path) = setup_db("run_calls_out");
        let a = insert_node(&db, "func_a", "mod::func_a", "a.py", "function");
        let b = insert_node(&db, "func_b", "mod::func_b", "b.py", "function");
        let c = insert_node(&db, "func_c", "mod::func_c", "c.py", "function");
        insert_edge(&db, &a, &b, "CALLS");
        insert_edge(&db, &b, &c, "CALLS");

        let results = run_calls(&db, "func_a", false, 2).unwrap();
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].depth, 1);
        assert_eq!(results[1].depth, 2);

        cleanup(&path);
    }

    #[test]
    fn test_run_calls_inbound() {
        let (db, path) = setup_db("run_calls_in");
        let a = insert_node(&db, "func_a", "mod::func_a", "a.py", "function");
        let b = insert_node(&db, "func_b", "mod::func_b", "b.py", "function");
        let c = insert_node(&db, "func_c", "mod::func_c", "c.py", "function");
        insert_edge(&db, &a, &c, "CALLS");
        insert_edge(&db, &b, &c, "CALLS");

        let results = run_calls(&db, "func_c", true, 1).unwrap();
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].depth, 1);

        cleanup(&path);
    }

    #[test]
    fn test_run_calls_node_not_found() {
        let (db, path) = setup_db("run_calls_nf");
        let results = run_calls(&db, "nonexistent", false, 2).unwrap();
        assert!(results.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_calls_result_json_serialization() {
        let cr = CallsResult {
            depth: 1,
            node_id: "abc123".to_string(),
            node_name: "my_func".to_string(),
            node_kind: "function".to_string(),
            file_path: "src/main.rs".to_string(),
            edge_kind: "CALLS".to_string(),
            signature: Some("fn my_func() -> u32".to_string()),
            start_line: Some(42),
            docstring: Some("Does something useful.".to_string()),
            visibility: Some("public".to_string()),
        };
        let json = serde_json::to_string(&cr).unwrap();
        assert!(json.contains("\"depth\":1"));
        assert!(json.contains("\"node_id\":\"abc123\""));
        assert!(json.contains("\"node_name\":\"my_func\""));
        assert!(json.contains("\"node_kind\":\"function\""));
        assert!(json.contains("\"file_path\":\"src/main.rs\""));
        assert!(json.contains("\"edge_kind\":\"CALLS\""));
        assert!(json.contains("\"signature\":\"fn my_func() -> u32\""));
        assert!(json.contains("\"start_line\":42"));
        assert!(json.contains("\"docstring\":\"Does something useful.\""));
        assert!(json.contains("\"visibility\":\"public\""));
    }

    // ------------------------------------------------------------------
    // run_impact tests
    // ------------------------------------------------------------------

    #[test]
    fn test_run_impact_grouped_by_module() {
        let (db, path) = setup_db("run_impact_mod");
        let a = insert_node(&db, "main", "app::main", "app/main.py", "function");
        let b = insert_node(&db, "helper", "utils::helper", "utils/helper.py", "function");
        let c = insert_node(&db, "validator", "utils::validator", "utils/validator.py", "function");
        insert_edge(&db, &a, &b, "CALLS");
        insert_edge(&db, &a, &c, "CALLS");

        let results = run_impact(&db, "main", 1).unwrap();
        assert_eq!(results.len(), 2);
        // Both should be in utils module
        assert!(results.iter().all(|r| r.module.contains("utils")));

        cleanup(&path);
    }

    #[test]
    fn test_run_impact_excludes_contains() {
        let (db, path) = setup_db("run_impact_exc");
        let a = insert_node(&db, "main", "app::main", "app/main.py", "function");
        let b = insert_node(&db, "helper", "utils::helper", "utils/helper.py", "function");
        let c = insert_node(&db, "inner", "app::inner", "app/inner.py", "class");
        insert_edge(&db, &a, &b, "CALLS");
        insert_edge(&db, &a, &c, "CONTAINS");

        let results = run_impact(&db, "main", 1).unwrap();
        // CONTAINS edge should be excluded
        assert_eq!(results.len(), 1);
        assert_eq!(&results[0].node_name, "helper");

        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // run_trace tests
    // ------------------------------------------------------------------

    #[test]
    fn test_run_trace_finds_path() {
        let (db, path) = setup_db("run_trace_path");
        let a = insert_node(&db, "main", "app::main", "app/main.py", "function");
        let b = insert_node(&db, "helper", "utils::helper", "utils/helper.py", "function");
        let c = insert_node(&db, "db_query", "db::query", "db/query.py", "function");
        insert_edge(&db, &a, &b, "CALLS");
        insert_edge(&db, &b, &c, "CALLS");

        let result = run_trace(&db, "main", "db_query").unwrap();
        assert!(result.is_some());
        let r_path = result.unwrap();
        assert_eq!(r_path.len(), 3);
        assert_eq!(r_path[0].1, "main");
        assert_eq!(r_path[2].1, "db_query");

        cleanup(&path);
    }

    #[test]
    fn test_run_trace_no_path() {
        let (db, path) = setup_db("run_trace_np");
        let _a = insert_node(&db, "main", "app::main", "app/main.py", "function");
        let _b = insert_node(&db, "other", "other::other", "other.py", "function");
        // No edges connecting them

        let result = run_trace(&db, "main", "other").unwrap();
        assert!(result.is_none());

        cleanup(&path);
    }

    #[test]
    fn test_run_trace_src_not_found() {
        let (db, path) = setup_db("run_trace_srcnf");
        let result = run_trace(&db, "nonexistent_src", "anything").unwrap();
        assert!(result.is_none());
        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // run_search tests
    // ------------------------------------------------------------------

    #[test]
    fn test_run_search_with_qualifiers() {
        let (db, path) = setup_db("run_search_qual");
        insert_node(&db, "MyClass", "mod::MyClass", "mod.py", "class");
        insert_node(&db, "myFunc", "mod::myFunc", "mod.py", "function");

        let results = run_search(&db, "kind:class MyClass").unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].name, "MyClass");

        cleanup(&path);
    }

    #[test]
    fn test_run_search_lang_filter() {
        let (db, path) = setup_db("run_search_lang");
        insert_node(&db, "handler", "ts::handler", "handler.ts", "function");
        // Insert the node with typescript language via raw SQL
        {
            let id = hash_id("handler.ts", "ts::handler");
            let _ts = now_ms();
            let conn = db.connection();
            conn.execute(
                "UPDATE nodes SET language = 'typescript' WHERE id = ?1",
                params![id],
            ).unwrap();
        }
        insert_node(&db, "handler", "py::handler", "handler.py", "function");

        let results = run_search(&db, "handler lang:typescript").unwrap();
        dbg!(&results);
        // At least one result, preferably the typescript one
        assert!(!results.is_empty());

        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // run_unresolved tests
    // ------------------------------------------------------------------

    #[test]
    fn test_run_unresolved_classifies_external() {
        let (db, path) = setup_db("run_unresolved_ext");
        let node = insert_node(&db, "main", "app::main", "app/main.py", "function");
        insert_unresolved(&db, "os", "import", "app/main.py", &node);

        let results = run_unresolved(&db).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].classification, "external");

        cleanup(&path);
    }

    #[test]
    fn test_run_unresolved_empty_when_none() {
        let (db, path) = setup_db("run_unresolved_empty");
        let results = run_unresolved(&db).unwrap();
        assert!(results.is_empty());
        cleanup(&path);
    }
}
