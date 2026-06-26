//! Snapshot diff — compares two named snapshots and reports added/removed/changed.

use serde::{Deserialize, Serialize};

/// Result of comparing two snapshots.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct SnapshotDiff {
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub changed: Vec<String>,
    pub unchanged: usize,
}

/// Compare two snapshots by name.
pub fn compare(_before: &str, _after: &str) -> anyhow::Result<SnapshotDiff> {
    // TODO: load snapshot data from DB and compute symmetric difference
    Ok(SnapshotDiff::default())
}

/// List available snapshot names.
pub fn list_snapshots() -> Vec<String> {
    Vec::new()
}
