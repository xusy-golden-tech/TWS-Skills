//! Extraction context — accumulates nodes and edges during a parse run.

use crate::db::models::{EdgeRecord, NodeRecord};

/// Mutable context passed through each extractor invocation.
///
/// Collects `NodeRecord`s and `EdgeRecord`s as the extractor walks
/// a tree-sitter CST.
pub struct ExtractionContext {
    /// Absolute or repository-relative path of the file being indexed.
    pub file_path: String,

    /// Detected language (e.g. `"python"`).
    pub language: String,

    /// Nodes discovered so far.
    pub nodes: Vec<NodeRecord>,

    /// Edges discovered so far.
    pub edges: Vec<EdgeRecord>,

    /// Next synthetic node id (negative to avoid collision with DB ids).
    next_id: i64,
}

impl ExtractionContext {
    pub fn new(file_path: String, language: String) -> Self {
        Self {
            file_path,
            language,
            nodes: Vec::new(),
            edges: Vec::new(),
            next_id: -1,
        }
    }

    /// Allocate a temporary negative id for a new node.
    pub fn next_temp_id(&mut self) -> i64 {
        let id = self.next_id;
        self.next_id -= 1;
        id
    }
}
