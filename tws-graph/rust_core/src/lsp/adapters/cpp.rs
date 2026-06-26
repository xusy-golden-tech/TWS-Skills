//! C++ LSP adapter — configures clangd.

use super::LspAdapter;

/// C++ LSP server configuration.
pub struct CppLspAdapter;

impl LspAdapter for CppLspAdapter {
    fn language(&self) -> &'static str {
        "cpp"
    }

    fn server_command(&self) -> &'static str {
        "clangd"
    }

    fn args(&self) -> Vec<&'static str> {
        vec![]
    }

    fn install_hint(&self) -> &'static str {
        "Install clangd via your system package manager"
    }
}
