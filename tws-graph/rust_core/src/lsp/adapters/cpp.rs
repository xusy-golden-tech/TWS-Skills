//! C++ LSP adapter — configures clangd.

/// C++ LSP server configuration.
pub struct CppLspAdapter;

impl CppLspAdapter {
    pub fn server_command() -> &'static str {
        "clangd"
    }
    pub fn args() -> Vec<&'static str> {
        vec![]
    }
}
