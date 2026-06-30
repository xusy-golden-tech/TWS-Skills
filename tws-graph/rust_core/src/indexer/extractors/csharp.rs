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
//! - `references`: using_directive (cross-file resolution, v7.3.0)
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

/// Check if a namespace/type name is from known external libraries.
fn is_external_ns(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    // System.* and Microsoft.* are external
    if name.starts_with("System.") || name.starts_with("System")
        || name.starts_with("Microsoft.")
    {
        return true;
    }
    // Check against the system namespace list (exact match)
    is_system_ns(name)
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

        let mut walker = Walker::new();
        walker.walk_children(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker with imported_names tracking
// ---------------------------------------------------------------------------

struct Walker {
    /// Maps imported simple name → fully qualified reference text.
    /// e.g. `using Foo.Bar.Baz;` populates "Baz" → "Foo.Bar.Baz::Baz"
    /// e.g. `using MyAlias = Foo.Bar.Baz;` populates "MyAlias" → "Foo.Bar.Baz::Baz"
    imported_names: HashMap<String, String>,
    /// Track class/struct scope for context.
    class_stack: Vec<String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            imported_names: HashMap::new(),
            class_stack: Vec::new(),
        }
    }

    /// Qualify a simple type name using imported_names.
    fn qualify_name(&self, simple_name: &str) -> String {
        // Handle dotted names: for "Foo.Bar", check if "Foo" is imported
        if let Some(dot_pos) = simple_name.find('.') {
            let first = &simple_name[..dot_pos];
            if let Some(qualified) = self.imported_names.get(first) {
                // Replace the first segment with qualified target
                // qualified is like "Some.Namespace.Foo::Foo"
                // We need to extract the module part and append the rest
                let rest = &simple_name[dot_pos + 1..];
                // Extract module part from qualified ref (before ::)
                if let Some(colon_pos) = qualified.find("::") {
                    let module = &qualified[..colon_pos];
                    return format!("{}::{}", module, rest);
                }
                return format!("{}::{}", qualified, rest);
            }
        }

        // Try simple name directly in imported_names
        self.imported_names
            .get(simple_name)
            .cloned()
            .unwrap_or_else(|| simple_name.to_string())
    }

    /// For call targets: "SomeClass.Method()" → qualify "SomeClass" if imported.
    /// Returns the qualified target_text for the call edge.
    fn qualify_call_target(&self, source: &[u8], func: Node) -> String {
        match func.kind() {
            "identifier" => {
                let name = get_text(source, Some(func));
                // Check if the function name itself is from a static import
                if let Some(qualified) = self.imported_names.get(&name) {
                    // Static import: the method is directly callable
                    return qualified.clone();
                }
                name
            }
            "member_access_expression" => {
                let method_name = get_text(source, func.child_by_field_name("name"));
                // tree-sitter-c-sharp uses "expression" field for the left side
                let obj_node = func.child_by_field_name("expression")
                    .or_else(|| func.child_by_field_name("object"));
                if let Some(obj_node) = obj_node {
                    let obj_name = get_text(source, Some(obj_node));
                    // Check if the object is an imported name
                    if let Some(qualified) = self.imported_names.get(&obj_name) {
                        // qualified is like "Foo.Bar.Baz::Baz"
                        // We want "Foo.Bar.Baz::MethodName"
                        if let Some(colon_pos) = qualified.find("::") {
                            let module = &qualified[..colon_pos];
                            return format!("{}::{}", module, method_name);
                        }
                    }
                }
                // Fallback: just the method name
                if !method_name.is_empty() {
                    method_name
                } else {
                    get_text(source, Some(func))
                }
            }
            "generic_name" => get_text(source, Some(func)),
            "conditional_access_expression" => {
                if let Some(name) = func.child_by_field_name("name") {
                    get_text(source, Some(name))
                } else {
                    get_text(source, Some(func))
                }
            }
            "element_access_expression" => {
                let obj = func.child_by_field_name("expression");
                self.qualify_call_target(source, obj.unwrap_or(func))
            }
            _ => get_text(source, Some(func)),
        }
    }

    /// Qualify a type name for instantiation or type references.
    fn qualify_type_name(&self, source: &[u8], type_node: Node) -> String {
        let type_name = match type_node.kind() {
            "identifier" | "generic_name" => get_text(source, Some(type_node)),
            "qualified_name" => {
                let text = get_text(source, Some(type_node));
                // First try to qualify the entire text
                if let Some(qualified) = self.imported_names.get(&text) {
                    if let Some(colon_pos) = qualified.find("::") {
                        return qualified[..colon_pos].to_string()
                            + "::"
                            + text.rsplit('.').next().unwrap_or(&text);
                    }
                }
                // Just the last segment
                text.rsplitn(2, '.').next().unwrap_or(&text).to_string()
            }
            "nullable_type" => {
                if let Some(inner) = type_node.named_child(0) {
                    return self.qualify_type_name(source, inner);
                } else {
                    get_text(source, Some(type_node))
                }
            }
            "array_type" => {
                if let Some(inner) = type_node.child_by_field_name("type") {
                    return self.qualify_type_name(source, inner);
                } else {
                    get_text(source, Some(type_node))
                }
            }
            _ => get_text(source, Some(type_node)),
        };

        // Try to qualify via imported_names
        self.qualify_name(&type_name)
    }
}

impl Walker {
    // ------------------------------------------------------------------
    // Tree walker (main entry point)
    // ------------------------------------------------------------------

    fn walk_children(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let mut i = 0;
        while i < node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "namespace_declaration" => {
                        self.extract_namespace(source, child, ctx, parent_id)?;
                    }
                    "class_declaration" => {
                        self.extract_class(source, child, ctx, parent_id)?;
                    }
                    "interface_declaration" => {
                        self.extract_interface(source, child, ctx, parent_id)?;
                    }
                    "struct_declaration" => {
                        self.extract_struct(source, child, ctx, parent_id)?;
                    }
                    "enum_declaration" => {
                        self.extract_enum(source, child, ctx, parent_id)?;
                    }
                    "method_declaration" => {
                        self.extract_method(source, child, ctx, parent_id, NodeKind::Method)?;
                    }
                    "constructor_declaration" => {
                        self.extract_constructor(source, child, ctx, parent_id)?;
                    }
                    "property_declaration" => {
                        self.extract_property(source, child, ctx, parent_id)?;
                    }
                    "field_declaration" => {
                        self.extract_field_decl(source, child, ctx, parent_id)?;
                    }
                    "using_directive" => {
                        self.extract_using(source, child, ctx, parent_id)?;
                    }
                    "attribute_list" => {
                        self.extract_attribute_list(source, child, ctx, parent_id)?;
                    }
                    "global_statement" => {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                    _ => {
                        // Recurse for compilation_unit, etc.
                        self.walk_children(source, child, ctx, parent_id)?;
                    }
                }
            }
            i += 1;
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Namespace
    // ------------------------------------------------------------------

    fn extract_namespace(
        &mut self,
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
            self.walk_children(source, body, ctx, &ns_id)?;
        }

        ctx.pop_scope();
        Ok(ns_id)
    }

    // ------------------------------------------------------------------
    // Class declaration
    // ------------------------------------------------------------------

    fn extract_class(
        &mut self,
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
            self.extract_base_types(source, bases, ctx, &name, line);
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

        self.class_stack.push(name.clone());

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body(source, body, ctx, &class_id)?;
        }

        self.class_stack.pop();

        ctx.pop_scope();
        Ok(class_id)
    }

    fn extract_base_types(
        &self,
        source: &[u8],
        bases_node: Node,
        ctx: &mut ExtractionContext,
        class_name: &str,
        line: u32,
    ) {
        for i in 0..bases_node.named_child_count() {
            if let Some(base) = bases_node.named_child(i) {
                let base_name = self.qualify_type_name(source, base);
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

    // ------------------------------------------------------------------
    // Interface
    // ------------------------------------------------------------------

    fn extract_interface(
        &mut self,
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
            self.walk_body(source, body, ctx, &iface_id)?;
        }

        ctx.pop_scope();
        Ok(iface_id)
    }

    // ------------------------------------------------------------------
    // Struct
    // ------------------------------------------------------------------

    fn extract_struct(
        &mut self,
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
        let struct_id = ctx.add_node(NodeKind::Struct, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "struct");
        ctx.push_scope_node(&struct_id);

        self.class_stack.push(name.clone());

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body(source, body, ctx, &struct_id)?;
        }

        self.class_stack.pop();

        ctx.pop_scope();
        Ok(struct_id)
    }

    // ------------------------------------------------------------------
    // Enum
    // ------------------------------------------------------------------

    fn extract_enum(
        &self,
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

    // ------------------------------------------------------------------
    // Body walker (class/struct/interface body)
    // ------------------------------------------------------------------

    fn walk_body(
        &mut self,
        source: &[u8],
        body: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..body.named_child_count() {
            if let Some(child) = body.named_child(i) {
                match child.kind() {
                    "method_declaration" => {
                        self.extract_method(source, child, ctx, parent_id, NodeKind::Method)?;
                    }
                    "constructor_declaration" => {
                        self.extract_constructor(source, child, ctx, parent_id)?;
                    }
                    "property_declaration" => {
                        self.extract_property(source, child, ctx, parent_id)?;
                    }
                    "field_declaration" => {
                        self.extract_field_decl(source, child, ctx, parent_id)?;
                    }
                    "class_declaration" => {
                        self.extract_class(source, child, ctx, parent_id)?;
                    }
                    "struct_declaration" => {
                        self.extract_struct(source, child, ctx, parent_id)?;
                    }
                    "interface_declaration" => {
                        self.extract_interface(source, child, ctx, parent_id)?;
                    }
                    "enum_declaration" => {
                        self.extract_enum(source, child, ctx, parent_id)?;
                    }
                    "attribute_list" => {
                        self.extract_attribute_list(source, child, ctx, parent_id)?;
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Method
    // ------------------------------------------------------------------

    fn extract_method(
        &mut self,
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
            self.extract_type_params(source, type_params, &method_id, ctx, line);
        }
        // Also from return type
        if let Some(ret_type) = node.child_by_field_name("return_type") {
            self.extract_type_refs_from_node(source, ret_type, &method_id, ctx, line);
        }

        ctx.push_scope(&name);
        ctx.push_scope_node(&method_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_for_calls(source, body, ctx, &method_id)?;
        }

        ctx.pop_scope();
        Ok(method_id)
    }

    // ------------------------------------------------------------------
    // Constructor
    // ------------------------------------------------------------------

    fn extract_constructor(
        &mut self,
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
            self.walk_for_calls(source, body, ctx, &ctor_id)?;
        }

        ctx.pop_scope();
        Ok(ctor_id)
    }

    // ------------------------------------------------------------------
    // Property
    // ------------------------------------------------------------------

    fn extract_property(
        &mut self,
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
                            self.walk_for_calls(source, body, ctx, &prop_id)?;
                        }
                    } else if is_set {
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
                            self.walk_for_calls(source, body, ctx, &prop_id)?;
                        }
                    }
                }
            }
        }

        // Expression-bodied property: => expression
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "arrow_expression_clause" {
                    self.walk_for_calls(source, child, ctx, &prop_id)?;
                }
            }
        }

        Ok(prop_id)
    }

    // ------------------------------------------------------------------
    // Field
    // ------------------------------------------------------------------

    fn extract_field_decl(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let field_names = collect_field_names(source, node);
        for field_name in &field_names {
            let fid = ctx.add_node(NodeKind::Field, field_name, &node, HashMap::new());
            let line = node.start_position().row as u32 + 1;
            ctx.add_edge(parent_id, &fid, EdgeKind::Contains, line, None);
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // using directive (enhanced for cross-file resolution, v7.3.0)
    // ------------------------------------------------------------------

    fn extract_using(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Detect if this is an alias: `using Alias = Target;`
        let mut is_alias = false;
        for i in 0..node.child_count() {
            if let Some(c) = node.child(i) {
                if c.kind() == "=" {
                    is_alias = true;
                    break;
                }
            }
        }

        // Detect if this has the `static` keyword
        let mut is_static = false;
        for i in 0..node.child_count() {
            if let Some(c) = node.child(i) {
                if c.kind() == "static" {
                    is_static = true;
                    break;
                }
            }
        }

        if is_alias {
            // `using MyAlias = Some.Namespace.Type;`
            let alias_name = get_text(source, node.child_by_field_name("name"));
            // The target is the unnamed qualified_name or identifier after "="
            let target_name = find_using_target(source, node);

            if alias_name.is_empty() || target_name.is_empty() {
                return Ok(());
            }

            if is_external_ns(&target_name) || is_system_ns(&target_name) {
                return Ok(());
            }

            // Create IMPORTS edge for the target
            let target_qn = format!("{}::{}", ctx.file_path, target_name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&target_name));

            // Create REFERENCES edge: "Some.Namespace.Type::Type"
            let last_seg = target_name.rsplit('.').next().unwrap_or(&target_name);
            let ref_text = format!("{}::{}", target_name, last_seg);
            let ref_qn = format!("{}::{}", ctx.file_path, ref_text);
            let ref_target = hash_id(&ctx.file_path, &ref_qn);
            ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_text));

            // Populate imported_names: alias → qualified ref
            self.imported_names
                .insert(alias_name, ref_text);

            return Ok(());
        }

        // Non-alias using: `using Namespace.Type;` or `using static Namespace.Type;`
        let ns_name = get_text(source, node.child_by_field_name("name"));
        let ns_name = if ns_name.is_empty() {
            find_using_name(source, node)
        } else {
            ns_name
        };

        if ns_name.is_empty() || is_system_ns(&ns_name) || is_external_ns(&ns_name) {
            return Ok(());
        }

        // Create IMPORTS edge
        let target_qn = format!("{}::{}", ctx.file_path, ns_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&ns_name));

        // Create REFERENCES edge: the last segment is the imported name
        let last_seg = ns_name.rsplit('.').next().unwrap_or(&ns_name);
        let ref_text = format!("{}::{}", ns_name, last_seg);
        let ref_qn = format!("{}::{}", ctx.file_path, ref_text);
        let ref_target = hash_id(&ctx.file_path, &ref_qn);
        ctx.add_edge(parent_id, &ref_target, EdgeKind::References, line, Some(&ref_text));

        // For static imports, also store the type name in imported_names
        if is_static {
            self.imported_names
                .insert(last_seg.to_string(), ref_text);
        } else {
            self.imported_names
                .insert(last_seg.to_string(), ref_text);
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Attribute list (decorates)
    // ------------------------------------------------------------------

    fn extract_attribute_list(
        &self,
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

    // ------------------------------------------------------------------
    // Type parameters (generics)
    // ------------------------------------------------------------------

    fn extract_type_params(
        &self,
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
        &self,
        source: &[u8],
        type_node: Node,
        parent_id: &str,
        ctx: &mut ExtractionContext,
        line: u32,
    ) {
        let type_name = get_text(source, Some(type_node));
        if type_name.contains('<') {
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
            "invocation_expression" => {
                self.extract_invocation(source, node, ctx, parent_id)?;
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "object_creation_expression" => {
                self.extract_new_object(source, node, ctx, parent_id)?;
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "assignment_expression" => {
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
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "method_declaration" => {
                self.extract_method(source, node, ctx, parent_id, NodeKind::Method)?;
            }
            _ => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
    }

    fn extract_invocation(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let func = node.child_by_field_name("function");
        let line = node.start_position().row as u32 + 1;

        match func {
            Some(f) => {
                let call_name = self.qualify_call_target(source, f);
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

    fn extract_new_object(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if let Some(type_node) = node.child_by_field_name("type") {
            let type_name = self.qualify_type_name(source, type_node);
            if !type_name.is_empty() {
                let target_qn = format!("{}::{}", ctx.file_path, type_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Instantiates, line, Some(&type_name));
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helper functions (standalone)
// ---------------------------------------------------------------------------

/// Find the target name in an alias using directive.
/// For `using Alias = Foo.Bar.Baz;`, the target is the qualified_name
/// or identifier after `=` that does NOT have field name "name".
fn find_using_target(source: &[u8], node: Node) -> String {
    let mut found_eq = false;
    for i in 0..node.child_count() {
        if let Some(c) = node.child(i) {
            if c.kind() == "=" {
                found_eq = true;
                continue;
            }
            if found_eq {
                match c.kind() {
                    "qualified_name" | "identifier" | "generic_name" => {
                        // In alias form, the name field child is BEFORE the =,
                        // so this unnamed qualified_name is the target
                        if node.field_name_for_child(i as u32) != Some("name") {
                            return get_text(source, Some(c));
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    String::new()
}

/// Find the imported name from a using_directive node that doesn't have
/// a `name` field on the using_directive itself.
fn find_using_name(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "qualified_name" | "identifier" | "generic_name" => {
                    // Skip children with field "name" (these are alias names)
                    if node.field_name_for_child(i as u32) != Some("name") {
                        return get_text(source, Some(child));
                    }
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
    // Class tests (existing)
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
    // Interface tests (existing)
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
    // Struct tests (existing)
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
    // Enum tests (existing)
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
    // Namespace tests (existing)
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
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
    }

    // ------------------------------------------------------------------
    // Property tests (existing)
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
    // Constructor tests (existing)
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
    // using directive tests (existing + new)
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
    // NEW: using directive REFERENCES edges (v7.3.0)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_using_creates_ref_edge() {
        let ctx = extract(
            "using MyApp.Services.UserService;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.iter().any(|t| t.contains("UserService")),
            "Expected UserService in REFERENCES, got: {:?}", targets
        );
    }

    #[test]
    fn test_extract_using_ref_target_text_format() {
        let ctx = extract(
            "using MyApp.Services.UserService;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // target_text should be "MyApp.Services.UserService::UserService"
        assert!(
            targets.contains(&"MyApp.Services.UserService::UserService"),
            "Expected 'MyApp.Services.UserService::UserService', got: {:?}", targets
        );
    }

    #[test]
    fn test_extract_using_static_creates_ref_edge() {
        let ctx = extract(
            "using static MyApp.Utils.Helpers;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.iter().any(|t| t.contains("Helpers")),
            "Expected Helpers in REFERENCES, got: {:?}", targets
        );
    }

    #[test]
    fn test_extract_using_alias_creates_ref_edge() {
        let ctx = extract(
            "using MyAlias = MyApp.Services.UserService;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.iter().any(|t| t.contains("UserService")),
            "Expected UserService in REFERENCES for alias, got: {:?}", targets
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let import_targets: Vec<&str> =
            imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            import_targets.contains(&"MyApp.Services.UserService"),
            "Expected IMPORTS edge for alias target, got: {:?}", import_targets
        );
    }

    #[test]
    fn test_extract_using_short_namespace_creates_ref() {
        // Short namespace (2 segments) should still create REFERENCES
        let ctx = extract(
            "using MyApp.Utils;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.contains(&"MyApp.Utils::Utils"),
            "Expected 'MyApp.Utils::Utils', got: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // NEW: imported_names call qualification (v7.3.0)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_imported_call_qualified() {
        // `using Foo.Bar;` then `Bar.Method()` → calls target_text = "Foo.Bar::Method"
        let ctx = extract(
            "using MyApp.Utils;\nclass Test { void Run() { Utils.DoSomething(); } }",
            "src/test.cs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.iter().any(|t| *t == "MyApp.Utils::DoSomething"),
            "Expected 'MyApp.Utils::DoSomething', got: {:?}", targets
        );
    }

    #[test]
    fn test_extract_imported_type_instantiation_qualified() {
        // `using Foo.Bar.Baz;` then `new Baz()` → target_text = "Foo.Bar.Baz::Baz"
        let ctx = extract(
            "using MyApp.Services.UserService;\nclass Test { void Run() { var x = new UserService(); } }",
            "src/test.cs",
        );
        let insts = find_edges(&ctx, EdgeKind::Instantiates);
        let targets: Vec<&str> =
            insts.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.iter().any(|t| t.contains("MyApp.Services.UserService")),
            "Expected qualified instantiation, got: {:?}", targets
        );
    }

    #[test]
    fn test_extract_non_imported_call_uses_bare_name() {
        // Local method call without import → bare name
        let ctx = extract(
            "class Test { void Run() { LocalMethod(); } void LocalMethod() { } }",
            "src/test.cs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> =
            calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(
            targets.contains(&"LocalMethod"),
            "Expected bare 'LocalMethod', got: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // NEW: System/Microsoft using still filtered
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_system_using_no_refs() {
        let ctx = extract(
            "using System;\nusing System.Collections.Generic;\nusing Microsoft.Extensions.Logging;\nclass Test { }",
            "src/test.cs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> =
            refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // No REFERENCES for system namespaces
        assert!(
            !targets.iter().any(|t| t.contains("System")),
            "Should not have System REFERENCES, got: {:?}", targets
        );
        assert!(
            !targets.iter().any(|t| t.contains("Microsoft")),
            "Should not have Microsoft REFERENCES, got: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Call tests (existing)
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
    // Attribute tests (existing)
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
    // Generic type tests (existing)
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
    // Edge cases (existing)
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
