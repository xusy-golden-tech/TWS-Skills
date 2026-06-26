//! LSP client — communicates with external language servers via stdio.

/// A generic LSP client that spawns and communicates with a language server.
pub struct LspClient {
    // TODO: hold child process handle, stdin writer, stdout reader
}

impl LspClient {
    pub fn new(_command: &str, _args: &[&str]) -> anyhow::Result<Self> {
        Ok(Self {})
    }

    /// Send an initialize request.
    pub fn initialize(&mut self, _root_uri: &str) -> anyhow::Result<()> {
        Ok(())
    }

    /// Request document symbols.
    pub fn document_symbols(&mut self, _file_uri: &str) -> anyhow::Result<Vec<String>> {
        Ok(Vec::new())
    }

    /// Shut down the server.
    pub fn shutdown(&mut self) -> anyhow::Result<()> {
        Ok(())
    }
}
