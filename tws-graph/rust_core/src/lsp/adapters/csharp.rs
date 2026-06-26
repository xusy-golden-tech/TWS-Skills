//! C# LSP adapter — configures OmniSharp / csharp-ls.

use super::LspAdapter;

/// C# LSP server configuration.
pub struct CSharpLspAdapter;

impl LspAdapter for CSharpLspAdapter {
    fn language(&self) -> &'static str {
        "csharp"
    }

    fn server_command(&self) -> &'static str {
        "omnisharp"
    }

    fn args(&self) -> Vec<&'static str> {
        vec!["--languageserver"]
    }

    fn install_hint(&self) -> &'static str {
        "Install OmniSharp: https://github.com/OmniSharp/omnisharp-roslyn"
    }
}
