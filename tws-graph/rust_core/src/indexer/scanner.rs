//! File scanner — discovers source files for indexing.
//!
//! Uses `git ls-files` as the primary strategy (respects `.gitignore`),
//! falling back to `walkdir` when git is unavailable.
//!
//! Optional `.twsignore` support filters files on top of git's own
//! exclusion rules.  See [`super::ignore`] for the parser.

use std::path::{Path, PathBuf};

use super::ignore::{self, IgnorePatterns};

/// Directories to skip during filesystem walk (fallback only).
const SKIP_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    "__pycache__",
    ".tox",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
    "target",
];

/// Path prefixes to skip during filesystem walk (fallback only).
const SKIP_PATH_PREFIXES: &[&str] = &[".tws/codegraph", ".tws/sessions"];

/// Scan a directory for indexable source files.
///
/// This is a convenience wrapper around [`scan_directory_with_ignore`]
/// that does **not** apply any `.twsignore` rules.
///
/// Strategy:
/// 1. Try `git ls-files --cached --others --exclude-standard` (fast + respects `.gitignore`).
/// 2. Fall back to `walkdir` when git is unavailable.
///
/// Returns a sorted list of relative paths.
pub fn scan_directory(root: &Path) -> Result<Vec<PathBuf>, anyhow::Error> {
    scan_directory_with_ignore(root, None)
}

/// Scan a directory with optional `.twsignore` filtering.
///
/// After collecting files via `git ls-files` (or walkdir fallback), each
/// relative path is tested against the ignore patterns loaded from
/// *twsignore_path*.
///
/// When *twsignore_path* is `Some(p)`, the file at `p` is parsed as a
/// `.twsignore` file.  When `None` the default `root/.twsignore` is used
/// if it exists — this means a `.twsignore` in the project root is always
/// honoured by default unless explicitly suppressed.
///
/// Returns a sorted list of relative paths (already filtered).
pub fn scan_directory_with_ignore(
    root: &Path,
    twsignore_path: Option<&Path>,
) -> Result<Vec<PathBuf>, anyhow::Error> {
    // Phase 1 — collect all candidate files (git or walkdir).
    let files = if let Some(git_files) = try_git_ls_files(root) {
        git_files
    } else {
        walkdir_fallback(root)?
    };

    // Phase 2 — load ignore patterns.
    let patterns: IgnorePatterns = match twsignore_path {
        Some(explicit) => ignore::parse_twsignore(explicit)?,
        None => {
            let default = root.join(".twsignore");
            ignore::parse_twsignore(&default)?
        }
    };

    // Phase 3 — filter if there are patterns to apply.
    if patterns.is_empty() {
        return Ok(files);
    }

    let filtered: Vec<PathBuf> = files
        .into_iter()
        .filter(|f| {
            let rel = f.to_str().unwrap_or("");
            !patterns.is_ignored(rel)
        })
        .collect();

    Ok(filtered)
}

// ---------------------------------------------------------------------------
// git ls-files strategy
// ---------------------------------------------------------------------------

/// Run `git ls-files` and return relative paths.
///
/// Returns `None` if git is unavailable or the command fails, so the
/// caller can fall back to the walkdir strategy.
fn try_git_ls_files(root: &Path) -> Option<Vec<PathBuf>> {
    let output = std::process::Command::new("git")
        .args([
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ])
        .current_dir(root)
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let stdout = String::from_utf8(output.stdout).ok()?;
    let mut files: Vec<PathBuf> = stdout
        .lines()
        .filter(|l| !l.is_empty())
        .map(PathBuf::from)
        .collect();

    files.sort();
    Some(files)
}

// ---------------------------------------------------------------------------
// walkdir fallback
// ---------------------------------------------------------------------------

/// Walk the directory tree collecting all files not in skipped dirs/paths.
fn walkdir_fallback(root: &Path) -> Result<Vec<PathBuf>, anyhow::Error> {
    let mut files = Vec::new();

    let walker = walkdir::WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| !should_skip_dir(e));

    for entry in walker {
        match entry {
            Ok(entry) => {
                if !entry.file_type().is_file() {
                    continue;
                }
                let path = entry.path();
                // Compute relative path from root
                let rel = path.strip_prefix(root).unwrap_or(path);
                // Skip specific path prefixes
                let rel_str = rel.to_str().unwrap_or("");
                if should_skip_path(rel_str) {
                    continue;
                }
                files.push(rel.to_path_buf());
            }
            Err(_) => continue,
        }
    }

    files.sort();
    Ok(files)
}

/// Decide whether a directory entry should be skipped during traversal.
fn should_skip_dir(entry: &walkdir::DirEntry) -> bool {
    if !entry.file_type().is_dir() {
        // Only filter directories at the entry level; files are handled later.
        return false;
    }
    let name = entry.file_name().to_str().unwrap_or("");
    SKIP_DIRS.contains(&name)
}

/// Decide whether a relative path should be excluded after walk.
fn should_skip_path(rel_path: &str) -> bool {
    // Normalize backslashes to forward slashes on Windows
    let normalized = rel_path.replace('\\', "/");
    SKIP_PATH_PREFIXES.iter().any(|prefix| {
        normalized.starts_with(prefix)
            || normalized.starts_with(&format!("{}/", prefix))
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// Helper: create a unique temporary directory using std only.
    fn temp_dir(prefix: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("tws_scanner_test_{}", prefix));
        // Remove if exists from a previous failed run
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Helper: clean up a temp directory.
    fn cleanup(dir: &Path) {
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn test_scan_directory_returns_files() {
        let root = temp_dir("returns_files");

        // Create some source-like files
        fs::create_dir_all(root.join("src")).unwrap();
        fs::create_dir_all(root.join("node_modules")).unwrap();
        fs::create_dir_all(root.join("__pycache__")).unwrap();
        fs::create_dir_all(root.join(".git")).unwrap();

        fs::write(root.join("src").join("main.rs"), "fn main() {}").unwrap();
        fs::write(root.join("src").join("lib.rs"), "pub fn foo() {}").unwrap();
        fs::write(root.join("Cargo.toml"), "[package]").unwrap();
        fs::write(root.join("node_modules").join("dep.js"), "// skip me").unwrap();
        fs::write(root.join("__pycache__").join("cache.pyc"), "cache").unwrap();
        fs::write(root.join(".git").join("config"), "git config").unwrap();

        // Without git, the walkdir fallback should be used.
        let files = scan_directory(&root).expect("scan should succeed");

        // Should find Cargo.toml + 2 .rs files, but NOT the skipped dir files.
        assert_eq!(
            files.len(),
            3,
            "expected 3 files, got {}: {:?}",
            files.len(),
            files
        );

        let paths: Vec<&str> = files.iter().map(|p| p.to_str().unwrap()).collect();
        assert!(
            paths.iter().any(|p| p.ends_with("Cargo.toml")),
            "Should contain Cargo.toml"
        );
        assert!(
            paths.iter().any(|p| p.contains("lib.rs")),
            "Should contain lib.rs"
        );
        assert!(
            paths.iter().any(|p| p.contains("main.rs")),
            "Should contain main.rs"
        );
    }

    #[test]
    fn test_scan_returns_sorted() {
        let root = temp_dir("sorted");

        fs::write(root.join("z.txt"), "z").unwrap();
        fs::write(root.join("a.txt"), "a").unwrap();
        fs::write(root.join("m.txt"), "m").unwrap();

        let files = scan_directory(&root).expect("scan should succeed");
        let names: Vec<&str> = files
            .iter()
            .map(|p| p.file_name().unwrap().to_str().unwrap())
            .collect();
        assert_eq!(names, vec!["a.txt", "m.txt", "z.txt"]);

        cleanup(&root);
    }

    #[test]
    fn test_skip_specific_paths() {
        let root = temp_dir("skip_paths");

        fs::create_dir_all(root.join(".tws").join("codegraph")).unwrap();
        fs::create_dir_all(root.join(".tws").join("sessions")).unwrap();
        fs::write(root.join(".tws").join("project-map.md"), "map").unwrap();
        fs::write(
            root.join(".tws")
                .join("codegraph")
                .join("index.db"),
            "index",
        )
        .unwrap();
        fs::write(
            root.join(".tws")
                .join("sessions")
                .join("session.json"),
            "{}",
        )
        .unwrap();

        let files = scan_directory(&root).expect("scan should succeed");

        let has_map = files.iter().any(|p| {
            p.to_str()
                .map(|s| s.contains("project-map.md"))
                .unwrap_or(false)
        });
        let has_index = files.iter().any(|p| {
            p.to_str()
                .map(|s| s.contains("index.db"))
                .unwrap_or(false)
        });
        let has_session = files.iter().any(|p| {
            p.to_str()
                .map(|s| s.contains("session.json"))
                .unwrap_or(false)
        });

        assert!(has_map, "Should include .tws/project-map.md");
        assert!(!has_index, "Should skip .tws/codegraph/ contents");
        assert!(!has_session, "Should skip .tws/sessions/ contents");

        cleanup(&root);
    }

    #[test]
    fn test_scan_empty_directory() {
        let root = temp_dir("empty");

        let files = scan_directory(&root).expect("scan should succeed");
        assert!(files.is_empty());

        cleanup(&root);
    }

    #[test]
    fn test_should_skip_dir_matches_known_dirs() {
        assert!(should_skip_dir_name("node_modules"));
        assert!(should_skip_dir_name(".git"));
        assert!(should_skip_dir_name("target"));
        assert!(should_skip_dir_name("build"));
        assert!(should_skip_dir_name("dist"));
        assert!(should_skip_dir_name("__pycache__"));
        assert!(!should_skip_dir_name("src"));
        assert!(!should_skip_dir_name("tests"));
    }

    #[test]
    fn test_should_skip_path_matches() {
        assert!(should_skip_path(".tws/codegraph/index.db"));
        assert!(should_skip_path(".tws/codegraph/anything"));
        assert!(should_skip_path(".tws/sessions/s1.json"));
        assert!(!should_skip_path(".tws/project-map.md"));
        assert!(!should_skip_path("src/main.rs"));
        // Windows-style paths
        assert!(should_skip_path(".tws\\codegraph\\index.db"));
        assert!(should_skip_path(".tws\\sessions\\s1.json"));
    }

    /// Direct name check against SKIP_DIRS (used by tests to avoid
    /// constructing walkdir::DirEntry).
    fn should_skip_dir_name(name: &str) -> bool {
        SKIP_DIRS.contains(&name)
    }
}
