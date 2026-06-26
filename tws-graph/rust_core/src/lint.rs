//! Skill linter — validates SKILL.md files against structural rules.
//!
//! Checks:
//! 1. YAML frontmatter (must have name + description fields)
//! 2. SUBAGENT-STOP tag placement (required on entry/flow, forbidden on comp/found)
//! 3. Cross-reference validity (symbols referenced in SKILL.md bodies exist)
//! 4. Directory name prefix matching (must match layer: using-/flow-/comp-/found-/tws-)
//! 5. Unreferenced skill detection (skills not cited by any other skill)

use std::collections::{HashMap, HashSet};
use std::path::Path;

/// A single lint issue.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LintIssue {
    pub rule: String,
    pub file: String,
    pub line: usize,
    pub severity: String,
    pub message: String,
}

/// Parse YAML frontmatter from markdown content.
///
/// Returns `Some((name, description))` if frontmatter is found and valid,
/// `None` otherwise.
fn parse_frontmatter(content: &str) -> Option<(String, String)> {
    let lines: Vec<&str> = content.lines().collect();
    if lines.len() < 4 {
        return None;
    }
    // Frontmatter starts with "---" on the first line
    if lines[0].trim() != "---" {
        return None;
    }
    let mut name = None;
    let mut description = None;
    let mut end_idx = None;
    for (i, line) in lines.iter().enumerate().skip(1) {
        let trimmed = line.trim();
        if trimmed == "---" {
            end_idx = Some(i + 1); // 1-based line number
            break;
        }
        if let Some(value) = trimmed.strip_prefix("name:") {
            name = Some(value.trim().to_string());
        } else if let Some(value) = trimmed.strip_prefix("description:") {
            description = Some(value.trim().to_string());
        }
    }
    match (name, description, end_idx) {
        (Some(n), Some(d), Some(_)) => Some((n, d)),
        _ => None,
    }
}

/// Determine the layer prefix from a directory name.
///
/// Returns one of: "using", "flow", "comp", "found", "tws"
fn get_layer_prefix(dir_name: &str) -> Option<&str> {
    if dir_name.starts_with("using-") {
        Some("using")
    } else if dir_name.starts_with("flow-") {
        Some("flow")
    } else if dir_name.starts_with("comp-") {
        Some("comp")
    } else if dir_name.starts_with("found-") {
        Some("found")
    } else if dir_name.starts_with("tws-") {
        Some("tws")
    } else {
        None
    }
}

/// Check if a path is an entry/flow skill (should have SUBAGENT-STOP).
fn is_entry_or_flow(dir_name: &str) -> bool {
    let prefix = get_layer_prefix(dir_name);
    prefix == Some("using") || prefix == Some("flow") || prefix == Some("tws")
}

/// Check if a path is a component/foundation skill (should NOT have SUBAGENT-STOP).
fn is_comp_or_found(dir_name: &str) -> bool {
    let prefix = get_layer_prefix(dir_name);
    prefix == Some("comp") || prefix == Some("found")
}

/// Check rule 1: YAML frontmatter validation.
fn check_frontmatter(content: &str, file: &str) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    let frontmatter = parse_frontmatter(content);
    match frontmatter {
        None => {
            issues.push(LintIssue {
                rule: "frontmatter".to_string(),
                file: file.to_string(),
                line: 1,
                severity: "error".to_string(),
                message: "Missing or invalid YAML frontmatter (must have name + description)".to_string(),
            });
        }
        Some((name, _desc)) => {
            if name.is_empty() {
                issues.push(LintIssue {
                    rule: "frontmatter".to_string(),
                    file: file.to_string(),
                    line: 1,
                    severity: "error".to_string(),
                    message: "Frontmatter 'name' field is empty".to_string(),
                });
            }
        }
    }
    issues
}

/// Check rule 2: SUBAGENT-STOP tag placement.
fn check_subagent_stop(content: &str, file: &str, dir_name: &str) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    let has_stop = content.contains("<SUBAGENT-STOP>");

    if is_entry_or_flow(dir_name) {
        if !has_stop {
            let line = content.lines().count().saturating_sub(1);
            issues.push(LintIssue {
                rule: "subagent-stop".to_string(),
                file: file.to_string(),
                line,
                severity: "error".to_string(),
                message: "Entry/flow skill missing required <SUBAGENT-STOP> tag".to_string(),
            });
        }
    } else if is_comp_or_found(dir_name) {
        if has_stop {
            // Find the line number of the tag
            let tag_line = content
                .lines()
                .position(|l| l.contains("<SUBAGENT-STOP>"))
                .map(|i| i + 1)
                .unwrap_or(1);
            issues.push(LintIssue {
                rule: "subagent-stop".to_string(),
                file: file.to_string(),
                line: tag_line,
                severity: "error".to_string(),
                message: "Component/foundation skill has forbidden <SUBAGENT-STOP> tag".to_string(),
            });
        }
    }
    issues
}

/// Check rule 3: Cross-reference validity.
///
/// Collects skill names from frontmatter of all files, then checks that
/// any `` `skill-name` `` references in skill bodies correspond to real skills.
fn check_cross_references(
    content: &str,
    file: &str,
    all_skill_names: &HashSet<String>,
) -> Vec<LintIssue> {
    let mut issues = Vec::new();

    // Find skill references in markdown links or skill name mentions
    // Pattern: look for text like `skill-name` in backticks at the end of lines
    // or explicit references to Skill("skill-name")
    for (line_idx, line) in content.lines().enumerate() {
        let line_num = line_idx + 1;

        // Skip frontmatter lines
        if line_num <= 4 && (line.trim() == "---" || line.trim().starts_with("name:") || line.trim().starts_with("description:")) {
            continue;
        }

        // Extract backtick-quoted strings
        let mut i = 0;
        let bytes = line.as_bytes();
        while i < bytes.len() {
            if bytes[i] == b'`' {
                let start = i + 1;
                let mut end = start;
                while end < bytes.len() && bytes[end] != b'`' {
                    end += 1;
                }
                if end > start {
                    let text = &line[start..end];
                    // Check if it looks like a skill reference (contains hyphens, common prefix)
                    if (text.starts_with("using-") || text.starts_with("flow-")
                        || text.starts_with("comp-") || text.starts_with("found-")
                        || text.starts_with("tws-"))
                        && text.len() > 5
                        && !all_skill_names.contains(text)
                    {
                        issues.push(LintIssue {
                            rule: "cross-refs".to_string(),
                            file: file.to_string(),
                            line: line_num,
                            severity: "warning".to_string(),
                            message: format!("Cross-reference to unknown skill: `{}`", text),
                        });
                    }
                }
                i = end;
            }
            i += 1;
        }
    }
    issues
}

/// Check rule 4: Directory name prefix matching.
fn check_prefix(file: &str, dir_name: &str) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    let prefix = get_layer_prefix(dir_name);
    if prefix.is_none() {
        issues.push(LintIssue {
            rule: "prefix".to_string(),
            file: file.to_string(),
            line: 0,
            severity: "error".to_string(),
            message: format!(
                "Directory '{}' does not match expected prefix (using-/flow-/comp-/found-/tws-)",
                dir_name
            ),
        });
    }
    issues
}

/// Check rule 5: Unreferenced skill detection.
fn check_unreferenced(
    skill_name: &str,
    file: &str,
    referenced_skills: &HashSet<String>,
) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    // Don't flag entry skills (using-tws, using-tws-goal) as unreferenced
    if !skill_name.starts_with("using-") && !referenced_skills.contains(skill_name) {
        // Also skip tws-init and tws-graph-init
        if skill_name != "tws-init" && skill_name != "tws-graph-init" {
            issues.push(LintIssue {
                rule: "unreferenced".to_string(),
                file: file.to_string(),
                line: 0,
                severity: "warning".to_string(),
                message: format!("Skill '{}' is not referenced by any other skill", skill_name),
            });
        }
    }
    issues
}

/// Run all lint rules against the skills directory.
pub fn lint_skills(skills_dir: &Path) -> Vec<LintIssue> {
    let mut issues = Vec::new();

    // Collect all SKILL.md files
    let mut skill_files: Vec<(String, String, String, String)> = Vec::new();
    // (file_path, content, skill_name, dir_name)

    if let Ok(entries) = std::fs::read_dir(skills_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let dir_name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                let skill_md = path.join("SKILL.md");
                if skill_md.exists() {
                    if let Ok(content) = std::fs::read_to_string(&skill_md) {
                        let skill_name = parse_frontmatter(&content)
                            .map(|(n, _)| n)
                            .unwrap_or_else(|| dir_name.clone());
                        let file_str = skill_md.to_string_lossy().to_string();
                        skill_files.push((file_str, content, skill_name, dir_name));
                    } else {
                        let file_str = skill_md.to_string_lossy().to_string();
                        issues.push(LintIssue {
                            rule: "frontmatter".to_string(),
                            file: file_str,
                            line: 0,
                            severity: "error".to_string(),
                            message: "Cannot read SKILL.md file".to_string(),
                        });
                    }
                }
            }
        }
    }

    // Build sets for cross-reference checking
    let all_skill_names: HashSet<String> = skill_files.iter().map(|(_, _, n, _)| n.clone()).collect();

    // Build set of referenced skills (from all skill bodies)
    let mut referenced_skills: HashSet<String> = HashSet::new();
    for (_, content, _, _) in &skill_files {
        for line in content.lines() {
            for name in &all_skill_names {
                if line.contains(name.as_str()) {
                    referenced_skills.insert(name.clone());
                }
            }
        }
    }

    // Run all rules for each skill file
    for (file, content, skill_name, dir_name) in &skill_files {
        // Rule 1: Frontmatter
        issues.extend(check_frontmatter(content, file));

        // Rule 2: SUBAGENT-STOP
        issues.extend(check_subagent_stop(content, file, dir_name));

        // Rule 3: Cross-references
        issues.extend(check_cross_references(content, file, &all_skill_names));

        // Rule 4: Prefix
        issues.extend(check_prefix(file, dir_name));

        // Rule 5: Unreferenced
        issues.extend(check_unreferenced(skill_name, file, &referenced_skills));
    }

    // Sort by severity (error first) then by file
    issues.sort_by(|a, b| {
        let sev_cmp = if a.severity == "error" && b.severity != "error" {
            std::cmp::Ordering::Less
        } else if a.severity != "error" && b.severity == "error" {
            std::cmp::Ordering::Greater
        } else {
            a.file.cmp(&b.file).then(a.line.cmp(&b.line))
        };
        sev_cmp
    });

    issues
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn create_temp_skills_dir(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("tws_lint_test_{}", name));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn create_skill_file(base: &Path, dir_name: &str, skill_md_content: &str) -> std::path::PathBuf {
        let dir = base.join(dir_name);
        std::fs::create_dir_all(&dir).unwrap();
        let file_path = dir.join("SKILL.md");
        let mut f = std::fs::File::create(&file_path).unwrap();
        f.write_all(skill_md_content.as_bytes()).unwrap();
        file_path
    }

    #[test]
    fn test_parse_valid_frontmatter() {
        let content = "---\nname: my-skill\ndescription: A test skill\n---\n# Body\n";
        let result = parse_frontmatter(content);
        assert!(result.is_some());
        let (name, desc) = result.unwrap();
        assert_eq!(name, "my-skill");
        assert_eq!(desc, "A test skill");
    }

    #[test]
    fn test_parse_missing_frontmatter() {
        let content = "# No frontmatter\nSome body text\n";
        let result = parse_frontmatter(content);
        assert!(result.is_none());
    }

    #[test]
    fn test_lint_valid_flow_skill() {
        let dir = create_temp_skills_dir("valid_flow");
        let content = "---\nname: flow-test\ndescription: Test flow\n---\n<SUBAGENT-STOP>\nThis is a flow skill.\n</SUBAGENT-STOP>\n# Body\nRefers to `comp-test`\n";
        create_skill_file(&dir, "flow-test", content);

        // Also create a referenced skill
        let comp_content = "---\nname: comp-test\ndescription: Test comp\n---\n# Body\n";
        create_skill_file(&dir, "comp-test", comp_content);

        let issues = lint_skills(&dir);
        // Should only have unreferenced warnings (not errors)
        let errors: Vec<_> = issues.iter().filter(|i| i.severity == "error").collect();
        assert!(errors.is_empty(), "Expected no errors, got: {:?}", errors);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_missing_subagent_stop_on_flow() {
        let dir = create_temp_skills_dir("missing_stop");
        let content = "---\nname: flow-test\ndescription: Test flow\n---\n# Body without SUBAGENT-STOP\n";
        create_skill_file(&dir, "flow-test", content);

        let issues = lint_skills(&dir);
        let subagent_errors: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "subagent-stop" && i.severity == "error")
            .collect();
        assert!(!subagent_errors.is_empty(), "Should flag missing SUBAGENT-STOP on flow skill");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_subagent_stop_on_comp_is_error() {
        let dir = create_temp_skills_dir("stop_on_comp");
        let content = "---\nname: comp-test\ndescription: Test comp\n---\n<SUBAGENT-STOP>\nThis should not be here.\n</SUBAGENT-STOP>\n# Body\n";
        create_skill_file(&dir, "comp-test", content);

        let issues = lint_skills(&dir);
        let errors: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "subagent-stop" && i.severity == "error")
            .collect();
        assert!(!errors.is_empty(), "Should flag SUBAGENT-STOP on component skill");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_invalid_prefix() {
        let dir = create_temp_skills_dir("invalid_prefix");
        let content = "---\nname: invalid-thing\ndescription: Test\n---\n# Body\n";
        create_skill_file(&dir, "invalid-thing", content);

        let issues = lint_skills(&dir);
        let prefix_errors: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "prefix" && i.severity == "error")
            .collect();
        assert!(!prefix_errors.is_empty(), "Should flag invalid directory prefix");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_missing_frontmatter() {
        let dir = create_temp_skills_dir("no_frontmatter");
        let content = "# No frontmatter at all\nJust body text\n";
        create_skill_file(&dir, "comp-nofm", content);

        let issues = lint_skills(&dir);
        let fm_errors: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "frontmatter" && i.severity == "error")
            .collect();
        assert!(!fm_errors.is_empty(), "Should flag missing frontmatter");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_cross_reference_warning() {
        let dir = create_temp_skills_dir("cross_ref");
        let content = "---\nname: flow-test\ndescription: Test flow\n---\n<SUBAGENT-STOP>\nFlow stop\n</SUBAGENT-STOP>\n# Body\nRefs `comp-nonexistent`\n";
        create_skill_file(&dir, "flow-test", content);

        let issues = lint_skills(&dir);
        let cross_refs: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "cross-refs")
            .collect();
        assert!(!cross_refs.is_empty(), "Should warn about unknown cross-reference");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_lint_unreferenced_skill() {
        let dir = create_temp_skills_dir("unref_skill");
        let content_a = "---\nname: comp-orphan\ndescription: Orphan component\n---\n# Body\n";
        create_skill_file(&dir, "comp-orphan", content_a);

        // Reference the orphan from another skill so it's not unreferenced
        let content_b = "---\nname: comp-parent\ndescription: Parent component\n---\n# Body\nUsing comp-orphan\n";
        create_skill_file(&dir, "comp-parent", content_b);

        let issues = lint_skills(&dir);
        let unreferenced_orphan: Vec<_> = issues
            .iter()
            .filter(|i| i.rule == "unreferenced" && i.message.contains("orphan"))
            .collect();
        assert!(unreferenced_orphan.is_empty(), "comp-orphan is referenced by comp-parent, should not be unreferenced");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_get_layer_prefix() {
        assert_eq!(get_layer_prefix("using-tws"), Some("using"));
        assert_eq!(get_layer_prefix("flow-add-feature"), Some("flow"));
        assert_eq!(get_layer_prefix("comp-implementation"), Some("comp"));
        assert_eq!(get_layer_prefix("found-core-principles"), Some("found"));
        assert_eq!(get_layer_prefix("tws-init"), Some("tws"));
        assert_eq!(get_layer_prefix("random-dir"), None);
    }
}
