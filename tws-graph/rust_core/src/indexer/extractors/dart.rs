//! Dart language extractor.
//!
//! Extracts symbols and relationships from Dart source files (`.dart`)
//! using the tree-sitter-dart grammar.
//!
//! # Node kinds produced
//! - `class`: class_declaration
//! - `function`: top-level function_declaration / function_signature
//! - `method`: method_declaration / method_signature / constructor_signature
//! - `variable`: variable_declaration
//! - `interface`: abstract class
//! - `enum`: enum_declaration
//!
//! # Edge kinds produced
//! - `calls`: call_expression
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import_specification / import_or_export / library_import
//! - `extends`: extends clause
//! - `implements`: implements / mixins / with clauses
//! - `decorates`: annotation applications
//! - `references`: cross-file symbol references from import declarations
//!
//! # Cross-file resolution (v7.3.0)
//! - `import '...' show X, Y` → REFERENCES edges for X, Y + imported_names
//! - `import '...' hide X` → REFERENCES edge for module + imported_names
//! - `import '...' as prefix` → REFERENCES edge with prefix + imported_names
//! - `import 'dart:core'` → IMPORTS only (SDK external)
//! - Call qualification via imported_names

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// Dart core library identifiers -- filtered from type references.
const DART_BUILTINS: &[&str] = &[
    "Object", "String", "int", "double", "num", "bool", "List", "Map",
    "Set", "Iterable", "Future", "Stream", "void", "dynamic", "Function",
    "Null", "Never", "Type", "Symbol", "Runes", "Duration", "DateTime",
    "RegExp", "StackTrace", "Stopwatch", "Comparator", "Enum",
    "print", "toString", "runtimeType", "hashCode", "noSuchMethod",
];

fn is_dart_builtin(name: &str) -> bool {
    DART_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// DartExtractor
// ---------------------------------------------------------------------------

pub struct DartExtractor;

impl Extractor for DartExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["dart"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["dart"]
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
        walker.walk_file(source, root, ctx, &file_id);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker with import tracking
// ---------------------------------------------------------------------------

struct Walker {
    class_stack: Vec<String>,
    /// Map from imported simple name to fully qualified reference.
    /// e.g. `import 'package:foo/bar.dart' show Baz;`
    ///   → `"Baz" → "package:foo/bar.dart::Baz"`
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

    /// Qualify a call target name using imported_names.
    /// Returns the qualified name if the simple name is known as an import,
    /// otherwise returns the original name.
    fn qualify_call_target(&self, name: &str) -> String {
        self.imported_names
            .get(name)
            .cloned()
            .unwrap_or_else(|| name.to_string())
    }

    /// Qualify a type name using imported_names.
    fn qualify_type(&self, name: &str) -> String {
        self.qualify_call_target(name)
    }

    // ------------------------------------------------------------------
    // File-level walking
    // ------------------------------------------------------------------

    fn walk_file(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_top_level(source, child, ctx, parent_id);
            }
        }
    }

    fn walk_top_level(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        match node.kind() {
            // Class-related
            "class_declaration" | "mixin_declaration" => {
                self.extract_class(source, node, ctx, parent_id);
            }
            // Top-level function
            "function_declaration" | "function_signature" => {
                self.extract_function(source, node, ctx, parent_id);
            }
            // Method inside class (shouldn't appear at top level but handle gracefully)
            "method_declaration" | "method_signature" | "constructor_signature"
            | "factory_constructor_signature" | "constant_constructor_signature"
            | "redirecting_factory_constructor_signature" => {
                self.extract_method(source, node, ctx, parent_id);
            }
            // Getter/setter
            "getter_declaration" | "setter_declaration" => {
                self.extract_method(source, node, ctx, parent_id);
            }
            // Variables
            "variable_declaration" | "top_level_variable_declaration"
            | "local_variable_declaration" => {
                self.extract_variable(source, node, ctx, parent_id);
            }
            "enum_declaration" => {
                self.extract_enum(source, node, ctx, parent_id);
            }
            // Imports
            "import_specification" | "import_or_export" | "library_import"
            | "library_export" | "export" | "part" | "part_directive" => {
                self.extract_import(source, node, ctx, parent_id);
            }
            // Calls at top level (e.g., main function body)
            "call_expression" | "constructor_invocation" | "new_expression" => {
                self.extract_call(source, node, ctx, parent_id);
            }
            // Inheritance
            "superclass" => {
                self.extract_extends(source, node, ctx, parent_id);
            }
            "interfaces" | "mixins" | "with" | "on" => {
                self.extract_implements(source, node, ctx, parent_id);
            }
            // Annotations
            "annotation" => {
                self.extract_annotation(source, node, ctx, parent_id);
            }
            // Recurse into structural nodes
            _ => {
                self.walk_node(source, node, ctx, parent_id);
            }
        }
    }

    fn walk_node(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    // Class-related
                    "class_declaration" | "mixin_declaration" => {
                        self.extract_class(source, child, ctx, parent_id);
                    }
                    // Top-level function
                    "function_declaration" | "function_signature" => {
                        self.extract_function(source, child, ctx, parent_id);
                    }
                    // Method inside class
                    "method_declaration" | "method_signature" | "constructor_signature"
                    | "factory_constructor_signature" | "constant_constructor_signature"
                    | "redirecting_factory_constructor_signature" => {
                        self.extract_method(source, child, ctx, parent_id);
                    }
                    // Getter/setter
                    "getter_declaration" | "setter_declaration" => {
                        self.extract_method(source, child, ctx, parent_id);
                    }
                    // Variables
                    "variable_declaration" | "top_level_variable_declaration"
                    | "local_variable_declaration" => {
                        self.extract_variable(source, child, ctx, parent_id);
                    }
                    "enum_declaration" => {
                        self.extract_enum(source, child, ctx, parent_id);
                    }
                    // Imports (nested, rare but handle)
                    "import_specification" | "import_or_export" | "library_import"
                    | "library_export" | "export" | "part" | "part_directive" => {
                        self.extract_import(source, child, ctx, parent_id);
                    }
                    // Calls
                    "call_expression" | "constructor_invocation" | "new_expression" => {
                        self.extract_call(source, child, ctx, parent_id);
                    }
                    // Inheritance
                    "superclass" => {
                        self.extract_extends(source, child, ctx, parent_id);
                    }
                    "interfaces" | "mixins" | "with" | "on" => {
                        self.extract_implements(source, child, ctx, parent_id);
                    }
                    // Annotations
                    "annotation" => {
                        self.extract_annotation(source, child, ctx, parent_id);
                    }
                    // Recurse into structural nodes
                    "if_statement" | "for_statement" | "while_statement"
                    | "do_statement" | "switch_statement" | "switch_block"
                    | "switch_expression" | "try_statement" | "return_statement"
                    | "expression_statement" | "block" | "declaration"
                    | "list_literal" | "set_or_map_literal" | "record_literal"
                    | "assignment_expression" | "cascade_section"
                    | "throw_expression" | "yield_statement" | "yield_each_statement"
                    | "assertion"
                    | "break_statement" | "continue_statement" | "labeled_statement"
                    | "pattern_variable_declaration" | "pattern_assignment"
                    | "cascade_call_expression" | "cascade_member_expression"
                    | "type_cast_expression" | "type_test_expression" | "throw"
                    | "class_member" | "function_body" | "arguments"
                    | "function_expression" | "function_expression_body"
                    | "constructor_tearoff" | "conditional_expression"
                    | "field_initializer" | "initializers" | "initializer_list_entry"
                    | "named_argument" | "formal_parameter_list" | "formal_parameter"
                    | "type_arguments" | "type_parameters" | "optional_formal_parameters"
                    | "initialized_identifier_list" | "identifier_list"
                    | "static_final_declaration_list" | "static_final_declaration"
                    | "class_body" | "enum_body" | "extension_body" | "combinator" => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                    _ => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                }
            }
        }
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
    ) {
        let name = get_identifier_text(source, node);
        if name.is_empty() {
            return;
        }

        let is_abstract = check_abstract(source, node);
        let kind = if is_abstract {
            NodeKind::Interface
        } else {
            NodeKind::Class
        };

        let class_id = ctx.add_node(kind, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains,
            (node.start_position().row + 1) as u32, Some(&name));

        self.class_stack.push(name.clone());
        ctx.push_scope(&name);
        ctx.push_scope_node(&class_id);

        // Walk the full class_declaration node for:
        // - body contents (methods, fields)
        // - superclass, interfaces, mixins, annotations
        self.walk_node(source, node, ctx, &class_id);

        ctx.pop_scope();
        self.class_stack.pop();
    }

    // ------------------------------------------------------------------
    // Method / function extraction
    // ------------------------------------------------------------------

    fn extract_method(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let name = get_identifier_text(source, node);
        if name.is_empty() {
            return;
        }

        let clean_name = name.trim_start_matches("get ").trim_start_matches("set ")
            .trim_start_matches("operator ").to_string();

        let method_id = ctx.add_node(NodeKind::Method, &clean_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains,
            (node.start_position().row + 1) as u32, Some(&clean_name));

        // Walk for calls inside method
        self.walk_node(source, node, ctx, &method_id);
    }

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let name = get_identifier_text(source, node);
        if name.is_empty() {
            return;
        }

        let func_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains,
            (node.start_position().row + 1) as u32, Some(&name));

        self.walk_node(source, node, ctx, &func_id);
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    fn extract_variable(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        extract_var_recursive(source, node, ctx, parent_id);
        self.walk_node(source, node, ctx, parent_id);
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
    ) {
        let name = get_identifier_text(source, node);
        if name.is_empty() {
            return;
        }

        ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
    }

    // ------------------------------------------------------------------
    // Import extraction (enhanced for cross-file resolution)
    // ------------------------------------------------------------------

    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        // Find the import URI
        let import_path = match find_import_path(source, node) {
            Some(p) => p,
            None => return,
        };

        let clean = import_path.trim_matches(|c| c == '"' || c == '\'').to_string();
        if clean.is_empty() {
            return;
        }

        let line = (node.start_position().row + 1) as u32;

        // Always create IMPORTS edge
        let imports_tgt_text = ctx.make_qualified(&clean);
        let imports_tgt_id = hash_id(&ctx.file_path, &imports_tgt_text);
        ctx.add_edge(parent_id, &imports_tgt_id, EdgeKind::Imports, line, Some(&clean));

        // dart: imports → SDK external, no REFERENCES, no imported_names
        if clean.starts_with("dart:") {
            return;
        }

        // Find show/hide combinators and prefix (as alias)
        let show_names = find_show_names(source, &node);
        let hide_names = find_hide_names(source, &node);
        let prefix_name = find_prefix_name(source, &node);

        // Extract module basename (last path segment without .dart extension)
        let module_basename = extract_module_basename(&clean);

        if let Some(ref alias) = prefix_name {
            // "import '...' as prefix_name"
            let ref_text = format!("{}::{}", clean, alias);
            let ref_target = hash_id(&ctx.file_path, &ref_text);
            ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_text));
            self.imported_names.insert(alias.clone(), ref_text);
        } else if !show_names.is_empty() {
            // "import '...' show X, Y"
            for name in &show_names {
                let ref_text = format!("{}::{}", clean, name);
                let ref_target = hash_id(&ctx.file_path, &ref_text);
                ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_text));
                self.imported_names.insert(name.clone(), ref_text);
            }
        } else {
            // Bare import or hide import
            // Add module basename to imported_names
            if !module_basename.is_empty() && !hide_names.contains(&module_basename) {
                let ref_text = format!("{}::{}", clean, module_basename);
                let ref_target = hash_id(&ctx.file_path, &ref_text);
                ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_text));
                self.imported_names.insert(module_basename.clone(), ref_text);
            }
        }
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
    ) {
        let name = get_identifier_text(source, node);
        if name.is_empty() || is_dart_builtin(&name) {
            return;
        }

        // Qualify call target using imported_names
        let qualified = self.qualify_call_target(&name);
        let tgt_text = if qualified != name {
            // Use the qualified name directly for cross-file resolution
            qualified.clone()
        } else {
            ctx.make_qualified(&name)
        };
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        let display_text = if qualified != name {
            qualified.as_str()
        } else {
            name.as_str()
        };
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
            (node.start_position().row + 1) as u32, Some(display_text));

        self.walk_node(source, node, ctx, parent_id);
    }

    // ------------------------------------------------------------------
    // Extends / Implements extraction
    // ------------------------------------------------------------------

    fn extract_extends(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "type_identifier" || child.kind() == "identifier"
                    || child.kind() == "type"
                {
                    let name = child.utf8_text(source).unwrap_or("");
                    if !name.is_empty() && !is_dart_builtin(&name) {
                        let qualified = self.qualify_type(&name);
                        let tgt_text = qualified.clone();
                        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Extends,
                            (child.start_position().row + 1) as u32, Some(&qualified));
                    }
                }
            }
        }
    }

    fn extract_implements(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "type_identifier" || child.kind() == "identifier"
                    || child.kind() == "type"
                {
                    let name = child.utf8_text(source).unwrap_or("");
                    if !name.is_empty() && !is_dart_builtin(&name) {
                        let qualified = self.qualify_type(&name);
                        let tgt_text = qualified.clone();
                        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Implements,
                            (child.start_position().row + 1) as u32, Some(&qualified));
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Annotation / Decorator extraction
    // ------------------------------------------------------------------

    fn extract_annotation(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let name = get_identifier_text(source, node);
        if !name.is_empty() {
            let qualified = self.qualify_type(&name);
            let tgt_text = qualified.clone();
            let tgt_id = hash_id(&ctx.file_path, &tgt_text);
            ctx.add_edge(parent_id, &tgt_id, EdgeKind::Decorates,
                (node.start_position().row + 1) as u32, Some(&qualified));
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers (free functions)
// ---------------------------------------------------------------------------

/// Get the first identifier name from a node tree (recursive).
fn get_identifier_text(source: &[u8], node: Node) -> String {
    // Try "name" field first
    if let Some(name_node) = node.child_by_field_name("name") {
        return name_node.utf8_text(source).unwrap_or("").to_string();
    }

    if node.kind() == "identifier" || node.kind() == "type_identifier"
        || node.kind() == "identifier_dollar_escaped"
    {
        return node.utf8_text(source).unwrap_or("").to_string();
    }

    // Recursively search for the first identifier
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let result = get_identifier_text(source, child);
            if !result.is_empty() {
                return result;
            }
        }
    }

    String::new()
}

fn check_abstract(source: &[u8], node: Node) -> bool {
    for i in 0..node.child_count() {
        if let Some(c) = node.child(i) {
            if c.kind() == "abstract" || c.utf8_text(source).unwrap_or("") == "abstract" {
                return true;
            }
        }
    }
    false
}

fn extract_var_recursive(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    if node.kind() == "identifier" {
        let name = node.utf8_text(source).unwrap_or("");
        if !name.is_empty() {
            ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
        }
        return;
    }

    if node.kind() == "initialized_identifier" || node.kind() == "initialized_variable_definition"
        || node.kind() == "typed_identifier" || node.kind() == "static_final_declaration"
    {
        let name = get_identifier_text(source, node);
        if !name.is_empty() {
            ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
        }
        return;
    }

    // Recurse into container nodes
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "initialized_identifier_list" | "identifier_list"
                | "static_final_declaration_list" | "static_final_declaration"
                | "initialized_variable_definition"
                | "initialized_identifier" | "typed_identifier" | "identifier"
                | "pattern_variable_declaration" | "variable_pattern" => {
                    extract_var_recursive(source, child, ctx, parent_id);
                }
                _ => {}
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Import-related helpers
// ---------------------------------------------------------------------------

/// Find the import path (URI string) within an import node.
fn find_import_path(source: &[u8], node: Node) -> Option<String> {
    match node.kind() {
        "uri" | "configurable_uri" | "configuration_uri"
        | "string_literal" | "string_literal_single_quotes"
        | "string_literal_double_quotes" | "raw_string_literal_single_quotes"
        | "raw_string_literal_double_quotes" => {
            return Some(node.utf8_text(source).unwrap_or("").to_string());
        }
        _ => {}
    }

    // Recurse into wrapper nodes
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "import_specification" | "library_import" | "import_or_export"
                | "library_export" | "configurable_uri" | "configuration_uri"
                | "uri" | "string_literal" | "string_literal_single_quotes"
                | "string_literal_double_quotes" | "raw_string_literal_single_quotes"
                | "raw_string_literal_double_quotes" => {
                    if let Some(path) = find_import_path(source, child) {
                        return Some(path);
                    }
                }
                _ => {}
            }
        }
    }

    None
}

/// Find `show` combinator names from an import node.
/// In tree-sitter-dart, combinators contain an `identifier_list` with `identifier` children.
fn find_show_names(source: &[u8], parent_node: &Node) -> Vec<String> {
    let mut names = Vec::new();
    find_combinator_names(source, parent_node, "show", &mut names);
    names
}

/// Find `hide` combinator names from an import node.
fn find_hide_names(source: &[u8], parent_node: &Node) -> Vec<String> {
    let mut names = Vec::new();
    find_combinator_names(source, parent_node, "hide", &mut names);
    names
}

/// Find names from show/hide combinators within a node tree.
fn find_combinator_names(source: &[u8], node: &Node, combinator_kind: &str, names: &mut Vec<String>) {
    // Check if this node is a combinator of the desired kind
    if node.kind() == "combinator" {
        // Check for show/hide keyword
        let mut is_target = false;
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let text = child.utf8_text(source).unwrap_or("");
                if text == combinator_kind {
                    is_target = true;
                    break;
                }
            }
        }
        // Also check unnamed children for the keyword
        if !is_target {
            for i in 0..node.child_count() {
                if let Some(child) = node.child(i) {
                    if !child.is_named() {
                        let text = child.utf8_text(source).unwrap_or("");
                        if text == combinator_kind {
                            is_target = true;
                            break;
                        }
                    }
                }
            }
        }
        if is_target {
            // Extract names from identifier_list or direct identifiers
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "identifier_list" {
                        for j in 0..child.named_child_count() {
                            if let Some(id_node) = child.named_child(j) {
                                if id_node.kind() == "identifier" || id_node.kind() == "type_identifier" {
                                    let name = id_node.utf8_text(source).unwrap_or("");
                                    if !name.is_empty() {
                                        names.push(name.to_string());
                                    }
                                }
                            }
                        }
                    } else if child.kind() == "identifier" || child.kind() == "type_identifier" {
                        let name = child.utf8_text(source).unwrap_or("");
                        if !name.is_empty() && name != combinator_kind {
                            names.push(name.to_string());
                        }
                    }
                }
            }
        }
        return;
    }

    // Recurse into children
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            find_combinator_names(source, &child, combinator_kind, names);
        }
    }
}

/// Find the `as` prefix/alias name from an import node.
///
/// In tree-sitter-dart, the prefix may be:
/// - A named child with kind "prefix"
/// - A field named "prefix" on the import node
/// - Stored as an "as" keyword followed by an identifier
fn find_prefix_name(source: &[u8], parent_node: &Node) -> Option<String> {
    // 1. Try field name "prefix" on the import node (common in tree-sitter grammars)
    if let Some(prefix_node) = parent_node.child_by_field_name("prefix") {
        let name = prefix_node.utf8_text(source).unwrap_or("");
        if !name.is_empty() && name != "as" {
            return Some(name.to_string());
        }
    }

    // 2. Search for named child with kind "prefix"
    for i in 0..parent_node.named_child_count() {
        if let Some(child) = parent_node.named_child(i) {
            if child.kind() == "prefix" {
                // The prefix node should have an identifier child
                for j in 0..child.named_child_count() {
                    if let Some(id_node) = child.named_child(j) {
                        if id_node.kind() == "identifier" || id_node.kind() == "type_identifier" {
                            return Some(id_node.utf8_text(source).unwrap_or("").to_string());
                        }
                    }
                }
                // Also check unnamed children
                for j in 0..child.child_count() {
                    if let Some(c) = child.child(j) {
                        if c.is_named() && (c.kind() == "identifier" || c.kind() == "type_identifier") {
                            return Some(c.utf8_text(source).unwrap_or("").to_string());
                        }
                    }
                }
            }
        }
    }

    // 3. Search for "as" keyword among all children, then grab the next named child (the alias)
    for i in 0..parent_node.child_count() {
        if let Some(child) = parent_node.child(i) {
            if !child.is_named() {
                let text = child.utf8_text(source).unwrap_or("");
                if text == "as" {
                    // Look forward for the next named child (the alias identifier)
                    for j in (i + 1)..parent_node.child_count() {
                        if let Some(next_child) = parent_node.child(j) {
                            if next_child.is_named() && (next_child.kind() == "identifier" || next_child.kind() == "type_identifier") {
                                let name = next_child.utf8_text(source).unwrap_or("");
                                if !name.is_empty() && name != "deferred" {
                                    return Some(name.to_string());
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 4. Recursively search deeper in the node tree
    for i in 0..parent_node.named_child_count() {
        if let Some(child) = parent_node.named_child(i) {
            if let Some(name) = find_prefix_name(source, &child) {
                return Some(name);
            }
        }
    }

    None
}

/// Extract the module basename from an import path.
/// e.g. `package:flutter/material.dart` → `material`
/// e.g. `foo/bar.dart` → `bar`
/// e.g. `foo/bar` → `bar`
/// e.g. `dart:core` → `core`
fn extract_module_basename(path: &str) -> String {
    // Strip the scheme prefix (package:xxx, dart:xxx)
    let after_scheme = if let Some(colon_pos) = path.find(':') {
        &path[colon_pos + 1..]
    } else {
        path
    };

    // Get the last segment
    let last_seg = if let Some(slash_pos) = after_scheme.rfind('/') {
        &after_scheme[slash_pos + 1..]
    } else {
        after_scheme
    };

    // Strip .dart extension
    if last_seg.ends_with(".dart") {
        last_seg[..last_seg.len() - 5].to_string()
    } else {
        last_seg.to_string()
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
        parser
            .set_language(&tree_sitter_dart::LANGUAGE.into())
            .expect("set dart language");
        let tree = parser.parse(source, None).expect("parse dart source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "dart".to_string());
        DartExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges<'a>(ctx: &'a ExtractionContext, kind: EdgeKind) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    // ------------------------------------------------------------------
    // Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.dart");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("// just a comment\n", "src/comments.dart");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract("class MyWidget {\n}\n", "src/test.dart");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyWidget");
    }

    #[test]
    fn test_extract_class_with_extends() {
        let ctx = extract(
            "class MyApp extends StatelessWidget {\n}\n",
            "src/test.dart",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        let extends_edges = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"StatelessWidget"), "Expected EXTENDS StatelessWidget, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_top_level_function() {
        let ctx = extract("void main() {\n  print('hello');\n}\n", "src/test.dart");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(funcs.len() >= 1, "Expected at least 1 function, got {}", funcs.len());
        let names: Vec<&str> = funcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"main"), "Expected 'main' in names: {:?}", names);
    }

    #[test]
    fn test_extract_method() {
        let ctx = extract(
            "class Counter {\n  void increment() {\n  }\n}\n",
            "src/test.dart",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(methods.len() >= 1, "Expected at least 1 method, got {}", methods.len());
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract("void main() {\n  doWork();\n}\n", "src/test.dart");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"doWork"), "Expected doWork in calls: {:?}", targets);
    }

    #[test]
    fn test_builtins_filtered() {
        let ctx = extract("void main() {\n  print('hello');\n}\n", "src/test.dart");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.contains(&"print"));
    }

    // ------------------------------------------------------------------
    // Import extraction (original)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_import() {
        let ctx = extract("import 'package:flutter/material.dart';\n", "src/test.dart");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"package:flutter/material.dart"), "got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract("enum Color { red, green, blue }\n", "src/test.dart");
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
    }

    // ------------------------------------------------------------------
    // Implements extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_implements() {
        let ctx = extract(
            "class MyService implements IService {\n}\n",
            "src/test.dart",
        );
        let impl_edges = find_edges(&ctx, EdgeKind::Implements);
        let targets: Vec<&str> = impl_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"IService"), "Expected IMPLEMENTS IService, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_variable() {
        let ctx = extract("var count = 0;\nfinal String name = 'app';\n", "src/test.dart");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 2, "Expected at least 2 variables, got {}", vars.len());
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("class MyClass {\n  void method() {}\n}\n", "src/test.dart");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Nested constructs
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_class_with_method_and_call() {
        let ctx = extract(
            "class App {\n  void run() {\n    boot();\n  }\n}\n",
            "src/test.dart",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(classes.len(), 1);
        assert!(methods.len() >= 1, "Expected at least 1 method, got {}", methods.len());
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"boot"), "Expected boot in calls: {:?}", targets);
    }

    #[test]
    fn test_multiple_classes() {
        let ctx = extract(
            "class A {}\nclass B {}\nclass C {}\n",
            "src/test.dart",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 3);
    }

    // ==================================================================
    // Stage 12: Cross-file import resolution tests
    // ==================================================================

    // ------------------------------------------------------------------
    // Package import creates REFERENCES edge
    // ------------------------------------------------------------------

    #[test]
    fn test_package_import_creates_references_edge() {
        let ctx = extract(
            "import 'package:flutter/material.dart';\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for package import");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // module basename is "material"
        let expected = "package:flutter/material.dart::material";
        assert!(targets.contains(&expected),
            "Expected REFERENCES target_text '{}', got: {:?}", expected, targets);
    }

    #[test]
    fn test_package_import_creates_imports_edge() {
        let ctx = extract(
            "import 'package:my_app/utils/helpers.dart';\n",
            "src/test.dart",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for package import");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("package:my_app/utils/helpers.dart")),
            "Expected IMPORTS with package path, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // dart: SDK import → IMPORTS only, no REFERENCES
    // ------------------------------------------------------------------

    #[test]
    fn test_dart_sdk_import_only_imports_edge() {
        let ctx = extract(
            "import 'dart:core';\n",
            "src/test.dart",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for dart: import");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(refs.is_empty(), "Expected NO REFERENCES edge for dart: import, got {:?}",
            refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect::<Vec<_>>());
    }

    // ------------------------------------------------------------------
    // show combinator → REFERENCES for each named symbol
    // ------------------------------------------------------------------

    #[test]
    fn test_show_combinator_creates_references_for_each_symbol() {
        let ctx = extract(
            "import 'package:foo/bar.dart' show Baz, Qux;\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edges for show import");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"package:foo/bar.dart::Baz"),
            "Expected Baz reference, got: {:?}", targets);
        assert!(targets.contains(&"package:foo/bar.dart::Qux"),
            "Expected Qux reference, got: {:?}", targets);
    }

    #[test]
    fn test_show_single_symbol_creates_reference() {
        let ctx = extract(
            "import 'package:my_app/models/user.dart' show User;\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"package:my_app/models/user.dart::User"),
            "Expected User reference, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // hide combinator → module-level REFERENCES
    // ------------------------------------------------------------------

    #[test]
    fn test_hide_combinator_creates_module_reference() {
        let ctx = extract(
            "import 'package:foo/bar.dart' hide Secret;\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // Module basename is "bar"
        let expected = "package:foo/bar.dart::bar";
        assert!(targets.contains(&expected),
            "Expected module reference '{}', got: {:?}", expected, targets);
    }

    // Test: hide does NOT create ref for the hidden symbol
    #[test]
    fn test_hide_combinator_does_not_reference_hidden_symbol() {
        let ctx = extract(
            "import 'package:foo/bar.dart' hide Secret;\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.contains(&"package:foo/bar.dart::Secret"),
            "Should NOT have reference for hidden symbol, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // as prefix → REFERENCES with alias
    // ------------------------------------------------------------------

    #[test]
    fn test_as_prefix_creates_alias_reference() {
        let ctx = extract(
            "import 'package:foo/bar.dart' as b;\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let expected = "package:foo/bar.dart::b";
        assert!(targets.contains(&expected),
            "Expected alias reference '{}', got: {:?}", expected, targets);
    }

    // ------------------------------------------------------------------
    // Relative import
    // ------------------------------------------------------------------

    #[test]
    fn test_relative_import_creates_references_edge() {
        let ctx = extract(
            "import 'helpers.dart';\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // basename: helpers
        assert!(targets.contains(&"helpers.dart::helpers"),
            "Expected relative import reference, got: {:?}", targets);
    }

    #[test]
    fn test_relative_import_with_subdirectory_creates_references() {
        let ctx = extract(
            "import 'utils/helpers.dart';\n",
            "src/test.dart",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // basename: helpers
        assert!(targets.contains(&"utils/helpers.dart::helpers"),
            "Expected relative subdirectory reference, got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Imported call qualification
    // ------------------------------------------------------------------

    #[test]
    fn test_imported_symbol_call_uses_qualified_target_text() {
        let ctx = extract(
            "import 'package:foo/bar.dart' show Baz;\nvoid main() {\n  Baz();\n}\n",
            "src/test.dart",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // Baz should be qualified with module path
        let expected = "package:foo/bar.dart::Baz";
        assert!(targets.contains(&expected),
            "Expected qualified call '{}', got: {:?}", expected, targets);
    }

    #[test]
    fn test_imported_alias_call_uses_qualified_target_text() {
        let ctx = extract(
            "import 'package:foo/bar.dart' as b;\nvoid main() {\n  b.someMethod();\n}\n",
            "src/test.dart",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        // b should be qualified
        let expected = "package:foo/bar.dart::b";
        assert!(targets.contains(&expected),
            "Expected alias-qualified call '{}', got: {:?}", expected, targets);
    }

    #[test]
    fn test_non_imported_call_uses_bare_name() {
        let ctx = extract(
            "void main() {\n  localFunc();\n}\n",
            "src/test.dart",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"localFunc"),
            "Expected bare-name call 'localFunc', got: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Type annotation qualification
    // ------------------------------------------------------------------

    #[test]
    fn test_imported_type_annotation_uses_qualified_target_text() {
        let ctx = extract(
            "import 'package:foo/bar.dart' show MyWidget;\nclass App extends MyWidget {\n}\n",
            "src/test.dart",
        );
        let extends_edges = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends_edges.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let expected = "package:foo/bar.dart::MyWidget";
        assert!(targets.contains(&expected),
            "Expected qualified type reference '{}', got: {:?}", expected, targets);
    }

    // ------------------------------------------------------------------
    // Multiple imports
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_imports_create_multiple_references() {
        let ctx = extract(
            "import 'package:flutter/material.dart';\nimport 'package:my_app/models/user.dart' show User;\nimport 'dart:convert';\n",
            "src/test.dart",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 3, "Expected at least 3 IMPORTS edges, got {}", imports.len());
        let refs = find_edges(&ctx, EdgeKind::References);
        // 2 non-dart imports: material (package) + User (show)
        assert!(refs.len() >= 2, "Expected at least 2 REFERENCES edges, got {}", refs.len());
    }

    // ------------------------------------------------------------------
    // Extract module basename helper
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_module_basename_package() {
        assert_eq!(extract_module_basename("package:flutter/material.dart"), "material");
        assert_eq!(extract_module_basename("package:my_app/foo/bar.dart"), "bar");
    }

    #[test]
    fn test_extract_module_basename_relative() {
        assert_eq!(extract_module_basename("foo.dart"), "foo");
        assert_eq!(extract_module_basename("utils/helpers.dart"), "helpers");
        assert_eq!(extract_module_basename("helpers"), "helpers");
    }

    #[test]
    fn test_extract_module_basename_dart_sdk() {
        assert_eq!(extract_module_basename("dart:core"), "core");
        assert_eq!(extract_module_basename("dart:convert"), "convert");
    }
}
