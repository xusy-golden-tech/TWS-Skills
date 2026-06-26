//! C# LSP adapter — configures OmniSharp / csharp-ls.

/// C# LSP server configuration.
pub struct CSharpLspAdapter;

impl CSharpLspAdapter {
    pub fn server_command() -> &'static str {
        "omnisharp"
    }
    pub fn args() -> Vec<&'static str> {
        vec!["--languageserver"]
    }
}
