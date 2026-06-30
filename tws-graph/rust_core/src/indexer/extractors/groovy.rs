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
//! - `package`: package declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: method invocations
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import declarations (wildcard)
//! - `references`: class/function import (class import)
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
// Groovy stdlib / external prefixes
// ---------------------------------------------------------------------------

const GROOVY_STDLIB_PREFIXES: &[&str] = &[
    "java.", "javax.", "jakarta.", "groovy.", "groovyjarjar.", "groovyx.",
    "org.codehaus.groovy.", "org.codehaus.gpars.", "org.w3c.", "org.xml.",
    "org.omg.", "org.ietf.", "com.sun.", "sun.",
];

fn is_groovy_stdlib(s: &str) -> bool {
    GROOVY_STDLIB_PREFIXES.iter().any(|p| s.starts_with(p))
}

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
    /// e.g. `import static com.foo.bar.Utils.helper` populates `"helper" → "com.foo.bar.Utils.helper"`.
    /// e.g. `import com.foo.Bar as Alias` populates `"Alias" → "com.foo.Bar"`.
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
                if child.kind() == "package_declaration" {
                    package_name = self.resolve_package_source(source, child);
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

    fn resolve_package_source(&self, source: &[u8], node: Node) -> String {
        let mut parts: Vec<String> = Vec::new();
        collect_import_identifiers(source, node, &mut parts);
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
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "enum_declaration" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "method_declaration" | "constructor_declaration" => {
                self.extract_method(source, node, ctx, parent_id)?;
            }
            "field_declaration" => {
                self.extract_field(source, node, ctx, parent_id)?;
            }
            "import_declaration" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "method_invocation" | "call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
            }
            "annotation" | "marker_annotation" => {
                self.extract_annotation(source, node, ctx, parent_id)?;
            }
            "package_declaration" => {
                // Already handled in first pass
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

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Handle superclass (extends)
        if let Some(superclass) = find_child_by_kind(node, "superclass") {
            for i in 0..superclass.named_child_count() {
                if let Some(child) = superclass.named_child(i) {
                    if child.kind() == "type_identifier" || child.kind() == "identifier" {
                        let base_name = get_text(source, Some(child));
                        if !is_groovy_stdlib(&base_name) {
                            let qualified = self.qualify_type(&base_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&qualified));
                        }
                    }
                }
            }
        }

        // Handle super_interfaces (implements)
        if let Some(supers) = find_child_by_kind(node, "super_interfaces") {
            let mut type_names: Vec<String> = Vec::new();
            collect_type_identifiers(source, supers, &mut type_names);
            for iface_name in type_names {
                if !is_groovy_stdlib(&iface_name) {
                    let qualified = self.qualify_type(&iface_name);
                    let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&qualified));
                }
            }
        }

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        // Walk all children for annotations, members, and calls
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "superclass" | "super_interfaces" => {} // Already handled above
                    _ => {
                        self.walk_declaration(source, child, ctx, &class_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
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
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;

        let iface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &iface_id, EdgeKind::Contains, line, None);

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "interface");
        ctx.push_scope_node(&iface_id);

        if let Some(body) = find_child_by_kind(node, "interface_body") {
            self.walk_all_children(source, body, ctx, &iface_id)?;
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
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
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "identifier");
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;

        let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "enum");
        ctx.push_scope_node(&enum_id);

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
                    } else {
                        self.walk_declaration(source, child, ctx, &enum_id)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Method / constructor extraction
    // ------------------------------------------------------------------

    fn extract_method(
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

        let method_id = ctx.add_node(NodeKind::Method, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "method");
        ctx.push_scope_node(&method_id);

        // Walk body for calls and nested constructs
        if let Some(body) = find_child_by_kind(node, "block") {
            self.walk_all_children(source, body, ctx, &method_id)?;
        }

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Field extraction
    // ------------------------------------------------------------------

    fn extract_field(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let declarators = find_all_children_by_kind(node, "variable_declarator");
        if declarators.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
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

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Check for wildcard: `import com.foo.*`
        let has_wildcard = has_wildcard_child(node);

        // Build the full import path (excluding "import", "static", "as" keywords)
        let import_path = build_groovy_import_path(source, node);

        if import_path.is_empty() {
            return Ok(());
        }

        // Check for alias: `import com.foo.Bar as Alias`
        if let Some(alias) = self.extract_alias_text(source, node, &import_path) {
            // Split import_path: last segment is the class/function name
            if let Some(last_dot) = import_path.rfind('.') {
                let fqn = import_path.clone();
                let alias_name = alias;

                if !is_groovy_stdlib(&fqn) {
                    let target_qn = build_qualified_target(&ctx.file_path, &fqn);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&fqn));

                    // Map alias to the fully qualified name
                    self.imported_names.insert(alias_name, fqn);
                }
            }
            return Ok(());
        }

        if has_wildcard {
            // Wildcard import: `import com.foo.bar.*;` or `import static com.foo.bar.*;`
            // Create IMPORTS edge to the package
            if !is_groovy_stdlib(&import_path) {
                let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
            }
        } else {
            // Class import: `import com.foo.bar.MyClass;`
            // or static method import: `import static com.foo.bar.Utils.helper;`
            // Create REFERENCES edge with the fully qualified name
            if !is_groovy_stdlib(&import_path) {
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

    /// Extract alias text from an import with `as` clause.
    /// e.g., `import com.foo.Bar as Alias` → returns Some("Alias").
    fn extract_alias_text(
        &self,
        source: &[u8],
        node: Node,
        import_path: &str,
    ) -> Option<String> {
        // Look for 'as' keyword followed by an identifier
        let mut found_as = false;
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "identifier" {
                    let text = get_text(source, Some(child));
                    if found_as {
                        // This is the alias name (after "as")
                        // Make sure it's not part of the import path
                        if text != import_path && !import_path.ends_with(&text) {
                            // Verify the text is after the last dot in import_path
                            if !import_path.contains(&format!(".{}", text)) || import_path.ends_with(&text) {
                                // This is likely the alias
                                return Some(text);
                            }
                        }
                    }
                    if text == "as" {
                        found_as = true;
                    }
                }
            }
        }
        None
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

        // Collect all direct identifier children of the call node.
        // In Groovy, `Utils.helper()` is parsed as method_invocation with
        // identifier("Utils"), identifier("helper") — last one is the method name.
        let identifiers: Vec<String> = node
            .named_children(&mut node.walk())
            .filter(|c| c.kind() == "identifier")
            .map(|c| get_text(source, Some(c)))
            .collect();

        // First check for field_access (e.g. obj.method() in some parse variants)
        if let Some(fa) = find_child_by_kind(node, "field_access") {
            let mut parts: Vec<String> = Vec::new();
            collect_identifiers_for_call(source, fa, &mut parts);
            if let Some(method_name) = parts.last().cloned() {
                if !is_groovy_builtin(&method_name) {
                    let full_name = parts.join(".");
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
        } else if identifiers.len() >= 2 {
            // Multi-identifier call: `Utils.helper()` → identifiers = ["Utils", "helper"]
            // The last identifier is the method name, preceding ones form the object chain.
            let full_name = identifiers.join(".");
            let method_name = identifiers.last().unwrap();
            if !is_groovy_builtin(method_name) {
                let qualified = self.qualify_type(&full_name);
                let target_qn = build_call_target(
                    &ctx.file_path,
                    &self.class_stack,
                    &qualified,
                );
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&qualified));
            }
        } else if let Some(name_node) = find_child_by_kind(node, "identifier") {
            // Simple call: `baz()` → single identifier
            let callee_name = get_text(source, Some(name_node));
            if !callee_name.is_empty() && !is_groovy_builtin(&callee_name) {
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
            self.walk_all_children(source, args, ctx, parent_id)?;
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Annotation extraction
    // ------------------------------------------------------------------

    fn extract_annotation(
        &mut self,
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
// Import helper functions
// ---------------------------------------------------------------------------

/// Build the import path from an import_declaration node.
/// Collects identifiers recursively, excluding keyword children.
fn build_groovy_import_path(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    collect_import_identifiers(source, node, &mut parts);
    parts.join(".")
}

/// Recursively collect identifiers from a node for building import paths.
/// Skips keywords and special nodes.
fn collect_import_identifiers(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "type_identifier" => {
                    parts.push(get_text(source, Some(child)));
                }
                "scoped_identifier" | "scoped_type_identifier" => {
                    collect_import_identifiers(source, child, parts);
                }
                "asterisk" | "wildcard" => {
                    // Stop at wildcards — don't collect
                }
                _ => {
                    collect_import_identifiers(source, child, parts);
                }
            }
        }
    }
}

/// Check if an import_declaration node has a wildcard (asterisk) child.
fn has_wildcard_child(node: Node) -> bool {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "asterisk" || child.kind() == "wildcard" {
                return true;
            }
            // Also check recursively for nested asterisks
            if has_wildcard_child(child) {
                return true;
            }
        }
    }
    false
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

/// Collect identifiers for call resolution (from field_access, etc.)
fn collect_identifiers_for_call(source: &[u8], node: Node, parts: &mut Vec<String>) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" || child.kind() == "type_identifier" {
                parts.push(get_text(source, Some(child)));
            } else if child.kind() == "scoped_type_identifier" || child.kind() == "scoped_identifier" {
                collect_identifiers_for_call(source, child, parts);
            } else {
                collect_identifiers_for_call(source, child, parts);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a call-target qualified name for a CALLS edge.
fn build_call_target(
    file_path: &str,
    class_stack: &[String],
    callee: &str,
) -> String {
    if let Some(class_name) = class_stack.last() {
        format!("{file_path}::{class_name}.{callee}")
    } else {
        format!("{file_path}::{callee}")
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
    // 8. Imports (class import → REFERENCES)
    // ------------------------------------------------------------------

    #[test]
    fn test_class_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport com.foo.bar.MyClass\nclass App {}\n",
            "src/com/example/App.groovy",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge for class import");
        // The import path should contain the fully qualified class name
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("MyClass")),
            "Expected import target to contain MyClass: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // 9. Wildcard import → IMPORTS
    // ------------------------------------------------------------------

    #[test]
    fn test_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "import com.foo.bar.*\nclass App {}\n",
            "src/App.groovy",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
    }

    // ------------------------------------------------------------------
    // 10. Imported call uses qualified target
    // ------------------------------------------------------------------

    #[test]
    fn test_imported_call_uses_qualified_target_text() {
        let ctx = extract(
            r#"package com.example

import com.foo.Utils

class App {
    void doWork() {
        Utils.helper()
    }
}
"#,
            "src/com/example/App.groovy",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // Should have a qualified call target referencing the imported class
        assert!(
            targets.iter().any(|t| t.contains("Utils.helper")),
            "Expected qualified call target text for imported method: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // 11. Method calls
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
    // 12. Annotations
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
    // 13. Field variables
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
    // 14. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("class MyClass {\n}\n", "src/test.groovy");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 15. Nested classes
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
    // 16. Package declaration
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

    // ------------------------------------------------------------------
    // 17. Multiple class imports create REFERENCES edges
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_class_imports() {
        let ctx = extract(
            "import com.foo.bar.MyService\nimport com.foo.baz.Utils\nclass Foo {}\n",
            "src/test.groovy",
        );
        // Both are project-internal imports → REFERENCES edges
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.len() >= 2,
            "Expected at least 2 REFERENCES edges for two class imports, got {}",
            refs.len()
        );
    }

    // ------------------------------------------------------------------
    // 18. Static import creates REFERENCES edge
    // ------------------------------------------------------------------

    #[test]
    fn test_static_import_creates_references_edge() {
        let ctx = extract(
            "package com.example\n\nimport static com.foo.Utils.helper\nclass App {}\n",
            "src/com/example/App.groovy",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        // Should have a REFERENCES edge for the static import
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("Utils") || t.contains("helper")),
            "Expected REFERENCES target text containing import info: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // 19. Static wildcard import creates IMPORTS edge
    // ------------------------------------------------------------------

    #[test]
    fn test_static_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "package com.example\n\nimport static com.foo.Utils.*\nclass App {}\n",
            "src/com/example/App.groovy",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for static wildcard import");
    }

    // ------------------------------------------------------------------
    // 20. Alias import maps alias → qualified name
    // ------------------------------------------------------------------

    #[test]
    fn test_alias_import() {
        let ctx = extract(
            "import com.foo.Bar as Baz\nclass App {}\n",
            "src/App.groovy",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for alias import");
    }

    // ------------------------------------------------------------------
    // 21. Static import call resolves through imported_names
    // ------------------------------------------------------------------

    #[test]
    fn test_static_import_call_resolves() {
        let ctx = extract(
            r#"package com.example

import static com.foo.Utils.helper

class App {
    void doWork() {
        helper()
    }
}
"#,
            "src/com/example/App.groovy",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // The call should be resolved via imported_names
        assert!(
            targets.iter().any(|t| t.contains("com.foo.Utils.helper")),
            "Expected qualified call target for static imported method: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // 22. Non-imported call keeps bare name
    // ------------------------------------------------------------------

    #[test]
    fn test_non_imported_call_uses_bare_name() {
        let ctx = extract(
            "class Foo {\n  def bar() {\n    localMethod()\n  }\n}\n",
            "src/test.groovy",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("localMethod")),
            "Expected bare call target text for local method: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // 23. Helper: dump node kinds for debugging
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
                    "{}",
                    node.kind()
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
}