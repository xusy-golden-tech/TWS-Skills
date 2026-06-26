//! Git diff impact analysis — determines which symbols are affected by a diff.
//!
//! Parses a unified diff, identifies changed files and line ranges, maps them to
//! code-graph nodes, and computes the impact radius for each affected node.

use crate::db::Database;
use std::collections::{HashMap, HashSet, VecDeque};

/// Analyze a git diff to find affected symbols and their impact radius.
///
/// Returns `(affected_node_id, name, impact_radius)` tuples sorted by impact
/// radius descending.
pub fn analyze_git_diff(
    db: &Database,
    diff_output: &str,
) -> Vec<(String, String, usize)> {
    let conn = db.connection();

    // Parse the diff to get changed file paths and line ranges
    let changed_regions = parse_diff(diff_output);
    if changed_regions.is_empty() {
        return Vec::new();
    }

    // Find nodes in the changed files/lines
    let mut affected_nodes: Vec<(String, String)> = Vec::new();

    let mut stmt = conn
        .prepare("SELECT id, name, file_path, start_line, end_line FROM nodes")
        .expect("failed to prepare nodes query");
    let all_nodes: Vec<(String, String, String, i64, i64)> = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(4)?,
            ))
        })
        .expect("failed to query nodes")
        .filter_map(|r| r.ok())
        .collect();

    for (id, name, file_path, start_line, end_line) in &all_nodes {
        // Check if node's file is in the changed files
        let normalized_fp = file_path.replace('\\', "/");
        for region in &changed_regions {
            let changed_file = &region.file;
            let line_ranges = &region.ranges;
            if normalized_fp.contains(changed_file.as_str())
                || changed_file.contains(normalized_fp.as_str())
                || normalized_fp.ends_with(changed_file)
                || changed_file.ends_with(&normalized_fp)
            {
                // Check if node overlaps with changed line ranges
                for &(range_start, range_end) in line_ranges {
                    if node_overlaps_range(*start_line, *end_line, range_start, range_end) {
                        affected_nodes.push((id.clone(), name.clone()));
                        break;
                    }
                }
                break;
            }
        }
    }

    if affected_nodes.is_empty() {
        return Vec::new();
    }

    // Build adjacency for BFS impact radius computation
    let mut adj: HashMap<String, Vec<String>> = HashMap::new();
    for (id, _, _, _, _) in &all_nodes {
        adj.entry(id.clone()).or_default();
    }

    let mut edge_stmt = conn
        .prepare("SELECT source, target FROM edges WHERE kind = 'CALLS'")
        .expect("failed to prepare edges query");
    let edges: Vec<(String, String)> = edge_stmt
        .query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))
        .expect("failed to query edges")
        .filter_map(|r| r.ok())
        .collect();

    for (src, tgt) in &edges {
        adj.entry(src.clone()).or_default().push(tgt.clone());
    }

    // Compute impact radius for each affected node via BFS
    let mut results: Vec<(String, String, usize)> = Vec::new();
    for (node_id, node_name) in &affected_nodes {
        let radius = bfs_impact_radius(&adj, node_id);
        results.push((node_id.clone(), node_name.clone(), radius));
    }

    results.sort_by(|a, b| b.2.cmp(&a.2));
    results
}

/// Parsed changed region: file path and list of (start_line, end_line) ranges.
struct ChangeRegion {
    file: String,
    ranges: Vec<(i64, i64)>,
}

/// Parse a unified diff output and extract changed files with line ranges.
fn parse_diff(diff_output: &str) -> Vec<ChangeRegion> {
    let mut regions: Vec<ChangeRegion> = Vec::new();
    let mut current_file: Option<String> = None;
    let mut current_ranges: Vec<(i64, i64)> = Vec::new();

    for line in diff_output.lines() {
        // Detect file header: "+++ b/path/to/file"
        if line.starts_with("+++ b/") {
            // Save previous if any
            if let Some(file) = current_file.take() {
                if !current_ranges.is_empty() {
                    regions.push(ChangeRegion {
                        file,
                        ranges: current_ranges.clone(),
                    });
                }
                current_ranges.clear();
            }
            let path = line[6..].trim().to_string();
            current_file = Some(path);
        }
        // Detect hunk header: "@@ -old_start,old_count +new_start,new_count @@"
        else if line.starts_with("@@") && current_file.is_some() {
            if let Some(range) = parse_hunk_range(line) {
                current_ranges.push(range);
            }
        }
    }

    // Save final region
    if let Some(file) = current_file {
        if !current_ranges.is_empty() {
            regions.push(ChangeRegion {
                file,
                ranges: current_ranges,
            });
        }
    }

    // Merge overlapping/adjacent ranges per file
    for region in &mut regions {
        region.ranges.sort_by_key(|r| r.0);
        let mut merged: Vec<(i64, i64)> = Vec::new();
        for &(start, end) in &region.ranges {
            if let Some(last) = merged.last_mut() {
                if start <= last.1 + 1 {
                    last.1 = last.1.max(end);
                    continue;
                }
            }
            merged.push((start, end));
        }
        region.ranges = merged;
    }

    regions
}

/// Parse a hunk header like "@@ -10,6 +10,8 @@" to get the new line range.
fn parse_hunk_range(line: &str) -> Option<(i64, i64)> {
    // Find the "+start,count" part
    let plus_idx = line.find('+')?;
    let after_plus = &line[plus_idx + 1..];
    let numbers: Vec<&str> = after_plus
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .collect();

    if numbers.is_empty() {
        return None;
    }

    let start: i64 = numbers[0].parse().ok()?;
    let count: i64 = if numbers.len() >= 2 {
        numbers[1].parse().ok()?
    } else {
        1 // @@ -10 +10 @@ means 1 line
    };

    if count == 0 {
        return None;
    }

    let end = start + count - 1;
    Some((start, end))
}

/// Check if a node's line range overlaps with a changed line range.
fn node_overlaps_range(node_start: i64, node_end: i64, change_start: i64, change_end: i64) -> bool {
    node_start <= change_end && node_end >= change_start
}

/// Compute impact radius via BFS from a given node.
///
/// Returns the maximum distance reachable from the node.
fn bfs_impact_radius(adj: &HashMap<String, Vec<String>>, start: &str) -> usize {
    let mut visited: HashSet<String> = HashSet::new();
    let mut queue: VecDeque<(String, usize)> = VecDeque::new();
    queue.push_back((start.to_string(), 0));
    visited.insert(start.to_string());

    let mut max_depth = 0usize;

    while let Some((node, depth)) = queue.pop_front() {
        max_depth = depth;
        if let Some(neighbors) = adj.get(&node) {
            for neighbor in neighbors {
                if !visited.contains(neighbor) {
                    visited.insert(neighbor.clone());
                    queue.push_back((neighbor.clone(), depth + 1));
                }
            }
        }
    }

    max_depth
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
        let path = std::env::temp_dir().join(format!("tws_gitdiff_test_{}.db", name));
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

    fn insert_node_with_lines(
        conn: &rusqlite::Connection,
        name: &str,
        file: &str,
        start: i64,
        end: i64,
    ) -> String {
        let qname = format!("{}::{}", file, name);
        let nid = hash_id(file, &qname);
        let ts = now_ms();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![nid, name, qname, file, "python", start, end, ts],
        )
        .unwrap();
        nid
    }

    fn insert_node(conn: &rusqlite::Connection, name: &str, file: &str) -> String {
        insert_node_with_lines(conn, name, file, 10, 20)
    }

    fn insert_edge(conn: &rusqlite::Connection, src: &str, tgt: &str) {
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, 'CALLS')",
            params![src, tgt],
        )
        .unwrap();
    }

    #[test]
    fn test_empty_diff() {
        let (db, path) = setup_db("empty_diff");
        let conn = db.connection();

        insert_node(conn, "main", "src/main.py");

        let result = analyze_git_diff(&db, "");
        assert!(result.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_diff_finds_affected_node() {
        let (db, path) = setup_db("affected_node");
        let conn = db.connection();

        // Node at lines 10-20 matches diff range 10-25
        let func = insert_node_with_lines(conn, "hello", "src/main.py", 10, 20);

        let diff = r"diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
@@ -10,6 +10,8 @@
+print('hello')
+print('world')
";

        let result = analyze_git_diff(&db, diff);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, func);
        assert_eq!(result[0].1, "hello");

        cleanup(&path);
    }

    #[test]
    fn test_diff_node_not_affected_outside_range() {
        let (db, path) = setup_db("not_affected");
        let conn = db.connection();

        // Node at lines 50-60, diff changes lines 10-15 — no overlap
        insert_node_with_lines(conn, "other", "src/main.py", 50, 60);

        let diff = r"diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -10,3 +10,5 @@
+added line
";

        let result = analyze_git_diff(&db, diff);
        assert!(result.is_empty(), "Node outside changed range should not be affected");

        cleanup(&path);
    }

    #[test]
    fn test_diff_with_call_chain_impact() {
        let (db, path) = setup_db("call_chain");
        let conn = db.connection();

        // a calls b calls c
        let a = insert_node_with_lines(conn, "a", "src/lib.py", 10, 20);
        let b = insert_node(conn, "b", "src/dep.py");
        let c = insert_node(conn, "c", "src/util.py");
        insert_edge(conn, &a, &b);
        insert_edge(conn, &b, &c);

        // Diff changes file containing a
        let diff = r"diff --git a/src/lib.py b/src/lib.py
--- a/src/lib.py
+++ b/src/lib.py
@@ -10,6 +10,8 @@
+changed line
";

        let result = analyze_git_diff(&db, diff);
        assert_eq!(result.len(), 1);
        // Impact radius: a -> b -> c = 2
        assert_eq!(result[0].1, "a");
        assert_eq!(result[0].2, 2, "Impact radius should be 2 (a -> b -> c)");

        cleanup(&path);
    }

    #[test]
    fn test_multiple_hunks_in_file() {
        let (db, path) = setup_db("multi_hunk");
        let conn = db.connection();

        // Node covers lines 10-50, diff has changes at 10 and 45
        let func = insert_node_with_lines(conn, "big_func", "src/big.py", 10, 50);

        let diff = r"diff --git a/src/big.py b/src/big.py
--- a/src/big.py
+++ b/src/big.py
@@ -10,4 +10,6 @@
+line a
+line b
@@ -45,2 +45,4 @@
+line c
";

        let result = analyze_git_diff(&db, diff);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].1, "big_func");

        cleanup(&path);
    }
}
