//! Rust LSP adapter — configures rust-analyzer.

use super::LspAdapter;

/// Rust LSP server configuration.
pub struct RustLspAdapter;

impl LspAdapter for RustLspAdapter {
    fn language(&self) -> &'static str {
        "rust"
    }

    fn server_command(&self) -> &'static str {
        "rust-analyzer"
    }

    fn args(&self) -> Vec<&'static str> {
        vec![]
    }

    fn install_hint(&self) -> &'static str {
        "rustup component add rust-analyzer"
    }
}
