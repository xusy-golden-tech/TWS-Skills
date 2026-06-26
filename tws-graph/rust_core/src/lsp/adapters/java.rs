//! Java LSP adapter — configures Eclipse JDT LS.

use super::LspAdapter;

/// Java LSP server configuration.
pub struct JavaLspAdapter;

impl LspAdapter for JavaLspAdapter {
    fn language(&self) -> &'static str {
        "java"
    }

    fn server_command(&self) -> &'static str {
        "jdtls"
    }

    fn args(&self) -> Vec<&'static str> {
        vec![]
    }

    fn install_hint(&self) -> &'static str {
        "See https://github.com/eclipse-jdtls/eclipse.jdt.ls for installation"
    }
}
