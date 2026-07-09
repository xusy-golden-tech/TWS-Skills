//! Data models for the code-graph SQLite schema.
//!
//! Every struct mirrors the Python `TypedDict` definitions in
//! `tws_graph/store/types.py` and the `schema.sql` table definitions.
//! All types are compatible with rusqlite `FromSql` / `ToSql` row mapping.

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// NodeRecord — a code symbol
// ---------------------------------------------------------------------------

/// A node (symbol) in the code graph.
///
/// Maps to the `nodes` table. The `id` is an XXH3_64 hash encoded as
/// 16 hex chars, computed as `XXH3_64("{file_path}:{qualified_name}")`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct NodeRecord {
    /// XXH3_64(file_path:qualified_name) encoded as 16 hex chars.
    pub id: String,
    /// Symbol kind — function, class, method, interface, variable, etc.
    pub kind: String,
    /// Simple name, e.g. `"calculateTotal"`.
    pub name: String,
    /// Fully qualified name, e.g. `"src/utils.py::MathHelper.calculateTotal"`.
    pub qualified_name: String,
    /// Project-relative file path.
    pub file_path: String,
    /// Language identifier — python, typescript, java, etc.
    pub language: String,
    /// Start line number (1-based).
    pub start_line: i64,
    /// End line number (1-based).
    pub end_line: i64,
    /// Function / method signature.
    pub signature: Option<String>,
    /// Documentation string (first 200 chars).
    pub docstring: Option<String>,
    /// Visibility: public / private / protected / internal.
    pub visibility: Option<String>,
    /// 0 or 1.
    pub is_abstract: i32,
    /// 0 or 1.
    pub is_exported: i32,
    /// JSON array of decorator / annotation names.
    pub decorators: Option<String>,
    /// Framework identifier, e.g. `"fastapi"`, `"express"`, `"spring"`.
    pub framework: Option<String>,
    /// JSON object of arbitrary extension properties.
    pub properties: Option<String>,
    /// Function / method body source text.
    pub body: Option<String>,
    /// SHA256 of function body (for incremental re-index).
    pub body_hash: Option<String>,
    /// Optional HTTP role: 'http-call' | 'http-route' | NULL.
    /// Set by CrossTierScanner during cross-tier indexing.
    pub http_role: Option<String>,
    /// Update timestamp (epoch milliseconds).
    pub updated_at: i64,
}

// ---------------------------------------------------------------------------
// EdgeRecord — a directed relationship
// ---------------------------------------------------------------------------

/// An edge (relationship) between two nodes in the code graph.
///
/// `source` references `nodes.id` via FK CASCADE. `target` is a node
/// hash that may not yet exist in `nodes` (unresolved cross-file refs).
/// `id` is `Option<i64>` because it is NULL during batch construction
/// and assigned by SQLite AUTOINCREMENT on insert.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EdgeRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// Source node id (FK → nodes.id).
    pub source: String,
    /// Target node id (may be unresolvable at insert time).
    pub target: String,
    /// Unhashed qualified name used to compute the target (for post-processing).
    pub target_text: Option<String>,
    /// Edge kind — CALLS, IMPORTS, EXTENDS, REFERENCES, CONTAINS, etc.
    pub kind: String,
    /// Source location string `"file:line:col"`.
    pub source_loc: Option<String>,
    /// Provenance — tree-sitter, heuristic, resolved, unresolved, ambiguous.
    pub provenance: Option<String>,
    /// JSON object of arbitrary extension properties.
    pub properties: Option<String>,
}

// ---------------------------------------------------------------------------
// FileRecord — indexed file metadata
// ---------------------------------------------------------------------------

/// Metadata for an indexed source file.
///
/// Maps to the `files` table. `path` is the primary key.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct FileRecord {
    /// Project-relative file path (primary key).
    pub path: String,
    /// SHA256 of the file content.
    pub content_hash: String,
    /// Detected language identifier.
    pub language: String,
    /// Number of nodes extracted from this file.
    pub node_count: i32,
    /// Epoch milliseconds of last successful index run.
    pub indexed_at: i64,
    /// File size in bytes (used for stat pre-filter).
    pub size: i64,
    /// Last modification time (epoch seconds).
    pub modified_at: i64,
}

// ---------------------------------------------------------------------------
// UnresolvedRefRecord — a cross-file / external reference
// ---------------------------------------------------------------------------

/// An unresolved reference — typically a cross-file call or import that
/// could not be resolved during the extraction pass.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct UnresolvedRefRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// The node that contains this reference (FK → nodes.id).
    pub from_node_id: String,
    /// Text of the unresolved reference.
    pub reference_name: String,
    /// Reference kind — call / import / reference.
    pub reference_kind: String,
    /// Line number where the reference occurs.
    pub line: i32,
    /// Column number where the reference occurs.
    pub col: i32,
    /// JSON array of candidate node_id strings.
    pub candidates: Option<String>,
    /// Source provenance — tree-sitter, heuristic, lsp_failed, manual.
    pub source: Option<String>,
    /// File path where the reference occurs.
    pub file_path: String,
    /// Language of the file.
    pub language: String,
    /// 0 = internal (project), 1 = external (SDK / library).
    pub is_external: i32,
}

// ---------------------------------------------------------------------------
// SearchResult — FTS5 query output
// ---------------------------------------------------------------------------

/// Result row from an FTS5 full-text search.
///
/// Includes the BM25 rank (lower is better).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SearchResult {
    /// Node id.
    pub id: String,
    /// Simple name.
    pub name: String,
    /// Fully qualified name.
    pub qualified_name: String,
    /// Symbol kind.
    pub kind: String,
    /// File path.
    pub file_path: String,
    /// Language.
    pub language: String,
    /// Function / method signature.
    pub signature: Option<String>,
    /// Documentation string.
    pub docstring: Option<String>,
    /// BM25 score (lower is better).
    pub rank: Option<f64>,
    /// Start line number (1-based) from the nodes table.
    pub start_line: Option<i64>,
}

// ---------------------------------------------------------------------------
// HttpCallRecord — a detected HTTP call from frontend code
// ---------------------------------------------------------------------------

/// A detected HTTP call from frontend code (TypeScript/JavaScript).
///
/// Maps to the `http_calls` table (created by v009 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HttpCallRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// Extracted URL or URL template.
    pub url: String,
    /// HTTP method: GET/POST/PUT/DELETE/PATCH.
    pub http_method: String,
    /// Rowid of the function node in `nodes` that makes this call.
    pub func_node_id: i64,
    /// 0 = literal URL, 1 = extracted from template string.
    pub url_is_template: bool,
    /// Project-relative file path.
    pub file_path: String,
    /// Start line number (1-based).
    pub line: i64,
    /// Start column number (1-based).
    pub column: i64,
    /// Source language: typescript / javascript.
    pub source_lang: String,
    /// Raw source snippet for debugging.
    pub raw_snippet: Option<String>,
}

// ---------------------------------------------------------------------------
// HttpRouteRecord — a detected HTTP route from backend code
// ---------------------------------------------------------------------------

/// A detected HTTP route definition from backend code (Python/Java/Go/etc.).
///
/// Maps to the `http_routes` table (created by v009 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HttpRouteRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// Normalized URL pattern, e.g. `/api/users/{user_id}`.
    pub url_pattern: String,
    /// Original URL pattern, e.g. `/api/users/<int:user_id>`.
    pub url_pattern_raw: String,
    /// HTTP method: GET/POST/PUT/DELETE/PATCH.
    pub http_method: String,
    /// Rowid of the handler function node in `nodes`.
    pub handler_node_id: i64,
    /// Project-relative file path.
    pub file_path: String,
    /// Start line number (1-based).
    pub line: i64,
    /// Start column number (1-based).
    pub column: i64,
    /// Source language: python / java / go / etc.
    pub source_lang: String,
    /// Detected framework: fastapi / flask / spring / express.
    pub source_framework: Option<String>,
    /// Raw source snippet for debugging.
    pub raw_snippet: Option<String>,
}

// ---------------------------------------------------------------------------
// CrossLangEdgeRecord — a matched HTTP call ↔ HTTP route pair
// ---------------------------------------------------------------------------

/// A cross-language edge linking a frontend HTTP call to a backend HTTP route.
///
/// Maps to the `cross_lang_edges` table (created by v009 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrossLangEdgeRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// FK → http_calls.id.
    pub from_call_id: i64,
    /// FK → http_routes.id.
    pub to_route_id: i64,
    /// The matched URL.
    pub url: String,
    /// HTTP method: GET/POST/PUT/DELETE/PATCH.
    pub http_method: String,
    /// Match type: "exact" | "template" | "fuzzy".
    pub match_type: String,
    /// Confidence score 0.0 ~ 1.0.
    pub confidence: f64,
}

// ---------------------------------------------------------------------------
// FfiImportRecord — a detected FFI import from source code
// ---------------------------------------------------------------------------

/// A detected FFI (Foreign Function Interface) import from source code.
///
/// Maps to the `ffi_imports` table (created by v010 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FfiImportRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// The symbol name being imported via FFI.
    pub symbol_name: String,
    /// Rowid of the call node in `nodes` that performs the FFI import.
    pub call_node_id: i64,
    /// The import statement text, e.g. `extern "C" { fn foo(); }`.
    pub import_stmt: Option<String>,
    /// FFI framework: cffi / pyo3 / jni / napi / ffi / cpython / wasm / cgo / jna.
    pub ffi_framework: String,
    /// Source language of the calling code.
    pub source_lang: String,
    /// Project-relative file path.
    pub file_path: String,
    /// Start line number (1-based).
    pub line: i64,
    /// Start column number (1-based).
    pub column: i64,
    /// Raw source snippet for debugging.
    pub raw_snippet: Option<String>,
}

// ---------------------------------------------------------------------------
// FfiExportRecord — a detected FFI export definition
// ---------------------------------------------------------------------------

/// A detected FFI (Foreign Function Interface) export definition.
///
/// Maps to the `ffi_exports` table (created by v010 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FfiExportRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// The exported symbol name.
    pub symbol_name: String,
    /// Original (raw) symbol name before normalization.
    pub symbol_name_raw: Option<String>,
    /// Rowid of the function node in `nodes` that is exported via FFI.
    pub func_node_id: i64,
    /// FFI framework: cffi / pyo3 / jni / napi / ffi / cpython / wasm / cgo / jna.
    pub ffi_framework: String,
    /// Source language of the exporting code.
    pub source_lang: String,
    /// Project-relative file path.
    pub file_path: String,
    /// Start line number (1-based).
    pub line: i64,
    /// Start column number (1-based).
    pub column: i64,
    /// Raw source snippet for debugging.
    pub raw_snippet: Option<String>,
}

// ---------------------------------------------------------------------------
// FfiCrossEdgeRecord — a matched FFI import ↔ FFI export pair
// ---------------------------------------------------------------------------

/// A cross-language edge linking an FFI import to an FFI export.
///
/// Maps to the `ffi_cross_edges` table (created by v010 migration).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FfiCrossEdgeRecord {
    /// Auto-increment primary key (`None` during construction).
    pub id: Option<i64>,
    /// Source node id (the caller node that imports via FFI).
    pub from_node_id: i64,
    /// Target node id (the exported function node).
    pub to_node_id: i64,
    /// FK → ffi_imports.id.
    pub ffi_import_id: i64,
    /// FK → ffi_exports.id.
    pub ffi_export_id: i64,
    /// Edge kind — always "CROSS_FFI".
    pub edge_kind: String,
    /// The matched symbol name.
    pub symbol_name: String,
    /// FFI framework used for this edge.
    pub ffi_framework: String,
    /// Creation timestamp.
    pub created_at: String,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_node_record_serialization() {
        let node = NodeRecord {
            id: "abc123".to_string(),
            kind: "function".to_string(),
            name: "calculateTotal".to_string(),
            qualified_name: "src/utils.py::calculateTotal".to_string(),
            file_path: "src/utils.py".to_string(),
            language: "python".to_string(),
            start_line: 10,
            end_line: 25,
            signature: Some("def calculateTotal(items: list[float]) -> float".to_string()),
            docstring: Some("Calculate the total of all items".to_string()),
            visibility: Some("public".to_string()),
            is_abstract: 0,
            is_exported: 1,
            decorators: None,
            framework: None,
            properties: Some("{}".to_string()),
            body: Some("    return sum(items)".to_string()),
            body_hash: Some("deadbeef".to_string()),
            http_role: None,
            updated_at: 1719000000000,
        };

        let json = serde_json::to_string(&node).unwrap();
        let parsed: NodeRecord = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id, "abc123");
        assert_eq!(parsed.kind, "function");
        assert_eq!(parsed.name, "calculateTotal");
    }

    #[test]
    fn test_edge_record_defaults() {
        let edge = EdgeRecord {
            id: None,
            source: "abc123".to_string(),
            target: "def456".to_string(),
            target_text: Some("src/other.py::helper".to_string()),
            kind: "CALLS".to_string(),
            source_loc: Some("src/utils.py:15:5".to_string()),
            provenance: Some("tree-sitter".to_string()),
            properties: Some("{}".to_string()),
        };

        assert_eq!(edge.id, None);
        assert_eq!(edge.source, "abc123");
        assert_eq!(edge.kind, "CALLS");
    }

    #[test]
    fn test_file_record_fields() {
        let file = FileRecord {
            path: "src/main.py".to_string(),
            content_hash: "sha256hex".to_string(),
            language: "python".to_string(),
            node_count: 42,
            indexed_at: 1719000000000,
            size: 2048,
            modified_at: 1718900000,
        };

        assert_eq!(file.path, "src/main.py");
        assert_eq!(file.node_count, 42);
    }

    #[test]
    fn test_unresolved_ref_record() {
        let uref = UnresolvedRefRecord {
            id: None,
            from_node_id: "abc123".to_string(),
            reference_name: "missing_func".to_string(),
            reference_kind: "call".to_string(),
            line: 20,
            col: 5,
            candidates: None,
            source: Some("tree-sitter".to_string()),
            file_path: "src/utils.py".to_string(),
            language: "python".to_string(),
            is_external: 0,
        };

        assert_eq!(uref.reference_name, "missing_func");
        assert_eq!(uref.is_external, 0);
    }

    #[test]
    fn test_search_result_serialization() {
        let result = SearchResult {
            id: "abc123".to_string(),
            name: "calculateTotal".to_string(),
            qualified_name: "src/utils.py::calculateTotal".to_string(),
            kind: "function".to_string(),
            file_path: "src/utils.py".to_string(),
            language: "python".to_string(),
            signature: None,
            docstring: Some("Calculate total".to_string()),
            rank: Some(1.5),
            start_line: None,
        };

        let json = serde_json::to_string(&result).unwrap();
        let parsed: SearchResult = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.id, "abc123");
        assert!((parsed.rank.unwrap() - 1.5).abs() < 0.001);
    }
}
