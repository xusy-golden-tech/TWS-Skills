//! Indexer — scans source files, detects languages, dispatches to extractors.
//!
//! # Architecture
//!
//! ```text
//! scanner::scan_directory()
//!   → for each file:
//!       language::detect_language()
//!         → registry::find_by_extension() or find_by_language()
//!           → extractor.extract(source, tree, &mut ExtractionContext)
//! ```
//!
//! # Modules
//!
//! | Module | Purpose |
//! |--------|---------|
//! | [`scanner`] | File discovery via `git ls-files` or walkdir |
//! | [`language`] | Extension → language name mapping (30+ languages) |
//! | [`registry`] | Extractor registration & lookup |
//! | [`context`] | Per-file node/edge accumulation with scope management |
//! | [`extractors`] | Language-specific tree-sitter extractors (28+) |

pub mod context;
pub mod language;
pub mod registry;
pub mod scanner;

pub mod extractors;
pub mod parallel;
pub mod parser_pool;

// Re-export key types for convenience
pub use context::{ExtractionContext, ExtractionResult, ScopeInfo};
pub use language::{detect, detect_by_extension, detect_language};
pub use registry::Registry;
pub use scanner::scan_directory;
