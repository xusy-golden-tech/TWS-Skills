//! Go language extractor.
//!
//! Extracts symbols and relationships from Go source files (`.go`)
//! using the tree-sitter-go grammar.
//!
//! # Node kinds produced
//! - `function`: function_declaration (no receiver — top-level or nested)
//! - `method`: method_declaration (has receiver)
//! - `class`: type_spec whose type is struct_type
//! - `interface`: type_spec whose type is interface_type
//! - `property`: struct field_declaration
//! - `variable`: short_var_declaration, var_declaration
//! - `type_alias`: type alias declarations
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: call_expression (including selector_expression)
//! - `contains`: containment (file → type → method)
//! - `imports`: import_declaration
//! - `implements`: interface implementation (struct → interface)
//! - `decorates`: struct field tags parsed as decorators
//! - `reads`: variable read
//! - `writes`: variable write (assignment, short_var_declaration)
//!
//! # Special handling
//! - Exported symbols: first character is uppercase Unicode letter
//! - Struct tags: parsed as key="value" pairs → decorates edges
//! - Receiver methods: linked back to struct type via the receiver type name

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Go built-in types and functions — filtered to reduce noise
// ---------------------------------------------------------------------------

const GO_BUILTIN_TYPES: &[&str] = &[
    "bool", "byte", "complex64", "complex128", "error", "float32", "float64",
    "int", "int8", "int16", "int32", "int64", "rune", "string",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
];

const GO_BUILTIN_FUNCS: &[&str] = &[
    "append", "cap", "close", "complex", "copy", "delete", "imag", "len",
    "make", "new", "panic", "print", "println", "real", "recover",
];

fn is_go_builtin(name: &str) -> bool {
    GO_BUILTIN_TYPES.contains(&name) || GO_BUILTIN_FUNCS.contains(&name)
}

// ---------------------------------------------------------------------------
// GoExtractor
// ---------------------------------------------------------------------------

pub struct GoExtractor;

impl Extractor for GoExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["go"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["go"]
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
                .unwrap_or("main")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        let mut walker = Walker::new();
        walker.walk_source_file(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker
// ---------------------------------------------------------------------------

struct Walker {
    /// Stack of type/struct names for contextual resolution.
    type_stack: Vec<String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            type_stack: Vec::new(),
        }
    }

    fn current_type(&self) -> Option<&str> {
        self.type_stack.last().map(|s| s.as_str())
    }

    // ------------------------------------------------------------------
    // Source file walking
    // ------------------------------------------------------------------

    fn walk_source_file(
        &mut self,
        source: &[u8],
        source_file: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // First pass: find package clause for scope
        let mut package_name = String::new();
        {
            let mut cursor = source_file.walk();
            if cursor.goto_first_child() {
                loop {
                    let child = cursor.node();
                    if child.is_named() && child.kind() == "package_clause" {
                        package_name = self.extract_package_clause(source, child);
                    }
                    if !cursor.goto_next_sibling() {
                        break;
                    }
                }
            }
        }

        if !package_name.is_empty() {
            ctx.push_scope_with_kind(&package_name, "package");
        }

        // Walk all top-level declarations
        {
            let mut cursor = source_file.walk();
            if cursor.goto_first_child() {
                loop {
                    let child = cursor.node();
                    if child.is_named() {
                        self.walk_declaration(source, child, ctx, parent_id)?;
                    }
                    if !cursor.goto_next_sibling() {
                        break;
                    }
                }
            }
        }

        if !package_name.is_empty() {
            ctx.pop_scope();
        }

        Ok(())
    }

    fn extract_package_clause(&self, source: &[u8], node: Node) -> String {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "package_identifier" {
                    return get_text(source, Some(child));
                }
            }
        }
        String::new()
    }

    fn walk_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "function_declaration" => {
                // Top-level function (no receiver)
                self.extract_function(source, node, ctx, parent_id, None)?;
            }
            "method_declaration" => {
                self.extract_method(source, node, ctx, parent_id)?;
            }
            "type_declaration" => {
                self.extract_type_decl(source, node, ctx, parent_id)?;
            }
            "import_declaration" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "var_declaration" => {
                self.extract_var_decl(source, node, ctx, parent_id)?;
            }
            "short_var_declaration" => {
                self.extract_short_var_decl(source, node, ctx, parent_id)?;
            }
            "assignment_statement" => {
                self.walk_body_statement(source, node, ctx, parent_id)?;
            }
            _ => {
                // Recurse into unknown children for possible nested declarations
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_declaration(source, child, ctx, parent_id)?;
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Function extraction (no receiver)
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        receiver_type: Option<&str>,
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Check if exported
        let is_exported = is_exported_name(&name);
        if is_exported {
            extra.insert("is_exported".to_string(), "true".to_string());
        }

        // Signature
        if let Some(params) = node.child_by_field_name("parameters") {
            let sig = get_text(source, Some(params));
            if !sig.is_empty() {
                extra.insert("signature".to_string(), format!("func {}({})", name, sig));
            }
        }

        // Determine kind: with receiver → Method, without → Function
        let kind = if receiver_type.is_some() {
            NodeKind::Method
        } else {
            NodeKind::Function
        };

        let func_id = ctx.add_node(kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        // Walk body for calls
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &func_id)?;
        }

        ctx.pop_scope();
        Ok(func_id)
    }

    // ------------------------------------------------------------------
    // Method extraction (has receiver)
    // ------------------------------------------------------------------

    fn extract_method(
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

        // Extract receiver type for edge back to struct
        let receiver_type = self.extract_receiver_type(source, node);

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        let is_exported = is_exported_name(&name);
        if is_exported {
            extra.insert("is_exported".to_string(), "true".to_string());
        }

        if let Some(ref rt) = receiver_type {
            extra.insert("receiver".to_string(), rt.clone());
        }

        // Signature
        if let Some(params) = node.child_by_field_name("parameters") {
            let sig = get_text(source, Some(params));
            if !sig.is_empty() {
                extra.insert("signature".to_string(), format!("func {}({})", name, sig));
            }
        }

        let method_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        // If we know the receiver type, add an edge from method → struct
        if let Some(ref rt) = receiver_type {
            let target_qn = build_qualified_target(&ctx.file_path, rt);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(
                &method_id,
                &target,
                EdgeKind::References,
                line,
                Some(rt),
            );
        }

        // Push receiver scope for contextual access
        if let Some(ref rt) = receiver_type {
            self.type_stack.push(rt.clone());
            ctx.push_scope_with_kind(rt, "type");
            ctx.push_scope_node(&method_id);
        } else {
            ctx.push_scope_with_kind(&name, "method");
            ctx.push_scope_node(&method_id);
        }

        // Walk body for calls
        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &method_id)?;
        }

        ctx.pop_scope();
        if receiver_type.is_some() {
            self.type_stack.pop();
        }

        Ok(method_id)
    }

    fn extract_receiver_type(&self, source: &[u8], node: Node) -> Option<String> {
        if let Some(receiver) = node.child_by_field_name("receiver") {
            // receiver is a parameter_list; find the parameter_declaration inside
            for i in 0..receiver.named_child_count() {
                if let Some(param) = receiver.named_child(i) {
                    if param.kind() == "parameter_declaration" {
                        // Extract just the type, ignoring pointer * prefix
                        if let Some(type_node) = param.child_by_field_name("type") {
                            let type_name = resolve_go_type_name(source, type_node);
                            if !type_name.is_empty() {
                                return Some(type_name);
                            }
                        }
                    }
                }
            }
        }
        None
    }

    // ------------------------------------------------------------------
    // Type declaration extraction
    // ------------------------------------------------------------------

    fn extract_type_decl(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "type_spec" => {
                        self.extract_type_spec(source, child, ctx, parent_id)?;
                    }
                    "type_alias" => {
                        self.extract_type_alias(source, child, ctx, parent_id)?;
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    fn extract_type_spec(
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
        let mut extra = HashMap::new();

        if is_exported_name(&name) {
            extra.insert("is_exported".to_string(), "true".to_string());
        }

        // Determine the underlying type
        let (kind, impl_ifaces) = if let Some(type_node) = node.child_by_field_name("type") {
            match type_node.kind() {
                "struct_type" => {
                    extra.insert("type".to_string(), "struct".to_string());
                    (NodeKind::Class, Vec::new())
                }
                "interface_type" => {
                    extra.insert("type".to_string(), "interface".to_string());
                    let ifaces = self.extract_embedded_interfaces(source, type_node);
                    (NodeKind::Interface, ifaces)
                }
                _ => {
                    extra.insert("type".to_string(), type_node.kind().to_string());
                    // Type alias or other simple type
                    (NodeKind::TypeAlias, Vec::new())
                }
            }
        } else {
            (NodeKind::TypeAlias, Vec::new())
        };

        let type_id = ctx.add_node(kind, &name, &node, extra);
        ctx.add_edge(parent_id, &type_id, EdgeKind::Contains, line, None);

        // Interface implementation: struct implementing embedded interfaces
        for iface in &impl_ifaces {
            let target_qn = build_qualified_target(&ctx.file_path, iface);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(&type_id, &target, EdgeKind::Implements, line, Some(iface));
        }

        // Walk struct fields or interface methods
        if let Some(type_node) = node.child_by_field_name("type") {
            match type_node.kind() {
                "struct_type" => {
                    self.type_stack.push(name.clone());
                    ctx.push_scope_with_kind(&name, "struct");
                    ctx.push_scope_node(&type_id);

                    self.extract_struct_fields(source, type_node, ctx, &type_id)?;

                    ctx.pop_scope();
                    self.type_stack.pop();
                }
                "interface_type" => {
                    self.type_stack.push(name.clone());
                    ctx.push_scope_with_kind(&name, "interface");
                    ctx.push_scope_node(&type_id);

                    self.extract_interface_methods(source, type_node, ctx, &type_id)?;

                    ctx.pop_scope();
                    self.type_stack.pop();
                }
                _ => {}
            }
        }

        Ok(type_id)
    }

    fn extract_type_alias(
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
        let mut extra = HashMap::new();
        extra.insert("is_alias".to_string(), "true".to_string());

        let alias_id = ctx.add_node(NodeKind::TypeAlias, &name, &node, extra);
        ctx.add_edge(parent_id, &alias_id, EdgeKind::Contains, line, None);

        Ok(alias_id)
    }

    // ------------------------------------------------------------------
    // Struct field extraction
    // ------------------------------------------------------------------

    fn extract_struct_fields(
        &self,
        source: &[u8],
        struct_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // struct_node is struct_type; find field_declaration_list
        for i in 0..struct_node.named_child_count() {
            if let Some(child) = struct_node.named_child(i) {
                if child.kind() == "field_declaration_list" {
                    for j in 0..child.named_child_count() {
                        if let Some(field) = child.named_child(j) {
                            if field.kind() == "field_declaration" {
                                self.extract_field_decl(source, field, ctx, parent_id)?;
                            }
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn extract_field_decl(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Field name is "multiple: true" — can have multiple names sharing a type
        let mut field_names: Vec<String> = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "field_identifier" {
                    field_names.push(get_text(source, Some(child)));
                }
            }
        }

        // If no explicit field names, check the "name" field (multiple)
        if let Some(name_field) = node.child_by_field_name("name") {
            // The field might be a single identifier even though the schema says multiple
            if field_names.is_empty() {
                let name_text = get_text(source, Some(name_field));
                if !name_text.is_empty() {
                    field_names.push(name_text);
                }
            }
        }

        // Extract struct tag (for decorates edges)
        let tag_text = if let Some(tag) = node.child_by_field_name("tag") {
            get_text(source, Some(tag))
        } else {
            String::new()
        };

        let tag_keys = parse_struct_tag(&tag_text);

        for field_name in &field_names {
            let mut extra = HashMap::new();
            let is_exported = is_exported_name(field_name);
            if is_exported {
                extra.insert("is_exported".to_string(), "true".to_string());
            }
            if !tag_text.is_empty() {
                extra.insert("tag".to_string(), tag_text.clone());
            }

            let field_id = ctx.add_node(NodeKind::Property, field_name, &node, extra);
            ctx.add_edge(parent_id, &field_id, EdgeKind::Contains, line, None);

            // Struct tags → decorates edges
            for key in &tag_keys {
                let target_qn = build_qualified_target(&ctx.file_path, key);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(
                    &field_id,
                    &target,
                    EdgeKind::Decorates,
                    line,
                    Some(&format!("tag:{key}")),
                );
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Interface method extraction
    // ------------------------------------------------------------------

    fn extract_interface_methods(
        &self,
        source: &[u8],
        iface_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..iface_node.named_child_count() {
            if let Some(child) = iface_node.named_child(i) {
                match child.kind() {
                    "method_elem" => {
                        // Method specification: name + signature, no body
                        let mut method_name = String::new();
                        for j in 0..child.named_child_count() {
                            if let Some(m) = child.named_child(j) {
                                if m.kind() == "field_identifier" {
                                    method_name = get_text(source, Some(m));
                                    break;
                                }
                            }
                        }
                        if !method_name.is_empty() {
                            let line = child.start_position().row as u32 + 1;
                            let mut extra = HashMap::new();
                            extra.insert("is_abstract".to_string(), "true".to_string());
                            let method_id = ctx.add_node(
                                NodeKind::Method,
                                &method_name,
                                &child,
                                extra,
                            );
                            ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);
                        }
                    }
                    "type_elem" => {
                        // Embedded interface → implements edge
                        let type_name = self.resolve_type_elem(source, child);
                        if !type_name.is_empty() && !is_go_builtin(&type_name) {
                            let line = child.start_position().row as u32 + 1;
                            let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(
                                parent_id,
                                &target,
                                EdgeKind::Implements,
                                line,
                                Some(&type_name),
                            );
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    /// Extract embedded interface names from an interface_type.
    fn extract_embedded_interfaces(&self, source: &[u8], iface_node: Node) -> Vec<String> {
        let mut ifaces = Vec::new();
        for i in 0..iface_node.named_child_count() {
            if let Some(child) = iface_node.named_child(i) {
                if child.kind() == "type_elem" {
                    let tn = self.resolve_type_elem(source, child);
                    if !tn.is_empty() && !is_go_builtin(&tn) {
                        ifaces.push(tn);
                    }
                }
            }
        }
        ifaces
    }

    fn resolve_type_elem(&self, source: &[u8], node: Node) -> String {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "type_identifier" => return get_text(source, Some(child)),
                    "qualified_type" => {
                        let mut parts = Vec::new();
                        for j in 0..child.named_child_count() {
                            if let Some(sub) = child.named_child(j) {
                                match sub.kind() {
                                    "package_identifier" | "type_identifier" => {
                                        parts.push(get_text(source, Some(sub)));
                                    }
                                    _ => {}
                                }
                            }
                        }
                        return parts.join(".");
                    }
                    _ => {}
                }
            }
        }
        get_text(source, Some(node))
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    fn extract_import(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // import_declaration contains import_spec or import_spec_list
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "import_spec" => {
                        let import_path = extract_import_path(source, child);
                        if !import_path.is_empty() {
                            let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(
                                parent_id,
                                &target,
                                EdgeKind::Imports,
                                line,
                                Some(&import_path),
                            );
                        }
                    }
                    "import_spec_list" => {
                        for j in 0..child.named_child_count() {
                            if let Some(spec) = child.named_child(j) {
                                if spec.kind() == "import_spec" {
                                    let import_path = extract_import_path(source, spec);
                                    if !import_path.is_empty() {
                                        let target_qn = build_qualified_target(&ctx.file_path, &import_path);
                                        let target = hash_id(&ctx.file_path, &target_qn);
                                        ctx.add_edge(
                                            parent_id,
                                            &target,
                                            EdgeKind::Imports,
                                            line,
                                            Some(&import_path),
                                        );
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Variable declarations
    // ------------------------------------------------------------------

    fn extract_var_decl(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "var_spec" {
                    // var_spec has name and value
                    let var_name = get_text(source, child.child_by_field_name("name"));
                    if !var_name.is_empty() {
                        let var_id = ctx.add_node(
                            NodeKind::Variable,
                            &var_name,
                            &child,
                            HashMap::new(),
                        );
                        ctx.add_edge(parent_id, &var_id, EdgeKind::Writes, line, Some(&var_name));

                        // Walk value for calls
                        if let Some(value) = child.child_by_field_name("value") {
                            self.walk_body_for_calls(source, value, ctx, parent_id)?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn extract_short_var_decl(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Extract variable names from left side
        if let Some(left) = node.child_by_field_name("left") {
            for i in 0..left.named_child_count() {
                if let Some(child) = left.named_child(i) {
                    if child.kind() == "identifier" {
                        let var_name = get_text(source, Some(child));
                        if !var_name.is_empty() && var_name != "_" {
                            let var_id = ctx.add_node(
                                NodeKind::Variable,
                                &var_name,
                                &child,
                                HashMap::new(),
                            );
                            ctx.add_edge(
                                parent_id,
                                &var_id,
                                EdgeKind::Writes,
                                line,
                                Some(&var_name),
                            );
                        }
                    }
                }
            }
        }

        // Walk right side for calls
        if let Some(right) = node.child_by_field_name("right") {
            self.walk_body_for_calls(source, right, ctx, parent_id)?;
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Body walking for calls
    // ------------------------------------------------------------------

    fn walk_body_for_calls(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "call_expression" => {
                self.extract_go_call(source, node, ctx, parent_id)?;
            }
            "selector_expression" => {
                // selector_expression in a non-call context (e.g., field access)
                // Walk for nested calls
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "short_var_declaration" => {
                self.extract_short_var_decl(source, node, ctx, parent_id)?;
            }
            "assignment_statement" => {
                // Track writes
                if let Some(left) = node.child_by_field_name("left") {
                    for i in 0..left.named_child_count() {
                        if let Some(child) = left.named_child(i) {
                            let name = get_text(source, Some(child));
                            if !name.is_empty() && name != "_" {
                                let line = node.start_position().row as u32 + 1;
                                let target_qn = build_qualified_target(&ctx.file_path, &name);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(
                                    parent_id,
                                    &target,
                                    EdgeKind::Writes,
                                    line,
                                    Some(&name),
                                );
                            }
                        }
                    }
                }
                // Walk the entire assignment for calls
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "inc_statement" | "dec_statement" => {
                // Track read+write for increment/decrement
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() == "identifier" {
                            let name = get_text(source, Some(child));
                            if !name.is_empty() {
                                let line = node.start_position().row as u32 + 1;
                                let target_qn = build_qualified_target(&ctx.file_path, &name);
                                let target = hash_id(&ctx.file_path, &target_qn);
                                ctx.add_edge(
                                    parent_id,
                                    &target,
                                    EdgeKind::Reads,
                                    line,
                                    Some(&name),
                                );
                            }
                        }
                    }
                }
            }
            // Recurse into structural nodes
            "block"
            | "statement_list"
            | "if_statement"
            | "for_statement"
            | "expression_switch_statement"
            | "type_switch_statement"
            | "expression_case"
            | "default_case"
            | "select_statement"
            | "communication_case"
            | "send_statement"
            | "go_statement"
            | "defer_statement"
            | "return_statement"
            | "expression_statement"
            | "binary_expression"
            | "unary_expression"
            | "parenthesized_expression"
            | "type_conversion_expression"
            | "type_assertion_expression"
            | "composite_literal"
            | "func_literal"
            | "index_expression"
            | "slice_expression"
            | "keyed_element"
            | "literal_element"
            | "argument_list"
            | "expression_list"
            | "literal_value"
            | "labeled_statement"
            | "fallthrough_statement"
            | "break_statement"
            | "continue_statement"
            | "type_instantiation_expression" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            _ => {
                // Scan for recognized children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call_expression"
                            || ck == "selector_expression"
                            || ck == "assignment_statement"
                            || ck == "short_var_declaration"
                            || ck == "block"
                            || ck == "return_statement"
                            || ck == "if_statement"
                            || ck == "for_statement"
                        {
                            self.walk_body_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    fn extract_go_call(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if let Some(function) = node.child_by_field_name("function") {
            match function.kind() {
                "identifier" => {
                    let name = get_text(source, Some(function));
                    if !name.is_empty() && !is_go_builtin(&name) {
                        let target_qn = build_qualified_target(&ctx.file_path, &name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Calls,
                            line,
                            Some(&name),
                        );
                    }
                }
                "selector_expression" => {
                    // obj.method() — extract method name from field
                    let field_name = get_text(source, function.child_by_field_name("field"));
                    if !field_name.is_empty() && !is_go_builtin(&field_name) {
                        let target_qn = build_qualified_target(&ctx.file_path, &field_name);
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
                "call_expression" => {
                    // Nested call expression like f()()
                    self.extract_go_call(source, function, ctx, parent_id)?;
                }
                "parenthesized_expression" => {
                    // (f)() — recurse
                    self.walk_body_for_calls(source, function, ctx, parent_id)?;
                }
                _ => {
                    // Walk the function expression for nested calls
                    self.walk_body_for_calls(source, function, ctx, parent_id)?;
                }
            }
        }

        // Walk arguments for nested calls
        if let Some(args) = node.child_by_field_name("arguments") {
            for i in 0..args.named_child_count() {
                if let Some(child) = args.named_child(i) {
                    if child.kind() != "(" && child.kind() != ")" {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Statement walking (for top-level assignments / expressions)
    // ------------------------------------------------------------------

    fn walk_body_statement(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        self.walk_body_for_calls(source, node, ctx, parent_id)
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Determine if a Go identifier is exported (first letter is Unicode uppercase).
fn is_exported_name(name: &str) -> bool {
    name.chars()
        .next()
        .map(|c| c.is_uppercase())
        .unwrap_or(false)
}

/// Build a target qualified name for cross-reference edges.
fn build_qualified_target(file_path: &str, name: &str) -> String {
    format!("{file_path}::{name}")
}

/// Get the UTF-8 text of a node from the source bytes.
fn get_text(source: &[u8], node: Option<Node>) -> String {
    match node {
        Some(n) => n
            .utf8_text(source)
            .map(|c| c.to_string())
            .unwrap_or_default(),
        None => String::new(),
    }
}

/// Resolve a Go type reference to its text representation.
fn resolve_go_type_name(source: &[u8], node: Node) -> String {
    match node.kind() {
        "type_identifier" => get_text(source, Some(node)),
        "qualified_type" => {
            let mut parts = Vec::new();
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "package_identifier" | "type_identifier" => {
                            parts.push(get_text(source, Some(child)));
                        }
                        _ => {}
                    }
                }
            }
            parts.join(".")
        }
        "pointer_type" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "type_identifier" | "qualified_type" => {
                            return resolve_go_type_name(source, child);
                        }
                        _ => {}
                    }
                }
            }
            get_text(source, Some(node))
        }
        "generic_type" => {
            // Extract base type name
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "type_identifier" | "qualified_type" => {
                            return resolve_go_type_name(source, child);
                        }
                        _ => {}
                    }
                }
            }
            get_text(source, Some(node))
        }
        _ => get_text(source, Some(node)),
    }
}

/// Extract the import path from an import_spec.
fn extract_import_path(source: &[u8], node: Node) -> String {
    // The path is in an interpreted_string_literal or raw_string_literal
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "interpreted_string_literal" | "raw_string_literal" => {
                    let text = get_text(source, Some(child));
                    // Strip quotes
                    return text.trim_matches('"').trim_matches('`').to_string();
                }
                _ => {}
            }
        }
    }
    String::new()
}

/// Parse a Go struct tag like `json:"name" xml:"Name"` into keys.
fn parse_struct_tag(tag_text: &str) -> Vec<String> {
    let mut keys = Vec::new();
    let trimmed = tag_text.trim_matches('`');

    // Struct tags are space-separated key:"value" pairs
    for part in trimmed.split_whitespace() {
        if let Some(key) = part.split(':').next() {
            let clean_key = key.trim().trim_matches('"').trim_matches('\'');
            if !clean_key.is_empty() {
                keys.push(clean_key.to_string());
            }
        }
    }
    keys
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

    /// Parse Go source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_go::LANGUAGE.into())
            .expect("set go language");
        let tree = parser.parse(source, None).expect("parse go source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "go".to_string());
        GoExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes<'a>(
        ctx: &'a ExtractionContext,
        kind: NodeKind,
    ) -> Vec<&'a crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    /// Helper: find edges of a given kind.
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
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_top_level_function() {
        let ctx = extract(
            "package main\n\nfunc hello() {}\n",
            "src/main.go",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "hello");
    }

    #[test]
    fn test_extract_exported_function() {
        let ctx = extract(
            "package main\n\nfunc Hello() {}\n",
            "src/main.go",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "Hello");
        // Check is_exported property
        if let Some(props) = &funcs[0].properties {
            assert!(props.contains("is_exported"));
        }
    }

    #[test]
    fn test_extract_multiple_functions() {
        let ctx = extract(
            "package main\n\nfunc foo() {}\nfunc bar() {}\n",
            "src/main.go",
        );
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 2);
    }

    // ------------------------------------------------------------------
    // Method extraction (with receiver)
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_method() {
        let ctx = extract(
            r#"package main

type Foo struct{}

func (f Foo) Bar() {}
"#,
            "src/main.go",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "Bar");
    }

    #[test]
    fn test_extract_pointer_receiver_method() {
        let ctx = extract(
            r#"package main

type Foo struct{}

func (f *Foo) Bar() {}
"#,
            "src/main.go",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "Bar");
    }

    // ------------------------------------------------------------------
    // Struct extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct_type() {
        let ctx = extract(
            "package main\n\ntype User struct {\n    Name  string\n    Email string\n}\n",
            "src/main.go",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "User");

        let properties = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(properties.len(), 2);
        let names: Vec<&str> = properties.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Name"));
        assert!(names.contains(&"Email"));
    }

    #[test]
    fn test_extract_struct_with_tags() {
        let ctx = extract(
            r#"package main

type User struct {
    Name string `json:"name" xml:"Name"`
}
"#,
            "src/main.go",
        );
        let properties = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(properties.len(), 1);
        assert_eq!(properties[0].name, "Name");

        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> = decorates
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.iter().any(|t| t.contains("json")), "Expected 'json' tag: {:?}", targets);
        assert!(targets.iter().any(|t| t.contains("xml")), "Expected 'xml' tag: {:?}", targets);
    }

    #[test]
    fn test_struct_contains_edge() {
        let ctx = extract(
            "package main\n\ntype Foo struct {\n    X int\n}\n",
            "src/main.go",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface() {
        let ctx = extract(
            "package main\n\ntype Reader interface {\n    Read(p []byte) (n int, err error)\n}\n",
            "src/main.go",
        );
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "Reader");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "Read");
    }

    #[test]
    fn test_extract_interface_with_embedded() {
        let ctx = extract(
            "package main\n\nimport \"io\"\n\ntype ReadWriter interface {\n    io.Reader\n    io.Writer\n}\n",
            "src/main.go",
        );
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);

        let implements = find_edges(&ctx, EdgeKind::Implements);
        // Should have edges to both io.Reader and io.Writer
        let targets: Vec<&str> = implements
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.iter().any(|t| t.contains("Reader")), "Expected io.Reader: {:?}", targets);
        assert!(targets.iter().any(|t| t.contains("Writer")), "Expected io.Writer: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract(
            "package main\n\nfunc foo() {\n    bar()\n}\n",
            "src/main.go",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"bar"), "Expected 'bar' in call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_selector_call() {
        let ctx = extract(
            "package main\n\nfunc foo(obj *MyType) {\n    obj.Method()\n}\n",
            "src/main.go",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"Method"), "Expected 'Method' in call targets: {:?}", targets);
    }

    #[test]
    fn test_filter_builtin_calls() {
        let ctx = extract(
            "package main\n\nfunc foo() {\n    println(\"hello\")\n    len(s)\n    make([]int, 0)\n}\n",
            "src/main.go",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(!targets.contains(&"println"), "Should filter println");
        assert!(!targets.contains(&"len"), "Should filter len");
        assert!(!targets.contains(&"make"), "Should filter make");
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_single_import() {
        let ctx = extract(
            "package main\n\nimport \"fmt\"\n",
            "src/main.go",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"fmt"), "Expected 'fmt' import: {:?}", targets);
    }

    #[test]
    fn test_extract_grouped_imports() {
        let ctx = extract(
            "package main\n\nimport (\n    \"fmt\"\n    \"strings\"\n)\n",
            "src/main.go",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 2, "Expected at least 2 import edges, got {}", imports.len());
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_short_var_decl() {
        let ctx = extract(
            "package main\n\nfunc foo() {\n    x := 42\n}\n",
            "src/main.go",
        );
        let writes = find_edges(&ctx, EdgeKind::Writes);
        let targets: Vec<&str> = writes
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"x"), "Expected 'x' write: {:?}", targets);
    }

    #[test]
    fn test_extract_records_write_edges() {
        let ctx = extract(
            "package main\n\nfunc foo() {\n    count := 0\n    count = 5\n}\n",
            "src/main.go",
        );
        let writes = find_edges(&ctx, EdgeKind::Writes);
        // Should have 2 writes: count := 0 and count = 5
        assert!(writes.len() >= 2, "Expected at least 2 writes, got {}", writes.len());
    }

    #[test]
    fn test_extract_reads_edges() {
        let ctx = extract(
            "package main\n\nfunc foo() {\n    x := 1\n    x++\n}\n",
            "src/main.go",
        );
        let reads = find_edges(&ctx, EdgeKind::Reads);
        let targets: Vec<&str> = reads
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"x"), "Expected 'x' read from increment: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Exported detection
    // ------------------------------------------------------------------

    #[test]
    fn test_detect_exported_struct_field() {
        let ctx = extract(
            "package main\n\ntype Foo struct {\n    PublicField  string\n    privateField string\n}\n",
            "src/main.go",
        );
        let properties = find_nodes(&ctx, NodeKind::Property);
        assert_eq!(properties.len(), 2);

        // Find the exported field
        let exported = properties.iter().find(|n| n.name == "PublicField");
        assert!(exported.is_some(), "Expected PublicField to be present");
        if let Some(props) = &exported.unwrap().properties {
            assert!(props.contains("is_exported"));
        }

        let private = properties.iter().find(|n| n.name == "privateField");
        assert!(private.is_some(), "Expected privateField to be present");
        // Private field should NOT be exported
        if let Some(props) = &private.unwrap().properties {
            assert!(!props.contains("is_exported"));
        }
    }

    // ------------------------------------------------------------------
    // Type alias extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_type_alias() {
        let ctx = extract(
            "package main\n\ntype MyInt = int\n",
            "src/main.go",
        );
        let aliases = find_nodes(&ctx, NodeKind::TypeAlias);
        assert_eq!(aliases.len(), 1);
        assert_eq!(aliases[0].name, "MyInt");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.go");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_only_package() {
        let ctx = extract("package main\n", "src/main.go");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            r#"package main

import (
    "fmt"
    "strings"
)

type Greeter interface {
    Greet(name string) string
}

type Person struct {
    Name string `json:"name"`
    Age  int    `json:"age"`
}

func (p *Person) Greet(name string) string {
    return fmt.Sprintf("Hello %s, I'm %s", name, p.Name)
}

func NewPerson(name string, age int) *Person {
    p := &Person{Name: name, Age: age}
    return p
}

func main() {
    p := NewPerson("Alice", 30)
    result := p.Greet("Bob")
    fmt.Println(result)
    parts := strings.Split(result, " ")
    count := len(parts)
    _ = count
}
"#,
            "src/main.go",
        );
        // Just verify it doesn't crash and produces nodes
        assert!(!ctx.result.nodes.is_empty());

        // We should find structs, functions, methods, etc.
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert!(!funcs.is_empty(), "Expected at least one function");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(!methods.is_empty(), "Expected at least one method");

        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(!classes.is_empty(), "Expected at least one struct");
    }

}
