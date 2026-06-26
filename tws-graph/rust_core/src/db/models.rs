//! Data models for the code-graph SQLite schema.
//!
//! `NodeRecord`, `EdgeRecord`, `FileRecord` are the primary row types.

use serde::{Deserialize, Serialize};

/// A node (symbol) stored in the graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeRecord {
    pub id: i64,
    pub kind: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub start_col: u32,
    pub end_col: u32,
    pub language: String,
    pub docstring: Option<String>,
    pub signature: Option<String>,
    pub body_hash: Option<String>,
}

/// An edge (relationship) between two nodes.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeRecord {
    pub id: i64,
    pub source_id: i64,
    pub target_id: i64,
    pub kind: String,
    pub weight: f64,
    pub provenance: Option<String>,
}

/// Metadata for an indexed source file.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileRecord {
    pub id: i64,
    pub path: String,
    pub language: String,
    pub checksum: String,
    pub last_indexed_at: String,
    pub node_count: u32,
    pub edge_count: u32,
}
