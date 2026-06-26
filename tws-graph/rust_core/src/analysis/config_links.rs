//! Config-link detection — finds connections between code constants and config files.
//!
//! Searches for code constant nodes that match config keys in YAML/JSON/TOML/ENV
//! files using three strategies: exact string match, snake_case/CamelCase conversion,
//! and path proximity.

use crate::db::Database;
use std::collections::HashMap;

/// Find links between code constants and configuration keys.
///
/// Returns `(code_node_id, code_name, config_key, config_file)` tuples.
pub fn find_config_links(db: &Database) -> Vec<(String, String, String, String)> {
    let conn = db.connection();

    // Get all constant nodes from code
    let mut const_stmt = conn
        .prepare(
            "SELECT id, name, qualified_name, file_path FROM nodes WHERE kind = 'constant'",
        )
        .expect("failed to prepare constants query");
    let constants: Vec<(String, String, String, String)> = const_stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .expect("failed to query constants")
        .filter_map(|r| r.ok())
        .collect();

    // Get config-type nodes (yaml_key, json_key, toml_table, etc.)
    let mut config_stmt = conn
        .prepare(
            "SELECT id, name, file_path, kind FROM nodes WHERE kind IN \
             ('yaml_key', 'json_key', 'toml_table', 'toml_table_array', \
              'hcl_variable', 'hcl_output', 'hcl_locals')",
        )
        .expect("failed to prepare config nodes query");
    let config_nodes: Vec<(String, String, String, String)> = config_stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .expect("failed to query config nodes")
        .filter_map(|r| r.ok())
        .collect();

    if constants.is_empty() || config_nodes.is_empty() {
        return Vec::new();
    }

    // Build lookup: normalized config key -> (config_key, config_file) pairs
    let mut config_map: HashMap<String, Vec<(String, String)>> = HashMap::new();
    for (_id, name, file, _kind) in &config_nodes {
        let normalized = normalize_config_key(name);
        config_map
            .entry(normalized)
            .or_default()
            .push((name.clone(), file.clone()));
    }

    let mut results: Vec<(String, String, String, String)> = Vec::new();

    for (code_id, code_name, _qn, code_file) in &constants {
        // Strategy 1: Exact lowercase match
        let code_lower = code_name.to_lowercase();
        if let Some(matches) = config_map.get(&code_lower) {
            for (config_key, config_file) in matches {
                results.push((
                    code_id.clone(),
                    code_name.clone(),
                    config_key.clone(),
                    config_file.clone(),
                ));
            }
            continue;
        }

        // Strategy 2: CamelCase/snake_case conversions
        let normalized_const = normalize_code_constant(code_name);
        if normalized_const != code_lower {
            if let Some(matches) = config_map.get(&normalized_const) {
                for (config_key, config_file) in matches {
                    results.push((
                        code_id.clone(),
                        code_name.clone(),
                        config_key.clone(),
                        config_file.clone(),
                    ));
                }
            }
        }

        // Strategy 3: Path proximity — same directory with similar name
        if !results.iter().any(|(cid, _, _, _)| cid == code_id) {
            let const_dir = std::path::Path::new(code_file)
                .parent()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();

            for (_id, config_name, config_file, _kind) in &config_nodes {
                let config_dir = std::path::Path::new(config_file)
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default();

                if const_dir == config_dir && is_proximity_match(code_name, config_name) {
                    results.push((
                        code_id.clone(),
                        code_name.clone(),
                        config_name.clone(),
                        config_file.clone(),
                    ));
                }
            }
        }
    }

    results
}

/// Normalize a config key for matching: lowercase, replace dots/dashes with underscores.
fn normalize_config_key(key: &str) -> String {
    key.to_lowercase()
        .replace('.', "_")
        .replace('-', "_")
        .trim()
        .to_string()
}

/// Normalize a code constant name: convert CamelCase to snake_case, lowercase.
fn normalize_code_constant(name: &str) -> String {
    let mut result = String::new();
    let chars: Vec<char> = name.chars().collect();
    for (i, &c) in chars.iter().enumerate() {
        if c.is_uppercase() {
            if i > 0 && chars[i - 1].is_lowercase() {
                result.push('_');
            }
            result.push(c.to_lowercase().next().unwrap_or(c));
        } else {
            result.push(c);
        }
    }
    result.trim_matches('_').to_string()
}

/// Check if two names are similar enough for proximity-based matching.
fn is_proximity_match(const_name: &str, config_name: &str) -> bool {
    let c1 = const_name.to_lowercase();
    let c2 = config_name.to_lowercase();
    c1.contains(&c2) || c2.contains(&c1)
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
        let path = std::env::temp_dir().join(format!("tws_config_test_{}.db", name));
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

    #[test]
    fn test_exact_match_found() {
        let (db, path) = setup_db("exact_match");
        let conn = db.connection();

        insert_node(conn, "database_url", "src/config.py", "constant");
        insert_node(conn, "database_url", "config/settings.yaml", "yaml_key");

        let links = find_config_links(&db);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].1, "database_url");
        assert_eq!(links[0].2, "database_url");

        cleanup(&path);
    }

    #[test]
    fn test_camelcase_conversion() {
        let (db, path) = setup_db("camelcase");
        let conn = db.connection();

        insert_node(conn, "DatabaseUrl", "src/db.py", "constant");
        insert_node(conn, "database_url", "config/app.yaml", "yaml_key");

        let links = find_config_links(&db);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].1, "DatabaseUrl");
        assert_eq!(links[0].2, "database_url");

        cleanup(&path);
    }

    #[test]
    fn test_no_match_different_keys() {
        let (db, path) = setup_db("no_match");
        let conn = db.connection();

        insert_node(conn, "MAX_RETRIES", "src/config.py", "constant");
        insert_node(conn, "timeout_seconds", "config/settings.yaml", "yaml_key");

        let links = find_config_links(&db);
        assert_eq!(links.len(), 0, "Expected no matches, got {:?}", links);

        cleanup(&path);
    }

    #[test]
    fn test_empty_data() {
        let (db, path) = setup_db("empty_config");
        let links = find_config_links(&db);
        assert!(links.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_proximity_match() {
        let (db, path) = setup_db("proximity");
        let conn = db.connection();

        // Same directory with similar names
        insert_node(conn, "redis_port", "config/settings.py", "constant");
        insert_node(conn, "redis_port", "config/settings.yaml", "yaml_key");

        let links = find_config_links(&db);
        assert_eq!(links.len(), 1);

        cleanup(&path);
    }

    #[test]
    fn test_multiple_config_keys() {
        let (db, path) = setup_db("multi_config");
        let conn = db.connection();

        insert_node(conn, "host", "src/db.py", "constant");
        insert_node(conn, "host", "config/db.yaml", "yaml_key");
        insert_node(conn, "port", "src/db.py", "constant");
        insert_node(conn, "port", "config/db.yaml", "yaml_key");
        // No matching config key for this one
        insert_node(conn, "internal_counter", "src/db.py", "constant");

        let links = find_config_links(&db);
        assert_eq!(links.len(), 2, "Expected 2 links, got {}: {:?}", links.len(), links);

        cleanup(&path);
    }
}
