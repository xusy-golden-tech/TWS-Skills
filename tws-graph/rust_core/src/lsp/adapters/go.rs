//! Go LSP adapter — configures gopls.

use super::LspAdapter;

/// Go LSP server configuration.
pub struct GoLspAdapter;

impl LspAdapter for GoLspAdapter {
    fn language(&self) -> &'static str {
        "go"
    }

    fn server_command(&self) -> &'static str {
        "gopls"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["serve", "-listen", "stdio"]
    }

    fn install_hint(&self) -> &'static str {
        "go install golang.org/x/tools/gopls@latest"
    }
}
