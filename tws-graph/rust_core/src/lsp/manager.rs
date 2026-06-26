//! LSP manager — coordinates multiple language server instances.
//!
//! Provides server detection (checking PATH for known LSP binaries) and
//! lifecycle management (start, stop, query availability).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::adapters::{
    self, LspAdapter,
    c::CLspAdapter,
    cpp::CppLspAdapter,
    csharp::CSharpLspAdapter,
    go::GoLspAdapter,
    java::JavaLspAdapter,
    php::PhpLspAdapter,
    python::PythonLspAdapter,
    ruby::RubyLspAdapter,
    rust::RustLspAdapter,
    typescript::TypeScriptLspAdapter,
};
use super::client::LspClient;

/// Manages a pool of LSP clients, one per language.
pub struct LspManager {
    /// Map: language -> (available_on_path, install_hint)
    available_servers: HashMap<String, (bool, String)>,
}

impl LspManager {
    /// Create a new LSP manager (no detection performed yet).
    pub fn new() -> Self {
        Self {
            available_servers: HashMap::new(),
        }
    }

    /// Detect all known LSP servers by checking their binaries on PATH.
    ///
    /// Returns a manager populated with availability info for all known
    /// language servers.  This is the primary constructor.
    pub fn detect() -> Self {
        let adapters: Vec<Box<dyn LspAdapter>> = vec![
            Box::new(CLspAdapter),
            Box::new(CppLspAdapter),
            Box::new(CSharpLspAdapter),
            Box::new(GoLspAdapter),
            Box::new(JavaLspAdapter),
            Box::new(PhpLspAdapter),
            Box::new(PythonLspAdapter),
            Box::new(RubyLspAdapter),
            Box::new(RustLspAdapter),
            Box::new(TypeScriptLspAdapter),
        ];

        let mut available_servers = HashMap::new();

        for adapter in &adapters {
            let cmd = adapter.server_command();
            let available = is_binary_on_path(cmd);
            available_servers.insert(
                adapter.language().to_string(),
                (available, adapter.install_hint().to_string()),
            );
        }

        Self { available_servers }
    }

    /// Check whether an LSP server is available for the given language.
    ///
    /// Returns `true` if the binary is on PATH; `false` otherwise.
    /// Unknown languages return `false`.
    pub fn is_available(&self, language: &str) -> bool {
        self.available_servers
            .get(language)
            .map(|(available, _)| *available)
            .unwrap_or(false)
    }

    /// List all detected servers with their availability and install hints.
    ///
    /// Returns `Vec<(language, available, install_hint)>`.
    pub fn list_servers(&self) -> Vec<(String, bool, Option<String>)> {
        let mut results: Vec<(String, bool, Option<String>)> = self
            .available_servers
            .iter()
            .map(|(lang, (available, hint))| {
                (lang.clone(), *available, Some(hint.clone()))
            })
            .collect();
        results.sort_by(|a, b| a.0.cmp(&b.0));
        results
    }

    /// Start an LSP server for the given language, rooted at `root`.
    ///
    /// Returns an `LspClient` if the server binary is available and can be
    /// spawned; returns `Err` if the binary is not on PATH or the process
    /// fails to start.
    pub fn start_server(&self, language: &str, root: &Path) -> anyhow::Result<LspClient> {
        let (cmd, args) = match language {
            "c" => (CLspAdapter.server_command(), CLspAdapter.args()),
            "cpp" => (CppLspAdapter.server_command(), CppLspAdapter.args()),
            "csharp" => (CSharpLspAdapter.server_command(), CSharpLspAdapter.args()),
            "go" => (GoLspAdapter.server_command(), GoLspAdapter.args()),
            "java" => (JavaLspAdapter.server_command(), JavaLspAdapter.args()),
            "php" => (PhpLspAdapter.server_command(), PhpLspAdapter.args()),
            "python" => (PythonLspAdapter.server_command(), PythonLspAdapter.args()),
            "ruby" => (RubyLspAdapter.server_command(), RubyLspAdapter.args()),
            "rust" => (RustLspAdapter.server_command(), RustLspAdapter.args()),
            "typescript" => (TypeScriptLspAdapter.server_command(), TypeScriptLspAdapter.args()),
            _ => return Err(anyhow::anyhow!("No LSP adapter for language: {}", language)),
        };

        if !is_binary_on_path(cmd) {
            return Err(anyhow::anyhow!(
                "LSP server '{}' not found on PATH for language '{}'",
                cmd,
                language
            ));
        }

        let string_args: Vec<String> = args.iter().map(|s| s.to_string()).collect();
        let client = LspClient::start(cmd, &string_args)?;

        // TODO: in a full implementation we would call client.initialize(root)
        // after the client is spawned, but stdio JSON-RPC requires async I/O.
        // For now we just verify the process started successfully.
        let _ = root;

        Ok(client)
    }

    /// Format server list as a table string (like `tws-graph lsp setup` output).
    ///
    /// Returns a human-readable table of all detected servers.
    pub fn format_setup_table(&self) -> String {
        let servers = self.list_servers();
        if servers.is_empty() {
            return "No LSP servers detected.".to_string();
        }

        let mut out = String::from("LSP Server Availability:\n");
        out.push_str(&format!(
            "  {:<16} {:<12} {}\n",
            "LANGUAGE", "STATUS", "INSTALL HINT"
        ));
        out.push_str(&format!(
            "  {:<16} {:<12} {}\n",
            "--------", "------", "-----------"
        ));

        for (lang, available, hint) in &servers {
            let status = if *available { "AVAILABLE" } else { "NOT FOUND" };
            out.push_str(&format!(
                "  {:<16} {:<12} {}\n",
                lang,
                status,
                hint.as_deref().unwrap_or("")
            ));
        }

        out
    }

    /// Shut down all managed clients (no-op for detection-only manager).
    pub fn shutdown_all(&mut self) -> anyhow::Result<()> {
        // In detection-only mode, no clients are tracked.
        // If we add client tracking in the future, shut them down here.
        Ok(())
    }
}

impl Default for LspManager {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Utility: PATH lookup
// ---------------------------------------------------------------------------

/// Check whether a binary with the given name is accessible on the system PATH.
///
/// On Unix this checks via `which`; on Windows it checks via `where`.
fn is_binary_on_path(binary: &str) -> bool {
    // On Windows, the binary may have a .cmd or .exe extension.
    // `which` on git-bash handles the extension resolution.
    which_binary(binary).is_some()
}

/// Locate a binary on PATH, returning its full path if found.
pub fn which_binary(binary: &str) -> Option<PathBuf> {
    let cmd = if cfg!(target_os = "windows") { "where" } else { "which" };

    let output = std::process::Command::new(cmd)
        .arg(binary)
        .output()
        .ok()?;

    if output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        let first_line = stdout.lines().next()?;
        let trimmed = first_line.trim();
        if !trimmed.is_empty() {
            return Some(PathBuf::from(trimmed));
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_does_not_panic() {
        let mgr = LspManager::detect();
        let servers = mgr.list_servers();
        // Should have entries for all 10 languages
        assert!(servers.len() >= 10);
    }

    #[test]
    fn test_list_servers_returns_sorted() {
        let mgr = LspManager::detect();
        let servers = mgr.list_servers();
        for window in servers.windows(2) {
            assert!(window[0].0 <= window[1].0, "servers should be sorted by language");
        }
    }

    #[test]
    fn test_is_available_unknown_language() {
        let mgr = LspManager::detect();
        assert!(!mgr.is_available("this_language_does_not_exist"));
    }

    #[test]
    fn test_is_available_known_language_returns_bool() {
        let mgr = LspManager::detect();
        // Rust-analyzer or clangd may or may not be available — just check it's a bool.
        let _ = mgr.is_available("rust");
        let _ = mgr.is_available("python");
    }

    #[test]
    fn test_format_setup_table_is_not_empty() {
        let mgr = LspManager::detect();
        let table = mgr.format_setup_table();
        assert!(!table.is_empty());
        assert!(table.contains("LSP Server Availability"));
        assert!(table.contains("LANGUAGE"));
        assert!(table.contains("STATUS"));
    }

    #[test]
    fn test_which_binary_common_command() {
        // `which` itself should always be found on Unix-like systems
        let result = which_binary(if cfg!(target_os = "windows") { "cmd" } else { "sh" });
        assert!(result.is_some(), "Expected to find cmd/sh on PATH");
    }

    #[test]
    fn test_which_binary_nonexistent() {
        let result = which_binary("this_binary_does_not_exist_xyz123");
        assert!(result.is_none());
    }

    #[test]
    fn test_start_server_unknown_language() {
        let mgr = LspManager::detect();
        let result = mgr.start_server("unknown_lang", Path::new("."));
        assert!(result.is_err());
    }

    #[test]
    fn test_default_manager_has_empty_servers() {
        let mgr = LspManager::default();
        assert!(mgr.list_servers().is_empty());
    }
}
