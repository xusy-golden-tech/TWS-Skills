//! Bash language extractor.
//!
//! Extracts symbols and relationships from Bash/shell source files
//! (`.sh`, `.bash`, `.zsh`) using the tree-sitter-bash grammar.
//!
//! # Node kinds produced
//! - `bash_function`: function definition
//! - `bash_variable`: variable assignment
//!
//! # Edge kinds produced
//! - `calls`: command invocations (function calls / external commands)
//! - `contains`: containment (file -> function)
//! - `imports`: `source` / `.` script imports
//! - `env_accesses`: `$VAR` / `${VAR}` references

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use regex::Regex;
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// Common shell builtins and keywords -- filtered from calls.
const SHELL_BUILTINS: &[&str] = &[
    "echo", "cd", "pwd", "ls", "cat", "rm", "mv", "cp", "mkdir", "rmdir",
    "touch", "chmod", "chown", "grep", "sed", "awk", "cut", "sort", "uniq",
    "wc", "find", "xargs", "tee", "head", "tail", "tr", "diff",
    "export", "unset", "set", "alias", "unalias", "readonly",
    "exit", "return", "shift", "test", "[", "[[", "true", "false",
    "exec", "eval", "trap", "wait", "jobs", "fg", "bg", "kill",
    "printf", "read", "source", ".", "let", "declare", "local", "typeset",
    "builtin", "command", "type", "hash", "enable", "ulimit",
    "if", "then", "else", "elif", "fi", "for", "while", "until",
    "do", "done", "case", "esac", "in", "function", "select", "time",
    "continue", "break",
];

fn is_shell_builtin(name: &str) -> bool {
    SHELL_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// BashExtractor
// ---------------------------------------------------------------------------

pub struct BashExtractor;

impl Extractor for BashExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["sh", "bash", "zsh"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["bash"]
    }

    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();

        let file_name = {
            let path = std::path::Path::new(&ctx.file_path);
            path.file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("script")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        walk_node(source, root, ctx, &file_id);

        // Scan source for environment variable references ($VAR or ${VAR})
        extract_env_vars_regex(source, ctx, &file_id);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walker
// ---------------------------------------------------------------------------

fn walk_node(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "function_definition" => {
                    extract_function(source, child, ctx, parent_id);
                }
                "variable_assignment" => {
                    extract_variable(source, child, ctx, parent_id);
                }
                "command" => {
                    extract_command(source, child, ctx, parent_id);
                }
                // Recurse into control flow structures
                "if_statement" | "elif_clause" | "else_clause"
                | "for_statement" | "while_statement" | "until_statement"
                | "case_statement" | "case_item" | "do_group"
                | "compound_statement" | "subshell" | "c_style_for_statement"
                | "pipeline" | "list" | "negated_command" | "test_command"
                | "declaration_command" | "unset_command" | "redirected_statement"
                | "block" | "brace_group" => {
                    walk_node(source, child, ctx, parent_id);
                }
                _ => {
                    // Default recursion for any other node
                    walk_node(source, child, ctx, parent_id);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Function extraction
// ---------------------------------------------------------------------------

fn extract_function(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_function_name(source, node);
    if name.is_empty() {
        return;
    }

    let func_id = ctx.add_node(NodeKind::BashFunction, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains,
        (node.start_position().row + 1) as u32, Some(&name));

    // Recurse into function body for calls, variables, env accesses
    let body = node.child_by_field_name("body");
    if let Some(body_node) = body {
        walk_node(source, body_node, ctx, &func_id);
    } else {
        // Fallback: walk all children
        walk_node(source, node, ctx, &func_id);
    }
}

fn get_function_name(source: &[u8], node: Node) -> String {
    // tree-sitter-bash: function_definition has a "name" field (a word node)
    if let Some(name_node) = node.child_by_field_name("name") {
        return name_node.utf8_text(source).unwrap_or("").to_string();
    }

    // Look for a word child (for `function_name() { ... }` syntax)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "word" {
                return child.utf8_text(source).unwrap_or("").to_string();
            }
        }
    }

    String::new()
}

// ---------------------------------------------------------------------------
// Variable extraction
// ---------------------------------------------------------------------------

fn extract_variable(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // tree-sitter-bash: variable_assignment has a "name" field (variable_name node)
    if let Some(name_node) = node.child_by_field_name("name") {
        let name = name_node.utf8_text(source).unwrap_or("");
        if !name.is_empty() {
            ctx.add_node(NodeKind::BashVariable, name, &name_node, HashMap::new());
        }
        return;
    }

    // Fallback: look for variable_name child
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "variable_name" {
                let name = child.utf8_text(source).unwrap_or("");
                if !name.is_empty() {
                    ctx.add_node(NodeKind::BashVariable, name, &child, HashMap::new());
                    return;
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Command / call extraction
// ---------------------------------------------------------------------------

fn extract_command(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Get the command name
    let cmd_name = get_command_name(source, node);
    if cmd_name.is_empty() {
        // Recurse into children anyway
        walk_node(source, node, ctx, parent_id);
        return;
    }

    // Check if this is a source command (import)
    if cmd_name == "source" || cmd_name == "." {
        extract_source_import(source, node, ctx, parent_id);
        return;
    }

    // Filter builtins
    if !is_shell_builtin(&cmd_name) {
        let tgt_text = ctx.make_qualified(&cmd_name);
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
            (node.start_position().row + 1) as u32, Some(&cmd_name));
    }

    // Recurse into children for more calls/variables
    walk_node(source, node, ctx, parent_id);
}

fn get_command_name(source: &[u8], node: Node) -> String {
    // tree-sitter-bash: command has a "name" field (command_name node)
    if let Some(name_node) = node.child_by_field_name("name") {
        return name_node.utf8_text(source).unwrap_or("").to_string();
    }

    // Fallback: look for first word child
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "command_name" {
                return child.utf8_text(source).unwrap_or("").to_string();
            }
            if child.kind() == "word" {
                let text = child.utf8_text(source).unwrap_or("");
                // Skip variable assignments that start with a word containing =
                if !text.contains('=') {
                    return text.to_string();
                }
            }
        }
    }

    String::new()
}

// ---------------------------------------------------------------------------
// Source import extraction
// ---------------------------------------------------------------------------

fn extract_source_import(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Look for the file argument (word or string after "source" or ".")
    let mut found_source_keyword = false;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "command_name" {
                let txt = child.utf8_text(source).unwrap_or("");
                if txt == "source" || txt == "." {
                    found_source_keyword = true;
                    continue;
                }
            }
            if found_source_keyword {
                if child.kind() == "word" || child.kind() == "string" {
                    let path = child.utf8_text(source).unwrap_or("");
                    let clean_path = path.trim_matches(|c| c == '"' || c == '\'').to_string();
                    if !clean_path.is_empty() {
                        let tgt_text = ctx.make_qualified(&clean_path);
                        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Imports,
                            (child.start_position().row + 1) as u32, Some(&clean_path));
                    }
                    return;
                }
            }
        }
    }

    // Walk children for env accesses
    walk_node(source, node, ctx, parent_id);
}

// ---------------------------------------------------------------------------
// Environment variable access (regex-based)
// ---------------------------------------------------------------------------

/// Scan the source text for environment variable references like `$HOME` or `${USER}`.
/// Only matches uppercase variable names to distinguish env vars from local shell vars.
fn extract_env_vars_regex(source: &[u8], ctx: &mut ExtractionContext, file_id: &str) {
    let source_str = std::str::from_utf8(source).unwrap_or("");
    // Match $VAR_NAME where VAR_NAME is uppercase (env vars) or ${VAR_NAME}
    // Regex: $[A-Z_][A-Z0-9_]* or ${[A-Z_][A-Z0-9_]*}
    let re = Regex::new(r"\$\{?([A-Z_][A-Z0-9_]*)\}?").unwrap();

    for (line_num, line) in source_str.lines().enumerate() {
        for cap in re.captures_iter(line) {
            let var_name = cap.get(1).unwrap().as_str();
            if !var_name.is_empty() {
                let tgt_text = ctx.make_qualified(var_name);
                let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                ctx.add_edge(file_id, &tgt_id, EdgeKind::EnvAccesses,
                    (line_num + 1) as u32, Some(var_name));
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::indexer::context::ExtractionContext;
    use crate::traits::{EdgeKind, NodeKind};
    use tree_sitter::Parser;

    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_bash::LANGUAGE.into())
            .expect("set bash language");
        let tree = parser.parse(source, None).expect("parse bash source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "bash".to_string());
        BashExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges<'a>(ctx: &'a ExtractionContext, kind: EdgeKind) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    // ------------------------------------------------------------------
    // Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.sh");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("# just a comment\n", "src/comments.sh");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_function() {
        let ctx = extract("myfunc() {\n  echo hello\n}\n", "src/test.sh");
        let funcs = find_nodes(&ctx, NodeKind::BashFunction);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "myfunc");
    }

    #[test]
    fn test_extract_function_keyword() {
        let ctx = extract("function greet {\n  echo hello\n}\n", "src/test.sh");
        let funcs = find_nodes(&ctx, NodeKind::BashFunction);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "greet");
    }

    #[test]
    fn test_extract_multiple_functions() {
        let ctx = extract("a() { :; }\nb() { :; }\nc() { :; }\n", "src/test.sh");
        let funcs = find_nodes(&ctx, NodeKind::BashFunction);
        assert_eq!(funcs.len(), 3);
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_variable_assignment() {
        let ctx = extract("CONFIG_PATH=/etc/app\nVERSION=1.0\n", "src/test.sh");
        let vars = find_nodes(&ctx, NodeKind::BashVariable);
        assert_eq!(vars.len(), 2);
        let names: Vec<&str> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"CONFIG_PATH"));
        assert!(names.contains(&"VERSION"));
    }

    #[test]
    fn test_extract_export_variable() {
        let ctx = extract("export DATABASE_URL=postgresql://localhost/db\n", "src/test.sh");
        let vars = find_nodes(&ctx, NodeKind::BashVariable);
        // The export command may produce a declaration_command that wraps the variable_assignment
        assert!(vars.len() >= 1, "Expected at least one variable node");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract("build_project() {\n  compile_sources\n}\n", "src/test.sh");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"compile_sources"), "Expected compile_sources in calls: {:?}", targets);
    }

    #[test]
    fn test_builtin_commands_filtered() {
        let ctx = extract("setup() {\n  echo 'starting'\n  cd /tmp\n}\n", "src/test.sh");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.contains(&"echo"));
        assert!(!targets.contains(&"cd"));
    }

    // ------------------------------------------------------------------
    // Import extraction (source / .)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_source_import() {
        let ctx = extract("source lib/utils.sh\n", "src/test.sh");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for source");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"lib/utils.sh"));
    }

    #[test]
    fn test_extract_dot_import() {
        let ctx = extract(". ./config.sh\n", "src/test.sh");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"./config.sh"));
    }

    // ------------------------------------------------------------------
    // Env access extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_env_variable_access() {
        let ctx = extract("setup() {\n  echo \"$HOME\"\n}\n", "src/test.sh");
        let env_edges = find_edges(&ctx, EdgeKind::EnvAccesses);
        let targets: Vec<&str> = env_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"HOME"), "Expected HOME env access, got: {:?}", targets);
    }

    #[test]
    fn test_extract_braced_env_variable() {
        let ctx = extract("echo \"${USER:-default}\"\n", "src/test.sh");
        let env_edges = find_edges(&ctx, EdgeKind::EnvAccesses);
        let targets: Vec<&str> = env_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"USER"), "Expected USER env access, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("myfunc() { echo hi; }\n", "src/test.sh");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Nested constructs
    // ------------------------------------------------------------------

    #[test]
    fn test_call_inside_if() {
        let ctx = extract("check() {\n  if [[ -f /tmp/x ]]; then\n    cleanup\n  fi\n}\n", "src/test.sh");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"cleanup"), "Expected cleanup in calls: {:?}", targets);
    }

    #[test]
    fn test_call_inside_for_loop() {
        let ctx = extract("process() {\n  for f in *.txt; do\n    handle \"$f\"\n  done\n}\n", "src/test.sh");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"handle"), "Expected handle in calls: {:?}", targets);
    }
}
