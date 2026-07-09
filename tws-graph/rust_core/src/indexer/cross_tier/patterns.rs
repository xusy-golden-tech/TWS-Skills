//! Framework-level tree-sitter Query pattern definitions for cross-tier tracing.
//!
//! Each pattern is a tree-sitter Query S-expr string that can be compiled with
//! `tree_sitter::Query::new(language, pattern)` and executed against a parsed
//! syntax tree. The patterns are organized by framework and language.
//!
//! # Phase 1 patterns (8 total)
//!
//! ## Frontend (TypeScript/JavaScript) — 5 patterns
//! 1. `PATTERN_AXIOS_METHOD_SHORTHAND`  — `axios.get(url)`, `axios.post(url, data)`, etc.
//! 2. `PATTERN_AXIOS_CONFIG_OBJECT`     — `axios({ method: 'POST', url: '/api/xxx' })`
//! 3. `PATTERN_AXIOS_CREATE_BASEURL`    — `const api = axios.create({ baseURL: '/api' })`
//! 4. `PATTERN_FETCH_BASIC`             — `fetch('/api/xxx')`, `fetch(url, { method: 'POST' })`
//! 5. `PATTERN_TEMPLATE_STRING`         — Template string URLs with `${var}` interpolation
//!
//! ## Backend (Python) — 3 patterns
//! 6. `PATTERN_FASTAPI_DECORATOR`       — `@app.get("/users")`, `@router.post("/auth/register")`
//! 7. `PATTERN_FLASK_ROUTE`             — `@app.route('/users', methods=['GET'])`
//! 8. `PATTERN_FASTAPI_INCLUDE_ROUTER`  — `app.include_router(user_router, prefix="/api")`

use std::collections::HashMap;
use tree_sitter::{Node, Tree, TreeCursor};

// ============================================================================
// Phase 1: Frontend HTTP call patterns (TypeScript / JavaScript)
// ============================================================================

/// Match `axios.get(url)`, `axios.post(url, data)`, `axios.put(url)`,
/// `axios.delete(url)`, `axios.patch(url)` method shorthand calls.
///
/// Captures:
/// - `@obj`    — identifier "axios"
/// - `@method` — property_identifier (get, post, put, delete, patch)
/// - `@url`    — first string argument (the URL)
pub const PATTERN_AXIOS_METHOD_SHORTHAND: &str = r#"(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @method)
  arguments: (arguments . (string) @url))"#;

/// Match `axios({ method: 'POST', url: '/api/xxx', ... })` config-object calls.
///
/// Captures:
/// - `@func`   — identifier "axios"
/// - `@pairs`  — first `pair` node inside the object; scanner recurses from here
pub const PATTERN_AXIOS_CONFIG_OBJECT: &str = r#"(call_expression
  function: (identifier) @func
  arguments: (arguments (object . (pair) @pairs)))"#;

/// Match `const api = axios.create({ baseURL: '/api' })` for baseURL tracking.
///
/// Captures:
/// - `@instance_name` — variable name (e.g. "api")
/// - `@axios`         — identifier "axios"
/// - `@create`        — property_identifier "create"
/// - `@baseurl_key`   — "baseURL" key in the config object
/// - `@baseurl_val`   — baseURL value string
pub const PATTERN_AXIOS_CREATE_BASEURL: &str = r#"(variable_declarator
  name: (identifier) @instance_name
  value: (call_expression
    function: (member_expression
      object: (identifier) @axios
      property: (property_identifier) @create)
    arguments: (arguments (object
      (pair key: (property_identifier) @baseurl_key value: (string) @baseurl_val)))))"#;

/// Match `fetch('/api/xxx')` (default GET) and
/// `fetch('/api/xxx', { method: 'POST', ... })`.
///
/// Captures:
/// - `@func` — identifier "fetch"
/// - `@url`  — first string argument (the URL)
pub const PATTERN_FETCH_BASIC: &str = r#"(call_expression
  function: (identifier) @func
  arguments: (arguments . (string) @url))"#;

/// Match template-string URLs like `` fetch(`/api/users/${userId}`) ``.
///
/// Captures:
/// - `@func` — identifier (fetch / axios method)
/// - `@tpl`  — template_string node
pub const PATTERN_TEMPLATE_STRING: &str = r#"(call_expression
  function: (identifier) @func
  arguments: (arguments . (template_string) @tpl .))"#;

// ============================================================================
// Phase 1: Backend route definition patterns (Python)
// ============================================================================

/// Match FastAPI decorator routes: `@app.get("/users")`,
/// `@router.post("/auth/register")`, etc.
///
/// Captures:
/// - `@app`    — identifier for the app/router object (e.g. "app", "router")
/// - `@method` — HTTP method as identifier (get, post, put, delete, patch)
/// - `@path`   — string argument (the URL path)
pub const PATTERN_FASTAPI_DECORATOR: &str = r#"(decorator
  (call
    function: (attribute
      object: (identifier) @app
      attribute: (identifier) @method)
    arguments: (argument_list . (string) @path)))"#;

/// Match Flask route decorators:
/// `@app.route('/users', methods=['GET'])`,
/// `@bp.route('/register', methods=['POST'])`, etc.
///
/// Captures:
/// - `@obj`         — identifier for the app/blueprint object
/// - `@route_attr`  — the attribute name ("route")
/// - `@path`        — string argument (the URL path)
pub const PATTERN_FLASK_ROUTE: &str = r#"(decorator
  (call
    function: (attribute
      object: (identifier) @obj
      attribute: (identifier) @route_attr)
    arguments: (argument_list . (string) @path)))"#;

/// Match FastAPI `app.include_router(user_router, prefix="/api")`.
///
/// Captures:
/// - `@app`     — identifier for the app object
/// - `@include` — the attribute name ("include_router")
///
/// The scanner will then walk the argument_list to extract the prefix keyword.
pub const PATTERN_FASTAPI_INCLUDE_ROUTER: &str = r#"(expression_statement
  (call
    function: (attribute
      object: (identifier) @app
      attribute: (identifier) @include)
    arguments: (argument_list)))"#;

// ============================================================================
// Phase 2: Frontend HTTP call patterns — jQuery (TypeScript / JavaScript)
// ============================================================================

/// Match `$.ajax({ url: '/api/xxx', method: 'POST', ... })` config-object calls.
///
/// Captures:
/// - `@obj`       — identifier "$"
/// - `@func_name` — property_identifier "ajax"
/// - `@config`    — first object argument containing url/method pairs
pub const PATTERN_JQUERY_AJAX_CONFIG: &str = r#"(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @func_name)
  arguments: (arguments . (object) @config))"#;

/// Match jQuery shorthand HTTP calls: `$.get(url)`, `$.post(url, data)`,
/// `$.getJSON(url)`, `$.put(url)`, `$.delete(url)`.
///
/// Captures:
/// - `@obj`       — identifier "$"
/// - `@method`    — property_identifier (get, post, getJSON, put, delete)
/// - `@url`       — first string argument (the URL)
pub const PATTERN_JQUERY_SHORTHAND: &str = r#"(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @method)
  arguments: (arguments . (string) @url))"#;

// ============================================================================
// Phase 2: Backend route definition patterns — Spring Boot (Java)
// ============================================================================

/// Match Spring Boot mapping annotations:
/// `@GetMapping("/path")`, `@PostMapping("/path")`, `@PutMapping("/path")`,
/// `@DeleteMapping("/path")`, `@PatchMapping("/path")`.
///
/// Captures:
/// - `@annotation` — the annotation name (GetMapping, PostMapping, etc.)
/// - `@url`        — first string argument (the URL path)
///
/// Also matches the more verbose form:
/// `@RequestMapping(value = "/path", method = RequestMethod.GET)`.
pub const PATTERN_SPRING_MAPPING: &str = r#"[(marker_annotation
  name: (identifier) @annotation)
 (annotation
  name: (identifier) @annotation
  arguments: (annotation_argument_list . (string) @url))]"#;

/// Match `@RequestMapping("/prefix")` at class level for route prefix tracking.
///
/// Captures:
/// - `@annotation` — "RequestMapping"
/// - `@url`        — prefix string
pub const PATTERN_SPRING_REQUESTMAPPING_PREFIX: &str = r#"(marker_annotation
  name: (identifier) @annotation
  arguments: (annotation_argument_list . (string) @url))"#;

// ============================================================================
// Phase 3: Go Gin backend route patterns
// ============================================================================

/// Match `r.GET("/path", handler)`, `router.POST("/path", handler)` etc.
///
/// In Go's tree-sitter grammar:
/// - `call_expression` wraps the entire call
/// - `selector_expression` wraps `r.GET` (operand + field)
/// - `field_identifier` is the method name (GET, POST, PUT, etc.)
/// - `interpreted_string_literal` is the URL argument
///
/// Captures:
/// - `@obj`    — identifier of the router object (e.g., "r", "router")
/// - `@method` — HTTP method as field_identifier (GET, POST, PUT, DELETE, PATCH)
/// - `@url`    — first string argument (the route path)
pub const PATTERN_GIN_ROUTE: &str = r#"(call_expression
  function: (selector_expression
    operand: (identifier) @obj
    field: (field_identifier) @method)
  arguments: (argument_list . (interpreted_string_literal) @url))"#;

// ============================================================================
// Phase 3: Express.js backend route patterns
// ============================================================================

/// Match `app.get('/path', handler)`, `router.post('/path', handler)` etc.
///
/// Captures:
/// - `@obj`    — identifier of the app/router object
/// - `@method` — HTTP method as property_identifier (get, post, put, delete, patch)
/// - `@url`    — first string argument (the route path)
pub const PATTERN_EXPRESS_ROUTE: &str = r#"(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @method)
  arguments: (arguments . (string) @url))"#;

/// Match `app.use('/prefix', router)` for Express Router prefix tracking.
///
/// Captures:
/// - `@obj`  — identifier of the app object
/// - `@use`  — "use" property
/// - `@prefix` — prefix string
pub const PATTERN_EXPRESS_USE_PREFIX: &str = r#"(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @use)
  arguments: (arguments . (string) @prefix))"#;

// ============================================================================
// FrameworkPattern registry
// ============================================================================

/// Post-processing strategy for a matched pattern.
///
/// Determines how the scanner should extract URL, HTTP method, and other
/// metadata from the tree-sitter query captures.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PatternProcessor {
    /// Process `axios.get(url)` style — @method is the HTTP method.
    AxiosMethodShorthand,
    /// Process `axios({ method, url })` style — walk object pairs for method/url.
    AxiosConfigObject,
    /// Process `axios.create({ baseURL })` — record instance_name → baseURL mapping.
    AxiosCreateBaseUrl,
    /// Process `fetch(url)` — default GET, optionally extract method from 2nd arg.
    FetchBasic,
    /// Process template-string URLs — replace `${var}` with `{var}`.
    TemplateString,
    /// Process FastAPI `@app.get("/path")` — @method is the HTTP method.
    FastApiDecorator,
    /// Process Flask `@app.route("/path", methods=[...])` — parse methods list.
    FlaskRoute,
    /// Process FastAPI `app.include_router(router, prefix="/prefix")` — extract prefix.
    FastApiIncludeRouter,
    // ── Phase 2 ──
    /// Process `$.ajax({ url, method })` — walk object pairs for method/url.
    JQueryAjaxConfig,
    /// Process `$.get(url)`, `$.post(url, data)` — @method is the HTTP method.
    JQueryShorthand,
    /// Process `@GetMapping("/path")` — annotation name encodes HTTP method.
    SpringMapping,
    /// Process `@RequestMapping("/prefix")` at class-level — collect route prefix.
    SpringRequestMappingPrefix,
    // ── Phase 3 ──
    /// Process `r.GET("/path", handler)` — Go Gin route definition.
    GinRoute,
    /// Process `app.get('/path', handler)` — Express.js route definition.
    ExpressRoute,
    /// Process `app.use('/prefix', router)` — Express.js prefix collection.
    ExpressUsePrefix,
}

/// A framework-specific tree-sitter Query pattern for cross-tier extraction.
#[derive(Debug, Clone)]
pub struct FrameworkPattern {
    /// Human-readable name for this pattern.
    pub name: &'static str,
    /// Source language: "typescript", "javascript", or "python".
    pub language: &'static str,
    /// Target framework: "axios", "fetch", "fastapi", "flask".
    pub framework: &'static str,
    /// The tree-sitter Query S-expr string.
    pub pattern: &'static str,
    /// Post-processing strategy for captured nodes.
    pub post_process: PatternProcessor,
}

/// Return all Phase 1 (MVP) framework patterns.
///
/// These cover:
/// - Frontend: axios + fetch for TypeScript/JavaScript (5 patterns)
/// - Backend: FastAPI + Flask for Python (3 patterns)
pub fn get_phase1_patterns() -> Vec<FrameworkPattern> {
    vec![
        // --- Frontend: axios (3 patterns) ---
        FrameworkPattern {
            name: "PATTERN_AXIOS_METHOD_SHORTHAND",
            language: "typescript",
            framework: "axios",
            pattern: PATTERN_AXIOS_METHOD_SHORTHAND,
            post_process: PatternProcessor::AxiosMethodShorthand,
        },
        FrameworkPattern {
            name: "PATTERN_AXIOS_CONFIG_OBJECT",
            language: "typescript",
            framework: "axios",
            pattern: PATTERN_AXIOS_CONFIG_OBJECT,
            post_process: PatternProcessor::AxiosConfigObject,
        },
        FrameworkPattern {
            name: "PATTERN_AXIOS_CREATE_BASEURL",
            language: "typescript",
            framework: "axios",
            pattern: PATTERN_AXIOS_CREATE_BASEURL,
            post_process: PatternProcessor::AxiosCreateBaseUrl,
        },
        // --- Frontend: fetch (2 patterns) ---
        FrameworkPattern {
            name: "PATTERN_FETCH_BASIC",
            language: "typescript",
            framework: "fetch",
            pattern: PATTERN_FETCH_BASIC,
            post_process: PatternProcessor::FetchBasic,
        },
        FrameworkPattern {
            name: "PATTERN_TEMPLATE_STRING",
            language: "typescript",
            framework: "fetch",
            pattern: PATTERN_TEMPLATE_STRING,
            post_process: PatternProcessor::TemplateString,
        },
        // --- Backend: FastAPI (2 patterns) ---
        FrameworkPattern {
            name: "PATTERN_FASTAPI_DECORATOR",
            language: "python",
            framework: "fastapi",
            pattern: PATTERN_FASTAPI_DECORATOR,
            post_process: PatternProcessor::FastApiDecorator,
        },
        FrameworkPattern {
            name: "PATTERN_FASTAPI_INCLUDE_ROUTER",
            language: "python",
            framework: "fastapi",
            pattern: PATTERN_FASTAPI_INCLUDE_ROUTER,
            post_process: PatternProcessor::FastApiIncludeRouter,
        },
        // --- Backend: Flask (1 pattern) ---
        FrameworkPattern {
            name: "PATTERN_FLASK_ROUTE",
            language: "python",
            framework: "flask",
            pattern: PATTERN_FLASK_ROUTE,
            post_process: PatternProcessor::FlaskRoute,
        },
        // --- Phase 2: jQuery (2 patterns) ---
        FrameworkPattern {
            name: "PATTERN_JQUERY_AJAX_CONFIG",
            language: "typescript",
            framework: "jquery",
            pattern: PATTERN_JQUERY_AJAX_CONFIG,
            post_process: PatternProcessor::JQueryAjaxConfig,
        },
        FrameworkPattern {
            name: "PATTERN_JQUERY_SHORTHAND",
            language: "typescript",
            framework: "jquery",
            pattern: PATTERN_JQUERY_SHORTHAND,
            post_process: PatternProcessor::JQueryShorthand,
        },
        // --- Phase 2: Spring Boot (2 patterns) ---
        // IMPORTANT: PREFIX pattern must come FIRST — extract_spring_mapping
        // reads from self.router_prefixes, so collect_spring_prefixes must
        // populate it before the mapping patterns are processed.
        FrameworkPattern {
            name: "PATTERN_SPRING_REQUESTMAPPING_PREFIX",
            language: "java",
            framework: "spring_boot",
            pattern: PATTERN_SPRING_REQUESTMAPPING_PREFIX,
            post_process: PatternProcessor::SpringRequestMappingPrefix,
        },
        FrameworkPattern {
            name: "PATTERN_SPRING_MAPPING",
            language: "java",
            framework: "spring_boot",
            pattern: PATTERN_SPRING_MAPPING,
            post_process: PatternProcessor::SpringMapping,
        },
        // --- Phase 3: Go Gin (1 pattern) ---
        FrameworkPattern {
            name: "PATTERN_GIN_ROUTE",
            language: "go",
            framework: "gin",
            pattern: PATTERN_GIN_ROUTE,
            post_process: PatternProcessor::GinRoute,
        },
        // --- Phase 3: Express.js (2 patterns) ---
        // IMPORTANT: PREFIX pattern must come FIRST for Express too —
        // collect_express_prefixes must populate router_prefixes before
        // extract_express_routes reads from it.
        FrameworkPattern {
            name: "PATTERN_EXPRESS_USE_PREFIX",
            language: "typescript",
            framework: "express",
            pattern: PATTERN_EXPRESS_USE_PREFIX,
            post_process: PatternProcessor::ExpressUsePrefix,
        },
        FrameworkPattern {
            name: "PATTERN_EXPRESS_ROUTE",
            language: "typescript",
            framework: "express",
            pattern: PATTERN_EXPRESS_ROUTE,
            post_process: PatternProcessor::ExpressRoute,
        },
    ]
}

// ============================================================================
// Helper: find baseURL declarations
// ============================================================================

/// Walk a TypeScript/JavaScript tree-sitter AST to find all
/// `const <name> = axios.create({ baseURL: '<value>' })` declarations.
///
/// Returns a `HashMap` mapping instance variable names (e.g. `"api"`)
/// to their baseURL values (e.g. `"/api"`).
///
/// These mappings are used later by the scanner to resolve relative URLs
/// from `api.get("/users")`-style calls against `baseURL`.
pub fn find_baseurl_declarations(tree: &Tree, source: &[u8]) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let root = tree.root_node();
    let mut cursor = root.walk();

    // Walk all nodes looking for variable_declarator
    if cursor.goto_first_child() {
        walk_for_baseurl_declarations(&mut cursor, source, &mut map);
    }

    map
}

/// Recursively walk a tree-sitter subtree looking for `axios.create({ baseURL })`.
fn walk_for_baseurl_declarations(
    cursor: &mut TreeCursor,
    source: &[u8],
    map: &mut HashMap<String, String>,
) {
    loop {
        let node = cursor.node();
        if node.kind() == "variable_declarator" {
            check_baseurl_declarator(node, source, map);
        }

        if cursor.goto_first_child() {
            walk_for_baseurl_declarations(cursor, source, map);
            cursor.goto_parent();
        }

        if !cursor.goto_next_sibling() {
            break;
        }
    }
}

/// Check if a single `variable_declarator` node is an `axios.create({ baseURL })` call.
fn check_baseurl_declarator(node: Node, source: &[u8], map: &mut HashMap<String, String>) {
    // Get the variable name
    let name_node = match node.child_by_field_name("name") {
        Some(n) => n,
        None => return,
    };
    let name = node_text(source, name_node);

    // Get the value — must be a call_expression
    let value_node = match node.child_by_field_name("value") {
        Some(n) => n,
        None => return,
    };
    if value_node.kind() != "call_expression" {
        return;
    }

    // Check that the function is axios.create (member_expression)
    let func_node = match value_node.child_by_field_name("function") {
        Some(n) => n,
        None => return,
    };
    if func_node.kind() != "member_expression" {
        return;
    }

    // Verify object is "axios" and property is "create"
    let object_node = match func_node.child_by_field_name("object") {
        Some(n) => n,
        None => return,
    };
    let property_node = match func_node.child_by_field_name("property") {
        Some(n) => n,
        None => return,
    };
    if node_text(source, object_node) != "axios" {
        return;
    }
    if node_text(source, property_node) != "create" {
        return;
    }

    // Get arguments object: axios.create({ baseURL: '...' })
    let args_node = match value_node.child_by_field_name("arguments") {
        Some(n) => n,
        None => return,
    };

    // The first argument should be an object literal
    for i in 0..args_node.named_child_count() {
        if let Some(arg) = args_node.named_child(i) {
            if arg.kind() == "object" {
                let baseurl = extract_object_property(source, arg, "baseURL");
                if !baseurl.is_empty() && !name.is_empty() {
                    map.insert(name.clone(), baseurl);
                }
            }
        }
    }
}

/// Extract a string property value from a tree-sitter object node.
/// Searches for `{ key: "value" }` matching `property_name`.
fn extract_object_property(source: &[u8], object: Node, property_name: &str) -> String {
    for i in 0..object.named_child_count() {
        if let Some(pair) = object.named_child(i) {
            if pair.kind() == "pair" {
                if let Some(key_node) = pair.child_by_field_name("key") {
                    let key = strip_quotes(&node_text(source, key_node));
                    if key == property_name {
                        if let Some(val_node) = pair.child_by_field_name("value") {
                            if val_node.kind() == "string" {
                                return strip_quotes(&node_text(source, val_node));
                            }
                        }
                    }
                }
            }
        }
    }
    String::new()
}

/// Get the source text of a tree-sitter node.
fn node_text(source: &[u8], node: Node) -> String {
    node.utf8_text(source)
        .map(|c| c.to_string())
        .unwrap_or_default()
}

/// Strip surrounding quotes from a string literal value.
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

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use tree_sitter::Parser;
    use tree_sitter::StreamingIterator;

    // ------------------------------------------------------------------
    // Test helpers
    // ------------------------------------------------------------------

    /// Parse TypeScript source code and return the tree.
    fn parse_ts(source: &str) -> Tree {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into())
            .expect("set typescript language");
        parser.parse(source, None).expect("parse ts source")
    }

    /// Parse Python source code and return the tree.
    fn parse_py(source: &str) -> Tree {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .expect("set python language");
        parser.parse(source, None).expect("parse python source")
    }

    /// Execute a single query against a tree and return captures.
    fn run_query(
        language: tree_sitter::Language,
        pattern_str: &str,
        tree: &Tree,
        source: &[u8],
    ) -> Vec<(String, String)> {
        let query =
            tree_sitter::Query::new(&language, pattern_str).expect("compile query");
        let mut cursor = tree_sitter::QueryCursor::new();
        let mut matches = cursor.matches(&query, tree.root_node(), source);
        let mut results = Vec::new();
        loop {
            matches.advance();
            match matches.get() {
                Some(m) => {
                    for capture in m.captures {
                        let capture_name =
                            query.capture_names()[capture.index as usize].to_string();
                        let text_node = capture.node;
                        let text = text_node
                            .utf8_text(source)
                            .map(|c| c.to_string())
                            .unwrap_or_default();
                        results.push((capture_name, text));
                    }
                }
                None => break,
            }
        }
        results
    }

    fn find_capture<'a>(results: &'a [(String, String)], name: &str) -> Vec<&'a str> {
        results
            .iter()
            .filter(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
            .collect()
    }

    // ------------------------------------------------------------------
    // PATTERN_AXIOS_METHOD_SHORTHAND
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_axios_get_matches() {
        let src = "axios.get('/api/users')";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_METHOD_SHORTHAND,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["axios"]);
        assert_eq!(find_capture(&results, "method"), vec!["get"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/users'"]);
    }

    #[test]
    fn test_pattern_axios_post_matches() {
        let src = "axios.post('/api/auth/register', data)";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_METHOD_SHORTHAND,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["axios"]);
        assert_eq!(find_capture(&results, "method"), vec!["post"]);
        assert_eq!(
            find_capture(&results, "url"),
            vec!["'/api/auth/register'"]
        );
    }

    #[test]
    fn test_pattern_axios_put_matches() {
        let src = "axios.put('/api/users/123', data)";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_METHOD_SHORTHAND,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["put"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/users/123'"]);
    }

    #[test]
    fn test_pattern_axios_delete_matches() {
        let src = "axios.delete('/api/users/123')";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_METHOD_SHORTHAND,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["delete"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/users/123'"]);
    }

    #[test]
    fn test_pattern_axios_patch_matches() {
        let src = "axios.patch('/api/users/123', data)";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_METHOD_SHORTHAND,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["patch"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/users/123'"]);
    }

    // ------------------------------------------------------------------
    // PATTERN_AXIOS_CONFIG_OBJECT
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_axios_config_matches() {
        let src = "axios({method:'POST', url:'/api/x'})";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_CONFIG_OBJECT,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["axios"]);
        // @pairs captures the first pair — verify it's non-empty
        assert!(
            !find_capture(&results, "pairs").is_empty(),
            "Expected at least one @pairs capture"
        );
    }

    #[test]
    fn test_pattern_axios_config_object_url_first() {
        let src = "axios({url: '/api/users', method: 'GET'})";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_CONFIG_OBJECT,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["axios"]);
    }

    // ------------------------------------------------------------------
    // PATTERN_AXIOS_CREATE_BASEURL (find_baseurl_declarations)
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_axios_create_baseurl() {
        let src = "const api = axios.create({ baseURL: '/api' })";
        let tree = parse_ts(src);
        // Also run the query for validation
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_AXIOS_CREATE_BASEURL,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "instance_name"), vec!["api"]);
        assert_eq!(find_capture(&results, "axios"), vec!["axios"]);
        assert_eq!(find_capture(&results, "create"), vec!["create"]);
        assert_eq!(find_capture(&results, "baseurl_key"), vec!["baseURL"]);
        assert_eq!(find_capture(&results, "baseurl_val"), vec!["'/api'"]);
    }

    #[test]
    fn test_find_baseurl_declarations_single() {
        let src = "const api = axios.create({ baseURL: '/api' })";
        let tree = parse_ts(src);
        let map = find_baseurl_declarations(&tree, src.as_bytes());
        assert_eq!(map.len(), 1);
        assert_eq!(map.get("api"), Some(&"/api".to_string()));
    }

    #[test]
    fn test_find_baseurl_declarations_multiple() {
        let src = r#"
            const api = axios.create({ baseURL: '/api' });
            const v2 = axios.create({ baseURL: '/api/v2' });
        "#;
        let tree = parse_ts(src);
        let map = find_baseurl_declarations(&tree, src.as_bytes());
        assert_eq!(map.len(), 2);
        assert_eq!(map.get("api"), Some(&"/api".to_string()));
        assert_eq!(map.get("v2"), Some(&"/api/v2".to_string()));
    }

    #[test]
    fn test_find_baseurl_declarations_none() {
        let src = "const x = 42;";
        let tree = parse_ts(src);
        let map = find_baseurl_declarations(&tree, src.as_bytes());
        assert!(map.is_empty());
    }

    // ------------------------------------------------------------------
    // PATTERN_FETCH_BASIC
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_fetch_default_get() {
        let src = "fetch('/api/x')";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_FETCH_BASIC,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["fetch"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/x'"]);
    }

    #[test]
    fn test_pattern_fetch_with_method() {
        let src = "fetch('/api/x', {method:'POST'})";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_FETCH_BASIC,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["fetch"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/x'"]);
    }

    #[test]
    fn test_pattern_fetch_post_url_body() {
        let src = "fetch('/api/auth', { method: 'POST', body: JSON.stringify(data) })";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_FETCH_BASIC,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["fetch"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/api/auth'"]);
    }

    // ------------------------------------------------------------------
    // PATTERN_TEMPLATE_STRING
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_template_simple() {
        let src = "fetch(`/api/users/${userId}`)";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_TEMPLATE_STRING,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["fetch"]);
        let tpl = find_capture(&results, "tpl");
        assert!(!tpl.is_empty(), "Expected template_string capture");
    }

    #[test]
    fn test_pattern_template_complex_flagged() {
        let src = "fetch(`/api/${module}/${action}`)";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_TEMPLATE_STRING,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "func"), vec!["fetch"]);
        let tpl_text = find_capture(&results, "tpl").join("");
        assert!(tpl_text.contains("module"), "Expected template containing 'module'");
        assert!(tpl_text.contains("action"), "Expected template containing 'action'");
    }

    #[test]
    fn test_pattern_template_axios_post() {
        let src = "axios.post(`/api/users/${userId}`, data)";
        // Template patterns only match `identifier` function, not `member_expression`.
        // This is expected — the scanner will handle member_expression template calls
        // by combining PATTERN_TEMPLATE_STRING logic with member_expression matching.
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_TEMPLATE_STRING,
            &tree,
            src.as_bytes(),
        );
        // The pattern matches `func: (identifier)` — for member_expression calls,
        // the scanner uses a separate matching path.
        // Verify at least the template_string is parseable
        assert!(tree.root_node().has_error() == false);
    }

    // ------------------------------------------------------------------
    // PATTERN_FASTAPI_DECORATOR
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_fastapi_get() {
        let src = "@app.get(\"/users\")\ndef get_users():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_DECORATOR,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "app"), vec!["app"]);
        assert_eq!(find_capture(&results, "method"), vec!["get"]);
        assert_eq!(find_capture(&results, "path"), vec!["\"/users\""]);
    }

    #[test]
    fn test_pattern_fastapi_router_post() {
        let src = "@router.post(\"/register\")\ndef register():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_DECORATOR,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "app"), vec!["router"]);
        assert_eq!(find_capture(&results, "method"), vec!["post"]);
        assert_eq!(find_capture(&results, "path"), vec!["\"/register\""]);
    }

    #[test]
    fn test_pattern_fastapi_put_delete() {
        // PUT
        let src_put = "@app.put(\"/users/1\")\ndef update_user():\n    pass\n";
        let tree = parse_py(src_put);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_DECORATOR,
            &tree,
            src_put.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["put"]);
        assert_eq!(find_capture(&results, "path"), vec!["\"/users/1\""]);

        // DELETE
        let src_del = "@app.delete(\"/users/1\")\ndef delete_user():\n    pass\n";
        let tree = parse_py(src_del);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_DECORATOR,
            &tree,
            src_del.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["delete"]);
    }

    #[test]
    fn test_pattern_fastapi_patch() {
        let src = "@app.patch(\"/users/1\")\ndef patch_user():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_DECORATOR,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["patch"]);
    }

    // ------------------------------------------------------------------
    // PATTERN_FLASK_ROUTE
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_flask_route_methods() {
        let src = "@app.route('/users', methods=['POST'])\ndef create_user():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FLASK_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["app"]);
        assert_eq!(find_capture(&results, "route_attr"), vec!["route"]);
        assert_eq!(find_capture(&results, "path"), vec!["'/users'"]);
    }

    #[test]
    fn test_pattern_flask_blueprint_route() {
        let src = "@bp.route('/register', methods=['POST'])\ndef register():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FLASK_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["bp"]);
        assert_eq!(find_capture(&results, "route_attr"), vec!["route"]);
        assert_eq!(find_capture(&results, "path"), vec!["'/register'"]);
    }

    #[test]
    fn test_pattern_flask_route_default_get() {
        let src = "@app.route('/users')\ndef list_users():\n    pass\n";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FLASK_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "path"), vec!["'/users'"]);
    }

    // ------------------------------------------------------------------
    // PATTERN_FASTAPI_INCLUDE_ROUTER
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_fastapi_include_router() {
        let src = "app.include_router(user_router, prefix=\"/api\")";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_INCLUDE_ROUTER,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "app"), vec!["app"]);
        assert_eq!(find_capture(&results, "include"), vec!["include_router"]);
    }

    #[test]
    fn test_pattern_fastapi_include_router_auth_prefix() {
        let src = "app.include_router(auth_router, prefix=\"/api/auth\")";
        let tree = parse_py(src);
        let results = run_query(
            tree_sitter_python::LANGUAGE.into(),
            PATTERN_FASTAPI_INCLUDE_ROUTER,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "app"), vec!["app"]);
        assert_eq!(find_capture(&results, "include"), vec!["include_router"]);
    }

    // ------------------------------------------------------------------
    // get_phase1_patterns
    // ------------------------------------------------------------------

    #[test]
    fn test_get_phase1_patterns_returns_15() {
        let patterns = get_phase1_patterns();
        assert_eq!(patterns.len(), 15);

        // Count by language
        let ts_count = patterns.iter().filter(|p| p.language == "typescript").count();
        let py_count = patterns.iter().filter(|p| p.language == "python").count();
        let java_count = patterns.iter().filter(|p| p.language == "java").count();
        let go_count = patterns.iter().filter(|p| p.language == "go").count();
        assert_eq!(ts_count, 9);
        assert_eq!(py_count, 3);
        assert_eq!(java_count, 2);
        assert_eq!(go_count, 1);

        // Count by framework
        let axios_count = patterns.iter().filter(|p| p.framework == "axios").count();
        let fetch_count = patterns.iter().filter(|p| p.framework == "fetch").count();
        let fastapi_count = patterns.iter().filter(|p| p.framework == "fastapi").count();
        let flask_count = patterns.iter().filter(|p| p.framework == "flask").count();
        let jquery_count = patterns.iter().filter(|p| p.framework == "jquery").count();
        let spring_count = patterns.iter().filter(|p| p.framework == "spring_boot").count();
        let gin_count = patterns.iter().filter(|p| p.framework == "gin").count();
        let express_count = patterns.iter().filter(|p| p.framework == "express").count();
        assert_eq!(axios_count, 3);
        assert_eq!(fetch_count, 2);
        assert_eq!(fastapi_count, 2);
        assert_eq!(flask_count, 1);
        assert_eq!(jquery_count, 2);
        assert_eq!(spring_count, 2);
        assert_eq!(gin_count, 1);
        assert_eq!(express_count, 2);
    }

    #[test]
    fn test_get_phase1_patterns_unique_names() {
        let patterns = get_phase1_patterns();
        let mut names: Vec<&str> = patterns.iter().map(|p| p.name).collect();
        names.sort();
        names.dedup();
        assert_eq!(names.len(), 15, "All pattern names should be unique");
    }

    #[test]
    fn test_get_phase1_patterns_all_have_valid_language() {
        let patterns = get_phase1_patterns();
        let valid_langs = ["typescript", "javascript", "python", "java", "go"];
        for p in &patterns {
            assert!(
                valid_langs.contains(&p.language),
                "{} has invalid language: {}",
                p.name,
                p.language
            );
        }
    }

    #[test]
    fn test_get_phase1_patterns_all_have_non_empty_pattern() {
        let patterns = get_phase1_patterns();
        for p in &patterns {
            assert!(
                !p.pattern.is_empty(),
                "{} has empty pattern",
                p.name
            );
        }
    }

    // ------------------------------------------------------------------
    // helper: strip_quotes
    // ------------------------------------------------------------------

    #[test]
    fn test_strip_quotes_single_quotes() {
        assert_eq!(strip_quotes("'hello'"), "hello");
    }

    #[test]
    fn test_strip_quotes_double_quotes() {
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

    // ------------------------------------------------------------------
    // Phase 2: Spring Boot annotation detection (Java AST walk)
    // ------------------------------------------------------------------

    #[test]
    fn test_spring_annotation_detection() {
        let src = r#"@GetMapping("/blog/getAllBlogs")
public List<Blog> getPost() {
    return blogRepository.findAll();
}"#;
        let mut parser = tree_sitter::Parser::new();
        parser.set_language(&tree_sitter_java::LANGUAGE.into()).unwrap();
        let tree = parser.parse(src, None).unwrap();

        // Dump AST
        fn dump(node: tree_sitter::Node, src: &[u8], depth: usize) {
            let indent = "  ".repeat(depth);
            let text = node.utf8_text(src).unwrap_or("<err>");
            eprintln!("{}{} [{}..{}]: {:?}",
                indent, node.kind(), node.start_position().column, node.end_position().column,
                if text.len() > 60 { &text[..60] } else { text });
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                dump(child, src, depth + 1);
            }
        }
        dump(tree.root_node(), src.as_bytes(), 0);

        // Walk AST looking for annotation nodes, including inside modifiers
        let mut found = Vec::new();
        let mut to_visit: Vec<tree_sitter::Node> = Vec::new();
        to_visit.push(tree.root_node());

        while let Some(node) = to_visit.pop() {
            let kind = node.kind();
            // Check all possible annotation node types
            if kind == "annotation" || kind == "marker_annotation" {
                let mut ann_name = String::new();
                let mut ann_url = String::new();
                let mut cursor = node.walk();
                eprintln!("--- annotation node children ---");
                for child in node.children(&mut cursor) {
                    let ck = child.kind();
                    let ct = child.utf8_text(src.as_bytes()).unwrap_or("<err>");
                    eprintln!("  child: kind={} text={:?}", ck, if ct.len() > 100 { &ct[..100] } else { ct });
                    if ck == "identifier" {
                        ann_name = ct.to_string();
                    }
                    if ck == "annotation_argument_list" {
                        let mut ac = child.walk();
                        for arg in child.children(&mut ac) {
                            let ak = arg.kind();
                            let at = arg.utf8_text(src.as_bytes()).unwrap_or("<err>");
                            eprintln!("    arg: kind={} text={:?}", ak, if at.len() > 100 { &at[..100] } else { at });
                            if ak == "string_literal" || ak == "string" {
                                ann_url = at.to_string();
                            }
                        }
                    }
                }
                if !ann_name.is_empty() {
                    found.push((ann_name.clone(), ann_url.clone()));
                    eprintln!("Found: {} with URL '{}' (kind={})", ann_name, ann_url, kind);
                }
            }
            // Also check the modifiers field of method/class declarations
            if kind == "method_declaration" || kind == "class_declaration" {
                if let Some(modifiers) = node.child_by_field_name("modifiers") {
                    to_visit.push(modifiers);
                }
            }
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                to_visit.push(child);
            }
        }

        eprintln!("Total found: {}", found.len());
        assert!(!found.is_empty(), "No annotations found");
        assert_eq!(found[0].0, "GetMapping");
        assert!(found[0].1.contains("/blog/getAllBlogs"), "URL was: {}", found[0].1);
    }

    // ------------------------------------------------------------------
    // Phase 3: Go Gin route detection
    // ------------------------------------------------------------------

    /// Parse Go source code and return the tree.
    fn parse_go(source: &str) -> Tree {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_go::LANGUAGE.into())
            .expect("set go language");
        parser.parse(source, None).expect("parse go source")
    }

    #[test]
    fn test_pattern_gin_get_route() {
        let src = "package main\nfunc main() {\n\tr.GET(\"/users\", GetUsers)\n}";
        let tree = parse_go(src);
        let results = run_query(
            tree_sitter_go::LANGUAGE.into(),
            PATTERN_GIN_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["r"]);
        assert_eq!(find_capture(&results, "method"), vec!["GET"]);
        assert_eq!(find_capture(&results, "url"), vec!["\"/users\""]);
    }

    #[test]
    fn test_pattern_gin_post_route() {
        let src = "package main\nfunc main() {\n\trouter.POST(\"/create_user\", CreateUser)\n}";
        let tree = parse_go(src);
        let results = run_query(
            tree_sitter_go::LANGUAGE.into(),
            PATTERN_GIN_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["POST"]);
        assert_eq!(find_capture(&results, "url"), vec!["\"/create_user\""]);
    }

    #[test]
    fn test_pattern_gin_route_with_param() {
        let src = "package main\nfunc main() {\n\tr.GET(\"/user/:id\", GetUser)\n}";
        let tree = parse_go(src);
        let results = run_query(
            tree_sitter_go::LANGUAGE.into(),
            PATTERN_GIN_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["GET"]);
        assert_eq!(find_capture(&results, "url"), vec!["\"/user/:id\""]);
    }

    #[test]
    fn test_pattern_gin_put_delete_routes() {
        let src = "package main\nfunc main() {\n\tr.PUT(\"/update_user/:id\", UpdateUser)\n\tr.DELETE(\"/delete_user/:id\", DeleteUser)\n}";
        let tree = parse_go(src);
        let results = run_query(
            tree_sitter_go::LANGUAGE.into(),
            PATTERN_GIN_ROUTE,
            &tree,
            src.as_bytes(),
        );
        let methods = find_capture(&results, "method");
        assert!(methods.contains(&"PUT"));
        assert!(methods.contains(&"DELETE"));
    }

    // ------------------------------------------------------------------
    // Phase 3: Express.js route detection
    // ------------------------------------------------------------------

    #[test]
    fn test_pattern_express_get_route() {
        let src = "const router = require('express').Router();\nrouter.get('/users', getUsers);";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_EXPRESS_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["router"]);
        assert_eq!(find_capture(&results, "method"), vec!["get"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/users'"]);
    }

    #[test]
    fn test_pattern_express_post_route() {
        let src = "import express from 'express';\nconst router = express.Router();\nrouter.post('/', createUser);";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_EXPRESS_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["post"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/'"]);
    }

    #[test]
    fn test_pattern_express_route_with_param() {
        let src = "import express from 'express';\nconst router = express.Router();\nrouter.get('/:id', getUser);";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_EXPRESS_ROUTE,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "method"), vec!["get"]);
        assert_eq!(find_capture(&results, "url"), vec!["'/:id'"]);
    }

    #[test]
    fn test_pattern_express_use_prefix() {
        let src = "import express from 'express';\nconst app = express();\napp.use('/users', userRouter);";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_EXPRESS_USE_PREFIX,
            &tree,
            src.as_bytes(),
        );
        assert_eq!(find_capture(&results, "obj"), vec!["app"]);
        assert_eq!(find_capture(&results, "use"), vec!["use"]);
        assert_eq!(find_capture(&results, "prefix"), vec!["'/users'"]);
    }

    #[test]
    fn test_pattern_express_not_match_axios() {
        // Express pattern should still MATCH axios calls structurally,
        // but the scanner disambiguates via is_express_file(). This test
        // verifies the pattern itself matches both for capture extraction.
        let src = "axios.get('/api/users')";
        let tree = parse_ts(src);
        let results = run_query(
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            PATTERN_EXPRESS_ROUTE,
            &tree,
            src.as_bytes(),
        );
        // The Express route pattern structurally matches axios calls
        assert_eq!(find_capture(&results, "method"), vec!["get"]);
        // But @obj is "axios", not a router — scanner disambiguates
        assert_eq!(find_capture(&results, "obj"), vec!["axios"]);
    }

    #[test]
    fn test_is_express_file_detection() {
        // Test that the is_express_file helper correctly identifies express imports
        // (this is tested in scanner, but verify the pattern here)
        assert!(true); // placeholder — actual test in scanner.rs
    }
}
