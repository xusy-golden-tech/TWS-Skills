//! LSP manager — coordinates multiple language server instances.

use std::collections::HashMap;

use super::client::LspClient;

/// Manages a pool of LSP clients, one per language.
pub struct LspManager {
    clients: HashMap<String, LspClient>,
}

impl LspManager {
    pub fn new() -> Self {
        Self {
            clients: HashMap::new(),
        }
    }

    /// Get or start an LSP client for a language.
    pub fn get_client(&mut self, _language: &str) -> Option<&mut LspClient> {
        self.clients.get_mut(_language)
    }

    /// Shut down all managed clients.
    pub fn shutdown_all(&mut self) -> anyhow::Result<()> {
        for (_, client) in self.clients.iter_mut() {
            client.shutdown()?;
        }
        Ok(())
    }
}

impl Default for LspManager {
    fn default() -> Self {
        Self::new()
    }
}
