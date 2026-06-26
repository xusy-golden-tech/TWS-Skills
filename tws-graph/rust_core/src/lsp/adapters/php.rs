//! PHP LSP adapter — configures Intelephense.

/// PHP LSP server configuration.
pub struct PhpLspAdapter;

impl PhpLspAdapter {
    pub fn server_command() -> &'static str {
        "intelephense"
    }
    pub fn args() -> Vec<&'static str> {
        vec!["--stdio"]
    }
}
