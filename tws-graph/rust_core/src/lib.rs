//! TWS CodeGraph — Rust core library with PyO3 bindings.
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

/// Verify PyO3 bridge is working.
#[pyfunction]
fn ping() -> PyResult<String> {
    Ok("pong".to_string())
}

/// The `_core` Python module — provides native-speed primitives for tws-graph CLI.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    Ok(())
}
