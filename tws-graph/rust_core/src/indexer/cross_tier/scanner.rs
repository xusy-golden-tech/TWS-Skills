//! CrossTierScanner — cross-tier HTTP call/route scanner.
//!
//! Runs after all language extractors have completed. Scans source files for
//! HTTP calls (frontend) and route definitions (backend), extracts them using
//! tree-sitter Query patterns, normalizes URLs, and matches callers to handlers.
//!
//! # Architecture
//!
//! ```text
//! scan(root, files)
//!   → for each file:
//!       detect_language() → ParserPool.get_or_create() → tree-sitter parse
//!         → for each applicable FrameworkPattern:
//!             compile Query → execute against AST → extract URL/method/function
//!               → normalize URL → create HttpCallRecord / HttpRouteRecord
//!   → batch_insert_http_calls() → read back with IDs
//!   → batch_insert_http_routes() → read back with IDs
//!   → matcher::match_all(calls, routes) → batch_insert_cross_lang_edges()
//! ```

use crate::db::connection::{hash_id, Database};
use crate::db::models::{CrossLangEdgeRecord, HttpCallRecord, HttpRouteRecord};
use crate::indexer::cross_tier::matcher;
use crate::indexer::cross_tier::normalizer;
use crate::indexer::cross_tier::patterns::{
    get_phase1_patterns, FrameworkPattern, PatternProcessor,
};
use crate::indexer::language;
use crate::indexer::parser_pool::ParserPool;
use crate::lang_to_tree_sitter;

use std::collections::HashMap;
use std::path::Path;
use tree_sitter::{Node, Query, QueryCursor, StreamingIterator};

// ============================================================================
// Public types
// ============================================================================

/// Scanner statistics returned after a successful scan.
pub struct CrossTierStats {
    /// Number of HTTP calls detected and inserted.
    pub http_calls_count: usize,
    /// Number of HTTP route definitions detected and inserted.
    pub http_routes_count: usize,
    /// Number of cross-language edges created.
    pub cross_lang_edges_count: usize,
}

// ============================================================================
// ExtractResult — tagged result from extraction methods
// ============================================================================

/// Tagged extraction result to handle the different return types
/// (calls vs routes) from a single dispatch point.
enum ExtractResult {
    Calls(Vec<HttpCallRecord>),
    Routes(Vec<HttpRouteRecord>),
    None,
}

// ============================================================================
// CrossTierScanner
// ============================================================================

/// The main cross-tier scanner.
///
/// Owns a `Database` handle for writing results and maintains in-memory
/// mappings for baseURL declarations and router prefix registrations.
pub struct CrossTierScanner {
    db: Database,
    /// Maps instance variable names (e.g. `"api"`) to their baseURL values.
    base_urls: HashMap<String, String>,
    /// Maps router variable names to their prefix values.
    router_prefixes: HashMap<String, String>,
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

impl CrossTierScanner {
    /// Create a new scanner with the given database handle.
    pub fn new(db: Database) -> Self {
        CrossTierScanner {
            db,
            base_urls: HashMap::new(),
            router_prefixes: HashMap::new(),
        }
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /// Main entry point: scan all indexed files for HTTP calls and routes.
    ///
    /// # Arguments
    /// * `root` — Project root directory (files are relative to this).
    /// * `files` — List of indexed file paths (relative to project root).
    ///
    /// # Returns
    /// Statistics about detected calls, routes, and matched edges.
    ///
    /// # Errors
    /// Returns an error string if database operations fail. Per-file errors
    /// are logged as warnings and do not interrupt the scan.
    pub fn scan(
        &mut self,
        root: &Path,
        files: &[String],
    ) -> Result<CrossTierStats, String> {
        let mut all_calls: Vec<HttpCallRecord> = Vec::new();
        let mut all_routes: Vec<HttpRouteRecord> = Vec::new();
        let mut parser_pool = ParserPool::new();

        // Clear internal mappings for a fresh scan
        self.base_urls.clear();
        self.router_prefixes.clear();

        let patterns = get_phase1_patterns();

        for file_path in files {
            let full_path = root.join(file_path);

            // Read source
            let source = match std::fs::read_to_string(&full_path) {
                Ok(s) => s,
                Err(_) => continue,
            };

            // Detect language
            let lang = match language::detect(file_path) {
                Some(l) => l,
                None => continue,
            };

            // Map "javascript" to "typescript" for pattern matching since
            // Phase 1 patterns are defined for "typescript" only (tree-sitter
            // uses the same grammar for both).
            let query_lang = if lang == "javascript" { "typescript" } else { lang };

            // Get parser from pool
            let parser = match parser_pool.get_or_create(lang) {
                Some(p) => p,
                None => continue,
            };

            let tree = match parser.parse(source.as_bytes(), None) {
                Some(t) => t,
                None => continue,
            };

            // Get language for tree-sitter Query compilation
            let ts_lang = match lang_to_tree_sitter(lang) {
                Some(l) => l,
                None => continue,
            };

            // Filter patterns applicable to this file's language
            let applicable: Vec<&FrameworkPattern> = patterns
                .iter()
                .filter(|p| p.language == query_lang)
                .collect();

            if applicable.is_empty() {
                continue;
            }

            for pattern in &applicable {
                let result = match pattern.post_process {
                    // Frontend HTTP call patterns
                    PatternProcessor::AxiosMethodShorthand
                    | PatternProcessor::FetchBasic
                    | PatternProcessor::TemplateString => {
                        self.extract_http_calls(
                            &source,
                            &tree,
                            pattern,
                            file_path,
                            query_lang,
                            &ts_lang,
                        )
                    }
                    PatternProcessor::AxiosConfigObject => {
                        self.extract_http_calls_config(
                            &source,
                            &tree,
                            pattern,
                            file_path,
                            query_lang,
                            &ts_lang,
                        )
                    }
                    // Backend route patterns
                    PatternProcessor::FastApiDecorator => {
                        self.extract_http_routes(
                            &source,
                            &tree,
                            pattern,
                            file_path,
                            query_lang,
                            &ts_lang,
                        )
                    }
                    PatternProcessor::FlaskRoute => {
                        self.extract_flask_routes(
                            &source,
                            &tree,
                            pattern,
                            file_path,
                            query_lang,
                            &ts_lang,
                        )
                    }
                    // Infrastructure patterns (collected into internal maps)
                    PatternProcessor::AxiosCreateBaseUrl => {
                        self.collect_base_urls(&source, &tree, file_path);
                        Ok(ExtractResult::None)
                    }
                    PatternProcessor::FastApiIncludeRouter => {
                        self.collect_router_prefixes(&source, &tree, file_path);
                        Ok(ExtractResult::None)
                    }
                };

                match result {
                    Ok(ExtractResult::Calls(calls)) => all_calls.extend(calls),
                    Ok(ExtractResult::Routes(routes)) => all_routes.extend(routes),
                    Ok(ExtractResult::None) => {}
                    Err(e) => {
                        log::warn!(
                            "cross-tier scanner: pattern {} failed on {}: {}",
                            pattern.name,
                            file_path,
                            e
                        );
                    }
                }
            }
        }

        // Batch insert calls into database
        if !all_calls.is_empty() {
            self.db
                .batch_insert_http_calls(&all_calls)
                .map_err(|e| format!("batch_insert_http_calls failed: {}", e))?;
        }

        // Batch insert routes into database
        if !all_routes.is_empty() {
            self.db
                .batch_insert_http_routes(&all_routes)
                .map_err(|e| format!("batch_insert_http_routes failed: {}", e))?;
        }

        // Read back calls and routes with assigned IDs for matching
        let calls_with_ids = self
            .db
            .get_all_http_calls()
            .map_err(|e| format!("get_all_http_calls failed: {}", e))?;

        let routes_with_ids = self
            .db
            .get_all_http_routes()
            .map_err(|e| format!("get_all_http_routes failed: {}", e))?;

        // URL matching
        let edges = matcher::match_all(&calls_with_ids, &routes_with_ids);
        let edge_records: Vec<CrossLangEdgeRecord> = edges
            .iter()
            .map(|m| CrossLangEdgeRecord {
                id: None,
                from_call_id: m.call_id,
                to_route_id: m.route_id,
                url: m.url.clone(),
                http_method: m.http_method.clone(),
                match_type: match_type_to_str(m.match_type).to_string(),
                confidence: m.confidence,
            })
            .collect();

        let edge_count = edge_records.len();
        if !edge_records.is_empty() {
            self.db
                .batch_insert_cross_lang_edges(&edge_records)
                .map_err(|e| format!("batch_insert_cross_lang_edges failed: {}", e))?;
        }

        Ok(CrossTierStats {
            http_calls_count: calls_with_ids.len(),
            http_routes_count: routes_with_ids.len(),
            cross_lang_edges_count: edge_count,
        })
    }

    // -----------------------------------------------------------------------
    // HTTP call extraction (frontend: axios shorthand, fetch, template)
    // -----------------------------------------------------------------------

    /// Extract HTTP calls using simple patterns:
    /// - AxiosMethodShorthand: `axios.get(url)` → @obj, @method, @url captures
    /// - FetchBasic: `fetch(url)` → @func, @url captures
    /// - TemplateString: `` fetch(`/api/${id}`) `` → @func, @tpl captures
    fn extract_http_calls(
        &self,
        source: &str,
        tree: &tree_sitter::Tree,
        pattern: &FrameworkPattern,
        file_path: &str,
        lang: &str,
        ts_lang: &tree_sitter::Language,
    ) -> Result<ExtractResult, String> {
        let query = Query::new(ts_lang, pattern.pattern)
            .map_err(|e| format!("query compile failed for {}: {}", pattern.name, e))?;
        let mut cursor = QueryCursor::new();
        let source_bytes = source.as_bytes();

        let mut matches = cursor.matches(&query, tree.root_node(), source_bytes);

        let mut calls: Vec<HttpCallRecord> = Vec::new();

        while let Some(m) = matches.next() {
            let mut url_str: Option<String> = None;
            let mut method_str: Option<String> = None;
            let mut template_raw: Option<String> = None;
            let mut has_func_check = false;
            let mut func_check_passed = true;

            for capture in m.captures {
                let capture_name = &query.capture_names()[capture.index as usize];
                let text = node_text(source_bytes, capture.node);

                match *capture_name {
                    "url" => {
                        url_str = Some(strip_quotes(&text));
                    }
                    "method" => {
                        // For AxiosMethodShorthand — the HTTP method (get, post, etc.)
                        method_str = Some(text.to_uppercase());
                    }
                    "func" => {
                        has_func_check = true;
                        // For FetchBasic and TemplateString — only match if func is "fetch"
                        if text != "fetch" {
                            func_check_passed = false;
                        }
                    }
                    "obj" => {
                        has_func_check = true;
                        // For AxiosMethodShorthand — only match if obj is "axios"
                        if text != "axios" {
                            func_check_passed = false;
                        }
                    }
                    "tpl" => {
                        // Template string URL
                        url_str = Some(template_to_pattern(&text));
                        template_raw = Some(text.clone());
                    }
                    _ => {}
                }
            }

            if has_func_check && !func_check_passed {
                continue;
            }

            let url = match url_str {
                Some(u) => u,
                None => continue,
            };

            // Determine HTTP method
            let http_method = match &method_str {
                Some(m) => m.clone(),
                None => {
                    // fetch() without explicit method defaults to GET
                    "GET".to_string()
                }
            };

            // Find enclosing function rowid
            let func_node_id = self.find_and_resolve_func_rowid(file_path, m.captures, source_bytes);

            let start_pos = m.captures[0].node.start_position();
            let is_template = matches!(pattern.post_process, PatternProcessor::TemplateString);

            // Normalize full URLs to path-only (e.g. http://host:port/path → /path)
            let url = normalizer::extract_url_path(&url);

            calls.push(HttpCallRecord {
                id: None,
                url,
                http_method,
                func_node_id,
                url_is_template: is_template,
                file_path: file_path.to_string(),
                line: (start_pos.row + 1) as i64,
                column: (start_pos.column + 1) as i64,
                source_lang: lang.to_string(),
                raw_snippet: template_raw.or_else(|| {
                    Some(snippet(source_bytes, m.captures[0].node))
                }),
            });
        }

        if calls.is_empty() {
            Ok(ExtractResult::None)
        } else {
            Ok(ExtractResult::Calls(calls))
        }
    }

    /// Extract HTTP calls from `axios({ method: 'POST', url: '/api/xxx' })` config-object calls.
    ///
    /// The pattern captures @func and @pairs. We walk the object's pair
    /// children to find `url` and `method` keys.
    fn extract_http_calls_config(
        &self,
        source: &str,
        tree: &tree_sitter::Tree,
        pattern: &FrameworkPattern,
        file_path: &str,
        lang: &str,
        ts_lang: &tree_sitter::Language,
    ) -> Result<ExtractResult, String> {
        let query = Query::new(ts_lang, pattern.pattern)
            .map_err(|e| format!("query compile failed for {}: {}", pattern.name, e))?;
        let mut cursor = QueryCursor::new();
        let source_bytes = source.as_bytes();

        let mut matches = cursor.matches(&query, tree.root_node(), source_bytes);

        let mut calls: Vec<HttpCallRecord> = Vec::new();

        while let Some(m) = matches.next() {
            // Check @func is "axios"
            let mut func_ok = false;
            for capture in m.captures {
                let capture_name = &query.capture_names()[capture.index as usize];
                if *capture_name == "func" {
                    if node_text(source_bytes, capture.node) == "axios" {
                        func_ok = true;
                    }
                    break;
                }
            }
            if !func_ok {
                continue;
            }

            // Walk from @pairs to the parent object node, then extract all pairs
            let mut url_value: Option<String> = None;
            let mut method_value: Option<String> = None;

            for capture in m.captures {
                let capture_name = &query.capture_names()[capture.index as usize];
                if *capture_name == "pairs" {
                    // Get the object node (parent of this pair)
                    if let Some(parent) = capture.node.parent() {
                        if parent.kind() == "object" {
                            let pairs = extract_object_pairs(source_bytes, parent);
                            url_value = pairs.get("url").cloned();
                            method_value = pairs.get("method").cloned();
                        }
                    }
                }
            }

            let url = match url_value {
                Some(u) => u,
                None => continue,
            };

            let http_method = method_value
                .unwrap_or_else(|| "GET".to_string())
                .to_uppercase();

            let func_node_id = self.find_and_resolve_func_rowid(file_path, m.captures, source_bytes);
            let start_pos = m.captures[0].node.start_position();

            // Normalize full URLs to path-only (e.g. http://host:port/path → /path)
            let url = normalizer::extract_url_path(&url);

            calls.push(HttpCallRecord {
                id: None,
                url,
                http_method,
                func_node_id,
                url_is_template: false,
                file_path: file_path.to_string(),
                line: (start_pos.row + 1) as i64,
                column: (start_pos.column + 1) as i64,
                source_lang: lang.to_string(),
                raw_snippet: Some(snippet(source_bytes, m.captures[0].node)),
            });
        }

        if calls.is_empty() {
            Ok(ExtractResult::None)
        } else {
            Ok(ExtractResult::Calls(calls))
        }
    }

    // -----------------------------------------------------------------------
    // HTTP route extraction (backend: FastAPI decorator)
    // -----------------------------------------------------------------------

    /// Extract HTTP routes from FastAPI decorator patterns:
    /// `@app.get("/users")`, `@router.post("/auth/register")`
    ///
    /// Captures: @app, @method, @path
    fn extract_http_routes(
        &self,
        source: &str,
        tree: &tree_sitter::Tree,
        pattern: &FrameworkPattern,
        file_path: &str,
        lang: &str,
        ts_lang: &tree_sitter::Language,
    ) -> Result<ExtractResult, String> {
        let query = Query::new(ts_lang, pattern.pattern)
            .map_err(|e| format!("query compile failed for {}: {}", pattern.name, e))?;
        let mut cursor = QueryCursor::new();
        let source_bytes = source.as_bytes();

        let mut matches = cursor.matches(&query, tree.root_node(), source_bytes);

        let mut routes: Vec<HttpRouteRecord> = Vec::new();

        while let Some(m) = matches.next() {
            let mut path_str: Option<String> = None;
            let mut method_str: Option<String> = None;
            let mut app_name: Option<String> = None;

            for capture in m.captures {
                let capture_name = &query.capture_names()[capture.index as usize];
                let text = node_text(source_bytes, capture.node);

                match *capture_name {
                    "path" => {
                        path_str = Some(strip_quotes(&text));
                    }
                    "method" => {
                        method_str = Some(text.to_uppercase());
                    }
                    "app" => {
                        app_name = Some(text.clone());
                    }
                    _ => {}
                }
            }

            let raw_path = match path_str {
                Some(p) => p,
                None => continue,
            };

            let http_method = method_str.unwrap_or_else(|| "GET".to_string());

            // Detect framework from source
            let framework = normalizer::detect_framework(file_path, source)
                .unwrap_or("fastapi");

            // Normalize URL
            let normalized_url = normalizer::normalize_url(&raw_path, framework);

            // Apply router prefix if applicable (currently keyed by app name only;
            // cross-file prefix resolution would require a more sophisticated approach)
            let final_url = if let Some(ref aname) = app_name {
                if let Some(prefix) = self.router_prefixes.get(aname) {
                    format!("{}{}", prefix, normalized_url)
                } else {
                    normalized_url
                }
            } else {
                normalized_url
            };

            // In FastAPI, the decorated function immediately follows the decorator.
            let func_node_id = self.find_enclosing_func_from_decorator(
                file_path,
                m.captures,
                source_bytes,
            );

            let start_pos = m.captures[0].node.start_position();

            routes.push(HttpRouteRecord {
                id: None,
                url_pattern: final_url,
                url_pattern_raw: raw_path,
                http_method,
                handler_node_id: func_node_id,
                file_path: file_path.to_string(),
                line: (start_pos.row + 1) as i64,
                column: (start_pos.column + 1) as i64,
                source_lang: lang.to_string(),
                source_framework: Some(framework.to_string()),
                raw_snippet: Some(snippet(source_bytes, m.captures[0].node)),
            });
        }

        if routes.is_empty() {
            Ok(ExtractResult::None)
        } else {
            Ok(ExtractResult::Routes(routes))
        }
    }

    /// Extract HTTP routes from Flask `@app.route('/path', methods=['GET'])` pattern.
    ///
    /// Captures: @obj, @route_attr, @path. We walk the argument_list
    /// to find the `methods` keyword argument.
    fn extract_flask_routes(
        &self,
        source: &str,
        tree: &tree_sitter::Tree,
        pattern: &FrameworkPattern,
        file_path: &str,
        lang: &str,
        ts_lang: &tree_sitter::Language,
    ) -> Result<ExtractResult, String> {
        let query = Query::new(ts_lang, pattern.pattern)
            .map_err(|e| format!("query compile failed for {}: {}", pattern.name, e))?;
        let mut cursor = QueryCursor::new();
        let source_bytes = source.as_bytes();

        let mut matches = cursor.matches(&query, tree.root_node(), source_bytes);

        let mut routes: Vec<HttpRouteRecord> = Vec::new();

        while let Some(m) = matches.next() {
            let mut path_str: Option<String> = None;
            let mut route_attr_str: Option<String> = None;
            let mut match_node: Option<Node> = None;

            for capture in m.captures {
                let capture_name = &query.capture_names()[capture.index as usize];
                let text = node_text(source_bytes, capture.node);

                if *capture_name == "path" {
                    path_str = Some(strip_quotes(&text));
                } else if *capture_name == "route_attr" {
                    route_attr_str = Some(text.clone());
                }

                // Track the captured node for parent traversal
                if match_node.is_none() {
                    match_node = Some(capture.node);
                }
            }

            // Skip non-Flask decorators (e.g. FastAPI's @app.get which also matches
            // structurally but uses HTTP method names instead of "route").
            if let Some(ref attr) = route_attr_str {
                if attr != "route" {
                    continue;
                }
            }

            let raw_path = match path_str {
                Some(p) => p,
                None => continue,
            };

            // Try to find the `methods` keyword argument by walking up to the
            // call node and then traversing the argument_list.
            let http_methods = match match_node {
                Some(node) => {
                    let call_node = find_ancestor(node, "call");
                    extract_flask_methods(source_bytes, call_node)
                }
                None => vec![],
            };

            let methods_list: Vec<String> = if http_methods.is_empty() {
                // Flask route default is GET
                vec!["GET".to_string()]
            } else {
                http_methods
            };

            let framework = normalizer::detect_framework(file_path, source)
                .unwrap_or("flask");

            let normalized_url = normalizer::normalize_url(&raw_path, framework);

            let func_node_id = match match_node {
                Some(node) => {
                    // Walk up from the string/path node to find the decorator,
                    // then to the decorated_definition containing the function
                    let decorator = find_ancestor(node, "decorator");
                    self.resolve_func_from_decorator(file_path, decorator, source_bytes)
                }
                None => 0,
            };

            let start_pos = match &match_node {
                Some(n) => n.start_position(),
                None => tree_sitter::Point::new(0, 0),
            };

            for method in &methods_list {
                routes.push(HttpRouteRecord {
                    id: None,
                    url_pattern: normalized_url.clone(),
                    url_pattern_raw: raw_path.clone(),
                    http_method: method.clone(),
                    handler_node_id: func_node_id,
                    file_path: file_path.to_string(),
                    line: (start_pos.row + 1) as i64,
                    column: (start_pos.column + 1) as i64,
                    source_lang: lang.to_string(),
                    source_framework: Some(framework.to_string()),
                    raw_snippet: Some(snippet(source_bytes, m.captures[0].node)),
                });
            }
        }

        if routes.is_empty() {
            Ok(ExtractResult::None)
        } else {
            Ok(ExtractResult::Routes(routes))
        }
    }

    // -----------------------------------------------------------------------
    // baseURL / router prefix collection
    // -----------------------------------------------------------------------

    /// Collect `axios.create({ baseURL: '...' })` declarations.
    /// Stores them in `self.base_urls` for later URL resolution.
    fn collect_base_urls(
        &mut self,
        source: &str,
        tree: &tree_sitter::Tree,
        _file_path: &str,
    ) {
        let source_bytes = source.as_bytes();
        let map = crate::indexer::cross_tier::patterns::find_baseurl_declarations(
            tree,
            source_bytes,
        );
        for (name, url) in map {
            self.base_urls.insert(name, url);
        }
    }

    /// Collect `app.include_router(router, prefix="/prefix")` declarations.
    /// Stores them in `self.router_prefixes` for URL prefix resolution.
    fn collect_router_prefixes(
        &mut self,
        source: &str,
        tree: &tree_sitter::Tree,
        _file_path: &str,
    ) {
        let source_bytes = source.as_bytes();
        let root = tree.root_node();

        // Walk all expression_statement nodes
        let mut cursor = root.walk();
        if cursor.goto_first_child() {
            self.walk_for_include_router(&mut cursor, source_bytes);
        }
    }

    fn walk_for_include_router(
        &mut self,
        cursor: &mut tree_sitter::TreeCursor,
        source: &[u8],
    ) {
        loop {
            let node = cursor.node();

            if node.kind() == "expression_statement" {
                if let Some(call_node) = node.child(0) {
                    if call_node.kind() == "call" {
                        if let Some(func_node) = call_node.child_by_field_name("function") {
                            if func_node.kind() == "attribute" {
                                if let Some(attr_node) =
                                    func_node.child_by_field_name("attribute")
                                {
                                    if node_text(source, attr_node) == "include_router" {
                                        if let Some(args) =
                                            call_node.child_by_field_name("arguments")
                                        {
                                            let prefix = extract_keyword_arg(
                                                source,
                                                args,
                                                "prefix",
                                            );
                                            if !prefix.is_empty() {
                                                let key = if let Some(obj) =
                                                    func_node.child_by_field_name("object")
                                                {
                                                    node_text(source, obj)
                                                } else {
                                                    String::new()
                                                };
                                                let resolved_key = if key.is_empty() {
                                                    String::from("__anonymous__")
                                                } else {
                                                    key
                                                };
                                                self.router_prefixes
                                                    .insert(resolved_key, strip_quotes(&prefix));
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            if cursor.goto_first_child() {
                self.walk_for_include_router(cursor, source);
                cursor.goto_parent();
            }

            if !cursor.goto_next_sibling() {
                break;
            }
        }
    }

    // -----------------------------------------------------------------------
    // Helpers: function node resolution
    // -----------------------------------------------------------------------

    /// From a set of tree-sitter captures, find the enclosing function node,
    /// compute its qualified_name hash, look up the database rowid, and return it.
    /// Returns 0 if the enclosing function could not be determined.
    fn find_and_resolve_func_rowid(
        &self,
        file_path: &str,
        captures: &[tree_sitter::QueryCapture],
        source: &[u8],
    ) -> i64 {
        if captures.is_empty() {
            return 0;
        }
        self.resolve_func_rowid_from_node(file_path, captures[0].node, source)
    }

    /// Find the enclosing function for a FastAPI-decorated handler.
    ///
    /// The captures point to the decorator node; the function definition is
    /// typically a sibling that follows the decorator inside decorated_definition.
    fn find_enclosing_func_from_decorator(
        &self,
        file_path: &str,
        captures: &[tree_sitter::QueryCapture],
        source: &[u8],
    ) -> i64 {
        if captures.is_empty() {
            return 0;
        }

        let start_node = captures[0].node;
        let decorated = find_ancestor(start_node, "decorated_definition");

        if let Some(dec_node) = decorated {
            for i in 0..dec_node.named_child_count() {
                if let Some(child) = dec_node.named_child(i) {
                    if child.kind() == "function_definition" {
                        return self.resolve_func_rowid_from_node(file_path, child, source);
                    }
                }
            }
        }

        0
    }

    /// Resolve the rowid of the function that follows a decorator node.
    fn resolve_func_from_decorator(
        &self,
        file_path: &str,
        decorator: Option<Node<'_>>,
        source: &[u8],
    ) -> i64 {
        if let Some(dec_node) = decorator {
            if let Some(parent) = dec_node.parent() {
                if parent.kind() == "decorated_definition" {
                    for i in 0..parent.named_child_count() {
                        if let Some(child) = parent.named_child(i) {
                            if child.kind() == "function_definition" {
                                return self.resolve_func_rowid_from_node(file_path, child, source);
                            }
                        }
                    }
                }
            }
        }
        0
    }

    /// Given a tree-sitter node (the function definition), extract the function
    /// name, build the qualified_name, hash it, and look up the database rowid.
    fn resolve_func_rowid_from_node(&self, file_path: &str, func_node: Node, source: &[u8]) -> i64 {
        let func_name = extract_function_name(func_node, source);

        if func_name.is_empty() {
            return 0;
        }

        let rel_path = file_path.replace('\\', "/");
        let qualified_name = format!("{}::{}", rel_path, func_name);
        let node_id = hash_id(&rel_path, &qualified_name);

        self.lookup_node_rowid(&node_id)
    }

    /// Look up the SQLite rowid for a node by its hash ID.
    fn lookup_node_rowid(&self, node_id: &str) -> i64 {
        let conn = self.db.connection();
        match conn.query_row(
            "SELECT rowid FROM nodes WHERE id = ?1",
            rusqlite::params![node_id],
            |row| row.get::<_, i64>(0),
        ) {
            Ok(rowid) => rowid,
            Err(_) => 0,
        }
    }
}

// ============================================================================
// Free helper functions
// ============================================================================

/// Get the source text of a tree-sitter node.
fn node_text(source: &[u8], node: Node) -> String {
    node.utf8_text(source)
        .map(|c| c.to_string())
        .unwrap_or_default()
}

/// Get a brief snippet of source text for a node (for debugging).
fn snippet(source: &[u8], node: Node) -> String {
    let text = node_text(source, node);
    if text.len() > 200 {
        format!("{}...", &text[..200])
    } else {
        text
    }
}

/// Strip surrounding quotes from a string literal.
fn strip_quotes(s: &str) -> String {
    let s = s.trim();
    if (s.starts_with('"') && s.ends_with('"'))
        || (s.starts_with('\'') && s.ends_with('\''))
        || (s.starts_with('`') && s.ends_with('`'))
    {
        s[1..s.len() - 1].to_string()
    } else {
        s.to_string()
    }
}

/// Convert a template string to a normalized URL pattern.
/// Replaces `${var}` → `{var}`.
fn template_to_pattern(s: &str) -> String {
    let s = strip_quotes(s);
    let mut result = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '$' && chars.peek() == Some(&'{') {
            chars.next(); // consume '{'
            result.push('{');
        } else {
            result.push(c);
        }
    }
    result
}

/// Walk up the AST from `start` to find an ancestor of the given `kind`.
fn find_ancestor<'a>(start: Node<'a>, kind: &str) -> Option<Node<'a>> {
    let mut current = start;
    loop {
        if current.kind() == kind {
            return Some(current);
        }
        current = current.parent()?;
    }
}

/// Extract the function name from a tree-sitter function definition node.
///
/// `source` must be the original source bytes that `func_node` was parsed from.
///
/// Handles:
/// - Python `function_definition` (name is second child)
/// - TypeScript `function_declaration` (name field)
/// - TypeScript `method_definition` (name field)
/// - TypeScript `arrow_function` (variable_declarator name)
fn extract_function_name(func_node: Node, source: &[u8]) -> String {
    let kind = func_node.kind();

    // Try the "name" field first (works for TS function_declaration, method_definition,
    // and also Python function_definition since tree-sitter-python v0.21+)
    if let Some(name_node) = func_node.child_by_field_name("name") {
        return name_node
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default();
    }

    match kind {
        "function_definition" => {
            // Python: def name(params):
            // Children: "def" (0), identifier (1), parameters (2), ...
            for i in 0..func_node.named_child_count() {
                if let Some(child) = func_node.named_child(i) {
                    if child.kind() == "identifier" {
                        return child
                            .utf8_text(source)
                            .map(|c| c.to_string())
                            .unwrap_or_default();
                    }
                }
            }
            String::new()
        }
        "function_declaration" | "method_definition" => {
            // TypeScript/JavaScript: name is usually the first identifier child
            for i in 0..func_node.named_child_count() {
                if let Some(child) = func_node.named_child(i) {
                    if child.kind() == "identifier" {
                        return child
                            .utf8_text(source)
                            .map(|c| c.to_string())
                            .unwrap_or_default();
                    }
                }
            }
            String::new()
        }
        "arrow_function" | "variable_declarator" => {
            if let Some(name_node) = func_node.child_by_field_name("name") {
                return name_node
                    .utf8_text(source)
                    .map(|c| c.to_string())
                    .unwrap_or_default();
            }
            String::new()
        }
        _ => String::new(),
    }
}

/// Convert MatchType to a static string.
fn match_type_to_str(mt: matcher::MatchType) -> &'static str {
    match mt {
        matcher::MatchType::Exact => "exact",
        matcher::MatchType::Template => "template",
        matcher::MatchType::Fuzzy => "fuzzy",
    }
}

/// Extract key-value pairs from a tree-sitter object node.
/// Returns a HashMap mapping property names to their (unquoted) values.
fn extract_object_pairs(source: &[u8], object: Node) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for i in 0..object.named_child_count() {
        if let Some(pair) = object.named_child(i) {
            if pair.kind() == "pair" {
                if let Some(key_node) = pair.child_by_field_name("key") {
                    let key = strip_quotes(&node_text(source, key_node));
                    if let Some(val_node) = pair.child_by_field_name("value") {
                        if val_node.kind() == "string" {
                            map.insert(key, strip_quotes(&node_text(source, val_node)));
                        }
                    }
                }
            }
        }
    }
    map
}

/// Extract the value of a keyword argument from a Python argument_list node.
fn extract_keyword_arg(source: &[u8], args: Node, keyword: &str) -> String {
    for i in 0..args.named_child_count() {
        if let Some(child) = args.named_child(i) {
            if child.kind() == "keyword_argument" {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if node_text(source, name_node) == keyword {
                        if let Some(val_node) = child.child_by_field_name("value") {
                            return node_text(source, val_node);
                        }
                    }
                }
            }
        }
    }
    String::new()
}

/// Extract HTTP methods from a Flask route's `methods` keyword argument.
/// Walks from the call node's argument_list to find `methods=['GET', 'POST']`.
fn extract_flask_methods(source: &[u8], call_node: Option<Node>) -> Vec<String> {
    let call = match call_node {
        Some(n) => n,
        None => return vec![],
    };

    let args = match call.child_by_field_name("arguments") {
        Some(a) => a,
        None => return vec![],
    };

    for i in 0..args.named_child_count() {
        if let Some(child) = args.named_child(i) {
            if child.kind() == "keyword_argument" {
                if let Some(name_node) = child.child_by_field_name("name") {
                    if node_text(source, name_node) == "methods" {
                        if let Some(val_node) = child.child_by_field_name("value") {
                            if val_node.kind() == "list" {
                                let mut methods = Vec::new();
                                for j in 0..val_node.named_child_count() {
                                    if let Some(item) = val_node.named_child(j) {
                                        let method = strip_quotes(&node_text(source, item))
                                            .to_uppercase();
                                        if !method.is_empty() {
                                            methods.push(method);
                                        }
                                    }
                                }
                                return methods;
                            }
                        }
                    }
                }
            }
        }
    }

    vec![]
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_quotes_single() {
        assert_eq!(strip_quotes("'hello'"), "hello");
    }

    #[test]
    fn test_strip_quotes_double() {
        assert_eq!(strip_quotes("\"hello\""), "hello");
    }

    #[test]
    fn test_strip_quotes_backtick() {
        assert_eq!(strip_quotes("`hello`"), "hello");
    }

    #[test]
    fn test_strip_quotes_no_quotes() {
        assert_eq!(strip_quotes("hello"), "hello");
    }

    #[test]
    fn test_strip_quotes_trim() {
        assert_eq!(strip_quotes("  'hello'  "), "hello");
    }

    #[test]
    fn test_template_to_pattern_simple() {
        assert_eq!(
            template_to_pattern("`/api/users/${userId}`"),
            "/api/users/{userId}"
        );
    }

    #[test]
    fn test_template_to_pattern_multiple() {
        assert_eq!(
            template_to_pattern("`/api/${module}/${action}`"),
            "/api/{module}/{action}"
        );
    }

    #[test]
    fn test_template_to_pattern_no_vars() {
        assert_eq!(template_to_pattern("`/api/users`"), "/api/users");
    }

    #[test]
    fn test_find_ancestor_self() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "def foo():\n    pass\n";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        // func is the first child of module
        let func = root.child(0).unwrap();
        assert_eq!(func.kind(), "function_definition");

        // Name node is the second child, index 1 (0=def, 1=identifier)
        if let Some(name_node) = func.child(1) {
            assert_eq!(name_node.kind(), "identifier");

            let ancestor = find_ancestor(name_node, "function_definition");
            assert!(ancestor.is_some());
            assert_eq!(ancestor.unwrap().kind(), "function_definition");
        }
    }

    #[test]
    fn test_find_enclosing_function_captures() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "def foo():\n    fetch('/api/users')\n";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        // Navigate to the string literal
        let func_def = root.child(0).unwrap();
        let body = func_def.child_by_field_name("body").unwrap();
        let expr = body.child(0).unwrap(); // expression_statement
        let call = expr.child(0).unwrap(); // call
        let args = call.child_by_field_name("arguments").unwrap();
        // args may have string as first child or named child
        let string_node = if args.child_count() > 0 {
            args.child(0).unwrap()
        } else {
            // fallback — shouldn't happen
            return;
        };

        // find_ancestor should find the function_definition
        let ancestor = find_ancestor(string_node, "function_definition");
        assert!(ancestor.is_some());
        assert_eq!(ancestor.unwrap().kind(), "function_definition");
    }

    #[test]
    fn test_extract_object_pairs() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into())
            .unwrap();
        let source = "({method: 'POST', url: '/api/x'})";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        // expression_statement → parenthesized_expression → object
        let expr_stmt = root.child(0).unwrap();
        if let Some(inner) = expr_stmt.child(0) {
            // parenthesized_expression
            if let Some(obj) = inner.child(1) {
                assert_eq!(obj.kind(), "object");

                let pairs = extract_object_pairs(source.as_bytes(), obj);
                assert_eq!(pairs.get("method").map(|s| s.as_str()), Some("POST"));
                assert_eq!(pairs.get("url").map(|s| s.as_str()), Some("/api/x"));
            }
        }
    }

    #[test]
    fn test_extract_flask_methods_from_source() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "@app.route('/users', methods=['GET', 'POST'])\ndef users():\n    pass\n";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        let decorated = root.child(0).unwrap(); // decorated_definition
        let decorator = decorated.child(0).unwrap(); // decorator
        // decorator.node(0) is '@' (anonymous), named_child(0) is the 'call' node
        let call = decorator.named_child(0).unwrap(); // call

        let methods = extract_flask_methods(source.as_bytes(), Some(call));
        assert_eq!(methods, vec!["GET", "POST"]);
    }

    #[test]
    fn test_extract_flask_methods_default() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "@app.route('/users')\ndef users():\n    pass\n";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        let decorated = root.child(0).unwrap();
        let decorator = decorated.child(0).unwrap();
        let call = decorator.child(0).unwrap();

        let methods = extract_flask_methods(source.as_bytes(), Some(call));
        // No methods keyword → empty vec
        assert!(methods.is_empty());
    }

    #[test]
    fn test_extract_keyword_arg() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "include_router(router, prefix='/api')";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        let expr = root.child(0).unwrap(); // expression_statement
        let call = expr.child(0).unwrap(); // call
        let args = call.child_by_field_name("arguments").unwrap();

        let prefix = extract_keyword_arg(source.as_bytes(), args, "prefix");
        assert_eq!(prefix, "'/api'");
    }

    #[test]
    fn test_cross_tier_stats() {
        let stats = CrossTierStats {
            http_calls_count: 5,
            http_routes_count: 3,
            cross_lang_edges_count: 2,
        };
        assert_eq!(stats.http_calls_count, 5);
        assert_eq!(stats.http_routes_count, 3);
        assert_eq!(stats.cross_lang_edges_count, 2);
    }

    #[test]
    fn test_match_type_to_str() {
        assert_eq!(match_type_to_str(matcher::MatchType::Exact), "exact");
        assert_eq!(match_type_to_str(matcher::MatchType::Template), "template");
        assert_eq!(match_type_to_str(matcher::MatchType::Fuzzy), "fuzzy");
    }

    #[test]
    fn test_extract_function_name_python() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        let source = "def get_users():\n    pass\n";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        let func_def = root.child(0).unwrap();
        assert_eq!(func_def.kind(), "function_definition");
        let name = extract_function_name(func_def, source.as_bytes());
        assert_eq!(name, "get_users");
    }

    #[test]
    fn test_extract_function_name_typescript() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into())
            .unwrap();
        let source = "function fetchUsers() {\n  return fetch('/api/users');\n}";
        let tree = parser.parse(source, None).unwrap();
        let root = tree.root_node();

        let func_decl = root.child(0).unwrap();
        assert_eq!(func_decl.kind(), "function_declaration");
        let name = extract_function_name(func_decl, source.as_bytes());
        assert_eq!(name, "fetchUsers");
    }
}
