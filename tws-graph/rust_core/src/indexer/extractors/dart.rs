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

        walk_node(source, root, ctx, &file_id);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walker
// ---------------------------------------------------------------------------

fn walk_node(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                // Class-related
                "class_declaration" | "mixin_declaration" => {
                    extract_class(source, child, ctx, parent_id);
                }
                // Top-level function
                "function_declaration" | "function_signature" => {
                    extract_top_function(source, child, ctx, parent_id);
                }
                // Method inside class
                "method_declaration" | "method_signature" | "constructor_signature"
                | "factory_constructor_signature" | "constant_constructor_signature"
                | "redirecting_factory_constructor_signature" => {
                    extract_method(source, child, ctx, parent_id);
                }
                // Getter/setter
                "getter_declaration" | "setter_declaration" => {
                    extract_method(source, child, ctx, parent_id);
                }
                // Variables
                "variable_declaration" | "top_level_variable_declaration"
                | "local_variable_declaration" => {
                    extract_variable(source, child, ctx, parent_id);
                }
                "enum_declaration" => {
                    extract_enum(source, child, ctx, parent_id);
                }
                // Imports
                "import_specification" | "import_or_export" | "library_import"
                | "library_export" | "export" | "part" | "part_directive" => {
                    extract_import(source, child, ctx, parent_id);
                }
                // Calls
                "call_expression" | "constructor_invocation" | "new_expression" => {
                    extract_call(source, child, ctx, parent_id);
                }
                // Inheritance - the actual node kinds are superclass and interfaces
                "superclass" => {
                    extract_extends(source, child, ctx, parent_id);
                }
                "interfaces" | "mixins" | "with" | "on" => {
                    extract_implements(source, child, ctx, parent_id);
                }
                // Annotations
                "annotation" => {
                    extract_annotation(source, child, ctx, parent_id);
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
                    walk_node(source, child, ctx, parent_id);
                }
                _ => {
                    // Default recursion
                    walk_node(source, child, ctx, parent_id);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Class extraction
// ---------------------------------------------------------------------------

fn extract_class(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
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

    ctx.push_scope(&name);
    ctx.push_scope_node(&class_id);

    // Walk the full class_declaration node for:
    // - body contents (methods, fields)
    // - superclass, interfaces, mixins, annotations
    walk_node(source, node, ctx, &class_id);

    ctx.pop_scope();
}

fn check_abstract(source: &[u8], node: Node) -> bool {
    // Look for "abstract" keyword among children
    for i in 0..node.child_count() {
        if let Some(c) = node.child(i) {
            if c.kind() == "abstract" || c.utf8_text(source).unwrap_or("") == "abstract" {
                return true;
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Method / top-level function extraction
// ---------------------------------------------------------------------------

fn extract_method(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
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
    walk_node(source, node, ctx, &method_id);
}

fn extract_top_function(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_identifier_text(source, node);
    if name.is_empty() {
        return;
    }

    let func_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains,
        (node.start_position().row + 1) as u32, Some(&name));

    walk_node(source, node, ctx, &func_id);
}

// ---------------------------------------------------------------------------
// Variable extraction
// ---------------------------------------------------------------------------

fn extract_variable(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Recursively search for identifiers to extract as variables
    extract_var_recursive(source, node, ctx, parent_id);
    walk_node(source, node, ctx, parent_id);
}

fn extract_var_recursive(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    if node.kind() == "identifier" {
        let name = node.utf8_text(source).unwrap_or("");
        if !name.is_empty() {
            ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
        }
        return;
    }

    // For initialized_identifier nodes, extract the name
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
// Enum extraction
// ---------------------------------------------------------------------------

fn extract_enum(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_identifier_text(source, node);
    if name.is_empty() {
        return;
    }

    ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
}

// ---------------------------------------------------------------------------
// Import extraction
// ---------------------------------------------------------------------------

fn extract_import(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Recursively search for the import path in the node tree
    if let Some(path) = find_import_path(source, node) {
        let clean = path.trim_matches(|c| c == '"' || c == '\'').to_string();
        if !clean.is_empty() {
            let tgt_text = ctx.make_qualified(&clean);
            let tgt_id = hash_id(&ctx.file_path, &tgt_text);
            ctx.add_edge(parent_id, &tgt_id, EdgeKind::Imports,
                (node.start_position().row + 1) as u32, Some(&clean));
        }
    }
}

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

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_identifier_text(source, node);
    if name.is_empty() || is_dart_builtin(&name) {
        return;
    }

    let tgt_text = ctx.make_qualified(&name);
    let tgt_id = hash_id(&ctx.file_path, &tgt_text);
    ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
        (node.start_position().row + 1) as u32, Some(&name));

    walk_node(source, node, ctx, parent_id);
}

// ---------------------------------------------------------------------------
// Extends / Implements extraction
// ---------------------------------------------------------------------------

fn extract_extends(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "type_identifier" || child.kind() == "identifier"
                || child.kind() == "type"
            {
                let name = child.utf8_text(source).unwrap_or("");
                if !name.is_empty() && !is_dart_builtin(&name) {
                    let tgt_text = ctx.make_qualified(&name);
                    let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                    ctx.add_edge(parent_id, &tgt_id, EdgeKind::Extends,
                        (child.start_position().row + 1) as u32, Some(&name));
                }
            }
        }
    }
}

fn extract_implements(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "type_identifier" || child.kind() == "identifier"
                || child.kind() == "type"
            {
                let name = child.utf8_text(source).unwrap_or("");
                if !name.is_empty() && !is_dart_builtin(&name) {
                    let tgt_text = ctx.make_qualified(&name);
                    let tgt_id = hash_id(&ctx.file_path, &tgt_text);
                    ctx.add_edge(parent_id, &tgt_id, EdgeKind::Implements,
                        (child.start_position().row + 1) as u32, Some(&name));
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Annotation / Decorator extraction
// ---------------------------------------------------------------------------

fn extract_annotation(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_identifier_text(source, node);
    if !name.is_empty() {
        let tgt_text = ctx.make_qualified(&name);
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Decorates,
            (node.start_position().row + 1) as u32, Some(&name));
    }
}

// ---------------------------------------------------------------------------
// Helpers
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
            // Skip wrapping nodes that don't contain useful identifiers
            let result = get_identifier_text(source, child);
            if !result.is_empty() {
                return result;
            }
        }
    }

    String::new()
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
    // Import extraction
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
}
