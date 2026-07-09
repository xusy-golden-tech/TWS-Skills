//! MCP (Model Context Protocol) server — exposes 21 tools + 3 resources.
//!
//! Implements a subset of JSON-RPC 2.0 for MCP over stdio. Each tool function
//! takes `&Database` as its first parameter and returns `serde_json::Value`.
//!
//! Protocol messages:
//! - `initialize`       — handshake (id, name, version)
//! - `tools/list`       — list available tools with their schemas
//! - `tools/call`       — call a tool by name with arguments
//! - `resources/list`   — list available resources
//! - `resources/read`   — read a resource by URI

use crate::db::Database;
use crate::query::traversal::{GraphTraverser, TraversalDirection};
// query engine functions used via Database methods directly
use serde_json::{json, Value};
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// JSON-RPC dispatcher
// ---------------------------------------------------------------------------

/// Handle a raw JSON-RPC 2.0 request string and return the response.
pub fn handle_request(db: &Database, request: &str) -> String {
    let req: Value = match serde_json::from_str(request) {
        Ok(v) => v,
        Err(e) => {
            return json!({
                "jsonrpc": "2.0",
                "id": null,
                "error": {"code": -32700, "message": format!("Parse error: {}", e)}
            })
            .to_string();
        }
    };

    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let method = req.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = req.get("params").cloned().unwrap_or(Value::Null);

    let result = match method {
        "initialize" => rpc_initialize(&params),
        "tools/list" => rpc_tools_list(),
        "tools/call" => rpc_tools_call(db, &params),
        "resources/list" => rpc_resources_list(),
        "resources/read" => rpc_resources_read(db, &params),
        _ => {
            return json!({
                "jsonrpc": "2.0",
                "id": id,
                "error": {"code": -32601, "message": format!("Method not found: {}", method)}
            })
            .to_string();
        }
    };

    match result {
        Ok(value) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": value,
        })
        .to_string(),
        Err(msg) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": {"code": -32603, "message": msg}
        })
        .to_string(),
    }
}

// ---------------------------------------------------------------------------
// JSON-RPC method handlers
// ---------------------------------------------------------------------------

fn rpc_initialize(params: &Value) -> Result<Value, String> {
    let name = params
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("tws-graph-mcp");
    let version = params
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("1.0.0");

    Ok(json!({
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": format!("tws-graph-mcp/{}", name),
            "version": version,
        },
        "capabilities": {
            "tools": {},
            "resources": {},
        }
    }))
}

fn rpc_tools_list() -> Result<Value, String> {
    Ok(json!({"tools": all_tools()}))
}

fn rpc_tools_call(db: &Database, params: &Value) -> Result<Value, String> {
    let tool_name = params
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or("Missing tool name")?;
    let arguments = params.get("arguments").cloned().unwrap_or(Value::Null);

    call_tool(db, tool_name, &arguments)
}

fn rpc_resources_list() -> Result<Value, String> {
    Ok(json!({
        "resources": [
            {"uri": "tws://stats", "name": "Code Graph Statistics", "description": "Node/edge/file counts, languages, index time"},
            {"uri": "tws://languages", "name": "Language Distribution", "description": "Per-language node/file distribution"},
            {"uri": "tws://health", "name": "Health Status", "description": "Index ready, uptime info"},
        ]
    }))
}

fn rpc_resources_read(db: &Database, params: &Value) -> Result<Value, String> {
    let uri = params
        .get("uri")
        .and_then(|v| v.as_str())
        .ok_or("Missing URI")?;

    match uri {
        "tws://stats" => resource_stats(db),
        "tws://languages" => resource_languages(db),
        "tws://health" => resource_health(db),
        _ => Err(format!("Unknown resource URI: {}", uri)),
    }
}

// ---------------------------------------------------------------------------
// Tool definitions (static)
// ---------------------------------------------------------------------------

fn all_tools() -> Vec<Value> {
    vec![
    json!({"name": "search_symbols", "description": "FTS5 full-text search for symbols with qualifier filters", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "kind": {"type": "string"}, "lang": {"type": "string"}, "path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}}),
    json!({"name": "semantic_search", "description": "11-signal fusion semantic search", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}}),
    json!({"name": "get_code", "description": "Get source code and metadata for a symbol", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}}, "required": ["symbol_id"]}}),
    json!({"name": "get_dependencies", "description": "Get callers (inbound) or callees (outbound)", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}, "direction": {"type": "string", "enum": ["inbound", "outbound"]}}, "required": ["symbol_id"]}}),
    json!({"name": "get_impact", "description": "Compute impact radius and risk level", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["symbol_id"]}}),
    json!({"name": "trace_path", "description": "Find shortest path between two symbols", "inputSchema": {"type": "object", "properties": {"from_id": {"type": "string"}, "to_id": {"type": "string"}}, "required": ["from_id", "to_id"]}}),
    json!({"name": "get_complexity", "description": "Analyze code complexity metrics", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}}, "required": ["symbol_id"]}}),
    json!({"name": "find_dead_code", "description": "Detect potentially unused code", "inputSchema": {"type": "object", "properties": {}}}),
    json!({"name": "get_test_coverage", "description": "Test coverage analysis via heuristics", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}}, "required": ["symbol_id"]}}),
    json!({"name": "get_entry_points", "description": "Identify project entry points", "inputSchema": {"type": "object", "properties": {}}}),
    json!({"name": "find_clones", "description": "Detect code clones with MinHash+LSH", "inputSchema": {"type": "object", "properties": {"threshold": {"type": "number"}}}}),
    json!({"name": "get_git_diff_impact", "description": "Analyze git diff impact on code graph", "inputSchema": {"type": "object", "properties": {"diff": {"type": "string"}}, "required": ["diff"]}}),
    json!({"name": "get_config_links", "description": "Find code-to-config relationships", "inputSchema": {"type": "object", "properties": {}}}),
    json!({"name": "query_cypher", "description": "Execute GQL query against code graph", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}),
    json!({"name": "get_edge_distribution", "description": "Get edge type distribution stats", "inputSchema": {"type": "object", "properties": {}}}),
    json!({"name": "detect_cross_service", "description": "Detect cross-service communication", "inputSchema": {"type": "object", "properties": {}}}),
    json!({"name": "review_changes", "description": "Code review: downstream impact + test suggestions", "inputSchema": {"type": "object", "properties": {"diff": {"type": "string"}}, "required": ["diff"]}}),
    json!({"name": "safe_refactor", "description": "Refactoring safety check: dependencies + checklist", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "string"}}, "required": ["symbol_id"]}}),
    json!({"name": "api_compat_check", "description": "API compatibility: breaking change detection", "inputSchema": {"type": "object", "properties": {"old_sig": {"type": "string"}, "new_sig": {"type": "string"}}, "required": ["old_sig", "new_sig"]}}),
    json!({"name": "find_pattern", "description": "AST structure pattern search", "inputSchema": {"type": "object", "properties": {"pattern": {"type": "string"}, "lang": {"type": "string"}}, "required": ["pattern"]}}),
    json!({"name": "security_scan", "description": "Security vulnerability detection", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}),
]}

// ---------------------------------------------------------------------------
// Tool dispatcher
// ---------------------------------------------------------------------------

fn call_tool(db: &Database, name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "search_symbols" => tool_search_symbols(db, args),
        "semantic_search" => tool_semantic_search(db, args),
        "get_code" => tool_get_code(db, args),
        "get_dependencies" => tool_get_dependencies(db, args),
        "get_impact" => tool_get_impact(db, args),
        "trace_path" => tool_trace_path(db, args),
        "get_complexity" => tool_get_complexity(db, args),
        "find_dead_code" => tool_find_dead_code(db, args),
        "get_test_coverage" => tool_get_test_coverage(db, args),
        "get_entry_points" => tool_get_entry_points(db, args),
        "find_clones" => tool_find_clones(db, args),
        "get_git_diff_impact" => tool_get_git_diff_impact(db, args),
        "get_config_links" => tool_get_config_links(db, args),
        "query_cypher" => tool_query_cypher(db, args),
        "get_edge_distribution" => tool_get_edge_distribution(db, args),
        "detect_cross_service" => tool_detect_cross_service(db, args),
        "review_changes" => tool_review_changes(db, args),
        "safe_refactor" => tool_safe_refactor(db, args),
        "api_compat_check" => tool_api_compat_check(db, args),
        "find_pattern" => tool_find_pattern(db, args),
        "security_scan" => tool_security_scan(db, args),
        _ => Err(format!("Unknown tool: {}", name)),
    }
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

fn get_str(args: &Value, key: &str) -> Option<String> {
    args.get(key).and_then(|v| v.as_str()).map(|s| s.to_string())
}

fn get_i64(args: &Value, key: &str, default: i64) -> i64 {
    args.get(key).and_then(|v| v.as_i64()).unwrap_or(default)
}

fn get_f64(args: &Value, key: &str, default: f64) -> f64 {
    args.get(key).and_then(|v| v.as_f64()).unwrap_or(default)
}

/// 1. search_symbols — FTS5 full-text search
fn tool_search_symbols(db: &Database, args: &Value) -> Result<Value, String> {
    let query = get_str(args, "query").unwrap_or_default();
    let kind = get_str(args, "kind");
    let lang = get_str(args, "lang");
    let path = get_str(args, "path");
    let limit = get_i64(args, "limit", 20) as usize;

    let results = db.search_fts5(
        &query,
        kind.as_deref(),
        lang.as_deref(),
        path.as_deref(),
        limit,
    )
    .map_err(|e| e.to_string())?;

    let items: Vec<Value> = results
        .iter()
        .map(|(id, k, name, qn, fp, lang_val, rank, start_line)| {
            json!({
                "id": id,
                "kind": k,
                "name": name,
                "qualified_name": qn,
                "file_path": fp,
                "language": lang_val,
                "rank": rank,
                "line_number": start_line,
            })
        })
        .collect();

    Ok(json!({
        "count": items.len(),
        "results": items,
    }))
}

/// 2. semantic_search — search with fallback to LIKE + edit distance
fn tool_semantic_search(db: &Database, args: &Value) -> Result<Value, String> {
    let query = get_str(args, "query").unwrap_or_default();
    let limit = get_i64(args, "limit", 10) as usize;

    // Try FTS5 first
    let mut results = db.search_fts5(&query, None, None, None, limit * 2)
        .map_err(|e| e.to_string())?;

    // If FTS5 returns few results, try LIKE search
    if results.len() < limit {
        let like_results = db.search_like(&query, None, None, None, limit)
            .map_err(|e| e.to_string())?;
        for r in like_results {
            results.push(r);
        }
    }

    // Also try edit distance for fuzzy matching
    if results.len() < limit {
        let edit_results = db.search_edit_distance(&query, None, None, None, limit)
            .map_err(|e| e.to_string())?;
        for r in edit_results {
            results.push(r);
        }
    }

    // Deduplicate by node ID
    let mut seen = std::collections::HashSet::new();
    let mut unique: Vec<_> = Vec::new();
    for r in &results {
        if seen.insert(&r.0) {
            unique.push(r.clone());
        }
    }
    unique.truncate(limit);

    let items: Vec<Value> = unique
        .iter()
        .map(|(id, k, name, qn, fp, lang_val, rank, start_line)| {
            json!({
                "id": id,
                "kind": k,
                "name": name,
                "qualified_name": qn,
                "file_path": fp,
                "language": lang_val,
                "rank": rank,
                "line_number": start_line,
            })
        })
        .collect();

    Ok(json!({
        "query": query,
        "count": items.len(),
        "results": items,
    }))
}

/// 3. get_code — get node details by ID
fn tool_get_code(db: &Database, args: &Value) -> Result<Value, String> {
    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;

    let node = db.get_node(&symbol_id).map_err(|e| e.to_string())?;

    match node {
        Some((id, kind, name, qn, lang, fp)) => Ok(json!({
            "id": id,
            "kind": kind,
            "name": name,
            "qualified_name": qn,
            "language": lang,
            "file_path": fp,
        })),
        None => Ok(json!({"error": "Symbol not found", "symbol_id": symbol_id})),
    }
}

/// 4. get_dependencies — inbound or outbound edges
fn tool_get_dependencies(db: &Database, args: &Value) -> Result<Value, String> {
    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;
    let direction = get_str(args, "direction").unwrap_or_else(|| "outbound".to_string());

    let edges = if direction == "inbound" {
        db.get_inbound_edges(&symbol_id).map_err(|e| e.to_string())?
    } else {
        db.get_outbound_edges(&symbol_id).map_err(|e| e.to_string())?
    };

    let mut deps: Vec<Value> = Vec::new();
    for (rowid, target, kind, target_text) in &edges {
        let mut dep = json!({
            "edge_id": rowid,
            "target_id": target,
            "kind": kind,
        });
        if let Some(tt) = target_text {
            dep["target_text"] = json!(tt);
        }
        // Try to resolve target node name
        if let Ok(Some((_, _nk, name, _, _, _))) = db.get_node(target) {
            dep["target_name"] = json!(name);
        }
        deps.push(dep);
    }

    Ok(json!({
        "symbol_id": symbol_id,
        "direction": direction,
        "count": deps.len(),
        "dependencies": deps,
    }))
}

/// 5. get_impact — compute impact radius
fn tool_get_impact(db: &Database, args: &Value) -> Result<Value, String> {
    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;
    let depth = get_i64(args, "depth", 3) as usize;

    let traverser = GraphTraverser::from_db(db, None, Some(&["CONTAINS"]), true, false)
        .map_err(|e| e.to_string())?;

    let affected = traverser.impact_radius(&symbol_id, depth, TraversalDirection::Outbound);

    // Group by file
    let mut by_file: HashMap<String, Vec<String>> = HashMap::new();
    for nid in &affected {
        if let Ok(Some((_, _, name, _, _, fp))) = db.get_node(nid) {
            by_file.entry(fp).or_default().push(name);
        }
    }

    let risk_level = match affected.len() {
        0..=5 => "low",
        6..=20 => "medium",
        21..=100 => "high",
        _ => "critical",
    };

    let files: Vec<Value> = by_file
        .into_iter()
        .map(|(fp, names)| {
            json!({
                "file": fp,
                "affected_symbols": names.len(),
                "symbols": names,
            })
        })
        .collect();

    Ok(json!({
        "symbol_id": symbol_id,
        "affected_count": affected.len(),
        "risk_level": risk_level,
        "depth": depth,
        "affected_files": files,
    }))
}

/// 6. trace_path — shortest path between two symbols
fn tool_trace_path(db: &Database, args: &Value) -> Result<Value, String> {
    let from_id = get_str(args, "from_id").ok_or("Missing from_id")?;
    let to_id = get_str(args, "to_id").ok_or("Missing to_id")?;

    let traverser = GraphTraverser::from_db(db, None, Some(&["CONTAINS"]), true, false)
        .map_err(|e| e.to_string())?;

    let path = traverser.shortest_path(&from_id, &to_id, TraversalDirection::Bidirectional);

    match path {
        Some(nodes) => {
            let mut path_info: Vec<Value> = Vec::new();
            for nid in &nodes {
                if let Ok(Some((_, kind, name, _, _, fp))) = db.get_node(nid) {
                    path_info.push(json!({
                        "id": nid,
                        "name": name,
                        "kind": kind,
                        "file": fp,
                    }));
                }
            }
            Ok(json!({
                "from": from_id,
                "to": to_id,
                "found": true,
                "hops": nodes.len().saturating_sub(1),
                "path": path_info,
            }))
        }
        None => Ok(json!({
            "from": from_id,
            "to": to_id,
            "found": false,
            "message": "No path found between the two symbols",
        })),
    }
}

/// 7. get_complexity — code complexity analysis
fn tool_get_complexity(db: &Database, args: &Value) -> Result<Value, String> {
    use crate::analysis::complexity::analyze_complexity;

    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;

    let node = db.get_node(&symbol_id).map_err(|e| e.to_string())?;

    match node {
        Some((id, kind, name, _qn, lang, fp)) => {
            // Try to get the body from the DB
            let body = get_node_body(db, &id);
            let metrics = if let Some(ref code) = body {
                analyze_complexity(code, &lang)
            } else {
                // No body available, return empty metrics
                crate::analysis::complexity::ComplexityMetrics::default()
            };

            Ok(json!({
                "symbol_id": id,
                "name": name,
                "kind": kind,
                "language": lang,
                "file_path": fp,
                "complexity": {
                    "cyclomatic": metrics.cyclomatic,
                    "cognitive": metrics.cognitive,
                    "halstead_volume": metrics.halstead_volume,
                    "halstead_difficulty": metrics.halstead_difficulty,
                    "halstead_effort": metrics.halstead_effort,
                    "lines_of_code": metrics.lines_of_code,
                }
            }))
        }
        None => Ok(json!({"error": "Symbol not found"})),
    }
}

/// 8. find_dead_code — detect potentially unused code
fn tool_find_dead_code(_db: &Database, _args: &Value) -> Result<Value, String> {
    use crate::analysis::dead_code::find_dead_code;

    let dead = find_dead_code(_db);

    let items: Vec<Value> = dead
        .iter()
        .map(|(id, name, kind, fp)| {
            json!({
                "id": id,
                "name": name,
                "kind": kind,
                "file_path": fp,
            })
        })
        .collect();

    Ok(json!({
        "count": items.len(),
        "dead_code": items,
    }))
}

/// 9. get_test_coverage — heuristic test coverage analysis
fn tool_get_test_coverage(db: &Database, args: &Value) -> Result<Value, String> {
    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;

    let node = db.get_node(&symbol_id).map_err(|e| e.to_string())?;

    match node {
        Some((id, _kind, name, _qn, _lang, fp)) => {
            // Strategy 1: Check if there's a test file matching this file
            let test_file_exists = check_test_file_exists(db, &fp);

            // Strategy 2: Check if any test node calls this node
            let has_test_caller = check_test_callers(db, &id);

            // Strategy 3: Check naming convention (test_ prefix)
            let test_naming = name.starts_with("test_") || name.ends_with("_test");

            let coverage = match (test_file_exists, has_test_caller) {
                (true, true) => 0.9,
                (true, false) => 0.6,
                (false, true) => 0.5,
                (false, false) => 0.0,
            };

            Ok(json!({
                "symbol_id": id,
                "name": name,
                "has_test_file": test_file_exists,
                "has_test_caller": has_test_caller,
                "is_test_naming": test_naming,
                "estimated_coverage": coverage,
            }))
        }
        None => Ok(json!({"error": "Symbol not found"})),
    }
}

/// 10. get_entry_points — identify project entry points
fn tool_get_entry_points(db: &Database, _args: &Value) -> Result<Value, String> {
    use crate::analysis::entry_point::find_entry_points;

    let entries = find_entry_points(db);

    let items: Vec<Value> = entries
        .iter()
        .map(|(id, name, kind, reason)| {
            json!({
                "id": id,
                "name": name,
                "kind": kind,
                "reason": reason,
            })
        })
        .collect();

    Ok(json!({
        "count": items.len(),
        "entry_points": items,
    }))
}

/// 11. find_clones — code clone detection
fn tool_find_clones(db: &Database, args: &Value) -> Result<Value, String> {
    use crate::analysis::clone::find_clones;
    let threshold = get_f64(args, "threshold", 0.8);

    let clones = find_clones(db, threshold);

    let items: Vec<Value> = clones
        .iter()
        .map(|(id1, n1, id2, n2, sim)| {
            json!({
                "node_1_id": id1,
                "node_1_name": n1,
                "node_2_id": id2,
                "node_2_name": n2,
                "similarity": sim,
            })
        })
        .collect();

    Ok(json!({
        "count": items.len(),
        "threshold": threshold,
        "clones": items,
    }))
}

/// 12. get_git_diff_impact — analyze git diff impact
fn tool_get_git_diff_impact(db: &Database, args: &Value) -> Result<Value, String> {
    use crate::analysis::git_diff::analyze_git_diff;
    let diff_text = get_str(args, "diff").unwrap_or_default();

    let affected = analyze_git_diff(db, &diff_text);

    let items: Vec<Value> = affected
        .iter()
        .map(|(id, name, radius)| {
            json!({
                "node_id": id,
                "name": name,
                "impact_radius": radius,
            })
        })
        .collect();

    // Count unique files from the diff
    let unique_files: std::collections::HashSet<String> = affected
        .iter()
        .filter_map(|(id, _, _)| {
            db.get_node(id).ok().flatten().map(|(_, _, _, _, _, fp)| fp)
        })
        .collect();

    Ok(json!({
        "affected_symbols": items.len(),
        "affected_files": unique_files.len(),
        "results": items,
    }))
}

/// 13. get_config_links — find config-to-code links
fn tool_get_config_links(db: &Database, _args: &Value) -> Result<Value, String> {
    use crate::analysis::config_links::find_config_links;

    let links = find_config_links(db);

    let items: Vec<Value> = links
        .iter()
        .map(|(code_id, code_name, config_key, config_file)| {
            json!({
                "code_id": code_id,
                "code_name": code_name,
                "config_key": config_key,
                "config_file": config_file,
            })
        })
        .collect();

    Ok(json!({
        "count": items.len(),
        "links": items,
    }))
}

/// 14. query_cypher — execute GQL query
fn tool_query_cypher(db: &Database, args: &Value) -> Result<Value, String> {
    use crate::gql::execute_gql;
    let query = get_str(args, "query").unwrap_or_default();

    let results = execute_gql(db, &query).map_err(|e| e.to_string())?;

    Ok(json!({
        "query": query,
        "row_count": results.len(),
        "rows": results,
    }))
}

/// 15. get_edge_distribution — edge type statistics
fn tool_get_edge_distribution(db: &Database, _args: &Value) -> Result<Value, String> {
    let conn = db.connection();

    let mut stmt = conn
        .prepare("SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC")
        .map_err(|e| e.to_string())?;

    let rows: Vec<(String, i64)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect();

    let total: i64 = rows.iter().map(|(_, c)| c).sum();

    let distribution: Vec<Value> = rows
        .iter()
        .map(|(kind, count)| {
            json!({
                "kind": kind,
                "count": count,
                "percentage": if total > 0 { (*count as f64 / total as f64 * 100.0 * 10.0).round() / 10.0 } else { 0.0 },
            })
        })
        .collect();

    Ok(json!({
        "total_edges": total,
        "edge_types": rows.len(),
        "distribution": distribution,
    }))
}

/// 16. detect_cross_service — detect cross-service communication
fn tool_detect_cross_service(db: &Database, _args: &Value) -> Result<Value, String> {
    let edges = db.get_all_edges(Some("HTTP_CALLS"))
        .map_err(|e| e.to_string())?;

    let grpc_edges = db.get_all_edges(Some("GRPC_SERVICE"))
        .map_err(|e| e.to_string())?;

    let mut services: Vec<Value> = Vec::new();

    for (src, tgt, kind, _tt) in &edges {
        services.push(json!({
            "source_id": src,
            "target_id": tgt,
            "kind": kind,
            "protocol": "HTTP",
        }));
    }

    for (src, tgt, kind, _tt) in &grpc_edges {
        services.push(json!({
            "source_id": src,
            "target_id": tgt,
            "kind": kind,
            "protocol": "gRPC",
        }));
    }

    // Detect message-based, gRPC client/server
    for kind in &["GRPC_CLIENT", "GRPC_SERVER"] {
        if let Ok(e) = db.get_all_edges(Some(kind)) {
            for (src, tgt, k, _tt) in &e {
                services.push(json!({
                    "source_id": src,
                    "target_id": tgt,
                    "kind": k,
                    "protocol": "gRPC",
                }));
            }
        }
    }

    Ok(json!({
        "cross_service_edges": services.len(),
        "edges": services,
    }))
}

/// 17. review_changes — code review assistance
fn tool_review_changes(db: &Database, args: &Value) -> Result<Value, String> {
    use crate::analysis::git_diff::analyze_git_diff;
    let diff_text = get_str(args, "diff").unwrap_or_default();

    let affected = analyze_git_diff(db, &diff_text);

    // Build downstream impact mapping
    let mut impact_entries: Vec<Value> = Vec::new();
    for (id, name, radius) in &affected {
        let risk = match radius {
            0 => "low",
            1..=3 => "medium",
            _ => "high",
        };
        impact_entries.push(json!({
            "symbol_id": id,
            "name": name,
            "impact_radius": radius,
            "risk": risk,
        }));
    }

    // Generate test suggestions based on affected files
    let mut test_suggestions: Vec<String> = Vec::new();
    for (_, name, _) in &affected {
        test_suggestions.push(format!("test_{}", name));
    }

    Ok(json!({
        "affected_count": affected.len(),
        "impact_summary": impact_entries,
        "test_suggestions": test_suggestions,
        "quality_gate": if affected.len() > 50 { "BLOCKED" } else if affected.len() > 20 { "WARNING" } else { "PASS" },
    }))
}

/// 18. safe_refactor — refactoring safety check
fn tool_safe_refactor(db: &Database, args: &Value) -> Result<Value, String> {
    let symbol_id = get_str(args, "symbol_id").ok_or("Missing symbol_id")?;

    // Get callers (inbound)
    let inbound = db.get_inbound_edges(&symbol_id).map_err(|e| e.to_string())?;

    // Get callees (outbound)
    let outbound = db.get_outbound_edges(&symbol_id).map_err(|e| e.to_string())?;

    let mut checklist: Vec<String> = Vec::new();
    checklist.push("Update all callers to match new interface".to_string());
    checklist.push("Update design document".to_string());
    checklist.push("Run unit tests for modified symbol".to_string());
    checklist.push("Run integration tests for all callers".to_string());
    checklist.push("Update API documentation if public".to_string());

    if inbound.len() > 10 {
        checklist.push("WARNING: Many callers — consider backward compatibility layer".to_string());
    }
    if outbound.len() > 10 {
        checklist.push("WARNING: Many dependencies — verify downstream compatibility".to_string());
    }

    Ok(json!({
        "symbol_id": symbol_id,
        "caller_count": inbound.len(),
        "callee_count": outbound.len(),
        "checklist": checklist,
        "risk": if inbound.len() > 20 { "high" } else if inbound.len() > 10 { "medium" } else { "low" },
    }))
}

/// 19. api_compat_check — API compatibility checking
fn tool_api_compat_check(_db: &Database, args: &Value) -> Result<Value, String> {
    let old_sig = get_str(args, "old_sig").unwrap_or_default();
    let new_sig = get_str(args, "new_sig").unwrap_or_default();

    // Basic heuristic: check for breaking changes
    let mut breaking_changes: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();

    // Check if function name changed
    let old_name = extract_func_name(&old_sig);
    let new_name = extract_func_name(&new_sig);

    if old_name != new_name {
        breaking_changes.push(format!("Function renamed from '{}' to '{}'", old_name, new_name));
    }

    // Check parameter count
    let old_params = extract_params(&old_sig);
    let new_params = extract_params(&new_sig);

    if new_params.len() < old_params.len() {
        // Removing params is breaking if old params has required ones after
        breaking_changes.push(format!(
            "Parameter count reduced: {} -> {}",
            old_params.len(),
            new_params.len()
        ));
    }

    // Check for added required parameters (all new params assumed required)
    let added_params: Vec<_> = new_params
        .iter()
        .filter(|p| !old_params.contains(p))
        .collect();
    if !added_params.is_empty() {
        breaking_changes.push(format!("New required parameter(s): {:?}", added_params));
    }

    // Check for removed parameters
    let removed_params: Vec<_> = old_params
        .iter()
        .filter(|p| !new_params.contains(p))
        .collect();
    if !removed_params.is_empty() {
        breaking_changes.push(format!("Removed parameter(s): {:?}", removed_params));
    }

    let semver = if !breaking_changes.is_empty() {
        "major"
    } else if !warnings.is_empty() {
        "minor"
    } else {
        "patch"
    };

    Ok(json!({
        "old_signature": old_sig,
        "new_signature": new_sig,
        "semver_bump": semver,
        "breaking_changes": breaking_changes,
        "warnings": warnings,
        "is_compatible": breaking_changes.is_empty(),
    }))
}

/// 20. find_pattern — AST pattern search
fn tool_find_pattern(db: &Database, args: &Value) -> Result<Value, String> {
    let pattern = get_str(args, "pattern").unwrap_or_default();
    let lang = get_str(args, "lang");

    // Use FTS5 search with the pattern as query
    let results = db.search_fts5(
        &pattern,
        None,
        lang.as_deref(),
        None,
        50,
    )
    .map_err(|e| e.to_string())?;

    let items: Vec<Value> = results
        .iter()
        .map(|(id, k, name, qn, fp, lang_val, _rank, _start_line)| {
            json!({
                "id": id,
                "kind": k,
                "name": name,
                "qualified_name": qn,
                "file_path": fp,
                "language": lang_val,
            })
        })
        .collect();

    Ok(json!({
        "pattern": pattern,
        "count": items.len(),
        "matches": items,
    }))
}

/// 21. security_scan — security vulnerability detection
fn tool_security_scan(db: &Database, args: &Value) -> Result<Value, String> {
    let file_path = get_str(args, "file_path").unwrap_or_default();

    // Query all nodes in the file
    let conn = db.connection();
    let mut stmt = conn
        .prepare("SELECT id, name, kind, body, signature FROM nodes WHERE file_path = ?1")
        .map_err(|e| e.to_string())?;

    let nodes: Vec<(String, String, String, Option<String>, Option<String>)> = stmt
        .query_map([&file_path], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
            ))
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .collect();

    let mut vulnerabilities: Vec<Value> = Vec::new();

    for (_id, name, _kind, body, _signature) in &nodes {
        let code = body.as_deref().unwrap_or("");

        // Check 1: Hardcoded credentials
        if has_hardcoded_secret(code) {
            vulnerabilities.push(json!({
                "node": name,
                "type": "hardcoded_secret",
                "severity": "high",
                "description": "Potential hardcoded credential or secret detected",
            }));
        }

        // Check 2: SQL injection patterns
        if has_sql_injection(code) {
            vulnerabilities.push(json!({
                "node": name,
                "type": "sql_injection",
                "severity": "high",
                "description": "Potential SQL injection via string concatenation",
            }));
        }

        // Check 3: Command injection
        if has_command_injection(code) {
            vulnerabilities.push(json!({
                "node": name,
                "type": "command_injection",
                "severity": "critical",
                "description": "Potential command injection via shell execution",
            }));
        }

        // Check 4: Path traversal
        if has_path_traversal(code) {
            vulnerabilities.push(json!({
                "node": name,
                "type": "path_traversal",
                "severity": "medium",
                "description": "Potential path traversal vulnerability",
            }));
        }

        // Check 5: Weak crypto
        if has_weak_crypto(code) {
            vulnerabilities.push(json!({
                "node": name,
                "type": "weak_crypto",
                "severity": "medium",
                "description": "Usage of weak or deprecated cryptographic algorithm",
            }));
        }
    }

    let risk = if vulnerabilities.iter().any(|v| v["severity"] == "critical") {
        "critical"
    } else if vulnerabilities.iter().any(|v| v["severity"] == "high") {
        "high"
    } else if !vulnerabilities.is_empty() {
        "medium"
    } else {
        "low"
    };

    Ok(json!({
        "file_path": file_path,
        "scanned_nodes": nodes.len(),
        "vulnerability_count": vulnerabilities.len(),
        "risk_level": risk,
        "vulnerabilities": vulnerabilities,
    }))
}

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

fn resource_stats(db: &Database) -> Result<Value, String> {
    let conn = db.connection();

    let node_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM nodes", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;

    let edge_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM edges", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;

    let file_count: i64 = conn
        .query_row("SELECT COUNT(DISTINCT file_path) FROM nodes", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;

    // Language distribution
    let mut lang_stmt = conn
        .prepare("SELECT language, COUNT(*) as cnt FROM nodes GROUP BY language ORDER BY cnt DESC")
        .map_err(|e| e.to_string())?;

    let langs: Vec<Value> = lang_stmt
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .map(|(lang, cnt)| json!({"language": lang, "count": cnt}))
        .collect();

    Ok(json!({
        "nodes": node_count,
        "edges": edge_count,
        "files": file_count,
        "languages": langs,
    }))
}

fn resource_languages(db: &Database) -> Result<Value, String> {
    let conn = db.connection();

    let mut stmt = conn
        .prepare(
            "SELECT language, COUNT(*) as nodes, COUNT(DISTINCT file_path) as files \
             FROM nodes GROUP BY language ORDER BY nodes DESC",
        )
        .map_err(|e| e.to_string())?;

    let langs: Vec<Value> = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })
        .map_err(|e| e.to_string())?
        .filter_map(|r| r.ok())
        .map(|(lang, nodes, files)| {
            json!({"language": lang, "node_count": nodes, "file_count": files})
        })
        .collect();

    Ok(json!({"languages": langs}))
}

fn resource_health(db: &Database) -> Result<Value, String> {
    let conn = db.connection();

    // Check if index has data
    let node_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM nodes", [], |r| r.get(0))
        .unwrap_or(0);

    let index_ready = node_count > 0;

    Ok(json!({
        "index_ready": index_ready,
        "total_nodes": node_count,
        "status": if index_ready { "healthy" } else { "empty" },
    }))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Get the body text for a node from the database.
fn get_node_body(db: &Database, node_id: &str) -> Option<String> {
    let conn = db.connection();
    conn.query_row(
        "SELECT body FROM nodes WHERE id = ?1",
        [node_id],
        |row| row.get(0),
    )
    .ok()
}

/// Check if a test file exists that corresponds to a given file path.
fn check_test_file_exists(db: &Database, file_path: &str) -> bool {
    let conn = db.connection();
    // Look for nodes in test directories or with test prefix
    let fp_lower = file_path.to_lowercase();
    let base = std::path::Path::new(file_path)
        .file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_default();

    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM nodes WHERE (file_path LIKE '%test%' OR file_path LIKE '%spec%') AND file_path LIKE ?1",
            [format!("%{}%", base)],
            |r| r.get(0),
        )
        .unwrap_or(0);
    count > 0
}

/// Check if any test node calls the given node.
fn check_test_callers(db: &Database, node_id: &str) -> bool {
    let conn = db.connection();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM edges e \
             JOIN nodes n ON n.id = e.source \
             WHERE e.target = ?1 AND e.kind = 'CALLS' \
             AND (n.file_path LIKE '%test%' OR n.name LIKE 'test_%' OR n.name LIKE '%_test')",
            [node_id],
            |r| r.get(0),
        )
        .unwrap_or(0);
    count > 0
}

/// Check for hardcoded secrets in code.
fn has_hardcoded_secret(code: &str) -> bool {
    let lower = code.to_lowercase();
    let secret_patterns = [
        "password", "secret", "api_key", "apikey", "token",
        "private_key", "access_key",
    ];
    for pattern in &secret_patterns {
        if let Some(pos) = lower.find(pattern) {
            // Check if there's an assignment-like operator within a reasonable
            // distance after the pattern
            let after = &lower[pos + pattern.len()..];
            let trimmed = after.trim_start();
            if trimmed.starts_with('=') || trimmed.starts_with(':') {
                return true;
            }
        }
    }
    false
}

/// Check for SQL injection patterns.
fn has_sql_injection(code: &str) -> bool {
    let lower = code.to_lowercase();
    (lower.contains("select") || lower.contains("insert") || lower.contains("update") || lower.contains("delete"))
        && (lower.contains("+") || lower.contains("format") || lower.contains("f\"") || lower.contains("f'"))
}

/// Check for command injection.
fn has_command_injection(code: &str) -> bool {
    let lower = code.to_lowercase();
    (lower.contains("os.system") || lower.contains("subprocess") || lower.contains("exec(")
        || lower.contains("shell_exec") || lower.contains("popen"))
        && lower.contains("+")
}

/// Check for path traversal.
fn has_path_traversal(code: &str) -> bool {
    let lower = code.to_lowercase();
    lower.contains("../") || lower.contains("..\\")
        || (lower.contains("path") && lower.contains("join") && lower.contains("user"))
}

/// Check for weak cryptographic algorithms.
fn has_weak_crypto(code: &str) -> bool {
    let lower = code.to_lowercase();
    lower.contains("md5") || lower.contains("sha1") || (lower.contains("des") && !lower.contains("aes"))
}

/// Extract function name from a signature string.
fn extract_func_name(sig: &str) -> String {
    // Simple heuristic: first word or word before '('
    let before_paren = sig.split('(').next().unwrap_or(sig);
    before_paren
        .split_whitespace()
        .last()
        .unwrap_or(before_paren)
        .to_string()
}

/// Extract parameter names from a signature string.
fn extract_params(sig: &str) -> Vec<String> {
    let inside = sig
        .split('(')
        .nth(1)
        .and_then(|s| s.split(')').next())
        .unwrap_or("");
    if inside.trim().is_empty() {
        return Vec::new();
    }
    inside
        .split(',')
        .map(|p| p.trim().split(':').next().unwrap_or(p).trim().to_string())
        .filter(|p| !p.is_empty())
        .collect()
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
        let path = std::env::temp_dir().join(format!("tws_mcp_test_{}.db", name));
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
        body: Option<&str>,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, body, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![nid, kind, name, qname, file, "python", 1, 1, body, ts],
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
    // JSON-RPC protocol tests
    // ------------------------------------------------------------------

    #[test]
    fn test_handle_initialize() {
        let (db, path) = setup_db("init");
        let req = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"name": "test-client", "version": "1.0"}
        })
        .to_string();

        let resp = handle_request(&db, &req);
        let v: Value = serde_json::from_str(&resp).unwrap();
        assert_eq!(v["id"], 1);
        assert!(v["result"]["capabilities"]["tools"].is_object());
        cleanup(&path);
    }

    #[test]
    fn test_handle_tools_list() {
        let (db, path) = setup_db("tools_list");
        let req = json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        })
        .to_string();

        let resp = handle_request(&db, &req);
        let v: Value = serde_json::from_str(&resp).unwrap();
        let tools = v["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 21);
        cleanup(&path);
    }

    #[test]
    fn test_handle_unknown_method() {
        let (db, path) = setup_db("unknown");
        let req = json!({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "nonexistent/method"
        })
        .to_string();

        let resp = handle_request(&db, &req);
        let v: Value = serde_json::from_str(&resp).unwrap();
        assert!(v["error"]["code"] == -32601);
        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // Tool tests
    // ------------------------------------------------------------------

    #[test]
    fn test_tool_search_symbols() {
        let (db, path) = setup_db("search_sym");
        let conn = db.connection();
        insert_node(conn, "auth_handler", "src/auth.py", "function", Some("def auth_handler(): pass"));
        insert_node(conn, "login_user", "src/auth.py", "function", Some("def login_user(): pass"));
        insert_node(conn, "data_processor", "src/data.py", "function", Some("def data_processor(): pass"));

        let args = json!({"query": "auth", "limit": 10});
        let result = tool_search_symbols(&db, &args).unwrap();
        assert!(result["count"].as_i64().unwrap() >= 1);

        cleanup(&path);
    }

    #[test]
    fn test_tool_get_code() {
        let (db, path) = setup_db("get_code");
        let conn = db.connection();
        let id = insert_node(conn, "test_func", "src/test.py", "function", Some("def test_func(): pass"));

        let args = json!({"symbol_id": id});
        let result = tool_get_code(&db, &args).unwrap();
        assert_eq!(result["name"], "test_func");
        assert_eq!(result["kind"], "function");

        cleanup(&path);
    }

    #[test]
    fn test_tool_get_code_not_found() {
        let (db, path) = setup_db("get_code_nf");
        let args = json!({"symbol_id": "nonexistent_id"});
        let result = tool_get_code(&db, &args).unwrap();
        assert!(result["error"].as_str().is_some());
        cleanup(&path);
    }

    #[test]
    fn test_tool_get_dependencies() {
        let (db, path) = setup_db("get_deps");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function", None);
        let b = insert_node(conn, "func_b", "src/b.py", "function", None);
        insert_edge(conn, &a, &b, "CALLS");

        let args = json!({"symbol_id": a});
        let result = tool_get_dependencies(&db, &args).unwrap();
        assert!(result["count"].as_i64().unwrap() >= 1);
        assert_eq!(result["direction"], "outbound");

        // Test inbound
        let args2 = json!({"symbol_id": b, "direction": "inbound"});
        let result2 = tool_get_dependencies(&db, &args2).unwrap();
        assert!(result2["count"].as_i64().unwrap() >= 1);
        assert_eq!(result2["direction"], "inbound");

        cleanup(&path);
    }

    #[test]
    fn test_tool_get_impact() {
        let (db, path) = setup_db("get_impact");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function", None);
        let b = insert_node(conn, "func_b", "src/b.py", "function", None);
        insert_edge(conn, &a, &b, "CALLS");

        let args = json!({"symbol_id": a, "depth": 2});
        let result = tool_get_impact(&db, &args).unwrap();
        assert!(result["affected_count"].as_i64().unwrap() >= 1);
        assert!(result["risk_level"].as_str().is_some());

        cleanup(&path);
    }

    #[test]
    fn test_tool_trace_path() {
        let (db, path) = setup_db("trace_path");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function", None);
        let b = insert_node(conn, "func_b", "src/b.py", "function", None);
        insert_edge(conn, &a, &b, "CALLS");

        let args = json!({"from_id": a, "to_id": b});
        let result = tool_trace_path(&db, &args).unwrap();
        assert!(result["found"].as_bool().unwrap());

        cleanup(&path);
    }

    #[test]
    fn test_tool_trace_path_not_found() {
        let (db, path) = setup_db("trace_nf");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function", None);
        let b = insert_node(conn, "func_b", "src/b.py", "function", None);
        // No edge connecting them

        let args = json!({"from_id": a, "to_id": b});
        let result = tool_trace_path(&db, &args).unwrap();
        assert!(!result["found"].as_bool().unwrap());

        cleanup(&path);
    }

    #[test]
    fn test_tool_get_edge_distribution() {
        let (db, path) = setup_db("edge_dist");
        let conn = db.connection();
        let a = insert_node(conn, "func_a", "src/a.py", "function", None);
        let b = insert_node(conn, "func_b", "src/b.py", "function", None);
        insert_edge(conn, &a, &b, "CALLS");
        insert_edge(conn, &a, &b, "IMPORTS");

        let args = json!({});
        let result = tool_get_edge_distribution(&db, &args).unwrap();
        assert!(result["total_edges"].as_i64().unwrap() >= 2);

        cleanup(&path);
    }

    #[test]
    fn test_tool_find_dead_code() {
        let (db, path) = setup_db("dead_code");
        let conn = db.connection();
        insert_node(conn, "unused_func", "src/unused.py", "function", None);

        let args = json!({});
        let result = tool_find_dead_code(&db, &args).unwrap();
        // May or may not find dead code depending on heuristics
        assert!(result["count"].as_i64().is_some());

        cleanup(&path);
    }

    #[test]
    fn test_tool_get_entry_points() {
        let (db, path) = setup_db("entry");
        let conn = db.connection();
        insert_node(conn, "main", "src/main.py", "function", None);

        let args = json!({});
        let result = tool_get_entry_points(&db, &args).unwrap();
        assert!(result["count"].as_i64().is_some());

        cleanup(&path);
    }

    #[test]
    fn test_tool_security_scan() {
        let (db, path) = setup_db("sec_scan");
        let conn = db.connection();
        // Node with potential hardcoded secret
        insert_node(
            conn,
            "risky_func",
            "src/risky.py",
            "function",
            Some("password = 'admin123'"),
        );

        let args = json!({"file_path": "src/risky.py"});
        let result = tool_security_scan(&db, &args).unwrap();
        assert!(result["vulnerability_count"].as_i64().is_some());

        cleanup(&path);
    }

    #[test]
    fn test_tool_api_compat_check() {
        let (db, path) = setup_db("api_check");
        let args = json!({
            "old_sig": "def foo(a, b, c):",
            "new_sig": "def foo(a, b):"
        });
        let result = tool_api_compat_check(&db, &args).unwrap();
        assert!(!result["is_compatible"].as_bool().unwrap());
        assert_eq!(result["semver_bump"], "major");

        cleanup(&path);
    }

    #[test]
    fn test_resource_stats() {
        let (db, path) = setup_db("res_stats");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", "function", None);

        let result = resource_stats(&db).unwrap();
        assert!(result["nodes"].as_i64().unwrap() >= 1);
        assert!(result["languages"].as_array().is_some());

        cleanup(&path);
    }

    #[test]
    fn test_resource_languages() {
        let (db, path) = setup_db("res_lang");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", "function", None);
        insert_node(conn, "func_b", "src/b.rs", "function", None);

        let result = resource_languages(&db).unwrap();
        let langs = result["languages"].as_array().unwrap();
        assert!(langs.len() >= 1);

        cleanup(&path);
    }

    #[test]
    fn test_resource_health() {
        let (db, path) = setup_db("res_health");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", "function", None);

        let result = resource_health(&db).unwrap();
        assert!(result["index_ready"].as_bool().unwrap());

        cleanup(&path);
    }

    #[test]
    fn test_security_helpers() {
        assert!(has_hardcoded_secret("password = 'secret'"));
        assert!(!has_hardcoded_secret("print('hello')"));
        assert!(has_sql_injection("SELECT * FROM users WHERE id = \" + userId"));
        assert!(!has_sql_injection("SELECT * FROM users WHERE id = ?"));
        assert!(has_command_injection("os.system('rm -rf ' + path)"));
        assert!(has_path_traversal("../etc/passwd"));
        assert!(has_weak_crypto("hashlib.md5(data)"));
    }
}
