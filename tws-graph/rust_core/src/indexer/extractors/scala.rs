//! Scala language extractor.
//!
//! Extracts symbols and relationships from Scala source files (`.scala`, `.sc`)
//! using the tree-sitter-scala grammar.
//!
//! # Node kinds produced
//! - `class`: class definition
//! - `object`: object definition
//! - `trait`: trait definition
//! - `function`: function/method definition
//! - `variable`: val/var definition
//! - `package`: package clause
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: call expressions
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import declarations
//! - `extends`: class/trait inheritance (extends clause)
//! - `implements`: trait mixing (with clause)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// ScalaExtractor
// ---------------------------------------------------------------------------

pub struct ScalaExtractor;

impl Extractor for ScalaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["scala", "sc"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["scala"]
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
        "package_clause" => extract_package(source, node, ctx, parent_id)?,
        "class_definition" => extract_class(source, node, ctx, parent_id)?,
        "object_definition" => extract_object(source, node, ctx, parent_id)?,
        "trait_definition" => extract_trait(source, node, ctx, parent_id)?,
        "function_definition" => extract_function(source, node, ctx, parent_id)?,
        "val_definition" | "var_definition" => extract_variable(source, node, ctx, parent_id, node.kind())?,
        "import_declaration" => extract_import(source, node, ctx, parent_id)?,
        "call_expression" => extract_call(source, node, ctx, parent_id)?,
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
// Package extraction
// ---------------------------------------------------------------------------

fn extract_package(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Find the package_identifier child
    if let Some(pkg_id) = find_child_by_kind(node, "package_identifier") {
        let pkg_name = get_text(source, Some(pkg_id));
        let pkg_id_node = ctx.add_node(NodeKind::Package, &pkg_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &pkg_id_node, EdgeKind::Contains, line, None);
    }

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
    let name = find_child_text(source, node, "identifier");
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;
    let mut extra = HashMap::new();

    // Check for abstract class
    if let Some(mods) = find_child_by_kind(node, "access_modifier") {
        extra.insert("visibility".to_string(), get_text(source, Some(mods)));
    }
    if find_child_by_kind(node, "abstract_modifier").is_some() {
        extra.insert("is_abstract".to_string(), "true".to_string());
    }

    let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    // Extends clause (extends)
    if let Some(extends) = find_child_by_kind(node, "extends_clause") {
        for i in 0..extends.named_child_count() {
            if let Some(child) = extends.named_child(i) {
                if child.kind() == "type_identifier" {
                    let base_name = get_text(source, Some(child));
                    let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&base_name));
                }
            }
        }
    }

    // With clause (implements/mixins) — inside extends_clause
    // Actually in tree-sitter-scala, the with clause types are also within extends_clause
    // The first type_identifier is the parent class (extends), subsequent ones are traits (implements)
    if let Some(extends) = find_child_by_kind(node, "extends_clause") {
        let mut first = true;
        for i in 0..extends.named_child_count() {
            if let Some(child) = extends.named_child(i) {
                if child.kind() == "type_identifier" {
                    if first {
                        first = false;
                        continue; // Already handled as extends above
                    }
                    let trait_name = get_text(source, Some(child));
                    let target_qn = build_qualified_target(&ctx.file_path, &trait_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&trait_name));
                }
            }
        }
    }

    ctx.push_scope_with_kind(&name, "class");
    ctx.push_scope_node(&class_id);

    // Walk template body for members
    if let Some(body) = find_child_by_kind(node, "template_body") {
        walk_all_children(source, body, ctx, &class_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Object extraction
// ---------------------------------------------------------------------------

fn extract_object(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let name = find_child_text(source, node, "identifier");
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;

    let obj_id = ctx.add_node(NodeKind::Object, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &obj_id, EdgeKind::Contains, line, None);

    // Extends clause for object
    if let Some(extends) = find_child_by_kind(node, "extends_clause") {
        for i in 0..extends.named_child_count() {
            if let Some(child) = extends.named_child(i) {
                if child.kind() == "type_identifier" {
                    let base_name = get_text(source, Some(child));
                    let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&obj_id, &target, EdgeKind::Extends, line, Some(&base_name));
                }
            }
        }
    }

    ctx.push_scope_with_kind(&name, "object");
    ctx.push_scope_node(&obj_id);

    if let Some(body) = find_child_by_kind(node, "template_body") {
        walk_all_children(source, body, ctx, &obj_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Trait extraction
// ---------------------------------------------------------------------------

fn extract_trait(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let name = find_child_text(source, node, "identifier");
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;

    let trait_id = ctx.add_node(NodeKind::Trait, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &trait_id, EdgeKind::Contains, line, None);

    // Extends clause for trait
    if let Some(extends) = find_child_by_kind(node, "extends_clause") {
        for i in 0..extends.named_child_count() {
            if let Some(child) = extends.named_child(i) {
                if child.kind() == "type_identifier" {
                    let base_name = get_text(source, Some(child));
                    let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&trait_id, &target, EdgeKind::Extends, line, Some(&base_name));
                }
            }
        }
    }

    ctx.push_scope_with_kind(&name, "trait");
    ctx.push_scope_node(&trait_id);

    if let Some(body) = find_child_by_kind(node, "template_body") {
        walk_all_children(source, body, ctx, &trait_id)?;
    }

    ctx.pop_scope();
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
    let name = find_child_text(source, node, "identifier");
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;

    let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "function");
    ctx.push_scope_node(&fn_id);

    // Walk the block/body for nested constructs and calls
    if let Some(body) = find_child_by_kind(node, "block") {
        walk_all_children(source, body, ctx, &fn_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Variable extraction (val / var)
// ---------------------------------------------------------------------------

fn extract_variable(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    kind: &str,
) -> anyhow::Result<()> {
    let name = find_child_text(source, node, "identifier");
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;
    let mut extra = HashMap::new();
    extra.insert("val_or_var".to_string(), kind.to_string());

    let var_id = ctx.add_node(NodeKind::Variable, &name, &node, extra);
    ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);

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

    // Collect all identifier tokens in the import path
    let mut parts: Vec<String> = Vec::new();
    collect_import_identifiers(source, node, &mut parts);

    if !parts.is_empty() {
        let import_path = parts.join(".");
        let target_qn = build_qualified_target(&ctx.file_path, &import_path);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
    }

    Ok(())
}

fn collect_import_identifiers(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "type_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                _ => {
                    collect_import_identifiers(source, child, parts);
                }
            }
        }
    }
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

    // The call target is usually the first identifier child
    if let Some(callee) = find_child_by_kind(node, "identifier") {
        let callee_name = get_text(source, Some(callee));
        if !callee_name.is_empty() {
            let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
        }
    }

    Ok(())
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

fn find_child_text(source: &[u8], node: Node, kind: &str) -> String {
    get_text(source, find_child_by_kind(node, kind))
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

    /// Parse Scala source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        let language: tree_sitter::Language = tree_sitter_scala::LANGUAGE.into();
        parser.set_language(&language).expect("set scala language");
        let tree = parser.parse(source, None).expect("parse scala source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "scala".to_string());
        ScalaExtractor
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
        let ctx = extract("", "src/empty.scala");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_class() {
        let ctx = extract("class MyClass\n", "src/test.scala");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_class_with_body() {
        let ctx = extract(
            "class MyClass {\n  def hello(): Unit = {}\n}\n",
            "src/test.scala",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "hello");
    }

    // ------------------------------------------------------------------
    // Extends / implements
    // ------------------------------------------------------------------

    #[test]
    fn test_class_extends() {
        let ctx = extract(
            "class MyClass extends BaseClass\n",
            "src/test.scala",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(!extends.is_empty(), "Expected EXTENDS edge");
        assert!(extends.iter().any(|e| e.target_text.as_deref() == Some("BaseClass")));
    }

    #[test]
    fn test_class_with_trait() {
        let ctx = extract(
            "class MyClass extends BaseClass with MyTrait\n",
            "src/test.scala",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert!(!extends.is_empty(), "Expected EXTENDS edge");
        assert!(!implements.is_empty(), "Expected IMPLEMENTS edge");
        assert!(implements.iter().any(|e| e.target_text.as_deref() == Some("MyTrait")));
    }

    // ------------------------------------------------------------------
    // Object extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_object() {
        let ctx = extract("object MyObject\n", "src/test.scala");
        let objects = find_nodes(&ctx, NodeKind::Object);
        assert_eq!(objects.len(), 1);
        assert_eq!(objects[0].name, "MyObject");
    }

    // ------------------------------------------------------------------
    // Trait extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_trait() {
        let ctx = extract("trait MyTrait\n", "src/test.scala");
        let traits = find_nodes(&ctx, NodeKind::Trait);
        assert_eq!(traits.len(), 1);
        assert_eq!(traits[0].name, "MyTrait");
    }

    // ------------------------------------------------------------------
    // Package extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_package_clause() {
        let ctx = extract(
            "package com.example.app\nclass Foo\n",
            "src/test.scala",
        );
        let packages = find_nodes(&ctx, NodeKind::Package);
        assert!(!packages.is_empty(), "Expected package node");
        assert_eq!(packages[0].name, "com.example.app");
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_val_definition() {
        let ctx = extract("class Foo {\n  val x: Int = 1\n}\n", "src/test.scala");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "x");
    }

    #[test]
    fn test_var_definition() {
        let ctx = extract("class Foo {\n  var y: String = \"hello\"\n}\n", "src/test.scala");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "y");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_import_declaration() {
        let ctx = extract(
            "import scala.collection.mutable.ListBuffer\nclass Foo\n",
            "src/test.scala",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_call() {
        let ctx = extract(
            "class Foo {\n  def bar(): Unit = {\n    baz()\n  }\n}\n",
            "src/test.scala",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edge");
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("baz")));
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_file_contains_class() {
        let ctx = extract("class MyClass\n", "src/test.scala");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    #[test]
    fn test_nested_structures() {
        let ctx = extract(
            "class Outer {\n  class Inner {\n    def method(): Unit = {}\n  }\n}\n",
            "src/test.scala",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 2);
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
    }

    #[test]
    fn test_object_extends() {
        let ctx = extract(
            "object MyApp extends App {\n  println(\"hello\")\n}\n",
            "src/test.scala",
        );
        let objects = find_nodes(&ctx, NodeKind::Object);
        assert_eq!(objects.len(), 1);
        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(!extends.is_empty(), "Expected EXTENDS edge for App");
    }
}
