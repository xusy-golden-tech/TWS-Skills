//! Git hooks — auto-sync the code graph on commit / merge / checkout.

use std::path::Path;

/// Install git hooks for automatic index sync.
pub fn install(_repo_root: &Path) -> anyhow::Result<()> {
    // TODO: write post-commit, post-merge, post-checkout hook scripts
    Ok(())
}

/// Remove previously installed git hooks.
pub fn remove(_repo_root: &Path) -> anyhow::Result<()> {
    // TODO: delete hook scripts, no-op if absent
    Ok(())
}

/// Check whether hooks are currently installed.
pub fn status(_repo_root: &Path) -> bool {
    false
}
