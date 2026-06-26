//! GQL (Graph Query Language) — full query engine.
//!
//! Provides lexing, parsing, planning, and execution of GQL queries
//! against the code-graph SQLite database.
//!
//! # Quick start
//!
//! ```ignore
//! use tws_graph_core::gql::execute_gql;
//! let results = execute_gql(&db, "FIND function WHERE name MATCHES 'auth'")?;
//! ```
//!
//! Supported statement types:
//! - `FIND kind [WHERE ...] [RETURN ...] [ORDER BY ...] [LIMIT ...]`
//! - `IMPACT qualified.name`
//! - `CALLS qualified.name [--inbound]`
//! - `TRACE source target`

pub mod ast;
pub mod executor;
pub mod lexer;
pub mod parser;
pub mod planner;

// Re-export the public API
pub use executor::execute;
pub use parser::parse;
pub use planner::plan;

use crate::db::Database;
use anyhow::Result;
use serde_json::Value;
use std::collections::HashMap;

/// Execute a GQL query string against the database and return results.
///
/// This is the primary entry point for the GQL engine. It handles
/// lexing, parsing, planning, and execution in one call.
///
/// # Arguments
/// * `db` - The code-graph database handle.
/// * `query` - A GQL query string.
///
/// # Returns
/// A vector of rows, each as a `HashMap<String, Value>`.
///
/// # Examples
///
/// ```
/// // Will be tested in integration tests
/// ```
pub fn execute_gql(
    db: &Database,
    query: &str,
) -> Result<Vec<HashMap<String, Value>>> {
    let statement = parse(query)?;
    let plan = plan(&statement)?;
    execute(db, &plan)
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

    fn temp_db_path(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("tws_test_gql_mod_{}.db", name))
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    fn insert_node(
        conn: &rusqlite::Connection,
        name: &str,
        qname: &str,
        kind: &str,
        file_path: &str,
        lang: &str,
    ) -> String {
        let node_id = hash_id(file_path, qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![node_id, kind, name, qname, file_path, lang, 1, 1, ts],
        )
        .unwrap();
        node_id
    }

    fn insert_edge(
        conn: &rusqlite::Connection,
        source: &str,
        target: &str,
        kind: &str,
    ) {
        conn.execute(
            "INSERT INTO edges (source, target, target_text, kind, source_loc, provenance) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                source,
                target,
                None::<String>,
                kind,
                Some("test.rs:1:1"),
                Some("test"),
            ],
        )
        .unwrap();
    }

    #[test]
    fn test_execute_gql_find() {
        let path = temp_db_path("find");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            insert_node(conn, "auth_handler", "src::auth_handler", "function", "src/auth.rs", "rust");
            insert_node(conn, "parse_json", "src::parse_json", "function", "src/parse.rs", "rust");
            insert_node(conn, "validate_auth", "src::validate_auth", "function", "src/auth.rs", "rust");

            let results = execute_gql(&db, "FIND function WHERE name MATCHES 'auth'").unwrap();
            assert_eq!(results.len(), 2);
            for r in &results {
                let name = r.get("name").unwrap().as_str().unwrap();
                assert!(name.contains("auth"));
            }
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_find_return_limit() {
        let path = temp_db_path("find_return");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            for i in 0..5 {
                insert_node(
                    conn,
                    &format!("class_{}", i),
                    &format!("src::class_{}", i),
                    "class",
                    "src/lib.rs",
                    "rust",
                );
            }

            let results = execute_gql(&db, "FIND class RETURN name LIMIT 2").unwrap();
            assert_eq!(results.len(), 2);
            // Should have "name" column
            assert!(results[0].contains_key("name"));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_impact() {
        let path = temp_db_path("impact");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let caller_id = insert_node(conn, "caller", "src::caller", "function", "src/main.rs", "rust");
            let target_id = insert_node(conn, "target", "src::target", "function", "src/main.rs", "rust");
            insert_edge(conn, &caller_id, &target_id, "CALLS");

            let results = execute_gql(&db, "IMPACT target").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].get("name").unwrap(), &Value::String("caller".to_string()));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_calls() {
        let path = temp_db_path("calls");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let src = insert_node(conn, "main", "src::main", "function", "src/main.rs", "rust");
            let tgt = insert_node(conn, "init", "src::init", "function", "src/db.rs", "rust");
            insert_edge(conn, &src, &tgt, "CALLS");

            let results = execute_gql(&db, "CALLS main").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].get("name").unwrap(), &Value::String("init".to_string()));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_calls_inbound() {
        let path = temp_db_path("calls_in");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let caller = insert_node(conn, "caller", "src::caller", "function", "src/a.rs", "rust");
            let callee = insert_node(conn, "helper", "src::helper", "function", "src/helper.rs", "rust");
            insert_edge(conn, &caller, &callee, "CALLS");

            let results = execute_gql(&db, "CALLS helper --inbound").unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].get("name").unwrap(), &Value::String("caller".to_string()));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_trace() {
        let path = temp_db_path("trace");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let a = insert_node(conn, "main", "src::main", "function", "src/main.rs", "rust");
            let b = insert_node(conn, "init", "src::init", "function", "src/init.rs", "rust");

            insert_edge(conn, &a, &b, "CALLS");

            let results = execute_gql(&db, "TRACE main init").unwrap();
            assert!(!results.is_empty());
            let path = results[0].get("path").unwrap().as_str().unwrap();
            assert!(path.contains("main"));
            assert!(path.contains("init"));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_gql_case_insensitive() {
        let path = temp_db_path("case");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            insert_node(conn, "my_func", "src::my_func", "function", "src/lib.rs", "rust");

            // Using lowercase keywords should work
            let results = execute_gql(&db, "find function where name matches 'my_func'").unwrap();
            assert_eq!(results.len(), 1);
        }

        cleanup(&path);
    }
}
