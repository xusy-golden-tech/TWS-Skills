//! Entry point identification — discovers application entry points using 6 heuristics.
//!
//! Heuristics (with confidence scores):
//! 1. Name matches "main" (0.95)
//! 2. Contains "__main__" or test_ prefix (0.9)
//! 3. CLI entry points (typer/click/argparse decorators) (0.85)
//! 4. Route handlers (FastAPI/Flask/Express decorators) (0.8)
//! 5. __init__.py files (0.7)
//! 6. Lifecycle hooks (startup/shutdown) (0.6)

use crate::db::Database;

/// An entry point candidate with its confidence score and reason.
#[derive(Debug, Clone, PartialEq)]
pub struct EntryPoint {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub confidence: f64,
    pub reason: String,
}

/// Find entry points in the code graph.
///
/// Returns a list of `(id, name, kind, reason)` tuples with the entry point candidates.
pub fn find_entry_points(db: &Database) -> Vec<(String, String, String, String)> {
    let conn = db.connection();

    // Get all nodes with relevant metadata
    let mut stmt = conn
        .prepare(
            "SELECT id, kind, name, file_path, decorators, framework, language \
             FROM nodes",
        )
        .expect("failed to prepare nodes query");

    let nodes: Vec<(String, String, String, String, Option<String>, Option<String>, String)> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
                row.get::<_, String>(6)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    let mut entry_points: Vec<EntryPoint> = Vec::new();

    for (id, kind, name, file_path, decorators, framework, language) in &nodes {
        let name_lower = name.to_lowercase();
        let file_lower = file_path.to_lowercase();

        // Heuristic 1: Name matches "main" (confidence 0.95)
        if name_lower == "main" {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.95,
                reason: "Name matches 'main'".to_string(),
            });
            continue;
        }

        // Heuristic 2: Contains "__main__" or test_ prefix (0.9)
        if name.contains("__main__") {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.9,
                reason: "Contains '__main__'".to_string(),
            });
            continue;
        }
        if name.starts_with("test_") {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.9,
                reason: "Test function prefix 'test_'".to_string(),
            });
            continue;
        }

        // Heuristic 3: CLI entry points (typer/click/argparse decorators) (0.85)
        if let Some(ref dec) = decorators {
            let dec_lower = dec.to_lowercase();
            if dec_lower.contains("click.")
                || dec_lower.contains("typer.")
                || dec_lower.contains("argparse")
                || dec_lower.contains("clap")
                || dec_lower.contains("commander")
                || dec_lower.contains("@command")
                || dec_lower.contains("@cli")
            {
                entry_points.push(EntryPoint {
                    id: id.clone(),
                    name: name.clone(),
                    kind: kind.clone(),
                    file_path: file_path.clone(),
                    confidence: 0.85,
                    reason: "CLI decorator detected".to_string(),
                });
                continue;
            }
        }

        // Heuristic 4: Route handlers (FastAPI/Flask/Express decorators) (0.8)
        let has_route = route_indicator(name, decorators, framework, file_path, language);
        if has_route {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.8,
                reason: "Route handler detected".to_string(),
            });
            continue;
        }

        // Heuristic 5: __init__.py files (0.7)
        if file_path.ends_with("__init__.py") || file_path.ends_with("__init__.pyi") {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.7,
                reason: "Python __init__.py file".to_string(),
            });
            continue;
        }
        // index.ts, index.js pattern
        if file_lower.ends_with("index.ts") || file_lower.ends_with("index.tsx")
            || file_lower.ends_with("index.js") || file_lower.ends_with("index.jsx")
        {
            if name_lower.contains("export") || kind == "module" || kind == "file" {
                entry_points.push(EntryPoint {
                    id: id.clone(),
                    name: name.clone(),
                    kind: kind.clone(),
                    file_path: file_path.clone(),
                    confidence: 0.7,
                    reason: "Index/barrel export file".to_string(),
                });
                continue;
            }
        }

        // Heuristic 6: Lifecycle hooks (startup/shutdown) (0.6)
        if name_lower == "startup" || name_lower == "shutdown" {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.6,
                reason: "Lifecycle hook".to_string(),
            });
            continue;
        }
        if name_lower == "on_startup" || name_lower == "on_shutdown"
            || name_lower == "before_start" || name_lower == "after_stop"
        {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.6,
                reason: "Lifecycle hook".to_string(),
            });
            continue;
        }
        if name_lower == "ngoninit" || name_lower == "ngondestroy"
            || name_lower == "componentdidmount" || name_lower == "componentwillunmount"
            || name_lower == "useefect"
        {
            entry_points.push(EntryPoint {
                id: id.clone(),
                name: name.clone(),
                kind: kind.clone(),
                file_path: file_path.clone(),
                confidence: 0.6,
                reason: "Component lifecycle hook".to_string(),
            });
            continue;
        }

        let _ = language;
    }

    // Sort by confidence (highest first), then by name
    entry_points.sort_by(|a, b| {
        b.confidence
            .partial_cmp(&a.confidence)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.name.cmp(&b.name))
    });

    entry_points
        .into_iter()
        .map(|ep| (ep.id, ep.name, ep.kind, format!("{:.2}: {}", ep.confidence, ep.reason)))
        .collect()
}

/// Check if a node is a route handler based on framework hints and naming conventions.
fn route_indicator(
    name: &str,
    decorators: &Option<String>,
    framework: &Option<String>,
    file_path: &str,
    language: &str,
) -> bool {
    let name_lower = name.to_lowercase();
    let file_lower = file_path.to_lowercase();

    // Framework name hints
    if let Some(ref fw) = framework {
        let fw_lower = fw.to_lowercase();
        if fw_lower.contains("fastapi")
            || fw_lower.contains("flask")
            || fw_lower.contains("express")
            || fw_lower.contains("django")
            || fw_lower.contains("spring")
            || fw_lower.contains("actix")
            || fw_lower.contains("gin")
            || fw_lower.contains("echo")
            || fw_lower.contains("axum")
            || fw_lower.contains("rocket")
        {
            return true;
        }
    }

    // Decorator hints
    if let Some(ref dec) = decorators {
        let dec_lower = dec.to_lowercase();
        if dec_lower.contains("@app.")
            || dec_lower.contains("@router.")
            || dec_lower.contains("@route")
            || dec_lower.contains("@get")
            || dec_lower.contains("@post")
            || dec_lower.contains("@put")
            || dec_lower.contains("@delete")
            || dec_lower.contains("@patch")
            || dec_lower.contains("@httpget")
            || dec_lower.contains("@httppost")
            || dec_lower.contains("@requestmapping")
            || dec_lower.contains("@websocket")
            || dec_lower.contains("@messagepattern")
        {
            return true;
        }
    }

    // File path hints
    if file_lower.contains("/routes/")
        || file_lower.contains("/handlers/")
        || file_lower.contains("/views/")
        || file_lower.contains("/controllers/")
        || file_lower.ends_with("_route.py")
        || file_lower.ends_with("_handler.py")
        || file_lower.ends_with("_view.py")
        || file_lower.ends_with("_controller.py")
    {
        return true;
    }

    // Name pattern hints
    if name_lower.starts_with("handle_")
        || name_lower.ends_with("_handler")
        || name_lower.ends_with("_route")
        || name_lower.ends_with("_endpoint")
        || name_lower.ends_with("_view")
    {
        return true;
    }

    let _ = language;

    false
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
        let path = std::env::temp_dir().join(format!("tws_entry_test_{}.db", name));
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

    fn insert_node_with_meta(
        conn: &rusqlite::Connection,
        name: &str,
        file: &str,
        kind: &str,
        decorators: Option<&str>,
        framework: Option<&str>,
        language: &str,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, decorators, framework, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![nid, kind, name, qname, file, language, 1, 1, decorators, framework, ts],
        )
        .unwrap();
        nid
    }

    #[test]
    fn test_main_function_detected() {
        let (db, path) = setup_db("main_detect");
        let conn = db.connection();

        insert_node_with_meta(conn, "main", "src/app.py", "function", None, None, "python");

        let entries = find_entry_points(&db);
        let main_entries: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "main").collect();
        assert!(!main_entries.is_empty(), "main function should be detected");
        assert!(main_entries[0].3.contains("0.95"), "Should have confidence 0.95");

        cleanup(&path);
    }

    #[test]
    fn test_cli_decorator_detected() {
        let (db, path) = setup_db("cli_detect");
        let conn = db.connection();

        insert_node_with_meta(conn, "my_command", "src/cli.py", "function",
            Some("@click.command()"), None, "python");

        let entries = find_entry_points(&db);
        let cli: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "my_command").collect();
        assert!(!cli.is_empty(), "CLI decorator should be detected");
        assert!(cli[0].3.contains("0.85"), "Should have confidence 0.85");

        cleanup(&path);
    }

    #[test]
    fn test_route_handler_detected() {
        let (db, path) = setup_db("route_detect");
        let conn = db.connection();

        insert_node_with_meta(conn, "get_users", "src/routes/user_routes.py", "function",
            Some("@app.get('/users')"), Some("fastapi"), "python");

        let entries = find_entry_points(&db);
        let routes: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "get_users").collect();
        assert!(!routes.is_empty(), "Route handler should be detected");
        assert!(routes[0].3.contains("0.8"), "Should have confidence 0.8");

        cleanup(&path);
    }

    #[test]
    fn test_init_py_detected() {
        let (db, path) = setup_db("init_detect");
        let conn = db.connection();

        insert_node_with_meta(conn, "init_func", "src/core/__init__.py", "function", None, None, "python");

        let entries = find_entry_points(&db);
        let init_entries: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "init_func").collect();
        assert!(!init_entries.is_empty(), "__init__.py file should be detected");
        assert!(init_entries[0].3.contains("0.7"), "Should have confidence 0.7");

        cleanup(&path);
    }

    #[test]
    fn test_lifecycle_hook_detected() {
        let (db, path) = setup_db("lifecycle_detect");
        let conn = db.connection();

        insert_node_with_meta(conn, "startup", "src/app.py", "function", None, None, "python");

        let entries = find_entry_points(&db);
        let hooks: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "startup").collect();
        assert!(!hooks.is_empty(), "Lifecycle hook should be detected");
        assert!(hooks[0].3.contains("0.6"), "Should have confidence 0.6");

        cleanup(&path);
    }

    #[test]
    fn test_regular_function_not_entry() {
        let (db, path) = setup_db("not_entry");
        let conn = db.connection();

        insert_node_with_meta(conn, "helper", "src/utils.py", "function", None, None, "python");

        let entries = find_entry_points(&db);
        let helpers: Vec<_> = entries.iter().filter(|(_, name, _, _)| name == "helper").collect();
        assert!(helpers.is_empty(), "Regular helper function should not be an entry point");

        cleanup(&path);
    }

    #[test]
    fn test_entries_sorted_by_confidence() {
        let (db, path) = setup_db("sorted_entries");
        let conn = db.connection();

        // Insert nodes in unsorted order (by confidence)
        insert_node_with_meta(conn, "on_shutdown", "src/app.py", "function", None, None, "python"); // 0.6
        insert_node_with_meta(conn, "main", "src/main.py", "function", None, None, "python"); // 0.95
        insert_node_with_meta(conn, "test_login", "tests/test_auth.py", "function", None, None, "python"); // 0.9

        let entries = find_entry_points(&db);
        assert!(!entries.is_empty());

        // First entry should have highest confidence (main = 0.95)
        assert!(entries[0].3.contains("0.95"));

        // Entries should be in descending confidence order
        let confs: Vec<f64> = entries.iter().map(|(_, _, _, r)| {
            r.split(':').next().unwrap().parse::<f64>().unwrap_or(0.0)
        }).collect();
        for i in 1..confs.len() {
            assert!(confs[i - 1] >= confs[i], "Entries should be sorted by descending confidence");
        }

        cleanup(&path);
    }
}
