//! Java LSP adapter — configures Eclipse JDT LS.

/// Java LSP server configuration.
pub struct JavaLspAdapter;

impl JavaLspAdapter {
    pub fn server_command() -> &'static str {
        "jdtls"
    }
    pub fn args() -> Vec<&'static str> {
        vec![]
    }
}
