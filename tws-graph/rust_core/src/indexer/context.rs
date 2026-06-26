//! Extraction context — accumulates nodes and edges during a parse run.

use crate::db::models::{EdgeRecord, NodeRecord};

/// Mutable context passed through each extractor invocation.
///
/// Collects `NodeRecord`s and `EdgeRecord`s as the extractor walks
/// a tree-sitter CST.  Node IDs are **not** assigned here — they are
/// computed by the caller via `db::hash_id(file_path, qualified_name)`
/// before insertion.
pub struct ExtractionContext {
    /// Project-relative path of the file being indexed.
    pub file_path: String,

    /// Detected language (e.g. `"python"`).
    pub language: String,

    /// Nodes discovered so far.
    pub nodes: Vec<NodeRecord>,

    /// Edges discovered so far.
    pub edges: Vec<EdgeRecord>,
}

impl ExtractionContext {
    pub fn new(file_path: String, language: String) -> Self {
        Self {
            file_path,
            language,
            nodes: Vec::new(),
            edges: Vec::new(),
        }
    }
}
