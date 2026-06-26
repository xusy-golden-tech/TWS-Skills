//! PHP LSP adapter — configures intelephense.

use super::LspAdapter;

/// PHP LSP server configuration.
pub struct PhpLspAdapter;

impl LspAdapter for PhpLspAdapter {
    fn language(&self) -> &'static str {
        "php"
    }

    fn server_command(&self) -> &'static str {
        "intelephense"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["--stdio"]
    }

    fn install_hint(&self) -> &'static str {
        "npm install -g intelephense"
    }
}
