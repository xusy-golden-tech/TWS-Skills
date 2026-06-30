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
//! - `imports`: import declarations (wildcard)
//! - `references`: class/function import (class import, multi-import, alias import)
//! - `extends`: class/trait inheritance (extends clause)
//! - `implements`: trait mixing (with clause)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Scala stdlib prefixes — filtered to reduce noise
// ---------------------------------------------------------------------------

const SCALA_STDLIB_PREFIXES: &[&str] = &[
    "scala.", "java.", "javax.", "sun.",
];

fn is_scala_stdlib(s: &str) -> bool {
    SCALA_STDLIB_PREFIXES.iter().any(|p| s.starts_with(p))
}

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
    /// Map from imported simple class name to fully qualified name.
    /// e.g. `import com.foo.bar.Baz` populates `"Baz" → "com.foo.bar.Baz"`.
    /// e.g. `import com.foo.bar.{Baz => Alias}` populates `"Alias" → "com.foo.bar.Baz"`.
    imported_names: HashMap<String, String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            class_stack: Vec::new(),
            imported_names: HashMap::new(),
        }
    }

    fn current_class(&self) -> Option<&str> {
        self.class_stack.last().map(|s| s.as_str())
    }

    /// Resolve a possibly-simple type name to its fully qualified form
    /// using the imported_names map.
    fn qualify_type(&self, simple_name: &str) -> String {
        if let Some(dot_pos) = simple_name.find('.') {
            let first = &simple_name[..dot_pos];
            if let Some(qualified) = self.imported_names.get(first) {
                let rest = &simple_name[dot_pos + 1..];
                return format!("{}.{}", qualified, rest);
            }
        }
        self.imported_names
            .get(simple_name)
            .cloned()
            .unwrap_or_else(|| simple_name.to_string())
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
                if child.kind() == "package_clause" {
                    if let Some(pkg_id) = find_child_by_kind(child, "package_identifier") {
                        package_name = get_text(source, Some(pkg_id));
                    }
                }
            }
        }

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

    fn walk_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "class_definition" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "object_definition" => {
                self.extract_object(source, node, ctx, parent_id)?;
            }
            "trait_definition" => {
                self.extract_trait(source, node, ctx, parent_id)?;
            }
            "function_definition" => {
                self.extract_function(source, node, ctx, parent_id)?;
            }
            "val_definition" | "var_definition" => {
                self.extract_variable(source, node, ctx, parent_id, node.kind())?;
            }
            "import_declaration" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
            }
            _ => {
                // Recurse into structural nodes
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_declaration(source, child, ctx, parent_id)?;
                    }
                }
            }
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
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let mut extra = HashMap::new();

        if let Some(mods) = find_child_by_kind(node, "access_modifier") {
            extra.insert("visibility".to_string(), get_text(source, Some(mods)));
        }
        if find_child_by_kind(node, "abstract_modifier").is_some() {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Extends clause
        if let Some(extends) = find_child_by_kind(node, "extends_clause") {
            let mut first = true;
            for i in 0..extends.named_child_count() {
                if let Some(child) = extends.named_child(i) {
                    if child.kind() == "type_identifier" {
                        let base_name = get_text(source, Some(child));
                        if !is_scala_stdlib(&base_name) {
                            let qualified = self.qualify_type(&base_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            if first {
                                ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&qualified));
                                first = false;
                            } else {
                                ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&qualified));
                            }
                        }
                    }
                }
            }
        }

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        if let Some(body) = find_child_by_kind(node, "template_body") {
            self.walk_class_members(source, body, ctx, &class_id)?;
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Object extraction
    // ------------------------------------------------------------------

    fn extract_object(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let obj_id = ctx.add_node(NodeKind::Object, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &obj_id, EdgeKind::Contains, line, None);

        // Extends clause
        if let Some(extends) = find_child_by_kind(node, "extends_clause") {
            for i in 0..extends.named_child_count() {
                if let Some(child) = extends.named_child(i) {
                    if child.kind() == "type_identifier" {
                        let base_name = get_text(source, Some(child));
                        if !is_scala_stdlib(&base_name) {
                            let qualified = self.qualify_type(&base_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(&obj_id, &target, EdgeKind::Extends, line, Some(&qualified));
                        }
                    }
                }
            }
        }

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "object");
        ctx.push_scope_node(&obj_id);

        if let Some(body) = find_child_by_kind(node, "template_body") {
            self.walk_class_members(source, body, ctx, &obj_id)?;
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Trait extraction
    // ------------------------------------------------------------------

    fn extract_trait(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let trait_id = ctx.add_node(NodeKind::Trait, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &trait_id, EdgeKind::Contains, line, None);

        // Extends clause
        if let Some(extends) = find_child_by_kind(node, "extends_clause") {
            for i in 0..extends.named_child_count() {
                if let Some(child) = extends.named_child(i) {
                    if child.kind() == "type_identifier" {
                        let base_name = get_text(source, Some(child));
                        if !is_scala_stdlib(&base_name) {
                            let qualified = self.qualify_type(&base_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(&trait_id, &target, EdgeKind::Extends, line, Some(&qualified));
                        }
                    }
                }
            }
        }

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "trait");
        ctx.push_scope_node(&trait_id);

        if let Some(body) = find_child_by_kind(node, "template_body") {
            self.walk_class_members(source, body, ctx, &trait_id)?;
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
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
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&fn_id);

        // Walk the block/body for calls
        if let Some(body) = find_child_by_kind(node, "block") {
            self.walk_body_for_calls(source, body, ctx, &fn_id)?;
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Variable extraction (val / var)
    // ------------------------------------------------------------------

    fn extract_variable(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        kind: &str,
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let mut extra = HashMap::new();
        extra.insert("val_or_var".to_string(), kind.to_string());

        let var_id = ctx.add_node(NodeKind::Variable, &name, &node, extra);
        ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);

        Ok(())
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------
    ///
    /// Handles four forms of Scala import:
    /// 1. Class import: `import com.foo.bar.Baz` → REFERENCES edge
    /// 2. Wildcard import: `import com.foo.bar._` → IMPORTS edge
    /// 3. Multi-import: `import com.foo.bar.{Baz, Qux}` → REFERENCES edges
    /// 4. Alias import: `import com.foo.bar.{Baz => Alias}` → REFERENCES edge
    ///    (target_text uses original path; Alias mapped in imported_names)

    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Check for namespace_selectors child (multi-import / aliased import)
        if let Some(selectors) = find_child_by_kind(node, "namespace_selectors") {
            // Build the package path from identifiers outside selectors
            let package_path = build_import_package_path(source, node);

            // Process each selector
            for i in 0..selectors.named_child_count() {
                if let Some(sel) = selectors.named_child(i) {
                    self.process_import_selector(source, sel, &package_path, ctx, parent_id, line)?;
                }
            }
            return Ok(());
        }

        // Check for wildcard: `_` or `*`
        let has_wildcard = has_wildcard_child(node);

        // Build the full import path from all identifiers
        let import_path = build_scala_import_path(source, node);

        if import_path.is_empty() {
            return Ok(());
        }

        if has_wildcard {
            // `import com.foo.bar._` → IMPORTS edge
            if !is_scala_stdlib(&import_path) {
                let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
            }
        } else {
            // Class/function import: `import com.foo.bar.Baz`
            // Split into package path + class name
            if !is_scala_stdlib(&import_path) {
                let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&import_path));

                // Populate imported_names: simple name → fully qualified name
                if let Some(simple_name) = import_path.rsplit('.').next() {
                    if !simple_name.is_empty() {
                        self.imported_names
                            .insert(simple_name.to_string(), import_path.clone());
                    }
                }
            }
        }

        Ok(())
    }

    /// Process a single import selector (simple or aliased).
    fn process_import_selector(
        &mut self,
        source: &[u8],
        sel: Node,
        package_path: &str,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
    ) -> anyhow::Result<()> {
        match sel.kind() {
            "identifier" => {
                let name = get_text(source, Some(sel));
                if name.is_empty() {
                    return Ok(());
                }
                let fqn = if package_path.is_empty() {
                    name.clone()
                } else {
                    format!("{}.{}", package_path, name)
                };
                if !is_scala_stdlib(&fqn) {
                    let target_qn = build_qualified_target(&ctx.file_path, &fqn);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&fqn));

                    self.imported_names
                        .insert(name, fqn);
                }
            }
            "arrow_renamed_identifier" => {
                // arrow_renamed_identifier has children: identifier (original name), identifier (alias)
                // The first identifier is the original, the second is the alias.
                let mut identifiers: Vec<String> = Vec::new();
                for i in 0..sel.named_child_count() {
                    if let Some(child) = sel.named_child(i) {
                        if child.kind() == "identifier" {
                            identifiers.push(get_text(source, Some(child)));
                        } else if child.kind() == "namespace_wildcard" || child.kind() == "wildcard" {
                            identifiers.push(String::new()); // wildcard placeholder
                        }
                    }
                }
                let original = identifiers.first().cloned().unwrap_or_default();
                let alias = identifiers.get(1).cloned().unwrap_or_else(|| original.clone());

                if original.is_empty() {
                    return Ok(());
                }

                let fqn = if package_path.is_empty() {
                    original.clone()
                } else {
                    format!("{}.{}", package_path, original)
                };

                if !is_scala_stdlib(&fqn) {
                    let target_qn = build_qualified_target(&ctx.file_path, &fqn);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&fqn));

                    // Map the alias to the qualified original name
                    if !alias.is_empty() {
                        self.imported_names.insert(alias, fqn);
                    }
                }
            }
            "wildcard" | "namespace_wildcard" => {
                // `import com.foo.{_}` or `import com.foo.{_ => Alias}` inside selectors
                // The wildcard itself cannot be resolved — skip
            }
            _ => {
                // Unknown selector kind — try to extract identifiers
                for i in 0..sel.named_child_count() {
                    if let Some(child) = sel.named_child(i) {
                        if child.kind() == "identifier" {
                            let name = get_text(source, Some(child));
                            if !name.is_empty() {
                                let fqn = if package_path.is_empty() {
                                    name.clone()
                                } else {
                                    format!("{}.{}", package_path, name)
                                };
                                if !is_scala_stdlib(&fqn) {
                                    let target_qn = build_qualified_target(&ctx.file_path, &fqn);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&fqn));

                                    self.imported_names
                                        .insert(name, fqn);
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Class body walking for members + calls
    // ------------------------------------------------------------------

    fn walk_class_members(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "class_definition" => {
                        self.extract_class(source, child, ctx, parent_id)?;
                    }
                    "object_definition" => {
                        self.extract_object(source, child, ctx, parent_id)?;
                    }
                    "trait_definition" => {
                        self.extract_trait(source, child, ctx, parent_id)?;
                    }
                    "function_definition" => {
                        self.extract_function(source, child, ctx, parent_id)?;
                    }
                    "val_definition" | "var_definition" => {
                        self.extract_variable(source, child, ctx, parent_id, child.kind())?;
                    }
                    "call_expression" => {
                        self.extract_call(source, child, ctx, parent_id)?;
                    }
                    _ => {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
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
            }
            "class_definition" | "object_definition" | "trait_definition" => {
                self.walk_declaration(source, node, ctx, parent_id)?;
            }
            // Recurse into structural nodes
            "block"
            | "if_expression"
            | "while_expression"
            | "do_while_expression"
            | "for_expression"
            | "try_expression"
            | "catch_clause"
            | "finally_clause"
            | "match_expression"
            | "case_clause"
            | "return_expression"
            | "throw_expression"
            | "val_definition"
            | "var_definition"
            | "assignment_expression"
            | "binary_expression"
            | "parenthesized_expression"
            | "function_definition" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            _ => {
                // For other nodes, scan for call-related children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call_expression"
                            || ck == "block"
                            || ck == "if_expression"
                            || ck == "while_expression"
                            || ck == "match_expression"
                            || ck == "for_expression"
                            || ck == "return_expression"
                            || ck == "val_definition"
                            || ck == "var_definition"
                            || ck == "class_definition"
                            || ck == "function_definition"
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
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // The call target can be:
        // - a bare identifier: `baz()`
        // - a field_expression: `Utils.helper()`
        if let Some(field_expr) = find_child_by_kind(node, "field_expression") {
            // e.g., `Utils.helper()` → field_expression with children [Utils, helper]
            let mut parts: Vec<String> = Vec::new();
            for i in 0..field_expr.named_child_count() {
                if let Some(child) = field_expr.named_child(i) {
                    if child.kind() == "identifier" {
                        parts.push(get_text(source, Some(child)));
                    }
                }
            }
            if !parts.is_empty() {
                let full_name = parts.join(".");
                if !is_scala_stdlib(&full_name) {
                    let qualified = self.qualify_type(&full_name);
                    let target_qn = build_call_target(
                        &ctx.file_path,
                        &self.class_stack,
                        &qualified,
                    );
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified));
                }
            }
        } else if let Some(callee) = find_child_by_kind(node, "identifier") {
            // Simple call: `baz()`
            let callee_name = get_text(source, Some(callee));
            if !callee_name.is_empty() && !is_scala_stdlib(&callee_name) {
                // Check if callee is imported → use qualified name
                let call_target_text = self.qualify_type(&callee_name);
                let target_qn = build_call_target(
                    &ctx.file_path,
                    &self.class_stack,
                    &call_target_text,
                );
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&call_target_text));
            }
        }

        // Recurse into arguments for nested calls
        if let Some(args) = find_child_by_kind(node, "arguments") {
            self.walk_body_for_calls(source, args, ctx, parent_id)?;
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Tree walking helpers
    // ------------------------------------------------------------------

    fn walk_all_children(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_declaration(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Import helper functions
// ---------------------------------------------------------------------------

/// Build the import path from all identifier children (recursively).
fn build_scala_import_path(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    collect_import_identifiers(source, node, &mut parts);
    parts.join(".")
}

/// Build the package path (excluding import_selectors).
/// Collects identifier children that are direct or indirect descendants
/// but stops at import_selectors boundary.
fn build_import_package_path(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "type_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                "import_selectors" | "namespace_selectors" => {
                    // Stop at selectors
                    break;
                }
                _ => {
                    // Recurse for stable_identifier / scoped_identifier wrappers
                    collect_import_identifiers_from_child(source, child, &mut parts);
                }
            }
        }
    }
    parts.join(".")
}

fn collect_import_identifiers(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "type_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                "import_selectors" => {
                    // Stop collecting — selectors handled separately
                }
                _ => {
                    collect_import_identifiers(source, child, parts);
                }
            }
        }
    }
}

fn collect_import_identifiers_from_child(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "type_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                _ => {
                    collect_import_identifiers_from_child(source, child, parts);
                }
            }
        }
    }
}

/// Check if an import node has a wildcard child (`_`).
/// In tree-sitter-scala, this is a named node `namespace_wildcard`.
fn has_wildcard_child(node: Node) -> bool {
    // Check named children for namespace_wildcard (tree-sitter-scala)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "namespace_wildcard" || child.kind() == "wildcard" {
                return true;
            }
        }
    }
    // Check unnamed children for "_" or "*" (fallback)
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() && (child.kind() == "_" || child.kind() == "*") {
                return true;
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// General helper functions
// ---------------------------------------------------------------------------

fn build_qualified_target(file_path: &str, name: &str) -> String {
    format!("{file_path}::{name}")
}

fn build_call_target(file_path: &str, class_stack: &[String], callee: &str) -> String {
    if let Some(class_name) = class_stack.last() {
        format!("{file_path}::{class_name}.{callee}")
    } else {
        format!("{file_path}::{callee}")
    }
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

    #[test]
    fn test_class_extends_uses_qualified_name() {
        let ctx = extract(
            "import com.foo.BaseModel\nclass MyModel extends BaseModel\n",
            "src/test.scala",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| *t == "com.foo.BaseModel"),
            "Expected qualified supertype name: {:?}", targets
        );
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

    // ======================================================================
    // Stage 11: Cross-file import resolution tests
    // ======================================================================

    // ------------------------------------------------------------------
    // Import: class import → REFERENCES edge
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge for class import");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar.MyClass"
        );

        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 0, "No IMPORTS edge for class import (uses REFERENCES)");

        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "App");
    }

    #[test]
    fn test_extract_multiple_class_imports_create_references_edges() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass\nimport com.foo.baz.OtherClass\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 2, "Expected 2 REFERENCES edges");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"com.foo.bar.MyClass"));
        assert!(targets.contains(&"com.foo.baz.OtherClass"));
    }

    // ------------------------------------------------------------------
    // Import: wildcard import → IMPORTS edge
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar._\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1, "Expected 1 IMPORTS edge for wildcard import");
        assert_eq!(
            imports[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar"
        );

        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 0, "No REFERENCES edge for wildcard import");
    }

    // ------------------------------------------------------------------
    // Import: multi-import → REFERENCES edges
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_multi_import_creates_references_edges() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.{Baz, Qux}\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        // Two symbols imported → 2 REFERENCES edges
        assert!(
            refs.len() >= 2,
            "Expected at least 2 REFERENCES edges for multi-import, got {}",
            refs.len()
        );
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.ends_with("Baz")),
            "Expected a REF edge containing 'Baz': {:?}", targets
        );
        assert!(
            targets.iter().any(|t| t.ends_with("Qux")),
            "Expected a REF edge containing 'Qux': {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Import: alias import → REFERENCES edge with original path
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_alias_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.{Baz => AliasClass}\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected at least 1 REFERENCES edge for alias import");
        // Target text should contain the original name (Baz), not the alias
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("Baz")),
            "Expected target_text containing 'Baz': {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Import: imported call uses qualified target_text
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_imported_call_uses_qualified_target_text() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.Utils\n\nclass App {\n  def doWork(): Unit = {\n    Utils.helper()\n  }\n}\n",
            "src/com/example/App.scala",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // Check that there's a call edge
        assert!(!calls.is_empty(), "Expected at least 1 call edge");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // The target_text should contain the qualified import path for Utils.helper()
        assert!(
            targets.iter().any(|t| t.contains("com.foo")),
            "Expected qualified call target_text containing 'com.foo': {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Import: non-imported call uses bare name
    // ------------------------------------------------------------------

    #[test]
    fn test_non_imported_call_uses_bare_name() {
        let ctx = extract(
            "class Foo {\n  def bar(): Unit = {\n    localFunc()\n  }\n}\n",
            "src/test.scala",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least 1 call edge");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| *t == "localFunc"),
            "Expected bare 'localFunc' in call targets: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Import: stdlib import not recorded (scala.*, java.*)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_stdlib_import_not_recorded() {
        let ctx = extract(
            "package com.example\n\nimport scala.collection.mutable.ListBuffer\nimport com.example.MyClass\n\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        // Only the project-internal import should create a REFERENCES edge
        assert_eq!(refs.len(), 1, "Only 1 REFERENCES edge (not stdlib)");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.example.MyClass"
        );
    }

    // ------------------------------------------------------------------
    // Import: import_declaration still produces IMPORTS edge (for backward compat)
    // Renamed: now test that old IMPORTS test still passes (becomes REFERENCES or IMPORTS)
    // ------------------------------------------------------------------

    #[test]
    fn test_import_declaration() {
        let ctx = extract(
            "import scala.collection.mutable.ListBuffer\nclass Foo\n",
            "src/test.scala",
        );
        // scala.* imports are filtered (stdlib), so there should be no REFERENCES or IMPORTS
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let refs = find_edges(&ctx, EdgeKind::References);
        // Both should be 0 since scala.* is stdlib-filtered
        assert_eq!(
            imports.len() + refs.len(),
            0,
            "scala stdlib import should be filtered out"
        );
    }

    // ------------------------------------------------------------------
    // Edge case: import with semicolons (Scala allows optional semicolons)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_import_with_newline_sep() {
        let ctx = extract(
            "package com.example\nimport com.foo.bar.MyClass\nimport com.foo.baz.OtherClass\nclass App {}\n",
            "src/com/example/App.scala",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 2, "Expected 2 REFERENCES edges for separate imports");
    }
}
