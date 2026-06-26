//! GQL executor — executes a plan against the SQLite database.
//!
//! Translates logical plan operators into SQL and executes them against
//! the `Database` handle, returning results as `HashMap<String, Value>` rows.

use crate::db::Database;
use anyhow::{bail, Result};
use serde_json::Value;
use std::collections::HashMap;

use super::ast::Statement;
use super::planner::{Operator, Plan};

/// Execute a GQL plan and return results as maps of column name to JSON value.
pub fn execute(db: &Database, plan: &Plan) -> Result<Vec<HashMap<String, Value>>> {
    if let Some(ref stmt) = plan.statement {
        // Special statement types (IMPACT, CALLS, TRACE) — use Database methods
        return execute_special(db, stmt);
    }

    // Build and execute SQL from the logical plan operators
    execute_sql(db, &plan.operators)
}

/// Execute special statement types using the Database API directly.
fn execute_special(db: &Database, stmt: &Statement) -> Result<Vec<HashMap<String, Value>>> {
    match stmt {
        Statement::Impact { target } => execute_impact(db, target),
        Statement::Calls { target, inbound } => execute_calls(db, target, *inbound),
        Statement::Trace { source, target } => execute_trace(db, source, target),
        _ => bail!("unexpected statement type in execute_special"),
    }
}

/// IMPACT target: find all nodes that depend on `target` (inbound edges).
fn execute_impact(db: &Database, target: &str) -> Result<Vec<HashMap<String, Value>>> {
    let conn = db.connection();

    // Find the target node by name or qualified_name
    let node_id = db
        .find_node_id_by_name(target)?
        .ok_or_else(|| anyhow::anyhow!("node not found: {}", target))?;

    // Get all inbound edges
    let edges = db.get_inbound_edges(&node_id)?;
    let mut results = Vec::new();

    for (_, source_id, kind, _) in &edges {
        if let Some(node) = db.get_node(source_id)? {
            let mut row = HashMap::new();
            row.insert("id".to_string(), Value::String(node.0));
            row.insert("kind".to_string(), Value::String(node.1));
            row.insert("name".to_string(), Value::String(node.2));
            row.insert("qualified_name".to_string(), Value::String(node.3));
            row.insert("language".to_string(), Value::String(node.4));
            row.insert("file_path".to_string(), Value::String(node.5));
            row.insert("edge_kind".to_string(), Value::String(kind.clone()));
            results.push(row);
        }
    }

    Ok(results)
}

/// CALLS target: find all outbound (or inbound, if flag is set) CALLS edges.
fn execute_calls(db: &Database, target: &str, inbound: bool) -> Result<Vec<HashMap<String, Value>>> {
    let node_id = db
        .find_node_id_by_name(target)?
        .ok_or_else(|| anyhow::anyhow!("node not found: {}", target))?;

    let edges = if inbound {
        db.get_inbound_edges(&node_id)?
    } else {
        db.get_outbound_edges(&node_id)?
    };

    let mut results = Vec::new();
    for (_, other_id, kind, _) in &edges {
        let node_id_to_fetch = if inbound { other_id } else { other_id };
        if let Some(node) = db.get_node(node_id_to_fetch)? {
            let mut row = HashMap::new();
            row.insert("id".to_string(), Value::String(node.0));
            row.insert("kind".to_string(), Value::String(node.1));
            row.insert("name".to_string(), Value::String(node.2));
            row.insert("qualified_name".to_string(), Value::String(node.3));
            row.insert("language".to_string(), Value::String(node.4));
            row.insert("file_path".to_string(), Value::String(node.5));
            row.insert("edge_kind".to_string(), Value::String(kind.clone()));
            results.push(row);
        }
    }

    Ok(results)
}

/// TRACE src tgt: find a path from source to target via BFS on the edges table.
fn execute_trace(db: &Database, source: &str, target: &str) -> Result<Vec<HashMap<String, Value>>> {
    let src_id = db
        .find_node_id_by_name(source)?
        .ok_or_else(|| anyhow::anyhow!("source node not found: {}", source))?;
    let tgt_id = db
        .find_node_id_by_name(target)?
        .ok_or_else(|| anyhow::anyhow!("target node not found: {}", target))?;

    if src_id == tgt_id {
        let mut results = Vec::new();
        let mut row = HashMap::new();
        row.insert("path".to_string(), Value::String(source.to_string()));
        row.insert("length".to_string(), Value::Number(0.into()));
        results.push(row);
        return Ok(results);
    }

    let conn = db.connection();

    // BFS: (current_id, path_vec)
    let mut visited: HashMap<String, usize> = HashMap::new();
    // Queue: (node_id, path)
    let mut queue: Vec<(String, Vec<String>)> = Vec::new();
    queue.push((src_id.clone(), vec![src_id.clone()]));
    visited.insert(src_id.clone(), 0);

    let max_depth = 10usize;
    let mut paths = Vec::new();

    while let Some((current, path)) = queue.pop() {
        if path.len() > max_depth {
            continue;
        }

        // Get outbound edges from current node
        let edges = db.get_outbound_edges(&current)?;

        for (_edge_id, next_id, _kind, _) in &edges {
            if next_id == &tgt_id {
                // Found target — record path
                let mut full_path = path.clone();
                full_path.push(next_id.clone());
                paths.push(full_path);
                // Don't stop — there may be multiple paths
                continue;
            }

            let new_depth = path.len() + 1;
            if !visited.contains_key(next_id) || *visited.get(next_id).unwrap() > new_depth {
                visited.insert(next_id.clone(), new_depth);
                let mut new_path = path.clone();
                new_path.push(next_id.clone());
                queue.push((next_id.clone(), new_path));
            }
        }
    }

    // Convert paths to results with node names
    let mut results = Vec::new();
    for path_ids in &paths {
        let mut path_names = Vec::new();
        for nid in path_ids {
            if let Ok(Some(node)) = db.get_node(nid) {
                path_names.push(node.2); // name field
            } else {
                path_names.push(nid.clone());
            }
        }
        let mut row = HashMap::new();
        row.insert(
            "path".to_string(),
            Value::String(path_names.join(" -> ")),
        );
        row.insert(
            "length".to_string(),
            Value::Number((path_ids.len() - 1).into()),
        );
        results.push(row);
    }

    Ok(results)
}

/// Build and execute SQL from the logical plan operators.
fn execute_sql(
    db: &Database,
    operators: &[Operator],
) -> Result<Vec<HashMap<String, Value>>> {
    if operators.is_empty() {
        return Ok(Vec::new());
    }

    let mut sql_parts: Vec<String> = Vec::new();
    let mut bind_values: Vec<String> = Vec::new();
    let mut param_idx = 1usize;

    // Default: SELECT * FROM nodes
    let mut select_clause = String::from("SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language, n.file_path");
    let mut where_clauses: Vec<String> = Vec::new();
    let mut order_clauses: Vec<String> = Vec::new();
    let mut limit_clause: Option<String> = None;

    let mut has_project = false;

    for op in operators {
        match op {
            Operator::Scan { kind } => {
                if let Some(k) = kind {
                    where_clauses.push(format!("n.kind = ?{}", param_idx));
                    bind_values.push(k.clone());
                    param_idx += 1;
                }
            }
            Operator::Filter { sql, params } => {
                // Replace numbered params (?1, ?2, ...) with the global index
                let renumbered = renumber_params(sql, &mut param_idx);
                where_clauses.push(renumbered);
                bind_values.extend(params.clone());
            }
            Operator::Project { fields } => {
                has_project = true;
                let cols: Vec<String> = fields
                    .iter()
                    .map(|f| format!("n.{}", f))
                    .collect();
                select_clause = format!("SELECT {}", cols.join(", "));
            }
            Operator::Sort { field, asc } => {
                let dir = if *asc { "ASC" } else { "DESC" };
                order_clauses.push(format!("n.{} {}", field, dir));
            }
            Operator::Limit { count } => {
                limit_clause = Some(format!("LIMIT {}", count));
            }
            Operator::EdgeExpand { .. } => {
                // Edge expansion is handled via Database methods in special statements
                // For SQL path, this is not directly supported — return empty
            }
        }
    }

    // Assemble SQL
    let mut sql = if has_project {
        format!("{} FROM nodes n", select_clause)
    } else {
        String::from(
            "SELECT n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language FROM nodes n",
        )
    };

    if !where_clauses.is_empty() {
        sql.push_str(" WHERE ");
        sql.push_str(&where_clauses.join(" AND "));
    }

    if !order_clauses.is_empty() {
        sql.push_str(" ORDER BY ");
        sql.push_str(&order_clauses.join(", "));
    }

    if let Some(lim) = &limit_clause {
        sql.push(' ');
        sql.push_str(lim);
    }

    // Execute
    let conn = db.connection();
    let mut stmt = conn.prepare(&sql)?;

    let param_refs: Vec<&dyn rusqlite::types::ToSql> = bind_values
        .iter()
        .map(|s| s as &dyn rusqlite::types::ToSql)
        .collect();

    let column_names: Vec<String> = stmt
        .column_names()
        .iter()
        .map(|c| c.to_string())
        .collect();

    let rows = stmt.query_map(
        param_refs.as_slice(),
        |row| {
            let mut map = HashMap::new();
            for (i, col_name) in column_names.iter().enumerate() {
                let val: rusqlite::Result<String> = row.get(i);
                match val {
                    Ok(s) => {
                        map.insert(col_name.clone(), Value::String(s));
                    }
                    Err(_) => {
                        // Try integer
                        let int_val: rusqlite::Result<i64> = row.get(i);
                        match int_val {
                            Ok(n) => {
                                map.insert(col_name.clone(), Value::Number(n.into()));
                            }
                            Err(_) => {
                                map.insert(col_name.clone(), Value::Null);
                            }
                        }
                    }
                }
            }
            Ok(map)
        },
    )?;

    let mut results = Vec::new();
    for row_result in rows {
        results.push(row_result?);
    }

    Ok(results)
}

/// Renumber `?1`, `?2`, ... in a SQL fragment to use the global parameter index.
fn renumber_params(sql: &str, param_idx: &mut usize) -> String {
    let mut result = String::new();
    let mut i = 0;
    let chars: Vec<char> = sql.chars().collect();
    while i < chars.len() {
        if chars[i] == '?' && i + 1 < chars.len() && chars[i + 1].is_ascii_digit() {
            // Read the number
            let start = i + 1;
            let mut end = start;
            while end < chars.len() && chars[end].is_ascii_digit() {
                end += 1;
            }
            // Replace with current global index
            result.push('?');
            result.push_str(&param_idx.to_string());
            *param_idx += 1;
            i = end;
        } else {
            result.push(chars[i]);
            i += 1;
        }
    }
    result
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
        std::env::temp_dir().join(format!("tws_test_gql_{}.db", name))
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

    /// Insert a simple node and return its id.
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

    /// Insert an edge between two nodes.
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

    // -----------------------------------------------------------------------
    // FIND query tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_execute_find_scan_all() {
        let path = temp_db_path("find_scan");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            insert_node(conn, "func_a", "src::func_a", "function", "src/lib.rs", "rust");
            insert_node(conn, "func_b", "src::func_b", "function", "src/lib.rs", "rust");
            insert_node(conn, "MyStruct", "src::MyStruct", "struct", "src/lib.rs", "rust");

            let plan = Plan {
                operators: vec![Operator::Scan { kind: None }],
                statement: None,
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 3);
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_find_scan_with_kind_filter() {
        let path = temp_db_path("find_scan_kind");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            insert_node(conn, "func_a", "src::func_a", "function", "src/lib.rs", "rust");
            insert_node(conn, "func_b", "src::func_b", "function", "src/lib.rs", "rust");
            insert_node(conn, "MyStruct", "src::MyStruct", "struct", "src/lib.rs", "rust");

            let plan = Plan {
                operators: vec![Operator::Scan {
                    kind: Some("function".to_string()),
                }],
                statement: None,
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 2);
            for r in &results {
                assert_eq!(r.get("kind").unwrap(), &Value::String("function".to_string()));
            }
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_find_with_filter() {
        let path = temp_db_path("find_filter");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            insert_node(conn, "auth_handler", "src::auth_handler", "function", "src/auth.rs", "rust");
            insert_node(conn, "parse_json", "src::parse_json", "function", "src/parse.rs", "rust");
            insert_node(conn, "validate_auth", "src::validate_auth", "function", "src/auth.rs", "rust");

            let plan = Plan {
                operators: vec![
                    Operator::Scan { kind: None },
                    Operator::Filter {
                        sql: "n.name LIKE ?1".to_string(),
                        params: vec!["%auth%".to_string()],
                    },
                ],
                statement: None,
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 2);
            for r in &results {
                let name = r.get("name").unwrap().as_str().unwrap();
                assert!(name.contains("auth"));
            }
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_find_with_limit() {
        let path = temp_db_path("find_limit");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            for i in 0..10 {
                insert_node(
                    conn,
                    &format!("func_{}", i),
                    &format!("src::func_{}", i),
                    "function",
                    "src/lib.rs",
                    "rust",
                );
            }

            let plan = Plan {
                operators: vec![
                    Operator::Scan { kind: None },
                    Operator::Limit { count: 3 },
                ],
                statement: None,
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 3);
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_find_full_pipeline() {
        let path = temp_db_path("find_full");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            // Mix of nodes with different names
            insert_node(conn, "compute", "src::compute", "function", "src/math.rs", "rust");
            insert_node(conn, "calculate", "src::calculate", "function", "src/math.rs", "rust");
            insert_node(conn, "render", "src::render", "function", "src/ui.rs", "rust");
            insert_node(conn, "MathHelper", "src::MathHelper", "struct", "src/math.rs", "rust");
            insert_node(conn, "ui_renderer", "src::ui_renderer", "function", "src/ui.rs", "rust");

            // FIND function WHERE name LIKE '%er%' LIMIT 2
            let plan = Plan {
                operators: vec![
                    Operator::Scan {
                        kind: Some("function".to_string()),
                    },
                    Operator::Filter {
                        sql: "n.name LIKE ?1".to_string(),
                        params: vec!["%er%".to_string()],
                    },
                    Operator::Limit { count: 2 },
                ],
                statement: None,
            };

            let results = execute(&db, &plan).unwrap();
            // "render" and "ui_renderer" (if in expected order)
            assert!(results.len() <= 2);
            assert!(!results.is_empty());
            for r in &results {
                assert_eq!(r.get("kind").unwrap(), &Value::String("function".to_string()));
            }
        }

        cleanup(&path);
    }

    // -----------------------------------------------------------------------
    // IMPACT tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_execute_impact_single_dependent() {
        let path = temp_db_path("impact_single");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let caller_id = insert_node(conn, "caller", "src::caller", "function", "src/main.rs", "rust");
            let callee_id = insert_node(conn, "callee", "src::callee", "function", "src/main.rs", "rust");
            insert_edge(conn, &caller_id, &callee_id, "CALLS");

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Impact {
                    target: "callee".to_string(),
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 1);
            assert_eq!(results[0].get("name").unwrap(), &Value::String("caller".to_string()));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_impact_multiple_dependents() {
        let path = temp_db_path("impact_multi");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let a_id = insert_node(conn, "func_a", "src::func_a", "function", "src/lib.rs", "rust");
            let b_id = insert_node(conn, "func_b", "src::func_b", "function", "src/lib.rs", "rust");
            let c_id = insert_node(conn, "func_c", "src::func_c", "function", "src/lib.rs", "rust");
            let target_id = insert_node(conn, "target", "src::target", "function", "src/lib.rs", "rust");

            insert_edge(conn, &a_id, &target_id, "CALLS");
            insert_edge(conn, &b_id, &target_id, "CALLS");
            insert_edge(conn, &c_id, &target_id, "CALLS");

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Impact {
                    target: "target".to_string(),
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 3);
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_impact_node_not_found() {
        let path = temp_db_path("impact_nf");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Impact {
                    target: "nonexistent".to_string(),
                }),
            };

            let result = execute(&db, &plan);
            assert!(result.is_err());
        }

        cleanup(&path);
    }

    // -----------------------------------------------------------------------
    // CALLS tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_execute_calls_outbound() {
        let path = temp_db_path("calls_out");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let src_id = insert_node(conn, "main", "src::main", "function", "src/main.rs", "rust");
            let tgt1_id = insert_node(conn, "init_db", "src::init_db", "function", "src/db.rs", "rust");
            let tgt2_id = insert_node(conn, "parse_args", "src::parse_args", "function", "src/args.rs", "rust");

            insert_edge(conn, &src_id, &tgt1_id, "CALLS");
            insert_edge(conn, &src_id, &tgt2_id, "CALLS");

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Calls {
                    target: "main".to_string(),
                    inbound: false,
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 2);
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_calls_inbound() {
        let path = temp_db_path("calls_in");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let caller1 = insert_node(conn, "caller1", "src::caller1", "function", "src/a.rs", "rust");
            let caller2 = insert_node(conn, "caller2", "src::caller2", "function", "src/b.rs", "rust");
            let callee = insert_node(conn, "helper", "src::helper", "function", "src/helper.rs", "rust");

            insert_edge(conn, &caller1, &callee, "CALLS");
            insert_edge(conn, &caller2, &callee, "CALLS");

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Calls {
                    target: "helper".to_string(),
                    inbound: true,
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert_eq!(results.len(), 2);
            let names: Vec<&str> = results
                .iter()
                .map(|r| r.get("name").unwrap().as_str().unwrap())
                .collect();
            assert!(names.contains(&"caller1"));
            assert!(names.contains(&"caller2"));
        }

        cleanup(&path);
    }

    // -----------------------------------------------------------------------
    // TRACE tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_execute_trace_direct_path() {
        let path = temp_db_path("trace_direct");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let a_id = insert_node(conn, "main", "src::main", "function", "src/main.rs", "rust");
            let b_id = insert_node(conn, "parse", "src::parse", "function", "src/parse.rs", "rust");

            insert_edge(conn, &a_id, &b_id, "CALLS");

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Trace {
                    source: "main".to_string(),
                    target: "parse".to_string(),
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert!(!results.is_empty());
            let path = results[0].get("path").unwrap().as_str().unwrap();
            assert!(path.contains("main"));
            assert!(path.contains("parse"));
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_trace_no_path() {
        let path = temp_db_path("trace_nopath");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();
            let conn = db.connection();

            let a_id = insert_node(conn, "main", "src::main", "function", "src/main.rs", "rust");
            let b_id = insert_node(conn, "parse", "src::parse", "function", "src/parse.rs", "rust");

            // No edge between them

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Trace {
                    source: "main".to_string(),
                    target: "parse".to_string(),
                }),
            };

            let results = execute(&db, &plan).unwrap();
            assert!(results.is_empty());
        }

        cleanup(&path);
    }

    #[test]
    fn test_execute_trace_source_not_found() {
        let path = temp_db_path("trace_srcnf");
        cleanup(&path);

        {
            let db = Database::initialize(&path).unwrap();

            let plan = Plan {
                operators: vec![],
                statement: Some(Statement::Trace {
                    source: "nonexistent".to_string(),
                    target: "parse".to_string(),
                }),
            };

            let result = execute(&db, &plan);
            assert!(result.is_err());
        }

        cleanup(&path);
    }

    #[test]
    fn test_renumber_params() {
        let mut idx = 3;
        let result = renumber_params("n.name LIKE ?1 AND n.kind = ?2", &mut idx);
        assert_eq!(result, "n.name LIKE ?3 AND n.kind = ?4");
        assert_eq!(idx, 5);
    }
}
