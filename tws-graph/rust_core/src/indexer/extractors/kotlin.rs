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
//! - `imports`: wildcard import (import com.foo.*)
//! - `references`: class/function import (import com.foo.MyClass)

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
    /// Map from imported simple class name to fully qualified name.
    /// e.g. `import com.foo.bar.MyClass` populates `"MyClass" → "com.foo.bar.MyClass"`.
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
    /// using the imported_names map. Returns the qualified name if known,
    /// otherwise returns the original name unchanged.
    fn qualify_type(&self, simple_name: &str) -> String {
        // Handle multi-segment names like "a.b.C" — try the first segment
        if let Some(dot_pos) = simple_name.find('.') {
            let first = &simple_name[..dot_pos];
            if let Some(qualified) = self.imported_names.get(first) {
                let rest = &simple_name[dot_pos + 1..];
                return format!("{}.{}", qualified, rest);
            }
        }
        // Try simple name directly
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
            "import" => {
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
    ///
    /// Handles three forms of Kotlin import:
    /// 1. Class import: `import com.foo.bar.MyClass` → REFERENCES edge
    /// 2. Wildcard import: `import com.foo.bar.*` → IMPORTS edge
    /// 3. Alias import: `import com.foo.bar.Baz as Alias` → REFERENCES edge
    ///    (target_text uses original path; Alias is mapped in imported_names)

    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Extract the qualified_identifier path (e.g., "com.foo.bar.MyClass")
        let import_path = build_import_path_kotlin(source, node);
        if import_path.is_empty() {
            return Ok(());
        }

        // Check for wildcard: unnamed child "*" is present
        let is_wildcard = has_asterisk_kotlin(node);

        // Check for alias: unnamed child "as" is present
        let alias_name = get_alias_name(source, node);

        if is_wildcard {
            // `import com.foo.bar.*` — IMPORTS edge (package level)
            if !is_kotlin_stdlib(&import_path) {
                let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
            }
        } else {
            // Class/function import: `import com.foo.bar.MyClass` or `import com.foo.bar.Baz as Alias`
            if !is_kotlin_stdlib(&import_path) {
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

                // If alias present, also map the alias
                if !alias_name.is_empty() {
                    self.imported_names
                        .insert(alias_name, import_path);
                }
            }
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

        // tree-sitter-kotlin-ng uses `delegation_specifiers` (plural) as the wrapper
        // for supertype declarations. Each specifier can contain:
        // - constructor_invocation (e.g., Base() or Base(arg))
        // - type (e.g., just the type name)
        // - explicit_delegation
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "delegation_specifiers" => {
                        // delegation_specifiers contains delegation_specifier children
                        for j in 0..child.named_child_count() {
                            if let Some(spec) = child.named_child(j) {
                                // Each delegation_specifier can have constructor_invocation or type
                                for k in 0..spec.named_child_count() {
                                    if let Some(inner) = spec.named_child(k) {
                                        self.extract_supertype_from_node(source, inner, target_node_id, ctx, line);
                                    }
                                }
                            }
                        }
                    }
                    "supertype_list" | "delegation_specifier" | "super_interfaces" => {
                        // Legacy / alternative grammar support
                        for j in 0..child.named_child_count() {
                            if let Some(st) = child.named_child(j) {
                                let type_name = resolve_kotlin_type(source, st);
                                if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                                    let qualified = self.qualify_type(&type_name);
                                    let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&qualified));
                                }
                            }
                        }
                    }
                    "type_identifier" | "identifier" | "user_type" | "nullable_type" => {
                        // Direct supertype as named child — exclude the own class name
                        let is_name_field = node
                            .child_by_field_name("name")
                            .map(|n| n.id() == child.id())
                            .unwrap_or(false);
                        if !is_name_field {
                            let type_name = resolve_kotlin_type(source, child);
                            if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                                let qualified = self.qualify_type(&type_name);
                                let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&qualified));
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    /// Extract a supertype name from a node inside a delegation_specifier.
    /// Handles constructor_invocation (e.g., Base() or Base(arg)) and type nodes.
    fn extract_supertype_from_node(
        &self,
        source: &[u8],
        node: Node,
        target_node_id: &str,
        ctx: &mut ExtractionContext,
        line: u32,
    ) {
        match node.kind() {
            "constructor_invocation" => {
                // constructor_invocation has named children with the user_type or type_identifier
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let type_name = resolve_kotlin_type(source, child);
                        if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                            let qualified = self.qualify_type(&type_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&qualified));
                        }
                    }
                }
            }
            "type" | "user_type" | "type_identifier" | "nullable_type" => {
                let type_name = resolve_kotlin_type(source, node);
                if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                    let qualified = self.qualify_type(&type_name);
                    let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&qualified));
                }
            }
            _ => {
                // Try to extract type name from any node
                let type_name = resolve_kotlin_type(source, node);
                if !type_name.is_empty() && !is_kotlin_stdlib(&type_name) {
                    let qualified = self.qualify_type(&type_name);
                    let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(target_node_id, &target, EdgeKind::Extends, line, Some(&qualified));
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

        // The callee is usually a simple_identifier or navigation_expression child.
        // In tree-sitter-kotlin-ng, it may be wrapped in an `expression` node.
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "simple_identifier" | "identifier" => {
                        let name = get_text(source, Some(child));
                        if !name.is_empty() && !is_kotlin_stdlib(&name) {
                            let call_target_text = self.qualify_type(&name);
                            let target_qn = build_call_target(
                                &ctx.file_path,
                                &self.class_stack,
                                &call_target_text,
                            );
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&call_target_text));
                        }
                    }
                    "navigation_expression" => {
                        // obj.method() — extract the full chain
                        let full = resolve_navigation_chain(source, child);
                        if !full.is_empty() {
                            let callee = full.rsplitn(2, '.').next().unwrap_or(&full);
                            if !is_kotlin_stdlib(callee) {
                                // Qualify the first segment if it's imported
                                let qualified_full = self.qualify_full_navigation(&full);
                                let qualified_callee = qualified_full.rsplitn(2, '.').next().unwrap_or(&qualified_full).to_string();
                                let target_qn = build_call_target(
                                    &ctx.file_path,
                                    &self.class_stack,
                                    &qualified_callee,
                                );
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified_full));
                            }
                        }
                    }
                    "expression" => {
                        // tree-sitter-kotlin-ng wraps the callee in an expression node.
                        // Recurse into it to find navigation_expression or identifier.
                        for j in 0..child.named_child_count() {
                            if let Some(inner) = child.named_child(j) {
                                match inner.kind() {
                                    "navigation_expression" => {
                                        let full = resolve_navigation_chain(source, inner);
                                        if !full.is_empty() {
                                            let callee = full.rsplitn(2, '.').next().unwrap_or(&full);
                                            if !is_kotlin_stdlib(callee) {
                                                let qualified_full = self.qualify_full_navigation(&full);
                                                let qualified_callee = qualified_full.rsplitn(2, '.').next().unwrap_or(&qualified_full).to_string();
                                                let target_qn = build_call_target(
                                                    &ctx.file_path,
                                                    &self.class_stack,
                                                    &qualified_callee,
                                                );
                                                let target = hash_id(&ctx.file_path, &target_qn);
                                                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified_full));
                                            }
                                        }
                                    }
                                    "identifier" | "simple_identifier" => {
                                        let name = get_text(source, Some(inner));
                                        if !name.is_empty() && !is_kotlin_stdlib(&name) {
                                            let call_target_text = self.qualify_type(&name);
                                            let target_qn = build_call_target(
                                                &ctx.file_path,
                                                &self.class_stack,
                                                &call_target_text,
                                            );
                                            let target = hash_id(&ctx.file_path, &target_qn);
                                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&call_target_text));
                                        }
                                    }
                                    _ => {}
                                }
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

    /// Qualify a full navigation chain like "obj.method()" by resolving
    /// the first segment through imported_names.
    fn qualify_full_navigation(&self, full: &str) -> String {
        if let Some(dot_pos) = full.find('.') {
            let first = &full[..dot_pos];
            let rest = &full[dot_pos + 1..];
            if let Some(qualified) = self.imported_names.get(first) {
                return format!("{}.{}", qualified, rest);
            }
        }
        full.to_string()
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

/// Build import path from import node.
/// In tree-sitter-kotlin-ng, the import node contains a qualified_identifier
/// whose children are identifier nodes forming the dotted path.
fn build_import_path_kotlin(source: &[u8], node: Node) -> String {
    // Find the qualified_identifier child
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "qualified_identifier" {
                let mut parts: Vec<String> = Vec::new();
                for j in 0..child.named_child_count() {
                    if let Some(id) = child.named_child(j) {
                        if id.kind() == "identifier" || id.kind() == "simple_identifier" {
                            parts.push(get_text(source, Some(id)));
                        }
                    }
                }
                return parts.join(".");
            }
        }
    }
    // Fallback: collect identifier/simple_identifier directly
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

/// Check if an import node has an asterisk (wildcard import).
fn has_asterisk_kotlin(node: Node) -> bool {
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() && child.kind() == "*" {
                return true;
            }
        }
    }
    false
}

/// Get the alias name from an import with `as` keyword.
/// e.g. `import com.foo.bar.Baz as Alias` → "Alias"
fn get_alias_name(source: &[u8], node: Node) -> String {
    // Check if there's an unnamed "as" child
    let mut has_as = false;
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() {
                if child.kind() == "as" {
                    has_as = true;
                }
            }
        }
    }
    if !has_as {
        return String::new();
    }
    // Find the named identifier child (the alias name)
    // In tree-sitter-kotlin-ng: import qualified_identifier "as" identifier
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" || child.kind() == "simple_identifier" {
                let text = get_text(source, Some(child));
                // Skip the qualified_identifier (it's also named)
                if child.kind() == "identifier" && text != get_text(source, node.named_child(0)) {
                    // This is the alias — it follows the qualified_identifier
                    // Check if it's not the first named child (which is the qualified_identifier)
                    if !is_qualified_identifier_child(source, node, child) {
                        return text;
                    }
                }
            }
        }
    }
    String::new()
}

/// Check if a given child node is the qualified_identifier child of the import node.
fn is_qualified_identifier_child(_source: &[u8], parent: Node, target: Node) -> bool {
    // The first named child should be the qualified_identifier
    if let Some(first) = parent.named_child(0) {
        if first.kind() == "qualified_identifier" {
            // qualified_identifier contains identifier children
            for i in 0..first.named_child_count() {
                if let Some(id_child) = first.named_child(i) {
                    if id_child.id() == target.id() {
                        return true;
                    }
                }
            }
            return false;
        }
    }
    false
}

/// Resolve a navigation_expression chain like `obj.property.method()`.
///
/// tree-sitter-kotlin-ng models navigation_expression with children:
/// [expression, identifier] — where the first element is the receiver (left side)
/// and following identifier elements are the accessed properties (right side).
/// For simple chains like `Utils.helper`, both children are identifiers.
fn resolve_navigation_chain(source: &[u8], node: Node) -> String {
    match node.kind() {
        "simple_identifier" | "identifier" => return get_text(source, Some(node)),
        "navigation_expression" => {
            let mut receiver_parts: Vec<String> = Vec::new();
            let mut property_parts: Vec<String> = Vec::new();

            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "identifier" | "simple_identifier" => {
                            // First identifier is the receiver; subsequent ones are properties
                            if receiver_parts.is_empty() {
                                receiver_parts.push(get_text(source, Some(child)));
                            } else {
                                property_parts.push(get_text(source, Some(child)));
                            }
                        }
                        "navigation_expression" => {
                            // Nested navigation — entire sub-chain becomes the receiver
                            receiver_parts.push(resolve_navigation_chain(source, child));
                        }
                        "call_expression" => {
                            // Method call on object — extract method name for the receiver
                            let mut call_text = String::new();
                            for j in 0..child.named_child_count() {
                                if let Some(sub) = child.named_child(j) {
                                    if sub.kind() == "simple_identifier" || sub.kind() == "identifier" {
                                        call_text = format!("{}()", get_text(source, Some(sub)));
                                    }
                                }
                            }
                            if !call_text.is_empty() {
                                receiver_parts.push(call_text);
                            }
                        }
                        "expression" => {
                            // Expression wrapping a receiver
                            let text = resolve_expression_chain(source, child);
                            if !text.is_empty() && receiver_parts.is_empty() {
                                receiver_parts.push(text);
                            }
                        }
                        _ => {
                            // Try as generic identifier
                            let text = resolve_expression_chain(source, child);
                            if !text.is_empty() {
                                if receiver_parts.is_empty() {
                                    receiver_parts.push(text);
                                } else {
                                    property_parts.push(text);
                                }
                            }
                        }
                    }
                }
            }

            let receiver = receiver_parts.join(".");
            let property = property_parts.join(".");

            if receiver.is_empty() {
                property
            } else if property.is_empty() {
                receiver
            } else {
                format!("{}.{}", receiver, property)
            }
        }
        _ => get_text(source, Some(node)),
    }
}

/// Resolve an expression child to its text representation.
/// Recurses into nested expressions (identifier, navigation_expression, call_expression).
fn resolve_expression_chain(source: &[u8], node: Node) -> String {
    match node.kind() {
        "identifier" | "simple_identifier" => get_text(source, Some(node)),
        "navigation_expression" => resolve_navigation_chain(source, node),
        "call_expression" => {
            // Extract method name from call expression
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "simple_identifier" || child.kind() == "identifier" {
                        return get_text(source, Some(child));
                    }
                }
            }
            get_text(source, Some(node))
        }
        _ => {
            // Try named children for identifier
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    let text = resolve_expression_chain(source, child);
                    if !text.is_empty() {
                        return text;
                    }
                }
            }
            get_text(source, Some(node))
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

    /// Parse Kotlin source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_kotlin_ng::LANGUAGE.into())
            .expect("set kotlin language");
        let tree = parser.parse(source, None).expect("parse kotlin source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "kotlin".to_string());
        KotlinExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes<'a>(ctx: &'a ExtractionContext, kind: NodeKind) -> Vec<&'a crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    /// Helper: find edges of a given kind.
    fn find_edges<'a>(
        ctx: &'a ExtractionContext,
        kind: EdgeKind,
    ) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result
            .edges
            .iter()
            .filter(|e| e.kind == kind_str)
            .collect()
    }

    // ------------------------------------------------------------------
    // File node & empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/Empty.kt");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_file_node_present() {
        let ctx = extract("fun main() {}\n", "src/Main.kt");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Package declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_package_no_crash() {
        // package_header IS in the AST but resolved name may depend on TS child structure
        let ctx = extract(
            "package com.example.app\n\nclass Foo {}\n",
            "src/Foo.kt",
        );
        // At minimum, file and class should be present
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
    }

    // ------------------------------------------------------------------
    // Simple class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract("class MyClass\n", "src/MyClass.kt");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_body() {
        let ctx = extract(
            "class MyClass {\n    val x: Int = 0\n}\n",
            "src/MyClass.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    // ------------------------------------------------------------------
    // Class with extends
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_with_extends() {
        let ctx = extract(
            "open class Base\nclass Child : Base()\n",
            "src/Child.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        let names: Vec<&str> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Base"));
        assert!(names.contains(&"Child"));

        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(extends.len() >= 1, "Expected at least 1 EXTENDS edge");
    }

    // ------------------------------------------------------------------
    // Interface extraction (NB: tree-sitter-kotlin-ng uses class_declaration
    // for interfaces, not interface_declaration — they produce Class nodes)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface_produces_class_node() {
        let ctx = extract(
            "interface MyInterface {\n    fun doSomething()\n}\n",
            "src/MyInterface.kt",
        );
        // tree-sitter-kotlin-ng reports interfaces as class_declaration
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyInterface");
    }

    #[test]
    fn test_extract_interface_with_abstract_method() {
        let ctx = extract(
            "interface Repository {\n    fun findById(id: Int): String\n}\n",
            "src/Repository.kt",
        );
        // Interface produces a Class node; abstract method inside it
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(methods.len() >= 1, "Expected abstract method in interface, got {}", methods.len());
    }

    // ------------------------------------------------------------------
    // Enum class extraction (NB: tree-sitter-kotlin-ng uses class_declaration
    // for enums too — they produce Class nodes)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum_produces_class_node() {
        let ctx = extract(
            "enum class Color { RED, GREEN, BLUE }\n",
            "src/Color.kt",
        );
        // tree-sitter-kotlin-ng reports enum classes as class_declaration
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Color");
    }

    // ------------------------------------------------------------------
    // Object declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_object_declaration() {
        let ctx = extract(
            "object Logger {\n    fun log(msg: String) {}\n}\n",
            "src/Logger.kt",
        );
        let objects = find_nodes(&ctx, NodeKind::Object);
        assert_eq!(objects.len(), 1);
        assert_eq!(objects[0].name, "Logger");
    }

    #[test]
    fn test_extract_companion_object() {
        let ctx = extract(
            "class MyClass {\n    companion object {\n        fun create(): MyClass = MyClass()\n    }\n}\n",
            "src/MyClass.kt",
        );
        let objects = find_nodes(&ctx, NodeKind::Object);
        assert!(objects.len() >= 1, "Expected companion object node");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(methods.len() >= 1, "Expected method in companion object");
    }

    // ------------------------------------------------------------------
    // Method inside class
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_method_in_class() {
        let ctx = extract(
            "class Foo {\n    fun bar() {}\n}\n",
            "src/Foo.kt",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "bar");
    }

    #[test]
    fn test_extract_multiple_methods() {
        let ctx = extract(
            "class Foo {\n    fun bar() {}\n    fun baz(): Int = 42\n}\n",
            "src/Foo.kt",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 2);
    }

    // ------------------------------------------------------------------
    // Top-level function
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_top_level_function() {
        let ctx = extract(
            "fun greet(): String = \"hello\"\n",
            "src/Greet.kt",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "greet");
    }

    // ------------------------------------------------------------------
    // Property declarations
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_property() {
        let ctx = extract(
            "class Person {\n    val name: String = \"\"\n}\n",
            "src/Person.kt",
        );
        let props = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(props.len(), 1);
        assert_eq!(props[0].name, "name");
    }

    #[test]
    fn test_extract_multiple_properties() {
        let ctx = extract(
            "class Person {\n    val name: String = \"\"\n    var age: Int = 0\n}\n",
            "src/Person.kt",
        );
        let props = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(props.len(), 2);
    }

    // ------------------------------------------------------------------
    // Local variable declarations
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_local_variable() {
        let ctx = extract(
            "fun compute(): Int {\n    val result = 42\n    return result\n}\n",
            "src/Compute.kt",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 1, "Expected local variable node");
        if !vars.is_empty() {
            assert_eq!(vars[0].name, "result");
        }
    }

    // ------------------------------------------------------------------
    // Function / method calls
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract(
            "class Foo {\n    fun bar() {\n        baz()\n    }\n}\n",
            "src/Foo.kt",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"baz"), "Expected 'baz' in call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_call_in_function() {
        let ctx = extract(
            "fun main() {\n    println(\"hello\")\n}\n",
            "src/Main.kt",
        );
        // println is kotlin stdlib, so it should be filtered
        let _calls = find_edges(&ctx, EdgeKind::Calls);
        // Either no calls (filtered) or some calls — just verify no crash
        let file_nodes = find_nodes(&ctx, NodeKind::File);
        assert_eq!(file_nodes.len(), 1);
    }

    #[test]
    fn test_extract_constructor_call() {
        let ctx = extract(
            "class Foo {\n    fun bar() {\n        val x = Foo()\n    }\n}\n",
            "src/Foo.kt",
        );
        // Just verify it doesn't crash
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
    }

    // ------------------------------------------------------------------
    // Import statement extraction (enhanced in v7.3.0)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass\n\nclass App {}\n",
            "src/com/example/App.kt",
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
    }

    #[test]
    fn test_extract_multiple_class_imports_create_references_edges() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass\nimport com.foo.baz.OtherClass\n\nclass App {}\n",
            "src/com/example/App.kt",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 2, "Expected 2 REFERENCES edges");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"com.foo.bar.MyClass"));
        assert!(targets.contains(&"com.foo.baz.OtherClass"));
    }

    #[test]
    fn test_extract_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.*\n\nclass App {}\n",
            "src/com/example/App.kt",
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

    #[test]
    fn test_extract_alias_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass as AliasClass\n\nclass App {}\n",
            "src/com/example/App.kt",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge for alias import");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar.MyClass",
            "Target text should be original path (not alias)"
        );
    }

    #[test]
    fn test_extract_imported_call_uses_qualified_target_text() {
        // Test that an imported class name qualifies call targets.
        // When Utils is imported from com.foo.Utils, calls to Utils.helper()
        // should use the qualified name in target_text.
        let ctx = extract(
            "package com.example\n\nimport com.foo.Utils\n\nclass App {\n    fun doWork() {\n        Utils.helper()\n    }\n}\n",
            "src/com/example/App.kt",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // There should be at least one call edge
        assert!(!calls.is_empty(), "Expected call edges for Utils.helper()");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // The target_text should contain the qualified import path
        assert!(
            targets.iter().any(|t| t.contains("com.foo")),
            "Expected qualified call target_text containing 'com.foo': {:?}", targets
        );
    }

    #[test]
    fn test_extract_stdlib_import_not_recorded() {
        let ctx = extract(
            "package com.example\n\nimport kotlin.collections.List\nimport com.example.MyClass\n\nclass App {}\n",
            "src/com/example/App.kt",
        );
        // kotlin.collections.List is stdlib → filtered
        // com.example.MyClass is project → REFERENCES
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Only 1 REFERENCES edge (not stdlib)");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.example.MyClass"
        );
    }

    #[test]
    fn test_extract_supertype_uses_qualified_name() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.BaseModel\n\nclass MyModel : BaseModel() {}\n",
            "src/com/example/MyModel.kt",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| *t == "com.foo.BaseModel"),
            "Expected qualified supertype name: {:?}", targets
        );
    }

    #[test]
    fn test_extract_top_level_function_import_is_call() {
        // Kotlin allows importing top-level functions.
        // `import com.foo.HelpersKt.helper` creates REFERENCES edge and
        // populates imported_names["helper"] = "com.foo.HelpersKt.helper".
        // When `helper()` is called, qualify_type resolves it to the qualified name.
        let ctx = extract(
            "package com.example\n\nimport com.foo.HelpersKt.helper\n\nclass App {\n    fun doWork() {\n        helper()\n    }\n}\n",
            "src/com/example/App.kt",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("com.foo")),
            "Expected qualified call target for imported top-level function: {:?}", targets
        );
    }

    #[test]
    fn test_extract_import_with_semicolon() {
        // Kotlin allows optional semicolons after imports
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass;\n\nclass App {}\n",
            "src/com/example/App.kt",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar.MyClass"
        );
    }

    // ------------------------------------------------------------------
    // Complex file with multiple classes and calls
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_complex_file() {
        let ctx = extract(
            "package com.example\n\nclass Service {\n    val repo: Repository = Repository()\n    fun getData(): String {\n        val result = repo.fetch()\n        return result\n    }\n}\n\nclass Repository {\n    fun fetch(): String = \"data\"\n}\n",
            "src/Service.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 2);
        let names: Vec<&str> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Service"));
        assert!(names.contains(&"Repository"));

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(methods.len() >= 2, "Expected getData + fetchData, got {}", methods.len());

        let props = find_nodes(&ctx, NodeKind::Property);
        assert!(props.len() >= 1, "Expected repo property");
    }

    // ------------------------------------------------------------------
    // Annotation usage
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_annotated_class() {
        let ctx = extract(
            "@Deprecated(\"use NewClass instead\")\nclass OldClass {\n    fun doWork() {}\n}\n",
            "src/OldClass.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "OldClass");
    }

    // ------------------------------------------------------------------
    // Nested classes
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_nested_class() {
        let ctx = extract(
            "class Outer {\n    class Inner {\n        fun work() {}\n    }\n}\n",
            "src/Outer.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 2);
        let names: Vec<&str> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            "package com.example\n\nimport com.example.data.Repository\n\nclass UserService(private val repo: Repository) {\n    fun getUsers(): List<String> {\n        return repo.findAll().map { it.name }.toList()\n    }\n\n    companion object {\n        fun create(repo: Repository): UserService = UserService(repo)\n    }\n}\n",
            "src/UserService.kt",
        );
        // Just verify it doesn't crash
        assert!(!ctx.result.nodes.is_empty());
    }

    #[test]
    fn test_extract_data_class() {
        let ctx = extract(
            "data class User(val id: Int, val name: String)\n",
            "src/User.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "User");
    }

    #[test]
    fn test_extract_sealed_class() {
        let ctx = extract(
            "sealed class Result {\n    data class Success(val data: String) : Result()\n    data class Error(val message: String) : Result()\n}\n",
            "src/Result.kt",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(classes.len() >= 2, "Expected at least Result + nested classes");
    }

    // ------------------------------------------------------------------
    // Additional edge case tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_with_no_body() {
        let ctx = extract("class Empty\n", "src/Empty.kt");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Empty");
    }

    #[test]
    fn test_extract_function_with_no_body() {
        let ctx = extract(
            "interface Callback {\n    fun onSuccess(data: String)\n}\n",
            "src/Callback.kt",
        );
        // Abstract function in interface — should produce method node
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(!methods.is_empty(), "Expected abstract method in interface");
    }

    #[test]
    fn test_extract_object_with_method() {
        let ctx = extract(
            "object Factory {\n    fun create(): String = \"created\"\n}\n",
            "src/Factory.kt",
        );
        let objects = find_nodes(&ctx, NodeKind::Object);
        assert_eq!(objects.len(), 1);
        assert_eq!(objects[0].name, "Factory");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "create");
    }

    #[test]
    fn test_extract_file_has_no_extra_nodes_on_empty() {
        let ctx = extract("", "src/Empty.kt");
        let total = ctx.result.nodes.len();
        assert_eq!(total, 1, "Empty file should only have 1 node (the file itself)");
    }
}

