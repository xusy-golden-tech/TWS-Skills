//! CMake language extractor.
//!
//! Extracts symbols and relationships from CMake source files
//! (`CMakeLists.txt`, `.cmake`) using the tree-sitter-cmake grammar.
//!
//! # Node kinds produced
//! - `function`: function/macro definition
//! - `variable`: set() / option() variable declarations
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: command invocations (add_executable, target_link_libraries, etc.)
//! - `contains`: containment (file -> function)
//! - `imports`: find_package, include, add_subdirectory

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// CmakeExtractor
// ---------------------------------------------------------------------------

pub struct CmakeExtractor;

impl Extractor for CmakeExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cmake"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["cmake"]
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
                .unwrap_or("module")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking
// ---------------------------------------------------------------------------

fn walk_node(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    match node.kind() {
        "function_def" => extract_function(source, node, ctx, parent_id, false)?,
        "macro_def" => extract_function(source, node, ctx, parent_id, true)?,
        "normal_command" => extract_normal_command(source, node, ctx, parent_id)?,
        "foreach_loop" | "while_loop" => {
            // Walk body of loop constructs
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
        // Walk all other constructs recursively (body, block_def, if_command, etc.)
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Function / macro extraction
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    is_macro: bool,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Find the function_command/macro_command child, then extract name from its argument_list
    let command_kind = if is_macro { "macro_command" } else { "function_command" };
    let name = if let Some(cmd_node) = find_child_by_kind(node, command_kind) {
        get_first_argument(source, cmd_node)
    } else {
        String::new()
    };
    if name.is_empty() {
        return Ok(());
    }

    let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, if is_macro { "macro" } else { "function" });
    ctx.push_scope_node(&fn_id);

    // Walk body for internal commands
    if let Some(body) = find_child_by_kind(node, "body") {
        walk_all_children(source, body, ctx, &fn_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Normal command extraction (calls, variables, imports)
// ---------------------------------------------------------------------------

fn extract_normal_command(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Command name is in the `identifier` child
    let cmd_name = get_command_name(source, node);
    if cmd_name.is_empty() {
        return Ok(());
    }

    match cmd_name.to_lowercase().as_str() {
        // Variable definitions
        "set" | "option" => {
            extract_variable_command(source, node, ctx, parent_id, line)?;
        }
        // Import-like commands
        "find_package" | "include" | "add_subdirectory" => {
            extract_import_command(source, node, ctx, parent_id, line)?;
        }
        // All other commands produce call edges
        _ => {
            if !is_cmake_builtin(&cmd_name) {
                let target_qn = build_qualified_target(&ctx.file_path, &cmd_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&cmd_name));
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Variable command (set / option)
// ---------------------------------------------------------------------------

fn extract_variable_command(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
) -> anyhow::Result<()> {
    // set(VAR value) or option(VAR description default)
    // First argument is the variable name (command name is separate in identifier)
    let args = get_all_arguments(source, node);
    if !args.is_empty() {
        let var_name = &args[0];
        let var_id = ctx.add_node(NodeKind::Variable, var_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Import command (find_package / include / add_subdirectory)
// ---------------------------------------------------------------------------

fn extract_import_command(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
) -> anyhow::Result<()> {
    // First argument is the package/module name (command name is separate in identifier)
    let args = get_all_arguments(source, node);
    if !args.is_empty() {
        let name = &args[0];
        let target_qn = build_qualified_target(&ctx.file_path, name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(name));
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Builtin filter
// ---------------------------------------------------------------------------

fn is_cmake_builtin(name: &str) -> bool {
    let lower = name.to_lowercase();
    matches!(
        lower.as_str(),
        "if" | "else" | "elseif" | "endif" | "foreach" | "endforeach" | "while"
            | "endwhile" | "endfunction" | "endmacro" | "return" | "break" | "continue"
            | "cmake_minimum_required" | "project" | "message" | "file" | "string"
            | "list" | "math" | "separate_arguments" | "configure_file" | "install"
            | "cmake_parse_arguments"
    )
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn walk_all_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
}

fn build_qualified_target(file_path: &str, name: &str) -> String {
    format!("{file_path}::{name}")
}

fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n.utf8_text(source).map(|c| c.to_string()).unwrap_or_default(),
        None => String::new(),
    }
}

fn find_child_by_kind<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}

fn get_command_name(source: &[u8], node: Node) -> String {
    // The command name is in the `identifier` child of a normal_command
    if let Some(id_node) = find_child_by_kind(node, "identifier") {
        return get_text(source, Some(id_node));
    }
    String::new()
}

fn get_first_argument(source: &[u8], node: Node) -> String {
    // The first argument is in the argument_list child's first unquoted_argument
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "argument_list" {
                for j in 0..child.named_child_count() {
                    if let Some(arg_node) = child.named_child(j) {
                        if arg_node.kind() == "argument" {
                            return get_argument_text(source, arg_node);
                        }
                    }
                }
            }
        }
    }
    String::new()
}

fn get_all_arguments(source: &[u8], node: Node) -> Vec<String> {
    let mut args = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "argument_list" {
                for j in 0..child.named_child_count() {
                    if let Some(arg_node) = child.named_child(j) {
                        if arg_node.kind() == "argument" {
                            args.push(get_argument_text(source, arg_node));
                        }
                    }
                }
            }
        }
    }
    args
}

fn get_argument_text(source: &[u8], arg_node: Node) -> String {
    // An argument can be unquoted_argument, quoted_argument, or bracket_argument
    for i in 0..arg_node.named_child_count() {
        if let Some(child) = arg_node.named_child(i) {
            match child.kind() {
                "unquoted_argument" => return get_text(source, Some(child)),
                "quoted_argument" => {
                    return get_text(source, Some(child))
                        .trim_matches('"')
                        .to_string()
                }
                "bracket_argument" => {
                    let text = get_text(source, Some(child));
                    // Bracket args: find content between [[ and ]]
                    if let Some(start) = text.find("[[") {
                        let after_start = &text[start + 2..];
                        if let Some(end) = after_start.rfind("]]") {
                            return after_start[..end].to_string();
                        }
                    }
                    return text;
                }
                _ => {}
            }
        }
    }
    String::new()
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
        let language: tree_sitter::Language = tree_sitter_cmake::LANGUAGE.into();
        parser.set_language(&language).expect("set cmake language");
        let tree = parser.parse(source, None).expect("parse cmake source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "cmake".to_string());
        CmakeExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    fn find_edges(ctx: &ExtractionContext, kind: EdgeKind) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result
            .edges
            .iter()
            .filter(|e| e.kind == kind_str)
            .collect()
    }

    // ------------------------------------------------------------------
    // 1. Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("# Just a comment\n", "empty.cmake");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // 2. Function definition
    // ------------------------------------------------------------------

    #[test]
    fn test_function_definition() {
        let ctx = extract(
            "function(add_test_target target_name sources)\n  add_executable(${target_name} ${sources})\nendfunction()\n",
            "tests/CMakeLists.txt",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(!funcs.is_empty(), "Expected at least 1 function");
        let names: Vec<_> = funcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"add_test_target"));
    }

    // ------------------------------------------------------------------
    // 3. Macro definition
    // ------------------------------------------------------------------

    #[test]
    fn test_macro_definition() {
        let ctx = extract(
            "macro(set_common_flags target)\n  target_compile_options(${target} PRIVATE -Wall)\nendmacro()\n",
            "tests/CMakeLists.txt",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        let names: Vec<_> = funcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"set_common_flags"), "Expected macro 'set_common_flags'");
    }

    // ------------------------------------------------------------------
    // 4. Variable extraction (set)
    // ------------------------------------------------------------------

    #[test]
    fn test_set_variable() {
        let ctx = extract(
            "set(PROJECT_NAME \"MyApp\")\nset(VERSION 2.0)\nset(SOURCE_DIRS src/core src/utils)\n",
            "tests/CMakeLists.txt",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 3, "Expected >=3 variables, got {}", vars.len());
        let names: Vec<_> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"PROJECT_NAME"));
        assert!(names.contains(&"VERSION"));
        assert!(names.contains(&"SOURCE_DIRS"));
    }

    // ------------------------------------------------------------------
    // 5. Option extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_option_variable() {
        let ctx = extract(
            "option(BUILD_TESTS \"Build the test suite\" ON)\noption(ENABLE_LOGGING \"Enable debug logging\" OFF)\n",
            "tests/CMakeLists.txt",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        let names: Vec<_> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"BUILD_TESTS"));
        assert!(names.contains(&"ENABLE_LOGGING"));
    }

    // ------------------------------------------------------------------
    // 6. Command calls
    // ------------------------------------------------------------------

    #[test]
    fn test_command_calls() {
        let ctx = extract(
            "add_executable(my_app main.cpp)\nadd_library(core_lib STATIC src/core.cpp)\n",
            "tests/CMakeLists.txt",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edges");
    }

    // ------------------------------------------------------------------
    // 7. Import commands
    // ------------------------------------------------------------------

    #[test]
    fn test_find_package() {
        let ctx = extract(
            "find_package(Boost REQUIRED)\nfind_package(OpenSSL)\n",
            "tests/CMakeLists.txt",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 2, "Expected >=2 import edges, got {}", imports.len());
    }

    // ------------------------------------------------------------------
    // 8. Include command
    // ------------------------------------------------------------------

    #[test]
    fn test_include() {
        let ctx = extract(
            "include(CTest)\ninclude(GenerateExportHeader)\n",
            "tests/CMakeLists.txt",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORT edges for include");
    }

    // ------------------------------------------------------------------
    // 9. Add subdirectory
    // ------------------------------------------------------------------

    #[test]
    fn test_add_subdirectory() {
        let ctx = extract(
            "add_subdirectory(third_party)\n",
            "tests/CMakeLists.txt",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for add_subdirectory");
    }

    // ------------------------------------------------------------------
    // 10. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract(
            "function(my_func)\n  message(\"hello\")\nendfunction()\n",
            "tests/CMakeLists.txt",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 11. Nested conditionals
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_conditionals() {
        let ctx = extract(
            "if(WIN32)\n  set(PLATFORM \"windows\")\n  add_executable(win_helper win_helper.cpp)\nelse()\n  set(PLATFORM \"unix\")\nendif()\n",
            "tests/CMakeLists.txt",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        let names: Vec<_> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"PLATFORM"), "Expected 'PLATFORM' variable");
    }

    // ------------------------------------------------------------------
    // 12. Multiple functions
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_functions() {
        let ctx = extract(
            "function(foo)\nendfunction()\nfunction(bar)\n  foo()\nendfunction()\n",
            "tests/CMakeLists.txt",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 2, "Expected 2 functions");
    }

    // ------------------------------------------------------------------
    // 13. Function with call inside
    // ------------------------------------------------------------------

    #[test]
    fn test_function_with_internal_call() {
        let ctx = extract(
            "function(helper)\nendfunction()\nfunction(main)\n  helper()\nendfunction()\n",
            "tests/CMakeLists.txt",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let call_targets: Vec<_> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(call_targets.contains(&"helper"), "Expected call to 'helper'");
    }

    // ------------------------------------------------------------------
    // 14. Macro treated as function
    // ------------------------------------------------------------------

    #[test]
    fn test_macro_as_function() {
        let ctx = extract(
            "macro(my_macro arg1 arg2)\n  set(${arg1} ${arg2})\nendmacro()\n",
            "tests/CMakeLists.txt",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(!funcs.is_empty());
        assert!(funcs.iter().any(|f| f.name == "my_macro"));
    }
}
