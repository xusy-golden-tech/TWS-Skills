//! Swift language extractor.
//!
//! Extracts symbols and relationships from Swift source files (`.swift`)
//! using the tree-sitter-swift grammar.
//!
//! Note: tree-sitter-swift uses a unified `class_declaration` node for all
//! type declarations (class, struct, enum, protocol, extension, actor).
//! The specific kind is determined by the body type and/or source text.
//!
//! # Node kinds produced
//! - `class`: class declaration
//! - `struct`: struct declaration
//! - `enum`: enum declaration
//! - `interface`: protocol declaration
//! - `function`: top-level function
//! - `method`: method inside a type
//! - `variable`: property/variable declaration
//!
//! # Edge kinds produced
//! - `calls`: call_expression
//! - `contains`: containment (file -> class -> method)
//! - `imports`: import_declaration
//! - `extends`: class/struct inheritance
//! - `implements`: protocol conformance

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// Swift standard library types -- filtered from type references.
const SWIFT_BUILTINS: &[&str] = &[
    "String", "Int", "Double", "Float", "Bool", "Character",
    "Array", "Dictionary", "Set", "Optional", "Result",
    "Int8", "Int16", "Int32", "Int64", "UInt", "UInt8", "UInt16", "UInt32", "UInt64",
    "Float32", "Float64", "Decimal",
    "Data", "Date", "URL", "UUID", "IndexPath", "IndexSet",
    "Any", "AnyObject", "Self", "Void", "Never",
    "print", "debugPrint", "dump", "fatalError", "precondition",
    "assert", "assertionFailure",
];

fn is_swift_builtin(name: &str) -> bool {
    SWIFT_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// SwiftExtractor
// ---------------------------------------------------------------------------

pub struct SwiftExtractor;

impl Extractor for SwiftExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["swift"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["swift"]
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
                "class_declaration" => {
                    extract_class_decl(source, child, ctx, parent_id);
                }
                "protocol_declaration" => {
                    extract_class_decl(source, child, ctx, parent_id);
                }
                "function_declaration" => {
                    extract_function(source, child, ctx, parent_id);
                }
                "call_expression" => {
                    extract_call(source, child, ctx, parent_id);
                }
                "import_declaration" => {
                    extract_import(source, child, ctx, parent_id);
                }
                "property_declaration" => {
                    extract_variable(source, child, ctx, parent_id);
                }
                "extension" | "extension_declaration" => {
                    extract_extension(source, child, ctx, parent_id);
                }
                // Recurse into structural nodes
                "if_statement" | "guard_statement" | "switch_statement"
                | "switch_entry" | "switch_pattern"
                | "for_statement" | "while_statement" | "repeat_while_statement"
                | "do_statement" | "return_statement" | "expression_statement"
                | "block" | "statements" | "declarations"
                | "class_body" | "enum_class_body" | "protocol_body" | "function_body"
                | "code_block" | "computed_property" | "computed_getter"
                | "computed_setter" | "willset_didset_block"
                | "value_arguments" | "lambda_literal" | "closure_expression"
                | "ternary_expression" | "assignment" | "navigation_expression"
                | "value_binding_pattern" | "pattern" | "tuple_expression"
                | "tuple_type" | "array_type" | "dictionary_type" | "optional_type"
                | "type_annotation" | "user_type" | "metatype"
                | "function_type" | "lambda_function_type"
                | "try_expression" | "await_expression"
                | "condition" | "nil_coalescing_expression"
                | "parameter" | "formal_parameter" | "type_arguments"
                | "modifiers" | "control_transfer_statement"
                | "infix_expression" | "prefix_expression" | "postfix_expression"
                | "comparison_expression" | "equality_expression"
                | "additive_expression" | "multiplicative_expression"
                | "bitwise_operation" | "as_expression" | "check_expression"
                | "constructor_expression" | "tuple_type_item"
                | "subscript_declaration" | "init_declaration"
                | "deinit_declaration" | "subscript" => {
                    walk_node(source, child, ctx, parent_id);
                }
                _ => {
                    walk_node(source, child, ctx, parent_id);
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Type declaration (class/struct/enum/protocol)
// ---------------------------------------------------------------------------

fn extract_class_decl(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) {
    let name = get_swift_name(source, node);
    if name.is_empty() {
        return;
    }

    // Determine the type kind by checking body type and source keywords
    let kind = determine_type_kind(source, node);

    let type_id = ctx.add_node(kind, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &type_id, EdgeKind::Contains,
        (node.start_position().row + 1) as u32, Some(&name));

    ctx.push_scope(&name);
    ctx.push_scope_node(&type_id);

    // Walk the full declaration node (body + inheritance)
    walk_node(source, node, ctx, &type_id);

    ctx.pop_scope();
}

fn determine_type_kind(source: &[u8], node: Node) -> NodeKind {
    // First check the node kind itself
    match node.kind() {
        "protocol_declaration" => return NodeKind::Interface,
        _ => {}
    }

    // Check the body field type
    if let Some(body) = node.child_by_field_name("body") {
        return match body.kind() {
            "enum_class_body" => NodeKind::Enum,
            "protocol_body" => NodeKind::Interface,
            "class_body" => {
                // Could be class or struct — check the first keyword in source text
                let full_text = node.utf8_text(source).unwrap_or("");
                let first_word = full_text.split_whitespace().next().unwrap_or("");
                match first_word {
                    "struct" => NodeKind::Struct,
                    "extension" => NodeKind::Class,
                    _ => NodeKind::Class,
                }
            }
            _ => NodeKind::Class,
        };
    }

    // Fallback: check source text for keyword
    let full_text = node.utf8_text(source).unwrap_or("");
    let first_word = full_text.split_whitespace().next().unwrap_or("");
    match first_word {
        "struct" => NodeKind::Struct,
        "enum" => NodeKind::Enum,
        "protocol" => NodeKind::Interface,
        "extension" => NodeKind::Class,
        "actor" => NodeKind::Class,
        _ => NodeKind::Class,
    }
}

// ---------------------------------------------------------------------------
// Extension extraction
// ---------------------------------------------------------------------------

fn extract_extension(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_swift_name(source, node);
    if name.is_empty() {
        return;
    }

    let ext_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());

    ctx.push_scope(&name);
    ctx.push_scope_node(&ext_id);

    walk_node(source, node, ctx, &ext_id);

    ctx.pop_scope();
}

// ---------------------------------------------------------------------------
// Function / method extraction
// ---------------------------------------------------------------------------

fn extract_function(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_swift_name(source, node);
    if name.is_empty() {
        return;
    }

    let is_method = ctx.scope_depth() > 0;
    let kind = if is_method {
        NodeKind::Method
    } else {
        NodeKind::Function
    };

    let func_id = ctx.add_node(kind, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains,
        (node.start_position().row + 1) as u32, Some(&name));

    // Walk the function body for calls
    walk_node(source, node, ctx, &func_id);
}

// ---------------------------------------------------------------------------
// Variable extraction
// ---------------------------------------------------------------------------

fn extract_variable(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Search for the simple_identifier in the pattern
    let name = find_simple_identifier(source, node);
    if !name.is_empty() {
        ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
    }
    walk_node(source, node, ctx, parent_id);
}

fn find_simple_identifier(source: &[u8], node: Node) -> String {
    if node.kind() == "simple_identifier" {
        return node.utf8_text(source).unwrap_or("").to_string();
    }

    // Check for pattern → simple_identifier structure
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "pattern" {
                let name = find_simple_identifier(source, child);
                if !name.is_empty() {
                    return name;
                }
            } else if child.kind() == "simple_identifier" {
                return child.utf8_text(source).unwrap_or("").to_string();
            }
        }
    }

    String::new()
}

// ---------------------------------------------------------------------------
// Import extraction
// ---------------------------------------------------------------------------

fn extract_import(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    // Recursively find all simple_identifier children
    let module_name = collect_import_identifiers(source, node);
    if !module_name.is_empty() {
        let tgt_text = ctx.make_qualified(&module_name);
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Imports,
            (node.start_position().row + 1) as u32, Some(&module_name));
    }
}

fn collect_import_identifiers(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    collect_ids_recursive(source, node, &mut parts);
    parts.join(".")
}

fn collect_ids_recursive(source: &[u8], node: Node, parts: &mut Vec<String>) {
    if node.kind() == "simple_identifier" || node.kind() == "type_identifier" {
        parts.push(node.utf8_text(source).unwrap_or("").to_string());
        return;
    }
    if node.kind() == "navigation_identifier" {
        parts.push(node.utf8_text(source).unwrap_or("").to_string());
        return;
    }
    if node.kind() == "import_kind" {
        return; // Skip e.g., "func", "struct" qualifiers
    }
    // Recurse into wrapper nodes
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            collect_ids_recursive(source, child, parts);
        }
    }
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
    let name = get_call_name(source, node);
    if name.is_empty() || is_swift_builtin(&name) {
        return;
    }

    let tgt_text = ctx.make_qualified(&name);
    let tgt_id = hash_id(&ctx.file_path, &tgt_text);
    ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
        (node.start_position().row + 1) as u32, Some(&name));

    walk_node(source, node, ctx, parent_id);
}

fn get_call_name(source: &[u8], node: Node) -> String {
    // call_expression: first named child is the function name
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "simple_identifier" => {
                    return child.utf8_text(source).unwrap_or("").to_string();
                }
                "navigation_expression" => {
                    return child.utf8_text(source).unwrap_or("").to_string();
                }
                "constructor_expression" => {
                    return child.utf8_text(source).unwrap_or("").to_string();
                }
                _ => {}
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract the name from a Swift declaration node.
fn get_swift_name(source: &[u8], node: Node) -> String {
    // Try "name" field first
    if let Some(name_node) = node.child_by_field_name("name") {
        return name_node.utf8_text(source).unwrap_or("").to_string();
    }

    // Look for type_identifier or simple_identifier child
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "type_identifier" || child.kind() == "simple_identifier" {
                return child.utf8_text(source).unwrap_or("").to_string();
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
            .set_language(&tree_sitter_swift::LANGUAGE.into())
            .expect("set swift language");
        let tree = parser.parse(source, None).expect("parse swift source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "swift".to_string());
        SwiftExtractor
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
        let ctx = extract("", "src/empty.swift");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("// just a comment\n", "src/comments.swift");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract("class MyClass {\n  func doSomething() {}\n}\n", "src/test.swift");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_inheritance() {
        let ctx = extract(
            "class MyView: UIView {\n  func draw() {}\n}\n",
            "src/test.swift",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyView");
    }

    // ------------------------------------------------------------------
    // Struct extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct() {
        let ctx = extract("struct Point {\n  var x: Int\n  var y: Int\n}\n", "src/test.swift");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Point");
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract("enum Direction {\n  case north, south, east, west\n}\n", "src/test.swift");
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Direction");
    }

    // ------------------------------------------------------------------
    // Protocol extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_protocol() {
        let ctx = extract("protocol Drawable {\n  func draw()\n}\n", "src/test.swift");
        let protocols = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(protocols.len(), 1);
        assert_eq!(protocols[0].name, "Drawable");
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function() {
        let ctx = extract("func greet() {\n  print(\"hello\")\n}\n", "src/test.swift");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "greet");
    }

    #[test]
    fn test_extract_method() {
        let ctx = extract(
            "class Counter {\n  func increment() {\n    count += 1\n  }\n}\n",
            "src/test.swift",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "increment");
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_variable() {
        let ctx = extract("var count = 0\nlet name = \"app\"\n", "src/test.swift");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(vars.len() >= 2, "Expected at least 2 variables, got {}", vars.len());
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract("func main() {\n  doWork()\n}\n", "src/test.swift");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"doWork"), "Expected doWork in calls: {:?}", targets);
    }

    #[test]
    fn test_builtins_filtered() {
        let ctx = extract("func main() {\n  print(\"hello\")\n}\n", "src/test.swift");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.contains(&"print"));
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_import() {
        let ctx = extract("import Foundation\n", "src/test.swift");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"Foundation"));
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("class MyClass {\n  func method() {}\n}\n", "src/test.swift");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Nested constructs
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_class_with_method_call() {
        let ctx = extract(
            "class ViewController {\n  func viewDidLoad() {\n    setupUI()\n  }\n}\n",
            "src/test.swift",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        let methods = find_nodes(&ctx, NodeKind::Method);
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert_eq!(classes.len(), 1);
        assert_eq!(methods.len(), 1);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"setupUI"), "Expected setupUI in calls: {:?}", targets);
    }

    #[test]
    fn test_multiple_type_declarations() {
        let ctx = extract(
            "class A {}\nstruct B {}\nenum C { case one }\nprotocol D {}\n",
            "src/test.swift",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        let structs = find_nodes(&ctx, NodeKind::Struct);
        let enums = find_nodes(&ctx, NodeKind::Enum);
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(classes.len(), 1);
        assert_eq!(structs.len(), 1);
        assert_eq!(enums.len(), 1);
        assert_eq!(interfaces.len(), 1);
    }
}