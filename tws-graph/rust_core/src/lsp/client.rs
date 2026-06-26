//! LSP client — communicates with external language servers via stdio.
//!
//! Spawns a language server subprocess and communicates using JSON-RPC 2.0
//! over stdin / stdout.  This is a simplified client: it supports server
//! start, initialize, and shutdown, but full async bidirectional
//! communication (handling server-to-client notifications) requires a
//! more elaborate message loop that is deferred to a future version.

use std::io::{BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};

use super::protocol::JsonRpcMessage;

/// A generic LSP client that spawns and communicates with a language server.
pub struct LspClient {
    /// The child process handle.
    process: Child,
    /// Auto-incrementing JSON-RPC request id.
    next_id: i64,
}

impl LspClient {
    /// Start a language server process.
    ///
    /// Spawns `command` with the given `args`, piping stdin/stdout for
    /// JSON-RPC communication.  Stderr is piped separately for diagnostics.
    pub fn start(command: &str, args: &[String]) -> anyhow::Result<Self> {
        let process = Command::new(command)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| {
                anyhow::anyhow!(
                    "Failed to spawn LSP server '{}': {}. Is it installed and on PATH?",
                    command,
                    e
                )
            })?;

        Ok(Self {
            process,
            next_id: 1,
        })
    }

    /// Send an `initialize` request to the LSP server.
    ///
    /// This tells the server the project root URI and client capabilities.
    /// Must be called before any other requests.
    pub fn initialize(&mut self, root: &Path) -> anyhow::Result<()> {
        let root_uri = path_to_uri(root);

        let params = serde_json::json!({
            "processId": std::process::id(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": { "dynamicRegistration": true },
                    "references": { "dynamicRegistration": true }
                }
            }
        });

        let response = self.send_request("initialize", params)?;
        let _ = response; // Ignore capabilities for now

        // Send initialized notification
        self.send_notification("initialized", serde_json::json!({}))?;

        Ok(())
    }

    /// Request go-to-definition for a symbol at `(file, line, col)`.
    ///
    /// Returns a list of `Location` objects (uri + range).
    pub fn definition(
        &mut self,
        file: &str,
        line: u32,
        col: u32,
    ) -> anyhow::Result<Vec<Location>> {
        let file_uri = path_to_uri(Path::new(file));

        let params = serde_json::json!({
            "textDocument": { "uri": file_uri },
            "position": { "line": line, "character": col }
        });

        let response = self.send_request("textDocument/definition", params)?;

        // Parse locations from response
        parse_locations(&response)
    }

    /// Request find-references for a symbol at `(file, line, col)`.
    pub fn references(
        &mut self,
        file: &str,
        line: u32,
        col: u32,
    ) -> anyhow::Result<Vec<Location>> {
        let file_uri = path_to_uri(Path::new(file));

        let params = serde_json::json!({
            "textDocument": { "uri": file_uri },
            "position": { "line": line, "character": col },
            "context": { "includeDeclaration": false }
        });

        let response = self.send_request("textDocument/references", params)?;
        parse_locations(&response)
    }

    /// Send a JSON-RPC request and wait for the response.
    fn send_request(
        &mut self,
        method: &str,
        params: serde_json::Value,
    ) -> anyhow::Result<serde_json::Value> {
        let id = self.next_id;
        self.next_id += 1;

        let request = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });

        let request_str = serde_json::to_string(&request)?;
        let content_length = request_str.len();

        // Write header + content
        {
            let stdin = self
                .process
                .stdin
                .as_mut()
                .ok_or_else(|| anyhow::anyhow!("LSP stdin not available"))?;

            write!(stdin, "Content-Length: {}\r\n\r\n{}", content_length, request_str)?;
            stdin.flush()?;
        }

        // Read response
        {
            let stdout = self
                .process
                .stdout
                .as_mut()
                .ok_or_else(|| anyhow::anyhow!("LSP stdout not available"))?;
            let mut reader = BufReader::new(stdout);

            // Read Content-Length header
            let mut header = String::new();
            reader.read_line(&mut header)?;
            let header = header.trim();
            if !header.to_lowercase().starts_with("content-length:") {
                return Err(anyhow::anyhow!("Unexpected LSP header: {}", header));
            }
            let len_str = header["content-length:".len()..].trim();
            let len: usize = len_str.parse()?;

            // Read blank line
            let mut blank = String::new();
            reader.read_line(&mut blank)?;

            // Read body
            let mut body = vec![0u8; len];
            reader.read_exact(&mut body)?;

            let response: JsonRpcMessage = serde_json::from_slice(&body)?;

            if let Some(err) = response.error {
                return Err(anyhow::anyhow!(
                    "LSP error {}: {}",
                    err.code,
                    err.message
                ));
            }

            Ok(response.result.unwrap_or(serde_json::Value::Null))
        }
    }

    /// Send a JSON-RPC notification (no response expected).
    fn send_notification(
        &mut self,
        method: &str,
        params: serde_json::Value,
    ) -> anyhow::Result<()> {
        let notification = serde_json::json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });

        let notif_str = serde_json::to_string(&notification)?;
        let content_length = notif_str.len();

        let stdin = self
            .process
            .stdin
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("LSP stdin not available"))?;

        write!(stdin, "Content-Length: {}\r\n\r\n{}", content_length, notif_str)?;
        stdin.flush()?;

        Ok(())
    }

    /// Shut down the server gracefully.
    ///
    /// Sends `shutdown` request, then `exit` notification, then waits for
    /// the child process to terminate.
    pub fn shutdown(mut self) -> anyhow::Result<()> {
        // Try to send shutdown request — ignore errors since the process
        // may have already exited.
        let _ = self.send_request("shutdown", serde_json::Value::Null);
        let _ = self.send_notification("exit", serde_json::Value::Null);

        // Wait for the process to exit (with timeout)
        let _ = self.process.wait();
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// A location in a source file (LSP `Location`).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Location {
    /// File URI (e.g. "file:///home/user/project/src/main.py").
    pub uri: String,
    /// Start line (0-based).
    pub start_line: u32,
    /// Start column (0-based).
    pub start_col: u32,
    /// End line (0-based).
    pub end_line: u32,
    /// End column (0-based).
    pub end_col: u32,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Convert a file path to a `file://` URI.
fn path_to_uri(path: &Path) -> String {
    let abs = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| Path::new(".").to_path_buf())
            .join(path)
    };

    let canonical = abs.canonicalize().unwrap_or(abs);

    // On Windows, we need to handle the drive letter specially.
    let path_str = canonical.to_string_lossy().replace('\\', "/");

    if cfg!(target_os = "windows") {
        format!("file:///{}", path_str)
    } else {
        format!("file://{}", path_str)
    }
}

/// Parse LSP `Location` array from a JSON-RPC response.
fn parse_locations(response: &serde_json::Value) -> anyhow::Result<Vec<Location>> {
    match response {
        serde_json::Value::Null => Ok(Vec::new()),
        serde_json::Value::Array(arr) => {
            let mut locations = Vec::new();
            for item in arr {
                if let Some(loc) = parse_single_location(item) {
                    locations.push(loc);
                }
            }
            Ok(locations)
        }
        // Single location object
        obj if obj.is_object() => {
            if let Some(loc) = parse_single_location(obj) {
                Ok(vec![loc])
            } else {
                Ok(Vec::new())
            }
        }
        _ => Ok(Vec::new()),
    }
}

/// Parse a single LSP `Location` object.
fn parse_single_location(value: &serde_json::Value) -> Option<Location> {
    let uri = value.get("uri")?.as_str()?.to_string();
    let range = value.get("range")?;
    let start = range.get("start")?;
    let end = range.get("end")?;

    Some(Location {
        uri,
        start_line: start.get("line")?.as_u64()? as u32,
        start_col: start.get("character")?.as_u64()? as u32,
        end_line: end.get("line")?.as_u64()? as u32,
        end_col: end.get("character")?.as_u64()? as u32,
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_path_to_uri_relative() {
        let uri = path_to_uri(Path::new("src/main.py"));
        assert!(uri.starts_with("file://"));
        assert!(uri.ends_with("src/main.py"));
    }

    #[test]
    fn test_parse_locations_null() {
        let locs = parse_locations(&serde_json::Value::Null).unwrap();
        assert!(locs.is_empty());
    }

    #[test]
    fn test_parse_locations_array() {
        let json = serde_json::json!([
            {
                "uri": "file:///src/main.py",
                "range": {
                    "start": {"line": 10, "character": 5},
                    "end": {"line": 10, "character": 12}
                }
            }
        ]);
        let locs = parse_locations(&json).unwrap();
        assert_eq!(locs.len(), 1);
        assert_eq!(locs[0].uri, "file:///src/main.py");
        assert_eq!(locs[0].start_line, 10);
        assert_eq!(locs[0].start_col, 5);
        assert_eq!(locs[0].end_line, 10);
        assert_eq!(locs[0].end_col, 12);
    }

    #[test]
    fn test_parse_locations_single_object() {
        let json = serde_json::json!({
            "uri": "file:///src/lib.rs",
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 1}
            }
        });
        let locs = parse_locations(&json).unwrap();
        assert_eq!(locs.len(), 1);
        assert_eq!(locs[0].uri, "file:///src/lib.rs");
    }

    #[test]
    fn test_parse_locations_empty_array() {
        let locs = parse_locations(&serde_json::json!([])).unwrap();
        assert!(locs.is_empty());
    }

    #[test]
    fn test_start_nonexistent_command() {
        let result = LspClient::start("this_command_does_not_exist_xyz", &[]);
        assert!(result.is_err());
    }

    #[test]
    fn test_location_serialization() {
        let loc = Location {
            uri: "file:///test.py".to_string(),
            start_line: 5,
            start_col: 2,
            end_line: 5,
            end_col: 10,
        };
        let json = serde_json::to_string(&loc).unwrap();
        let parsed: Location = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.uri, loc.uri);
        assert_eq!(parsed.start_line, loc.start_line);
    }
}
