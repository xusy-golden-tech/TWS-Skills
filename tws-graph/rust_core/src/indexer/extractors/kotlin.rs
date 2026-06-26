//! Kotlin language extractor.
//!
//! Extracts symbols and relationships from Kotlin source files (`.kt`, `.kts`)
//! using the tree-sitter-kotlin grammar.
//!
//! # Node kinds produced
//! - `class`: class_declaration
//! - `function`: top-level function_declaration
//! - `method`: function_declaration inside a class
//! - `interface`: interface_declaration
//! - `enum`: enum_class
//! - `variable`: local variable declarations
//! - `package`: package_header
//! - `property`: property_declaration (class member)
//! - `object`: object_declaration
//! - `file`: compilation unit
//!
//! # Edge kinds produced
//! - `calls`: call_expression
//! - `contains`: containment (file -> class -> method)
//! - `extends`: class/interface inheritance (supertype_list)
//! - `implements`: interface implementation (via delegation_specifier)
//! - `imports`: import_header

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Kotlin stdlib prefixes — filtered to reduce noise
// ---------------------------------------------------------------------------

const KOTLIN_STDLIB_PREFIXES: &[&str] = &[
    "kotlin.", "java.", "javax.", "android.", "androidx.",
];

fn is_kotlin_stdlib(s: &str) -> bool {
    KOTLIN_STDLIB_PREFIXES.iter().any(|p| s.starts_with(p))
}

// ---------------------------------------------------------------------------
// KotlinExtractor
// ---------------------------------------------------------------------------

pub struct KotlinExtractor;

impl Extractor for KotlinExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["kt", "kts"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["kotlin"]
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
                .unwrap_or("File")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        let mut walker = Walker::new();
        walker.walk_source_file(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker
// ---------------------------------------------------------------------------

struct Walker {
    class_stack: Vec<String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            class_stack: Vec::new(),
        }
    }

    fn current_class(&self) -> Option<&str> {
        self.class_stack.last().map(|s| s.as_str())
    }

    // ------------------------------------------------------------------
    // Source file walking
    // ------------------------------------------------------------------

    fn walk_source_file(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // First pass: extract package
        let mut package_name = String::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "package_header" {
                    package_name = self.resolve_package_name(source, child);
                }
            }
        }

        // Push package scope
        if !package_name.is_empty() {
            let pkg_id = ctx.add_node(
                NodeKind::Package,
                &package_name,
                &node,
                HashMap::new(),
            );
            let line = node.start_position().row as u32 + 1;
            ctx.add_edge(parent_id, &pkg_id, EdgeKind::Contains, line, Some(&package_name));
            ctx.push_scope_with_kind(&package_name, "package");
        }

        // Second pass: walk all declarations
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_declaration(source, child, ctx, parent_id)?;
            }
        }

        if !package_name.is_empty() {
            ctx.pop_scope();
        }

        Ok(())
    }

    fn resolve_package_name(&self, source: &[u8], node: Node) -> String {
        let mut parts: Vec<String> = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "identifier" || child.kind() == "simple_identifier" {
                    parts.push(get_text(source, Some(child)));
                }
            }
        }
        parts.join(".")
    }

    fn walk_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "object_declaration" => {
                self.extract_object(source, node, ctx, parent_id)?;
            }
            "function_declaration" => {
                let is_inside_class = self.current_class().is_some();
                let fn_kind = if is_inside_class {
                    NodeKind::Method
                } else {
                    NodeKind::Function
                };
                self.extract_function(source, node, ctx, parent_id, fn_kind)?;
            }
            "property_declaration" => {
                self.extract_property(source, node, ctx, parent_id)?;
            }
            "import_header" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "enum_class" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "companion_object" => {
                self.extract_companion_object(source, node, ctx, parent_id)?;
            }
            "package_header" => {
                // Already handled in first pass
            }
            "type_alias" => {
                // typealias could be extracted as TypeAlias in future
            }
            _ => {}
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    fn extract_class(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        let name = find_name_in_node(source, node, &["type_identifier", "identifier"]);
        if name.is_empty() {
            return Ok(String::new());
        }

        let line = node.start_position().row as u32 + 1;
        let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Supertype list (extends / implements)
        self.extract_supertypes(source, node, &class_id, ctx);

        // Walk class body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        if let Some(body) = node.child_by_field_name("body") {
            // class_body can be explicit or implicit; also try named children
            self.walk_class_body(source, body, ctx, &class_id)?;
        } else {
            // class_body not found via field name — search named children
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "class_body" {
                        self.walk_class_body(source, child, ctx, &class_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(class_id)
    }

    fn walk_class_body(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_class_member(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    fn walk_class_member(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "function_declaration" => {
                self.extract_function(source, node, ctx, parent_id, NodeKind::Method)?;
            }
            "property_declaration" => {
                self.extract_property(source, node, ctx, parent_id)?;
            }
            "class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "object_declaration" => {
                self.extract_object(source, node, ctx, parent_id)?;
            }
            "companion_object" => {
                self.extract_companion_object(source, node, ctx, parent_id)?;
            }
            "enum_class" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "init_block" | "anonymous_initializer" => {
                // init { ... } block — walk for calls
                self.walk_body_for_calls(source, node, ctx, parent_id)?;
            }
            _ => {}
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Object declaration extraction
    // ------------------------------------------------------------------

    fn extract_object(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        let name = find_name_in_node(source, node, &["type_identifier", "identifier"]);
        if name.is_empty() {
            return Ok(String::new());
        }

        let line = node.start_position().row as u32 + 1;
        let obj_id = ctx.add_node(NodeKind::Object, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &obj_id, EdgeKind::Contains, line, None);

        // Supertype list
        self.extract_supertypes(source, node, &obj_id, ctx);

        // Walk body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "object");
        ctx.push_scope_node(&obj_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_class_body(source, body, ctx, &obj_id)?;
        } else {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "class_body" {
                        self.walk_class_body(source, child, ctx, &obj_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(obj_id)
    }

    // ------------------------------------------------------------------
    // Companion object extraction
    // ------------------------------------------------------------------

    fn extract_companion_object(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // Companion object may or may not have a name
        let name = find_name_in_node(source, node, &["type_identifier", "identifier"]);
        let object_name = if name.is_empty() {
            "Companion".to_string()
        } else {
            name
        };

        let line = node.start_position().row as u32 + 1;
        let obj_id = ctx.add_node(NodeKind::Object, &object_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &obj_id, EdgeKind::Contains, line, None);

        // Walk body
        if let Some(body) = node.child_by_field_name("body") {
            self.walk_class_body(source, body, ctx, &obj_id)?;
        } else {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "class_body" {
                        self.walk_class_body(source, child, ctx, &obj_id)?;
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    fn extract_interface(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        let name = find_name_in_node(source, node, &["type_identifier", "identifier"]);
        if name.is_empty() {
            return Ok(String::new());
        }

        let line = node.start_position().row as u32 + 1;
        let iface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &iface_id, EdgeKind::Contains, line, None);

        // Supertype list (interface extends)
        self.extract_supertypes(source, node, &iface_id, ctx);

        // Walk body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "interface");
        ctx.push_scope_node(&iface_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_class_body(source, body, ctx, &iface_id)?;
        } else {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "class_body" {
                        self.walk_class_body(source, child, ctx, &iface_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(iface_id)
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    fn extract_enum(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        let name = find_name_in_node(source, node, &["type_identifier", "identifier"]);
        if name.is_empty() {
            return Ok(String::new());
        }

        let line = node.start_position().row as u32 + 1;
        let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

        // Walk body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "enum");
        ctx.push_scope_node(&enum_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_class_body(source, body, ctx, &enum_id)?;
        } else {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "class_body" || child.kind() == "enum_class_body" {
                        self.walk_class_body(source, child, ctx, &enum_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(enum_id)
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
        fn_kind: NodeKind,
    ) -> anyhow::Result<String> {
        let name = find_name_in_node(source, node, &["simple_identifier", "identifier"]);
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Check if abstract (no body)
        let has_body = node.child_by_field_name("body").is_some()
            || node.named_child_count() > 0
                && node.named_child(node.named_child_count() - 1)
                    .map(|c| c.kind() == "function_body")
                    .unwrap_or(false);

        if !has_body {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        let func_id = ctx.add_node(fn_kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        // Walk body for calls
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &func_id)?;
        } else {
            // Try to find function_body among named children
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "function_body" {
                        self.walk_body_for_calls(source, child, ctx, &func_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        Ok(func_id)
    }

    // ------------------------------------------------------------------
    // Property extraction
    // ------------------------------------------------------------------

    fn extract_property(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // Property has a variable_declaration child
        let line = node.start_position().row as u32 + 1;

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "variable_declaration" {
                    let prop_name = find_name_in_node(source, child, &["simple_identifier", "identifier"]);
                    if !prop_name.is_empty() {
                        let prop_id = ctx.add_node(
                            NodeKind::Property,
                            &prop_name,
                            &child,
                            HashMap::new(),
                        );
                        ctx.add_edge(parent_id, &prop_id, EdgeKind::Contains, line, None);
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    fn extract_import(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let import_path = build_import_path_kotlin(source, node);
        if !import_path.is_empty() && !is_kotlin_stdlib(&import_path) {
            let target_qn = build_qualified_target(&ctx.file_path, &import_path);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Supertype extraction (extends / implements)
    // ------------------------------------------------------------------

    fn extract_supertypes(
        &self,
        source: &[u8],
        node: Node,
        target_node_id: &str,
        ctx: &mut ExtractionContext,
    ) {
        let line = node.start_position().row as u32 + 1;

        // supertype_list or delegation_specifier
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "supertype_list" | "delegation_specifier" | "super_interfaces" => {
                        // Walk all named subtypes
                        for j in 0..child.named_child_count() {
                            if let Some(st) = child.named_child(j) {
                                let type_name = resolve_kotlin_type(source, st);
                                if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                                    let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&type_name));
                                }
                            }
                        }
                    }
                    "type_identifier" | "identifier" | "user_type" | "nullable_type" => {
                        // Direct supertype as named child of class_declaration
                        let type_name = resolve_kotlin_type(source, child);
                        if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                            let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&type_name));
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Body walking for calls
    // ------------------------------------------------------------------

    fn walk_body_for_calls(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
                // Recurse into arguments
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() == "call_suffix" || child.kind() == "value_arguments" {
                            self.walk_body_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
            "class_declaration" | "object_declaration" | "enum_class" | "companion_object" => {
                // Nested type inside method — walk as declaration
                self.walk_declaration(source, node, ctx, parent_id)?;
            }
            "property_declaration" => {
                // Local property inside function
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() == "variable_declaration" {
                            let var_name = find_name_in_node(source, child, &["simple_identifier", "identifier"]);
                            if !var_name.is_empty() {
                                let line = child.start_position().row as u32 + 1;
                                let var_id = ctx.add_node(
                                    NodeKind::Variable,
                                    &var_name,
                                    &child,
                                    HashMap::new(),
                                );
                                ctx.add_edge(parent_id, &var_id, EdgeKind::Writes, line, Some(&var_name));
                            }
                        }
                    }
                }
                // Recurse into expression parts
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            // Recurse into structural nodes
            "function_body"
            | "control_flow_body"
            | "block"
            | "statements"
            | "if_expression"
            | "when_expression"
            | "when_entry"
            | "for_statement"
            | "while_statement"
            | "do_while_statement"
            | "try_expression"
            | "catch_block"
            | "finally_block"
            | "return_expression"
            | "throw_expression"
            | "lambda_literal"
            | "anonymous_function"
            | "elvis_expression"
            | "infix_expression"
            | "binary_expression"
            | "prefix_expression"
            | "postfix_expression"
            | "parenthesized_expression"
            | "string_template"
            | "collection_literal_expression"
            | "argument"
            | "value_argument"
            | "lambda_argument"
            | "assignment"
            | "assignment_expression"
            | "declaration"
            | "expression" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            _ => {
                // Scan for call-related children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call_expression"
                            || ck == "block"
                            || ck == "function_body"
                            || ck == "return_expression"
                            || ck == "if_expression"
                            || ck == "when_expression"
                            || ck == "for_statement"
                            || ck == "property_declaration"
                            || ck == "class_declaration"
                            || ck == "lambda_literal"
                        {
                            self.walk_body_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
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

        // The callee is usually a simple_identifier or navigation_expression child
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "simple_identifier" | "identifier" => {
                        let name = get_text(source, Some(child));
                        if !name.is_empty() && !is_kotlin_stdlib(&name) {
                            let target_qn = build_call_target(
                                &ctx.file_path,
                                &self.class_stack,
                                &name,
                            );
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                        }
                    }
                    "navigation_expression" => {
                        // obj.method() — extract the final method name
                        let full = resolve_navigation_chain(source, child);
                        if !full.is_empty() {
                            let callee = full.rsplitn(2, '.').next().unwrap_or(&full);
                            if !is_kotlin_stdlib(callee) {
                                let target_qn = build_call_target(
                                    &ctx.file_path,
                                    &self.class_stack,
                                    callee,
                                );
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full));
                            }
                        }
                    }
                    "call_suffix" | "value_arguments" => {
                        // Skip, these contain arguments
                    }
                    _ => {}
                }
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Find a name in a node by searching for the first named child of given kinds.
fn find_name_in_node(source: &[u8], node: Node, kinds: &[&str]) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if kinds.contains(&child.kind()) {
                let text = get_text(source, Some(child));
                if !text.is_empty() {
                    return text;
                }
            }
        }
    }
    // Fallback: try child_by_field_name("name")
    if let Some(name_node) = node.child_by_field_name("name") {
        let text = get_text(source, Some(name_node));
        if !text.is_empty() {
            return text;
        }
    }
    String::new()
}

/// Build a target qualified name for a call edge.
fn build_call_target(file_path: &str, class_stack: &[String], callee: &str) -> String {
    if let Some(class_name) = class_stack.last() {
        format!("{file_path}::{class_name}.{callee}")
    } else {
        format!("{file_path}::{callee}")
    }
}

/// Build a qualified target for cross-reference edges.
fn build_qualified_target(file_path: &str, name: &str) -> String {
    format!("{file_path}::{name}")
}

/// Get the UTF-8 text of a node from the source bytes.
fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
    }
}

/// Resolve a Kotlin type reference to its text representation.
fn resolve_kotlin_type(source: &[u8], node: Node) -> String {
    match node.kind() {
        "type_identifier" | "identifier" | "simple_identifier" => {
            get_text(source, Some(node))
        }
        "user_type" => {
            // user_type contains type_identifier and possibly type_arguments
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "type_identifier" || child.kind() == "simple_identifier" {
                        return get_text(source, Some(child));
                    }
                }
            }
            get_text(source, Some(node))
        }
        "nullable_type" => {
            // nullable_type wraps a type; extract the inner type
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "type_identifier" | "user_type" | "simple_identifier" => {
                            return resolve_kotlin_type(source, child);
                        }
                        _ => {}
                    }
                }
            }
            String::new()
        }
        _ => get_text(source, Some(node)),
    }
}

/// Build import path from import_header node.
fn build_import_path_kotlin(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "simple_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                _ => {}
            }
        }
    }
    parts.join(".")
}

/// Resolve a navigation_expression chain like `obj.property.method()`.
fn resolve_navigation_chain(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();

    match node.kind() {
        "simple_identifier" | "identifier" => return get_text(source, Some(node)),
        "navigation_expression" => {
            // Find the navigation_suffix which contains simple_identifier
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "navigation_suffix" {
                        for j in 0..child.named_child_count() {
                            if let Some(sub) = child.named_child(j) {
                                if sub.kind() == "simple_identifier" || sub.kind() == "identifier" {
                                    parts.push(get_text(source, Some(sub)));
                                }
                            }
                        }
                    }
                }
            }
            // Recurse into the expression part
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "simple_identifier" | "identifier" => {
                            parts.push(get_text(source, Some(child)));
                        }
                        "navigation_expression" => {
                            parts.push(resolve_navigation_chain(source, child));
                        }
                        "call_expression" => {
                            // Intermediate call in chain
                            let mut call_parts = Vec::new();
                            for j in 0..child.named_child_count() {
                                if let Some(sub) = child.named_child(j) {
                                    if sub.kind() == "simple_identifier" || sub.kind() == "identifier" {
                                        call_parts.push(format!("{}()", get_text(source, Some(sub))));
                                    }
                                }
                            }
                            if !call_parts.is_empty() {
                                parts.push(call_parts.join(""));
                            }
                        }
                        _ => {}
                    }
                }
            }
        }
        _ => return get_text(source, Some(node)),
    }

    parts.reverse();
    parts.join(".")
}

