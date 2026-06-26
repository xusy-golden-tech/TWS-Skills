//! Git hooks — auto-sync the code graph on commit / merge / checkout.
//!
//! Installs three hook scripts into `.git/hooks/` that run `tws-graph sync`
//! automatically after relevant git operations. The hooks are:
//!
//! - `post-commit`  — sync after every commit
//! - `post-merge`   — sync after merge / pull
//! - `post-checkout` — sync after branch switch
//!
//! Each hook runs `tws-graph sync` with the project root as the working
//! directory. Hooks are written as shell scripts (bash on Unix, batch on
//! Windows) that invoke the `tws-graph` CLI.

use std::path::{Path, PathBuf};

/// Install git hooks for automatic index sync.
///
/// Creates `post-commit`, `post-merge`, and `post-checkout` hook scripts
/// in `.git/hooks/`. Pre-existing hooks of the same name are backed up as
/// `.bak` before overwriting.
pub fn install_hooks(repo_path: &Path) -> anyhow::Result<()> {
    let hooks_dir = repo_path.join(".git").join("hooks");

    if !hooks_dir.exists() {
        return Err(anyhow::anyhow!(
            "No .git/hooks directory found at {}. Is this a git repository?",
            hooks_dir.display()
        ));
    }

    let hooks = [
        ("post-commit", post_commit_script()),
        ("post-merge", post_merge_script()),
        ("post-checkout", post_checkout_script()),
    ];

    for (name, script) in &hooks {
        let hook_path = hooks_dir.join(name);

        // Back up existing hook if present
        if hook_path.exists() {
            let backup_path = hooks_dir.join(format!("{}.bak", name));
            std::fs::copy(&hook_path, &backup_path)?;
        }

        std::fs::write(&hook_path, script)?;

        // Make hook executable on Unix
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = std::fs::metadata(&hook_path)?.permissions();
            perms.set_mode(0o755);
            std::fs::set_permissions(&hook_path, perms)?;
        }
    }

    Ok(())
}

/// Remove previously installed git hooks.
///
/// Deletes `post-commit`, `post-merge`, and `post-checkout` hook scripts.
/// Restores `.bak` backups if they exist.
pub fn uninstall_hooks(repo_path: &Path) -> anyhow::Result<()> {
    let hooks_dir = repo_path.join(".git").join("hooks");

    if !hooks_dir.exists() {
        return Ok(());
    }

    let hooks = ["post-commit", "post-merge", "post-checkout"];

    for name in &hooks {
        let hook_path = hooks_dir.join(name);
        let backup_path = hooks_dir.join(format!("{}.bak", name));

        // Remove the hook
        if hook_path.exists() {
            std::fs::remove_file(&hook_path)?;
        }

        // Restore backup if it exists
        if backup_path.exists() {
            std::fs::rename(&backup_path, &hook_path)?;
        }
    }

    Ok(())
}

/// Check whether hooks are currently installed.
///
/// Returns a descriptive string indicating the status of each hook.
pub fn hooks_status(repo_path: &Path) -> String {
    let hooks_dir = repo_path.join(".git").join("hooks");

    if !hooks_dir.exists() {
        return String::from("Hooks status: not a git repository (no .git/hooks directory)");
    }

    let hooks = ["post-commit", "post-merge", "post-checkout"];
    let mut lines = vec![String::from("Hooks status:")];

    for name in &hooks {
        let hook_path = hooks_dir.join(name);
        let backup_path = hooks_dir.join(format!("{}.bak", name));

        if hook_path.exists() {
            let size = std::fs::metadata(&hook_path)
                .map(|m| m.len())
                .unwrap_or(0);
            lines.push(format!("  {}: installed ({} bytes)", name, size));
        } else if backup_path.exists() {
            lines.push(format!("  {}: backed up (not active)", name));
        } else {
            lines.push(format!("  {}: not installed", name));
        }
    }

    lines.join("\n")
}

/// Generate the post-commit hook script.
///
/// Runs `tws-graph sync` in the repo root after each commit.
fn post_commit_script() -> String {
    format!(
        r#"#!/usr/bin/env bash
# tws-graph post-commit hook — auto-sync code graph after commit
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '')"
if [ -n "$REPO_ROOT" ]; then
    cd "$REPO_ROOT"
    tws-graph sync 2>/dev/null || true
fi
"#
    )
}

/// Generate the post-merge hook script.
///
/// Runs `tws-graph sync` in the repo root after merge or pull.
fn post_merge_script() -> String {
    format!(
        r#"#!/usr/bin/env bash
# tws-graph post-merge hook — auto-sync code graph after merge/pull
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '')"
if [ -n "$REPO_ROOT" ]; then
    cd "$REPO_ROOT"
    tws-graph sync 2>/dev/null || true
fi
"#
    )
}

/// Generate the post-checkout hook script.
///
/// Runs `tws-graph sync` in the repo root after branch switch.
fn post_checkout_script() -> String {
    format!(
        r#"#!/usr/bin/env bash
# tws-graph post-checkout hook — auto-sync code graph after checkout
set -euo pipefail
PREV_HEAD="$1"
NEW_HEAD="$2"
BRANCH_SWITCH="$3"

# Only sync on branch switches (3rd arg = 1), not file checkouts
if [ "$BRANCH_SWITCH" = "1" ]; then
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '')"
    if [ -n "$REPO_ROOT" ]; then
        cd "$REPO_ROOT"
        tws-graph sync 2>/dev/null || true
    fi
fi
"#
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn setup_temp_repo(name: &str) -> (PathBuf, PathBuf) {
        let dir = std::env::temp_dir().join(format!("tws_hooks_test_{}", name));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();

        // Create minimal git repo structure
        let git_dir = dir.join(".git");
        let hooks_dir = git_dir.join("hooks");
        std::fs::create_dir_all(&hooks_dir).unwrap();
        std::fs::create_dir_all(git_dir.join("objects")).unwrap();
        std::fs::create_dir_all(git_dir.join("refs")).unwrap();
        std::fs::write(git_dir.join("HEAD"), "ref: refs/heads/master\n").unwrap();

        (dir, hooks_dir)
    }

    fn cleanup(dir: &Path) {
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn test_install_hooks_creates_files() {
        let (repo_path, hooks_dir) = setup_temp_repo("install");
        install_hooks(&repo_path).unwrap();

        assert!(hooks_dir.join("post-commit").exists());
        assert!(hooks_dir.join("post-merge").exists());
        assert!(hooks_dir.join("post-checkout").exists());

        cleanup(&repo_path);
    }

    #[test]
    fn test_install_hooks_content() {
        let (repo_path, hooks_dir) = setup_temp_repo("content");
        install_hooks(&repo_path).unwrap();

        let content = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();
        assert!(content.contains("tws-graph sync"));
        assert!(content.contains("post-commit hook"));

        cleanup(&repo_path);
    }

    #[test]
    fn test_uninstall_hooks_removes_files() {
        let (repo_path, hooks_dir) = setup_temp_repo("uninstall");
        install_hooks(&repo_path).unwrap();
        uninstall_hooks(&repo_path).unwrap();

        assert!(!hooks_dir.join("post-commit").exists());
        assert!(!hooks_dir.join("post-merge").exists());
        assert!(!hooks_dir.join("post-checkout").exists());

        cleanup(&repo_path);
    }

    #[test]
    fn test_uninstall_restores_backup() {
        let (repo_path, hooks_dir) = setup_temp_repo("restore");

        // Create a pre-existing hook
        let original = "#!/bin/bash\necho original hook\n";
        std::fs::write(hooks_dir.join("post-commit"), original).unwrap();

        // Install hooks (this backs up the original)
        install_hooks(&repo_path).unwrap();

        // Backup should exist
        assert!(hooks_dir.join("post-commit.bak").exists());

        let backup_content =
            std::fs::read_to_string(hooks_dir.join("post-commit.bak")).unwrap();
        assert_eq!(backup_content, original);

        // Uninstall should restore the backup
        uninstall_hooks(&repo_path).unwrap();

        let restored = std::fs::read_to_string(hooks_dir.join("post-commit")).unwrap();
        assert_eq!(restored, original);

        // Backup should be gone
        assert!(!hooks_dir.join("post-commit.bak").exists());

        cleanup(&repo_path);
    }

    #[test]
    fn test_hooks_status_reports() {
        let (repo_path, _hooks_dir) = setup_temp_repo("status");
        install_hooks(&repo_path).unwrap();

        let status = hooks_status(&repo_path);
        assert!(status.contains("post-commit"));
        assert!(status.contains("installed"));
        assert!(status.contains("post-merge"));
        assert!(status.contains("post-checkout"));

        cleanup(&repo_path);
    }

    #[test]
    fn test_hooks_status_not_installed() {
        let (repo_path, _hooks_dir) = setup_temp_repo("no_hooks");
        let status = hooks_status(&repo_path);
        assert!(status.contains("not installed"));

        cleanup(&repo_path);
    }

    #[test]
    fn test_install_non_repo_fails() {
        let dir = std::env::temp_dir().join("tws_hooks_test_nonrepo");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();

        let result = install_hooks(&dir);
        assert!(result.is_err());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_uninstall_idempotent() {
        let (repo_path, hooks_dir) = setup_temp_repo("idempotent");
        install_hooks(&repo_path).unwrap();
        uninstall_hooks(&repo_path).unwrap();
        // Second uninstall should be a no-op (not error)
        let result = uninstall_hooks(&repo_path);
        assert!(result.is_ok());
        assert!(!hooks_dir.join("post-commit").exists());

        cleanup(&repo_path);
    }
}
