//! File-system poll watcher — periodically checks for file changes
//! and triggers incremental re-indexing.

use std::path::Path;
use std::time::Duration;

/// A poll-based file watcher.
pub struct PollWatcher {
    root: std::path::PathBuf,
    interval: Duration,
}

impl PollWatcher {
    /// Create a new poll watcher.
    pub fn new(root: &Path, interval: Duration) -> Self {
        Self {
            root: root.to_path_buf(),
            interval,
        }
    }

    /// Start watching (placeholder — runs once and returns).
    pub fn watch(&self) -> anyhow::Result<()> {
        // TODO: implement poll loop with stat-based change detection
        Ok(())
    }
}
