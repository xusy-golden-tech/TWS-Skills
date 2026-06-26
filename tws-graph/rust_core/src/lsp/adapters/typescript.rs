//! TypeScript LSP adapter — configures typescript-language-server.

use super::LspAdapter;

/// TypeScript LSP server configuration.
pub struct TypeScriptLspAdapter;

impl LspAdapter for TypeScriptLspAdapter {
    fn language(&self) -> &'static str {
        "typescript"
    }

    fn server_command(&self) -> &'static str {
        "typescript-language-server"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["--stdio"]
    }

    fn install_hint(&self) -> &'static str {
        "npm install -g typescript-language-server typescript"
    }
}
