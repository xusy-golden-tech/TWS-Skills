//! C# language extractor.
//!
//! Extracts symbols and relationships from C# source files (`.cs`)
//! using the tree-sitter-c-sharp grammar.
//!
//! # Node kinds produced
//! - `class`: class declaration
//! - `method`: method declaration
//! - `struct`: struct declaration
//! - `interface`: interface declaration
//! - `namespace`: namespace declaration
//! - `enum`: enum declaration
//! - `field`: field declaration
//! - `property`: property declaration
//! - `constructor`: constructor declaration (stored as method)
//! - `file`: the file node
//!
//! # Edge kinds produced
//! - `calls`: invocation_expression
//! - `contains`: containment hierarchy
//! - `imports`: using_directive
//! - `decorates`: attribute_list on declarations
//! - `type_ref`: generic type parameters
//! - `reads`: inferred from property get accessor
//! - `writes`: inferred from property set accessor

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// C# system namespaces — filtered from using edges.
const CSHARP_SYSTEM_NS: &[&str] = &[
    "System",
    "System.Collections",
    "System.Collections.Generic",
    "System.Collections.Concurrent",
    "System.Collections.Immutable",
    "System.ComponentModel",
    "System.Data",
    "System.Diagnostics",
    "System.Drawing",
    "System.Dynamic",
    "System.Globalization",
    "System.IO",
    "System.Linq",
    "System.Linq.Expressions",
    "System.Net",
    "System.Net.Http",
    "System.Numerics",
    "System.Reflection",
    "System.Resources",
    "System.Runtime",
    "System.Security",
    "System.Text",
    "System.Text.Json",
    "System.Text.RegularExpressions",
    "System.Threading",
    "System.Threading.Tasks",
    "System.Timers",
    "System.Xml",
    "System.Xml.Linq",
    "Microsoft.Extensions.DependencyInjection",
    "Microsoft.Extensions.Logging",
];

fn is_system_ns(name: &str) -> bool {
    CSHARP_SYSTEM_NS.contains(&name)
}

// ---------------------------------------------------------------------------
// CSharpExtractor
// ---------------------------------------------------------------------------

pub struct CSharpExtractor;

impl Extractor for CSharpExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cs"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["csharp"]
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
                "namespace_declaration" => {
                    extract_namespace(source, child, ctx, parent_id)?;
                }
                "class_declaration" => {
                    extract_class(source, child, ctx, parent_id)?;
                }
                "interface_declaration" => {
                    extract_interface(source, child, ctx, parent_id)?;
                }
                "struct_declaration" => {
                    extract_struct(source, child, ctx, parent_id)?;
                }
                "enum_declaration" => {
                    extract_enum(source, child, ctx, parent_id)?;
                }
                "method_declaration" => {
                    extract_method(source, child, ctx, parent_id, NodeKind::Method)?;
                }
                "constructor_declaration" => {
                    extract_constructor(source, child, ctx, parent_id)?;
                }
                "property_declaration" => {
                    extract_property(source, child, ctx, parent_id)?;
                }
                "field_declaration" => {
                    extract_field_decl(source, child, ctx, parent_id)?;
                }
                "using_directive" => {
                    extract_using(source, child, ctx, parent_id)?;
                }
                "attribute_list" => {
                    extract_attribute_list(source, child, ctx, parent_id)?;
                }
                "global_statement" => {
                    // Top-level statements (C# 9+)
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
                _ => {
                    // Recurse for compilation_unit, etc.
                    for j in 0..child.named_child_count() {
                        if let Some(sub) = child.named_child(j) {
                            walk_children(source, sub, ctx, parent_id)?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Namespace
// ---------------------------------------------------------------------------

fn extract_namespace(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let ns_id = ctx.add_node(NodeKind::Namespace, &name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &ns_id, EdgeKind::Contains, line, None);

    ctx.push_scope(&name);
    ctx.push_scope_node(&ns_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_children(source, body, ctx, &ns_id)?;
    }

    ctx.pop_scope();
    Ok(ns_id)
}

// ---------------------------------------------------------------------------
// Class declaration
// ---------------------------------------------------------------------------

fn extract_class(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;

    let extra = HashMap::new();

    // Collect attribute_list decorators
    let decorator_names = collect_attributes_from_node(source, node);

    // Base list (extends)
    if let Some(bases) = node.child_by_field_name("bases") {
        extract_base_types(source, bases, ctx, &name, line);
    }

    let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    // Add decorates edges for attributes on the class
    for dec_name in &decorator_names {
        let target_qn = format!("{}::{}", ctx.file_path, dec_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(&class_id, &target, EdgeKind::Decorates, line, Some(dec_name));
    }

    ctx.push_scope_with_kind(&name, "class");
    ctx.push_scope_node(&class_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_body(source, body, ctx, &class_id)?;
    }

    ctx.pop_scope();
    Ok(class_id)
}

fn extract_base_types(
    source: &[u8],
    bases_node: Node,
    ctx: &mut ExtractionContext,
    class_name: &str,
    line: u32,
) {
    for i in 0..bases_node.named_child_count() {
        if let Some(base) = bases_node.named_child(i) {
            let base_name = resolve_type_name(source, base);
            if !base_name.is_empty() {
                let target_qn = format!("{}::{}", ctx.file_path, base_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(
                    &hash_id(&ctx.file_path, &format!("{}::{}", ctx.file_path, class_name)),
                    &target,
                    EdgeKind::Extends,
                    line,
                    Some(&base_name),
                );
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------

fn extract_interface(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let iface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &iface_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "interface");
    ctx.push_scope_node(&iface_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_body(source, body, ctx, &iface_id)?;
    }

    ctx.pop_scope();
    Ok(iface_id)
}

// ---------------------------------------------------------------------------
// Struct
// ---------------------------------------------------------------------------

fn extract_struct(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let node_text = get_text(source, Some(node));
    // Print children with field names
    for i in 0..node.child_count() {
        if let Some(c) = node.child(i) {
            let fn_ = node.field_name_for_child(i as u32);
        }
    }
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let struct_id = ctx.add_node(NodeKind::Struct, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&name, "struct");
    ctx.push_scope_node(&struct_id);

    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.named_child_count() {
            if let Some(bc) = body.named_child(i) {
            }
        }
        walk_body(source, body, ctx, &struct_id)?;
    } else {
    }

    ctx.pop_scope();
    Ok(struct_id)
}

// ---------------------------------------------------------------------------
// Enum
// ---------------------------------------------------------------------------

fn extract_enum(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                if child.kind() == "enum_member_declaration" {
                    let mem_name = get_text(source, child.child_by_field_name("name"));
                    if !mem_name.is_empty() {
                        let mem_id =
                            ctx.add_node(NodeKind::EnumMember, &mem_name, &child, HashMap::new());
                        let mline = child.start_position().row as u32 + 1;
                        ctx.add_edge(&enum_id, &mem_id, EdgeKind::Contains, mline, None);
                    }
                }
            }
        }
    }

    Ok(enum_id)
}

// ---------------------------------------------------------------------------
// Body walker (class/struct/interface body)
// ---------------------------------------------------------------------------

fn walk_body(
    source: &[u8],
    body: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..body.named_child_count() {
        if let Some(child) = body.named_child(i) {
            match child.kind() {
                "method_declaration" => {
                    extract_method(source, child, ctx, parent_id, NodeKind::Method)?;
                }
                "constructor_declaration" => {
                    extract_constructor(source, child, ctx, parent_id)?;
                }
                "property_declaration" => {
                    extract_property(source, child, ctx, parent_id)?;
                }
                "field_declaration" => {
                    extract_field_decl(source, child, ctx, parent_id)?;
                }
                "class_declaration" => {
                    extract_class(source, child, ctx, parent_id)?;
                }
                "struct_declaration" => {
                    extract_struct(source, child, ctx, parent_id)?;
                }
                "interface_declaration" => {
                    extract_interface(source, child, ctx, parent_id)?;
                }
                "enum_declaration" => {
                    extract_enum(source, child, ctx, parent_id)?;
                }
                "attribute_list" => {
                    extract_attribute_list(source, child, ctx, parent_id)?;
                }
                _ => {}
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Method
// ---------------------------------------------------------------------------

fn extract_method(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    _kind: NodeKind,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let mut extra = HashMap::new();
    let line = node.start_position().row as u32 + 1;

    // Signature
    if let Some(params) = node.child_by_field_name("parameters") {
        let sig = get_text(source, Some(params));
        let ret = get_text(source, node.child_by_field_name("return_type"));
        extra.insert("signature".to_string(), format!("{}({}) -> {}", name, sig, ret));
    }

    // Collect attribute_list decorators
    let mut decorator_names: Vec<String> = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "attribute_list" {
                let decs = collect_attribute_names(source, child);
                decorator_names.extend(decs);
            }
        }
    }
    if !decorator_names.is_empty() {
        if let Ok(json) = serde_json::to_string(&decorator_names) {
            extra.insert("decorators".to_string(), json);
        }
    }

    let method_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
    ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

    // Add decorates edges for attributes
    for dec_name in &decorator_names {
        let target_qn = format!("{}::{}", ctx.file_path, dec_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(&method_id, &target, EdgeKind::Decorates, line, Some(dec_name));
    }

    // Extract type_ref from generic type parameters
    if let Some(type_params) = node.child_by_field_name("type_parameters") {
        extract_type_params(source, type_params, &method_id, ctx, line);
    }
    // Also from return type
    if let Some(ret_type) = node.child_by_field_name("return_type") {
        extract_type_refs_from_node(source, ret_type, &method_id, ctx, line);
    }

    ctx.push_scope(&name);
    ctx.push_scope_node(&method_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_for_calls(source, body, ctx, &method_id)?;
    }

    ctx.pop_scope();
    Ok(method_id)
}

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

fn extract_constructor(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let ctor_id = ctx.add_node(NodeKind::Method, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &ctor_id, EdgeKind::Contains, line, None);

    // Constructor instantiates the class
    if let Some(class_name) = ctx
        .result
        .nodes
        .iter()
        .rev()
        .find(|n| n.kind == "class" || n.kind == "struct")
        .map(|n| n.name.clone())
    {
        let target_qn = format!("{}::{}", ctx.file_path, class_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(&ctor_id, &target, EdgeKind::Instantiates, line, Some(&class_name));
    }

    ctx.push_scope(&name);
    ctx.push_scope_node(&ctor_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_for_calls(source, body, ctx, &ctor_id)?;
    }

    ctx.pop_scope();
    Ok(ctor_id)
}

// ---------------------------------------------------------------------------
// Property
// ---------------------------------------------------------------------------

fn extract_property(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let prop_id = ctx.add_node(NodeKind::Property, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &prop_id, EdgeKind::Contains, line, None);

    // Analyze accessors: get -> READS, set -> WRITES
    if let Some(accessors) = node.child_by_field_name("accessors") {
        for i in 0..accessors.named_child_count() {
            if let Some(acc) = accessors.named_child(i) {
                let acc_kind = acc.kind();
                let acc_text = get_text(source, Some(acc));
                let is_get = acc_kind == "get_accessor_declaration"
                    || acc_text.trim_start().starts_with("get");
                let is_set = acc_kind == "set_accessor_declaration"
                    || acc_text.trim_start().starts_with("set");

                if is_get {
                    // get → reads this property
                    let target_qn = format!("{}::{}", ctx.file_path, name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        &prop_id,
                        &target,
                        EdgeKind::Reads,
                        line,
                        Some(&format!("get_{}", name)),
                    );
                    if let Some(body) = acc.child_by_field_name("body") {
                        walk_for_calls(source, body, ctx, &prop_id)?;
                    }
                } else if is_set {
                    // set → writes this property
                    let target_qn = format!("{}::{}", ctx.file_path, name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        &prop_id,
                        &target,
                        EdgeKind::Writes,
                        line,
                        Some(&format!("set_{}", name)),
                    );
                    if let Some(body) = acc.child_by_field_name("body") {
                        walk_for_calls(source, body, ctx, &prop_id)?;
                    }
                }
            }
        }
    }

    // Expression-bodied property: => expression
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "arrow_expression_clause" => {
                    walk_for_calls(source, child, ctx, &prop_id)?;
                }
                _ => {}
            }
        }
    }

    Ok(prop_id)
}

// ---------------------------------------------------------------------------
// Field
// ---------------------------------------------------------------------------

fn extract_field_decl(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // field_declaration contains variable_declaration which contains variable_declarator
    let field_names = collect_field_names(source, node);
    for field_name in &field_names {
        let fid = ctx.add_node(NodeKind::Field, field_name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &fid, EdgeKind::Contains, line, None);
    }
    Ok(())
}

fn collect_field_names(source: &[u8], node: Node) -> Vec<String> {
    let mut names = Vec::new();
    match node.kind() {
        "variable_declarator" => {
            let name = get_text(source, node.child_by_field_name("name"));
            if !name.is_empty() {
                names.push(name);
            }
        }
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    names.extend(collect_field_names(source, child));
                }
            }
        }
    }
    names
}

// ---------------------------------------------------------------------------
// using directive
// ---------------------------------------------------------------------------

fn extract_using(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // using namespace; or using static Type; or using alias = Type;
    // The name field on using_directive depends on the using form
    let ns_name = if let Some(name_node) = node.child_by_field_name("name") {
        get_text(source, Some(name_node))
    } else {
        // Try to find a qualified_name or identifier child
        find_using_name(source, node)
    };

    if !ns_name.is_empty() && !is_system_ns(&ns_name) {
        let target_qn = format!("{}::{}", ctx.file_path, ns_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&ns_name));
    }

    Ok(())
}

fn find_using_name(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "qualified_name" | "identifier" | "generic_name" => {
                    return get_text(source, Some(child));
                }
                _ => {
                    let name = find_using_name(source, child);
                    if !name.is_empty() {
                        return name;
                    }
                }
            }
        }
    }
    String::new()
}

// ---------------------------------------------------------------------------
// Attribute list (decorates)
// ---------------------------------------------------------------------------

fn extract_attribute_list(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;
    let decorators = collect_attribute_names(source, node);

    for dec_name in &decorators {
        let target_qn = format!("{}::{}", ctx.file_path, dec_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Decorates, line, Some(dec_name));
    }

    Ok(())
}

/// Collect attribute names from attribute_list children of a declaration node.
fn collect_attributes_from_node(source: &[u8], node: Node) -> Vec<String> {
    let mut names = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "attribute_list" {
                names.extend(collect_attribute_names(source, child));
            }
        }
    }
    names
}

fn collect_attribute_names(source: &[u8], attr_list: Node) -> Vec<String> {
    let mut names = Vec::new();
    for i in 0..attr_list.named_child_count() {
        if let Some(child) = attr_list.named_child(i) {
            if child.kind() == "attribute" {
                let name = get_text(source, child.child_by_field_name("name"));
                if !name.is_empty() {
                    names.push(name);
                }
            }
        }
    }
    names
}

// ---------------------------------------------------------------------------
// Type parameters (generics)
// ---------------------------------------------------------------------------

fn extract_type_params(
    source: &[u8],
    type_params: Node,
    parent_id: &str,
    ctx: &mut ExtractionContext,
    line: u32,
) {
    for i in 0..type_params.named_child_count() {
        if let Some(param) = type_params.named_child(i) {
            if param.kind() == "type_parameter" {
                let name = get_text(source, Some(param));
                if !name.is_empty() {
                    let target_qn = format!("{}::{}", ctx.file_path, name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(parent_id, &target, EdgeKind::TypeRef, line, Some(&name));
                }
            }
        }
    }
}

fn extract_type_refs_from_node(
    source: &[u8],
    type_node: Node,
    parent_id: &str,
    ctx: &mut ExtractionContext,
    line: u32,
) {
    // Look for generic types like List<MyType>
    let type_name = get_text(source, Some(type_node));
    if type_name.contains('<') {
        // Has generic arguments — extract the inner types
        for i in 0..type_node.named_child_count() {
            if let Some(child) = type_node.named_child(i) {
                if child.kind() == "type_argument_list" {
                    for j in 0..child.named_child_count() {
                        if let Some(arg) = child.named_child(j) {
                            let arg_name = get_text(source, Some(arg));
                            if !arg_name.is_empty() && !is_builtin_type(&arg_name) {
                                let target_qn = format!("{}::{}", ctx.file_path, arg_name);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(
                                    parent_id,
                                    &target,
                                    EdgeKind::TypeRef,
                                    line,
                                    Some(&arg_name),
                                );
                            }
                        }
                    }
                }
            }
        }
    }
}

fn is_builtin_type(name: &str) -> bool {
    matches!(
        name,
        "int"
            | "long"
            | "short"
            | "byte"
            | "float"
            | "double"
            | "decimal"
            | "bool"
            | "char"
            | "string"
            | "void"
            | "object"
            | "var"
            | "dynamic"
            | "nint"
            | "nuint"
    )
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
        "invocation_expression" => {
            extract_invocation(source, node, ctx, parent_id)?;
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
        "object_creation_expression" => {
            extract_new_object(source, node, ctx, parent_id)?;
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
        "assignment_expression" => {
            // Check left side for writes
            if let Some(left) = node.child_by_field_name("left") {
                if left.kind() == "identifier" || left.kind() == "member_access_expression" {
                    let left_name = resolve_name(source, left);
                    if !left_name.is_empty() {
                        let target_qn = format!("{}::{}", ctx.file_path, left_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        let line = node.start_position().row as u32 + 1;
                        ctx.add_edge(parent_id, &target, EdgeKind::Writes, line, Some(&left_name));
                    }
                }
            }
            // Recurse both sides
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
        "class_declaration" => {
            extract_class(source, node, ctx, parent_id)?;
        }
        "method_declaration" => {
            extract_method(source, node, ctx, parent_id, NodeKind::Method)?;
        }
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_invocation(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let func = node.child_by_field_name("function");
    let line = node.start_position().row as u32 + 1;

    match func {
        Some(f) => {
            let call_name = resolve_call_target(source, f);
            if !call_name.is_empty() {
                let target_qn = format!("{}::{}", ctx.file_path, call_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&call_name));
            }
        }
        None => {}
    }

    Ok(())
}

fn resolve_call_target(source: &[u8], func: Node) -> String {
    match func.kind() {
        "identifier" => get_text(source, Some(func)),
        "member_access_expression" => {
            let method_name = get_text(source, func.child_by_field_name("name"));
            if !method_name.is_empty() {
                return method_name;
            }
            // Fallback: full dotted name
            get_text(source, Some(func))
        }
        "generic_name" => {
            // Func<T>(args)
            get_text(source, Some(func))
        }
        "conditional_access_expression" => {
            // obj?.Method()
            if let Some(name) = func.child_by_field_name("name") {
                get_text(source, Some(name))
            } else {
                get_text(source, Some(func))
            }
        }
        "element_access_expression" => {
            // this[i] or dict[key]
            let obj = func.child_by_field_name("expression");
            resolve_call_target(source, obj.unwrap_or(func))
        }
        _ => get_text(source, Some(func)),
    }
}

fn extract_new_object(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    if let Some(type_node) = node.child_by_field_name("type") {
        let type_name = resolve_type_name(source, type_node);
        if !type_name.is_empty() {
            let target_qn = format!("{}::{}", ctx.file_path, type_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Instantiates, line, Some(&type_name));
        }
    }

    Ok(())
}

fn resolve_name(source: &[u8], node: Node) -> String {
    match node.kind() {
        "identifier" => get_text(source, Some(node)),
        "member_access_expression" => {
            let name = get_text(source, node.child_by_field_name("name"));
            if !name.is_empty() {
                return name;
            }
            get_text(source, Some(node))
        }
        _ => get_text(source, Some(node)),
    }
}

fn resolve_type_name(source: &[u8], type_node: Node) -> String {
    match type_node.kind() {
        "identifier" | "generic_name" => get_text(source, Some(type_node)),
        "qualified_name" => {
            let text = get_text(source, Some(type_node));
            text.rsplitn(2, '.').next().unwrap_or(&text).to_string()
        }
        "nullable_type" => {
            // Type? — extract inner type
            if let Some(inner) = type_node.named_child(0) {
                resolve_type_name(source, inner)
            } else {
                get_text(source, Some(type_node))
            }
        }
        "array_type" => {
            if let Some(inner) = type_node.child_by_field_name("type") {
                resolve_type_name(source, inner)
            } else {
                get_text(source, Some(type_node))
            }
        }
        _ => get_text(source, Some(type_node)),
    }
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
            .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
            .expect("set csharp language");
        let tree = parser.parse(source, None).expect("parse csharp source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "csharp".to_string());
        CSharpExtractor
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
    // Class tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract(
            "class MyClass { }",
            "src/test.cs",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_method_in_class() {
        let ctx = extract(
            "class MyClass { void MyMethod() { } }",
            "src/test.cs",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "MyMethod");
    }

    #[test]
    fn test_extract_class_with_inheritance() {
        let ctx = extract(
            "class Child : Base { }",
            "src/test.cs",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Child");
    }

    // ------------------------------------------------------------------
    // Interface tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface() {
        let ctx = extract(
            "interface IMyInterface { void DoSomething(); }",
            "src/test.cs",
        );
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "IMyInterface");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "DoSomething");
    }

    // ------------------------------------------------------------------
    // Struct tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct() {
        let ctx = extract(
            "struct Point { public int X; public int Y; }",
            "src/test.cs",
        );
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Point");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2);
    }

    // ------------------------------------------------------------------
    // Enum tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract(
            "enum Color { Red, Green, Blue }",
            "src/test.cs",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
        let members = find_nodes(&ctx, NodeKind::EnumMember);
        assert_eq!(members.len(), 3);
    }

    // ------------------------------------------------------------------
    // Namespace tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_namespace() {
        let ctx = extract(
            "namespace MyApp { class Program { } }",
            "src/test.cs",
        );
        let namespaces = find_nodes(&ctx, NodeKind::Namespace);
        assert_eq!(namespaces.len(), 1);
        assert_eq!(namespaces[0].name, "MyApp");
        // Class should be contained
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
    }

    // ------------------------------------------------------------------
    // Property tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_property() {
        let ctx = extract(
            "class User { public string Name { get; set; } }",
            "src/test.cs",
        );
        let props = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(props.len(), 1);
        assert_eq!(props[0].name, "Name");
    }

    #[test]
    fn test_property_get_generates_reads() {
        let ctx = extract(
            "class User { public string Name { get; set; } }",
            "src/test.cs",
        );
        let reads = find_edges(&ctx, EdgeKind::Reads);
        let targets: Vec<&str> =
            reads.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| t.contains("get_")), "Expected get_READS edge");
    }

    #[test]
    fn test_property_set_generates_writes() {
        let ctx = extract(
            "class User { public int Age { get; set; } }",
            "src/test.cs",
        );
        let writes = find_edges(&ctx, EdgeKind::Writes);
        let targets: Vec<&str> =
            writes.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| t.contains("set_")), "Expected set_WRITES edge");
    }

    // ------------------------------------------------------------------
    // Constructor tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_constructor() {
        let ctx = extract(
            "class MyClass { public MyClass() { } }",
            "src/test.cs",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        let names: Vec<&str> = methods.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"MyClass"), "Constructor not found in {:?}", names);
    }

    // ------------------------------------------------------------------
    // using directive tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_using_directive() {
        let ctx = extract(
            "using MyApp.Utils;\nclass Test { }",
            "src/test.cs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> =
            imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"MyApp.Utils"), "Expected MyApp.Utils in: {:?}", targets);
    }

    #[test]
    fn test_filter_system_using() {
        let ctx = extract(
            "using System;\nusing System.Collections.Generic;\nclass Test { }",
            "src/test.cs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> =
            imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(!targets.contains(&"System"));
        assert!(!targets.contains(&"System.Collections.Generic"));
    }

    // ------------------------------------------------------------------
    // Call tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_method_call() {
        let ctx = extract(
            "class C { void A() { B(); } void B() { } }",
            "src/test.cs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"B"), "Expected B calls in: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Attribute tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_attribute() {
        let ctx = extract(
            "[Obsolete]\nclass OldClass { }",
            "src/test.cs",
        );
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> =
            decorates.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"Obsolete"), "Expected Obsolete attribute");
    }

    // ------------------------------------------------------------------
    // Generic type tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_generic_class() {
        let ctx = extract(
            "class Box<T> { public T Value; }",
            "src/test.cs",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Box");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.cs");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_field_declaration() {
        let ctx = extract(
            "class Data { private int id; private string name; }",
            "src/test.cs",
        );
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2);
    }
}
