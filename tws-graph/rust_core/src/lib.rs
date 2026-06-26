//! TWS CodeGraph -- Rust core library with PyO3 bindings.
//!
//! This crate provides a high-performance code symbol graph engine:
//! - Tree-sitter-based multi-language extraction (30+ languages)
//! - SQLite-backed graph storage with FTS5 search
//! - Graph traversal, impact analysis, clone detection
//! - GQL query language, LSP integration, and MCP server support
//!
//! Exposed to Python via the `_core` PyO3 module.

mod analysis;
pub mod traits;
pub mod db;
mod diff;
mod export;
mod gql;
mod graph;
mod hooks;
mod indexer;
mod lint;
mod lsp;
mod mcp;
mod query;
mod semantic;
mod services;
mod snapshot;
mod watch;
mod federate;

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::Path;
use std::collections::HashMap;
use std::sync::LazyLock;

// ============================================================================
// Helper: open a database from a path string
// ============================================================================

fn open_db(db_path: &str) -> PyResult<db::Database> {
    let path = Path::new(db_path);
    db::Database::open(path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

fn init_db(db_path: &str) -> PyResult<db::Database> {
    let path = Path::new(db_path);
    db::Database::initialize(path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

// ============================================================================
// Ping
// ============================================================================

/// Verify PyO3 bridge is working.
#[pyfunction]
fn ping() -> PyResult<String> {
    Ok("pong".to_string())
}

// ============================================================================
// Index
// ============================================================================

/// Process a single file during indexing.
///
/// Extracted into a standalone function to keep stack frames bounded on
/// platforms with small default thread stacks (notably Windows, 1 MB).
#[allow(clippy::too_many_arguments)]
fn index_one_file(
    conn: &rusqlite::Connection,
    root_path: &Path,
    file_path: &Path,
    registry: &indexer::registry::Registry,
) -> Option<(usize, usize)> {
    let file_path_str = file_path.to_string_lossy();

    // Detect language
    let lang = indexer::language::detect(&file_path_str)?;

    // Find extractor
    let ext_str = file_path.extension()?.to_str()?;
    let extractor = registry.find_by_extension(ext_str)?;

    // Get tree-sitter Language (lazily initialized via LazyLock)
    let ts_lang = lang_to_tree_sitter(lang)?;

    // Read file
    let abs_path = root_path.join(file_path);
    let source_bytes = std::fs::read(&abs_path).ok()?;

    // Compute relative path
    let rel_path = file_path_str.replace('\\', "/");

    // Create extraction context
    let mut ctx = indexer::context::ExtractionContext::new(rel_path, lang.to_string());

    // Parse with tree-sitter
    let mut parser = tree_sitter::Parser::new();
    parser.set_language(&ts_lang).ok()?;
    let tree = parser.parse(&source_bytes, None)?;

    // Extract
    extractor.extract(&source_bytes, &tree, &mut ctx).ok()?;

    let result = std::mem::take(&mut ctx.result);
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    // Insert nodes
    let mut node_count: usize = 0;
    let mut edge_count: usize = 0;
    let _ = conn.execute_batch("BEGIN TRANSACTION");
    for node in &result.nodes {
        let _ = conn.execute(
            "INSERT OR REPLACE INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, signature, docstring, visibility, is_abstract, \
             is_exported, decorators, framework, properties, body, body_hash, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
            rusqlite::params![
                node.id, node.kind, node.name, node.qualified_name,
                node.file_path, node.language,
                node.start_line, node.end_line,
                node.signature, node.docstring, node.visibility,
                node.is_abstract,
                node.is_exported, node.decorators, node.framework,
                node.properties,
                node.body, node.body_hash, ts,
            ],
        );
        node_count += 1;
    }
    for edge in &result.edges {
        let _ = conn.execute(
            "INSERT OR REPLACE INTO edges (source, target, target_text, kind, source_loc, provenance, properties) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                edge.source, edge.target, edge.target_text, edge.kind,
                edge.source_loc, edge.provenance, edge.properties,
            ],
        );
        edge_count += 1;
    }
    let _ = conn.execute_batch("COMMIT");

    Some((node_count, edge_count))
}

/// Run a full index of source files in `root`, storing results in `db_path`.
/// Returns a summary string with node/edge counts.
///
/// This function orchestrates the full indexing pipeline:
/// 1. Scan directory for source files
/// 2. Detect language for each file
/// 3. Parse with the appropriate tree-sitter grammar
/// 4. Extract symbols and edges using the language-specific extractor
/// 5. Insert results into the SQLite database
///
/// Per-file processing is delegated to `index_one_file()` to keep stack
/// frames small.  On Windows the default thread stack is only 1 MB, which
/// is insufficient when all 28+ tree-sitter grammars are initialised
/// eagerly inside a single function body.
#[pyfunction]
fn index(db_path: &str, root: &str) -> PyResult<String> {
    let db = init_db(db_path)?;
    let root_path = Path::new(root);

    // Scan directory for source files
    let files = indexer::scanner::scan_directory(root_path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    // Build registry with all available extractors
    let mut registry = indexer::registry::Registry::new();
    register_all_extractors(&mut registry);

    let conn = db.connection();
    let mut total_nodes: usize = 0;
    let mut total_edges: usize = 0;
    let mut file_count: usize = 0;

    for file_path in &files {
        if let Some((nodes, edges)) = index_one_file(&conn, &root_path, file_path, &registry) {
            total_nodes += nodes;
            total_edges += edges;
            file_count += 1;
        }
    }

    db.optimize()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    Ok(format!(
        "Index complete: {} files, {} nodes, {} edges",
        file_count, total_nodes, total_edges
    ))
}

/// Register all available language extractors in the registry.
fn register_all_extractors(registry: &mut indexer::registry::Registry) {
    use crate::traits::Extractor;
    registry.register(Box::new(indexer::extractors::python::PythonExtractor));
    registry.register(Box::new(indexer::extractors::typescript::TypeScriptExtractor));
    registry.register(Box::new(indexer::extractors::java::JavaExtractor));
    registry.register(Box::new(indexer::extractors::go::GoExtractor));
    registry.register(Box::new(indexer::extractors::rust::RustExtractor));
    registry.register(Box::new(indexer::extractors::kotlin::KotlinExtractor));
    registry.register(Box::new(indexer::extractors::php::PhpExtractor));
    registry.register(Box::new(indexer::extractors::ruby::RubyExtractor));
    registry.register(Box::new(indexer::extractors::c::CExtractor));
    registry.register(Box::new(indexer::extractors::cpp::CppExtractor));
    registry.register(Box::new(indexer::extractors::csharp::CSharpExtractor));
    registry.register(Box::new(indexer::extractors::scala::ScalaExtractor));
    registry.register(Box::new(indexer::extractors::elixir::ElixirExtractor));
    registry.register(Box::new(indexer::extractors::haskell::HaskellExtractor));
    registry.register(Box::new(indexer::extractors::clojure::ClojureExtractor));
    registry.register(Box::new(indexer::extractors::lua::LuaExtractor));
    registry.register(Box::new(indexer::extractors::bash::BashExtractor));
    registry.register(Box::new(indexer::extractors::html::HtmlExtractor));
    registry.register(Box::new(indexer::extractors::css::CssExtractor));
    registry.register(Box::new(indexer::extractors::markdown::MarkdownExtractor));
    registry.register(Box::new(indexer::extractors::toml_extractor::TomlExtractor));
    registry.register(Box::new(indexer::extractors::yaml::YamlExtractor));
    registry.register(Box::new(indexer::extractors::hcl::HclExtractor));
    registry.register(Box::new(indexer::extractors::json::JsonExtractor));
    registry.register(Box::new(indexer::extractors::sql::SqlExtractor));
    registry.register(Box::new(indexer::extractors::dockerfile::DockerfileExtractor));
    registry.register(Box::new(indexer::extractors::proto::ProtoExtractor));
    registry.register(Box::new(indexer::extractors::dart::DartExtractor));
    registry.register(Box::new(indexer::extractors::swift::SwiftExtractor));
    registry.register(Box::new(indexer::extractors::groovy::GroovyExtractor));
    registry.register(Box::new(indexer::extractors::zig::ZigExtractor));
    registry.register(Box::new(indexer::extractors::nix::NixExtractor));
    registry.register(Box::new(indexer::extractors::cmake::CmakeExtractor));
    registry.register(Box::new(indexer::extractors::kustomize::KustomizeExtractor));
    registry.register(Box::new(indexer::extractors::kubernetes::KubernetesExtractor));
}

/// Map a canonical language name to its tree-sitter Language.
///
/// Each tree-sitter Language is lazily initialized via `std::sync::LazyLock`
/// to avoid stack overflow on Windows (where the default thread stack is
/// only 1 MB — far too small for 28+ statically-initialized parser objects).
fn lang_to_tree_sitter(lang: &str) -> Option<tree_sitter::Language> {
    static PY_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_python::LANGUAGE.into());
    static TS_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into());
    static JAVA_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_java::LANGUAGE.into());
    static GO_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_go::LANGUAGE.into());
    static RUST_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_rust::LANGUAGE.into());
    static KOTLIN_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_kotlin_ng::LANGUAGE.into());
    static PHP_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_php::LANGUAGE_PHP.into());
    static RUBY_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_ruby::LANGUAGE.into());
    static C_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_c::LANGUAGE.into());
    static CPP_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_cpp::LANGUAGE.into());
    static CSHARP_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_c_sharp::LANGUAGE.into());
    static SCALA_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_scala::LANGUAGE.into());
    static ELIXIR_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_elixir::LANGUAGE.into());
    static HASKELL_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_haskell::LANGUAGE.into());
    static CLOJURE_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_clojure::LANGUAGE.into());
    static LUA_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_lua::LANGUAGE.into());
    static BASH_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_bash::LANGUAGE.into());
    static HTML_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_html::LANGUAGE.into());
    static CSS_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_css::LANGUAGE.into());
    static MD_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_md::LANGUAGE.into());
    static TOML_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_toml_ng::LANGUAGE.into());
    static YAML_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_yaml::LANGUAGE.into());
    static HCL_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_hcl::LANGUAGE.into());
    static JSON_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_json::LANGUAGE.into());
    static SQL_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_sequel::LANGUAGE.into());
    static DOCKER_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_containerfile::LANGUAGE.into());
    static PROTO_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_proto::LANGUAGE.into());
    static DART_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_dart::LANGUAGE.into());
    static SWIFT_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_swift::LANGUAGE.into());
    static GROOVY_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_groovy::LANGUAGE.into());
    static ZIG_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_zig::LANGUAGE.into());
    static NIX_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_nix::LANGUAGE.into());
    static CMAKE_LANG: LazyLock<tree_sitter::Language> = LazyLock::new(|| tree_sitter_cmake::LANGUAGE.into());

    match lang {
        "python" => Some(PY_LANG.clone()),
        "typescript" | "javascript" => Some(TS_LANG.clone()),
        "java" => Some(JAVA_LANG.clone()),
        "go" => Some(GO_LANG.clone()),
        "rust" => Some(RUST_LANG.clone()),
        "kotlin" => Some(KOTLIN_LANG.clone()),
        "php" => Some(PHP_LANG.clone()),
        "ruby" => Some(RUBY_LANG.clone()),
        "c" => Some(C_LANG.clone()),
        "cpp" => Some(CPP_LANG.clone()),
        "csharp" => Some(CSHARP_LANG.clone()),
        "scala" => Some(SCALA_LANG.clone()),
        "elixir" => Some(ELIXIR_LANG.clone()),
        "haskell" => Some(HASKELL_LANG.clone()),
        "clojure" => Some(CLOJURE_LANG.clone()),
        "lua" => Some(LUA_LANG.clone()),
        "bash" => Some(BASH_LANG.clone()),
        "html" => Some(HTML_LANG.clone()),
        "css" => Some(CSS_LANG.clone()),
        "markdown" => Some(MD_LANG.clone()),
        "toml" => Some(TOML_LANG.clone()),
        "yaml" => Some(YAML_LANG.clone()),
        "hcl" => Some(HCL_LANG.clone()),
        "json" => Some(JSON_LANG.clone()),
        "sql" => Some(SQL_LANG.clone()),
        "dockerfile" => Some(DOCKER_LANG.clone()),
        "proto" => Some(PROTO_LANG.clone()),
        "dart" => Some(DART_LANG.clone()),
        "swift" => Some(SWIFT_LANG.clone()),
        "groovy" => Some(GROOVY_LANG.clone()),
        "zig" => Some(ZIG_LANG.clone()),
        "nix" => Some(NIX_LANG.clone()),
        "cmake" => Some(CMAKE_LANG.clone()),
        _ => None,
    }
}

// ============================================================================
// Query operations
// ============================================================================

/// Full-text search with qualifier parsing (kind:, lang:, path:).
/// Returns a list of dicts with id, name, qualified_name, kind, file_path, language.
#[pyfunction]
fn search(py: Python<'_>, db_path: &str, query_text: &str, limit: Option<usize>) -> PyResult<Py<PyAny>> {
    let db = open_db(db_path)?;
    let parsed = query::search::parse_query(query_text);
    let results = query::run_search_parsed(&db, &parsed, limit.unwrap_or(50))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let list = pyo3::types::PyList::empty(py);
    for r in &results {
        let item = PyDict::new(py);
        item.set_item("id", &r.id)?;
        item.set_item("name", &r.name)?;
        item.set_item("qualified_name", &r.qualified_name)?;
        item.set_item("kind", &r.kind)?;
        item.set_item("file_path", &r.file_path)?;
        item.set_item("language", &r.language)?;
        item.set_item("line_number", r.start_line.unwrap_or(0))?;
        list.append(item)?;
    }
    Ok(list.into())
}

/// Show calls from or to a node.
/// If `inbound` is true, show callers; else show callees.
#[pyfunction]
fn calls(db_path: &str, name: &str, inbound: Option<bool>, depth: Option<usize>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let is_inbound = inbound.unwrap_or(false);
    let d = depth.unwrap_or(1);
    let results = query::run_calls(&db, name, is_inbound, d)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    if results.is_empty() {
        return Ok(format!("No calls found for '{}'", name));
    }

    let direction = if is_inbound { "Callers" } else { "Calls" };
    let mut out = format!("{} for '{}':\n", direction, name);
    for r in &results {
        let indent = "  ".repeat(r.depth);
        out.push_str(&format!("{}{} ({}):{} @ {}\n",
            indent, r.depth, r.node_name, r.node_kind, r.file_path));
    }
    Ok(out)
}

/// Compute impact radius of a symbol, grouped by module.
#[pyfunction]
fn impact(db_path: &str, name: &str, depth: Option<usize>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let d = depth.unwrap_or(1);
    let results = query::run_impact(&db, name, d)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    if results.is_empty() {
        return Ok(format!("No impact found for '{}'", name));
    }

    let mut out = format!("Impact of '{}' (depth {}):\n", name, d);

    // Group by module
    let mut by_module: HashMap<String, Vec<&query::ImpactResult>> = HashMap::new();
    for r in &results {
        by_module.entry(r.module.clone()).or_default().push(r);
    }

    for (mod_name, items) in &by_module {
        out.push_str(&format!("  {} ({} items):\n", mod_name, items.len()));
        for item in items {
            out.push_str(&format!("    - {} ({})\n", item.node_name, item.kind));
        }
    }
    Ok(out)
}

/// Find the shortest path between two symbols.
#[pyfunction]
fn trace(db_path: &str, src: &str, tgt: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let result = query::run_trace(&db, src, tgt)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    match result {
        Some(path) => {
            let mut out = format!("Trace from '{}' to '{}':\n", src, tgt);
            for (i, (_id, node_name)) in path.iter().enumerate() {
                if i > 0 {
                    out.push_str(" -> ");
                }
                out.push_str(node_name);
            }
            Ok(out)
        }
        None => Ok(format!("No path found from '{}' to '{}'", src, tgt)),
    }
}

/// List unresolved references with internal/external classification.
#[pyfunction]
fn unresolved(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let results = query::run_unresolved(&db)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    if results.is_empty() {
        return Ok("No unresolved references found.".to_string());
    }

    let mut out = String::from("Unresolved references:\n");
    for r in &results {
        out.push_str(&format!("  [{}] {} ({}) @ {}\n",
            r.classification, r.reference_name, r.reference_kind, r.file_path));
    }
    Ok(out)
}

// ============================================================================
// Export operations
// ============================================================================

/// Export a subgraph in Graphviz DOT format.
#[pyfunction]
fn export_dot(db_path: &str, from_node: &str, depth: Option<usize>, kind: Option<&str>) -> PyResult<String> {
    let db = open_db(db_path)?;
    Ok(export::export_dot(&db, from_node, depth.unwrap_or(2), kind))
}

/// Export a subgraph in Mermaid format.
#[pyfunction]
fn export_mermaid(db_path: &str, from_node: &str, depth: Option<usize>, kind: Option<&str>) -> PyResult<String> {
    let db = open_db(db_path)?;
    Ok(export::export_mermaid(&db, from_node, depth.unwrap_or(2), kind))
}

/// Export nodes and edges as JSON string.
#[pyfunction]
fn export_json(db_path: &str, kind: Option<&str>, limit: Option<usize>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let value = export::export_json(&db, kind, limit.unwrap_or(0));
    serde_json::to_string_pretty(&value)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

// ============================================================================
// Analysis operations
// ============================================================================

/// Detect cycles in the call graph.
#[pyfunction]
fn cycles(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let cycles = analysis::detect_cycles(&db);
    if cycles.is_empty() {
        return Ok("No cycles detected.".to_string());
    }
    let mut out = String::from("Cycles detected:\n");
    for (i, cycle) in cycles.iter().enumerate() {
        out.push_str(&format!("  Cycle {}: ", i + 1));
        let names: Vec<&str> = cycle.iter().map(|(_, n)| n.as_str()).collect();
        out.push_str(&names.join(" -> "));
        out.push('\n');
    }
    Ok(out)
}

/// Detect architectural layer violations.
/// Auto-infers layers from directory structure (depth 1).
#[pyfunction]
fn layers(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    // Auto-infer layers from directory structure
    let layer_map = infer_layers(&db);
    let violations = analysis::detect_layer_violations(&db, &layer_map);
    if violations.is_empty() {
        return Ok("No layer violations detected.".to_string());
    }
    let mut out = format!("Layer violations ({} found):\n", violations.len());
    for (src_id, src_name, src_layer, tgt_id, tgt_name, tgt_layer) in &violations {
        out.push_str(&format!("  {} (layer {}) -> {} (layer {})\n",
            src_name, src_layer, tgt_name, tgt_layer));
        let _ = (src_id, tgt_id);
    }
    Ok(out)
}

/// Auto-infer layer numbers from file path directory prefixes.
fn infer_layers(db: &db::Database) -> HashMap<String, usize> {
    let mut dirs: HashMap<String, usize> = HashMap::new();
    let conn = db.connection();
    if let Ok(mut stmt) = conn.prepare("SELECT DISTINCT file_path FROM nodes") {
        if let Ok(rows) = stmt.query_map([], |row| row.get::<_, String>(0)) {
            let mut unique_dirs: Vec<String> = rows
                .filter_map(|r| r.ok())
                .filter_map(|fp| {
                    // Extract first directory component
                    let parts: Vec<&str> = fp.split('/').collect();
                    if parts.len() >= 2 {
                        Some(parts[0].to_string())
                    } else if parts.len() == 1 {
                        Some(".".to_string())
                    } else {
                        None
                    }
                })
                .collect();
            unique_dirs.sort();
            unique_dirs.dedup();
            for (i, d) in unique_dirs.iter().enumerate() {
                dirs.insert(d.clone(), i);
            }
        }
    }
    dirs
}

/// Compute module-level metrics (cohesion, coupling, instability).
#[pyfunction]
fn metrics(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let m = analysis::compute_metrics(&db);
    if m.is_empty() {
        return Ok("No metrics computed (empty graph).".to_string());
    }
    let mut out = String::from("Module metrics:\n");
    out.push_str(&format!("  {:<30} {:>8} {:>8} {:>8} {:>8} {:>8} {:>8}\n",
        "Module", "Nodes", "Cohesion", "Coupling", "Instab.", "Internal", "External"));
    for (name, metrics) in &m {
        out.push_str(&format!("  {:<30} {:>8} {:>8.3} {:>8.3} {:>8.3} {:>8} {:>8}\n",
            name, metrics.node_count, metrics.cohesion, metrics.coupling,
            metrics.instability, metrics.internal_edges, metrics.external_edges));
    }
    Ok(out)
}

/// Taint analysis: find paths from source nodes to sink nodes.
#[pyfunction]
fn taint(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let sources = &["ENV_ACCESSES", "READS", "HTTP_CALLS"];
    let sinks = &["HTTP_CALLS", "GRPC_CLIENT", "WRITES"];
    let paths = analysis::taint_analysis(&db, sources, sinks);
    if paths.is_empty() {
        return Ok("No taint paths found.".to_string());
    }
    let mut out = format!("Taint analysis ({} paths):\n", paths.len());
    for (i, path) in paths.iter().enumerate() {
        out.push_str(&format!("  Path {}: ", i + 1));
        let names: Vec<&str> = path.iter().map(|(_, n)| n.as_str()).collect();
        out.push_str(&names.join(" -> "));
        out.push('\n');
    }
    Ok(out)
}

/// Code health scoring per file.
#[pyfunction]
fn health(db_path: &str, worst: Option<usize>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let mut scores = analysis::compute_health(&db);
    if scores.is_empty() {
        return Ok("No health data available.".to_string());
    }
    // Sort by score ascending (worst first)
    scores.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
    let limit = worst.unwrap_or(10).min(scores.len());

    let mut out = format!("Code health (worst {} files):\n", limit);
    out.push_str(&format!("  {:<40} {:>8} {:>8} {:>8} {:>8} {:>8}\n",
        "File", "Score", "Coverage", "Complex.", "Coupling", "Dead%"));
    for (fp, score, cov, comp, coup, dead) in scores.iter().take(limit) {
        out.push_str(&format!("  {:<40} {:>8.3} {:>8.3} {:>8.3} {:>8.3} {:>8.3}\n",
            fp, score, cov, comp, coup, dead));
    }
    Ok(out)
}

/// Predict the impact of changing a symbol.
#[pyfunction]
fn predict_impact(db_path: &str, name: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let pred = analysis::predict_impact(&db, name);
    let mut out = format!("Impact prediction for '{}':\n", name);
    out.push_str(&format!("  Risk level: {}\n", pred.risk_level));
    out.push_str(&format!("  Blast radius: {} hops\n", pred.radius));
    out.push_str(&format!("  Estimated churn: {} nodes\n", pred.estimated_churn));
    if !pred.affected_files.is_empty() {
        out.push_str(&format!("  Affected files ({}):\n", pred.affected_files.len()));
        for f in &pred.affected_files {
            out.push_str(&format!("    - {}\n", f));
        }
    } else {
        out.push_str("  Affected files: none\n");
    }
    Ok(out)
}

/// Detect potentially dead (unused) code.
#[pyfunction]
fn dead_code(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let dead = analysis::find_dead_code(&db);
    if dead.is_empty() {
        return Ok("No dead code detected.".to_string());
    }
    let mut out = format!("Potentially dead code ({} items):\n", dead.len());
    for (_id, name, kind, file_path) in &dead {
        out.push_str(&format!("  {} ({}) @ {}\n", name, kind, file_path));
    }
    Ok(out)
}

/// Find entry points in the codebase.
#[pyfunction]
fn entry_points(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let entries = analysis::find_entry_points(&db);
    if entries.is_empty() {
        return Ok("No entry points found.".to_string());
    }
    let mut out = format!("Entry points ({} found):\n", entries.len());
    for (_id, name, kind, reason) in &entries {
        out.push_str(&format!("  {} ({}) - {}\n", name, kind, reason));
    }
    Ok(out)
}

/// Detect code clones above a similarity threshold.
#[pyfunction]
fn clones(db_path: &str, threshold: Option<f64>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let t = threshold.unwrap_or(0.8);
    let results = analysis::find_clones(&db, t);
    if results.is_empty() {
        return Ok(format!("No clones detected above threshold {:.2}.", t));
    }
    let mut out = format!("Code clones (threshold {:.2}, {} pairs):\n", t, results.len());
    for (_id1, n1, _id2, n2, sim) in &results {
        out.push_str(&format!("  {} <-> {} (similarity: {:.3})\n", n1, n2, sim));
    }
    Ok(out)
}

/// Detect communities in the code graph using Louvain algorithm.
#[pyfunction]
fn community(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let results = analysis::detect_communities(&db);
    if results.is_empty() {
        return Ok("No communities detected (empty graph).".to_string());
    }
    // Count communities
    let max_comm = results.iter().map(|(_, _, c)| c).max().copied().unwrap_or(0);
    let mut out = format!("Community detection ({} communities):\n", max_comm + 1);
    // Group by community
    let mut by_community: HashMap<usize, Vec<&(String, String, usize)>> = HashMap::new();
    for r in &results {
        by_community.entry(r.2).or_default().push(r);
    }
    for cid in 0..=max_comm {
        if let Some(members) = by_community.get(&cid) {
            out.push_str(&format!("  Community {} ({} nodes):\n", cid, members.len()));
            for m in members.iter().take(10) {
                out.push_str(&format!("    - {}\n", m.1));
            }
            if members.len() > 10 {
                out.push_str(&format!("    ... and {} more\n", members.len() - 10));
            }
        }
    }
    Ok(out)
}

/// Compute centrality metrics (PageRank + Betweenness) for all nodes.
#[pyfunction]
fn centrality(db_path: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let pagerank = analysis::compute_pagerank(&db, 0.85, 50);
    let betweenness = analysis::compute_betweenness(&db);

    if pagerank.is_empty() {
        return Ok("No centrality data (empty graph).".to_string());
    }

    // Get node names
    let conn = db.connection();
    let mut names: HashMap<String, String> = HashMap::new();
    if let Ok(mut stmt) = conn.prepare("SELECT id, name FROM nodes") {
        if let Ok(rows) = stmt.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        }) {
            for row in rows.flatten() {
                names.insert(row.0, row.1);
            }
        }
    }

    // Combine and sort by PageRank
    let mut combined: Vec<(&String, &f64, &f64)> = pagerank.iter()
        .map(|(id, pr)| {
            let bw = betweenness.get(id).unwrap_or(&0.0);
            (id, pr, bw)
        })
        .collect();
    combined.sort_by(|a, b| b.1.partial_cmp(a.1).unwrap_or(std::cmp::Ordering::Equal));

    let mut out = String::from("Centrality metrics (top 20 by PageRank):\n");
    out.push_str(&format!("  {:<30} {:<12} {:<12}\n", "Node", "PageRank", "Betweenness"));
    for (id, pr, bw) in combined.iter().take(20) {
        let name = names.get(*id).map(|s| s.as_str()).unwrap_or(id);
        out.push_str(&format!("  {:<30} {:<12.6} {:<12.6}\n", name, pr, bw));
    }
    Ok(out)
}

// ============================================================================
// GQL
// ============================================================================

/// Execute a GQL query and return results as JSON string.
#[pyfunction]
fn gql_query(db_path: &str, query: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let results = gql::execute_gql(&db, query)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    serde_json::to_string_pretty(&results)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

// ============================================================================
// Snapshot operations
// ============================================================================

/// Create a named snapshot of the current database state.
#[pyfunction]
fn snapshot_create(db_path: &str, name: &str) -> PyResult<String> {
    let db = open_db(db_path)?;
    let repo_root = Path::new(db_path)
        .parent()  // codegraph/
        .and_then(|p| p.parent())  // .tws/
        .and_then(|p| p.parent())  // project root
        .unwrap_or_else(|| Path::new("."));
    snapshot::create_snapshot(&db, name, repo_root)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(format!("Snapshot '{}' created.", name))
}

/// List all available snapshots.
#[pyfunction]
fn snapshot_list(db_path: &str) -> PyResult<String> {
    let repo_root = Path::new(db_path)
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .unwrap_or_else(|| Path::new("."));
    let names = snapshot::list_snapshots(repo_root);
    if names.is_empty() {
        return Ok("No snapshots found.".to_string());
    }
    let mut out = String::from("Snapshots:\n");
    for n in &names {
        out.push_str(&format!("  - {}\n", n));
    }
    Ok(out)
}

/// Compare two snapshots and return the diff as text.
#[pyfunction]
fn snapshot_diff(db_path: &str, a: &str, b: &str, brief: Option<bool>) -> PyResult<String> {
    let repo_root = Path::new(db_path)
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .unwrap_or_else(|| Path::new("."));
    let diff = snapshot::diff_snapshots(repo_root, a, b, brief.unwrap_or(false))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    if brief.unwrap_or(false) {
        Ok(snapshot::format_diff_brief(&diff, a, b))
    } else {
        Ok(snapshot::format_diff_detailed(&diff, a, b))
    }
}

// ============================================================================
// Tool operations
// ============================================================================

/// Lint skill files in the given directory and return results.
///
/// Returns a JSON string matching the Python `LintReport` format:
/// ```json
/// {
///   "files_checked": N,
///   "errors": N,
///   "warnings": N,
///   "items": [
///     {"skill": "...", "file": "...", "rule": "...", "message": "...", "severity": "..."}
///   ]
/// }
/// ```
/// When `json_output` is false, a human-readable summary string is returned instead.
#[pyfunction]
fn lint_skills(skills_dir: &str, json_output: Option<bool>) -> PyResult<String> {
    let issues = lint::lint_skills(Path::new(skills_dir));

    // Count number of skill files checked (unique files from issues + dirs scanned)
    let skill_file_count = {
        let mut files = std::collections::HashSet::new();
        for i in &issues {
            files.insert(i.file.clone());
        }
        // Also count files with no issues by scanning dirs
        if let Ok(entries) = std::fs::read_dir(Path::new(skills_dir)) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    let skill_md = path.join("SKILL.md");
                    if skill_md.exists() {
                        files.insert(skill_md.to_string_lossy().to_string());
                    }
                }
            }
        }
        files.len()
    };

    let errors: Vec<_> = issues.iter().filter(|i| i.severity == "error").collect();
    let warnings: Vec<_> = issues.iter().filter(|i| i.severity == "warning").collect();

    let use_json = json_output.unwrap_or(false);

    if use_json {
        let json_issues: Vec<serde_json::Value> = issues.iter().map(|i| {
            // Extract skill name from file path (parent directory name)
            let skill_name = std::path::Path::new(&i.file)
                .parent()
                .and_then(|p| p.file_name())
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            serde_json::json!({
                "skill": skill_name,
                "file": i.file,
                "rule": i.rule,
                "message": i.message,
                "severity": i.severity,
            })
        }).collect();

        let output = serde_json::json!({
            "files_checked": skill_file_count,
            "errors": errors.len(),
            "warnings": warnings.len(),
            "items": json_issues,
        });
        serde_json::to_string_pretty(&output)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    } else {
        // Plain-text output matching Python's lint text format
        if issues.is_empty() {
            return Ok(format!("  检查 {} 个文件，全部通过", skill_file_count));
        }

        let mut out = String::new();
        if !errors.is_empty() {
            out.push_str(&format!("\n  {} 个错误:\n", errors.len()));
            for w in &errors {
                out.push_str(&format!("    [{}] {}: {}\n", w.rule, w.file, w.message));
            }
        }

        if !warnings.is_empty() {
            out.push_str(&format!("\n  {} 个警告:\n", warnings.len()));
            for w in &warnings {
                out.push_str(&format!("    [{}] {}: {}\n", w.rule, w.file, w.message));
            }
        }

        out.push_str(&format!("\n  检查 {} 个文件: {} 错误, {} 警告",
            skill_file_count, errors.len(), warnings.len()));

        Ok(out)
    }
}

/// Install git hooks for auto-sync.
#[pyfunction]
fn hooks_install(repo_path: &str) -> PyResult<String> {
    hooks::install_hooks(Path::new(repo_path))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok("Git hooks installed (post-commit, post-merge, post-checkout).".to_string())
}

/// Remove previously installed git hooks.
#[pyfunction]
fn hooks_remove(repo_path: &str) -> PyResult<String> {
    hooks::uninstall_hooks(Path::new(repo_path))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok("Git hooks removed.".to_string())
}

/// Check whether hooks are currently installed.
#[pyfunction]
fn hooks_status(repo_path: &str) -> PyResult<String> {
    Ok(hooks::hooks_status(Path::new(repo_path)))
}

// ============================================================================
// Advanced operations
// ============================================================================

/// Semantic search with multi-signal fusion ranking.
/// Returns results as JSON string.
#[pyfunction]
fn semantic_search(db_path: &str, query: &str, limit: Option<usize>) -> PyResult<String> {
    let db = open_db(db_path)?;
    let results = semantic::semantic_search(&db, query, limit.unwrap_or(10))
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    serde_json::to_string_pretty(&results)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

/// Detect available LSP servers and return a setup table.
#[pyfunction]
fn lsp_setup() -> PyResult<String> {
    let manager = lsp::manager::LspManager::detect();
    Ok(manager.format_setup_table())
}

/// Start watching a directory for file changes (polling-based).
/// Note: This is a simplified wrapper. For production use, prefer the CLI
/// `tws-graph watch` command which handles long-running process management.
#[pyfunction]
fn watch_start(db_path: &str, root: &str, interval: Option<f64>) -> PyResult<String> {
    let db_path_buf = std::path::PathBuf::from(db_path);
    let root_buf = std::path::PathBuf::from(root);
    let interval_secs = interval.unwrap_or(2.0);

    let mut watcher = watch::FileWatcher::new(&root_buf, &db_path_buf, interval_secs);
    let _rx = watcher.start();

    Ok(format!(
        "Watcher started on '{}' (interval: {:.1}s).\n\
         Note: Use 'tws-graph watch' CLI for long-running operation.\n\
         The Python binding returns immediately; the watcher runs in a background thread.",
        root, interval_secs
    ))
}

/// Federated search across multiple repositories.
/// `repos_json` is a JSON array of [name, root, db_path] tuples.
#[pyfunction]
fn federate_search(repos_json: &str, query: &str, kind: Option<&str>, lang: Option<&str>) -> PyResult<String> {
    let repos: Vec<(String, std::path::PathBuf, std::path::PathBuf)> =
        serde_json::from_str(repos_json)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let fm = federate::FederationManager::new(repos)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    let results = fm.federated_search(query, kind, lang);
    serde_json::to_string_pretty(&results)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

// ============================================================================
// Python module registration
// ============================================================================

/// The `_core` Python module -- provides native-speed primitives for tws-graph CLI.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Ping
    m.add_function(wrap_pyfunction!(ping, m)?)?;

    // Index
    m.add_function(wrap_pyfunction!(index, m)?)?;

    // Query
    m.add_function(wrap_pyfunction!(search, m)?)?;
    m.add_function(wrap_pyfunction!(calls, m)?)?;
    m.add_function(wrap_pyfunction!(impact, m)?)?;
    m.add_function(wrap_pyfunction!(trace, m)?)?;
    m.add_function(wrap_pyfunction!(unresolved, m)?)?;

    // Export
    m.add_function(wrap_pyfunction!(export_dot, m)?)?;
    m.add_function(wrap_pyfunction!(export_mermaid, m)?)?;
    m.add_function(wrap_pyfunction!(export_json, m)?)?;

    // Analysis
    m.add_function(wrap_pyfunction!(cycles, m)?)?;
    m.add_function(wrap_pyfunction!(layers, m)?)?;
    m.add_function(wrap_pyfunction!(metrics, m)?)?;
    m.add_function(wrap_pyfunction!(taint, m)?)?;
    m.add_function(wrap_pyfunction!(health, m)?)?;
    m.add_function(wrap_pyfunction!(predict_impact, m)?)?;
    m.add_function(wrap_pyfunction!(dead_code, m)?)?;
    m.add_function(wrap_pyfunction!(entry_points, m)?)?;
    m.add_function(wrap_pyfunction!(clones, m)?)?;
    m.add_function(wrap_pyfunction!(community, m)?)?;
    m.add_function(wrap_pyfunction!(centrality, m)?)?;

    // GQL
    m.add_function(wrap_pyfunction!(gql_query, m)?)?;

    // Snapshot
    m.add_function(wrap_pyfunction!(snapshot_create, m)?)?;
    m.add_function(wrap_pyfunction!(snapshot_list, m)?)?;
    m.add_function(wrap_pyfunction!(snapshot_diff, m)?)?;

    // Tools
    m.add_function(wrap_pyfunction!(lint_skills, m)?)?;
    m.add_function(wrap_pyfunction!(hooks_install, m)?)?;
    m.add_function(wrap_pyfunction!(hooks_remove, m)?)?;
    m.add_function(wrap_pyfunction!(hooks_status, m)?)?;

    // Advanced
    m.add_function(wrap_pyfunction!(semantic_search, m)?)?;
    m.add_function(wrap_pyfunction!(lsp_setup, m)?)?;
    m.add_function(wrap_pyfunction!(watch_start, m)?)?;
    m.add_function(wrap_pyfunction!(federate_search, m)?)?;

    Ok(())
}
