//! Haskell language extractor.
//!
//! Extracts symbols and relationships from Haskell source files (`.hs`)
//! using the tree-sitter-haskell grammar.
//!
//! # Node kinds produced
//! - `module`: module declaration
//! - `function`: function definition / binding
//! - `signature`: type signature
//! - `type_def`: data/newtype declarations
//! - `class`: class declaration
//! - `instance`: instance declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function application (apply nodes)
//! - `contains`: containment (file -> module -> function)
//! - `imports`: import declarations

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// HaskellExtractor
// ---------------------------------------------------------------------------

pub struct HaskellExtractor;

impl Extractor for HaskellExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["hs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["haskell"]
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
                .unwrap_or("Main")
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
        // Module declaration — the grammar uses "module" both for module decl
        // and module references. We detect the declaration by checking if the
        // node's parent is "header".
        "module" => {
            if let Some(parent) = node.parent() {
                if parent.kind() == "header" {
                    return extract_module(source, node, ctx, parent_id);
                }
            }
            walk_all_children(source, node, ctx, parent_id)?;
        }
        // Import
        "import" => {
            return extract_import(source, node, ctx, parent_id);
        }
        // Type signature
        "signature" => {
            return extract_signature(source, node, ctx, parent_id);
        }
        // Function definition (with patterns)
        "function" => {
            return extract_function(source, node, ctx, parent_id);
        }
        // Simple binding (non-pattern)
        "bind" => {
            return extract_bind(source, node, ctx, parent_id);
        }
        // Function application (calls)
        "apply" => {
            return extract_call(source, node, ctx, parent_id);
        }
        // Infix application
        "infix" => {
            return extract_call(source, node, ctx, parent_id);
        }
        // Data type declaration
        "data_type" => {
            return extract_type_def(source, node, ctx, parent_id, "data");
        }
        // Newtype declaration
        "newtype" => {
            return extract_type_def(source, node, ctx, parent_id, "newtype");
        }
        // Class declaration
        "class" => {
            return extract_class(source, node, ctx, parent_id);
        }
        // Instance declaration
        "instance" => {
            return extract_instance(source, node, ctx, parent_id);
        }
        _ => {
            walk_all_children(source, node, ctx, parent_id)?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Module extraction
// ---------------------------------------------------------------------------

fn extract_module(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Module name from module_id child
    let mod_name = if let Some(mid) = find_child_by_kind(node, "module_id") {
        get_text(source, Some(mid))
    } else {
        String::new()
    };

    if mod_name.is_empty() {
        return Ok(());
    }

    let mod_id = ctx.add_node(NodeKind::Module, &mod_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &mod_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&mod_name, "module");
    ctx.push_scope_node(&mod_id);

    walk_all_children(source, node, ctx, &mod_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Import extraction
// ---------------------------------------------------------------------------

fn extract_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Imported module name from nested module node
    if let Some(mod_node) = find_child_by_kind(node, "module") {
        // Module name may be split across multiple module_id children (e.g., "Data" + "List")
        let mod_name = collect_module_id_parts(source, mod_node);
        if mod_name.is_empty() {
            return Ok(());
        }
        let target_qn = build_qualified_target(&ctx.file_path, &mod_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&mod_name));
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Type signature extraction
// ---------------------------------------------------------------------------

fn extract_signature(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Signature name from variable child (or operator from prefix_id)
    let name = if let Some(var_node) = find_child_by_kind(node, "variable") {
        get_text(source, Some(var_node))
    } else if let Some(pref) = find_child_by_kind(node, "prefix_id") {
        get_text(source, Some(pref))
    } else {
        String::new()
    };

    if name.is_empty() {
        return Ok(());
    }

    let sig_id = ctx.add_node(NodeKind::Signature, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &sig_id, EdgeKind::Contains, line, None);

    Ok(())
}

// ---------------------------------------------------------------------------
// Function extraction
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let name_node = find_child_by_kind(node, "variable");
    let name = get_text(source, name_node);

    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "function");
    ctx.push_scope_node(&fn_id);

    walk_all_children(source, node, ctx, &fn_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Bind extraction (simple bindings like `main = ...`)
// ---------------------------------------------------------------------------

fn extract_bind(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let name_node = find_child_by_kind(node, "variable");
    let name = get_text(source, name_node);

    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    // Check if this name was already extracted (e.g., from a function with patterns)
    if ctx.result.nodes.iter().any(|n| n.kind == "function" && n.name == name) {
        walk_all_children(source, node, ctx, parent_id)?;
        return Ok(());
    }

    let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "function");
    ctx.push_scope_node(&fn_id);

    walk_all_children(source, node, ctx, &fn_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Type definition extraction (data/newtype)
// ---------------------------------------------------------------------------

fn extract_type_def(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    def_kind: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let type_name = find_first_child_text_by_kind(source, node, "name")
        .unwrap_or_default();

    if type_name.is_empty() {
        return Ok(());
    }

    let mut extra = HashMap::new();
    extra.insert("type_kind".to_string(), def_kind.to_string());

    let type_id = ctx.add_node(NodeKind::TypeDef, &type_name, &node, extra);
    ctx.add_edge(parent_id, &type_id, EdgeKind::Contains, line, None);

    Ok(())
}

// ---------------------------------------------------------------------------
// Class extraction
// ---------------------------------------------------------------------------

fn extract_class(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let class_name = if let Some(name_node) = find_child_by_kind(node, "name") {
        get_text(source, Some(name_node))
    } else {
        String::new()
    };

    if class_name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let class_id = ctx.add_node(NodeKind::Class, &class_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&class_name, "class");
    ctx.push_scope_node(&class_id);

    walk_all_children(source, node, ctx, &class_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Instance extraction
// ---------------------------------------------------------------------------

fn extract_instance(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Instance name: class name + type name
    let class_name = find_first_child_text_by_kind(source, node, "name")
        .unwrap_or_default();

    // Get the type from type_patterns child
    let type_name = if let Some(tp) = find_child_by_kind(node, "type_patterns") {
        find_first_child_text_by_kind(source, tp, "name").unwrap_or_default()
    } else {
        String::new()
    };

    let instance_name = if type_name.is_empty() {
        class_name.clone()
    } else {
        format!("{} {}", class_name, type_name)
    };

    if instance_name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let inst_id = ctx.add_node(NodeKind::Instance, &instance_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &inst_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&instance_name, "instance");
    ctx.push_scope_node(&inst_id);

    walk_all_children(source, node, ctx, &inst_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Call extraction (apply and infix nodes)
// ---------------------------------------------------------------------------

fn extract_call(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Get the callee name from the first named child
    if node.named_child_count() > 0 {
        if let Some(first_child) = node.named_child(0) {
            let callee = match first_child.kind() {
                "variable" => Some(get_text(source, Some(first_child))),
                "name" => Some(get_text(source, Some(first_child))),
                "apply" | "infix_id" => {
                    let v = find_deep_variable_text(source, first_child);
                    if v.is_empty() { None } else { Some(v) }
                }
                _ => None,
            };

            if let Some(callee_name) = callee {
                if !callee_name.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
                }
            }
        }
    }

    // Recurse into children for nested calls
    walk_all_children(source, node, ctx, parent_id)?;
    Ok(())
}

fn find_deep_variable_text(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "variable" {
                return get_text(source, Some(child));
            }
            let result = find_deep_variable_text(source, child);
            if !result.is_empty() {
                return result;
            }
        }
    }
    String::new()
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

/// Concatenate all module_id children (e.g., "Data" + "List" => "Data.List").
fn collect_module_id_parts(source: &[u8], node: Node) -> String {
    let mut parts = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "module_id" {
                parts.push(get_text(source, Some(child)));
            }
        }
    }
    parts.join(".")
}

fn find_first_child_text_by_kind(source: &[u8], node: Node, kind: &str) -> Option<String> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(get_text(source, Some(child)));
            }
        }
    }
    None
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
        let language: tree_sitter::Language = tree_sitter_haskell::LANGUAGE.into();
        parser.set_language(&language).expect("set haskell language");
        let tree = parser.parse(source, None).expect("parse haskell source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "haskell".to_string());
        HaskellExtractor
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
        let ctx = extract("", "src/empty.hs");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Module extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_module_declaration() {
        let ctx = extract(
            "module Main where\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert!(!modules.is_empty(), "Expected module node");
        assert_eq!(modules[0].name, "Main");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract(
            "module Main where\nimport Data.List\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        assert!(imports.iter().any(|e| e.target_text.as_deref() == Some("Data.List")));
    }

    #[test]
    fn test_qualified_import() {
        let ctx = extract(
            "module Main where\nimport qualified Data.Map as Map\nmain = return ()\n",
            "src/test.hs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for qualified import");
        assert!(
            imports.iter().any(|e| e.target_text.as_deref() == Some("Data.Map")),
            "Expected Data.Map in imports"
        );
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_with_patterns() {
        let ctx = extract(
            "fact :: Int -> Int\nfact 0 = 1\nfact n = n * fact (n - 1)\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(!functions.is_empty(), "Expected function node");
        assert!(functions.iter().any(|f| f.name == "fact"));
    }

    #[test]
    fn test_simple_function() {
        let ctx = extract(
            "module Main where\ngreet name = \"Hello, \" ++ name\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(!functions.is_empty(), "Expected function nodes");
    }

    // ------------------------------------------------------------------
    // Signature extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_signature() {
        let ctx = extract(
            "module Main where\ndouble :: Int -> Int\ndouble x = x * 2\n",
            "src/test.hs",
        );
        let sigs = find_nodes(&ctx, NodeKind::Signature);
        assert!(!sigs.is_empty(), "Expected signature node");
        assert_eq!(sigs[0].name, "double");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_call() {
        let ctx = extract(
            "module Main where\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edge");
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("putStrLn")));
    }

    // ------------------------------------------------------------------
    // Type definition extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_data_declaration() {
        let ctx = extract(
            "module Main where\ndata Tree a = Leaf a | Node (Tree a) (Tree a)\n",
            "src/test.hs",
        );
        let types = find_nodes(&ctx, NodeKind::TypeDef);
        assert!(!types.is_empty(), "Expected TypeDef node for data declaration");
        assert_eq!(types[0].name, "Tree");
    }

    #[test]
    fn test_newtype_declaration() {
        let ctx = extract(
            "module Main where\nnewtype Age = Age Int\n",
            "src/test.hs",
        );
        let types = find_nodes(&ctx, NodeKind::TypeDef);
        assert!(!types.is_empty(), "Expected TypeDef node for newtype");
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_class_declaration() {
        let ctx = extract(
            "module Main where\nclass Eq a where\n  (==) :: a -> a -> Bool\n",
            "src/test.hs",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(!classes.is_empty(), "Expected class node");
    }

    // ------------------------------------------------------------------
    // Instance extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_instance_declaration() {
        let ctx = extract(
            "module Main where\ninstance Eq Int where\n  x == y = True\n",
            "src/test.hs",
        );
        let instances = find_nodes(&ctx, NodeKind::Instance);
        assert!(!instances.is_empty(), "Expected instance node");
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_file_contains_module() {
        let ctx = extract(
            "module Main where\nmain = return ()\n",
            "src/test.hs",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Nested / multiple
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_functions() {
        let ctx = extract(
            "module Main where\na = 1\nb = 2\nc = a + b\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(functions.len() >= 2, "Expected at least 2 function nodes");
    }

    #[test]
    fn test_no_explicit_module() {
        let ctx = extract(
            "main = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        // Should not crash on files without explicit module declaration
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }
}
