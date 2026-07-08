//! Three-level URL matching pipeline (exact → template → fuzzy).
//!
//! Takes HTTP calls (from frontend files) and HTTP routes (from backend
//! files), both with normalized URLs, and produces `Match` entries for every
//! call-route pair whose URL and HTTP method are compatible.
//!
//! # Pipeline
//!
//! 1. **Exact match** — same method AND identical URL string → confidence 1.0
//! 2. **Template match** — same method, segment-by-segment comparison where
//!    `{param}` route segments match *any* call segment → confidence 1.0
//! 3. **Fuzzy match** — same method, 2-gram Jaccard similarity > 0.7 →
//!    confidence = Jaccard score
//!
//! # One-to-many matching
//!
//! A single HTTP call may match multiple routes (e.g. `/users/123` matches
//! both `/users/{id}` and `/users/{name}`). All matches are preserved and
//! sorted by confidence descending.

use crate::db::models::{HttpCallRecord, HttpRouteRecord};
use std::collections::HashSet;

// ============================================================================
// Public types
// ============================================================================

/// The type of URL match between an HTTP call and a route definition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum MatchType {
    /// Exact string equality after normalization. Highest confidence.
    Exact,
    /// Template (segment-by-segment with `{param}` wildcards). High confidence.
    Template,
    /// 2-gram Jaccard similarity heuristic. Lower confidence.
    Fuzzy,
}

/// A single call → route match produced by the matching pipeline.
#[derive(Debug, Clone)]
pub struct Match {
    /// The `HttpCallRecord.id` of the matched caller.
    pub call_id: i64,
    /// The `HttpRouteRecord.id` of the matched route.
    pub route_id: i64,
    /// The URL from the call side (normalized).
    pub url: String,
    /// HTTP method (e.g. "GET", "POST").
    pub http_method: String,
    /// How the match was determined.
    pub match_type: MatchType,
    /// Confidence score: 0.0 – 1.0.
    pub confidence: f64,
}

// ============================================================================
// Public API
// ============================================================================

/// Run the full three-level matching pipeline.
///
/// # Returns
/// A vector of `Match` entries sorted by confidence descending (ties broken
/// by match type priority: Exact > Template > Fuzzy).
pub fn match_all(
    calls: &[HttpCallRecord],
    routes: &[HttpRouteRecord],
) -> Vec<Match> {
    let mut results: Vec<Match> = Vec::new();

    // Track which (call, route) pairs have already been matched so that
    // exact/template matches block fuzzy duplicates.
    let mut matched_pairs: HashSet<(i64, i64)> = HashSet::new();

    // ── Step 1: Exact match ──
    for call in calls {
        for route in routes {
            if call.http_method != route.http_method {
                continue;
            }
            if call.url == route.url_pattern {
                // Guard against duplicate exact matches (shouldn't happen, but safe)
                let pair = (call.id.unwrap_or(-1), route.id.unwrap_or(-1));
                if matched_pairs.insert(pair) {
                    results.push(Match {
                        call_id: pair.0,
                        route_id: pair.1,
                        url: call.url.clone(),
                        http_method: call.http_method.clone(),
                        match_type: MatchType::Exact,
                        confidence: 1.0,
                    });
                }
            }
        }
    }

    // ── Step 2: Template match ──
    for call in calls {
        for route in routes {
            let pair = (call.id.unwrap_or(-1), route.id.unwrap_or(-1));
            if matched_pairs.contains(&pair) {
                continue;
            }
            if call.http_method != route.http_method {
                continue;
            }
            if template_match(&call.url, &route.url_pattern) {
                matched_pairs.insert(pair);
                results.push(Match {
                    call_id: pair.0,
                    route_id: pair.1,
                    url: call.url.clone(),
                    http_method: call.http_method.clone(),
                    match_type: MatchType::Template,
                    confidence: 1.0,
                });
            }
        }
    }

    // ── Step 3: Fuzzy match (2-gram Jaccard) ──
    for call in calls {
        for route in routes {
            let pair = (call.id.unwrap_or(-1), route.id.unwrap_or(-1));
            if matched_pairs.contains(&pair) {
                continue;
            }
            if call.http_method != route.http_method {
                continue;
            }
            let jac = jaccard_similarity(&call.url, &route.url_pattern);
            if jac > 0.7 {
                // No need to track matched_pairs — fuzzy can overlap across
                // different thresholds (we keep all like one-to-many).
                results.push(Match {
                    call_id: pair.0,
                    route_id: pair.1,
                    url: call.url.clone(),
                    http_method: call.http_method.clone(),
                    match_type: MatchType::Fuzzy,
                    confidence: jac,
                });
            }
        }
    }

    // Sort: confidence descending, then match type priority
    results.sort_by(|a, b| {
        b.confidence
            .partial_cmp(&a.confidence)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.match_type.cmp(&b.match_type))
    });

    results
}

// ============================================================================
// Template (segment-by-segment) matching
// ============================================================================

/// Returns `true` when the call URL matches the route pattern segment-by-segment.
///
/// A route segment in `{param}` form matches *any* call segment.
/// A literal route segment must equal the corresponding call segment exactly.
/// The number of segments in both URLs must be equal.
fn template_match(call_url: &str, route_pattern: &str) -> bool {
    let call_segs = split_segments(call_url);
    let route_segs = split_segments(route_pattern);

    if call_segs.len() != route_segs.len() {
        return false;
    }
    // Handle 0 segments: e.g. "/" matches "/"
    if call_segs.is_empty() {
        return true;
    }

    for (call_seg, route_seg) in call_segs.iter().zip(route_segs.iter()) {
        if is_param_segment(route_seg) {
            // {param} matches anything — continue
            continue;
        }
        if call_seg != route_seg {
            return false;
        }
    }

    true
}

// ============================================================================
// 2-gram Jaccard fuzzy matching
// ============================================================================

/// Compute Jaccard similarity between two URLs using 2-gram (bigram) character sets.
///
/// Jaccard(A, B) = |A ∩ B| / |A ∪ B|
fn jaccard_similarity(a: &str, b: &str) -> f64 {
    let bigrams_a = character_bigrams(a);
    let bigrams_b = character_bigrams(b);

    if bigrams_a.is_empty() && bigrams_b.is_empty() {
        return 1.0; // both empty strings → identical
    }
    if bigrams_a.is_empty() || bigrams_b.is_empty() {
        return 0.0;
    }

    let intersection = bigrams_a.intersection(&bigrams_b).count();
    let union = bigrams_a.union(&bigrams_b).count();

    intersection as f64 / union as f64
}

/// Extract character 2-grams from a URL string.
///
/// Example: `"/api"` → `{"/a", "ap", "pi"}`
fn character_bigrams(s: &str) -> HashSet<(char, char)> {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() < 2 {
        return HashSet::new();
    }
    chars.windows(2).map(|w| (w[0], w[1])).collect()
}

// ============================================================================
// Segment helpers
// ============================================================================

/// Split a URL path into segments by "/", filtering out empty strings.
///
/// # Examples
/// ```
/// assert_eq!(split_segments("/api/users/123"), vec!["api", "users", "123"]);
/// assert_eq!(split_segments("/"), Vec::<&str>::new());
/// assert_eq!(split_segments(""), Vec::<&str>::new());
/// ```
fn split_segments(url: &str) -> Vec<&str> {
    url.split('/')
        .filter(|s| !s.is_empty())
        .collect()
}

/// Returns `true` when a route segment is a parameter placeholder `{...}`.
///
/// # Examples
/// ```
/// assert!(is_param_segment("{user_id}"));
/// assert!(is_param_segment("{id}"));
/// assert!(!is_param_segment("users"));
/// ```
fn is_param_segment(seg: &str) -> bool {
    seg.starts_with('{') && seg.ends_with('}')
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // ── Helper to create test records ──

    fn make_call(id: i64, url: &str, method: &str) -> HttpCallRecord {
        HttpCallRecord {
            id: Some(id),
            url: url.to_string(),
            http_method: method.to_string(),
            func_node_id: 100 + id,
            url_is_template: false,
            file_path: "frontend.ts".to_string(),
            line: 1,
            column: 1,
            source_lang: "typescript".to_string(),
            raw_snippet: None,
        }
    }

    fn make_route(id: i64, url_pattern: &str, method: &str) -> HttpRouteRecord {
        HttpRouteRecord {
            id: Some(id),
            url_pattern: url_pattern.to_string(),
            url_pattern_raw: url_pattern.to_string(),
            http_method: method.to_string(),
            handler_node_id: 200 + id,
            file_path: "backend.py".to_string(),
            line: 1,
            column: 1,
            source_lang: "python".to_string(),
            source_framework: Some("fastapi".to_string()),
            raw_snippet: None,
        }
    }

    // ── Exact match ──

    #[test]
    fn test_exact_match_identical_urls() {
        let calls = vec![make_call(1, "/api/users", "GET")];
        let routes = vec![make_route(1, "/api/users", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Exact);
        assert!((matches[0].confidence - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_exact_match_method_mismatch() {
        let calls = vec![make_call(1, "/api/users", "GET")];
        let routes = vec![make_route(1, "/api/users", "POST")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0);
    }

    #[test]
    fn test_exact_match_url_mismatch() {
        let calls = vec![make_call(1, "/api/users", "GET")];
        let routes = vec![make_route(1, "/api/items", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0);
    }

    // ── Template match ──

    #[test]
    fn test_template_match_single_param() {
        let calls = vec![make_call(1, "/api/users/123", "GET")];
        let routes = vec![make_route(1, "/api/users/{id}", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Template);
        assert!((matches[0].confidence - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_template_match_multi_params() {
        let calls = vec![make_call(1, "/api/org/5/user/10", "GET")];
        let routes = vec![make_route(
            1,
            "/api/org/{orgId}/user/{userId}",
            "GET",
        )];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Template);
    }

    #[test]
    fn test_template_match_segment_count_mismatch() {
        let calls = vec![make_call(1, "/api/users/123/extra", "GET")];
        let routes = vec![make_route(1, "/api/users/{id}", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0); // segment count differs
    }

    #[test]
    fn test_template_match_literal_mismatch() {
        let calls = vec![make_call(1, "/api/users/123", "GET")];
        let routes = vec![make_route(1, "/api/items/{id}", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0); // "users" != "items"
    }

    #[test]
    fn test_template_match_method_mismatch() {
        let calls = vec![make_call(1, "/api/users/123", "GET")];
        let routes = vec![make_route(1, "/api/users/{id}", "POST")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0);
    }

    // ── Fuzzy match ──

    #[test]
    fn test_fuzzy_match_high_jaccard() {
        // "/api/v1/users" vs "/api/v1/user" — high character-bigram overlap
        // (only the last segment differs by one character)
        let calls = vec![make_call(1, "/api/v1/users", "POST")];
        let routes = vec![make_route(1, "/api/v1/user", "POST")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Fuzzy);
        assert!(matches[0].confidence > 0.7);
    }

    #[test]
    fn test_fuzzy_match_low_jaccard() {
        // "/api/auth/login" vs "/api/items/create" — low overlap
        let calls = vec![make_call(1, "/api/auth/login", "POST")];
        let routes = vec![make_route(1, "/api/items/create", "POST")];
        let matches = match_all(&calls, &routes);
        // Jaccard should be < 0.7 → no match
        assert_eq!(matches.len(), 0);
    }

    #[test]
    fn test_fuzzy_match_method_filter() {
        // High Jaccard but different methods → no match
        let calls = vec![make_call(1, "/api/auth/login", "GET")];
        let routes = vec![make_route(1, "/api/auth/login-handler", "POST")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 0);
    }

    // ── One-to-many matching ──

    #[test]
    fn test_one_to_many_matching() {
        let calls = vec![make_call(1, "/api/users/123", "GET")];
        let routes = vec![
            make_route(1, "/api/users/{id}", "GET"),
            make_route(2, "/api/users/{name}", "GET"),
        ];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 2);
        // Both should be template matches
        assert!(matches.iter().all(|m| m.match_type == MatchType::Template));
    }

    // ── Edge cases ──

    #[test]
    fn test_match_all_empty_input() {
        // Empty calls
        let calls: Vec<HttpCallRecord> = vec![];
        let routes = vec![make_route(1, "/api/users", "GET")];
        let matches = match_all(&calls, &routes);
        assert!(matches.is_empty());

        // Empty routes
        let calls = vec![make_call(1, "/api/users", "GET")];
        let routes: Vec<HttpRouteRecord> = vec![];
        let matches = match_all(&calls, &routes);
        assert!(matches.is_empty());

        // Both empty
        let calls: Vec<HttpCallRecord> = vec![];
        let routes: Vec<HttpRouteRecord> = vec![];
        let matches = match_all(&calls, &routes);
        assert!(matches.is_empty());
    }

    #[test]
    fn test_match_order_by_confidence() {
        // Exact, template, and fuzzy matches should be sorted by confidence
        let calls = vec![
            make_call(1, "/api/users/42", "GET"),     // template → route 2
            make_call(2, "/api/health", "GET"),        // exact → route 1
            make_call(3, "/api/v1/users", "POST"),     // fuzzy(high) → route 3
        ];
        let routes = vec![
            make_route(1, "/api/health", "GET"),
            make_route(2, "/api/users/{id}", "GET"),
            make_route(3, "/api/v1/user", "POST"),     // high Jaccard with /api/v1/users
        ];
        let matches = match_all(&calls, &routes);

        // Should have 3 matches (exact + template + fuzzy)
        assert!(matches.len() >= 3);
        // First should be exact (confidence 1.0, Exact > Template in sort)
        assert_eq!(matches[0].match_type, MatchType::Exact);
        // Confidence should be non-increasing
        for w in matches.windows(2) {
            assert!(w[0].confidence >= w[1].confidence);
        }
    }

    #[test]
    fn test_exact_beats_template_for_same_url() {
        // When both exact and template could match, exact takes priority
        let calls = vec![make_call(1, "/api/users/{id}", "GET")]; // literal URL containing {id}
        let routes = vec![
            make_route(1, "/api/users/{id}", "GET"), // exact match possible
        ];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Exact);
    }

    // ── Unit tests for helpers ──

    #[test]
    fn test_split_segments_normal() {
        assert_eq!(split_segments("/api/users/123"), vec!["api", "users", "123"]);
    }

    #[test]
    fn test_split_segments_root() {
        assert!(split_segments("/").is_empty());
    }

    #[test]
    fn test_split_segments_empty() {
        assert!(split_segments("").is_empty());
    }

    #[test]
    fn test_is_param_segment_valid() {
        assert!(is_param_segment("{user_id}"));
        assert!(is_param_segment("{id}"));
        assert!(is_param_segment("{orgId}"));
    }

    #[test]
    fn test_is_param_segment_invalid() {
        assert!(!is_param_segment("users"));
        assert!(!is_param_segment("{incomplete"));
        assert!(!is_param_segment("incomplete}"));
        assert!(!is_param_segment(""));
    }

    #[test]
    fn test_template_match_root() {
        let calls = vec![make_call(1, "/", "GET")];
        let routes = vec![make_route(1, "/", "GET")];
        let matches = match_all(&calls, &routes);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].match_type, MatchType::Exact);
    }

    #[test]
    fn test_character_bigrams() {
        let bigrams = character_bigrams("/api");
        assert!(bigrams.contains(&('/', 'a')));
        assert!(bigrams.contains(&('a', 'p')));
        assert!(bigrams.contains(&('p', 'i')));
        assert_eq!(bigrams.len(), 3);
    }

    #[test]
    fn test_character_bigrams_short() {
        assert!(character_bigrams("/").is_empty());
        assert!(character_bigrams("").is_empty());
        assert!(character_bigrams("a").is_empty());
    }

    #[test]
    fn test_jaccard_identical() {
        let score = jaccard_similarity("/api/users", "/api/users");
        assert!((score - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_jaccard_disjoint() {
        let score = jaccard_similarity("/abc", "/xyz");
        assert!((score - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_jaccard_similar() {
        // "/api/v1/users" vs "/api/v1/user" — high overlap, only last char differs
        let score = jaccard_similarity("/api/v1/users", "/api/v1/user");
        assert!(score > 0.7);
        assert!(score < 1.0);
    }
}
