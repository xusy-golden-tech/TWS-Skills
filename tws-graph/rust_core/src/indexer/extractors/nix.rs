//! Nix language extractor.
//!
//! Extracts symbols and relationships from Nix source files (`.nix`)
//! using the tree-sitter-nix grammar.
//!
//! # Node kinds produced
//! - `function`: function expression in a binding
//! - `variable`: let binding / variable
//! - `class`: attrset expressions
//! - `property`: attrset key-value pairs
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: apply expressions (function calls)
//! - `contains`: containment (file -> attrset -> property)
//! - `imports`: import expressions, inherit, with

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// NixExtractor
// ---------------------------------------------------------------------------

pub struct NixExtractor;

impl Extractor for NixExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["nix"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["nix"]
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
        "let_expression" => extract_let(source, node, ctx, parent_id)?,
        "attrset_expression" | "rec_attrset_expression" | "let_attrset_expression" => {
            extract_attrset(source, node, ctx, parent_id, None)?
        }
        "apply_expression" => extract_apply(source, node, ctx, parent_id)?,
        "with_expression" => extract_with(source, node, ctx, parent_id)?,
        "inherit_from" => extract_inherit_from(source, node, ctx, parent_id)?,
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
// Let expression extraction
// ---------------------------------------------------------------------------

fn extract_let(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "binding_set" => {
                    for j in 0..child.named_child_count() {
                        if let Some(bc) = child.named_child(j) {
                            match bc.kind() {
                                "binding" => extract_binding(source, bc, ctx, parent_id)?,
                                _ => walk_node(source, bc, ctx, parent_id)?,
                            }
                        }
                    }
                }
                _ => walk_node(source, child, ctx, parent_id)?,
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Binding extraction (variable or function)
// ---------------------------------------------------------------------------

fn extract_binding(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // A binding has: key (identifier) = value (expression)
    // Find the key (first identifier child)
    let name = get_binding_name(source, node);
    if name.is_empty() {
        return Ok(());
    }

    // Check if the value is a function_expression
    let mut is_function = false;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "function_expression" {
                is_function = true;
                let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
                ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

                ctx.push_scope_with_kind(&name, "function");
                ctx.push_scope_node(&fn_id);
                walk_all_children(source, child, ctx, &fn_id)?;
                ctx.pop_scope();
                return Ok(());
            }
            // Check for attrset as value
            if child.kind() == "attrset_expression"
                || child.kind() == "rec_attrset_expression"
                || child.kind() == "let_attrset_expression"
            {
                extract_attrset(source, child, ctx, parent_id, Some(&name))?;
                return Ok(());
            }
        }
    }

    // Simple variable binding
    let var_id = ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);

    // Walk value for calls and other expressions
    ctx.push_scope_with_kind(&name, "variable");
    ctx.push_scope_node(&var_id);
    walk_all_children(source, node, ctx, &var_id)?;
    ctx.pop_scope();

    Ok(())
}

fn get_binding_name(source: &[u8], node: Node) -> String {
    // A binding has: attrpath (contains identifier) = value
    // Look for attrpath first, then identifier inside it
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "attrpath" {
                for j in 0..child.named_child_count() {
                    if let Some(gc) = child.named_child(j) {
                        if gc.kind() == "identifier" {
                            let name = get_text(source, Some(gc));
                            if !name.is_empty() && !is_nix_keyword(&name) {
                                return name;
                            }
                        }
                    }
                }
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Attrset extraction (maps to Class) (class / property)
// ---------------------------------------------------------------------------

fn extract_attrset(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    attr_name: Option<&str>,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // For unnamed attrsets, use a placeholder name or the binding name
    let name = match attr_name {
        Some(n) => n.to_string(),
        None => "attrset".to_string(),
    };

    let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "attrset");
    ctx.push_scope_node(&class_id);

    // Walk children: bindings become properties, inherit becomes imports
    // Node: binding_set wraps bindings in attrset expressions
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "binding_set" => {
                    for j in 0..child.named_child_count() {
                        if let Some(bc) = child.named_child(j) {
                            match bc.kind() {
                                "binding" => {
                                    extract_attr_property(source, bc, ctx, &class_id)?
                                }
                                "inherit_from" => {
                                    extract_inherit_from(source, bc, ctx, &class_id)?
                                }
                                _ => walk_node(source, bc, ctx, &class_id)?,
                            }
                        }
                    }
                }
                _ => walk_node(source, child, ctx, &class_id)?,
            }
        }
    }

    ctx.pop_scope();
    Ok(())
}

fn extract_attr_property(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let name = get_binding_name(source, node);
    if name.is_empty() {
        return Ok(());
    }

    let prop_id = ctx.add_node(NodeKind::Property, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &prop_id, EdgeKind::Contains, line, None);

    // Walk value for nested constructs
    ctx.push_scope_with_kind(&name, "property");
    ctx.push_scope_node(&prop_id);
    walk_all_children(source, node, ctx, &prop_id)?;
    ctx.pop_scope();

    Ok(())
}

// ---------------------------------------------------------------------------
// Inherit from extraction (imports)
// ---------------------------------------------------------------------------

fn extract_inherit_from(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // inherit_from has: select_expression (the source scope) + inherited_attrs (identifiers)
    // We want the scope path as an imports edge
    let scope = get_scope_path(source, node);
    if !scope.is_empty() {
        let target_qn = build_qualified_target(&ctx.file_path, &scope);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&scope));
    }

    Ok(())
}

fn get_scope_path(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "select_expression" | "variable_expression" => {
                    return get_full_path(source, child);
                }
                _ => {}
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Apply expression extraction (calls + import detection)
// ---------------------------------------------------------------------------

fn extract_apply(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Check if this is an import call
    let callee_name = get_first_callee_name(source, node);
    if callee_name == "import" {
        // Find import path (spath_expression or path_expression)
        let import_path = find_arg_path(source, node);
        if !import_path.is_empty() {
            let target_qn = build_qualified_target(&ctx.file_path, &import_path);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
        }
    } else if !callee_name.is_empty() && !is_nix_builtin(&callee_name) {
        let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
    }

    // Walk children for nested calls
    walk_all_children(source, node, ctx, parent_id)?;
    Ok(())
}

fn get_first_callee_name(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "variable_expression" => {
                    for j in 0..child.named_child_count() {
                        if let Some(gc) = child.named_child(j) {
                            if gc.kind() == "identifier" {
                                return get_text(source, Some(gc));
                            }
                        }
                    }
                }
                "select_expression" => {
                    return get_full_path(source, child);
                }
                "apply_expression" => {
                    return get_first_callee_name(source, child);
                }
                _ => {}
            }
        }
    }
    String::new()
}

fn find_arg_path(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "spath_expression" | "path_expression" | "hpath_expression"
                | "uri_expression" => {
                    return get_text(source, Some(child))
                        .trim_matches('<')
                        .trim_matches('>')
                        .to_string();
                }
                _ => {
                    let path = find_arg_path(source, child);
                    if !path.is_empty() {
                        return path;
                    }
                }
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// With expression extraction (imports via scope)
// ---------------------------------------------------------------------------

fn extract_with(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // with expr; body
    // The expression introduces a scope, treated as an import of that scope
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "variable_expression" || child.kind() == "select_expression" {
                let path = get_full_path(source, child);
                if !path.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &path);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&path));
                    // Only first variable expression is the scope
                    break;
                }
            }
        }
    }

    // Walk body
    walk_all_children(source, node, ctx, parent_id)?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Builtin filters
// ---------------------------------------------------------------------------

fn is_nix_builtin(name: &str) -> bool {
    matches!(
        name,
        "builtins" | "true" | "false" | "null" | "import"
    )
}

fn is_nix_keyword(name: &str) -> bool {
    matches!(
        name,
        "let" | "in" | "with" | "rec" | "inherit" | "if" | "then" | "else" | "or" | "assert"
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

fn get_full_path(source: &[u8], node: Node) -> String {
    // Recursively collect path from variable_expression, select_expression
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" {
                return get_text(source, Some(child));
            }
        }
    }
    // For select expressions, find all identifiers
    let mut parts: Vec<String> = Vec::new();
    collect_all_identifiers(source, node, &mut parts);
    parts.join(".")
}

fn collect_all_identifiers(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" {
                parts.push(get_text(source, Some(child)));
            } else {
                collect_all_identifiers(source, child, parts);
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
        let language: tree_sitter::Language = tree_sitter_nix::LANGUAGE.into();
        parser.set_language(&language).expect("set nix language");
        let tree = parser.parse(source, None).expect("parse nix source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "nix".to_string());
        NixExtractor
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
        let ctx = extract("# Just a comment\n", "src/empty.nix");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // 2. Let bindings (variables)
    // ------------------------------------------------------------------

    #[test]
    fn test_let_bindings() {
        let ctx = extract(
            "let\n  name = \"test\";\n  version = \"1.0\";\n  count = 42;\nin\nname\n",
            "test/default.nix",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 3, "Expected >=3 variables, got {}", vars.len());
        let names: Vec<_> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"name"));
        assert!(names.contains(&"version"));
        assert!(names.contains(&"count"));
    }

    // ------------------------------------------------------------------
    // 3. Function in binding
    // ------------------------------------------------------------------

    #[test]
    fn test_function_binding() {
        let ctx = extract(
            "let\n  greet = name: \"Hello, ${name}\";\n  add = a: b: a + b;\nin\ngreet\n",
            "test/default.nix",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(funcs.len() >= 2, "Expected >=2 functions, got {}", funcs.len());
        let names: Vec<_> = funcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"greet"));
        assert!(names.contains(&"add"));
    }

    // ------------------------------------------------------------------
    // 4. Attrset (class)
    // ------------------------------------------------------------------

    #[test]
    fn test_attrset() {
        let ctx = extract(
            "{\n  name = \"my-package\";\n  version = \"2.0\";\n  src = ./src;\n}\n",
            "test/default.nix",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(!classes.is_empty(), "Expected attrset class");
        let props = find_nodes(&ctx, NodeKind::Property);
        assert!(props.len() >= 3, "Expected >=3 properties, got {}", props.len());
    }

    // ------------------------------------------------------------------
    // 5. Rec attrsets
    // ------------------------------------------------------------------

    #[test]
    fn test_rec_attrset() {
        let ctx = extract(
            "rec {\n  a = 1;\n  b = a + 1;\n}\n",
            "test/default.nix",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(!classes.is_empty(), "Expected rec attrset class");
    }

    // ------------------------------------------------------------------
    // 6. Inherit (imports)
    // ------------------------------------------------------------------

    #[test]
    fn test_inherit() {
        let ctx = extract(
            "{\n  inherit (pkgs.stdenv) mkDerivation;\n}\n",
            "test/default.nix",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for inherit");
    }

    // ------------------------------------------------------------------
    // 7. Import expression
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract(
            "let\n  pkgs = import <nixpkgs> {};\n  lib = import ./lib.nix;\nin\npkgs\n",
            "test/default.nix",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edges");
    }

    // ------------------------------------------------------------------
    // 8. With expression (imports)
    // ------------------------------------------------------------------

    #[test]
    fn test_with_expression() {
        let ctx = extract(
            "with pkgs; [ hello gcc ]\n",
            "test/default.nix",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for with");
    }

    // ------------------------------------------------------------------
    // 9. Apply expression (calls)
    // ------------------------------------------------------------------

    #[test]
    fn test_apply_expression() {
        let ctx = extract(
            "let\n  f = x: x + 1;\n  result = f 5;\n  multi = f (f 3);\nin\nresult\n",
            "test/default.nix",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edges");
    }

    // ------------------------------------------------------------------
    // 10. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract(
            "{\n  name = \"test\";\n}\n",
            "test/default.nix",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 11. Nested attrset
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_attrset() {
        let ctx = extract(
            "{\n  inner = {\n    x = 1;\n    y = 2;\n  };\n}\n",
            "test/default.nix",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(classes.len() >= 2, "Expected >=2 attrsets, got {}", classes.len());
    }

    // ------------------------------------------------------------------
    // 12. Multiple let bindings
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_let() {
        let ctx = extract(
            "let\n  outer = \"outer\";\n  inner = let\n    x = 10;\n    y = 20;\n  in {\n    sum = x + y;\n  };\nin\ninner\n",
            "test/default.nix",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        let names: Vec<_> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"outer"));
        assert!(names.contains(&"x"));
        assert!(names.contains(&"y"));
    }

    // ------------------------------------------------------------------
    // 13. Named attrset via binding
    // ------------------------------------------------------------------

    #[test]
    fn test_named_attrset() {
        let ctx = extract(
            "let\n  config = {\n    name = \"app\";\n  };\nin\nconfig\n",
            "test/default.nix",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        let names: Vec<_> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"config"), "Expected named attrset 'config', got {:?}", names);
    }

    // ------------------------------------------------------------------
    // 14. Builtin filter (import should not create extra call)
    // ------------------------------------------------------------------

    #[test]
    fn test_import_not_called() {
        let ctx = extract(
            "import ./lib.nix\n",
            "test/default.nix",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // import is a builtin, should not create a call edge
        assert!(calls.iter().all(|e| e.target_text.as_deref() != Some("import")));
    }
}
