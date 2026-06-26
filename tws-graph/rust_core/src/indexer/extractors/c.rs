//! C language extractor.
//!
//! Extracts symbols and relationships from C source files (`.c`, `.h`)
//! using the tree-sitter-c grammar.
//!
//! # Node kinds produced
//! - `function`: function definition
//! - `struct`: struct definition
//! - `union`: union definition
//! - `enum`: enum definition
//! - `variable`: declarations (globals, function-scoped)
//! - `field`: struct/union member fields
//!
//! # Edge kinds produced
//! - `calls`: function calls (`call_expression`)
//! - `contains`: containment (file -> struct -> field)
//! - `imports`: `#include` directives (three forms)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// C standard library headers — filtered from include edges.
const C_STANDARD_HEADERS: &[&str] = &[
    "assert.h",
    "complex.h",
    "ctype.h",
    "errno.h",
    "fenv.h",
    "float.h",
    "inttypes.h",
    "iso646.h",
    "limits.h",
    "locale.h",
    "math.h",
    "setjmp.h",
    "signal.h",
    "stdalign.h",
    "stdarg.h",
    "stdatomic.h",
    "stdbool.h",
    "stddef.h",
    "stdint.h",
    "stdio.h",
    "stdlib.h",
    "stdnoreturn.h",
    "string.h",
    "tgmath.h",
    "threads.h",
    "time.h",
    "uchar.h",
    "wchar.h",
    "wctype.h",
];

fn is_std_header(name: &str) -> bool {
    C_STANDARD_HEADERS.contains(&name)
}

// ---------------------------------------------------------------------------
// CExtractor
// ---------------------------------------------------------------------------

pub struct CExtractor;

impl Extractor for CExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["c", "h"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["c"]
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
                .unwrap_or("file")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        walk_children(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walker
// ---------------------------------------------------------------------------

fn walk_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "function_definition" => {
                    extract_function(source, child, ctx, parent_id)?;
                }
                "preproc_include" => {
                    extract_include(source, child, ctx, parent_id)?;
                }
                "struct_specifier" => {
                    extract_struct(source, child, ctx, parent_id)?;
                }
                "union_specifier" => {
                    extract_union(source, child, ctx, parent_id)?;
                }
                "enum_specifier" => {
                    extract_enum(source, child, ctx, parent_id)?;
                }
                "declaration" => {
                    extract_declaration(source, child, ctx, parent_id)?;
                }
                _ => {
                    // Recurse into compound_statement, translation_unit, etc.
                    walk_children(source, child, ctx, parent_id)?;
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
) -> anyhow::Result<String> {
    // Navigate: function_definition -> function_declarator -> (pointer_declarator)? -> identifier
    let declarator = node.child_by_field_name("declarator");
    let name = match declarator {
        Some(d) => find_function_name(source, d),
        None => String::new(),
    };

    if name.is_empty() {
        return Ok(String::new());
    }

    let mut extra = HashMap::new();
    let line = node.start_position().row as u32 + 1;

    // Signature
    if let Some(decl) = declarator {
        let sig = format!("{} {}", get_text(source, Some(decl)), get_type_text(source, node));
        extra.insert("signature".to_string(), sig);
    }

    let func_id = ctx.add_node(NodeKind::Function, &name, &node, extra);
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

    ctx.push_scope(&name);
    ctx.push_scope_node(&func_id);

    // Walk body for calls
    if let Some(body) = node.child_by_field_name("body") {
        walk_for_calls(source, body, ctx, &func_id)?;
    }

    ctx.pop_scope();
    Ok(func_id)
}

/// Deep-traverse a function_declarator to find the identifier.
fn find_function_name(source: &[u8], declarator: Node) -> String {
    if declarator.kind() == "identifier" {
        return get_text(source, Some(declarator));
    }
    if declarator.kind() == "function_declarator" {
        if let Some(d) = declarator.child_by_field_name("declarator") {
            return find_function_name(source, d);
        }
    }
    if declarator.kind() == "pointer_declarator" {
        if let Some(d) = declarator.child_by_field_name("declarator") {
            return find_function_name(source, d);
        }
    }
    if declarator.kind() == "parenthesized_declarator" {
        return find_function_name_nested(source, declarator);
    }
    // General recursive search for identifier
    for i in 0..declarator.named_child_count() {
        if let Some(c) = declarator.named_child(i) {
            let name = find_function_name(source, c);
            if !name.is_empty() {
                return name;
            }
        }
    }
    String::new()
}

fn find_function_name_nested(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(c) = node.named_child(i) {
            if c.kind() == "identifier" {
                return get_text(source, Some(c));
            }
            if c.kind() == "function_declarator"
                || c.kind() == "pointer_declarator"
                || c.kind() == "parenthesized_declarator"
            {
                let name = find_function_name(source, c);
                if !name.is_empty() {
                    return name;
                }
            }
        }
    }
    String::new()
}

fn get_type_text(source: &[u8], func_node: Node) -> String {
    if let Some(type_node) = func_node.child_by_field_name("type") {
        get_text(source, Some(type_node))
    } else {
        // Return type may be absent (implicit int), so try storage class or type qualifiers
        for i in 0..func_node.named_child_count() {
            if let Some(c) = func_node.named_child(i) {
                let k = c.kind();
                if k == "primitive_type"
                    || k == "type_identifier"
                    || k == "struct_specifier"
                    || k == "enum_specifier"
                    || k == "union_specifier"
                    || k == "sized_type_specifier"
                    || k == "type_descriptor"
                {
                    return get_text(source, Some(c));
                }
            }
        }
        String::new()
    }
}

// ---------------------------------------------------------------------------
// Struct extraction
// ---------------------------------------------------------------------------

fn extract_struct(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        // Anonymous struct — still process body for fields
        let anon_name = format!("__anon_struct_{}", node.start_position().row);
        let struct_id = ctx.add_node(NodeKind::Struct, &anon_name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

        if let Some(body) = node.child_by_field_name("body") {
            extract_struct_fields(source, body, ctx, &struct_id)?;
        }
        return Ok(struct_id);
    }

    let struct_id = ctx.add_node(NodeKind::Struct, &name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

    ctx.push_scope(&name);
    ctx.push_scope_node(&struct_id);

    if let Some(body) = node.child_by_field_name("body") {
        extract_struct_fields(source, body, ctx, &struct_id)?;
    }

    ctx.pop_scope();
    Ok(struct_id)
}

fn extract_struct_fields(
    source: &[u8],
    body: Node,
    ctx: &mut ExtractionContext,
    struct_id: &str,
) -> anyhow::Result<()> {
    for i in 0..body.named_child_count() {
        if let Some(child) = body.named_child(i) {
            match child.kind() {
                "field_declaration" => {
                    extract_field(source, child, ctx, struct_id)?;
                }
                "function_definition" => {
                    // C structs can have function pointer fields or (GCC extension) methods
                    extract_function(source, child, ctx, struct_id)?;
                }
                _ => {}
            }
        }
    }
    Ok(())
}

fn extract_field(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    if let Some(decl) = node.child_by_field_name("declarator") {
        let name = get_field_name(source, decl);
        if !name.is_empty() {
            let field_id = ctx.add_node(NodeKind::Field, &name, &node, HashMap::new());
            let line = node.start_position().row as u32 + 1;
            ctx.add_edge(parent_id, &field_id, EdgeKind::Contains, line, None);
        }
    }
    // Multiple declarators in one field_declaration (e.g., int a, b, c;)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "field_declarator" {
                let name = get_field_name(source, child);
                if !name.is_empty() {
                    let field_id = ctx.add_node(NodeKind::Field, &name, &node, HashMap::new());
                    let line = node.start_position().row as u32 + 1;
                    ctx.add_edge(parent_id, &field_id, EdgeKind::Contains, line, None);
                }
            }
        }
    }
    Ok(())
}

fn get_field_name(source: &[u8], declarator: Node) -> String {
    if declarator.kind() == "identifier" {
        return get_text(source, Some(declarator));
    }
    if declarator.kind() == "field_identifier" {
        return get_text(source, Some(declarator));
    }
    // pointer_declarator -> identifier
    for i in 0..declarator.named_child_count() {
        if let Some(c) = declarator.named_child(i) {
            let name = get_field_name(source, c);
            if !name.is_empty() {
                return name;
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Union extraction
// ---------------------------------------------------------------------------

fn extract_union(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    let display_name = if name.is_empty() {
        format!("__anon_union_{}", node.start_position().row)
    } else {
        name.clone()
    };

    let union_id = ctx.add_node(NodeKind::Union, &display_name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &union_id, EdgeKind::Contains, line, None);

    ctx.push_scope(&display_name);
    ctx.push_scope_node(&union_id);

    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                if child.kind() == "field_declaration" {
                    extract_field(source, child, ctx, &union_id)?;
                }
            }
        }
    }

    ctx.pop_scope();
    Ok(union_id)
}

// ---------------------------------------------------------------------------
// Enum extraction
// ---------------------------------------------------------------------------

fn extract_enum(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    let display_name = if name.is_empty() {
        format!("__anon_enum_{}", node.start_position().row)
    } else {
        name.clone()
    };

    let enum_id = ctx.add_node(NodeKind::Enum, &display_name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

    // Enum members (enumerators)
    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                if child.kind() == "enumerator" {
                    let mem_name = get_text(source, child.child_by_field_name("name"));
                    if !mem_name.is_empty() {
                        let member_id =
                            ctx.add_node(NodeKind::EnumMember, &mem_name, &child, HashMap::new());
                        let mline = child.start_position().row as u32 + 1;
                        ctx.add_edge(&enum_id, &member_id, EdgeKind::Contains, mline, None);
                    }
                }
            }
        }
    }

    Ok(enum_id)
}

// ---------------------------------------------------------------------------
// Declaration extraction (variables)
// ---------------------------------------------------------------------------

fn extract_declaration(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // If this is a function prototype, skip — handled by function_definition
    // Check if any direct child is function_declarator
    for i in 0..node.named_child_count() {
        if let Some(c) = node.named_child(i) {
            if c.kind() == "function_declarator" {
                // Function prototype — skip (actual definition is function_definition)
                return Ok(());
            }
        }
    }

    // Extract variable declarations
    // Simple declarator: int x;
    // Init declarator: int x = 5;
    // Multiple: int x, y, z;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "init_declarator" => {
                    if let Some(decl) = child.child_by_field_name("declarator") {
                        let var_name = get_variable_name(source, decl);
                        if !var_name.is_empty() {
                            let var_id =
                                ctx.add_node(NodeKind::Variable, &var_name, &child, HashMap::new());
                            let line = node.start_position().row as u32 + 1;
                            ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);
                        }
                    }
                    // Also extract calls in initializer value
                    if let Some(value) = child.child_by_field_name("value") {
                        walk_for_calls(source, value, ctx, parent_id)?;
                    }
                }
                "call_expression" => {
                    // Module-level call (e.g. in initialization)
                    extract_call(source, child, ctx, parent_id)?;
                }
                _ => {}
            }
        }
    }

    Ok(())
}

fn get_variable_name(source: &[u8], declarator: Node) -> String {
    if declarator.kind() == "identifier" {
        return get_text(source, Some(declarator));
    }
    if declarator.kind() == "pointer_declarator"
        || declarator.kind() == "array_declarator"
        || declarator.kind() == "parenthesized_declarator"
    {
        if let Some(d) = declarator.child_by_field_name("declarator") {
            return get_variable_name(source, d);
        }
    }
    for i in 0..declarator.named_child_count() {
        if let Some(c) = declarator.named_child(i) {
            let name = get_variable_name(source, c);
            if !name.is_empty() {
                return name;
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// #include extraction
// ---------------------------------------------------------------------------

fn extract_include(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Three #include forms:
    //   system_lib_string: #include <stdio.h>
    //   string:            #include "myheader.h"
    //   the raw path text is inside a child node

    // Check for path string inside preproc_include
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "system_lib_string" | "string_literal" | "identifier" => {
                    let header = get_text(source, Some(child));
                    // Strip angle brackets / quotes
                    let header = header.trim_matches(|c| c == '<' || c == '>' || c == '"');
                    if !header.is_empty() && !is_std_header(header) {
                        let target_qn = format!("{}::{}", ctx.file_path, header);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Imports,
                            line,
                            Some(&header),
                        );
                    }
                }
                "path" => {
                    let path_text = get_text(source, Some(child));
                    if !path_text.is_empty() {
                        // May be inside angle brackets — trim them
                        let path_text = path_text.trim_matches(|c| c == '<' || c == '>');
                        let target_qn = format!("{}::{}", ctx.file_path, path_text);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Imports,
                            line,
                            Some(&path_text),
                        );
                    }
                }
                "concatenated_string" => {
                    // Macro-based include
                    let header = get_text(source, Some(child));
                    if !header.is_empty() {
                        let target_qn = format!("{}::{}", ctx.file_path, header);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&header));
                    }
                }
                _ => {}
            }
        }
    }

    // Fallback: extract entire preproc_include text minus #include
    let raw = get_text(source, Some(node));
    if let Some(after_include) = raw.strip_prefix("#include") {
        let header = after_include.trim().trim_matches(|c| c == '<' || c == '>' || c == '"');
        if !header.is_empty() && !is_std_header(header) {
            // Check if we already added an edge for this
            let target_qn = format!("{}::{}", ctx.file_path, header);
            let target = hash_id(&ctx.file_path, &target_qn);
            let already_added = ctx
                .result
                .edges
                .iter()
                .any(|e| e.target == target && e.kind == "IMPORTS");
            if !already_added {
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&header));
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn walk_for_calls(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    match node.kind() {
        "call_expression" => {
            extract_call(source, node, ctx, parent_id)?;
            // Also recurse into arguments for nested calls
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "call_expression" {
                        walk_for_calls(source, child, ctx, parent_id)?;
                    } else {
                        walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        // Nested struct/union/enum definitions within function bodies
        "struct_specifier" => {
            extract_struct(source, node, ctx, parent_id)?;
        }
        "union_specifier" => {
            extract_union(source, node, ctx, parent_id)?;
        }
        "enum_specifier" => {
            extract_enum(source, node, ctx, parent_id)?;
        }
        "function_definition" => {
            // Nested function (GCC extension)
            extract_function(source, node, ctx, parent_id)?;
        }
        "declaration" => {
            extract_declaration(source, node, ctx, parent_id)?;
        }
        _ => {
            // Recurse into children for common statement/expression types
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_call(
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
                if !name.is_empty() {
                    let target_qn = format!("{}::{}", ctx.file_path, name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                }
            }
            "field_expression" => {
                // struct->method() or obj.method()
                let field_name = get_text(source, f.child_by_field_name("field"));
                if !field_name.is_empty() {
                    let target_qn = format!("{}::{}", ctx.file_path, field_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        parent_id,
                        &target,
                        EdgeKind::Calls,
                        line,
                        Some(&field_name),
                    );
                }
            }
            "pointer_expression" => {
                // (*func_ptr)(args)
                if let Some(inner) = f.named_child(0) {
                    if inner.kind() == "identifier" {
                        let name = get_text(source, Some(inner));
                        if !name.is_empty() {
                            let target_qn = format!("{}::{}", ctx.file_path, name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                        }
                    }
                }
            }
            "parenthesized_expression" => {
                // (func_ptr)(args) — recurse in
                extract_call(source, f, ctx, parent_id)?;
            }
            _ => {
                // Other callable expressions — use text
                let name = get_text(source, Some(f));
                if !name.is_empty() {
                    let target_qn = format!("{}::{}", ctx.file_path, name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                }
            }
        },
        None => {}
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
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
            .set_language(&tree_sitter_c::LANGUAGE.into())
            .expect("set c language");
        let tree = parser.parse(source, None).expect("parse c source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "c".to_string());
        CExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

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
    // Function tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_function() {
        let ctx = extract("int add(int a, int b) { return a + b; }", "src/test.c");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "add");
    }

    #[test]
    fn test_extract_void_function() {
        let ctx = extract("void do_nothing(void) { }", "src/test.c");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "do_nothing");
    }

    #[test]
    fn test_extract_function_pointer_param() {
        let ctx = extract(
            "void register_callback(void (*callback)(int)) { callback(1); }",
            "src/test.c",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "register_callback");
    }

    // ------------------------------------------------------------------
    // Struct tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct() {
        let ctx = extract("struct Point { int x; int y; };", "src/test.c");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Point");
    }

    #[test]
    fn test_extract_struct_fields() {
        let ctx = extract("struct Data { int id; float value; };", "src/test.c");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2);
        let names: Vec<&str> = fields.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"id"));
        assert!(names.contains(&"value"));
    }

    // ------------------------------------------------------------------
    // Union tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_union() {
        let ctx = extract(
            "union DataUnion { int int_val; float float_val; };",
            "src/test.c",
        );
        let unions = find_nodes(&ctx, NodeKind::Union);
        assert_eq!(unions.len(), 1);
        assert_eq!(unions[0].name, "DataUnion");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2);
    }

    // ------------------------------------------------------------------
    // Enum tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract(
            "enum Color { RED, GREEN, BLUE };",
            "src/test.c",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
        let members = find_nodes(&ctx, NodeKind::EnumMember);
        assert_eq!(members.len(), 3);
        let names: Vec<&str> = members.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"RED"));
        assert!(names.contains(&"GREEN"));
        assert!(names.contains(&"BLUE"));
    }

    // ------------------------------------------------------------------
    // #include tests (three forms)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_include_system() {
        let ctx = extract("#include <myheader.h>\n", "src/test.c");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        // myheader.h is not a standard header, so it should appear
        let targets: Vec<&str> =
            imports
                .iter()
                .filter_map(|e| e.target_text.as_deref())
                .collect();
        assert!(targets.contains(&"myheader.h"), "Expected myheader.h in: {:?}", targets);
    }

    #[test]
    fn test_extract_include_local() {
        let ctx = extract("#include \"myutils.h\"\n", "src/test.c");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> =
            imports
                .iter()
                .filter_map(|e| e.target_text.as_deref())
                .collect();
        assert!(targets.contains(&"myutils.h"), "Expected myutils.h in: {:?}", targets);
    }

    #[test]
    fn test_filter_std_headers() {
        let ctx = extract("#include <stdio.h>\n#include <stdlib.h>\n", "src/test.c");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        // stdio.h and stdlib.h are standard headers — should be filtered
        let targets: Vec<&str> =
            imports
                .iter()
                .filter_map(|e| e.target_text.as_deref())
                .collect();
        assert!(!targets.contains(&"stdio.h"));
        assert!(!targets.contains(&"stdlib.h"));
    }

    // ------------------------------------------------------------------
    // Variable declaration tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_global_variable() {
        let ctx = extract("int global_count = 0;\n", "src/test.c");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "global_count");
    }

    #[test]
    fn test_extract_multiple_variables() {
        let ctx = extract("int a = 1, b = 2, c = 3;\n", "src/test.c");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 3);
        let names: Vec<&str> = vars.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"a"));
        assert!(names.contains(&"b"));
        assert!(names.contains(&"c"));
    }

    // ------------------------------------------------------------------
    // Call tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract(
            "int main() { init(); return 0; }",
            "src/test.c",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"init"), "Expected 'init' in: {:?}", targets);
    }

    #[test]
    fn test_extract_preserves_contains_edge() {
        let ctx = extract("struct User { char *name; };\nint main() { return 0; }\n", "src/test.c");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected at least one CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.c");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_comments() {
        let ctx = extract("/* comment */\n// another\n", "src/comments.c");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_function_with_call_in_body() {
        let ctx = extract(
            "int foo() { bar(); baz(1, 2); return 0; }",
            "src/test.c",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"bar"), "Expected bar in: {:?}", targets);
        assert!(targets.contains(&"baz"), "Expected baz in: {:?}", targets);
    }
}
