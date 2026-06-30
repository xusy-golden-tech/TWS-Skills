//! PHP language extractor.
//!
//! Extracts symbols and relationships from PHP source files (`.php`)
//! using the tree-sitter-php grammar.
//!
//! # Node kinds produced
//! - `class`: class_declaration
//! - `interface`: interface_declaration
//! - `trait`: trait_declaration
//! - `namespace`: namespace_definition
//! - `function`: function_definition (top-level)
//! - `method`: method_declaration
//! - `property`: property_declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function_call_expression, member_call_expression
//! - `contains`: containment (file → class → method)
//! - `imports`: namespace_use_declaration, require/include expressions
//! - `implements`: trait use_declaration inside class/trait
//! - `decorates`: PHP 8 attribute_list
//! - `type_ref`: named_type annotations

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// PhpExtractor
// ---------------------------------------------------------------------------

pub struct PhpExtractor;

impl Extractor for PhpExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["php"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["php"]
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
                .unwrap_or("index")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        let mut walker = Walker::new();
        walker.walk_node(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker
// ---------------------------------------------------------------------------

struct Walker {
    /// Maps imported symbol name → fully qualified name.
    /// E.g. "Baz" → "Foo\\Bar\\Baz", "Alias" → "Foo\\Bar\\Baz"
    imported_names: HashMap<String, String>,
    /// Current namespace (from `namespace Foo\Bar;` declaration).
    current_namespace: String,
}

impl Walker {
    fn new() -> Self {
        Self {
            imported_names: HashMap::new(),
            current_namespace: String::new(),
        }
    }

    /// Main recursive dispatcher. Returns `true` if the node was handled and
    /// children should NOT be recursed by the caller.
    fn walk_node(
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
            "trait_declaration" => {
                self.extract_trait(source, node, ctx, parent_id)?;
            }
            "namespace_definition" => {
                self.extract_namespace(source, node, ctx, parent_id)?;
            }
            "function_definition" => {
                self.extract_function(source, node, ctx, parent_id, NodeKind::Function)?;
            }
            "method_declaration" => {
                self.extract_method(source, node, ctx, parent_id)?;
            }
            "property_declaration" => {
                self.extract_property(source, node, ctx, parent_id)?;
            }
            "namespace_use_declaration" => {
                self.extract_use_import(source, node, ctx, parent_id)?;
            }
            "use_declaration" => {
                self.extract_trait_use(source, node, ctx, parent_id)?;
            }
            "attribute_list" => {
                self.extract_attributes(source, node, ctx, parent_id)?;
            }
            "named_type" => {
                self.extract_type_ref(source, node, ctx, parent_id)?;
            }
            "function_call_expression" | "member_call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
            }
            k if k == "require_once_expression"
                || k == "require_expression"
                || k == "include_expression"
                || k == "include_once_expression" =>
            {
                self.extract_require_include(source, node, ctx, parent_id)?;
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_node(source, child, ctx, parent_id)?;
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
        let name = find_child_text(source, node, "name");
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Visibility
        if let Some(vis) = find_child_by_kind(node, "visibility_modifier") {
            extra.insert("visibility".to_string(), get_text(source, Some(vis)));
        }
        // Abstract
        if find_child_by_kind(node, "abstract_modifier").is_some() {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Inheritance (extends)
        if let Some(base_clause) = find_child_by_kind(node, "base_clause") {
            for i in 0..base_clause.named_child_count() {
                if let Some(child) = base_clause.named_child(i) {
                    let base_name = get_text(source, Some(child));
                    if !base_name.is_empty()
                        && child.kind() != "extends"
                        && child.kind() != "implements"
                        && child.kind() != "class_interface_clause"
                    {
                        let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            &class_id,
                            &target,
                            EdgeKind::Extends,
                            line,
                            Some(&base_name),
                        );
                    }
                }
            }
        }

        // Interface implementation (implements clause)
        if let Some(iface_clause) = find_child_by_kind(node, "class_interface_clause") {
            for i in 0..iface_clause.named_child_count() {
                if let Some(child) = iface_clause.named_child(i) {
                    let iface_name = get_text(source, Some(child));
                    if !iface_name.is_empty() && child.kind() != "implements" {
                        let target_qn = build_qualified_target(&ctx.file_path, &iface_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            &class_id,
                            &target,
                            EdgeKind::Implements,
                            line,
                            Some(&iface_name),
                        );
                    }
                }
            }
        }

        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        // Walk all named children (includes attribute_list, declaration_list, etc.)
        self.walk_all_children(source, node, ctx, &class_id)?;

        ctx.pop_scope();
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
        let name = find_child_text(source, node, "name");
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let line = node.start_position().row as u32 + 1;
        let iface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &iface_id, EdgeKind::Contains, line, None);

        // Interface extends (extends clause)
        if let Some(base_clause) = find_child_by_kind(node, "base_clause") {
            for i in 0..base_clause.named_child_count() {
                if let Some(child) = base_clause.named_child(i) {
                    let base_name = get_text(source, Some(child));
                    if !base_name.is_empty()
                        && child.kind() != "extends"
                    {
                        let target_qn = build_qualified_target(&ctx.file_path, &base_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            &iface_id,
                            &target,
                            EdgeKind::Extends,
                            line,
                            Some(&base_name),
                        );
                    }
                }
            }
        }

        ctx.push_scope_with_kind(&name, "interface");
        ctx.push_scope_node(&iface_id);

        self.walk_all_children(source, node, ctx, &iface_id)?;

        ctx.pop_scope();
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
        let name = find_child_text(source, node, "name");
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let line = node.start_position().row as u32 + 1;
        let trait_id = ctx.add_node(NodeKind::Trait, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &trait_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "trait");
        ctx.push_scope_node(&trait_id);

        self.walk_all_children(source, node, ctx, &trait_id)?;

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Namespace extraction
    // ------------------------------------------------------------------

    fn extract_namespace(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // The namespace name is in a child node of kind "namespace_name"
        let ns_name_node = find_child_by_kind(node, "namespace_name");
        let ns_name = get_text(source, ns_name_node);
        if ns_name.is_empty() {
            // Fallback: try "name" child
            let name_node = find_child_by_kind(node, "name");
            let ns_name = get_text(source, name_node);
            if ns_name.is_empty() {
                for i in 0..node.named_child_count() {
                    if let Some(c) = node.named_child(i) {
                        self.walk_node(source, c, ctx, parent_id)?;
                    }
                }
                return Ok(());
            }
            let line = node.start_position().row as u32 + 1;
            let ns_id = ctx.add_node(NodeKind::Namespace, &ns_name, &node, HashMap::new());
            ctx.add_edge(parent_id, &ns_id, EdgeKind::Contains, line, None);

            ctx.push_scope_with_kind(&ns_name, "namespace");
            ctx.push_scope_node(&ns_id);

            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    self.walk_node(source, child, ctx, &ns_id)?;
                }
            }

            ctx.pop_scope();
            return Ok(());
        }

        let line = node.start_position().row as u32 + 1;
        let ns_id = ctx.add_node(NodeKind::Namespace, &ns_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &ns_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&ns_name, "namespace");
        ctx.push_scope_node(&ns_id);

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_node(source, child, ctx, &ns_id)?;
            }
        }

        ctx.pop_scope();
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
        fn_kind: NodeKind,
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "name");
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Return type
        if let Some(rt) = find_child_by_kind(node, "return_type") {
            extra.insert("return_type".to_string(), get_text(source, Some(rt)));
        }

        let func_id = ctx.add_node(fn_kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        // Walk all children for calls and type refs (including parameters, return type)
        self.walk_all_children(source, node, ctx, &func_id)?;

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Method extraction
    // ------------------------------------------------------------------

    fn extract_method(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = find_child_text(source, node, "name");
        if name.is_empty() {
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Visibility
        if let Some(vis) = find_child_by_kind(node, "visibility_modifier") {
            extra.insert("visibility".to_string(), get_text(source, Some(vis)));
        }
        // Static
        if find_child_by_kind(node, "static_modifier").is_some() {
            extra.insert("decorators".to_string(), "[\"static\"]".to_string());
        }

        let method_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "method");
        ctx.push_scope_node(&method_id);

        self.walk_all_children(source, node, ctx, &method_id)?;

        ctx.pop_scope();
        Ok(())
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
        // property_declaration has property_element → variable_name → name
        let prop_elem = find_child_by_kind(node, "property_element");
        let var_name_node = match prop_elem {
            Some(pe) => find_child_by_kind(pe, "variable_name"),
            None => None,
        };
        let name_node = match var_name_node {
            Some(vn) => find_child_by_kind(vn, "name"),
            None => None,
        };
        let prop_name = get_text(source, name_node);
        if prop_name.is_empty() {
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if let Some(vis) = find_child_by_kind(node, "visibility_modifier") {
            extra.insert("visibility".to_string(), get_text(source, Some(vis)));
        }
        if find_child_by_kind(node, "static_modifier").is_some() {
            extra.insert("decorators".to_string(), "[\"static\"]".to_string());
        }

        let prop_id = ctx.add_node(NodeKind::Property, &prop_name, &node, extra);
        ctx.add_edge(parent_id, &prop_id, EdgeKind::Contains, line, None);

        Ok(())
    }

    // ------------------------------------------------------------------
    // Use import extraction (use Namespace\Class)
    // ------------------------------------------------------------------

    fn extract_use_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Detect use_type: "function" or "const" (class import is the default).
        // In tree-sitter-php, 'function' and 'const' are unnamed tokens in the
        // namespace_use_declaration node. We scan the source text for the keyword
        // between "use" and the first namespace_use_clause.
        let node_text = get_text(source, Some(node));
        let use_type_text = if node_text.contains("function ") {
            "function".to_string()
        } else if node_text.contains("const ") {
            "const".to_string()
        } else {
            String::new()
        };

        // namespace_use_declaration → namespace_use_clause → qualified_name / name
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "namespace_use_clause" {
                    let qn = find_child_by_kind(child, "qualified_name");
                    let full_name = get_text(source, qn);
                    if !full_name.is_empty() {
                        self.add_php_import(
                            source, child, &full_name, &use_type_text,
                            ctx, parent_id, line,
                        )?;
                    } else {
                        let name_node = find_child_by_kind(child, "name");
                        let simple_name = get_text(source, name_node);
                        if !simple_name.is_empty() {
                            self.add_php_import(
                                source, child, &simple_name, &use_type_text,
                                ctx, parent_id, line,
                            )?;
                        }
                    }
                }
            }
        }

        Ok(())
    }

    /// Add a single PHP import (class/function/const), creating IMPORTS and
    /// REFERENCES edges, and populating imported_names.
    fn add_php_import(
        &mut self,
        source: &[u8],
        clause_node: Node,
        full_name: &str,
        use_type: &str,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
    ) -> anyhow::Result<()> {
        // Extract alias if present: `use Foo\Bar as Baz` → alias = "Baz"
        // In tree-sitter-php, `as` alias is a `name` node with field name "alias"
        let alias_node = clause_node.child_by_field_name("alias");
        let alias_name = get_text(source, alias_node);

        // Determine local name (what the symbol is called locally)
        let local_name = if !alias_name.is_empty() {
            alias_name.clone()
        } else {
            // Last segment after last backslash
            full_name
                .rsplit('\\')
                .next()
                .unwrap_or(full_name)
                .to_string()
        };

        // Determine the canonical symbol name (original name, not alias)
        // Class:   Foo\Bar\Baz → symbol = "Baz"
        // Func:    Foo\Bar\helper → symbol = "helper"
        // Const:   Foo\Bar\MY_CONST → symbol = "MY_CONST"
        let symbol_name = full_name
            .rsplit('\\')
            .next()
            .unwrap_or(full_name)
            .to_string();

        // Determine module_part for REFERENCES edge
        // Class:   Foo\Bar\Baz → module = "Foo\Bar\Baz" (the full FQN)
        // Func:    Foo\Bar\helper → module = "Foo\Bar" (prefix before function name)
        // Const:   Foo\Bar\MY_CONST → module = "Foo\Bar" (prefix before const name)
        let module_part = if use_type == "function" || use_type == "const" {
            // Function/const import: strip the last segment to get module path
            if let Some(last_bs) = full_name.rfind('\\') {
                full_name[..last_bs].to_string()
            } else {
                String::new()
            }
        } else {
            // Class import: the full name IS the module
            full_name.to_string()
        };

        // The full qualified reference text (used for both REFERENCES edge and
        // call/type_ref lookups). Format: "module_part::symbol_name"
        let ref_target_text = if module_part.is_empty() {
            format!("{}::{}", full_name, symbol_name)
        } else {
            format!("{}::{}", module_part, symbol_name)
        };

        // Store in imported_names: local_name → full qualified reference text
        // This ensures aliased imports (e.g. `use Foo\Bar\Baz as Alias`)
        // still produce the correct qualified reference to the original symbol.
        self.imported_names
            .insert(local_name.clone(), ref_target_text.clone());

        // Create IMPORTS edge
        let target_qn = build_qualified_target(&ctx.file_path, full_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(
            parent_id,
            &target,
            EdgeKind::Imports,
            line,
            Some(full_name),
        );

        // Create REFERENCES edge with :: separator for resolver parsing
        let ref_target_qn = build_qualified_target(&ctx.file_path, &ref_target_text);
        let ref_target = hash_id(&ctx.file_path, &ref_target_qn);
        ctx.add_edge(
            parent_id,
            &ref_target,
            EdgeKind::References,
            line,
            Some(&ref_target_text),
        );

        Ok(())
    }

    // ------------------------------------------------------------------
    // Trait use extraction (use TraitName inside class body)
    // ------------------------------------------------------------------

    fn extract_trait_use(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // use_declaration has name children for trait names
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "name" {
                    let trait_name = get_text(source, Some(child));
                    if !trait_name.is_empty() {
                        let target_qn = build_qualified_target(&ctx.file_path, &trait_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Implements,
                            line,
                            Some(&trait_name),
                        );
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // PHP 8 Attribute extraction
    // ------------------------------------------------------------------

    fn extract_attributes(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // attribute_list → attribute_group → attribute → name
        for i in 0..node.named_child_count() {
            if let Some(group) = node.named_child(i) {
                if group.kind() == "attribute_group" {
                    let attr_node = find_child_by_kind(group, "attribute");
                    if let Some(attr) = attr_node {
                        let attr_name = find_child_text(source, attr, "name");
                        if !attr_name.is_empty() {
                            // Use a synthetic ID for the attribute target
                            let target_qn = build_qualified_target(&ctx.file_path, &attr_name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(
                                parent_id,
                                &target,
                                EdgeKind::Decorates,
                                line,
                                Some(&attr_name),
                            );
                        }
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Type reference extraction (type hints)
    // ------------------------------------------------------------------

    fn extract_type_ref(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // named_type has name or qualified_name children
        let type_name_node = find_child_by_kind(node, "qualified_name")
            .or_else(|| find_child_by_kind(node, "name"));
        let type_name = get_text(source, type_name_node);
        if !type_name.is_empty() {
            // Check if this type is imported — if so, use qualified target_text
            let target_text = if let Some(qualified) = self.imported_names.get(&type_name) {
                qualified.clone()
            } else {
                type_name.clone()
            };
            let target_qn = build_qualified_target(&ctx.file_path, &target_text);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(
                parent_id,
                &target,
                EdgeKind::TypeRef,
                line,
                Some(&target_text),
            );
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
        let kind = node.kind();

        if kind == "member_call_expression" {
            // member_call_expression: variable_name, name, arguments
            let method_name_node = find_child_by_kind(node, "name");
            let callee_name = get_text(source, method_name_node);
            if !callee_name.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
            }
        } else {
            // function_call_expression
            // Check for member_access_expression (object->method) first
            let mae = find_child_by_kind(node, "member_access_expression");
            if let Some(mae_node) = mae {
                let method_name_node = find_child_by_kind(mae_node, "name");
                let callee_name = get_text(source, method_name_node);
                if !callee_name.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
                }
            } else {
                // Simple function call: name or qualified_name
                let qn = find_child_by_kind(node, "qualified_name");
                let callee_name = get_text(source, qn);
                if !callee_name.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
                } else {
                    let name_node = find_child_by_kind(node, "name");
                    let simple_name = get_text(source, name_node);
                    if !simple_name.is_empty() && simple_name != "argument_list" {
                        // Check if this function is imported — use qualified target_text
                        let target_text = if let Some(qualified) = self.imported_names.get(&simple_name) {
                            qualified.clone()
                        } else {
                            simple_name.clone()
                        };
                        let target_qn = build_qualified_target(&ctx.file_path, &target_text);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Calls,
                            line,
                            Some(&target_text),
                        );
                    }
                }
            }
        }

        // Walk children for nested calls
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let ck = child.kind();
                if ck == "function_call_expression"
                    || ck == "member_call_expression"
                    || ck == "named_type"
                {
                    self.walk_node(source, child, ctx, parent_id)?;
                }
                // Skip argument_list children to avoid recursion, but process any calls inside
                if ck == "arguments" || ck == "argument_list" {
                    for j in 0..child.named_child_count() {
                        if let Some(inner) = child.named_child(j) {
                            let ik = inner.kind();
                            if ik == "function_call_expression"
                                || ik == "member_call_expression"
                            {
                                self.walk_node(source, inner, ctx, parent_id)?;
                            }
                        }
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Helper: walk all named children of a node
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
                self.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Require/include extraction
    // ------------------------------------------------------------------

    fn extract_require_include(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // require_once_expression, require_expression, include_expression, include_once_expression
        let str_node = find_child_by_kind(node, "string");
        if let Some(sn) = str_node {
            let text = get_text(source, Some(sn));
            let module_name = text.trim_matches(|c| c == '"' || c == '\'');
            if !module_name.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, module_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(
                    parent_id,
                    &target,
                    EdgeKind::Imports,
                    line,
                    Some(module_name),
                );
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Build a target qualified name for cross-reference edges.
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

/// Find the first direct named child with the given kind.
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

/// Get the text of the first direct named child with the given kind.
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

    /// Parse PHP source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_php::LANGUAGE_PHP.into())
            .expect("set php language");
        let tree = parser.parse(source, None).expect("parse php source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "php".to_string());
        PhpExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes(
        ctx: &ExtractionContext,
        kind: NodeKind,
    ) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    /// Helper: find edges of a given kind.
    fn find_edges(
        ctx: &ExtractionContext,
        kind: EdgeKind,
    ) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result
            .edges
            .iter()
            .filter(|e| e.kind == kind_str)
            .collect()
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class() {
        let ctx = extract("<?php\nclass MyClass {}\n", "src/test.php");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_extends() {
        let ctx = extract("<?php\nclass Child extends BaseClass {}\n", "src/test.php");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Child");

        let extends_edges = find_edges(&ctx, EdgeKind::Extends);
        assert_eq!(extends_edges.len(), 1);
        assert_eq!(
            extends_edges[0].target_text.as_deref().unwrap_or(""),
            "BaseClass"
        );
    }

    #[test]
    fn test_extract_class_with_implements() {
        let ctx = extract(
            "<?php\nclass MyClass implements MyInterface {}\n",
            "src/test.php",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);

        let impl_edges = find_edges(&ctx, EdgeKind::Implements);
        assert!(
            !impl_edges.is_empty(),
            "Expected IMPLEMENTS edge for interface"
        );
        let targets: Vec<&str> = impl_edges
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"MyInterface"), "Expected MyInterface in {:?}", targets);
    }

    #[test]
    fn test_class_contains_edge() {
        let ctx = extract("<?php\nclass MyClass {}\n", "src/test.php");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface() {
        let ctx = extract("<?php\ninterface MyInterface {}\n", "src/test.php");
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "MyInterface");
    }

    // ------------------------------------------------------------------
    // Trait extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_trait() {
        let ctx = extract("<?php\ntrait MyTrait {}\n", "src/test.php");
        let traits = find_nodes(&ctx, NodeKind::Trait);
        assert_eq!(traits.len(), 1);
        assert_eq!(traits[0].name, "MyTrait");
    }

    // ------------------------------------------------------------------
    // Namespace extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_namespace() {
        let ctx = extract(
            "<?php\nnamespace App\\Models;\n\nclass User {}\n",
            "src/test.php",
        );
        let namespaces = find_nodes(&ctx, NodeKind::Namespace);
        assert_eq!(namespaces.len(), 1);
        // Namespace name should contain App\Models
        let ns_name = &namespaces[0].name;
        assert!(ns_name.contains("App"), "Expected namespace containing 'App', got: {}", ns_name);
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function() {
        let ctx = extract(
            "<?php\nfunction myFunc() {\n    return 42;\n}\n",
            "src/test.php",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "myFunc");
    }

    // ------------------------------------------------------------------
    // Method extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_method() {
        let ctx = extract(
            "<?php\nclass MyClass {\n    public function myMethod() {}\n}\n",
            "src/test.php",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "myMethod");
    }

    // ------------------------------------------------------------------
    // Property extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_property() {
        let ctx = extract(
            "<?php\nclass MyClass {\n    public $name;\n}\n",
            "src/test.php",
        );
        let properties = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(properties.len(), 1);
        assert_eq!(properties[0].name, "name");
    }

    // ------------------------------------------------------------------
    // Use import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_use_import() {
        let ctx = extract(
            "<?php\nuse App\\Services\\UserService;\n",
            "src/test.php",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected imports edge for use statement");
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("UserService")),
            "Expected UserService in targets: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Trait use extraction (implements edge)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_trait_use() {
        let ctx = extract(
            "<?php\nclass MyClass {\n    use MyTrait;\n}\n",
            "src/test.php",
        );
        let implements = find_edges(&ctx, EdgeKind::Implements);
        let targets: Vec<&str> = implements
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"MyTrait"),
            "Expected MyTrait in implements: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // PHP 8 Attribute extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_php8_attribute() {
        let ctx = extract(
            "<?php\n#[Route('/api')]\nclass ApiController {}\n",
            "src/test.php",
        );
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> = decorates
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Route")),
            "Expected Route attribute in decorates: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Function call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract(
            "<?php\nfunction foo() {\n    bar();\n}\n",
            "src/test.php",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"bar"),
            "Expected 'bar' in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_method_call() {
        let ctx = extract(
            r#"<?php
function foo() {
    $obj->method();
}
"#,
            "src/test.php",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"method"),
            "Expected 'method' in call targets: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Require/include extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_require() {
        let ctx = extract(
            "<?php\nrequire 'config.php';\n",
            "src/test.php",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"config.php"),
            "Expected 'config.php' in imports: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Type reference extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_type_ref() {
        let ctx = extract(
            r#"<?php
function foo(User $user): User {
    return $user;
}
"#,
            "src/test.php",
        );
        let type_refs = find_edges(&ctx, EdgeKind::TypeRef);
        let targets: Vec<&str> = type_refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| *t == "User"),
            "Expected User type ref in: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // File node
    // ------------------------------------------------------------------

    #[test]
    fn test_file_node_exists() {
        let ctx = extract("<?php\n", "src/test.php");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.php");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_php_tag_only() {
        let ctx = extract("<?php\n", "src/test.php");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            r#"<?php
namespace App\Models;

use Illuminate\Database\Eloquent\Model;

#[SoftDeletes]
class User extends Model
{
    use HasFactory;

    public string $name;

    public function posts()
    {
        return $this->hasMany(Post::class);
    }
}

function helper(): string
{
    return config('app.name');
}
"#,
            "src/User.php",
        );
        // Verify it doesn't crash and produces nodes
        assert!(!ctx.result.nodes.is_empty());

        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1, "Expected 1 class");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1, "Expected 1 method");

        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1, "Expected 1 function");

        let namespaces = find_nodes(&ctx, NodeKind::Namespace);
        assert_eq!(namespaces.len(), 1, "Expected 1 namespace");
    }

    // ------------------------------------------------------------------
    // Cross-file import resolution tests (Stage 7: PHP)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_import_creates_references() {
        let ctx = extract(
            "<?php\nuse Foo\\Bar\\Baz;\n",
            "src/test.php",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for class import");
        let targets: Vec<&str> = refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\Baz::Baz")),
            "Expected Foo\\Bar\\Baz::Baz in REFERENCES targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_aliased_import_creates_references() {
        let ctx = extract(
            "<?php\nuse Foo\\Bar\\Baz as Alias;\n",
            "src/test.php",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for aliased import");
        let targets: Vec<&str> = refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\Baz::Baz")),
            "Expected Foo\\Bar\\Baz::Baz in REFERENCES targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_function_import_creates_references() {
        let ctx = extract(
            "<?php\nuse function Foo\\Bar\\helper;\n",
            "src/test.php",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for function import");
        let targets: Vec<&str> = refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar::helper")),
            "Expected Foo\\Bar::helper in REFERENCES targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_const_import_creates_references() {
        let ctx = extract(
            "<?php\nuse const Foo\\Bar\\MY_CONST;\n",
            "src/test.php",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for const import");
        let targets: Vec<&str> = refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar::MY_CONST")),
            "Expected Foo\\Bar::MY_CONST in REFERENCES targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_multiple_imports_references() {
        let ctx = extract(
            "<?php\nuse Foo\\Bar\\Baz;\nuse Foo\\Bar\\Qux;\n",
            "src/test.php",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(refs.len() >= 2, "Expected at least 2 REFERENCES edges, got {}", refs.len());
        let targets: Vec<&str> = refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\Baz::Baz")),
            "Expected Baz REF: {:?}", targets
        );
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\Qux::Qux")),
            "Expected Qux REF: {:?}", targets
        );
    }

    #[test]
    fn test_extract_import_preserves_imports_edge() {
        let ctx = extract(
            "<?php\nuse Foo\\Bar\\Baz;\n",
            "src/test.php",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\Baz")),
            "Expected Foo\\Bar\\Baz in IMPORTS targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_imported_call_qualified() {
        let ctx = extract(
            r#"<?php
use function Foo\Bar\helper;

function test(): void {
    helper();
}
"#,
            "src/test.php",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar::helper")),
            "Expected qualified Foo\\Bar::helper in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_non_imported_call_bare() {
        let ctx = extract(
            "<?php\nfunction test(): void {\n    localFunc();\n}\n",
            "src/test.php",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"localFunc"),
            "Expected bare 'localFunc' in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_imported_type_ref_qualified() {
        let ctx = extract(
            r#"<?php
use Foo\Bar\User;

function process(User $user): User {
    return $user;
}
"#,
            "src/test.php",
        );
        let type_refs = find_edges(&ctx, EdgeKind::TypeRef);
        let targets: Vec<&str> = type_refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar\\User::User")),
            "Expected Foo\\Bar\\User::User in type refs: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_non_imported_type_ref_bare() {
        let ctx = extract(
            r"<?php
function test(LocalClass $x): void {}
",
            "src/test.php",
        );
        let type_refs = find_edges(&ctx, EdgeKind::TypeRef);
        let targets: Vec<&str> = type_refs
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| *t == "LocalClass"),
            "Expected bare 'LocalClass' in type refs: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_aliased_function_import_qualified() {
        let ctx = extract(
            r#"<?php
use function Foo\Bar\helper as h;

function test(): void {
    h();
}
"#,
            "src/test.php",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("Foo\\Bar::helper")),
            "Expected aliased qualified Foo\\Bar::helper in call targets: {:?}",
            targets
        );
    }
}
