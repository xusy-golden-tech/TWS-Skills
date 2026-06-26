//! Groovy language extractor.
//!
//! Extracts symbols and relationships from Groovy source files (`.groovy`)
//! using the tree-sitter-groovy grammar.
//!
//! # Node kinds produced
//! - `class`: class declaration
//! - `interface`: interface declaration
//! - `enum`: enum declaration
//! - `method`: method/constructor declaration
//! - `function`: top-level function
//! - `variable`: field/property declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: method invocations
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import declarations
//! - `extends`: class inheritance (extends clause)
//! - `implements`: interface implementation (implements clause)
//! - `decorates`: annotations

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// GroovyExtractor
// ---------------------------------------------------------------------------

pub struct GroovyExtractor;

impl Extractor for GroovyExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["groovy"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["groovy"]
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
        "class_declaration" => extract_class(source, node, ctx, parent_id)?,
        "interface_declaration" => extract_interface(source, node, ctx, parent_id)?,
        "enum_declaration" => extract_enum(source, node, ctx, parent_id)?,
        "method_declaration" | "constructor_declaration" => {
            extract_method(source, node, ctx, parent_id)?
        }
        "field_declaration" => extract_field(source, node, ctx, parent_id)?,
        "import_declaration" => extract_import(source, node, ctx, parent_id)?,
        "package_declaration" => extract_package(source, node, ctx, parent_id)?,
        "method_invocation" => extract_call(source, node, ctx, parent_id)?,
        "annotation" | "marker_annotation" => extract_annotation(source, node, ctx, parent_id)?,
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

    let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    // Handle superclass (extends)
    if let Some(superclass) = find_child_by_kind(node, "superclass") {
        for i in 0..superclass.named_child_count() {
            if let Some(child) = superclass.named_child(i) {
                if child.kind() == "type_identifier" || child.kind() == "identifier" {
                    let base_name = get_text(source, Some(child));
                    let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&base_name));
                }
            }
        }
    }

    // Handle super_interfaces (implements)
    if let Some(supers) = find_child_by_kind(node, "super_interfaces") {
        let mut type_names: Vec<String> = Vec::new();
        collect_type_identifiers(source, supers, &mut type_names);
        for iface_name in type_names {
            let target_qn = build_qualified_target(&ctx.file_path, &iface_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&iface_name));
        }
    }

    ctx.push_scope_with_kind(&name, "class");
    ctx.push_scope_node(&class_id);

    // Walk all children for annotations, members, and calls
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "superclass" | "super_interfaces" => {} // Already handled above
                _ => walk_node(source, child, ctx, &class_id)?,
            }
        }
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Interface extraction
// ---------------------------------------------------------------------------

fn extract_interface(
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

    let iface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &iface_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "interface");
    ctx.push_scope_node(&iface_id);

    if let Some(body) = find_child_by_kind(node, "interface_body") {
        walk_all_children(source, body, ctx, &iface_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Enum extraction
// ---------------------------------------------------------------------------

fn extract_enum(
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

    let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

    // Extract enum constants
    if let Some(body) = find_child_by_kind(node, "enum_body") {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                if child.kind() == "enum_constant" {
                    let const_name = find_child_text(source, child, "identifier");
                    if !const_name.is_empty() {
                        let const_line = child.start_position().row as u32 + 1;
                        let const_id =
                            ctx.add_node(NodeKind::EnumMember, &const_name, &child, HashMap::new());
                        ctx.add_edge(&enum_id, &const_id, EdgeKind::Contains, const_line, None);
                    }
                }
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Method / constructor extraction
// ---------------------------------------------------------------------------

fn extract_method(
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

    let kind = if node.kind() == "constructor_declaration" {
        NodeKind::Method
    } else {
        NodeKind::Method
    };

    let method_id = ctx.add_node(kind, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "method");
    ctx.push_scope_node(&method_id);

    // Walk body for calls and nested constructs
    if let Some(body) = find_child_by_kind(node, "block") {
        walk_all_children(source, body, ctx, &method_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Field extraction
// ---------------------------------------------------------------------------

fn extract_field(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Find the variable declarator(s) in the field
    let declarators = find_all_children_by_kind(node, "variable_declarator");
    if declarators.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;

    for decl in declarators {
        let name = find_child_text(source, decl, "identifier");
        if !name.is_empty() {
            let var_id = ctx.add_node(NodeKind::Variable, &name, &decl, HashMap::new());
            ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
        }
    }

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

    // Collect all identifiers in the import path
    let mut parts: Vec<String> = Vec::new();
    collect_identifiers(source, node, &mut parts);

    if !parts.is_empty() {
        // Skip the "import" keyword itself
        let import_path = if parts.len() >= 2 {
            parts[1..].join(".")
        } else {
            parts.join(".")
        };
        let target_qn = build_qualified_target(&ctx.file_path, &import_path);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
    }

    Ok(())
}

fn collect_type_identifiers(source: &[u8], node: Node, names: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "type_identifier" || child.kind() == "identifier" {
                names.push(get_text(source, Some(child)));
            } else {
                collect_type_identifiers(source, child, names);
            }
        }
    }
}

fn collect_identifiers(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" || child.kind() == "type_identifier" {
                parts.push(get_text(source, Some(child)));
            } else if child.kind() == "scoped_type_identifier" || child.kind() == "scoped_identifier" {
                collect_identifiers(source, child, parts);
            } else {
                collect_identifiers(source, child, parts);
            }
        }
    }
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

    let mut parts: Vec<String> = Vec::new();
    collect_identifiers(source, node, &mut parts);

    if !parts.is_empty() {
        let pkg_name = parts.join(".");
        let pkg_id = ctx.add_node(NodeKind::Package, &pkg_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &pkg_id, EdgeKind::Contains, line, None);
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

    // The call target is typically an identifier or field_access
    if let Some(name_node) = find_child_by_kind(node, "identifier") {
        let callee_name = get_text(source, Some(name_node));
        if !callee_name.is_empty() && !is_groovy_builtin(&callee_name) {
            let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
        }
    }

    // Also handle method invocations with field_access (e.g. obj.method())
    if let Some(fa) = find_child_by_kind(node, "field_access") {
        // Try to find the method name (last identifier in field_access)
        let mut parts: Vec<String> = Vec::new();
        collect_identifiers(source, fa, &mut parts);
        if let Some(method_name) = parts.last() {
            if !is_groovy_builtin(method_name) {
                let target_qn = build_qualified_target(&ctx.file_path, method_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(method_name));
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Annotation extraction
// ---------------------------------------------------------------------------

fn extract_annotation(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    if let Some(name_node) = find_child_by_kind(node, "identifier") {
        let anno_name = get_text(source, Some(name_node));
        if !anno_name.is_empty() {
            let target_text = format!("@{}", anno_name);
            let target_qn = build_qualified_target(&ctx.file_path, &target_text);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Decorates, line, Some(&target_text));
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Builtin filter
// ---------------------------------------------------------------------------

fn is_groovy_builtin(name: &str) -> bool {
    matches!(
        name,
        "println"
            | "print"
            | "printf"
            | "sprintf"
            | "assert"
            | "return"
            | "throw"
            | "new"
            | "this"
            | "super"
            | "null"
            | "true"
            | "false"
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

fn find_all_children_by_kind<'a>(node: Node<'a>, kind: &str) -> Vec<Node<'a>> {
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

    /// Parse Groovy source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        let language: tree_sitter::Language = tree_sitter_groovy::LANGUAGE.into();
        parser
            .set_language(&language)
            .expect("set groovy language");
        let tree = parser.parse(source, None).expect("parse groovy source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "groovy".to_string());
        GroovyExtractor
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
        let ctx = extract("", "src/empty.groovy");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // 2. Simple class
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_class() {
        let ctx = extract("class MyClass {\n}\n", "src/test.groovy");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    // ------------------------------------------------------------------
    // 3. Class with method
    // ------------------------------------------------------------------

    #[test]
    fn test_class_with_method() {
        let ctx = extract(
            "class MyClass {\n  def hello() { println 'hi' }\n}\n",
            "src/test.groovy",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(!methods.is_empty(), "Expected at least one method");
        assert_eq!(methods[0].name, "hello");
    }

    // ------------------------------------------------------------------
    // 4. Class extends
    // ------------------------------------------------------------------

    #[test]
    fn test_class_extends() {
        let ctx = extract(
            "class Child extends Parent {\n}\n",
            "src/test.groovy",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(!extends.is_empty(), "Expected EXTENDS edge");
        assert!(extends
            .iter()
            .any(|e| e.target_text.as_deref() == Some("Parent")));
    }

    // ------------------------------------------------------------------
    // 5. Class implements
    // ------------------------------------------------------------------

    #[test]
    fn test_class_implements() {
        let ctx = extract(
            "class Service implements Runnable {\n}\n",
            "src/test.groovy",
        );
        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert!(!implements.is_empty(), "Expected IMPLEMENTS edge");
        assert!(implements
            .iter()
            .any(|e| e.target_text.as_deref() == Some("Runnable")));
    }

    // ------------------------------------------------------------------
    // 6. Interface
    // ------------------------------------------------------------------

    #[test]
    fn test_interface() {
        let ctx = extract(
            "interface Repository {\n  void save()\n}\n",
            "src/test.groovy",
        );
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "Repository");
    }

    // ------------------------------------------------------------------
    // 7. Enum
    // ------------------------------------------------------------------

    #[test]
    fn test_enum() {
        let ctx = extract(
            "enum Color {\n  RED, GREEN, BLUE\n}\n",
            "src/test.groovy",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");

        let members = find_nodes(&ctx, NodeKind::EnumMember);
        assert!(members.len() >= 3);
    }

    // ------------------------------------------------------------------
    // 8. Imports
    // ------------------------------------------------------------------

    #[test]
    fn test_imports() {
        let ctx = extract(
            "import java.util.List\nimport groovy.json.JsonSlurper\nclass Foo {}\n",
            "src/test.groovy",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edges");
    }

    // ------------------------------------------------------------------
    // 9. Method calls
    // ------------------------------------------------------------------

    #[test]
    fn test_method_calls() {
        let ctx = extract(
            "class Foo {\n  def bar() {\n    baz()\n    qux()\n  }\n}\n",
            "src/test.groovy",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(calls.len() >= 2, "Expected >=2 CALLS edges");
    }

    // ------------------------------------------------------------------
    // 10. Annotations
    // ------------------------------------------------------------------

    #[test]
    fn test_annotations() {
        let ctx = extract(
            "@ToString\n@EqualsAndHashCode\nclass Data {\n}\n",
            "src/test.groovy",
        );
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        assert!(
            !decorates.is_empty(),
            "Expected DECORATES edges for annotations"
        );
    }

    // ------------------------------------------------------------------
    // 11. Field variables
    // ------------------------------------------------------------------

    #[test]
    fn test_field_variables() {
        let ctx = extract(
            "class User {\n  String name\n  int age\n}\n",
            "src/test.groovy",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 2, "Expected >=2 field variables");
    }

    // ------------------------------------------------------------------
    // 12. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("class MyClass {\n}\n", "src/test.groovy");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 13. Nested classes
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_classes() {
        let ctx = extract(
            "class Outer {\n  class Inner {\n  }\n}\n",
            "src/test.groovy",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 2);
        let names: Vec<&str> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    // ------------------------------------------------------------------
    // 14. Package declaration
    // ------------------------------------------------------------------

    // ------------------------------------------------------------------
    // Helper to dump node kinds for debugging
    // ------------------------------------------------------------------

    fn dump_kinds(source: &str) -> Vec<String> {
        let mut parser = Parser::new();
        let language: tree_sitter::Language = tree_sitter_groovy::LANGUAGE.into();
        parser.set_language(&language).unwrap();
        let tree = parser.parse(source, None).unwrap();
        let mut kinds = Vec::new();
        fn walk(node: tree_sitter::Node, kinds: &mut Vec<String>) {
            if node.is_named() {
                kinds.push(format!(
                    "{}[{}]",
                    node.kind(),
                    node.utf8_text(&[]).unwrap_or("")
                ));
            }
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk(child, kinds);
                }
            }
        }
        walk(tree.root_node(), &mut kinds);
        kinds
    }

    // ------------------------------------------------------------------
    // 14. Package declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_package() {
        let ctx = extract(
            "package com.example.app\nclass Foo {}\n",
            "src/test.groovy",
        );
        let packages = find_nodes(&ctx, NodeKind::Package);
        assert!(!packages.is_empty(), "Expected package node");
    }
}
