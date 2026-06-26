//! Skill linter — validates SKILL.md files against structural rules.
//!
//! Checks:
//! 1. YAML frontmatter (name + description)
//! 2. SUBAGENT-STOP tag placement
//! 3. Cross-reference validity
//! 4. Directory name prefix matching
//! 5. Unreferenced skill detection

/// A single lint issue.
#[derive(Debug)]
pub struct LintIssue {
    pub file: String,
    pub line: usize,
    pub severity: String,
    pub message: String,
}

/// Run all lint rules against the skills directory.
pub fn lint_skills(_skills_dir: &str) -> Vec<LintIssue> {
    // TODO: implement the 5 lint rules
    Vec::new()
}
