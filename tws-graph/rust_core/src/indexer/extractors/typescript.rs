//! TypeScript / JavaScript language extractor.
//!
//! Extracts symbols and relationships from TypeScript and JavaScript source
//! files (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`) using the
//! tree-sitter-typescript grammar.
//!
//! # Node kinds produced
//! - `class`: class declaration
//! - `function`: top-level function declaration
//! - `method`: method inside a class
//! - `interface`: interface declaration
//! - `enum`: enum declaration
//! - `enum_member`: enum member
//! - `variable`: const/let/var declaration
//! - `type_alias`: type alias declaration
//! - `field`: class property
//!
//! # Edge kinds produced
//! - `calls`: function/method calls
//! - `contains`: containment (file -> class -> method)
//! - `extends`: class inheritance
//! - `implements`: interface implementation
//! - `imports`: import statements
//! - `decorates`: decorator application
//! - `env_accesses`: `process.env.X` access

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// JS/TS built-in names -- filtered from edges
// ---------------------------------------------------------------------------

const JS_BUILTINS: &[&str] = &[
    "Object", "Array", "String", "Number", "Boolean", "Symbol", "BigInt",
    "Math", "Date", "RegExp", "Error", "JSON", "Promise", "Map", "Set",
    "WeakMap", "WeakSet", "Proxy", "Reflect", "eval", "isFinite", "isNaN",
    "parseFloat", "parseInt", "decodeURI", "decodeURIComponent",
    "encodeURI", "encodeURIComponent", "escape", "unescape", "console",
    "log", "warn", "error", "info", "debug", "trace", "toString",
    "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable",
    "toLocaleString", "push", "pop", "shift", "unshift", "splice", "slice",
    "concat", "join", "indexOf", "lastIndexOf", "forEach", "map", "filter",
    "reduce", "reduceRight", "some", "every", "find", "findIndex",
    "includes", "flat", "flatMap", "sort", "reverse", "fill", "copyWithin",
    "entries", "keys", "values", "document", "window", "navigator",
    "location", "history", "localStorage", "sessionStorage", "fetch",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "alert", "confirm",
    "prompt", "getElementById", "querySelector", "querySelectorAll",
    "addEventListener", "removeEventListener", "createElement",
    "appendChild", "removeChild", "getOwnPropertyDescriptor",
    "defineProperty", "freeze", "seal", "assign", "create", "fromEntries",
    "parse", "stringify",
];

fn is_js_builtin(name: &str) -> bool {
    JS_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// TypeScriptExtractor
// ---------------------------------------------------------------------------

pub struct TypeScriptExtractor;

impl Extractor for TypeScriptExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["ts", "tsx", "js", "jsx", "mjs", "cjs"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["typescript", "javascript"]
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

        let mut walker = TsWalker::new();
        walker.walk_program(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// TsWalker -- scope-aware tree walker for TypeScript/JavaScript
// ---------------------------------------------------------------------------

struct TsWalker {
    class_stack: Vec<String>,
}

impl TsWalker {
    fn new() -> Self {
        Self {
            class_stack: Vec::new(),
        }
    }

    fn current_class(&self) -> Option<&str> {
        self.class_stack.last().map(|s| s.as_str())
    }

    // ------------------------------------------------------------------
    // Program / top-level walking
    // ------------------------------------------------------------------

    fn walk_program(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let mut cursor = node.walk();
        if cursor.goto_first_child() {
            loop {
                let child = cursor.node();
                if child.is_named() {
                    self.walk_top_level(source, child, ctx, parent_id)?;
                }
                if !cursor.goto_next_sibling() {
                    break;
                }
            }
        }
        Ok(())
    }

    fn walk_top_level(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "class_declaration" | "abstract_class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "function_declaration" | "generator_function_declaration" => {
                self.extract_function(source, node, ctx, parent_id, NodeKind::Function, &[])?;
            }
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "enum_declaration" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "type_alias_declaration" => {
                self.extract_type_alias(source, node, ctx, parent_id)?;
            }
            "import_statement" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "export_statement" => {
                self.extract_export_statement(source, node, ctx, parent_id)?;
            }
            "lexical_declaration" | "variable_declaration" => {
                self.extract_variable_declaration(source, node, ctx, parent_id)?;
            }
            "expression_statement" => {
                self.walk_for_calls(source, node, ctx, parent_id)?;
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_top_level(source, child, ctx, parent_id)?;
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
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        let dec_names = collect_decorators(source, node);
        if !dec_names.is_empty() {
            if let Ok(json) = serde_json::to_string(&dec_names) {
                extra.insert("decorators".to_string(), json);
            }
        }

        if node.kind() == "abstract_class_declaration" {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Class heritage (extends / implements) — not a field-name child, iterate named children
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "class_heritage" {
                    for j in 0..child.named_child_count() {
                        if let Some(clause) = child.named_child(j) {
                            match clause.kind() {
                                "extends_clause" => {
                                    // extends_clause has value: (identifier) or value: (type_identifier)
                                    if let Some(val) = clause.child_by_field_name("value") {
                                        let ext_name = get_text(source, Some(val));
                                        if !ext_name.is_empty() && !is_js_builtin(&ext_name) {
                                            let target_qn = format!("{}::{}", ctx.file_path, ext_name);
                                            let target = hash_id(&ctx.file_path, &target_qn);
                                            ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&ext_name));
                                        }
                                    }
                                }
                                "implements_clause" => {
                                    for k in 0..clause.named_child_count() {
                                        if let Some(impl_) = clause.named_child(k) {
                                            let impl_name = get_text(source, Some(impl_));
                                            if !impl_name.is_empty() && !is_js_builtin(&impl_name) {
                                                let target_qn = format!("{}::{}", ctx.file_path, impl_name);
                                                let target = hash_id(&ctx.file_path, &target_qn);
                                                ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&impl_name));
                                            }
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }
        }

        // Decorates edges
        for dec_name in &dec_names {
            if !is_js_builtin(dec_name) {
                let target_qn = format!("{}::{}", ctx.file_path, dec_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&class_id, &target, EdgeKind::Decorates, line, Some(dec_name));
            }
        }

        // Push class scope and process body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_class_member(source, child, ctx, &class_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
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
            "method_definition" => {
                self.extract_method(source, node, ctx, parent_id)?;
            }
            "constructor_definition" => {
                self.extract_method(source, node, ctx, parent_id)?;
            }
            "public_field_definition" | "private_field_definition" | "field_definition" => {
                if let Some(name_node) = node.child_by_field_name("name") {
                    let name = get_text(source, Some(name_node));
                    if !name.is_empty() {
                        let field_id = ctx.add_node(NodeKind::Field, &name, &node, HashMap::new());
                        let line = node.start_position().row as u32 + 1;
                        ctx.add_edge(parent_id, &field_id, EdgeKind::Contains, line, None);
                    }
                }
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_class_member(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Function / method extraction
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        fn_kind: NodeKind,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }
        self.extract_function_impl(source, node, ctx, parent_id, fn_kind, decorators)
    }

    fn extract_method(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(());
        }
        let dec_names = collect_decorators(source, node);
        let func_id =
            self.extract_function_impl(source, node, ctx, parent_id, NodeKind::Method, &dec_names)?;

        // Decorates edges for method decorators
        let line = node.start_position().row as u32 + 1;
        for dec_name in &dec_names {
            if !is_js_builtin(dec_name) {
                let target_qn = format!("{}::{}", ctx.file_path, dec_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&func_id, &target, EdgeKind::Decorates, line, Some(dec_name));
            }
        }
        Ok(())
    }

    fn extract_function_impl(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        fn_kind: NodeKind,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        let func_id = ctx.add_node(fn_kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        // Push scope and process body
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_for_calls(source, body, ctx, &func_id)?;
        }

        ctx.pop_scope();
        Ok(func_id)
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    fn extract_interface(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(());
        }

        let interface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &interface_id, EdgeKind::Contains, line, None);

        // extends_type_clause is a named child of interface_declaration, not a field child
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "extends_type_clause" {
                    // extends_type_clause has type: (type_identifier) children
                    for j in 0..child.named_child_count() {
                        if let Some(ext) = child.named_child(j) {
                            let ext_name = get_text(source, Some(ext));
                            if !ext_name.is_empty() && !is_js_builtin(&ext_name) {
                                let target_qn = format!("{}::{}", ctx.file_path, ext_name);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(&interface_id, &target, EdgeKind::Extends, line, Some(&ext_name));
                            }
                        }
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    fn extract_enum(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(());
        }

        let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    // enum_body children are property_identifier nodes
                    if child.kind() == "property_identifier" {
                        let member_text = get_text(source, Some(child));
                        if !member_text.is_empty() {
                            let member_id = ctx.add_node(
                                NodeKind::EnumMember,
                                &member_text,
                                &child,
                                HashMap::new(),
                            );
                            let mline = child.start_position().row as u32 + 1;
                            ctx.add_edge(&enum_id, &member_id, EdgeKind::Contains, mline, None);
                        }
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Type alias extraction
    // ------------------------------------------------------------------

    fn extract_type_alias(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(());
        }

        let alias_id = ctx.add_node(NodeKind::TypeAlias, &name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &alias_id, EdgeKind::Contains, line, None);

        Ok(())
    }

    // ------------------------------------------------------------------
    // Variable declaration extraction
    // ------------------------------------------------------------------

    fn extract_variable_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // Collect variable names and values for processing
        let mut var_infos: Vec<(String, Node)> = Vec::new();
        let mut values_to_walk: Vec<Node> = Vec::new();
        let cur_parent = parent_id.to_string();

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "variable_declarator" {
                    if let Some(name_node) = child.child_by_field_name("name") {
                        let var_name = get_text(source, Some(name_node));
                        if !var_name.is_empty() {
                            var_infos.push((var_name, child));
                        }
                    }
                    if let Some(value) = child.child_by_field_name("value") {
                        values_to_walk.push(value);
                    }
                } else if child.kind() == "call_expression"
                    || child.kind() == "new_expression"
                {
                    values_to_walk.push(child);
                }
            }
        }

        // Create variable nodes first
        for (var_name, child) in &var_infos {
            let node_kind = if is_all_caps_or_const(var_name) {
                NodeKind::Constant
            } else {
                NodeKind::Variable
            };
            let var_id = ctx.add_node(node_kind, var_name, child, HashMap::new());
            let line = child.start_position().row as u32 + 1;
            ctx.add_edge(&cur_parent, &var_id, EdgeKind::Contains, line, None);
        }

        // Walk values for calls and process.env
        for value in &values_to_walk {
            self.walk_for_calls(source, *value, ctx, &cur_parent)?;
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

        if let Some(source_node) = node.child_by_field_name("source") {
            let module_name = get_text(source, Some(source_node));
            let clean = module_name.trim_matches(|c| c == '\'' || c == '"' || c == '`');
            if !clean.is_empty() {
                let target_qn = format!("{}::{}", ctx.file_path, clean);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(clean));
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Export statement
    // ------------------------------------------------------------------

    fn extract_export_statement(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "class_declaration"
                    | "abstract_class_declaration"
                    | "function_declaration"
                    | "interface_declaration"
                    | "enum_declaration"
                    | "type_alias_declaration"
                    | "lexical_declaration"
                    | "variable_declaration" => {
                        self.walk_top_level(source, child, ctx, parent_id)?;
                    }
                    "import_statement" => {
                        self.extract_import(source, child, ctx, parent_id)?;
                    }
                    _ => {
                        self.walk_top_level(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    fn walk_for_calls(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "new_expression" => {
                self.extract_new_expr(source, node, ctx, parent_id)?;
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "member_expression" => {
                self.check_process_env(source, node, ctx, parent_id);
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            // Declaration-like nodes that can appear in function bodies
            "lexical_declaration"
            | "variable_declaration" => {
                // Extract variables and walk values
                self.extract_variable_declaration(source, node, ctx, parent_id)?;
            }
            // Container-like nodes -- recurse into children
            "statement_block"
            | "expression_statement"
            | "return_statement"
            | "if_statement"
            | "for_statement"
            | "for_in_statement"
            | "while_statement"
            | "do_statement"
            | "switch_statement"
            | "switch_case"
            | "switch_default"
            | "try_statement"
            | "catch_clause"
            | "finally_clause"
            | "with_statement"
            | "labeled_statement"
            | "throw_statement"
            | "arrow_function"
            | "function_expression"
            | "generator_function"
            | "assignment_expression"
            | "augmented_assignment_expression"
            | "ternary_expression"
            | "binary_expression"
            | "unary_expression"
            | "update_expression"
            | "await_expression"
            | "yield_expression"
            | "spread_element"
            | "sequence_expression"
            | "parenthesized_expression"
            | "object"
            | "array"
            | "object_pattern"
            | "array_pattern"
            | "template_substitution"
            | "jsx_element"
            | "jsx_self_closing_element"
            | "jsx_expression"
            | "jsx_fragment"
            | "conditional_expression"
            | "subscript_expression"
            | "computed_property_name"
            | "pair"
            | "arguments"
            | "formal_parameters"
            | "optional_parameter"
            | "required_parameter"
            | "rest_parameter"
            | "template_string"
            | "string"
            | "regex"
            | "number"
            | "true"
            | "false"
            | "null"
            | "undefined"
            | "this"
            | "super" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call_expression"
                            || ck == "new_expression"
                            || ck == "member_expression"
                            || ck == "arrow_function"
                            || ck == "function_expression"
                        {
                            self.walk_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let func = node.child_by_field_name("function");
        let line = node.start_position().row as u32 + 1;

        match func {
            Some(f) => match f.kind() {
                "identifier" => {
                    let name = get_text(source, Some(f));
                    if !name.is_empty() && !is_js_builtin(&name) {
                        let target_qn = build_call_target(&ctx.file_path, &self.class_stack, &name, false);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                    }
                }
                "member_expression" => {
                    self.extract_member_call(source, f, ctx, parent_id, line)?;
                }
                "call_expression" => {
                    self.extract_call(source, f, ctx, parent_id)?;
                }
                _ => {}
            },
            None => {}
        }

        Ok(())
    }

    fn extract_new_expr(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let constructor = node.child_by_field_name("constructor");
        let line = node.start_position().row as u32 + 1;

        match constructor {
            Some(c) => match c.kind() {
                "identifier" => {
                    let name = get_text(source, Some(c));
                    if !name.is_empty() && !is_js_builtin(&name) {
                        let target_qn = format!("{}::{}", ctx.file_path, name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                    }
                }
                "member_expression" => {
                    self.extract_member_call(source, c, ctx, parent_id, line)?;
                }
                _ => {}
            },
            None => {}
        }

        Ok(())
    }

    fn extract_member_call(
        &self,
        source: &[u8],
        member_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
    ) -> anyhow::Result<()> {
        if let Some(obj) = member_node.child_by_field_name("object") {
            let obj_text = get_text(source, Some(obj));

            if let Some(prop) = member_node.child_by_field_name("property") {
                let prop_text = get_text(source, Some(prop));

                if !prop_text.is_empty() {
                    if obj_text == "this" || obj_text == "self" {
                        let target_qn = build_call_target(&ctx.file_path, &self.class_stack, &prop_text, true);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        let full_name = format!("{}.{}", obj_text, prop_text);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full_name));
                    } else if !is_js_builtin(&prop_text) {
                        let full_chain = resolve_member_chain(source, member_node);
                        let callee = full_chain.rsplitn(2, '.').next().unwrap_or(&full_chain);
                        let target_qn = format!("{}::{}", ctx.file_path, callee);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full_chain));
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // process.env detection
    // ------------------------------------------------------------------

    fn check_process_env(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        if let Some(prop) = node.child_by_field_name("property") {
            if let Some(obj) = node.child_by_field_name("object") {
                if obj.kind() == "member_expression" {
                    if let Some(inner_prop) = obj.child_by_field_name("property") {
                        if get_text(source, Some(inner_prop)) == "env" {
                            if let Some(inner_obj) = obj.child_by_field_name("object") {
                                if get_text(source, Some(inner_obj)) == "process" {
                                    let line = node.start_position().row as u32 + 1;
                                    let env_var = get_text(source, Some(prop));
                                    let full = format!("process.env.{}", env_var);
                                    ctx.add_edge(
                                        parent_id,
                                        parent_id,
                                        EdgeKind::EnvAccesses,
                                        line,
                                        Some(&full),
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

fn build_call_target(
    file_path: &str,
    class_stack: &[String],
    callee: &str,
    is_this_call: bool,
) -> String {
    if is_this_call {
        if let Some(class_name) = class_stack.last() {
            return format!("{file_path}::{class_name}.{callee}");
        }
    }
    format!("{file_path}::{callee}")
}

fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
    }
}

fn collect_decorators(source: &[u8], node: Node) -> Vec<String> {
    let mut names = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "decorator" {
                if let Some(inner) = child.named_child(0) {
                    let name = match inner.kind() {
                        "call_expression" => {
                            if let Some(func) = inner.child_by_field_name("function") {
                                get_text(source, Some(func))
                            } else {
                                String::new()
                            }
                        }
                        "member_expression" => resolve_member_chain(source, inner),
                        _ => get_text(source, Some(inner)),
                    };
                    if !name.is_empty() {
                        names.push(name);
                    }
                }
            }
        }
    }
    names
}

fn resolve_member_chain(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();

    match node.kind() {
        "identifier" => {
            return get_text(source, Some(node));
        }
        "member_expression" => {
            if let Some(prop) = node.child_by_field_name("property") {
                parts.push(get_text(source, Some(prop)));
            }
            let mut current = node.child_by_field_name("object");
            loop {
                match current {
                    Some(obj) => match obj.kind() {
                        "identifier" => {
                            parts.push(get_text(source, Some(obj)));
                            break;
                        }
                        "member_expression" => {
                            if let Some(p) = obj.child_by_field_name("property") {
                                parts.push(get_text(source, Some(p)));
                            }
                            current = obj.child_by_field_name("object");
                        }
                        "call_expression" | "new_expression" => {
                            let call_name = if let Some(f) = obj.child_by_field_name("function") {
                                format!("{}()", get_text(source, Some(f)))
                            } else if let Some(c) = obj.child_by_field_name("constructor") {
                                format!("new {}()", get_text(source, Some(c)))
                            } else {
                                String::from("?()")
                            };
                            parts.push(call_name);
                            break;
                        }
                        _ => break,
                    },
                    None => break,
                }
            }
        }
        _ => {
            return get_text(source, Some(node));
        }
    }

    parts.reverse();
    parts.join(".")
}

fn is_all_caps_or_const(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    name.chars()
        .all(|c| c.is_uppercase() || c == '_' || c.is_ascii_digit())
        && name.chars().any(|c| c.is_alphabetic())
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
        parser
            .set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into())
            .expect("set typescript language");
        let tree = parser.parse(source, None).expect("parse ts source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "typescript".to_string());
        TypeScriptExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn extract_js(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into())
            .expect("set typescript language");
        let tree = parser.parse(source, None).expect("parse js source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "javascript".to_string());
        TypeScriptExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(
        ctx: &ExtractionContext,
        kind: NodeKind,
    ) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

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

    fn node_kind_to_str(kind: NodeKind) -> &'static str {
        crate::indexer::context::node_kind_to_str(kind)
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class() {
        let ctx = extract("class MyClass { }\n", "src/test.ts");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_extends() {
        let ctx = extract("class Child extends Parent { }\n", "src/test.ts");
        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert_eq!(extends.len(), 1);
        assert_eq!(extends[0].target_text.as_deref().unwrap_or(""), "Parent");
    }

    #[test]
    fn test_extract_class_with_implements() {
        let ctx = extract("class MyClass implements MyInterface { }\n", "src/test.ts");
        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert_eq!(implements.len(), 1);
        assert_eq!(implements[0].target_text.as_deref().unwrap_or(""), "MyInterface");
    }

    #[test]
    fn test_extract_class_with_extends_and_implements() {
        let ctx = extract(
            "class Child extends Parent implements I1, I2 { }\n",
            "src/test.ts",
        );
        assert_eq!(find_edges(&ctx, EdgeKind::Extends).len(), 1);
        assert_eq!(find_edges(&ctx, EdgeKind::Implements).len(), 2);
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function() {
        let ctx = extract("function myFunc() { }\n", "src/test.ts");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "myFunc");
    }

    #[test]
    fn test_extract_method() {
        let ctx = extract("class MyClass {\n  myMethod() { }\n}\n", "src/test.ts");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "myMethod");
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface() {
        let ctx = extract("interface MyInterface { name: string; }\n", "src/test.ts");
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "MyInterface");
    }

    #[test]
    fn test_extract_interface_with_extends() {
        let ctx = extract("interface Child extends Parent { }\n", "src/test.ts");
        assert_eq!(find_edges(&ctx, EdgeKind::Extends).len(), 1);
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract("enum Color { Red, Green, Blue }\n", "src/test.ts");
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");

        let members = find_nodes(&ctx, NodeKind::EnumMember);
        assert_eq!(members.len(), 3);
        let names: Vec<&str> = members.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Red"));
        assert!(names.contains(&"Green"));
        assert!(names.contains(&"Blue"));
    }

    // ------------------------------------------------------------------
    // Type alias extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_type_alias() {
        let ctx = extract("type MyType = string | number;\n", "src/test.ts");
        let aliases = find_nodes(&ctx, NodeKind::TypeAlias);
        assert_eq!(aliases.len(), 1);
        assert_eq!(aliases[0].name, "MyType");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract("function foo() { bar(); }\n", "src/test.ts");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"bar"), "Expected 'bar' in calls: {:?}", targets);
    }

    #[test]
    fn test_extract_this_method_call() {
        let ctx = extract(
            "class MyClass {\n  methodA() { this.methodB(); }\n}\n",
            "src/test.ts",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty());
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("methodB")), "Expected methodB in: {:?}", targets);
    }

    #[test]
    fn test_extract_new_expression() {
        let ctx = extract("function foo() { new Bar(); }\n", "src/test.ts");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"Bar"), "Expected 'Bar' in: {:?}", targets);
    }

    #[test]
    fn test_filter_js_builtins() {
        let ctx = extract("function foo() { console.log('test'); }\n", "src/test.ts");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.iter().any(|t| *t == "log"), "console.log should be filtered");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_import() {
        let ctx = extract("import { foo } from './module';\n", "src/test.ts");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].target_text.as_deref().unwrap_or(""), "./module");
    }

    #[test]
    fn test_extract_default_import() {
        let ctx = extract("import foo from 'bar';\n", "src/test.ts");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1);
        assert_eq!(imports[0].target_text.as_deref().unwrap_or(""), "bar");
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_const_variable() {
        let ctx = extract("const MAX_SIZE = 100;\n", "src/test.ts");
        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert_eq!(constants.len(), 1);
        assert_eq!(constants[0].name, "MAX_SIZE");
    }

    #[test]
    fn test_extract_let_variable() {
        let ctx = extract("let myVar = 42;\n", "src/test.ts");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "myVar");
    }

    // ------------------------------------------------------------------
    // Decorator extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_class_decorator() {
        let ctx = extract("@Component({})\nclass MyClass { }\n", "src/test.ts");
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        assert!(!decorates.is_empty(), "Expected decorates edge");
    }

    // ------------------------------------------------------------------
    // Env access (process.env)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_process_env() {
        let ctx = extract(
            "function foo() { const val = process.env.DATABASE_URL; }\n",
            "src/test.ts",
        );
        let env_edges = find_edges(&ctx, EdgeKind::EnvAccesses);
        assert!(!env_edges.is_empty(), "Expected ENV_ACCESSES for process.env.DATABASE_URL");
        let targets: Vec<&str> = env_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("DATABASE_URL")), "Expected DATABASE_URL: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // File node
    // ------------------------------------------------------------------

    #[test]
    fn test_file_node_exists() {
        let ctx = extract("// empty\n", "src/test.ts");
        assert_eq!(find_nodes(&ctx, NodeKind::File).len(), 1);
    }

    // ------------------------------------------------------------------
    // Export handling
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_exported_class() {
        let ctx = extract("export class MyClass { }\n", "src/test.ts");
        assert_eq!(find_nodes(&ctx, NodeKind::Class).len(), 1);
    }

    #[test]
    fn test_extract_exported_function() {
        let ctx = extract("export function myFunc() { }\n", "src/test.ts");
        assert_eq!(find_nodes(&ctx, NodeKind::Function).len(), 1);
    }

    // ------------------------------------------------------------------
    // JavaScript file support
    // ------------------------------------------------------------------

    #[test]
    fn test_parse_javascript() {
        let ctx = extract_js("function hello() { console.log('hi'); }\n", "src/test.js");
        assert_eq!(find_nodes(&ctx, NodeKind::Function).len(), 1);
        assert_eq!(ctx.language, "javascript");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.ts");
        assert_eq!(find_nodes(&ctx, NodeKind::File).len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("// just a comment\n", "src/comments.ts");
        assert_eq!(find_nodes(&ctx, NodeKind::File).len(), 1);
    }

    #[test]
    fn test_contains_edges() {
        let ctx = extract("class MyClass {\n  myMethod() { }\n}\n", "src/test.ts");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(contains.len() >= 2, "Expected >= 2 CONTAINS edges, got {}", contains.len());
    }
}
