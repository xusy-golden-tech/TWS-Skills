//! Python LSP adapter — configures pylsp (python-lsp-server) and pyright.

use super::LspAdapter;

/// Python LSP server configuration.
pub struct PythonLspAdapter;

impl PythonLspAdapter {
    /// Preferred command: pyright-langserver (richer type analysis).
    pub fn preferred_command() -> &'static str {
        "pyright-langserver"
    }
}

impl LspAdapter for PythonLspAdapter {
    fn language(&self) -> &'static str {
        "python"
    }

    fn server_command(&self) -> &'static str {
        "pylsp"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["--stdio"]
    }

    fn install_hint(&self) -> &'static str {
        "pip install python-lsp-server"
    }
}
