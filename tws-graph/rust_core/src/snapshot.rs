//! Snapshot management — create, list, and compare code-graph snapshots.
//!
//! Snapshots are stored as SQLite database copies in `.tws/snapshots/` using
//! SQLite's `VACUUM INTO` command (SQLite 3.27.0+). This creates a
//! self-contained copy of the current database state that can be compared
//! with future states to track code-graph evolution.

use crate::db::Database;
use rusqlite::Result as SqlResult;
use std::path::{Path, PathBuf};

/// Create a named snapshot of the current database state.
///
/// Copies the database to `.tws/snapshots/{name}.db` using `VACUUM INTO`.
pub fn create_snapshot(db: &Database, name: &str, repo_root: &Path) -> SqlResult<()> {
    let snapshots_dir = repo_root.join(".tws").join("snapshots");
    std::fs::create_dir_all(&snapshots_dir).map_err(|e| {
        rusqlite::Error::ToSqlConversionFailure(Box::new(e))
    })?;

    let snapshot_path = snapshots_dir.join(format!("{}.db", name));
    let snapshot_path_str = snapshot_path.to_string_lossy().replace('\\', "/");

    // Use VACUUM INTO to create a compact copy of the database
    let conn = db.connection();
    conn.execute_batch(&format!("VACUUM INTO '{}';", snapshot_path_str.replace('\'', "''")))?;

    Ok(())
}

/// List all available snapshot names.
///
/// Scans `.tws/snapshots/` for `.db` files and returns their base names
/// (without the `.db` extension).
pub fn list_snapshots(repo_root: &Path) -> Vec<String> {
    let snapshots_dir = repo_root.join(".tws").join("snapshots");
    if !snapshots_dir.exists() {
        return Vec::new();
    }

    let mut names: Vec<String> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&snapshots_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().map(|e| e == "db").unwrap_or(false) {
                if let Some(stem) = path.file_stem() {
                    names.push(stem.to_string_lossy().to_string());
                }
            }
        }
    }
    names.sort();
    names
}

/// Result of comparing two snapshots.
#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct SnapshotDiff {
    /// Nodes added in the second snapshot.
    pub added_nodes: Vec<String>,
    /// Nodes removed in the second snapshot.
    pub removed_nodes: Vec<String>,
    /// Nodes changed (same ID, different body/signature).
    pub changed_nodes: Vec<String>,
    /// Count of unchanged nodes.
    pub unchanged_nodes: usize,
    /// New files detected.
    pub new_files: Vec<String>,
    /// Deleted files.
    pub deleted_files: Vec<String>,
}

/// Compare two snapshots and return the differences.
///
/// If `brief` is true, returns only summary counts (empty vectors for details).
pub fn diff_snapshots(
    repo_root: &Path,
    name1: &str,
    name2: &str,
    brief: bool,
) -> SqlResult<SnapshotDiff> {
    let snapshots_dir = repo_root.join(".tws").join("snapshots");
    let path1 = snapshots_dir.join(format!("{}.db", name1));
    let path2 = snapshots_dir.join(format!("{}.db", name2));

    if !path1.exists() {
        return Err(rusqlite::Error::ToSqlConversionFailure(
            Box::new(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                format!("Snapshot not found: {}", name1),
            )),
        ));
    }
    if !path2.exists() {
        return Err(rusqlite::Error::ToSqlConversionFailure(
            Box::new(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                format!("Snapshot not found: {}", name2),
            )),
        ));
    }

    let db1 = Database::open(&path1).map_err(|e| {
        rusqlite::Error::ToSqlConversionFailure(
            Box::new(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())),
        )
    })?;
    let db2 = Database::open(&path2).map_err(|e| {
        rusqlite::Error::ToSqlConversionFailure(
            Box::new(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())),
        )
    })?;

    let mut diff = SnapshotDiff::default();

    // Load node sets from both snapshots
    let nodes1 = load_node_map(db1.connection())?;
    let nodes2 = load_node_map(db2.connection())?;

    // Compare nodes
    let mut unchanged = 0usize;

    for (id, (name, kind, _file_path, body)) in &nodes1 {
        if let Some((name2, _kind2, _file_path2, body2)) = nodes2.get(id) {
            if body != body2 || name != name2 {
                if !brief {
                    diff.changed_nodes.push(format!("{} ({})", name, kind));
                }
            } else {
                unchanged += 1;
            }
        } else {
            if !brief {
                diff.removed_nodes.push(format!("{} ({})", name, kind));
            }
        }
    }

    for (id, (name, kind, _file_path, _body)) in &nodes2 {
        if !nodes1.contains_key(id) {
            if !brief {
                diff.added_nodes.push(format!("{} ({})", name, kind));
            }
        }
    }

    if !brief {

        // Compare files
        let files1 = load_files(db1.connection())?;
        let files2 = load_files(db2.connection())?;

        let mut files1_set: std::collections::HashSet<String> =
            files1.keys().cloned().collect();
        let files2_set: std::collections::HashSet<String> =
            files2.keys().cloned().collect();

        for f in files2_set.difference(&files1_set) {
            diff.new_files.push(f.clone());
        }
        for f in files1_set.difference(&files2_set) {
            diff.deleted_files.push(f.clone());
        }
    }

    diff.unchanged_nodes = unchanged;

    close_db(db1);
    close_db(db2);

    Ok(diff)
}

/// Load all nodes from a connection into a map keyed by node ID.
/// Returns `(node_id => (name, kind, file_path, body))`.
fn load_node_map(conn: &rusqlite::Connection) -> SqlResult<std::collections::HashMap<String, (String, String, String, Option<String>)>> {
    let mut map = std::collections::HashMap::new();
    let mut stmt = conn.prepare(
        "SELECT id, name, kind, file_path, body FROM nodes",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, Option<String>>(4)?,
        ))
    })?;
    for row in rows.flatten() {
        map.insert(row.0, (row.1, row.2, row.3, row.4));
    }
    Ok(map)
}

/// Load all unique file paths from a connection.
fn load_files(conn: &rusqlite::Connection) -> SqlResult<std::collections::HashMap<String, usize>> {
    let mut map = std::collections::HashMap::new();
    let mut stmt = conn.prepare(
        "SELECT file_path, COUNT(*) as cnt FROM nodes GROUP BY file_path",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, usize>(1)?,
        ))
    })?;
    for row in rows.flatten() {
        map.insert(row.0, row.1);
    }
    Ok(map)
}

/// Helper to close a Database without consuming it.
fn close_db(db: Database) {
    let _ = db.close();
}

/// Export a snapshot diff as a brief summary string.
pub fn format_diff_brief(diff: &SnapshotDiff, name1: &str, name2: &str) -> String {
    format!(
        "Snapshot diff: {} -> {}\n  +{} added, -{} removed, ~{} changed, ={} unchanged\n  Files: +{} new, -{} deleted",
        name1,
        name2,
        diff.added_nodes.len(),
        diff.removed_nodes.len(),
        diff.changed_nodes.len(),
        diff.unchanged_nodes,
        diff.new_files.len(),
        diff.deleted_files.len(),
    )
}

/// Export a snapshot diff as a detailed string including names.
pub fn format_diff_detailed(diff: &SnapshotDiff, name1: &str, name2: &str) -> String {
    let mut out = String::new();
    out.push_str(&format!("Snapshot diff: {} -> {}\n", name1, name2));
    out.push_str(&format!(
        "  Summary: +{} added, -{} removed, ~{} changed, ={} unchanged\n\n",
        diff.added_nodes.len(),
        diff.removed_nodes.len(),
        diff.changed_nodes.len(),
        diff.unchanged_nodes,
    ));

    if !diff.added_nodes.is_empty() {
        out.push_str("  Added:\n");
        for n in &diff.added_nodes {
            out.push_str(&format!("    + {}\n", n));
        }
        out.push('\n');
    }

    if !diff.removed_nodes.is_empty() {
        out.push_str("  Removed:\n");
        for n in &diff.removed_nodes {
            out.push_str(&format!("    - {}\n", n));
        }
        out.push('\n');
    }

    if !diff.changed_nodes.is_empty() {
        out.push_str("  Changed:\n");
        for n in &diff.changed_nodes {
            out.push_str(&format!("    ~ {}\n", n));
        }
        out.push('\n');
    }

    if !diff.new_files.is_empty() {
        out.push_str("  New files:\n");
        for f in &diff.new_files {
            out.push_str(&format!("    + {}\n", f));
        }
        out.push('\n');
    }

    if !diff.deleted_files.is_empty() {
        out.push_str("  Deleted files:\n");
        for f in &diff.deleted_files {
            out.push_str(&format!("    - {}\n", f));
        }
        out.push('\n');
    }

    out
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

    fn setup_db(name: &str) -> (Database, PathBuf) {
        let dir = std::env::temp_dir().join(format!("tws_snapshot_test_{}", name));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(".tws").join("snapshots")).unwrap();

        let db_path = dir.join(".tws").join("codegraph").join("index.db");
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }

        let db = Database::initialize(&db_path).unwrap();

        // Clean up temp files on drop
        let _ = std::fs::remove_file(db_path.with_extension("db-wal"));
        let _ = std::fs::remove_file(db_path.with_extension("db-shm"));

        (db, dir)
    }

    fn cleanup(dir: &Path) {
        let _ = std::fs::remove_dir_all(dir);
    }

    fn insert_node(conn: &rusqlite::Connection, name: &str, file: &str, body: Option<&str>) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, body, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, 'python', 1, 1, ?5, ?6)",
            params![nid, name, qname, file, body, ts],
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
    // Creation and listing tests
    // ------------------------------------------------------------------

    #[test]
    fn test_create_and_list_snapshots() {
        let (db, repo_root) = setup_db("create_list");
        let conn = db.connection();
        insert_node(conn, "test_func", "src/test.py", Some("def test_func(): pass"));

        create_snapshot(&db, "snap1", &repo_root).unwrap();
        create_snapshot(&db, "snap2", &repo_root).unwrap();

        let names = list_snapshots(&repo_root);
        assert!(names.contains(&"snap1".to_string()));
        assert!(names.contains(&"snap2".to_string()));
        assert_eq!(names.len(), 2);

        cleanup(&repo_root);
    }

    #[test]
    fn test_list_empty_snapshots() {
        let dir = std::env::temp_dir().join("tws_snapshot_empty_list");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(".tws").join("snapshots")).unwrap();

        let names = list_snapshots(&dir);
        assert!(names.is_empty());

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ------------------------------------------------------------------
    // Diff tests
    // ------------------------------------------------------------------

    #[test]
    fn test_diff_identical_snapshots() {
        let (db, repo_root) = setup_db("diff_identical");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", Some("def func_a(): pass"));
        insert_node(conn, "func_b", "src/b.py", Some("def func_b(): pass"));

        create_snapshot(&db, "v1", &repo_root).unwrap();
        create_snapshot(&db, "v2", &repo_root).unwrap();

        let diff = diff_snapshots(&repo_root, "v1", "v2", false).unwrap();
        assert!(diff.added_nodes.is_empty());
        assert!(diff.removed_nodes.is_empty());
        assert!(diff.changed_nodes.is_empty());
        assert!(diff.unchanged_nodes > 0);

        cleanup(&repo_root);
    }

    #[test]
    fn test_diff_added_node() {
        let (db, repo_root) = setup_db("diff_added");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", Some("def func_a(): pass"));
        create_snapshot(&db, "v1", &repo_root).unwrap();

        // Add a new node
        insert_node(conn, "func_b", "src/b.py", Some("def func_b(): pass"));
        create_snapshot(&db, "v2", &repo_root).unwrap();

        let diff = diff_snapshots(&repo_root, "v1", "v2", false).unwrap();
        assert!(!diff.added_nodes.is_empty());
        assert!(diff.added_nodes.iter().any(|n| n.contains("func_b")));
        assert!(diff.removed_nodes.is_empty());

        cleanup(&repo_root);
    }

    #[test]
    fn test_diff_removed_node() {
        let (db, repo_root) = setup_db("diff_removed");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", Some("def func_a(): pass"));
        insert_node(conn, "func_b", "src/b.py", Some("def func_b(): pass"));
        create_snapshot(&db, "v1", &repo_root).unwrap();

        // Delete func_b manually via direct SQL
        let qname = "src/b.py::func_b";
        let nid = hash_id("src/b.py", qname);
        conn.execute("DELETE FROM nodes WHERE id = ?1", params![nid]).unwrap();
        create_snapshot(&db, "v2", &repo_root).unwrap();

        let diff = diff_snapshots(&repo_root, "v1", "v2", false).unwrap();
        assert!(!diff.removed_nodes.is_empty());
        assert!(diff.removed_nodes.iter().any(|n| n.contains("func_b")));

        cleanup(&repo_root);
    }

    #[test]
    fn test_diff_changed_node() {
        let (db, repo_root) = setup_db("diff_changed");
        let conn = db.connection();
        let id = insert_node(conn, "func_a", "src/a.py", Some("def func_a(): return 1"));
        create_snapshot(&db, "v1", &repo_root).unwrap();

        // Change the body
        conn.execute(
            "UPDATE nodes SET body = ?1 WHERE id = ?2",
            params!["def func_a(): return 2", id],
        )
        .unwrap();
        create_snapshot(&db, "v2", &repo_root).unwrap();

        let diff = diff_snapshots(&repo_root, "v1", "v2", false).unwrap();
        assert!(!diff.changed_nodes.is_empty());
        assert!(diff.changed_nodes.iter().any(|n| n.contains("func_a")));

        cleanup(&repo_root);
    }

    #[test]
    fn test_diff_brief_mode() {
        let (db, repo_root) = setup_db("diff_brief");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", Some("def func_a(): pass"));
        insert_node(conn, "func_b", "src/b.py", Some("def func_b(): pass"));
        create_snapshot(&db, "v1", &repo_root).unwrap();

        // Add a new node
        insert_node(conn, "func_c", "src/c.py", Some("def func_c(): pass"));
        create_snapshot(&db, "v2", &repo_root).unwrap();

        let diff = diff_snapshots(&repo_root, "v1", "v2", true).unwrap();
        assert!(diff.added_nodes.is_empty()); // Brief mode doesn't fill vectors
        assert!(diff.unchanged_nodes > 0);

        cleanup(&repo_root);
    }

    #[test]
    fn test_diff_nonexistent_snapshot() {
        let (db, repo_root) = setup_db("diff_bad");
        let conn = db.connection();
        insert_node(conn, "func_a", "src/a.py", Some("def func_a(): pass"));
        create_snapshot(&db, "v1", &repo_root).unwrap();

        let result = diff_snapshots(&repo_root, "v1", "nonexistent", false);
        assert!(result.is_err());

        cleanup(&repo_root);
    }
}
