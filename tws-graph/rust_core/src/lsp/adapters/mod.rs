//! LSP adapters — language-specific LSP server configurations.
//!
//! Each adapter knows the canonical LSP server command, install instructions,
//! and per-language initialization options.
//!
//! The `LspAdapter` trait provides a uniform interface for all language
//! adapters so the `LspManager` can query server availability without
//! hard-coding language names.

pub mod c;
pub mod cpp;
pub mod csharp;
pub mod go;
pub mod java;
pub mod php;
pub mod python;
pub mod ruby;
pub mod rust;
pub mod typescript;

/// Trait for language-specific LSP adapters.
///
/// Each language adapter implements this trait so the `LspManager` can
/// uniformly start, stop, and query language servers.
pub trait LspAdapter {
    /// Human-readable language identifier (e.g. "python", "typescript").
    fn language(&self) -> &'static str;

    /// The LSP server binary name (searchable on PATH).
    fn server_command(&self) -> &'static str;

    /// Command-line arguments to pass to the server.
    fn args(&self) -> Vec<&'static str> {
        vec!["--stdio"]
    }

    /// Human-readable install hint for users who lack the server.
    fn install_hint(&self) -> &'static str;
}
