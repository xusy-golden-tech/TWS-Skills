//! TypeScript LSP adapter — configures TypeScript Language Server.

/// TypeScript LSP server configuration.
pub struct TypeScriptLspAdapter;

impl TypeScriptLspAdapter {
    pub fn server_command() -> &'static str {
        "typescript-language-server"
    }
    pub fn args() -> Vec<&'static str> {
        vec!["--stdio"]
    }
}
