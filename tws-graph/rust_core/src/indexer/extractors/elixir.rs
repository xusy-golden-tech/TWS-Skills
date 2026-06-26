//! Elixir language extractor.
//!
//! Extracts symbols and relationships from Elixir source files (`.ex`, `.exs`)
//! using the tree-sitter-elixir grammar.
//!
//! # Node kinds produced
//! - `module`: defmodule definition
//! - `function`: def/defp definition
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function calls (including module.function dot calls)
//! - `contains`: containment (file -> module -> function)
//! - `imports`: import declarations

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// ElixirExtractor
// ---------------------------------------------------------------------------

pub struct ElixirExtractor;

impl Extractor for ElixirExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["ex", "exs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["elixir"]
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
    let kind = node.kind();

    if kind == "call" {
        // Determine what kind of call this is based on the identifier
        let identifier = find_child_by_kind(node, "identifier");
        if let Some(id_node) = identifier {
            let id_text = get_text(source, Some(id_node));
            match id_text.as_str() {
                "defmodule" => {
                    return extract_module(source, node, ctx, parent_id);
                }
                "def" | "defp" => {
                    return extract_function(source, node, ctx, parent_id);
                }
                "import" => {
                    return extract_import(source, node, ctx, parent_id);
                }
                _ => {
                    // Regular function call
                    return extract_call(source, node, ctx, parent_id);
                }
            }
        }
        // Call without identifier — could be a dot call or pipe
        return extract_call(source, node, ctx, parent_id);
    }

    // Recurse into children for unrecognized nodes
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Module extraction (defmodule MyApp.Greeter do ... end)
// ---------------------------------------------------------------------------

fn extract_module(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Module name is in arguments -> alias
    let module_name = if let Some(args) = find_child_by_kind(node, "arguments") {
        if let Some(alias) = find_child_by_kind(args, "alias") {
            get_text(source, Some(alias))
        } else {
            find_first_identifier_text(source, args)
        }
    } else {
        String::new()
    };

    if module_name.is_empty() {
        return Ok(());
    }

    let mod_id = ctx.add_node(NodeKind::Module, &module_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &mod_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&module_name, "module");
    ctx.push_scope_node(&mod_id);

    // Walk the do_block body
    if let Some(do_block) = find_child_by_kind(node, "do_block") {
        walk_all_children(source, do_block, ctx, &mod_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Function extraction (def hello do ... end / defp hello do ... end)
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Function name: in arguments, there may be a nested call with the function name
    // e.g., def hello(name) → arguments contains call: hello(name) → identifier: hello
    let fn_name = if let Some(args) = find_child_by_kind(node, "arguments") {
        // Look for the first identifier (not in a dot call)
        find_deep_identifier_text(source, args)
    } else {
        String::new()
    };

    if fn_name.is_empty() {
        return Ok(());
    }

    let fn_id = ctx.add_node(NodeKind::Function, &fn_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&fn_name, "function");
    ctx.push_scope_node(&fn_id);

    // Walk the do_block body
    if let Some(do_block) = find_child_by_kind(node, "do_block") {
        walk_all_children(source, do_block, ctx, &fn_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Import extraction (import Logger)
// ---------------------------------------------------------------------------

fn extract_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Import target in arguments
    if let Some(args) = find_child_by_kind(node, "arguments") {
        if let Some(alias) = find_child_by_kind(args, "alias") {
            let import_name = get_text(source, Some(alias));
            if !import_name.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, &import_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_name));
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Handle dot calls: Logger.info(...) → "Logger.info"
    if let Some(dot) = find_child_by_kind(node, "dot") {
        let mut parts: Vec<String> = Vec::new();
        collect_dot_call_parts(source, dot, &mut parts);
        if !parts.is_empty() {
            let call_name = parts.join(".");
            let target_qn = build_qualified_target(&ctx.file_path, &call_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&call_name));
        }
        return Ok(());
    }

    // Handle direct calls by identifier
    if let Some(id_node) = find_child_by_kind(node, "identifier") {
        let callee = get_text(source, Some(id_node));
        // Skip keywords like defmodule, def, defp, import (they're not actual calls)
        if !callee.is_empty()
            && callee != "defmodule"
            && callee != "def"
            && callee != "defp"
            && callee != "import"
            && callee != "alias"
        {
            let target_qn = build_qualified_target(&ctx.file_path, &callee);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee));
        }
    }

    Ok(())
}

fn collect_dot_call_parts(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "alias" | "identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                "dot" => {
                    collect_dot_call_parts(source, child, parts);
                }
                _ => {}
            }
        }
    }
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

/// Find the text of the first identifier found via shallow or recursive search.
fn find_first_identifier_text(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" {
                return get_text(source, Some(child));
            }
            let result = find_first_identifier_text(source, child);
            if !result.is_empty() {
                return result;
            }
        }
    }
    String::new()
}

/// Find the first identifier's text recursively, skipping "dot" nodes
/// to handle the nested call structure in Elixir function definitions.
fn find_deep_identifier_text(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let kind = child.kind();
            if kind == "identifier" {
                return get_text(source, Some(child));
            }
            if kind == "call" {
                // In args like hello(name), there's a nested call with an identifier
                let result = find_deep_identifier_text(source, child);
                if !result.is_empty() {
                    return result;
                }
            }
            // Skip dot nodes (they're for module. prefix)
            if kind != "dot" {
                let result = find_deep_identifier_text(source, child);
                if !result.is_empty() {
                    return result;
                }
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
        let language: tree_sitter::Language = tree_sitter_elixir::LANGUAGE.into();
        parser.set_language(&language).expect("set elixir language");
        let tree = parser.parse(source, None).expect("parse elixir source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "elixir".to_string());
        ElixirExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges(ctx: &ExtractionContext, kind: EdgeKind) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    // ------------------------------------------------------------------
    // Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.ex");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Module extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_module() {
        let ctx = extract("defmodule MyApp do\nend\n", "src/test.ex");
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "MyApp");
    }

    #[test]
    fn test_nested_module_name() {
        let ctx = extract(
            "defmodule MyApp.Greeter do\nend\n",
            "src/test.ex",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "MyApp.Greeter");
    }

    #[test]
    fn test_module_with_function() {
        let ctx = extract(
            "defmodule MyApp do\n  def hello do\n    :world\n  end\nend\n",
            "src/test.ex",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "hello");
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_def_function() {
        let ctx = extract("def greet do\n  IO.puts(\"hello\")\nend\n", "src/test.ex");
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "greet");
    }

    #[test]
    fn test_defp_function() {
        let ctx = extract("defp private_thing do\n  42\nend\n", "src/test.ex");
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "private_thing");
    }

    #[test]
    fn test_function_with_args() {
        let ctx = extract(
            "def add(a, b) do\n  a + b\nend\n",
            "src/test.ex",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "add");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract(
            "defmodule MyApp do\n  import Logger\nend\n",
            "src/test.ex",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        assert!(imports.iter().any(|e| e.target_text.as_deref() == Some("Logger")));
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_call() {
        let ctx = extract(
            "defmodule MyApp do\n  def run do\n    do_work()\n  end\nend\n",
            "src/test.ex",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edge");
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("do_work")));
    }

    #[test]
    fn test_dot_call() {
        let ctx = extract(
            "defmodule MyApp do\n  def run do\n    Logger.info(\"hello\")\n  end\nend\n",
            "src/test.ex",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edge for dot call");
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("Logger.info")));
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_file_contains_module() {
        let ctx = extract("defmodule MyApp do\nend\n", "src/test.ex");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Nested structures
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_module() {
        let ctx = extract(
            "defmodule Outer do\n  defmodule Inner do\n    def work do\n    end\n  end\nend\n",
            "src/test.ex",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 2);
        let names: Vec<&str> = modules.iter().map(|m| m.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    #[test]
    fn test_multiple_functions() {
        let ctx = extract(
            "defmodule MyApp do\n  def first do\n  end\n  def second do\n  end\nend\n",
            "src/test.ex",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 2);
    }

    #[test]
    fn test_module_call() {
        let ctx = extract(
            "defmodule MyApp do\n  def start do\n    Supervisor.start_link([])\n  end\nend\n",
            "src/test.ex",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("Supervisor.start_link")));
    }
}
