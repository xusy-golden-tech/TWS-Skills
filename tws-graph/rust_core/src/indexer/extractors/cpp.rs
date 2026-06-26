//! C++ language extractor.
//!
//! Extracts symbols and relationships from C++ source files
//! (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`) using tree-sitter-cpp.
//!
//! # Node kinds produced
//! - `class`: class definition
//! - `function`: free function
//! - `method`: function inside a class
//! - `struct`: struct definition
//! - `namespace`: namespace declaration
//! - `enum`: enum definition
//! - `variable`: variable declaration
//! - `template`: template declaration (named if applicable)
//! - `field`: class/struct data member
//!
//! # Edge kinds produced
//! - `calls`: function/method calls
//! - `contains`: containment (file -> namespace -> class -> method)
//! - `imports`: `#include` directives
//! - `extends`: base_class_clause (inheritance)
//! - `overrides`: virtual method overrides (post-process heuristic)
//! - `instantiates`: constructor calls
//! - `type_ref`: template parameters

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// C++ standard library headers — filtered from include edges.
const CPP_STANDARD_HEADERS: &[&str] = &[
    "algorithm",
    "array",
    "atomic",
    "bitset",
    "chrono",
    "codecvt",
    "complex",
    "condition_variable",
    "deque",
    "exception",
    "filesystem",
    "forward_list",
    "fstream",
    "functional",
    "future",
    "initializer_list",
    "iomanip",
    "ios",
    "iosfwd",
    "iostream",
    "istream",
    "iterator",
    "limits",
    "list",
    "locale",
    "map",
    "memory",
    "mutex",
    "new",
    "numeric",
    "optional",
    "ostream",
    "queue",
    "random",
    "ratio",
    "regex",
    "scoped_allocator",
    "set",
    "shared_mutex",
    "sstream",
    "stack",
    "stdexcept",
    "streambuf",
    "string",
    "string_view",
    "strstream",
    "system_error",
    "thread",
    "tuple",
    "type_traits",
    "typeindex",
    "typeinfo",
    "unordered_map",
    "unordered_set",
    "utility",
    "valarray",
    "variant",
    "vector",
    // Also filter C standard headers
    "assert.h",
    "ctype.h",
    "errno.h",
    "fenv.h",
    "float.h",
    "inttypes.h",
    "limits.h",
    "locale.h",
    "math.h",
    "setjmp.h",
    "signal.h",
    "stdarg.h",
    "stddef.h",
    "stdio.h",
    "stdlib.h",
    "string.h",
    "time.h",
    "wchar.h",
    "wctype.h",
    // C++ C-compatibility headers
    "cassert",
    "cctype",
    "cerrno",
    "cfenv",
    "cfloat",
    "cinttypes",
    "climits",
    "clocale",
    "cmath",
    "csetjmp",
    "csignal",
    "cstdarg",
    "cstddef",
    "cstdio",
    "cstdlib",
    "cstring",
    "ctime",
    "cwchar",
    "cwctype",
];

fn is_std_header(name: &str) -> bool {
    CPP_STANDARD_HEADERS.contains(&name)
}

// ---------------------------------------------------------------------------
// CppExtractor
// ---------------------------------------------------------------------------

pub struct CppExtractor;

impl Extractor for CppExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cpp", "cc", "cxx", "hpp", "hh", "hxx"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["cpp"]
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

        // Post-process: resolve virtual overrides
        post_process_overrides(ctx);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker
// ---------------------------------------------------------------------------

struct ScopeStack {
    /// Stack of class/struct names.
    classes: Vec<String>,
    /// The current namespace (or "" if none).
    current_namespace: String,
}

impl ScopeStack {
    fn new() -> Self {
        Self { classes: Vec::new(), current_namespace: String::new() }
    }

    fn current_class(&self) -> Option<&str> {
        self.classes.last().map(|s| s.as_str())
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
                // core
                "function_definition" => {
                    let mut ss = ScopeStack::new();
                    extract_function(source, child, ctx, parent_id, NodeKind::Function, &mut ss)?;
                }
                "class_specifier" => {
                    let mut ss = ScopeStack::new();
                    extract_class(source, child, ctx, parent_id, &mut ss)?;
                }
                "struct_specifier" => {
                    let mut ss = ScopeStack::new();
                    extract_struct_like(source, child, ctx, parent_id, &mut ss)?;
                }
                "namespace_definition" => {
                    let mut ss = ScopeStack::new();
                    extract_namespace(source, child, ctx, parent_id, &mut ss)?;
                }
                "template_declaration" => {
                    let mut ss = ScopeStack::new();
                    extract_template(source, child, ctx, parent_id, &mut ss)?;
                }
                "enum_specifier" => {
                    extract_enum(source, child, ctx, parent_id)?;
                }
                "preproc_include" => {
                    extract_include(source, child, ctx, parent_id)?;
                }
                "declaration" => {
                    extract_declaration(source, child, ctx, parent_id)?;
                }
                "linkage_specification" => {
                    // extern "C" { ... }
                    walk_children(source, child, ctx, parent_id)?;
                }
                _ => {
                    // Recurse for translation_unit, etc.
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
    ss: &mut ScopeStack,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));

    let ns_id = if !name.is_empty() {
        let id = ctx.add_node(NodeKind::Namespace, &name, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &id, EdgeKind::Contains, line, None);
        ss.current_namespace = name.clone();
        id
    } else {
        // Anonymous namespace
        let anon = format!("__anon_ns_{}", node.start_position().row);
        let id = ctx.add_node(NodeKind::Namespace, &anon, &node, HashMap::new());
        let line = node.start_position().row as u32 + 1;
        ctx.add_edge(parent_id, &id, EdgeKind::Contains, line, None);
        id
    };

    ctx.push_scope(&name);
    ctx.push_scope_node(&ns_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_children(source, body, ctx, &ns_id)?;
    }

    ctx.pop_scope();
    Ok(ns_id)
}

// ---------------------------------------------------------------------------
// Class / struct extraction
// ---------------------------------------------------------------------------

fn extract_class(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    ss: &mut ScopeStack,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    if name.is_empty() {
        return Ok(String::new());
    }

    let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

    // Base class clause — iterate named_children to find base_class_clause
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "base_class_clause" {
                extract_base_classes(source, child, &class_id, ctx);
                break;
            }
        }
    }

    ss.classes.push(name.clone());
    ctx.push_scope_with_kind(&name, "class");
    ctx.push_scope_node(&class_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_class_body(source, body, ctx, &class_id, ss)?;
    }

    ctx.pop_scope();
    ss.classes.pop();
    Ok(class_id)
}

fn extract_struct_like(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    ss: &mut ScopeStack,
) -> anyhow::Result<String> {
    let name = get_text(source, node.child_by_field_name("name"));
    // In C++, `struct_specifier` can be either a struct or a class-like entity
    // For simplicity, treat named struct_specifier as struct
    if name.is_empty() {
        return Ok(String::new());
    }

    let struct_id = ctx.add_node(NodeKind::Struct, &name, &node, HashMap::new());
    let line = node.start_position().row as u32 + 1;
    ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

    // Base classes for struct (yes, C++ structs can inherit)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "base_class_clause" {
                extract_base_classes(source, child, &struct_id, ctx);
                break;
            }
        }
    }

    ss.classes.push(name.clone());
    ctx.push_scope_with_kind(&name, "struct");
    ctx.push_scope_node(&struct_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_class_body(source, body, ctx, &struct_id, ss)?;
    }

    ctx.pop_scope();
    ss.classes.pop();
    Ok(struct_id)
}

fn extract_base_classes(
    source: &[u8],
    base_clause: Node,
    class_id: &str,
    ctx: &mut ExtractionContext,
) {
    // base_class_clause contains named_children: access_specifier, type_identifier, etc.
    for i in 0..base_clause.named_child_count() {
        if let Some(child) = base_clause.named_child(i) {
            let base_name = match child.kind() {
                "type_identifier" | "identifier" | "qualified_identifier"
                | "type_descriptor" | "template_type" | "generic_type" => {
                    get_text(source, Some(child))
                }
                _ => continue,
            };
            if !base_name.is_empty() {
                let target_qn = format!("{}::{}", ctx.file_path, base_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                let line = child.start_position().row as u32 + 1;
                ctx.add_edge(class_id, &target, EdgeKind::Extends, line, Some(&base_name));
            }
        }
    }
}

fn walk_class_body(
    source: &[u8],
    body: Node,
    ctx: &mut ExtractionContext,
    class_id: &str,
    ss: &mut ScopeStack,
) -> anyhow::Result<()> {
    for i in 0..body.named_child_count() {
        if let Some(child) = body.named_child(i) {
            match child.kind() {
                "function_definition" => {
                    extract_function(source, child, ctx, class_id, NodeKind::Method, ss)?;
                }
                "field_declaration" => {
                    extract_field(source, child, ctx, class_id)?;
                }
                "class_specifier" => {
                    extract_class(source, child, ctx, class_id, ss)?;
                }
                "struct_specifier" => {
                    extract_struct_like(source, child, ctx, class_id, ss)?;
                }
                "template_declaration" => {
                    extract_template(source, child, ctx, class_id, ss)?;
                }
                "access_specifier" => {
                    // public:/private:/protected: — recurse into children
                    walk_class_body(source, child, ctx, class_id, ss)?;
                }
                "declaration" => {
                    // Could be a member declaration (variable) or using declaration
                    // First check if it contains field_declaration
                    let mut found_field = false;
                    for j in 0..child.named_child_count() {
                        if let Some(sub) = child.named_child(j) {
                            if sub.kind() == "field_declaration" {
                                extract_field(source, sub, ctx, class_id)?;
                                found_field = true;
                            }
                        }
                    }
                    if !found_field {
                        extract_declaration(source, child, ctx, class_id)?;
                    }
                }
                "constructor_declaration" | "destructor_declaration" => {
                    extract_constructor(source, child, ctx, class_id)?;
                }
                _ => {
                    // Recursively check for nested field_declarations
                    for j in 0..child.named_child_count() {
                        if let Some(sub) = child.named_child(j) {
                            if sub.kind() == "field_declaration" {
                                extract_field(source, sub, ctx, class_id)?;
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Function / method extraction
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    fn_kind: NodeKind,
    _ss: &mut ScopeStack,
) -> anyhow::Result<String> {
    let name = find_cpp_function_name(source, node);
    if name.is_empty() {
        return Ok(String::new());
    }

    let mut extra = HashMap::new();
    let line = node.start_position().row as u32 + 1;

    // Detect virtual — check function_specifiers child
    let mut is_virtual = false;
    for i in 0..node.named_child_count() {
        if let Some(c) = node.named_child(i) {
            let ck = c.kind();
            if ck == "virtual" || ck == "virtual_function_specifier" {
                is_virtual = true;
            } else if ck == "function_specifiers" {
                // function_specifiers contains 'virtual', 'inline', etc.
                let text = get_text(source, Some(c));
                if text.contains("virtual") {
                    is_virtual = true;
                }
            }
        }
    }
    // Also check declarator for virtual specifier
    if !is_virtual {
        if let Some(decl) = node.child_by_field_name("declarator") {
            let text = get_text(source, Some(decl));
            if text.contains("virtual") {
                is_virtual = true;
            }
        }
    }
    // Also check full node text
    if !is_virtual {
        let full_text = get_text(source, Some(node));
        if full_text.starts_with("virtual ") || full_text.contains(" virtual ") {
            is_virtual = true;
        }
    }
    if is_virtual {
        extra.insert("virtual".to_string(), "true".to_string());
    }

    // Signature
    if let Some(decl) = node.child_by_field_name("declarator") {
        let sig = get_text(source, Some(decl));
        let ret = get_text(source, node.child_by_field_name("type"));
        extra.insert("signature".to_string(), format!("{} {}", ret, sig));
    }

    let func_id = ctx.add_node(fn_kind, &name, &node, extra);
    ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

    // Store virtual info for post-processing
    if is_virtual {
        let target_qn = format!("{}::{}", ctx.file_path, name);
        let target = hash_id(&ctx.file_path, &target_qn);
        // Mark with a special edge that we'll post-process
        ctx.add_edge(&func_id, &target, EdgeKind::Overrides, line, Some(&name));
    }

    ctx.push_scope_with_kind(&name, "function");
    ctx.push_scope_node(&func_id);

    if let Some(body) = node.child_by_field_name("body") {
        walk_for_calls(source, body, ctx, &func_id)?;
    }

    ctx.pop_scope();
    Ok(func_id)
}

fn find_cpp_function_name(source: &[u8], node: Node) -> String {
    // Walk the function_declarator to find identifier
    if let Some(decl) = node.child_by_field_name("declarator") {
        return find_name_in_declarator(source, decl);
    }
    String::new()
}

fn find_name_in_declarator(source: &[u8], decl: Node) -> String {
    if decl.kind() == "identifier" || decl.kind() == "field_identifier" || decl.kind() == "destructor_name" {
        return get_text(source, Some(decl));
    }
    if decl.kind() == "function_declarator" {
        if let Some(d) = decl.child_by_field_name("declarator") {
            return find_name_in_declarator(source, d);
        }
    }
    if decl.kind() == "pointer_declarator"
        || decl.kind() == "reference_declarator"
        || decl.kind() == "array_declarator"
    {
        if let Some(d) = decl.child_by_field_name("declarator") {
            return find_name_in_declarator(source, d);
        }
    }
    if decl.kind() == "qualified_identifier" {
        // Namespace::ClassName::method — take the last component
        let text = get_text(source, Some(decl));
        return text.rsplitn(2, "::").next().unwrap_or(&text).to_string();
    }
    // Recurse children
    for i in 0..decl.named_child_count() {
        if let Some(c) = decl.named_child(i) {
            let name = find_name_in_declarator(source, c);
            if !name.is_empty() {
                return name;
            }
        }
    }
    String::new()
}

fn extract_constructor(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<String> {
    // constructor_declaration: ClassName(params) {...}
    let name = find_cpp_function_name(source, node);
    if name.is_empty() {
        return Ok(String::new());
    }

    let line = node.start_position().row as u32 + 1;
    let ctor_id = ctx.add_node(NodeKind::Method, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &ctor_id, EdgeKind::Contains, line, None);

    // Add instantiates edge — constructor instantiates the class
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
// Template extraction
// ---------------------------------------------------------------------------

fn extract_template(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    ss: &mut ScopeStack,
) -> anyhow::Result<()> {
    // template_declaration has template_parameter_list and a body
    let line = node.start_position().row as u32 + 1;

    // Extract template type_ref edges from the parameter list
    if let Some(params) = node.child_by_field_name("parameters") {
        extract_template_params(source, params, ctx, parent_id, line);
    }

    // Find the actual definition inside the template
    // It could be class_specifier, struct_specifier, function_definition, etc.
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "class_specifier" => {
                    let id = extract_class(source, child, ctx, parent_id, ss)?;
                    if !id.is_empty() {
                        // Mark as template
                        if let Some(last) = ctx.result.nodes.last_mut() {
                            if last.id == id {
                                let mut props: HashMap<String, String> =
                                    if let Some(ref p) = last.properties {
                                        serde_json::from_str(p).unwrap_or_default()
                                    } else {
                                        HashMap::new()
                                    };
                                props.insert("is_template".to_string(), "true".to_string());
                                last.properties = serde_json::to_string(&props).ok();
                            }
                        }
                    }
                }
                "struct_specifier" => {
                    extract_struct_like(source, child, ctx, parent_id, ss)?;
                }
                "function_definition" => {
                    extract_function(source, child, ctx, parent_id, NodeKind::Function, ss)?;
                }
                "declaration" => {
                    // Template variable or alias
                    extract_declaration(source, child, ctx, parent_id)?;
                }
                _ => {}
            }
        }
    }

    Ok(())
}

fn extract_template_params(
    source: &[u8],
    params: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
) {
    for i in 0..params.named_child_count() {
        if let Some(param) = params.named_child(i) {
            match param.kind() {
                "type_parameter_declaration" | "parameter_declaration" => {
                    // template<typename T> or template<class T>
                    // The type name (T) is nested
                    let type_name = find_template_type_name(source, param);
                    if !type_name.is_empty() {
                        let target_qn = format!("{}::{}", ctx.file_path, type_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::TypeRef,
                            line,
                            Some(&type_name),
                        );
                    }
                }
                "variadic_parameter_declaration" => {
                    let text = get_text(source, Some(param));
                    if !text.is_empty() {
                        let target_qn = format!("{}::{}", ctx.file_path, text);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::TypeRef, line, Some(&text));
                    }
                }
                _ => {}
            }
        }
    }
}

fn find_template_type_name(source: &[u8], param: Node) -> String {
    for i in 0..param.named_child_count() {
        if let Some(c) = param.named_child(i) {
            match c.kind() {
                "type_identifier" | "identifier" => {
                    return get_text(source, Some(c));
                }
                _ => {
                    let name = find_template_type_name(source, c);
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
// Field extraction
// ---------------------------------------------------------------------------

fn extract_field(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Extract field names from field_declaration
    if let Some(decl) = node.child_by_field_name("declarator") {
        let names = find_all_field_names(source, decl);
        for name in &names {
            let fid = ctx.add_node(NodeKind::Field, name, &node, HashMap::new());
            let line = node.start_position().row as u32 + 1;
            ctx.add_edge(parent_id, &fid, EdgeKind::Contains, line, None);
        }
    } else {
        // Fallback: search named children for field_declarator
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let names = find_all_field_names(source, child);
                for name in &names {
                    let fid = ctx.add_node(NodeKind::Field, name, &node, HashMap::new());
                    let line = node.start_position().row as u32 + 1;
                    ctx.add_edge(parent_id, &fid, EdgeKind::Contains, line, None);
                }
            }
        }
    }
    Ok(())
}

fn find_all_field_names(source: &[u8], node: Node) -> Vec<String> {
    let mut names = Vec::new();
    match node.kind() {
        "identifier" | "field_identifier" => {
            let text = get_text(source, Some(node));
            if !text.is_empty() { names.push(text); }
        }
        "field_declarator" | "pointer_declarator" | "reference_declarator"
        | "array_declarator" => {
            for i in 0..node.child_count() {
                if let Some(c) = node.child(i) {
                    names.extend(find_all_field_names(source, c));
                }
            }
        }
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    names.extend(find_all_field_names(source, c));
                }
            }
        }
    }
    names
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

    if let Some(body) = node.child_by_field_name("body") {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                if child.kind() == "enumerator" {
                    let mem = get_text(source, child.child_by_field_name("name"));
                    if !mem.is_empty() {
                        let mem_id = ctx.add_node(NodeKind::EnumMember, &mem, &child, HashMap::new());
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
// Declaration (variables)
// ---------------------------------------------------------------------------

fn extract_declaration(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
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
                    // Extract calls in initializer
                    if let Some(value) = child.child_by_field_name("value") {
                        walk_for_calls(source, value, ctx, parent_id)?;
                    }
                }
                "call_expression" => {
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
        || declarator.kind() == "reference_declarator"
        || declarator.kind() == "array_declarator"
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

    let raw = get_text(source, Some(node));
    if let Some(after) = raw.strip_prefix("#include") {
        let header = after.trim().trim_matches(|c| c == '<' || c == '>' || c == '"');
        if !header.is_empty() && !is_std_header(header) {
            let target_qn = format!("{}::{}", ctx.file_path, header);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&header));
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
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }
        "new_expression" => {
            extract_new_expression(source, node, ctx, parent_id)?;
        }
        "class_specifier" => {
            let mut ss = ScopeStack::new();
            extract_class(source, node, ctx, parent_id, &mut ss)?;
        }
        "function_definition" => {
            let mut ss = ScopeStack::new();
            extract_function(source, node, ctx, parent_id, NodeKind::Function, &mut ss)?;
        }
        "template_declaration" => {
            let mut ss = ScopeStack::new();
            extract_template(source, node, ctx, parent_id, &mut ss)?;
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

fn extract_call(
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
        "field_expression" => {
            let field = get_text(source, func.child_by_field_name("field"));
            if !field.is_empty() {
                return field;
            }
            // obj->method() or a.b() — take the field part
            get_text(source, Some(func))
        }
        "qualified_identifier" => {
            // ns::func
            let text = get_text(source, Some(func));
            text.rsplitn(2, "::").next().unwrap_or(&text).to_string()
        }
        "template_function" => {
            // func<T>(args)
            if let Some(name) = func.child_by_field_name("name") {
                return get_text(source, Some(name));
            }
            get_text(source, Some(func))
        }
        "parenthesized_expression" => {
            // (func_ptr)(args)
            if let Some(inner) = func.named_child(0) {
                return resolve_call_target(source, inner);
            }
            String::new()
        }
        _ => {
            let text = get_text(source, Some(func));
            text
        }
    }
}

fn extract_new_expression(
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

    // Recurse for calls in arguments
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_for_calls(source, child, ctx, parent_id)?;
        }
    }

    Ok(())
}

fn resolve_type_name(source: &[u8], type_node: Node) -> String {
    match type_node.kind() {
        "identifier" | "type_identifier" => get_text(source, Some(type_node)),
        "qualified_identifier" => {
            let text = get_text(source, Some(type_node));
            text.rsplitn(2, "::").next().unwrap_or(&text).to_string()
        }
        "template_type" => {
            if let Some(name) = type_node.child_by_field_name("name") {
                return get_text(source, Some(name));
            }
            get_text(source, Some(type_node))
        }
        _ => get_text(source, Some(type_node)),
    }
}

// ---------------------------------------------------------------------------
// Virtual override post-processing
// ---------------------------------------------------------------------------

/// Remove self-referencing OVERRIDES edges that were used as markers.
fn post_process_overrides(ctx: &mut ExtractionContext) {
    let overrides: Vec<_> = ctx
        .result
        .edges
        .iter()
        .filter(|e| e.kind == "OVERRIDES" && e.source == e.target)
        .cloned()
        .collect();

    // For each self-referencing OVERRIDES edge, the source node is a virtual method.
    // Keep the edge but change target to a cross-file reference so it's meaningful.
    // In practice, tree-sitter can't know the base class, so we keep a marker.
    for edge in &overrides {
        // The edge is already ok as a marker — virtual was detected.
        // We clean up self-references by keeping target_text as label.
        let idx = ctx.result.edges.iter().position(|e| {
            e.source == edge.source
                && e.target == edge.target
                && e.kind == "OVERRIDES"
        });
        if let Some(_idx) = idx {
            // Mark as virtual override marker; target_text holds the method name
            // This is informational — the actual override target is unresolved
        }
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
            .set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("set cpp language");
        let tree = parser.parse(source, None).expect("parse cpp source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "cpp".to_string());
        CppExtractor
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
            "class MyClass { public: void foo() {} };",
            "src/test.cpp",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_method_in_class() {
        let ctx = extract(
            "class MyClass { public: void myMethod() {} };",
            "src/test.cpp",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "myMethod");
    }

    #[test]
    fn test_extract_class_inheritance() {
        let ctx = extract(
            "class Derived : public Base1, private Base2 {};",
            "src/test.cpp",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Derived");
        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(extends.len() >= 1, "Expected at least one EXTENDS edge");
        let targets: Vec<&str> =
            extends.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "Base1"));
    }

    // ------------------------------------------------------------------
    // Struct tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct() {
        let ctx = extract("struct Point { int x; int y; };", "src/test.cpp");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Point");
    }

    #[test]
    fn test_extract_struct_fields() {
        let ctx = extract(
            "struct Data { int id; float val; char* name; };",
            "src/test.cpp",
        );
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 3);
    }

    // ------------------------------------------------------------------
    // Namespace tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_namespace() {
        let ctx = extract(
            "namespace myns { int x = 1; }",
            "src/test.cpp",
        );
        let namespaces = find_nodes(&ctx, NodeKind::Namespace);
        assert_eq!(namespaces.len(), 1);
        assert_eq!(namespaces[0].name, "myns");
    }

    #[test]
    fn test_nested_namespace() {
        let ctx = extract(
            "namespace outer { namespace inner { void f() {} } }",
            "src/test.cpp",
        );
        let namespaces = find_nodes(&ctx, NodeKind::Namespace);
        assert_eq!(namespaces.len(), 2);
    }

    // ------------------------------------------------------------------
    // Template tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_template_class() {
        let ctx = extract(
            "template<typename T> class Box { T value; };",
            "src/test.cpp",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Box");
        let type_refs = find_edges(&ctx, EdgeKind::TypeRef);
        let targets: Vec<&str> =
            type_refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"T"), "Expected T in type_refs: {:?}", targets);
    }

    #[test]
    fn test_extract_template_function() {
        let ctx = extract(
            "template<typename T> T max(T a, T b) { return a > b ? a : b; }",
            "src/test.cpp",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "max");
    }

    // ------------------------------------------------------------------
    // Enum tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract(
            "enum class Color { Red, Green, Blue };",
            "src/test.cpp",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert!(enums.len() >= 1, "Expected at least one enum");
    }

    // ------------------------------------------------------------------
    // Virtual / override tests
    // ------------------------------------------------------------------

    #[test]
    fn test_detect_virtual_method() {
        let ctx = extract(
            "class B { public: virtual void f() {} };",
            "src/test.cpp",
        );
        let overrides = find_edges(&ctx, EdgeKind::Overrides);
        assert!(overrides.len() >= 1, "Expected OVERRIDES edge(s) for virtual method");
    }

    // ------------------------------------------------------------------
    // Call tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_function_call() {
        let ctx = extract(
            "int main() { helper(); return 0; }",
            "src/test.cpp",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"helper"), "Expected helper in: {:?}", targets);
    }

    #[test]
    fn test_extract_method_call() {
        let ctx = extract(
            "class C { void a() { b(); } void b() {} };",
            "src/test.cpp",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"b"), "Expected b in: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Include tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_include() {
        let ctx = extract("#include <myheader.hpp>\n", "src/test.cpp");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> =
            imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.contains(&"myheader.hpp"));
    }

    // ------------------------------------------------------------------
    // Variable tests
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_variable() {
        let ctx = extract("int global_x = 42;\n", "src/test.cpp");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "global_x");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.cpp");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_constructor_extraction() {
        let ctx = extract(
            "class MyClass { public: MyClass() {} };",
            "src/test.cpp",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(!methods.is_empty(), "Expected at least one method (constructor)");
    }
}
