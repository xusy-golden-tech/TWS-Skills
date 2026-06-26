//! Python LSP adapter — configures Pyright / basedpyright.

/// Python LSP server configuration.
pub struct PythonLspAdapter;

impl PythonLspAdapter {
    pub fn server_command() -> &'static str {
        "pyright-langserver"
    }
    pub fn args() -> Vec<&'static str> {
        vec!["--stdio"]
    }
}
