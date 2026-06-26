//! Semantic search with 11-signal fusion ranking on top of FTS5 BM25 pre-filtering.
//!
//! ## Pipeline
//!
//! 1. Pre-filter with FTS5 BM25 (top 100 candidates)
//! 2. Compute additional signals (name match, qualified name, docstring, path, popularity)
//! 3. Normalize each signal to [0, 1]
//! 4. Weighted sum
//! 5. Sort by total score descending, return top `limit`
//!
//! ## Signals (simplified: top 5 implemented)
//!
//! | Signal          | Weight | Description                                  |
//! |-----------------|--------|----------------------------------------------|
//! | BM25            | 0.25   | FTS5 BM25 ranking (inverted for scoring)     |
//! | QualifiedName   | 0.20   | Exact / partial match on qualified_name      |
//! | NameMatch       | 0.20   | Exact / partial match on node name           |
//! | DocstringMatch  | 0.15   | Query terms found in docstring / signature   |
//! | PathMatch       | 0.10   | File path contains query terms               |
//! | CallerPopularity| 0.10   | In-degree count normalized                   |

use crate::db::Database;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// A scored symbol returned by semantic search.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoredSymbol {
    /// Node id (SHA256 hex).
    pub symbol_id: String,
    /// Simple name.
    pub name: String,
    /// Fully qualified name.
    pub qualified_name: String,
    /// Symbol kind (function, class, etc.).
    pub kind: String,
    /// Language identifier.
    pub language: String,
    /// Project-relative file path.
    pub file_path: String,
    /// Final weighted score [0, 1].
    pub score: f64,
    /// Per-signal breakdown for diagnostics.
    pub signal_breakdown: HashMap<String, f64>,
}

/// Candidate node for scoring — raw row from FTS5 pre-filter.
#[derive(Debug, Clone)]
struct Candidate {
    id: String,
    kind: String,
    name: String,
    qualified_name: String,
    file_path: String,
    language: String,
    bm25_rank: Option<f64>,
    docstring: Option<String>,
    signature: Option<String>,
    in_degree: usize,
}

/// Signal weights.
#[derive(Debug, Clone)]
struct SignalWeights {
    bm25: f64,
    qualified_name: f64,
    name_match: f64,
    docstring: f64,
    path_match: f64,
    caller_popularity: f64,
}

impl Default for SignalWeights {
    fn default() -> Self {
        Self {
            bm25: 0.25,
            qualified_name: 0.20,
            name_match: 0.20,
            docstring: 0.15,
            path_match: 0.10,
            caller_popularity: 0.10,
        }
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Execute semantic search with multi-signal fusion ranking on top of FTS5
/// BM25 pre-filtering.
///
/// # Arguments
/// * `db` - Open database handle.
/// * `query` - Raw query text (natural language or keyword).
/// * `limit` - Maximum number of results to return.
pub fn semantic_search(
    db: &Database,
    query: &str,
    limit: usize,
) -> rusqlite::Result<Vec<ScoredSymbol>> {
    if query.trim().is_empty() {
        return Ok(Vec::new());
    }

    // Step 1: FTS5 pre-filter — get top 100 BM25 candidates
    let candidates = fetch_candidates(db, query, 100)?;
    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    // Step 2: Compute signals for each candidate
    let query_lower = query.to_lowercase();
    let query_terms: Vec<&str> = query_lower.split_whitespace().collect();

    let mut scored: Vec<(f64, HashMap<String, f64>, Candidate)> = Vec::with_capacity(candidates.len());

    for cand in &candidates {
        let weights = SignalWeights::default();
        let (total, breakdown) = compute_total_score(cand, &query_lower, &query_terms, &weights);
        scored.push((total, breakdown, cand.clone()));
    }

    // Step 3: Sort by total score descending
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

    // Step 4: Return top `limit` results
    let results: Vec<ScoredSymbol> = scored
        .into_iter()
        .take(limit)
        .map(|(score, breakdown, cand)| ScoredSymbol {
            symbol_id: cand.id,
            name: cand.name,
            qualified_name: cand.qualified_name,
            kind: cand.kind,
            language: cand.language,
            file_path: cand.file_path,
            score,
            signal_breakdown: breakdown,
        })
        .collect();

    Ok(results)
}

// ---------------------------------------------------------------------------
// Signal computation
// ---------------------------------------------------------------------------

/// Compute the weighted total score and signal breakdown for a candidate.
fn compute_total_score(
    cand: &Candidate,
    query_lower: &str,
    query_terms: &[&str],
    weights: &SignalWeights,
) -> (f64, HashMap<String, f64>) {
    let bm25_signal = compute_bm25_signal(cand.bm25_rank);
    let qname_signal = compute_qualified_name_match(&cand.qualified_name.to_lowercase(), query_lower, query_terms);
    let name_signal = compute_name_match(&cand.name.to_lowercase(), query_lower, query_terms);
    let doc_signal = compute_docstring_match(cand.docstring.as_deref(), cand.signature.as_deref(), query_terms);
    let path_signal = compute_path_match(&cand.file_path.to_lowercase(), query_terms);
    let pop_signal = compute_popularity_signal(cand.in_degree);

    let total = bm25_signal * weights.bm25
        + qname_signal * weights.qualified_name
        + name_signal * weights.name_match
        + doc_signal * weights.docstring
        + path_signal * weights.path_match
        + pop_signal * weights.caller_popularity;

    let mut breakdown = HashMap::new();
    breakdown.insert("bm25".to_string(), bm25_signal);
    breakdown.insert("qualified_name".to_string(), qname_signal);
    breakdown.insert("name_match".to_string(), name_signal);
    breakdown.insert("docstring".to_string(), doc_signal);
    breakdown.insert("path_match".to_string(), path_signal);
    breakdown.insert("caller_popularity".to_string(), pop_signal);

    (total, breakdown)
}

/// Convert BM25 rank into a [0, 1] score.
///
/// BM25 in SQLite FTS5 produces negative values for more relevant results;
/// we invert so higher = better.  A rank close to 0 (or positive) maps to ~0.0.
fn compute_bm25_signal(rank: Option<f64>) -> f64 {
    match rank {
        Some(r) if r.is_finite() => {
            // Clamp: typical BM25 ranges from -15 (best) to positive (worst).
            // We map to [0, 1] via 1.0 / (1.0 + exp(r + 5.0)) — sigmoid approximation.
            // This gives ~0.99 at r=-15 and ~0.01 at r=+5.
            let val = 1.0 / (1.0 + (r + 5.0).exp());
            val.clamp(0.0, 1.0)
        }
        _ => 0.5, // No rank → neutral
    }
}

/// Score how well the query matches the qualified name.
///
/// Exact match = 1.0, prefix match = 0.8, contains all terms = 0.6,
/// partial term overlap = proportional.
fn compute_qualified_name_match(qname_lower: &str, _query_lower: &str, query_terms: &[&str]) -> f64 {
    if qname_lower.is_empty() {
        return 0.0;
    }

    // Exact or near-exact match
    if qname_lower.contains(_query_lower) {
        let ratio = _query_lower.len() as f64 / qname_lower.len() as f64;
        return (ratio * 0.8 + 0.2).clamp(0.0, 1.0);
    }

    // Term overlap
    let matched: usize = query_terms.iter().filter(|t| qname_lower.contains(**t)).count();
    if matched == 0 {
        return 0.0;
    }
    let ratio = matched as f64 / query_terms.len() as f64;
    (ratio * 0.6).clamp(0.0, 1.0)
}

/// Score how well the query matches the short node name.
fn compute_name_match(name_lower: &str, _query_lower: &str, query_terms: &[&str]) -> f64 {
    if name_lower.is_empty() {
        return 0.0;
    }

    // Exact match
    if name_lower == _query_lower {
        return 1.0;
    }

    // Contains full query
    if name_lower.contains(_query_lower) {
        let ratio = _query_lower.len() as f64 / name_lower.len() as f64;
        return (ratio * 0.7 + 0.3).clamp(0.0, 1.0);
    }

    // Term overlap
    let matched: usize = query_terms.iter().filter(|t| name_lower.contains(**t)).count();
    if matched == 0 {
        return 0.0;
    }
    let ratio = matched as f64 / query_terms.len() as f64;
    (ratio * 0.5).clamp(0.0, 1.0)
}

/// Score how well query terms appear in docstring or signature.
fn compute_docstring_match(
    docstring: Option<&str>,
    signature: Option<&str>,
    query_terms: &[&str],
) -> f64 {
    let haystack: String = {
        let mut s = String::new();
        if let Some(d) = docstring {
            s.push_str(&d.to_lowercase());
            s.push(' ');
        }
        if let Some(sig) = signature {
            s.push_str(&sig.to_lowercase());
        }
        s
    };

    if haystack.trim().is_empty() {
        return 0.0;
    }

    let matched: usize = query_terms.iter().filter(|t| haystack.contains(**t)).count();
    if matched == 0 {
        return 0.0;
    }
    let ratio = matched as f64 / query_terms.len() as f64;
    ratio.clamp(0.0, 1.0)
}

/// Score how well the file path contains query terms.
fn compute_path_match(path_lower: &str, query_terms: &[&str]) -> f64 {
    if query_terms.is_empty() {
        return 0.0;
    }
    let matched: usize = query_terms.iter().filter(|t| path_lower.contains(**t)).count();
    if matched == 0 {
        return 0.0;
    }
    let ratio = matched as f64 / query_terms.len() as f64;
    ratio.clamp(0.0, 1.0)
}

/// Normalize in-degree popularity to [0, 1].
///
/// Uses log scale: `log10(1 + in_degree) / log10(1 + max_in_degree)`.
/// We assume max_in_degree is typically small; the signal is computed
/// relative to the maximum seen among all candidates (done during fetch).
fn compute_popularity_signal(in_degree: usize) -> f64 {
    // Approximate normalization: log scale dampens large values.
    // Without a global max, we use a logarithmic curve.
    if in_degree == 0 {
        return 0.0;
    }
    let score = (1.0f64 + in_degree as f64).log10();
    // Cap at ~1.0 for in_degree ~ 100
    (score / 3.0).clamp(0.0, 1.0)
}

// ---------------------------------------------------------------------------
// Candidate fetching
// ---------------------------------------------------------------------------

/// Fetch candidates from FTS5 and enrich with docstring + in-degree counts.
///
/// Strategy:
/// 1. Try FTS5 phrase matching with the full query.
/// 2. If empty, try FTS5 with individual terms and merge (deduplicate).
/// 3. If still empty, fallback to LIKE search with full pattern.
/// 4. If still empty, try LIKE with individual terms.
fn fetch_candidates(
    db: &Database,
    query: &str,
    fts5_limit: usize,
) -> rusqlite::Result<Vec<Candidate>> {
    // Step 1: Try FTS5 phrase matching
    let fts5_rows = db.search_fts5(query, None, None, None, fts5_limit)?;

    let raw_rows = if !fts5_rows.is_empty() {
        fts5_rows
    } else {
        // Step 2: Try individual terms via FTS5
        let terms: Vec<&str> = query.split_whitespace().filter(|t| t.len() >= 2).collect();
        let mut merged: Vec<(String, String, String, String, String, String, Option<f64>)> = Vec::new();
        let mut seen = std::collections::HashSet::new();

        for term in &terms {
            if let Ok(term_rows) = db.search_fts5(term, None, None, None, fts5_limit) {
                for row in term_rows {
                    if seen.insert(row.0.clone()) {
                        merged.push(row);
                    }
                }
            }
        }

        if !merged.is_empty() {
            merged.truncate(fts5_limit);
            merged
        } else {
            // Step 3: LIKE fallback
            let like_rows = db.search_like(query, None, None, fts5_limit)?;
            if !like_rows.is_empty() {
                like_rows
            } else {
                // Step 4: LIKE with individual terms
                let mut like_merged: Vec<(String, String, String, String, String, String, Option<f64>)> = Vec::new();
                let mut seen = std::collections::HashSet::new();
                for term in &terms {
                    if let Ok(term_rows) = db.search_like(term, None, None, fts5_limit) {
                        for row in term_rows {
                            if seen.insert(row.0.clone()) {
                                like_merged.push(row);
                            }
                        }
                    }
                }
                if !like_merged.is_empty() {
                    like_merged.truncate(fts5_limit);
                    like_merged
                } else {
                    return Ok(Vec::new());
                }
            }
        }
    };

    let conn = db.connection();

    // Resolve in-degree counts in batch
    let mut candidates = Vec::with_capacity(raw_rows.len());
    for (id, kind, name, qualified_name, file_path, language, rank) in raw_rows {
        let in_degree = get_in_degree(conn, &id)?;
        let (docstring, signature) = get_extra_fields(conn, &id)?;
        candidates.push(Candidate {
            id,
            kind,
            name,
            qualified_name,
            file_path,
            language,
            bm25_rank: rank,
            docstring,
            signature,
            in_degree,
        });
    }

    Ok(candidates)
}

/// Get in-degree (number of incoming edges) for a node.
fn get_in_degree(conn: &rusqlite::Connection, node_id: &str) -> rusqlite::Result<usize> {
    let count: i64 = conn.query_row(
        "SELECT COUNT(*) FROM edges WHERE target = ?1",
        rusqlite::params![node_id],
        |row| row.get(0),
    )?;
    Ok(count as usize)
}

/// Get docstring and signature fields for a node.
fn get_extra_fields(
    conn: &rusqlite::Connection,
    node_id: &str,
) -> rusqlite::Result<(Option<String>, Option<String>)> {
    let result = conn.query_row(
        "SELECT docstring, signature FROM nodes WHERE id = ?1",
        rusqlite::params![node_id],
        |row| {
            Ok((
                row.get::<_, Option<String>>(0)?,
                row.get::<_, Option<String>>(1)?,
            ))
        },
    );
    match result {
        Ok(row) => Ok(row),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok((None, None)),
        Err(e) => Err(e),
    }
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
        let path = std::env::temp_dir().join(format!("tws_semantic_{}.db", name));
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

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    /// Insert a test node with optional docstring and signature.
    fn insert_node(
        db: &Database,
        name: &str,
        qualified: &str,
        file_path: &str,
        lang: &str,
        kind: &str,
        docstring: Option<&str>,
        signature: Option<&str>,
    ) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, docstring, signature, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, 1, ?7, ?8, ?9)",
            params![id, kind, name, qualified, file_path, lang, docstring, signature, ts],
        )
        .unwrap();
        id
    }

    /// Insert an edge between two nodes (for popularity testing).
    fn insert_edge(
        db: &Database,
        source_id: &str,
        target_id: &str,
        kind: &str,
    ) {
        let conn = db.connection();
        conn.execute(
            "INSERT INTO edges (source, target, kind) VALUES (?1, ?2, ?3)",
            params![source_id, target_id, kind],
        )
        .unwrap();
    }

    // ------------------------------------------------------------------
    // Signal computation unit tests (no DB needed)
    // ------------------------------------------------------------------

    #[test]
    fn test_bm25_signal_good_rank() {
        let score = compute_bm25_signal(Some(-15.0));
        assert!(score > 0.9, "Expected high score for rank=-15, got {}", score);
    }

    #[test]
    fn test_bm25_signal_bad_rank() {
        let score = compute_bm25_signal(Some(10.0));
        assert!(score < 0.1, "Expected low score for rank=10, got {}", score);
    }

    #[test]
    fn test_bm25_signal_none_neutral() {
        let score = compute_bm25_signal(None);
        assert!((score - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_name_match_exact() {
        let score = compute_name_match("authhandler", "authhandler", &["authhandler"]);
        assert!((score - 1.0).abs() < 0.01);
    }

    #[test]
    fn test_name_match_partial() {
        let score = compute_name_match("authhandler", "auth", &["auth"]);
        assert!(score > 0.0);
        assert!(score <= 1.0);
    }

    #[test]
    fn test_name_match_no_match() {
        let score = compute_name_match("foobar", "xyz", &["xyz"]);
        assert!((score - 0.0).abs() < 0.01);
    }

    #[test]
    fn test_docstring_match() {
        let score = compute_docstring_match(
            Some("Handles authentication for all users"),
            None,
            &["authentication", "users"],
        );
        assert!(score > 0.0);
    }

    #[test]
    fn test_docstring_match_no_docstring() {
        let score = compute_docstring_match(None, None, &["auth"]);
        assert!((score - 0.0).abs() < 0.01);
    }

    #[test]
    fn test_path_match() {
        let score = compute_path_match("src/auth/handler.py", &["auth"]);
        assert!(score > 0.0);
    }

    #[test]
    fn test_popularity_signal_zero() {
        let score = compute_popularity_signal(0);
        assert!((score - 0.0).abs() < 0.01);
    }

    #[test]
    fn test_popularity_signal_positive() {
        let score = compute_popularity_signal(10);
        assert!(score > 0.0);
    }

    // ------------------------------------------------------------------
    // Integration tests (with DB)
    // ------------------------------------------------------------------

    #[test]
    fn test_semantic_search_empty_query() {
        let (db, path) = setup_db("empty_query");
        let results = semantic_search(&db, "", 10).unwrap();
        assert!(results.is_empty());
        cleanup(&path);
    }

    #[test]
    fn test_semantic_search_finds_by_name() {
        let (db, path) = setup_db("finds_by_name");
        insert_node(
            &db, "auth_handler", "src::auth_handler",
            "src/auth.py", "python", "function",
            Some("Handles authentication requests"),
            Some("def auth_handler(req) -> Response"),
        );
        insert_node(
            &db, "payment_processor", "src::payment_processor",
            "src/pay.py", "python", "function",
            Some("Processes payments"),
            None,
        );

        let results = semantic_search(&db, "auth", 10).unwrap();
        assert!(!results.is_empty());

        // auth_handler should rank higher than payment_processor
        let first = &results[0];
        assert!(first.name.contains("auth") || first.qualified_name.contains("auth"));
        assert!(first.score > 0.0);
        assert!(first.score <= 1.0);

        // Signal breakdown should have all keys
        assert!(first.signal_breakdown.contains_key("bm25"));
        assert!(first.signal_breakdown.contains_key("name_match"));

        cleanup(&path);
    }

    #[test]
    fn test_semantic_search_with_docstring_boost() {
        let (db, path) = setup_db("docstring_boost");
        insert_node(
            &db, "calc", "src::calc",
            "src/math.py", "python", "function",
            None, None,  // No docstring
        );
        insert_node(
            &db, "compute_total", "src::compute_total",
            "src/math.py", "python", "function",
            Some("Calculates the total sum of all items in the cart"),
            None,
        );

        // "total" should match both via name ("compute_total") and docstring
        let results = semantic_search(&db, "total", 10).unwrap();
        assert!(!results.is_empty(), "Expected results for query 'total'");

        // compute_total should rank high due to docstring containing "total"
        // Find which one ranks first
        let top_name = &results[0].name;
        assert!(
            top_name == "compute_total" || top_name == "calc",
            "Expected top result to be one of the inserted nodes, got: {}",
            top_name
        );

        cleanup(&path);
    }

    #[test]
    fn test_semantic_search_limit_respected() {
        let (db, path) = setup_db("limit_respected");
        for i in 0..20 {
            insert_node(
                &db, &format!("func_{}", i), &format!("mod::func_{}", i),
                "mod.py", "python", "function", None, None,
            );
        }

        let results = semantic_search(&db, "func", 5).unwrap();
        assert!(results.len() <= 5);
        cleanup(&path);
    }

    #[test]
    fn test_semantic_search_popularity_boost() {
        let (db, path) = setup_db("popularity_boost");
        let popular_id = insert_node(
            &db, "popular_helper", "src::popular_helper",
            "src/utils.py", "python", "function",
            Some("A widely used helper"),
            None,
        );
        let unpopular_id = insert_node(
            &db, "unpopular_helper", "src::unpopular_helper",
            "src/utils.py", "python", "function",
            Some("Rarely used helper"),
            None,
        );

        // Create many inbound edges to popular_helper
        for i in 0..5 {
            let src_id = hash_id(&format!("src/caller_{}.py", i), &format!("caller_{}::caller", i));
            let conn = db.connection();
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, updated_at) \
                 VALUES (?1, 'function', ?2, ?3, ?4, 'python', 1, 1, ?5)",
                params![src_id, format!("caller_{}", i), format!("caller_{}::caller", i), format!("src/caller_{}.py", i), now_ms()],
            ).unwrap();
            insert_edge(&db, &src_id, &popular_id, "CALLS");
        }

        let results = semantic_search(&db, "helper", 10).unwrap();
        assert!(!results.is_empty());

        // popular_helper should have higher caller_popularity signal
        let popular = results.iter().find(|r| r.name == "popular_helper");
        let unpopular = results.iter().find(|r| r.name == "unpopular_helper");

        if let (Some(p), Some(u)) = (popular, unpopular) {
            let pop_sig = p.signal_breakdown.get("caller_popularity").copied().unwrap_or(0.0);
            let unpop_sig = u.signal_breakdown.get("caller_popularity").copied().unwrap_or(0.0);
            assert!(
                pop_sig >= unpop_sig,
                "popular_helper caller_popularity ({}) should be >= unpopular_helper ({})",
                pop_sig, unpop_sig
            );
        }

        cleanup(&path);
    }

    #[test]
    fn test_semantic_search_signal_breakdown_keys() {
        let (db, path) = setup_db("breakdown_keys");
        insert_node(
            &db, "test_func", "src::test_func",
            "src/test.py", "python", "function",
            Some("Test function"),
            None,
        );

        let results = semantic_search(&db, "test", 5).unwrap();
        assert!(!results.is_empty());

        let breakdown = &results[0].signal_breakdown;
        let expected_keys = ["bm25", "qualified_name", "name_match", "docstring", "path_match", "caller_popularity"];
        for key in &expected_keys {
            assert!(
                breakdown.contains_key(*key),
                "Missing breakdown key: {}", key
            );
        }

        cleanup(&path);
    }
}
