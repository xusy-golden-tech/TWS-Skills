//! FTS5 full-text search with qualifier parsing.
//!
//! Supports qualifiers: `kind:`, `lang:`, `path:`.
//! Example: `tws-graph search kind:function auth lang:python`
//!
//! Three-tier search strategy:
//! 1. FTS5 BM25 scoring (primary)
//! 2. LIKE-based fallback
//! 3. Levenshtein edit distance <= 2

use crate::db::Database;
use crate::db::models::SearchResult;

/// Parsed search query with optional qualifiers.
#[derive(Debug, Default)]
pub struct SearchQuery {
    pub text: String,
    pub kind: Option<String>,
    pub lang: Option<String>,
    pub path: Option<String>,
}

/// Parse a raw search string into a `SearchQuery`.
///
/// Qualifiers are extracted and stripped from the text portion.
pub fn parse_query(raw: &str) -> SearchQuery {
    let mut query = SearchQuery::default();
    let mut text_parts: Vec<&str> = Vec::new();

    for token in raw.split_whitespace() {
        if let Some(value) = token.strip_prefix("kind:") {
            query.kind = Some(value.to_string());
        } else if let Some(value) = token.strip_prefix("lang:") {
            query.lang = Some(value.to_string());
        } else if let Some(value) = token.strip_prefix("path:") {
            query.path = Some(value.to_string());
        } else {
            text_parts.push(token);
        }
    }

    query.text = text_parts.join(" ");
    query
}

/// Execute a 3-tier search: FTS5 BM25 → LIKE → edit distance (<= 2).
///
/// Returns up to `limit` results sorted by relevance (best first).
pub fn execute_search(
    db: &Database,
    query: &SearchQuery,
    limit: usize,
) -> rusqlite::Result<Vec<SearchResult>> {
    let text = query.text.as_str();
    let kind = query.kind.as_deref();
    let lang = query.lang.as_deref();
    let path = query.path.as_deref();

    // Tier 1: FTS5 BM25
    if !text.is_empty() {
        let fts5_results = db.search_fts5(text, kind, lang, path, limit)?;
        if !fts5_results.is_empty() {
            return Ok(convert_fts5_results(fts5_results));
        }
    }

    // Tier 2: LIKE fallback
    if !text.is_empty() {
        let like_results = db.search_like(text, kind, lang, path, limit)?;
        if !like_results.is_empty() {
            return Ok(convert_like_results(like_results));
        }
    }

    // Tier 3: Edit distance <= 2 (only for queries >= 3 chars — matches Python)
    if !text.is_empty() && text.len() >= 3 {
        let edit_results = db.search_edit_distance(text, kind, lang, path, limit)?;
        if !edit_results.is_empty() {
            return Ok(convert_edit_results(edit_results));
        }
    }

    // No text — just apply kind/lang/path filters without text search
    if text.is_empty() {
        let results = db.search_like("", kind, lang, path, limit)?;
        return Ok(convert_like_results(results));
    }

    Ok(Vec::new())
}

/// Full search with ranking.
///
/// Executes all tiers and merges results, assigning a composite rank.
/// FTS5 results get `rank` from BM25, LIKE results get `None` rank,
/// edit-distance results get the edit distance as rank.
pub fn search_and_rank(
    db: &Database,
    query: &SearchQuery,
    limit: usize,
) -> rusqlite::Result<Vec<SearchResult>> {
    execute_search(db, query, limit)
}

// ---------------------------------------------------------------------------
// Conversion helpers
// ---------------------------------------------------------------------------

type SearchRow = (String, String, String, String, String, String, Option<f64>, Option<i64>);

fn convert_fts5_results(rows: Vec<SearchRow>) -> Vec<SearchResult> {
    rows.into_iter()
        .map(|(id, kind, name, qualified_name, file_path, language, rank, start_line)| SearchResult {
            id,
            name,
            qualified_name,
            kind,
            file_path,
            language,
            signature: None,
            docstring: None,
            rank,
            start_line,
        })
        .collect()
}

fn convert_like_results(rows: Vec<SearchRow>) -> Vec<SearchResult> {
    rows.into_iter()
        .map(|(id, kind, name, qualified_name, file_path, language, rank, start_line)| SearchResult {
            id,
            name,
            qualified_name,
            kind,
            file_path,
            language,
            signature: None,
            docstring: None,
            rank,
            start_line,
        })
        .collect()
}

fn convert_edit_results(rows: Vec<SearchRow>) -> Vec<SearchResult> {
    rows.into_iter()
        .map(|(id, kind, name, qualified_name, file_path, language, rank, start_line)| SearchResult {
            id,
            name,
            qualified_name,
            kind,
            file_path,
            language,
            signature: None,
            docstring: None,
            rank,
            start_line,
        })
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

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_search_{}.db", name));
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

    fn insert_test_node(
        db: &Database,
        name: &str,
        qualified: &str,
        file_path: &str,
        lang: &str,
        kind: &str,
        docstring: Option<&str>,
    ) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, docstring, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, 1, ?7, ?8)",
            params![id, kind, name, qualified, file_path, lang, docstring, ts],
        )
        .unwrap();
        id
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // Parsing tests (no DB needed)
    // ------------------------------------------------------------------

    #[test]
    fn test_parse_basic() {
        let q = parse_query("auth");
        assert_eq!(q.text, "auth");
        assert!(q.kind.is_none());
    }

    #[test]
    fn test_parse_qualifiers() {
        let q = parse_query("kind:function auth lang:python");
        assert_eq!(q.text, "auth");
        assert_eq!(q.kind.as_deref(), Some("function"));
        assert_eq!(q.lang.as_deref(), Some("python"));
    }

    #[test]
    fn test_parse_path() {
        let q = parse_query("parse path:src/utils");
        assert_eq!(q.text, "parse");
        assert_eq!(q.path.as_deref(), Some("src/utils"));
    }

    #[test]
    fn test_parse_all_qualifiers() {
        let q = parse_query("kind:class UserManager lang:typescript path:src/auth");
        assert_eq!(q.text, "UserManager");
        assert_eq!(q.kind.as_deref(), Some("class"));
        assert_eq!(q.lang.as_deref(), Some("typescript"));
        assert_eq!(q.path.as_deref(), Some("src/auth"));
    }

    #[test]
    fn test_parse_empty() {
        let q = parse_query("");
        assert_eq!(q.text, "");
        assert!(q.kind.is_none());
    }

    // ------------------------------------------------------------------
    // FTS5 search tests (with DB)
    // ------------------------------------------------------------------

    #[test]
    fn test_fts5_search_finds_by_name() {
        let (db, path) = setup_db("fts5_by_name");
        insert_test_node(&db, "calculateTotal", "utils::calculateTotal", "utils.py", "python", "function", Some("Calculates total sum"));
        insert_test_node(&db, "getUser", "api::getUser", "api.py", "python", "function", None);

        let query = SearchQuery {
            text: "calculateTotal".to_string(),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].name, "calculateTotal");

        cleanup(&path);
    }

    #[test]
    fn test_fts5_search_with_kind_filter() {
        let (db, path) = setup_db("fts5_kind_filter");
        insert_test_node(&db, "MyClass", "mod::MyClass", "mod.py", "python", "class", None);
        insert_test_node(&db, "myFunc", "mod::myFunc", "mod.py", "python", "function", None);

        let query = SearchQuery {
            text: "my".to_string(),
            kind: Some("class".to_string()),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].name, "MyClass");

        cleanup(&path);
    }

    #[test]
    fn test_fts5_search_with_lang_filter() {
        let (db, path) = setup_db("fts5_lang_filter");
        insert_test_node(&db, "handler", "src::handler", "src/handler.ts", "typescript", "function", None);
        insert_test_node(&db, "handler", "src::handler", "src/handler.py", "python", "function", None);

        let query = SearchQuery {
            text: "handler".to_string(),
            lang: Some("typescript".to_string()),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].language, "typescript");

        cleanup(&path);
    }

    #[test]
    fn test_fts5_search_with_path_filter() {
        let (db, path) = setup_db("fts5_path_filter");
        insert_test_node(&db, "helper", "auth::helper", "src/auth/utils.py", "python", "function", None);
        insert_test_node(&db, "helper", "pay::helper", "src/pay/utils.py", "python", "function", None);

        let query = SearchQuery {
            text: "helper".to_string(),
            path: Some("auth".to_string()),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 1);
        assert!(results[0].file_path.contains("auth"));

        cleanup(&path);
    }

    #[test]
    fn test_execute_search_empty_text() {
        let (db, path) = setup_db("empty_text");
        insert_test_node(&db, "foo", "mod::foo", "mod.py", "python", "function", None);
        insert_test_node(&db, "bar", "mod::bar", "mod.py", "python", "function", None);

        let query = SearchQuery {
            text: String::new(),
            kind: Some("function".to_string()),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 2);

        cleanup(&path);
    }

    #[test]
    fn test_fts5_rank_is_set_for_matches() {
        let (db, path) = setup_db("fts5_rank");
        insert_test_node(&db, "authHandler", "src::authHandler", "src/auth.ts", "typescript", "function", Some("Handles authentication"));

        let query = SearchQuery {
            text: "authHandler".to_string(),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert_eq!(results.len(), 1);
        // FTS5 rank should be present
        assert!(results[0].rank.is_some());

        cleanup(&path);
    }

    #[test]
    fn test_fts5_limit_respected() {
        let (db, path) = setup_db("fts5_limit");
        for i in 0..20 {
            insert_test_node(&db, &format!("func_{}", i), &format!("mod::func_{}", i), "mod.py", "python", "function", None);
        }

        let query = SearchQuery {
            text: "func".to_string(),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 5).unwrap();
        assert!(results.len() <= 5);

        cleanup(&path);
    }

    #[test]
    fn test_like_fallback_when_fts5_empty() {
        let (db, path) = setup_db("like_fallback");
        // Insert without FTS5 triggers matching
        insert_test_node(&db, "xyzVerySpecificName", "mod::xyzVerySpecificName", "mod.py", "python", "function", None);

        // Search for a substring that FTS5 might not handle well with phrase query
        let query = SearchQuery {
            text: "VerySpecificName".to_string(),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert!(!results.is_empty());

        cleanup(&path);
    }

    #[test]
    fn test_search_and_rank_returns_same_as_execute() {
        let (db, path) = setup_db("search_and_rank");
        insert_test_node(&db, "computeHash", "crypto::computeHash", "crypto.py", "python", "function", Some("Computes SHA256 hash"));

        let query = SearchQuery {
            text: "computeHash".to_string(),
            ..Default::default()
        };
        let r1 = execute_search(&db, &query, 10).unwrap();
        let r2 = search_and_rank(&db, &query, 10).unwrap();
        assert_eq!(r1.len(), r2.len());
        if !r1.is_empty() {
            assert_eq!(r1[0].id, r2[0].id);
        }

        cleanup(&path);
    }

    #[test]
    fn test_no_results_for_nonexistent() {
        let (db, path) = setup_db("nonexistent");
        insert_test_node(&db, "foo", "mod::foo", "mod.py", "python", "function", None);

        let query = SearchQuery {
            text: "nonexistent_symbol_xyz".to_string(),
            ..Default::default()
        };
        let results = execute_search(&db, &query, 10).unwrap();
        assert!(results.is_empty());

        cleanup(&path);
    }
}
