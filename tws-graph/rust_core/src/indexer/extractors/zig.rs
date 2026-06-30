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
//! - `references`: @import with variable binding (cross-file resolution)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// ZigExtractor (entry point)
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

        let mut walker = ZigWalker::new();
        walker.walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// ZigWalker — stateful tree walker
// ---------------------------------------------------------------------------

/// Stateful walker that tracks `@import` variable bindings so that
/// subsequent calls through imported names can produce qualified
/// `target_text` for cross-file resolution.
struct ZigWalker {
    /// Maps local variable names to their imported module path.
    /// e.g., `const std = @import("std")` → "std" → "std"
    /// e.g., `const utils = @import("utils.zig")` → "utils" → "utils.zig"
    /// e.g., `const bar = @import("subdir/bar.zig")` → "bar" → "subdir/bar.zig"
    imported_names: HashMap<String, String>,
}

impl ZigWalker {
    fn new() -> Self {
        Self {
            imported_names: HashMap::new(),
        }
    }

    // -------------------------------------------------------------------
    // Tree walking
    // -------------------------------------------------------------------

    fn walk_node(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "function_declaration" => self.extract_function(source, node, ctx, parent_id)?,
            "test_declaration" => self.extract_test(source, node, ctx, parent_id)?,
            "variable_declaration" => self.extract_variable(source, node, ctx, parent_id)?,
            "call_expression" => self.extract_call(source, node, ctx, parent_id)?,
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

    // -------------------------------------------------------------------
    // Function extraction
    // -------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let name = get_function_name(source, node);
        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let line = node.start_position().row as u32 + 1;
        let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&fn_id);

        if let Some(body) = find_child_by_kind(node, "block") {
            self.walk_all_children(source, body, ctx, &fn_id)?;
        }

        ctx.pop_scope();
        Ok(())
    }

    // -------------------------------------------------------------------
    // Test extraction
    // -------------------------------------------------------------------

    fn extract_test(
        &mut self,
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
            self.walk_all_children(source, body, ctx, &test_id)?;
        }
        ctx.pop_scope();

        Ok(())
    }

    // -------------------------------------------------------------------
    // Variable extraction (also handles struct/enum/error_set via assignments
    // and @import with imported_names tracking)
    // -------------------------------------------------------------------

    fn extract_variable(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Get the variable name
        let var_name = get_var_name(source, node);
        if var_name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        // Check if this variable is assigned an @import
        // This also records the mapping in imported_names
        let is_import = self.check_import(source, node, ctx, parent_id, line, &var_name);

        // Check if this variable is assigned a struct/enum/error_set
        let mut is_container = false;
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "struct_declaration" => {
                        self.extract_named_struct(source, child, ctx, parent_id, &var_name, line)?;
                        is_container = true;
                    }
                    "enum_declaration" => {
                        self.extract_named_enum(source, child, ctx, parent_id, &var_name, line)?;
                        is_container = true;
                    }
                    "error_set_declaration" => {
                        self.extract_named_enum(source, child, ctx, parent_id, &var_name, line)?;
                        is_container = true;
                    }
                    _ => {
                        self.walk_node(source, child, ctx, parent_id)?;
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

    // -------------------------------------------------------------------
    // @import detection (enhanced with imported_names and REFERENCES edges)
    // -------------------------------------------------------------------

    /// Check whether this variable declaration contains an @import.
    ///
    /// If found:
    /// 1. Record the mapping `var_name → import_path` in `imported_names`
    /// 2. Create an IMPORTS edge (file → hashed target)
    /// 3. Create a REFERENCES edge for non-external imports (supports cross-file resolution)
    ///
    /// Returns `true` if an @import was detected.
    fn check_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
        var_name: &str,
    ) -> bool {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "call_expression" | "builtin_function" => {
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
                                // Record variable_name → module_path mapping
                                self.imported_names
                                    .insert(var_name.to_string(), import_path.clone());

                                // IMPORTS edge: file → hashed target
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

                                // REFERENCES edge: for cross-file resolution
                                // Format: "import_path::" (module with no symbol,
                                //   so resolver matches the module file)
                                //
                                // External imports ("std", "builtin", etc.)
                                //   still get a REFERENCES edge — the resolver's
                                //   is_external() will classify them correctly.
                                let ref_target_text = if import_path.contains('/')
                                    || import_path.ends_with(".zig")
                                {
                                    // File-based import: "utils.zig::" or "subdir/bar.zig::"
                                    format!("{}.zig{}", import_path.trim_end_matches(".zig"),
                                        if import_path.ends_with(".zig") { "" } else { "" })
                                } else {
                                    // Bare name like "std" — these are external
                                    format!("{}::", import_path)
                                };
                                let ref_target_qn = build_qualified_target(
                                    &ctx.file_path,
                                    &ref_target_text,
                                );
                                let ref_target = hash_id(&ctx.file_path, &ref_target_qn);
                                ctx.add_edge(
                                    &file_id,
                                    &ref_target,
                                    EdgeKind::References,
                                    line,
                                    Some(&ref_target_text),
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

    // -------------------------------------------------------------------
    // Named struct extraction
    // -------------------------------------------------------------------

    fn extract_named_struct(
        &mut self,
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

        self.walk_all_children(source, node, ctx, &class_id)?;

        ctx.pop_scope();
        Ok(())
    }

    // -------------------------------------------------------------------
    // Named enum extraction
    // -------------------------------------------------------------------

    fn extract_named_enum(
        &mut self,
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

        self.walk_all_children(source, node, ctx, &enum_id)?;

        ctx.pop_scope();
        Ok(())
    }

    // -------------------------------------------------------------------
    // Call extraction (enhanced with imported_names for cross-file calls)
    // -------------------------------------------------------------------

    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Try to extract the callee — either a direct identifier or a field_expression.
        // tree-sitter-zig parses `foo()` as call_expression → identifier "foo",
        // but `foo.bar()` as call_expression → field_expression (foo . bar).
        let callee_info = self.extract_callee_info(source, node);

        if let Some((root_name, full_name)) = callee_info {
            if !root_name.is_empty()
                && !root_name.starts_with('@')
                && !is_zig_builtin(&root_name)
            {
                if let Some(module_path) = self.imported_names.get(&root_name) {
                    // This call is on an imported module — use qualified target_text
                    // Format: "module_path::rest_of_call"
                    let qualified = if full_name != root_name {
                        // e.g., root="utils", full="utils.helper"
                        //  → module_path="utils.zig", qualified="utils.zig::helper"
                        let rest = &full_name[root_name.len() + 1..]; // skip "utils."
                        format!("{}::{}", module_path, rest)
                    } else {
                        format!("{}::", module_path)
                    };
                    ctx.add_edge(parent_id, &hash_id(&ctx.file_path, &qualified), EdgeKind::Calls, line, Some(&qualified));
                } else {
                    // Not an imported module — use bare name (existing behavior)
                    let target_qn = build_qualified_target(&ctx.file_path, &full_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full_name));
                }
            }
        }

        self.walk_all_children(source, node, ctx, parent_id)?;
        Ok(())
    }

    /// Extract the callee of a call expression.
    ///
    /// Returns `Some((root_name, full_name))` where:
    /// - `root_name`: the first identifier (e.g. "utils" in "utils.helper")
    /// - `full_name`: the complete expression (e.g. "utils.helper")
    ///
    /// Handles:
    /// - Simple calls: `foo()` → ("foo", "foo")
    /// - Field-access calls: `utils.helper()` → ("utils", "utils.helper")
    fn extract_callee_info(
        &self,
        source: &[u8],
        node: Node,
    ) -> Option<(String, String)> {
        // 1. Direct identifier child: `foo()`
        if let Some(ident) = find_child_by_kind(node, "identifier") {
            let name = get_text(source, Some(ident));
            if !name.is_empty() {
                return Some((name.clone(), name));
            }
        }

        // 2. Field expression: `utils.helper()`
        if let Some(field_expr) = find_child_by_kind(node, "field_expression") {
            return self.extract_field_expression(source, field_expr);
        }

        None
    }

    /// Extract (root_name, full_name) from a field_expression node.
    ///
    /// For `utils.helper`, root_name = "utils", full_name = "utils.helper".
    /// For `std.debug.print`, root_name = "std", full_name = "std.debug.print".
    fn extract_field_expression(
        &self,
        source: &[u8],
        node: Node,
    ) -> Option<(String, String)> {
        // Collect all identifiers in the field_expression tree (left to right)
        let mut parts: Vec<String> = Vec::new();
        self.collect_field_identifiers(source, node, &mut parts);

        if parts.is_empty() {
            return None;
        }

        let root_name = parts[0].clone();
        let full_name = parts.join(".");

        Some((root_name, full_name))
    }

    /// Recursively collect identifiers from a field_expression, left-to-right.
    ///
    /// For `a.b.c`, this collects ["a", "b", "c"].
    /// Handles nesting: `(a.b).c` also collects ["a", "b", "c"].
    fn collect_field_identifiers(
        &self,
        source: &[u8],
        node: Node,
        parts: &mut Vec<String>,
    ) {
        // First, recurse into nested field_expressions (left side of chain)
        if let Some(sub_field) = find_child_by_kind(node, "field_expression") {
            self.collect_field_identifiers(source, sub_field, parts);
        }

        // Then collect identifiers (but skip the ones from nested field_expr which
        // were already collected)
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "identifier" {
                    let name = get_text(source, Some(child));
                    if !name.is_empty() {
                        // Avoid duplicates from nested field_expression recursion
                        if !parts.contains(&name) {
                            parts.push(name);
                        }
                    }
                }
            }
        }
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    fn walk_all_children(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                // Recurse via a fresh walk that won't inherit imported_names
                // (but for simplicity and Zig's scoping where @import is at
                //  file level, sharing imported_names across the file is correct)
                let mut child_walker = ZigWalker {
                    imported_names: self.imported_names.clone(),
                };
                child_walker.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }
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

#[allow(dead_code)]
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

// ---------------------------------------------------------------------------
// Free functions (used across the extractor)
// ---------------------------------------------------------------------------

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

#[allow(dead_code)]
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
    // 7b. @import creates REFERENCES edge (Stage 17)
    // ------------------------------------------------------------------

    #[test]
    fn test_import_creates_references_edge() {
        let ctx = extract(
            "const utils = @import(\"utils.zig\");\n",
            "src/main.zig",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected REFERENCES edge for @import with variable binding");
        // The REFERENCES target_text should include the module path
        assert!(
            refs.iter().any(|e| e.target_text.as_deref()
                .map_or(false, |t| t.contains("utils.zig"))),
            "REFERENCES edge should reference utils.zig"
        );
    }

    // ------------------------------------------------------------------
    // 7c. @import relative path creates REFERENCES edge
    // ------------------------------------------------------------------

    #[test]
    fn test_import_relative_path_references() {
        let ctx = extract(
            "const bar = @import(\"subdir/bar.zig\");\n",
            "src/main.zig",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref()
                .map_or(false, |t| t.contains("subdir/bar.zig"))),
            "REFERENCES edge should reference subdir/bar.zig"
        );
    }

    // ------------------------------------------------------------------
    // 7d. @import std (external) still creates REFERENCES
    // ------------------------------------------------------------------

    #[test]
    fn test_import_std_creates_references() {
        let ctx = extract(
            "const std = @import(\"std\");\n",
            "src/main.zig",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            refs.iter().any(|e| e.target_text.as_deref()
                .map_or(false, |t| t.contains("std"))),
            "REFERENCES edge for std should contain 'std'"
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

    // ------------------------------------------------------------------
    // 15. Stage 17: Imported call uses qualified target_text
    // ------------------------------------------------------------------

    #[test]
    fn test_imported_call_uses_qualified_target_text() {
        let ctx = extract(
            "const utils = @import(\"utils.zig\");\nfn main() void {\n    utils.helper();\n}\n",
            "src/main.zig",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least 1 CALLS edge");
        // The call to utils.helper() should use a qualified target_text
        let qualified_calls: Vec<_> = calls
            .iter()
            .filter(|e| {
                e.target_text
                    .as_deref()
                    .map_or(false, |t| t.contains("utils.zig") && t.contains("helper"))
            })
            .collect();
        assert!(
            !qualified_calls.is_empty(),
            "Expected a call with qualified target_text containing 'utils.zig::helper', got: {:?}",
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect::<Vec<_>>()
        );
    }

    // ------------------------------------------------------------------
    // 16. Stage 17: Non-imported call uses bare name (backward compatibility)
    // ------------------------------------------------------------------

    #[test]
    fn test_non_imported_call_uses_bare_name() {
        let ctx = extract(
            "fn main() void {\n    localHelper();\n}\n",
            "src/main.zig",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected at least 1 CALLS edge");
        // local helper is not imported → bare name
        assert!(
            calls.iter().any(|e| {
                e.target_text
                    .as_deref()
                    .map_or(false, |t| t == "localHelper")
            }),
            "Expected bare target_text 'localHelper' for non-imported call"
        );
    }

    // ------------------------------------------------------------------
    // 17. Stage 17: Multiple @imports
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_imports() {
        let ctx = extract(
            "const std = @import(\"std\");\nconst utils = @import(\"utils.zig\");\n",
            "src/main.zig",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 2, "Expected >=2 IMPORTS edges");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(refs.len() >= 2, "Expected >=2 REFERENCES edges");
    }

    // ------------------------------------------------------------------
    // 18. Stage 17: @import without variable name (standalone)
    // ------------------------------------------------------------------

    #[test]
    fn test_import_without_variable_name() {
        // _ = @import("foo") or just @import("foo") — should still create IMPORTS edge
        // but may not have a variable name to track
        let ctx = extract(
            "const _ = @import(\"builtin.zig\");\n",
            "src/main.zig",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        // The import should still be detected
        assert!(imports.len() >= 1, "Expected IMPORTS edge for standalone @import");
    }
}
