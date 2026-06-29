//! .twsignore file parser — gitignore-compatible pattern matching.
//!
//! Reads a `.twsignore` file placed in the project root to exclude files
//! from indexing. Format is identical to `.gitignore`:
//!
//! - Empty lines and `#` comment lines are ignored.
//! - Lines starting with `!` are *negative* patterns (re-include).
//! - All other lines are *positive* patterns (exclude).
//!
//! Pattern semantics follow gitignore conventions:
//! - Patterns without `/` match at any directory depth.
//! - Patterns containing `/` match relative to the project root.
//! - Patterns ending with `/` match directory contents recursively.
//! - Standard glob wildcards: `*`, `**`, `?`, `[abc]`, `[a-z]`.

use std::path::Path;

/// A set of ignore patterns parsed from a `.twsignore` file.
#[derive(Debug, Clone)]
pub struct IgnorePatterns {
    /// Patterns that exclude files (positive = "this should be ignored").
    positive: Vec<glob::Pattern>,
    /// Patterns that re-include files (preceded by `!`).
    negative: Vec<glob::Pattern>,
}

impl IgnorePatterns {
    /// Create an empty pattern set — no files are excluded.
    pub fn empty() -> Self {
        IgnorePatterns {
            positive: Vec::new(),
            negative: Vec::new(),
        }
    }

    /// Returns `true` when there are no patterns at all.
    pub fn is_empty(&self) -> bool {
        self.positive.is_empty() && self.negative.is_empty()
    }

    /// Check whether *relative_path* should be **excluded** (ignored).
    ///
    /// Negative patterns (re-inclusions) are checked **first** — a path
    /// matching a negative pattern is never ignored, even if a positive
    /// pattern also matches.
    ///
    /// *relative_path* should be the file path relative to the project
    /// root using forward slashes (e.g. `"src/main.rs"`).
    pub fn is_ignored(&self, relative_path: &str) -> bool {
        let normalized = relative_path.replace('\\', "/");

        // Negative patterns first: explicit re-include wins.
        for pat in &self.negative {
            if pat.matches(&normalized) {
                return false;
            }
        }

        // Positive patterns: exclude if matched.
        for pat in &self.positive {
            if pat.matches(&normalized) {
                return true;
            }
        }

        false
    }
}

/// Parse a `.twsignore` file at *path*.
///
/// Returns `Ok(IgnorePatterns::empty())` when the file does not exist so
/// that callers always get a valid (possibly empty) filter without needing
/// to check for file-existence themselves.
pub fn parse_twsignore(path: &Path) -> Result<IgnorePatterns, anyhow::Error> {
    if !path.exists() {
        return Ok(IgnorePatterns::empty());
    }

    let content = std::fs::read_to_string(path)?;
    parse_twsignore_content(&content)
}

/// Parse `.twsignore` content from an in-memory string (useful for tests).
pub fn parse_twsignore_content(content: &str) -> Result<IgnorePatterns, anyhow::Error> {
    let mut patterns = IgnorePatterns::empty();

    for raw_line in content.lines() {
        let trimmed = raw_line.trim();

        // Skip blank lines and comments.
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }

        // Negative pattern: re-include.
        if let Some(rest) = trimmed.strip_prefix('!') {
            let rest = rest.trim();
            if rest.is_empty() {
                continue; // lone "!" is meaningless
            }
            for pat in compile_glob(rest)? {
                patterns.negative.push(pat);
            }
        } else {
            // Positive pattern: exclude.
            for pat in compile_glob(trimmed)? {
                patterns.positive.push(pat);
            }
        }
    }

    Ok(patterns)
}

/// Compile a single raw pattern string into one or more `glob::Pattern`s.
///
/// Applies gitignore-style semantics:
/// - Trailing `/`  → directory pattern: match everything inside that
///   directory (`dir/**`).
/// - No `/` inside → bare filename pattern: match the entry itself at
///   any depth (`**/pattern`) AND match directory contents if it names
///   a directory (`**/pattern/**`).  This ensures `g_assistant*`
///   excludes both the top-level item *and* everything inside it.
/// - Contains `/` → full-path pattern: use as-is + a directory-contents
///   variant (`pattern/**`) in case the path names a directory.
pub(crate) fn compile_glob(raw: &str) -> Result<Vec<glob::Pattern>, anyhow::Error> {
    let s = raw.trim();

    if s.ends_with('/') {
        // Directory pattern: strip trailing /, then apply anchoring rules.
        let dir = &s[..s.len() - 1];
        if dir.contains('/') {
            // Anchored: "src/tests/" → "src/tests/**"
            Ok(vec![glob::Pattern::new(&format!("{}/**", dir))?])
        } else {
            // Bare directory name: "tests/" → matches at any depth.
            // Generate both the dir entry and its contents.
            let entry = glob::Pattern::new(&format!("**/{}", dir))?;
            let contents = glob::Pattern::new(&format!("**/{}/**", dir))?;
            Ok(vec![entry, contents])
        }
    } else if !s.contains('/') {
        // Bare pattern: matches the entry itself at any depth, AND
        // matches everything inside a directory with that name.
        let entry = glob::Pattern::new(&format!("**/{}", s))?;
        let contents = glob::Pattern::new(&format!("**/{}/**", s))?;
        Ok(vec![entry, contents])
    } else {
        // Full-path pattern: use as-is, plus a directory-contents
        // variant so that "src/foo" also matches "src/foo/bar/baz.py".
        let exact = glob::Pattern::new(s)?;
        let contents = glob::Pattern::new(&format!("{}/**", s))?;
        Ok(vec![exact, contents])
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Empty / missing file
    // -----------------------------------------------------------------------

    #[test]
    fn empty_content_produces_empty_patterns() {
        let pats = parse_twsignore_content("").unwrap();
        assert!(pats.is_empty());
        assert!(!pats.is_ignored("anything.rs"));
    }

    #[test]
    fn comment_only_content_is_empty() {
        let pats = parse_twsignore_content("# just a comment\n# another\n").unwrap();
        assert!(pats.is_empty());
    }

    #[test]
    fn blank_lines_are_skipped() {
        let pats = parse_twsignore_content("\n  \n# comment\n\n").unwrap();
        assert!(pats.is_empty());
    }

    // -----------------------------------------------------------------------
    // Positive patterns
    // -----------------------------------------------------------------------

    #[test]
    fn single_extension_pattern() {
        let pats = parse_twsignore_content("*.pyc").unwrap();
        assert!(pats.is_ignored("foo.pyc"));
        assert!(pats.is_ignored("src/bar.pyc"));
        assert!(pats.is_ignored("deep/nested/baz.pyc"));
        assert!(!pats.is_ignored("foo.py"));
        assert!(!pats.is_ignored("foo.rs"));
    }

    #[test]
    fn directory_pattern() {
        let pats = parse_twsignore_content("tests/").unwrap();
        assert!(pats.is_ignored("tests/test_main.py"));
        assert!(pats.is_ignored("tests/unit/test_util.py"));
        assert!(!pats.is_ignored("src/tests_helpers.py"));
        assert!(!pats.is_ignored("README.md"));
    }

    #[test]
    fn full_path_pattern() {
        let pats = parse_twsignore_content("build/output.log").unwrap();
        assert!(pats.is_ignored("build/output.log"));
        assert!(!pats.is_ignored("output.log"));
        assert!(!pats.is_ignored("src/build/output.log"));
    }

    #[test]
    fn wildcard_star() {
        let pats = parse_twsignore_content("*.log").unwrap();
        assert!(pats.is_ignored("error.log"));
        assert!(pats.is_ignored("logs/access.log"));
        assert!(!pats.is_ignored("error.log.txt"));
    }

    #[test]
    fn wildcard_question_mark() {
        let pats = parse_twsignore_content("temp?.txt").unwrap();
        assert!(pats.is_ignored("temp1.txt"));
        assert!(pats.is_ignored("src/tempA.txt"));
        assert!(!pats.is_ignored("temp10.txt"));
        assert!(!pats.is_ignored("temp.txt"));
    }

    #[test]
    fn character_class() {
        let pats = parse_twsignore_content("*.[ch]").unwrap();
        assert!(pats.is_ignored("foo.c"));
        assert!(pats.is_ignored("bar.h"));
        assert!(!pats.is_ignored("foo.py"));
    }

    #[test]
    fn recursive_globstar() {
        // "**" must be a standalone path component — the glob crate
        // rejects patterns like `**.generated.*`.  Use `**/*.generated.*`
        // to match files at any depth.
        let pats = parse_twsignore_content("**/*.generated.*").unwrap();
        assert!(pats.is_ignored("foo.generated.rs"));
        assert!(pats.is_ignored("src/bar.generated.py"));
        assert!(!pats.is_ignored("generated.go"));
    }

    #[test]
    fn explicit_globstar() {
        let pats = parse_twsignore_content("docs/**/*.png").unwrap();
        assert!(pats.is_ignored("docs/images/diagram.png"));
        assert!(pats.is_ignored("docs/screenshot.png"));
        assert!(!pats.is_ignored("assets/logo.png"));
    }

    // -----------------------------------------------------------------------
    // Negative (re-include) patterns
    // -----------------------------------------------------------------------

    #[test]
    fn negative_reinclude() {
        let content = "*.md\n!README.md\n";
        let pats = parse_twsignore_content(content).unwrap();
        // README.md is re-included by the negative pattern.
        assert!(!pats.is_ignored("README.md"));
        // Other .md files are still excluded.
        assert!(pats.is_ignored("CONTRIBUTING.md"));
        assert!(pats.is_ignored("docs/guide.md"));
        assert!(!pats.is_ignored("src/main.py"));
    }

    #[test]
    fn negative_with_directory() {
        let content = "tests/\n!tests/smoke/\n";
        let pats = parse_twsignore_content(content).unwrap();
        // tests/smoke/ is re-included.
        assert!(!pats.is_ignored("tests/smoke/test_basic.py"));
        assert!(!pats.is_ignored("tests/smoke/deep/test.py"));
        // Other tests/ are excluded.
        assert!(pats.is_ignored("tests/unit/test_main.py"));
        assert!(pats.is_ignored("tests/integration/test_api.py"));
    }

    #[test]
    fn negative_only_matches_specific_path() {
        let content = "*.tmp\n!important.tmp\n";
        let pats = parse_twsignore_content(content).unwrap();
        assert!(!pats.is_ignored("important.tmp"));
        assert!(!pats.is_ignored("src/important.tmp"));
        assert!(pats.is_ignored("other.tmp"));
        assert!(pats.is_ignored("src/other.tmp"));
    }

    // -----------------------------------------------------------------------
    // Windows path normalization
    // -----------------------------------------------------------------------

    #[test]
    fn windows_backslash_normalized() {
        let pats = parse_twsignore_content("src/generated/").unwrap();
        // Backslashes are normalized to forward slashes before matching.
        assert!(pats.is_ignored("src\\generated\\code.rs"));
        assert!(pats.is_ignored("src/generated/code.rs"));
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn trailing_whitespace_in_pattern() {
        let pats = parse_twsignore_content("*.pyc   \n").unwrap();
        assert!(pats.is_ignored("foo.pyc"));
    }

    #[test]
    fn lone_bang_is_skipped() {
        let pats = parse_twsignore_content("!").unwrap();
        assert!(pats.is_empty());
    }

    #[test]
    fn mixed_empty_lines_and_patterns() {
        let content = "\n\n*.pyc\n\n# generated\n*.gen.rs\n\n";
        let pats = parse_twsignore_content(content).unwrap();
        assert!(pats.is_ignored("foo.pyc"));
        assert!(pats.is_ignored("bar.gen.rs"));
        assert!(!pats.is_ignored("main.rs"));
    }

    #[test]
    fn leading_whitespace_is_stripped() {
        let pats = parse_twsignore_content("  *.swp  \n").unwrap();
        assert!(pats.is_ignored(".file.swp"));
    }

    #[test]
    fn multiple_patterns() {
        let content = "\
# Build artifacts
target/
build/
dist/

# Python
*.pyc
__pycache__/
*.egg-info/

# But keep setup files
!setup.py
";
        let pats = parse_twsignore_content(content).unwrap();

        // Excluded.
        assert!(pats.is_ignored("target/debug/foo.o"));
        assert!(pats.is_ignored("build/output.txt"));
        assert!(pats.is_ignored("dist/bundle.js"));
        assert!(pats.is_ignored("foo.pyc"));
        assert!(pats.is_ignored("__pycache__/bar.cpython-39.pyc"));
        assert!(pats.is_ignored("my_package.egg-info/PKG-INFO"));

        // Re-included.
        assert!(!pats.is_ignored("setup.py"));

        // Not matched.
        assert!(!pats.is_ignored("src/main.rs"));
        assert!(!pats.is_ignored("README.md"));
    }
}
