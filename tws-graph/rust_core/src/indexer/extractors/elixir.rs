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
//! - `references`: alias, use, require, import cross-file references

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

        let mut walker = ElixirWalker::new();
        walker.walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// ElixirWalker — stateful tree walker with import tracking
// ---------------------------------------------------------------------------

struct ElixirWalker {
    /// Maps alias names to their fully qualified module names.
    /// e.g., `alias MyApp.Utils.Helper, as: H` → "H" → "MyApp.Utils.Helper"
    /// e.g., `alias MyApp.Services.User` → "User" → "MyApp.Services.User"
    imported_names: HashMap<String, String>,
}

impl ElixirWalker {
    fn new() -> Self {
        Self {
            imported_names: HashMap::new(),
        }
    }

    // ------------------------------------------------------------------
    // Tree walking
    // ------------------------------------------------------------------

    fn walk_node(
        &mut self,
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
                        return self.extract_module(source, node, ctx, parent_id);
                    }
                    "def" | "defp" => {
                        return self.extract_function(source, node, ctx, parent_id);
                    }
                    "import" => {
                        return self.extract_import(source, node, ctx, parent_id);
                    }
                    "alias" => {
                        return self.extract_alias(source, node, ctx, parent_id);
                    }
                    "use" => {
                        return self.extract_use(source, node, ctx, parent_id);
                    }
                    "require" => {
                        return self.extract_require(source, node, ctx, parent_id);
                    }
                    _ => {
                        // Regular function call
                        return self.extract_call(source, node, ctx, parent_id);
                    }
                }
            }
            // Call without identifier — could be a dot call or pipe
            return self.extract_call(source, node, ctx, parent_id);
        }

        // Recurse into children for unrecognized nodes
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Module extraction (defmodule MyApp.Greeter do ... end)
    // ------------------------------------------------------------------

    fn extract_module(
        &mut self,
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
            self.walk_all_children(source, do_block, ctx, &mod_id)?;
        }

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Function extraction (def hello do ... end / defp hello do ... end)
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
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
            self.walk_all_children(source, do_block, ctx, &fn_id)?;
        }

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Import extraction (import Logger)
    // ------------------------------------------------------------------

    fn extract_import(
        &mut self,
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
                    // IMPORTS edge (existing behavior)
                    let target_qn = build_qualified_target(&ctx.file_path, &import_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_name));

                    // REFERENCES edge for cross-file resolution (new)
                    // format: "ModuleName::" — module-only reference
                    let ref_target_text = format!("{}::", import_name);
                    let ref_qn = format!("__ref__{}", ref_target_text);
                    let ref_target = hash_id(&ctx.file_path, &ref_qn);
                    ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_target_text));

                    // Track imported module for call qualification
                    // For `import Enum`, bare calls like `map()` could come from Enum
                    // Store the base module name
                    let base_name = import_name.split('.').last().unwrap_or(&import_name);
                    if base_name != import_name {
                        self.imported_names.insert(base_name.to_string(), import_name.clone());
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Alias extraction (alias Foo.Bar / alias Foo.Bar, as: Baz)
    // ------------------------------------------------------------------

    fn extract_alias(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // alias target is in arguments
        if let Some(args) = find_child_by_kind(node, "arguments") {
            // Look for the first alias node (the module being aliased)
            if let Some(alias_node) = find_child_by_kind(args, "alias") {
                let alias_name = get_text(source, Some(alias_node));
                if alias_name.is_empty() {
                    return Ok(());
                }

                // Create REFERENCES edge for cross-file resolution
                // target_text = "Module.Name::"
                let ref_target_text = format!("{}::", alias_name);
                let ref_qn = format!("__ref__{}", ref_target_text);
                let ref_target = hash_id(&ctx.file_path, &ref_qn);
                ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_target_text));

                // Check for `, as: AliasName` — look for keyword "as" in the children
                let as_name = find_alias_as_name(source, args);
                let effective_name = if let Some(ref an) = as_name {
                    self.imported_names.insert(an.clone(), alias_name.clone());
                    an.clone()
                } else {
                    // Default alias: last segment of the module name
                    let short = alias_name.split('.').last().unwrap_or(&alias_name);
                    self.imported_names.insert(short.to_string(), alias_name.clone());
                    short.to_string()
                };

                let _ = effective_name; // used via imported_names for call qualification
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Use extraction (use SomeModule)
    // ------------------------------------------------------------------

    fn extract_use(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // use target in arguments
        if let Some(args) = find_child_by_kind(node, "arguments") {
            if let Some(alias) = find_child_by_kind(args, "alias") {
                let use_name = get_text(source, Some(alias));
                if !use_name.is_empty() {
                    // REFERENCES edge for cross-file resolution
                    let ref_target_text = format!("{}::", use_name);
                    let ref_qn = format!("__ref__{}", ref_target_text);
                    let ref_target = hash_id(&ctx.file_path, &ref_qn);
                    ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_target_text));
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Require extraction (require SomeModule)
    // ------------------------------------------------------------------

    fn extract_require(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // require target in arguments
        if let Some(args) = find_child_by_kind(node, "arguments") {
            if let Some(alias) = find_child_by_kind(args, "alias") {
                let req_name = get_text(source, Some(alias));
                if !req_name.is_empty() {
                    // REFERENCES edge for cross-file resolution
                    let ref_target_text = format!("{}::", req_name);
                    let ref_qn = format!("__ref__{}", ref_target_text);
                    let ref_target = hash_id(&ctx.file_path, &ref_qn);
                    ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_target_text));
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    fn extract_call(
        &self,
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

                // If the first part is an imported alias, qualify it
                let qualified_name = if let Some(first) = parts.first() {
                    if let Some(qualified_module) = self.imported_names.get(first) {
                        // Replace the alias with the full module name
                        let mut qualified_parts: Vec<String> = vec![qualified_module.clone()];
                        qualified_parts.extend(parts.iter().skip(1).cloned());
                        qualified_parts.join(".")
                    } else {
                        call_name.clone()
                    }
                } else {
                    call_name.clone()
                };

                let target_qn = build_qualified_target(&ctx.file_path, &qualified_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified_name));
            }
            return Ok(());
        }

        // Handle direct calls by identifier
        if let Some(id_node) = find_child_by_kind(node, "identifier") {
            let callee = get_text(source, Some(id_node));
            // Skip keywords like defmodule, def, defp, import, alias, use, require
            if !callee.is_empty()
                && callee != "defmodule"
                && callee != "def"
                && callee != "defp"
                && callee != "import"
                && callee != "alias"
                && callee != "use"
                && callee != "require"
            {
                // Check if this is an imported function (e.g., after `import Enum`, `map()`)
                let qualified_name = if let Some(module) = self.imported_names.get(&callee) {
                    format!("{}.{}", module, callee)
                } else {
                    callee.clone()
                };

                let target_qn = build_qualified_target(&ctx.file_path, &qualified_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified_name));
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helpers (standalone functions)
// ---------------------------------------------------------------------------

impl ElixirWalker {
    fn walk_all_children(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }
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

/// Collect all child nodes of a given kind.
fn find_children_by_kind<'a>(node: Node<'a>, kind: &str) -> Vec<Node<'a>> {
    let mut result = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                result.push(child);
            }
        }
    }
    result
}

/// Find the `as:` keyword in alias arguments and return the alias name.
/// e.g., `alias Foo.Bar, as: Baz` → returns Some("Baz")
///
/// Tree structure for `alias Foo.Bar, as: Baz`:
///   arguments
///     alias: "Foo.Bar"
///     keywords
///       pair
///         keyword: "as: "
///         alias: "Baz"  (or identifier)
fn find_alias_as_name(source: &[u8], args: Node) -> Option<String> {
    // Look for a `keywords` child in arguments (contains keyword pairs)
    if let Some(kw_list) = find_child_by_kind(args, "keywords") {
        // Find `pair` children within keywords
        for i in 0..kw_list.named_child_count() {
            if let Some(pair) = kw_list.named_child(i) {
                if pair.kind() == "pair" {
                    // Find the `keyword` child and the value child within the pair
                    let mut kw_text = String::new();
                    let mut value_name = String::new();
                    for j in 0..pair.named_child_count() {
                        if let Some(child) = pair.named_child(j) {
                            match child.kind() {
                                "keyword" => {
                                    kw_text = get_text(source, Some(child))
                                        .trim()
                                        .trim_end_matches(':')
                                        .to_string();
                                }
                                "alias" | "identifier" => {
                                    value_name = get_text(source, Some(child));
                                }
                                _ => {}
                            }
                        }
                    }
                    if kw_text == "as" && !value_name.is_empty() {
                        return Some(value_name);
                    }
                }
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
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::indexer::context::ExtractionContext;
    use crate::traits::{EdgeKind, NodeKind};
    use tree_sitter::Parser;

    // Debug test for find_alias_as_name - temp removed

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
    // Import creates REFERENCES edge (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_import_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  import Logger\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Logger::")),
            "Expected REFERENCES edge with target_text 'Logger::', got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    // ------------------------------------------------------------------
    // Alias extraction (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_alias_simple_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  alias MyApp.Services.User\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Services.User::")),
            "Expected REFERENCES edge for alias, got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_alias_as_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  alias MyApp.Services.User, as: U\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Services.User::")),
            "Expected REFERENCES edge with original module name, got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_alias_as_qualifies_call() {
        let ctx = extract(
            "defmodule MyApp do\n  alias MyApp.Services.Helper, as: H\n  def work do\n    H.do_thing()\n  end\nend\n",
            "src/test.ex",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(
            calls.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Services.Helper.do_thing")),
            "Expected qualified call 'MyApp.Services.Helper.do_thing', got: {:?}",
            calls.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_alias_without_as_qualifies_call() {
        let ctx = extract(
            "defmodule MyApp do\n  alias MyApp.Services.Helper\n  def work do\n    Helper.do_thing()\n  end\nend\n",
            "src/test.ex",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(
            calls.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Services.Helper.do_thing")),
            "Expected qualified call via default alias, got: {:?}",
            calls.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    // ------------------------------------------------------------------
    // Use extraction (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_use_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  use Phoenix.LiveView\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Phoenix.LiveView::")),
            "Expected REFERENCES edge for use, got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_use_with_options_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  use GenServer, opts\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("GenServer::")),
            "Expected REFERENCES edge for use with opts, got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    // ------------------------------------------------------------------
    // Require extraction (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_require_creates_references_edge() {
        let ctx = extract(
            "defmodule MyApp do\n  require Logger\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Logger::")),
            "Expected REFERENCES edge for require, got: {:?}",
            refs.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
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

    // ------------------------------------------------------------------
    // Multiple references (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_references_in_module() {
        let ctx = extract(
            "defmodule MyApp do\n  alias MyApp.Services.User\n  alias MyApp.Repo\n  use Phoenix.Controller\n  import Logger\n  require Ecto.Query\nend\n",
            "src/test.ex",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Services.User::")),
            "Missing User alias reference"
        );
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("MyApp.Repo::")),
            "Missing Repo alias reference"
        );
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Phoenix.Controller::")),
            "Missing Phoenix.Controller use reference"
        );
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Logger::")),
            "Missing Logger import reference"
        );
        assert!(
            refs.iter().any(|e| e.target_text.as_deref() == Some("Ecto.Query::")),
            "Missing Ecto.Query require reference"
        );
    }
}
