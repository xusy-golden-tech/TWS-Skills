//! Database layer — SQLite connection management, schema & models.
//!
//! # Modules
//!
//! - [`models`] — `NodeRecord`, `EdgeRecord`, `FileRecord`,
//!   `UnresolvedRefRecord`, `SearchResult`.
//! - [`connection`] — `Database` handle + `hash_id` utility.
//! - [`migrations`] — 8 schema migrations (v001–v008) + `MigrationRunner`.

pub mod connection;
pub mod migrations;
pub mod models;

// Re-export the public API so consumers can use `db::Database` etc.
pub use connection::{hash_id, Database};
pub use models::{EdgeRecord, FileRecord, NodeRecord, SearchResult, UnresolvedRefRecord};
