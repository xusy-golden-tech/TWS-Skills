//! Ruby LSP adapter — configures Solargraph.

use super::LspAdapter;

/// Ruby LSP server configuration.
pub struct RubyLspAdapter;

impl LspAdapter for RubyLspAdapter {
    fn language(&self) -> &'static str {
        "ruby"
    }

    fn server_command(&self) -> &'static str {
        "solargraph"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["stdio"]
    }

    fn install_hint(&self) -> &'static str {
        "gem install solargraph"
    }
}
