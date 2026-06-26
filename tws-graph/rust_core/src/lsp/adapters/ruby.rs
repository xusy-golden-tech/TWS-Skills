//! Ruby LSP adapter — configures Solargraph.

/// Ruby LSP server configuration.
pub struct RubyLspAdapter;

impl RubyLspAdapter {
    pub fn server_command() -> &'static str {
        "solargraph"
    }
    pub fn args() -> Vec<&'static str> {
        vec!["stdio"]
    }
}
