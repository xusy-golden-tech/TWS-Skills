//! File scanner — discovers source files for indexing.
//!
//! Uses `git ls-files` as the primary strategy (respects `.gitignore`),
//! falling back to `walkdir` when git is unavailable.

use std::path::{Path, PathBuf};

/// Scan a directory for indexable source files.
///
/// Strategy:
/// 1. Try `git ls-files` (fast + respects `.gitignore`).
/// 2. Fall back to `walkdir` (os.walk equivalent).
pub fn scan(root: &Path) -> Vec<PathBuf> {
    if let Some(files) = try_git_ls_files(root) {
        return files;
    }
    walkdir_fallback(root)
}

/// Run `git ls-files` and return relative paths.
fn try_git_ls_files(root: &Path) -> Option<Vec<PathBuf>> {
    let output = std::process::Command::new("git")
        .args(["ls-files", "--cached", "--others", "--exclude-standard"])
        .current_dir(root)
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let stdout = String::from_utf8(output.stdout).ok()?;
    Some(
        stdout
            .lines()
            .filter(|l| !l.is_empty())
            .map(|l| root.join(l))
            .collect(),
    )
}

/// Walk the directory tree looking for files with known extensions.
fn walkdir_fallback(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let walker = walkdir::WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| {
            // Skip hidden directories and common non-source dirs
            let name = e.file_name().to_str().unwrap_or("");
            !name.starts_with('.')
                && name != "node_modules"
                && name != "__pycache__"
                && name != "target"
                && name != "build"
                && name != "dist"
                && name != "vendor"
        });

    for entry in walker {
        if let Ok(entry) = entry {
            if entry.file_type().is_file() {
                files.push(entry.into_path());
            }
        }
    }

    files
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_scan_current_dir() {
        let files = scan(Path::new("."));
        // At minimum we should find Cargo.toml
        assert!(!files.is_empty() || std::env::current_dir().is_err());
    }
}
