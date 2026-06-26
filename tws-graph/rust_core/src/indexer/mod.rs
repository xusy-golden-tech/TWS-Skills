//! Indexer — scans source files, detects languages, dispatches to extractors.

pub mod context;
pub mod language;
pub mod registry;
pub mod scanner;

pub mod extractors;
