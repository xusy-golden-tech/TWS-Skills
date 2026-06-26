//! Lua language extractor.
//!
//! Extracts symbols and relationships from Lua source files (`.lua`)
//! using the tree-sitter-lua grammar.
//!
//! # Node kinds produced
//! - `function`: function definition
//! - `variable`: variable declaration / assignment
//! - `lua_table`: table constructor
//!
//! # Edge kinds produced
//! - `calls`: function calls
//! - `contains`: containment (file -> function/table)
//! - `imports`: `require("...")` calls

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// Lua standard library function names -- filtered from calls.
const LUA_BUILTINS: &[&str] = &[
    "print", "type", "tonumber", "tostring", "assert", "error",
    "ipairs", "pairs", "next", "rawget", "rawset", "rawlen",
    "rawequal", "select", "pcall", "xpcall", "setmetatable",
    "getmetatable", "require", "load", "loadfile", "dofile",
    "os", "io", "math", "string", "table", "coroutine", "debug",
    "utf8", "package",
];

fn is_lua_builtin(name: &str) -> bool {
    LUA_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// LuaExtractor
// ---------------------------------------------------------------------------

pub struct LuaExtractor;

impl Extractor for LuaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["lua"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["lua"]
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

        walk_node(source, root, ctx, &file_id);

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
                "function_declaration" | "function_definition" => {
                    extract_function(source, child, ctx, parent_id);
                }
                "variable_declaration" | "local_variable_declaration" => {
                    extract_variable(source, child, ctx, parent_id);
                }
                "function_call" => {
                    extract_call(source, child, ctx, parent_id);
                }
                // Recurse into other structural nodes
                "if_statement" | "for_statement" | "while_statement"
                | "repeat_statement" | "do_statement" | "return_statement" => {
                    walk_node(source, child, ctx, parent_id);
                }
                _ => {
                    // Recurse into generic blocks and other nodes
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
    // Determine function name from the node
    let name = get_node_name(source, node);

    if name.is_empty() {
        return;
    }

    let func_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, (node.start_position().row + 1) as u32, Some(&name));

    // Recurse into the function body for nested calls
    walk_node(source, node, ctx, &func_id);
}

// ---------------------------------------------------------------------------
// Variable extraction
// ---------------------------------------------------------------------------

fn extract_variable(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Look for identifier(s) on the left side of the assignment
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "dot_index_expression" | "bracket_index_expression" => {
                    let name = child.utf8_text(source).unwrap_or("unknown");
                    if !name.is_empty() && name != "unknown" {
                        ctx.add_node(NodeKind::Variable, name, &child, HashMap::new());
                    }
                }
                "assignment_statement" | "variable_declaration" | "local_variable_declaration" => {
                    extract_variable(source, child, ctx, parent_id);
                }
                "function_call" => {
                    extract_call(source, child, ctx, parent_id);
                }
                _ => {
                    walk_node(source, child, ctx, parent_id);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let call_name = get_node_name(source, node);
    if call_name.is_empty() {
        return;
    }

    // require() calls are imports (handle before builtin filter)
    if call_name == "require" {
        let arg_name = extract_require_arg(source, node);
        if !arg_name.is_empty() {
            let mod_text = ctx.make_qualified(&arg_name);
            let mod_id = hash_id(&ctx.file_path, &mod_text);
            ctx.add_edge(parent_id, &mod_id, EdgeKind::Imports,
                (node.start_position().row + 1) as u32, Some(&arg_name));
        }
        return;
    }

    // Filter builtins after checking for require
    if is_lua_builtin(&call_name) {
        return;
    }

    let tgt_text = ctx.make_qualified(&call_name);
    let tgt_id = hash_id(&ctx.file_path, &tgt_text);
    ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
        (node.start_position().row + 1) as u32, Some(&call_name));
}

/// Extract the first string argument from a require() call
fn extract_require_arg(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "arguments" {
                for j in 0..child.named_child_count() {
                    if let Some(arg) = child.named_child(j) {
                        if arg.kind() == "string" {
                            let s = arg.utf8_text(source).unwrap_or("");
                            return s.trim_matches(|c| c == '"' || c == '\'').to_string();
                        }
                    }
                }
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract the name of a node (identifier of a function/variable/call).
fn get_node_name(source: &[u8], node: Node) -> String {
    // First try to find an "identifier" child (for calls like `foo()`)
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if child.kind() == "identifier" || child.kind() == "name" {
                return child.utf8_text(source).unwrap_or("").to_string();
            }
        }
    }

    // For functions: look for a "name" field or first identifier before parameters
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" => return child.utf8_text(source).unwrap_or("").to_string(),
                "dot_index_expression" | "method_index_expression" => {
                    // For obj:method() style calls, return the full text
                    return child.utf8_text(source).unwrap_or("").to_string();
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
        parser
            .set_language(&tree_sitter_lua::LANGUAGE.into())
            .expect("set lua language");
        let tree = parser.parse(source, None).expect("parse lua source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "lua".to_string());
        LuaExtractor
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
        let ctx = extract("", "src/empty.lua");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("-- just a comment\n--[[ block comment ]]--\n", "src/comments.lua");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_function() {
        let ctx = extract("function foo()\n  return 1\nend\n", "src/test.lua");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "foo");
    }

    #[test]
    fn test_extract_local_function() {
        let ctx = extract("local function bar()\n  print('hello')\nend\n", "src/test.lua");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "bar");
    }

    #[test]
    fn test_extract_multiple_functions() {
        let ctx = extract(
            "function a() end\nfunction b() end\nfunction c() end\n",
            "src/test.lua",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 3);
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract("function main()\n  do_work()\nend\n", "src/test.lua");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least one CALLS edge");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"do_work"));
    }

    #[test]
    fn test_builtin_calls_filtered() {
        let ctx = extract("function main()\n  print('hello')\n  type(1)\nend\n", "src/test.lua");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // Builtins like print and type should not appear as call targets
        assert!(!targets.contains(&"print"));
        assert!(!targets.contains(&"type"));
    }

    // ------------------------------------------------------------------
    // Import extraction (require)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_require() {
        let ctx = extract("function setup()\n  local http = require('http')\nend\n", "src/test.lua");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for require");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"http"));
    }

    // ------------------------------------------------------------------
    // Nested constructs
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_function_calls() {
        let ctx = extract(
            "function outer()\n  function inner()\n    do_stuff()\n  end\nend\n",
            "src/test.lua",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 2);
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty());
    }

    #[test]
    fn test_if_block_call() {
        let ctx = extract(
            "function check()\n  if true then\n    log('ok')\n  end\nend\n",
            "src/test.lua",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"log"));
    }

    #[test]
    fn test_for_loop_call() {
        let ctx = extract(
            "function process()\n  for i=1,10 do\n    handle(i)\n  end\nend\n",
            "src/test.lua",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"handle"));
    }

    #[test]
    fn test_contains_edges() {
        let ctx = extract("function main() end\n", "src/test.lua");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }
}
