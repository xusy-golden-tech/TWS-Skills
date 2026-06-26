//! Zig language extractor.
//!
//! Extracts symbols and relationships from Zig source files (`.zig`)
//! using the tree-sitter-zig grammar.
//!
//! # Node kinds produced
//! - `function`: function declaration
//! - `class`: struct declaration
//! - `enum`: enum declaration
//! - `variable`: variable declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: call expressions
//! - `contains`: containment (file -> struct -> function)
//! - `imports`: @import builtin calls

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// ZigExtractor
// ---------------------------------------------------------------------------

pub struct ZigExtractor;

impl Extractor for ZigExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["zig"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["zig"]
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

        walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking
// ---------------------------------------------------------------------------

fn walk_node(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    match node.kind() {
        "function_declaration" => extract_function(source, node, ctx, parent_id)?,
        "test_declaration" => extract_test(source, node, ctx, parent_id)?,
        "variable_declaration" => extract_variable(source, node, ctx, parent_id)?,
        "call_expression" => extract_call(source, node, ctx, parent_id)?,
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Function extraction
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let name = get_function_name(source, node);
    if name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    let line = node.start_position().row as u32 + 1;
    let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "function");
    ctx.push_scope_node(&fn_id);

    if let Some(body) = find_child_by_kind(node, "block") {
        walk_all_children(source, body, ctx, &fn_id)?;
    }

    ctx.pop_scope();
    Ok(())
}

fn get_function_name(source: &[u8], node: Node) -> String {
    let mut found_fn = false;
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            let kind = child.kind();
            if kind == "fn" {
                found_fn = true;
                continue;
            }
            if kind == "pub" || kind == "export" || kind == "inline" || kind == "noinline" {
                continue;
            }
            if found_fn && kind == "identifier" {
                let name = get_text(source, Some(child));
                if !is_zig_keyword(&name) {
                    return name;
                }
            }
            if found_fn
                && (kind == "parameter"
                    || kind == "function_signature"
                    || kind == "block"
                    || kind == ";"
                    || kind == "pointer_type"
                    || kind == "!"
                    || kind == "anytype")
            {
                break;
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Test extraction
// ---------------------------------------------------------------------------

fn extract_test(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    let mut test_name = String::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "string" || child.kind() == "multiline_string" {
                test_name = get_text(source, Some(child))
                    .trim_matches('"')
                    .to_string();
                break;
            }
        }
    }
    if test_name.is_empty() {
        test_name = "anonymous_test".to_string();
    }

    let test_id = ctx.add_node(NodeKind::Function, &test_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &test_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&test_name, "function");
    ctx.push_scope_node(&test_id);
    if let Some(body) = find_child_by_kind(node, "block") {
        walk_all_children(source, body, ctx, &test_id)?;
    }
    ctx.pop_scope();

    Ok(())
}

// ---------------------------------------------------------------------------
// Variable extraction (also handles struct/enum/error_set via assignments)
// ---------------------------------------------------------------------------

fn extract_variable(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Get the variable name
    let var_name = get_var_name(source, node);
    if var_name.is_empty() {
        return walk_all_children(source, node, ctx, parent_id);
    }

    // Check if this variable is assigned an @import
    let is_import = check_import(source, node, ctx, parent_id, line);

    // Check if this variable is assigned a struct/enum/error_set
    let mut is_container = false;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "struct_declaration" => {
                    extract_named_struct(source, child, ctx, parent_id, &var_name, line)?;
                    is_container = true;
                }
                "enum_declaration" => {
                    extract_named_enum(source, child, ctx, parent_id, &var_name, line)?;
                    is_container = true;
                }
                "error_set_declaration" => {
                    extract_named_enum(source, child, ctx, parent_id, &var_name, line)?;
                    is_container = true;
                }
                _ => {
                    walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
    }

    if !is_container && !is_import {
        let var_id = ctx.add_node(NodeKind::Variable, &var_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
    }

    Ok(())
}

fn get_var_name(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" {
                let name = get_text(source, Some(child));
                if !is_zig_keyword(&name) {
                    return name;
                }
            }
        }
    }
    String::new()
}

fn check_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
) -> bool {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "call_expression" | "builtin_function" => {
                    // Check for @import — identifier could be "identifier" or "builtin_identifier"
                    let mut is_import = false;
                    for j in 0..child.child_count() {
                        if let Some(gc) = child.child(j) {
                            let gk = gc.kind();
                            if gk == "builtin_identifier" {
                                let import_name = get_text(source, Some(gc));
                                if import_name == "@import" || import_name == "import" {
                                    is_import = true;
                                }
                            } else if gk == "identifier" {
                                let name = get_text(source, Some(gc));
                                if name == "@import" || name == "import" {
                                    is_import = true;
                                }
                            }
                        }
                    }
                    if is_import {
                        let import_path = find_import_path(source, child);
                        if !import_path.is_empty() {
                            let target_qn =
                                build_qualified_target(&ctx.file_path, &import_path);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            let file_id = hash_id(&ctx.file_path, &ctx.file_path);
                            ctx.add_edge(
                                &file_id,
                                &target,
                                EdgeKind::Imports,
                                line,
                                Some(&import_path),
                            );
                            return true;
                        }
                    }
                }
                _ => {}
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// Named struct extraction
// ---------------------------------------------------------------------------

fn extract_named_struct(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    name: &str,
    line: u32,
) -> anyhow::Result<()> {
    if name.is_empty() {
        return Ok(());
    }

    let class_id = ctx.add_node(NodeKind::Class, name, &node, HashMap::new());
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(name, "struct");
    ctx.push_scope_node(&class_id);

    walk_all_children(source, node, ctx, &class_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Named enum extraction
// ---------------------------------------------------------------------------

fn extract_named_enum(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    name: &str,
    line: u32,
) -> anyhow::Result<()> {
    if name.is_empty() {
        return Ok(());
    }

    let enum_id = ctx.add_node(NodeKind::Enum, name, &node, HashMap::new());
    ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(name, "enum");
    ctx.push_scope_node(&enum_id);

    walk_all_children(source, node, ctx, &enum_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    if let Some(callee) = find_child_by_kind(node, "identifier") {
        let callee_name = get_text(source, Some(callee));
        if !callee_name.is_empty()
            && !callee_name.starts_with('@')
            && !is_zig_builtin(&callee_name)
        {
            let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&callee_name));
        }
    }

    walk_all_children(source, node, ctx, parent_id)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Builtin filters
// ---------------------------------------------------------------------------

fn is_zig_builtin(name: &str) -> bool {
    matches!(
        name,
        "return"
            | "if"
            | "else"
            | "while"
            | "for"
            | "switch"
            | "try"
            | "catch"
            | "defer"
            | "errdefer"
    )
}

fn is_zig_keyword(name: &str) -> bool {
    matches!(
        name,
        "fn" | "pub" | "const" | "var" | "struct" | "enum" | "error" | "union" | "return"
            | "if" | "else" | "while" | "for" | "switch" | "try" | "catch" | "export"
            | "extern" | "inline" | "noinline" | "comptime" | "test" | "usingnamespace"
            | "defer" | "errdefer" | "suspend" | "resume" | "anytype" | "anyframe" | "void"
            | "bool" | "noreturn" | "type" | "allowzero" | "volatile" | "align"
            | "linksection" | "callconv" | "threadlocal" | "async" | "await" | "nosuspend"
            | "unreachable"
    )
}

fn find_import_path(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "string" || child.kind() == "multiline_string" {
                return get_text(source, Some(child)).trim_matches('"').to_string();
            }
            // Recurse into argument wrappers
            if child.kind() == "arguments" || child.kind() == "argument_list" {
                let path = find_import_path(source, child);
                if !path.is_empty() {
                    return path;
                }
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn walk_all_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
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

    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        let language: tree_sitter::Language = tree_sitter_zig::LANGUAGE.into();
        parser.set_language(&language).expect("set zig language");
        let tree = parser.parse(source, None).expect("parse zig source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "zig".to_string());
        ZigExtractor
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
        let ctx = extract("", "src/empty.zig");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // 2. Simple function
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_function() {
        let ctx = extract(
            "fn add(a: i32, b: i32) i32 {\n    return a + b;\n}\n",
            "src/math.zig",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1, "Expected 1 function");
        assert_eq!(funcs[0].name, "add");
    }

    // ------------------------------------------------------------------
    // 3. Function with call
    // ------------------------------------------------------------------

    #[test]
    fn test_function_with_call() {
        let ctx = extract("fn main() void {\n    compute();\n}\n", "src/main.zig");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edges");
    }

    // ------------------------------------------------------------------
    // 4. Struct declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_struct() {
        let ctx = extract(
            "const Point = struct {\n    x: f64,\n    y: f64,\n};\n",
            "src/point.zig",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Point");
    }

    // ------------------------------------------------------------------
    // 5. Enum declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_enum() {
        let ctx = extract(
            "const Color = enum {\n    red,\n    green,\n    blue,\n};\n",
            "src/color.zig",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
    }

    // ------------------------------------------------------------------
    // 6. Simple variable
    // ------------------------------------------------------------------

    #[test]
    fn test_simple_variable() {
        let ctx = extract("const MAX_SIZE: u32 = 1024;\n", "src/consts.zig");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert!(!vars.is_empty(), "Expected at least 1 variable");
        assert!(vars.iter().any(|v| v.name == "MAX_SIZE"));
    }

    // ------------------------------------------------------------------
    // 7. @import detection
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract("const std = @import(\"std\");\n", "src/main.zig");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for @import");
        assert!(
            imports
                .iter()
                .any(|e| e.target_text.as_deref() == Some("std")),
            "Expected import target 'std'"
        );
    }

    // ------------------------------------------------------------------
    // 8. Test declaration
    // ------------------------------------------------------------------

    #[test]
    fn test_test_declaration() {
        let ctx = extract(
            "test \"simple check\" {\n    try std.testing.expect(1 + 1 == 2);\n}\n",
            "src/test.zig",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        let test_funcs: Vec<_> = funcs.iter().filter(|n| n.name == "simple check").collect();
        assert!(
            !test_funcs.is_empty(),
            "Expected test function 'simple check'"
        );
    }

    // ------------------------------------------------------------------
    // 9. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("fn main() void {}\n", "src/main.zig");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 10. Multiple functions
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_functions() {
        let ctx = extract("fn foo() void {}\nfn bar() void { foo(); }\n", "src/multi.zig");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 2, "Expected 2 functions");
    }

    // ------------------------------------------------------------------
    // 11. Struct with method
    // ------------------------------------------------------------------

    #[test]
    fn test_struct_with_method() {
        let ctx = extract(
            "const Counter = struct {\n    pub fn init() Counter { return Counter{}; }\n    pub fn increment(self: *Counter) void {}\n};\n",
            "src/counter.zig",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(
            funcs.len() >= 2,
            "Expected >=2 functions: init + increment, got {}",
            funcs.len()
        );
    }

    // ------------------------------------------------------------------
    // 12. Error set
    // ------------------------------------------------------------------

    #[test]
    fn test_error_set() {
        let ctx = extract(
            "const AppError = error{\n    NotFound,\n    PermissionDenied,\n};\n",
            "src/errors.zig",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1, "Expected 1 enum for error set");
        assert_eq!(enums[0].name, "AppError");
    }

    // ------------------------------------------------------------------
    // 13. Nested struct
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_struct() {
        let ctx = extract(
            "const Outer = struct {\n    const Inner = struct {\n        value: i32,\n    };\n    inner: Inner,\n};\n",
            "src/nested.zig",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(
            classes.len(),
            2,
            "Expected 2 classes: Outer + Inner, got {}",
            classes.len()
        );
    }

    // ------------------------------------------------------------------
    // 14. Pub function
    // ------------------------------------------------------------------

    #[test]
    fn test_pub_function() {
        let ctx = extract(
            "pub fn publicApi() i32 {\n    return 42;\n}\n",
            "src/api.zig",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "publicApi");
    }
}
