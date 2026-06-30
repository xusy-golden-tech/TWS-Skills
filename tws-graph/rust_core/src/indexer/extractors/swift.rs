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
//! - `references`: import_declaration symbols + type references
//! - `extends`: class/struct inheritance (via type_ref in extend)
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

/// Known Apple framework prefixes that should be filtered from type references
/// (these are resolved via the import system, not as raw type-ref edges).
const APPLE_FRAMEWORK_PREFIXES: &[&str] = &[
    "UIKit", "Foundation", "SwiftUI", "Combine", "AppKit", "WatchKit",
    "CoreGraphics", "CoreData", "CoreAnimation", "CoreLocation", "CoreBluetooth",
    "CoreMotion", "CoreImage", "CoreVideo", "CoreMedia", "CoreAudio",
    "CoreText", "CoreML", "CoreNFC", "CoreServices",
    "AVFoundation", "AVKit", "ARKit", "RealityKit",
    "MapKit", "WebKit", "SceneKit", "SpriteKit", "GameKit", "GameController",
    "Metal", "MetalKit", "StoreKit", "CloudKit",
    "Photos", "PhotosUI", "Contacts", "ContactsUI",
    "EventKit", "EventKitUI", "HealthKit", "HomeKit",
    "UserNotifications", "NotificationCenter",
    "SafariServices", "AuthenticationServices",
    "WidgetKit", "SwiftData", "SwiftCharts",
    "CryptoKit", "NaturalLanguage", "Speech", "Vision",
    "Dispatch", "Darwin", "ObjectiveC",
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

        let mut imported_names: HashMap<String, String> = HashMap::new();
        let mut walker = Walker { imported_names: &mut imported_names };
        walker.walk_node(source, root, ctx, &file_id);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker struct
// ---------------------------------------------------------------------------

struct Walker<'a> {
    /// Maps simple type/function names to their fully-qualified import paths.
    /// e.g. `"UIViewController" → "UIKit.UIViewController"`
    imported_names: &'a mut HashMap<String, String>,
}

impl<'a> Walker<'a> {
    fn walk_node(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "class_declaration" => {
                        self.extract_class_decl(source, child, ctx, parent_id);
                    }
                    "protocol_declaration" => {
                        self.extract_class_decl(source, child, ctx, parent_id);
                    }
                    "function_declaration" => {
                        self.extract_function(source, child, ctx, parent_id);
                    }
                    "call_expression" => {
                        self.extract_call(source, child, ctx, parent_id);
                    }
                    "import_declaration" => {
                        self.extract_import(source, child, ctx, parent_id);
                    }
                    "property_declaration" => {
                        self.extract_variable(source, child, ctx, parent_id);
                    }
                    "extension" | "extension_declaration" => {
                        self.extract_extension(source, child, ctx, parent_id);
                    }
                    "user_type" => {
                        self.extract_type_ref(source, child, ctx, parent_id);
                    }
                    // Also extract type references from type_annotation → user_type chain
                    "type_annotation" => {
                        self.walk_node(source, child, ctx, parent_id);
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
                    | "metatype"
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
                        self.walk_node(source, child, ctx, parent_id);
                    }
                    _ => {
                        self.walk_node(source, child, ctx, parent_id);
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------------------
    // Type declaration (class/struct/enum/protocol)
    // ---------------------------------------------------------------------------

    fn extract_class_decl(
        &mut self,
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
        self.walk_node(source, node, ctx, &type_id);

        ctx.pop_scope();
    }

    // ---------------------------------------------------------------------------
    // Extension extraction
    // ---------------------------------------------------------------------------

    fn extract_extension(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        let name = get_swift_name(source, node);
        if name.is_empty() {
            return;
        }

        let ext_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());

        ctx.push_scope(&name);
        ctx.push_scope_node(&ext_id);

        self.walk_node(source, node, ctx, &ext_id);

        ctx.pop_scope();
    }

    // ---------------------------------------------------------------------------
    // Function / method extraction
    // ---------------------------------------------------------------------------

    fn extract_function(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
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

        // Walk the function body for calls + type refs
        self.walk_node(source, node, ctx, &func_id);
    }

    // ---------------------------------------------------------------------------
    // Variable extraction
    // ---------------------------------------------------------------------------

    fn extract_variable(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        // Search for the simple_identifier in the pattern
        let name = find_simple_identifier(source, node);
        if !name.is_empty() {
            ctx.add_node(NodeKind::Variable, &name, &node, HashMap::new());
        }
        self.walk_node(source, node, ctx, parent_id);
    }

    // ---------------------------------------------------------------------------
    // Import extraction
    // ---------------------------------------------------------------------------

    fn extract_import(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        // Parse the import details: (module_name, import_kind, imported_symbol)
        //   import UIKit            → ("UIKit", None, None)
        //   import class UIKit.UIViewController → ("UIKit", "class", "UIViewController")
        //   import func UIKit.doSomething      → ("UIKit", "func", "doSomething")
        //   import struct UIKit.CGPoint        → ("UIKit", "struct", "CGPoint")
        let (module_name, _import_kind, imported_symbol) = parse_swift_import(source, node);

        if module_name.is_empty() {
            return;
        }

        let line = (node.start_position().row + 1) as u32;

        // 1. Always create IMPORTS edge for the module
        let tgt_text = ctx.make_qualified(&module_name);
        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Imports,
            line, Some(&module_name));

        // 2. Create REFERENCES edge + populate imported_names
        if let Some(symbol) = imported_symbol {
            // Specific import: `import class UIKit.UIViewController`
            // REFERENCES edge target_text = "UIKit.UIViewController::UIViewController"
            let ref_text = format!("{}.{}::{}", module_name, symbol, symbol);
            let ref_tgt_id = hash_id(&ctx.file_path, &ref_text);
            ctx.add_edge(parent_id, &ref_tgt_id, EdgeKind::References,
                line, Some(&ref_text));
            // Map simple name to qualified name for type-ref resolution
            self.imported_names.insert(symbol.clone(), format!("{}.{}", module_name, symbol));
        } else {
            // Whole module import: `import UIKit`
            // REFERENCES edge target_text = "UIKit::"
            let ref_text = format!("{}::", module_name);
            let ref_tgt_id = hash_id(&ctx.file_path, &ref_text);
            ctx.add_edge(parent_id, &ref_tgt_id, EdgeKind::References,
                line, Some(&ref_text));
            // Map module name → module name
            self.imported_names.insert(module_name.clone(), module_name.clone());
        }
    }

    // ---------------------------------------------------------------------------
    // Type reference extraction
    // ---------------------------------------------------------------------------

    /// Extract a type reference from a `user_type` node.
    /// Creates REFERENCES edges if the type is not a builtin.
    fn extract_type_ref(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        let type_name = node.utf8_text(source).unwrap_or("").to_string();
        if type_name.is_empty() || is_swift_builtin(&type_name) {
            return;
        }

        // Check if this type was imported or is from a known framework
        let qualified = self.qualify_type_name(&type_name);

        let tgt_text = if qualified != type_name {
            // Qualified: create REFERENCES with fully qualified target_text
            format!("{}::{}", qualified, type_name)
        } else {
            // Not imported — use file-qualified name for cross-file resolution
            ctx.make_qualified(&type_name)
        };

        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::References,
            (node.start_position().row + 1) as u32, Some(&tgt_text));
    }

    /// Attempt to qualify a bare type name using imported_names.
    /// Returns the qualified name if found, or the original name.
    fn qualify_type_name(&self, name: &str) -> String {
        // Direct lookup
        if let Some(q) = self.imported_names.get(name) {
            return q.clone();
        }

        // Try to match against known Apple framework prefixes for heuristic resolution
        // e.g., if we see `UIViewController` and `UIKit` was imported, produce `UIKit.UIViewController`
        for &prefix in APPLE_FRAMEWORK_PREFIXES {
            if name.starts_with(prefix) && name.len() > prefix.len() {
                // Check if this framework prefix was imported
                if self.imported_names.contains_key(prefix) {
                    return format!("{}.{}", prefix, name);
                }
            }
        }

        name.to_string()
    }

    // ---------------------------------------------------------------------------
    // Call extraction
    // ---------------------------------------------------------------------------

    fn extract_call(&mut self, source: &[u8], node: Node, ctx: &mut ExtractionContext, parent_id: &str) {
        let name = get_call_name(source, node);
        if name.is_empty() || is_swift_builtin(&name) {
            return;
        }

        // Check if this call is to an imported symbol
        let qualified = self.qualify_call_name(&name);
        let tgt_text = if qualified != name {
            // Use the qualified target_text for cross-file resolution
            format!("{}::{}", qualified, name)
        } else {
            ctx.make_qualified(&name)
        };

        let tgt_id = hash_id(&ctx.file_path, &tgt_text);
        ctx.add_edge(parent_id, &tgt_id, EdgeKind::Calls,
            (node.start_position().row + 1) as u32, Some(&tgt_text));

        self.walk_node(source, node, ctx, parent_id);
    }

    /// Qualify a call target name using imported_names.
    /// Returns the qualified name if the simple name or its prefix was imported.
    fn qualify_call_name(&self, name: &str) -> String {
        // Check navigation_expression like `SomeType.method`
        // For simple identifiers, check imported_names directly
        if let Some(q) = self.imported_names.get(name) {
            return q.clone();
        }

        // Check prefixes of navigation expressions
        // e.g., `UIView.animate` → check `UIView` in imported_names
        if let Some(dot_pos) = name.find('.') {
            let first_segment = &name[..dot_pos];
            if let Some(q) = self.imported_names.get(first_segment) {
                let rest = &name[dot_pos..];
                return format!("{}{}", q, rest);
            }
        }

        name.to_string()
    }
}

// ---------------------------------------------------------------------------
// Helper functions (standalone, callable from Walker methods)
// ---------------------------------------------------------------------------

/// Determine the NodeKind for a class_declaration by checking body type
/// and source keywords.
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

/// Find a simple_identifier name within a property_declaration node.
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

/// Extract the call target name from a call_expression node.
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
// Import parsing helpers
// ---------------------------------------------------------------------------

/// Parse a Swift import declaration into its components.
///
/// Swift supports several import forms:
/// - `import UIKit` → module_name="UIKit", import_kind=None, symbol=None
/// - `import class UIKit.UIViewController` → module="UIKit", kind="class", symbol="UIViewController"
/// - `import func UIKit.doSomething` → module="UIKit", kind="func", symbol="doSomething"
/// - `import struct UIKit.CGPoint` → module="UIKit", kind="struct", symbol="CGPoint"
/// - `import typealias UIKit.SomeType` → module="UIKit", kind="typealias", symbol="SomeType"
///
/// Returns (module_name, import_kind_opt, imported_symbol_opt).
fn parse_swift_import(source: &[u8], node: Node) -> (String, Option<String>, Option<String>) {
    // Collect all identifier parts and the import_kind (class/struct/func/etc.)
    let mut parts: Vec<String> = Vec::new();
    let mut import_kind: Option<String> = None;

    collect_import_parts(source, node, &mut parts, &mut import_kind);

    if parts.is_empty() {
        return (String::new(), None, None);
    }

    // If there's only one part, it's a simple module import like `import UIKit`
    if parts.len() == 1 {
        return (parts[0].clone(), import_kind, None);
    }

    // Multiple parts: e.g., `import class UIKit.UIViewController`
    // Module = all parts except the last, Symbol = last part
    let symbol = parts.last().unwrap().clone();
    let module_name = parts[..parts.len() - 1].join(".");

    (module_name, import_kind, Some(symbol))
}

/// Recursively collect identifier parts from an import_declaration node.
///
/// Handles:
/// - `simple_identifier` and `type_identifier` → identifier parts
/// - `navigation_identifier` → dot-separated navigation
/// - `import_kind` → class/func/struct/typealias qualifier (stored separately, skipped)
fn collect_import_parts(
    source: &[u8],
    node: Node,
    parts: &mut Vec<String>,
    import_kind: &mut Option<String>,
) {
    match node.kind() {
        "simple_identifier" | "type_identifier" => {
            parts.push(node.utf8_text(source).unwrap_or("").to_string());
            return;
        }
        "navigation_identifier" => {
            parts.push(node.utf8_text(source).unwrap_or("").to_string());
            return;
        }
        "import_kind" => {
            *import_kind = Some(node.utf8_text(source).unwrap_or("").to_string());
            return;
        }
        _ => {}
    }

    // Recurse into children
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            collect_import_parts(source, child, parts, import_kind);
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
        assert!(targets.iter().any(|t| t.contains("doWork")), "Expected doWork in calls: {:?}", targets);
    }

    #[test]
    fn test_builtins_filtered() {
        let ctx = extract("func main() {\n  print(\"hello\")\n}\n", "src/test.swift");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(!targets.iter().any(|t| t.contains("print")));
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
        assert!(targets.iter().any(|t| t.contains("Foundation")));
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
        assert!(targets.iter().any(|t| t.contains("setupUI")), "Expected setupUI in calls: {:?}", targets);
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

    // ------------------------------------------------------------------
    // Stage 13: Import + REFERENCES edges
    // ------------------------------------------------------------------

    #[test]
    fn test_import_creates_references_edge() {
        let ctx = extract("import UIKit\n", "src/test.swift");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for import UIKit");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let has_ui_kit = targets.iter().any(|t| t.contains("UIKit") && t.ends_with("::"));
        assert!(has_ui_kit, "Expected UIKit:: in REFERENCES: {:?}", targets);
    }

    #[test]
    fn test_import_creates_imports_edge() {
        let ctx = extract("import Foundation\n", "src/test.swift");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge");
    }

    #[test]
    fn test_import_class_creates_specific_reference() {
        // `import class UIKit.UIViewController` should create REFERENCES to UIViewController
        let ctx = extract("import class UIKit.UIViewController\n", "src/test.swift");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let has_view_ctrl = targets.iter().any(|t| t.contains("UIViewController"));
        assert!(has_view_ctrl, "Expected UIViewController in REFERENCES: {:?}", targets);
    }

    #[test]
    fn test_import_struct_creates_specific_reference() {
        // `import struct Foundation.Data` should create REFERENCES to Data
        let ctx = extract("import struct Foundation.Data\n", "src/test.swift");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let has_data = targets.iter().any(|t| t.contains("Data"));
        assert!(has_data, "Expected Data in REFERENCES: {:?}", targets);
    }

    #[test]
    fn test_import_func_creates_specific_reference() {
        // `import func UIKit.debugPrint` should create REFERENCES to debugPrint
        let ctx = extract("import func UIKit.debugPrint\n", "src/test.swift");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let has_debug = targets.iter().any(|t| t.contains("debugPrint"));
        assert!(has_debug, "Expected debugPrint in REFERENCES: {:?}", targets);
    }

    #[test]
    fn test_multiple_imports_create_multiple_references() {
        let ctx = extract(
            "import UIKit\nimport SwiftUI\n",
            "src/test.swift",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        let has_ui_kit = targets.iter().any(|t| t.contains("UIKit"));
        let has_swift_ui = targets.iter().any(|t| t.contains("SwiftUI"));
        assert!(has_ui_kit, "Expected UIKit in REFERENCES: {:?}", targets);
        assert!(has_swift_ui, "Expected SwiftUI in REFERENCES: {:?}", targets);
    }
}