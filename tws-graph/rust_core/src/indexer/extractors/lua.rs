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
//! - `references`: cross-file module references from require (target_text = "module::")

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
// LuaWalker — stateful tree walker with import tracking
// ---------------------------------------------------------------------------

struct LuaWalker {
    /// Maps local variable names to their source module name.
    /// e.g., `local http = require("http")` → "http" → "http"
    /// e.g., `local log = require("logging")` → "log" → "logging"
    imported_names: HashMap<String, String>,
}

impl LuaWalker {
    fn new() -> Self {
        Self {
            imported_names: HashMap::new(),
        }
    }

    fn walk_node(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "function_declaration" | "function_definition" => {
                        self.extract_function(source, child, ctx, parent_id);
                    }
                    "variable_declaration" | "local_variable_declaration" => {
                        self.extract_variable(source, child, ctx, parent_id);
                    }
                    "function_call" => {
                        self.extract_call(source, child, ctx, parent_id, &[]);
                    }
                    // Recurse into other structural nodes
                    "if_statement" | "for_statement" | "while_statement"
                    | "repeat_statement" | "do_statement" | "return_statement" => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                    _ => {
                        // Recurse into generic blocks and other nodes
                        self.walk_node(source, child, ctx, parent_id);
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let name = get_node_name(source, node);
        if name.is_empty() {
            return;
        }

        let func_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains,
            (node.start_position().row + 1) as u32, Some(&name));

        // Recurse into the function body for nested calls
        self.walk_node(source, node, ctx, &func_id);
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    fn extract_variable(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        // Collect variable names being assigned (identifiers on LHS)
        let var_names = self.collect_var_names(source, node);

        // Process all children to extract variable nodes and recurse
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "identifier" | "dot_index_expression" | "bracket_index_expression" => {
                        let name = child.utf8_text(source).unwrap_or("unknown");
                        if !name.is_empty() && name != "unknown" {
                            ctx.add_node(NodeKind::Variable, name, &child, HashMap::new());
                        }
                    }
                    "function_call" => {
                        self.extract_call(source, child, ctx, parent_id, &var_names);
                    }
                    "assignment_statement" | "variable_declaration" | "local_variable_declaration" => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                    _ => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                }
            }
        }
    }

    /// Collect identifier names from variable declarations (LHS of assignment).
    fn collect_var_names(&mut self, source: &[u8], node: Node) -> Vec<String> {
        let mut names = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "identifier" => {
                        let name = child.utf8_text(source).unwrap_or("").to_string();
                        if !name.is_empty() {
                            names.push(name);
                        }
                    }
                    "variable_declaration" | "local_variable_declaration" | "assignment_statement" => {
                        let nested = self.collect_var_names(source, child);
                        names.extend(nested);
                    }
                    "variable_list" => {
                        // Lua variable_list contains identifiers
                        for j in 0..child.named_child_count() {
                            if let Some(vc) = child.named_child(j) {
                                if vc.kind() == "identifier" {
                                    let name = vc.utf8_text(source).unwrap_or("").to_string();
                                    if !name.is_empty() {
                                        names.push(name);
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        names
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        var_names: &[String],
    ) {
        let call_name = get_node_name(source, node);
        if call_name.is_empty() {
            return;
        }

        // require() calls are imports — handle before builtin filter
        if call_name == "require" {
            let arg_name = extract_require_arg(source, node);
            if !arg_name.is_empty() {
                self.create_require_references(ctx, parent_id, &arg_name, node);

                // Map variable names from assignment context (passed via var_names)
                for var_name in var_names {
                    self.imported_names.insert(var_name.clone(), arg_name.clone());
                }

                // Also walk up the parent chain to find variable declarations
                // in case var_names wasn't passed (e.g., through walk_node recursion)
                self.map_require_to_parent_vars(source, &node, &arg_name);

                // Add module name → module name for simple unqualified uses
                let base = arg_name.split('.').next().unwrap_or(&arg_name).to_string();
                self.imported_names.insert(base.clone(), arg_name.clone());
            }
            return;
        }

        // Filter builtins after checking for require
        if is_lua_builtin(&call_name) {
            return;
        }

        // Check if this is an obj.method() or obj:method() call where
        // the receiver might be an imported module
        let (receiver, method) = split_method_call(&call_name);
        if !receiver.is_empty() && !method.is_empty() {
            if let Some(module_name) = self.imported_names.get(&receiver) {
                // Qualified target_text: module::method format for resolver
                let qualified = format!("{}::{}", module_name, method);
                let tgt_text = ctx.make_qualified(&qualified);
                let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
                    (node.start_position().row + 1) as u32, Some(&qualified));
                return;
            }
        }

        let tgt_text = ctx.make_qualified(&call_name);
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
            (node.start_position().row + 1) as u32, Some(&call_name));
    }

    /// Walk up the parent chain from a require() call to find variable
    /// declarations, and map any identifier variable names to the module.
    fn map_require_to_parent_vars(
        &mut self,
        source: &[u8],
        require_node: &Node,
        arg_name: &str,
    ) {
        let mut p = require_node.parent();
        while let Some(pp) = p {
            match pp.kind() {
                "variable_declaration" | "local_variable_declaration" => {
                    let var_names = self.collect_var_names(source, pp);
                    for vn in var_names {
                        self.imported_names.insert(vn, arg_name.to_string());
                    }
                    break;
                }
                _ => {
                    p = pp.parent();
                }
            }
        }
    }

    /// Create REFERENCES edge for a require("module") call.
    /// target_text uses the "module::" format so the resolver can parse it.
    fn create_require_references(
        &mut self,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        arg_name: &str,
        node: Node,
    ) {
        let mod_text = ctx.make_qualified(arg_name);
        let mod_id = hash_id(&ctx.file_path, &mod_text);
        ctx.add_edge(parent_id, &mod_id, EdgeKind::Imports,
            (node.start_position().row + 1) as u32, Some(arg_name));

        // REFERENCES edge with "module::" format for cross-file resolution
        let ref_tgt = format!("{}::", arg_name);
        let ref_tgt_qualified = ctx.make_qualified(&ref_tgt);
        let ref_id = hash_id(&ctx.file_path, &ref_tgt_qualified);
        ctx.add_edge(parent_id, &ref_id, EdgeKind::References,
            (node.start_position().row + 1) as u32, Some(&ref_tgt));
    }
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

        let mut walker = LuaWalker::new();
        walker.walk_node(source, root, ctx, &file_id);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

/// Split "obj.method" or "obj:method" into (receiver, method) parts.
/// Returns (receiver, method) or ("", "") if not a method call.
fn split_method_call(call_name: &str) -> (String, String) {
    // Handle obj:method() — Lua style method call
    if let Some(pos) = call_name.find(':') {
        let receiver = &call_name[..pos];
        let method = &call_name[pos + 1..];
        if receiver.contains('.') {
            // obj.field:method — use the full receiver
            return (receiver.to_string(), method.to_string());
        }
        return (receiver.to_string(), method.to_string());
    }

    // Handle obj.method() — dot-index call
    if let Some(pos) = call_name.rfind('.') {
        let receiver = &call_name[..pos];
        let method = &call_name[pos + 1..];
        return (receiver.to_string(), method.to_string());
    }

    (String::new(), String::new())
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
    // Stage 14: Cross-file require REFERENCES edges
    // ------------------------------------------------------------------

    #[test]
    fn test_require_creates_references_edge() {
        let ctx = extract("require('http')\n", "src/test.lua");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for require");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"http::"));
    }

    #[test]
    fn test_require_nested_module_references() {
        let ctx = extract("require('foo.bar')\n", "src/test.lua");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"foo.bar::"), "Expected foo.bar:: for nested require");
    }

    #[test]
    fn test_require_in_local_assignment_references() {
        let ctx = extract("local http = require('http')\n", "src/test.lua");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"http::"), "Expected REFERENCES edge for local require assignment");
        // Also verify the IMPORTS edge is kept
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for local require assignment");
    }

    #[test]
    fn test_require_in_function_references() {
        let ctx = extract("function init()\n  require('mymod')\nend\n", "src/test.lua");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"mymod::"), "Expected REFERENCES edge for require in function");
        // IMPORTS edge should also exist
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for require in function");
    }

    #[test]
    fn test_multiple_require_creates_multiple_references() {
        let ctx = extract(
            "local http = require('http')\nlocal json = require('json')\nrequire('utils')\n",
            "src/test.lua",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"http::"), "Expected http:: REF");
        assert!(targets.contains(&"json::"), "Expected json:: REF");
        assert!(targets.contains(&"utils::"), "Expected utils:: REF");
        assert_eq!(refs.len(), 3, "Expected 3 REFERENCES edges");
    }

    #[test]
    fn test_require_alternative_syntax() {
        let ctx = extract("require 'alternative'\n", "src/test.lua");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"alternative::"), "Expected REFERENCES edge for require without parens");
    }

    #[test]
    fn test_imported_names_qualified_method_call() {
        // When we have `local http = require("http")`, then `http.get()`,
        // the call should use a qualified target_text
        let ctx = extract(
            "local http = require('http')\nfunction fetch()\n  http.get('/api')\nend\n",
            "src/test.lua",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // http.get should be qualified as http::get
        assert!(targets.iter().any(|t| t.contains("http::get")),
            "Expected qualified call target http::get, got: {:?}", targets);
    }

    #[test]
    fn test_unimported_method_call_uses_bare_name() {
        // Without a require, method calls should use bare names
        let ctx = extract(
            "function test()\n  obj.method()\nend\n",
            "src/test.lua",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // Should have the method call but NOT qualified with ::
        let has_qualified = targets.iter().any(|t| t.contains("obj::method"));
        assert!(!has_qualified, "Unimported calls should not be qualified, got: {:?}", targets);
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
