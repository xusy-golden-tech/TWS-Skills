//! Java language extractor.
//!
//! Extracts symbols and relationships from Java source files (`.java`)
//! using the tree-sitter-java grammar.
//!
//! # Node kinds produced
//! - `class`: class_declaration
//! - `method`: method_declaration inside a class
//! - `interface`: interface_declaration
//! - `enum`: enum_declaration
//! - `variable`: field_declaration, local_variable_declaration
//! - `package`: package_declaration (package name node)
//! - `lambda`: lambda_expression
//! - `record`: record_declaration
//! - `file`: compilation unit
//!
//! # Edge kinds produced
//! - `calls`: method_invocation (chained calls → final method name only)
//! - `contains`: containment (file → class → method)
//! - `imports`: import_declaration
//! - `extends`: class/interface extension
//! - `implements`: interface implementation
//! - `decorates`: annotation application
//! - `instantiates`: object_creation_expression (`new Foo()`)
//! - `overrides`: @Override annotation → parent method
//! - `type_ref`: generic type parameter references
//! - `reads`: variable/field read access
//! - `writes`: variable/field write (assignment)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Java common library packages — filtered to reduce noise
// ---------------------------------------------------------------------------

const JAVA_STDLIB_PREFIXES: &[&str] = &[
    "java.", "javax.", "jakarta.", "org.w3c.", "org.xml.", "org.omg.",
    "org.ietf.", "com.sun.", "sun.",
];

fn is_java_stdlib(s: &str) -> bool {
    JAVA_STDLIB_PREFIXES.iter().any(|p| s.starts_with(p))
}

// ---------------------------------------------------------------------------
// JavaExtractor
// ---------------------------------------------------------------------------

pub struct JavaExtractor;

impl Extractor for JavaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["java"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["java"]
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
                .unwrap_or("File")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        let mut walker = Walker::new();
        walker.walk_program(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker
// ---------------------------------------------------------------------------

struct Walker {
    class_stack: Vec<String>,
    /// Map from imported simple class name to fully qualified name.
    /// e.g. `import com.foo.bar.MyClass;` populates `"MyClass" → "com.foo.bar.MyClass"`.
    /// e.g. `import static com.foo.bar.Utils.helper;` populates `"helper" → "com.foo.bar.Utils.helper"`.
    imported_names: HashMap<String, String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            class_stack: Vec::new(),
            imported_names: HashMap::new(),
        }
    }

    fn current_class(&self) -> Option<&str> {
        self.class_stack.last().map(|s| s.as_str())
    }

    /// Resolve a possibly-simple type name to its fully qualified form
    /// using the imported_names map. Returns the qualified name if known,
    /// otherwise returns the original name unchanged.
    fn qualify_type(&self, simple_name: &str) -> String {
        // Handle multi-segment names like "a.b.C" — try the first segment
        if let Some(dot_pos) = simple_name.find('.') {
            let first = &simple_name[..dot_pos];
            if let Some(qualified) = self.imported_names.get(first) {
                let rest = &simple_name[dot_pos + 1..];
                return format!("{}.{}", qualified, rest);
            }
        }
        // Try simple name directly
        self.imported_names
            .get(simple_name)
            .cloned()
            .unwrap_or_else(|| simple_name.to_string())
    }

    // ------------------------------------------------------------------
    // Program walking
    // ------------------------------------------------------------------

    fn walk_program(
        &mut self,
        source: &[u8],
        program: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // First pass: find package_declaration to set scope
        let mut package_name = String::new();
        {
            let mut cursor = program.walk();
            if cursor.goto_first_child() {
                loop {
                    let child = cursor.node();
                    if child.is_named() && child.kind() == "package_declaration" {
                        package_name = self.resolve_package_source(source, child);
                    }
                    if !cursor.goto_next_sibling() {
                        break;
                    }
                }
            }
        }

        // Push package scope so qualified names include package
        if !package_name.is_empty() {
            ctx.push_scope_with_kind(&package_name, "package");
            // Register package as a node
            let _pkg_node = get_text(source, program.named_child(0));
            let pkg_id = ctx.add_node(
                NodeKind::Package,
                &package_name,
                &program,
                HashMap::new(),
            );
            ctx.add_edge(
                parent_id,
                &pkg_id,
                EdgeKind::Contains,
                program.start_position().row as u32 + 1,
                Some(&package_name),
            );
        }

        // Second pass: walk all declarations
        {
            let mut cursor = program.walk();
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

    fn resolve_package_source(&self, source: &[u8], node: Node) -> String {
        let mut parts: Vec<String> = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "identifier" => parts.push(get_text(source, Some(child))),
                    "scoped_identifier" => {
                        for j in 0..child.named_child_count() {
                            if let Some(sub) = child.named_child(j) {
                                if sub.kind() == "identifier" {
                                    parts.push(get_text(source, Some(sub)));
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        parts.join(".")
    }

    fn walk_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "enum_declaration" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "record_declaration" => {
                self.extract_record(source, node, ctx, parent_id)?;
            }
            "method_declaration" => {
                self.extract_method(source, node, ctx, parent_id, NodeKind::Function)?;
            }
            "constructor_declaration" => {
                self.extract_constructor(source, node, ctx, parent_id)?;
            }
            "import_declaration" => {
                self.extract_import(source, node, ctx, parent_id)?;
            }
            "field_declaration" => {
                self.extract_field(source, node, ctx, parent_id)?;
            }
            "package_declaration" => {
                // Already handled in first pass
            }
            "annotation_type_declaration" => {
                // Treat as interface-like
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            _ => {}
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Class extraction
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

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Collect annotations
        let annotations = self.collect_annotations(source, node);
        if !annotations.is_empty() {
            if let Ok(json) = serde_json::to_string(&annotations) {
                extra.insert("decorators".to_string(), json);
            }
        }

        let class_id = ctx.add_node(NodeKind::Class, &name, &node, extra);
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Superclass → extends
        if let Some(superclass) = node.child_by_field_name("superclass") {
            for i in 0..superclass.named_child_count() {
                if let Some(sc) = superclass.named_child(i) {
                    let type_name = resolve_type_name(source, sc);
                    if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                        let qualified = self.qualify_type(&type_name);
                        let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&qualified));
                    }
                }
            }
        }

        // Interfaces → implements
        if let Some(interfaces) = node.child_by_field_name("interfaces") {
            for i in 0..interfaces.named_child_count() {
                if let Some(iface) = interfaces.named_child(i) {
                    if iface.kind() == "type_list" {
                        for j in 0..iface.named_child_count() {
                            if let Some(typ) = iface.named_child(j) {
                                let iface_name = resolve_type_name(source, typ);
                                if !iface_name.is_empty() && !is_java_stdlib(&iface_name) {
                                    let qualified = self.qualify_type(&iface_name);
                                    let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&qualified));
                                }
                            }
                        }
                    } else {
                        let iface_name = resolve_type_name(source, iface);
                        if !iface_name.is_empty() && !is_java_stdlib(&iface_name) {
                            let qualified = self.qualify_type(&iface_name);
                            let target_qn = build_qualified_target(&ctx.file_path, &qualified);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(&class_id, &target, EdgeKind::Implements, line, Some(&qualified));
                        }
                    }
                }
            }
        }

        // Type parameters → type_ref
        self.extract_type_params(source, node, &class_id, ctx);

        // Annotations → decorates
        for ann in &annotations {
            let target_qn = build_qualified_target(&ctx.file_path, ann);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(&class_id, &target, EdgeKind::Decorates, line, Some(ann));
        }

        // Walk class body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_class_member(source, child, ctx, &class_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(class_id)
    }

    fn walk_class_member(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "method_declaration" => {
                self.extract_method(source, node, ctx, parent_id, NodeKind::Method)?;
            }
            "constructor_declaration" => {
                self.extract_constructor(source, node, ctx, parent_id)?;
            }
            "field_declaration" => {
                self.extract_field(source, node, ctx, parent_id)?;
            }
            "class_declaration" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "interface_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            "enum_declaration" => {
                self.extract_enum(source, node, ctx, parent_id)?;
            }
            "record_declaration" => {
                self.extract_record(source, node, ctx, parent_id)?;
            }
            "annotation_type_declaration" => {
                self.extract_interface(source, node, ctx, parent_id)?;
            }
            _ => {}
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Interface extraction
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
        let interface_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &interface_id, EdgeKind::Contains, line, None);

        // extends_interfaces → extends edges
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "extends_interfaces" {
                    for j in 0..child.named_child_count() {
                        if let Some(typ) = child.named_child(j) {
                            if typ.kind() == "type_list" {
                                for k in 0..typ.named_child_count() {
                                    if let Some(t) = typ.named_child(k) {
                                        let type_name = resolve_type_name(source, t);
                                        if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                                            let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                                            let target = hash_id(&ctx.file_path, &target_qn);
                                            ctx.add_edge(&interface_id, &target, EdgeKind::Extends, line, Some(&type_name));
                                        }
                                    }
                                }
                            } else {
                                let type_name = resolve_type_name(source, typ);
                                if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                                    let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                                    let target = hash_id(&ctx.file_path, &target_qn);
                                    ctx.add_edge(&interface_id, &target, EdgeKind::Extends, line, Some(&type_name));
                                }
                            }
                        }
                    }
                }
            }
        }

        // Type parameters
        self.extract_type_params(source, node, &interface_id, ctx);

        // Walk body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "interface");
        ctx.push_scope_node(&interface_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    match child.kind() {
                        "method_declaration" => {
                            self.extract_abstract_method(source, child, ctx, &interface_id)?;
                        }
                        "class_declaration" | "interface_declaration" | "enum_declaration"
                        | "annotation_type_declaration" => {
                            self.walk_class_member(source, child, ctx, &interface_id)?;
                        }
                        _ => {}
                    }
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(interface_id)
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    fn extract_enum(
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
        let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

        // Interfaces → implements
        if let Some(interfaces) = node.child_by_field_name("interfaces") {
            for i in 0..interfaces.named_child_count() {
                if let Some(iface) = interfaces.named_child(i) {
                    let iface_name = resolve_type_name(source, iface);
                    if !iface_name.is_empty() && !is_java_stdlib(&iface_name) {
                        let target_qn = build_qualified_target(&ctx.file_path, &iface_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&enum_id, &target, EdgeKind::Implements, line, Some(&iface_name));
                    }
                }
            }
        }

        // Walk body (enum constants, methods, fields)
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "enum");
        ctx.push_scope_node(&enum_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_class_member(source, child, ctx, &enum_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(enum_id)
    }

    // ------------------------------------------------------------------
    // Record extraction
    // ------------------------------------------------------------------

    fn extract_record(
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
        let record_id = ctx.add_node(NodeKind::Record, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &record_id, EdgeKind::Contains, line, None);

        // Interfaces
        if let Some(interfaces) = node.child_by_field_name("interfaces") {
            for i in 0..interfaces.named_child_count() {
                if let Some(iface) = interfaces.named_child(i) {
                    let iface_name = resolve_type_name(source, iface);
                    if !iface_name.is_empty() && !is_java_stdlib(&iface_name) {
                        let target_qn = build_qualified_target(&ctx.file_path, &iface_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&record_id, &target, EdgeKind::Implements, line, Some(&iface_name));
                    }
                }
            }
        }

        // Walk body
        self.class_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "record");
        ctx.push_scope_node(&record_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_class_member(source, child, ctx, &record_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.class_stack.pop();
        Ok(record_id)
    }

    // ------------------------------------------------------------------
    // Method extraction
    // ------------------------------------------------------------------

    fn extract_method(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        kind: NodeKind,
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        // Collect annotations
        let annotations = self.collect_annotations(source, node);
        if !annotations.is_empty() {
            if let Ok(json) = serde_json::to_string(&annotations) {
                extra.insert("decorators".to_string(), json);
            }
        }

        // Check is_abstract
        let is_abstract = self.check_modifier(source, node, "abstract");
        if is_abstract {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        // Signature
        if let Some(params) = node.child_by_field_name("parameters") {
            let sig = get_text(source, Some(params));
            if !sig.is_empty() {
                extra.insert("signature".to_string(), format!("{}({})", name, sig));
            }
        }

        let method_id = ctx.add_node(kind, &name, &node, extra);
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        // @Override annotation → overrides edge
        let has_override = annotations.iter().any(|a| a == "Override");
        if has_override {
            // Emit an overrides edge (target will be resolved later)
            let target_qn = format!("{}::__override__{}", ctx.file_path, name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(
                &method_id,
                &target,
                EdgeKind::Overrides,
                line,
                Some(&format!("override:{name}")),
            );
        }

        // Annotations → decorates
        for ann in &annotations {
            if ann == "Override" {
                continue; // already handled
            }
            let target_qn = build_qualified_target(&ctx.file_path, ann);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(&method_id, &target, EdgeKind::Decorates, line, Some(ann));
        }

        // Type parameters → type_ref
        self.extract_type_params(source, node, &method_id, ctx);

        // Walk body for calls, instantiations, reads, writes
        ctx.push_scope_with_kind(&name, "method");
        ctx.push_scope_node(&method_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &method_id)?;
        }

        ctx.pop_scope();
        Ok(method_id)
    }

    fn extract_abstract_method(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<String> {
        // Interface methods have no body; treat as abstract
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        extra.insert("is_abstract".to_string(), "true".to_string());
        let line = node.start_position().row as u32 + 1;

        let method_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        // Type parameters
        self.extract_type_params(source, node, &method_id, ctx);

        // Even abstract methods might have @Override
        let annotations = self.collect_annotations(source, node);
        if annotations.iter().any(|a| a == "Override") {
            let target_qn = format!("{}::__override__{}", ctx.file_path, name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(
                &method_id,
                &target,
                EdgeKind::Overrides,
                line,
                Some(&format!("override:{name}")),
            );
        }

        Ok(method_id)
    }

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

        let mut extra = HashMap::new();
        extra.insert("is_constructor".to_string(), "true".to_string());
        let line = node.start_position().row as u32 + 1;

        let ctor_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
        ctx.add_edge(parent_id, &ctor_id, EdgeKind::Contains, line, None);

        // Walk body
        ctx.push_scope_with_kind(&name, "constructor");
        ctx.push_scope_node(&ctor_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &ctor_id)?;
        }

        ctx.pop_scope();
        Ok(ctor_id)
    }

    // ------------------------------------------------------------------
    // Field extraction
    // ------------------------------------------------------------------

    fn extract_field(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Multiple declarators possible (e.g., int a, b, c;)
        if let Some(declarator_field) = node.child_by_field_name("declarator") {
            // declarator field is "multiple: true"
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "variable_declarator" {
                        if let Some(name_node) = child.child_by_field_name("name") {
                            let var_name = get_text(source, Some(name_node));
                            if !var_name.is_empty() {
                                let var_id = ctx.add_node(
                                    NodeKind::Variable,
                                    &var_name,
                                    &child,
                                    HashMap::new(),
                                );
                                ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);

                                // Check if value contains object_creation_expression
                                if let Some(value) = child.child_by_field_name("value") {
                                    self.extract_field_value(
                                        source, value, &var_id, ctx,
                                    )?;
                                }
                            }
                        }
                    }
                }
            }
            // Also try the field by name for single declarators
            let _ = declarator_field; // used in "multiple: true" context
        }

        // Walk for local_variable_declaration inside method bodies
        // (handled separately in walk_body_for_calls)

        Ok(())
    }

    fn extract_field_value(
        &self,
        source: &[u8],
        node: Node,
        var_id: &str,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if node.kind() == "object_creation_expression" {
            if let Some(type_node) = node.child_by_field_name("type") {
                let type_name = resolve_type_name(source, type_node);
                if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                    let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        var_id,
                        &target,
                        EdgeKind::Instantiates,
                        line,
                        Some(&type_name),
                    );
                }
            }
        }

        // Recurse into children for nested object_creation_expression
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "object_creation_expression" {
                    if let Some(type_node) = child.child_by_field_name("type") {
                        let type_name = resolve_type_name(source, type_node);
                        if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                            let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(
                                var_id,
                                &target,
                                EdgeKind::Instantiates,
                                line,
                                Some(&type_name),
                            );
                        }
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Import extraction
    // ------------------------------------------------------------------

    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Check if this is a wildcard import (ends with *)
        let is_wildcard = has_asterisk(node);

        // Build the full import path
        let import_path = build_import_path(source, node);

        if import_path.is_empty() {
            return Ok(());
        }

        if is_wildcard {
            // Wildcard import: `import com.foo.bar.*;` or `import static com.foo.bar.MyClass.*;`
            // Create IMPORTS edge to the package (no per-symbol REFERENCES since we don't know which symbols are used)
            let target_qn = build_qualified_target(&ctx.file_path, &import_path);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&import_path));
        } else {
            // Class import: `import com.foo.bar.MyClass;`
            // or static method import: `import static com.foo.bar.Utils.helper;`
            // Create REFERENCES edge with the fully qualified name
            let target_qn = build_qualified_target(&ctx.file_path, &import_path);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&import_path));

            // Populate imported_names: simple name → fully qualified name
            // For class imports, the last segment is the class name
            // For static imports, the last segment is the method/field name
            if let Some(simple_name) = import_path.rsplit('.').next() {
                if !simple_name.is_empty() && !is_java_stdlib(&import_path) {
                    self.imported_names
                        .insert(simple_name.to_string(), import_path.clone());
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Body walking for calls, instantiations, reads, writes
    // ------------------------------------------------------------------

    fn walk_body_for_calls(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "method_invocation" => {
                self.extract_method_call(source, node, ctx, parent_id)?;
            }
            "object_creation_expression" => {
                self.extract_instantiation(source, node, ctx, parent_id)?;
            }
            "lambda_expression" => {
                self.extract_lambda(source, node, ctx, parent_id)?;
            }
            "class_declaration" => {
                // Nested class inside method
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "assignment_expression" | "update_assignment" => {
                // Track writes
                if let Some(left) = node.child_by_field_name("left") {
                    self.record_write(source, left, ctx, parent_id);
                }
                // Also recurse for calls on RHS
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            "local_variable_declaration" => {
                // Extract local var nodes and check for instantiations
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() == "variable_declarator" {
                            if let Some(name_node) = child.child_by_field_name("name") {
                                let var_name = get_text(source, Some(name_node));
                                if !var_name.is_empty() {
                                    let line = child.start_position().row as u32 + 1;
                                    let var_id = ctx.add_node(
                                        NodeKind::Variable,
                                        &var_name,
                                        &child,
                                        HashMap::new(),
                                    );
                                    ctx.add_edge(parent_id, &var_id, EdgeKind::Writes, line, Some(&var_name));

                                    // Check value for instantiation
                                    if let Some(value) = child.child_by_field_name("value") {
                                        self.extract_field_value(source, value, &var_id, ctx)?;
                                        self.walk_body_for_calls(source, value, ctx, parent_id)?;
                                    }
                                }
                            }
                        }
                    }
                }
                // Also recurse into the node
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        if child.kind() != "variable_declarator" {
                            self.walk_body_for_calls(source, child, ctx, parent_id)?;
                        }
                    }
                }
            }
            // Recurse into structural nodes
            "block"
            | "if_statement"
            | "for_statement"
            | "while_statement"
            | "do_statement"
            | "enhanced_for_statement"
            | "switch_expression"
            | "switch_block"
            | "switch_block_statement_group"
            | "try_statement"
            | "try_with_resources_statement"
            | "catch_clause"
            | "finally_clause"
            | "synchronized_statement"
            | "return_statement"
            | "throw_statement"
            | "expression_statement"
            | "assert_statement"
            | "parenthesized_expression"
            | "cast_expression"
            | "ternary_expression"
            | "binary_expression"
            | "instanceof_expression"
            | "unary_expression"
            | "array_creation_expression"
            | "array_initializer"
            | "field_access"
            | "array_access"
            | "this"
            | "argument_list"
            | "conditional_expression"
            | "yield_statement" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls(source, child, ctx, parent_id)?;
                    }
                }
            }
            _ => {
                // For other nodes, scan for call-related children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "method_invocation"
                            || ck == "object_creation_expression"
                            || ck == "lambda_expression"
                            || ck == "assignment_expression"
                            || ck == "local_variable_declaration"
                            || ck == "block"
                            || ck == "return_statement"
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
    // Method call extraction
    // ------------------------------------------------------------------

    fn extract_method_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Get the final method name in the chain
        // For chained calls (obj.foo().bar()), tree-sitter nests — the
        // outermost method_invocation's "name" field is the final method.
        if let Some(name_node) = node.child_by_field_name("name") {
            let method_name = get_text(source, Some(name_node));

            // Check for System.out.println etc. — standard library, skip
            if !method_name.is_empty() {
                // Check object for common patterns
                let is_stdlib = if let Some(object) = node.child_by_field_name("object") {
                    let obj_text = get_text(source, Some(object));
                    is_java_stdlib(&obj_text) || is_system_call(&obj_text)
                } else {
                    false
                };

                if !is_stdlib {
                    // Check if the object part refers to an imported class
                    // e.g., `Utils.helper()` where Utils was imported from `com.foo.Utils`
                    let call_target_text = if let Some(object) = node.child_by_field_name("object") {
                        let obj_text = get_text(source, Some(object));
                        // Resolve the object through imported_names to get fully qualified name
                        let obj_base = obj_text.split('.').next().unwrap_or(&obj_text);
                        if let Some(qualified) = self.imported_names.get(obj_base) {
                            // Replace the first segment with the fully qualified name
                            // e.g., obj_text="Utils.helper" → "com.foo.Utils.helper"
                            let suffix = if obj_text.len() > obj_base.len() {
                                // Multi-segment object like "obj.inner"
                                &obj_text[obj_base.len() + 1..]
                            } else {
                                ""
                            };
                            if suffix.is_empty() {
                                format!("{}.{}", qualified, method_name)
                            } else {
                                format!("{}.{}.{}", qualified, suffix, method_name)
                            }
                        } else {
                            // Check if method name itself is a static import
                            if let Some(qualified_static) = self.imported_names.get(&method_name) {
                                qualified_static.clone()
                            } else {
                                method_name.clone()
                            }
                        }
                    } else {
                        // No object — check if method name is a static import
                        if let Some(qualified_static) = self.imported_names.get(&method_name) {
                            qualified_static.clone()
                        } else {
                            method_name.clone()
                        }
                    };

                    let target_qn = build_call_target(
                        &ctx.file_path,
                        &self.class_stack,
                        &call_target_text,
                    );
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        parent_id,
                        &target,
                        EdgeKind::Calls,
                        line,
                        Some(&call_target_text),
                    );
                }
            }
        }

        // Also walk arguments for nested calls
        if let Some(args) = node.child_by_field_name("arguments") {
            for i in 0..args.named_child_count() {
                if let Some(child) = args.named_child(i) {
                    self.walk_body_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Instantiation extraction
    // ------------------------------------------------------------------

    fn extract_instantiation(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if let Some(type_node) = node.child_by_field_name("type") {
            let type_name = resolve_type_name(source, type_node);
            if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                // Check if the type name is imported (use fully qualified name)
                let qualified_name = self
                    .imported_names
                    .get(&type_name)
                    .cloned()
                    .unwrap_or_else(|| type_name.clone());

                let target_qn = build_qualified_target(&ctx.file_path, &qualified_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(
                    parent_id,
                    &target,
                    EdgeKind::Instantiates,
                    line,
                    Some(&qualified_name),
                );
            }
        }

        // Walk arguments for nested calls
        if let Some(args) = node.child_by_field_name("arguments") {
            for i in 0..args.named_child_count() {
                if let Some(child) = args.named_child(i) {
                    self.walk_body_for_calls(source, child, ctx, parent_id)?;
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Lambda extraction
    // ------------------------------------------------------------------

    fn extract_lambda(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Generate a synthetic name for the lambda
        let lambda_name = format!("lambda${}", line);
        let lambda_id = ctx.add_node(
            NodeKind::Lambda,
            &lambda_name,
            &node,
            HashMap::new(),
        );
        ctx.add_edge(parent_id, &lambda_id, EdgeKind::Contains, line, None);

        // Walk body for calls
        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls(source, body, ctx, &lambda_id)?;
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Variable read / write tracking
    // ------------------------------------------------------------------

    fn record_write(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let line = node.start_position().row as u32 + 1;
        let name = get_text(source, Some(node));
        if !name.is_empty() {
            let target_qn = build_qualified_target(&ctx.file_path, &name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Writes, line, Some(&name));
        }
    }

    // ------------------------------------------------------------------
    // Type parameters → type_ref
    // ------------------------------------------------------------------

    fn extract_type_params(
        &self,
        source: &[u8],
        node: Node,
        target_node_id: &str,
        ctx: &mut ExtractionContext,
    ) {
        let line = node.start_position().row as u32 + 1;

        if let Some(type_params) = node.child_by_field_name("type_parameters") {
            // type_parameters contains type_parameter children
            for i in 0..type_params.named_child_count() {
                if let Some(param) = type_params.named_child(i) {
                    if param.kind() == "type_parameter" {
                        for j in 0..param.named_child_count() {
                            if let Some(child) = param.named_child(j) {
                                match child.kind() {
                                    "type_identifier" | "generic_type" => {
                                        let type_name = resolve_type_name(source, child);
                                        if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                                            let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                                            let target = hash_id(&ctx.file_path, &target_qn);
                                            ctx.add_edge(
                                                target_node_id,
                                                &target,
                                                EdgeKind::TypeRef,
                                                line,
                                                Some(&type_name),
                                            );
                                        }
                                    }
                                    "type_bound" => {
                                        for k in 0..child.named_child_count() {
                                            if let Some(bound) = child.named_child(k) {
                                                let type_name = resolve_type_name(source, bound);
                                                if !type_name.is_empty() && !is_java_stdlib(&type_name) {
                                                    let target_qn = build_qualified_target(&ctx.file_path, &type_name);
                                                    let target = hash_id(&ctx.file_path, &target_qn);
                                                    ctx.add_edge(
                                                        target_node_id,
                                                        &target,
                                                        EdgeKind::TypeRef,
                                                        line,
                                                        Some(&type_name),
                                                    );
                                                }
                                            }
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Annotation collection
    // ------------------------------------------------------------------

    fn collect_annotations(&self, source: &[u8], node: Node) -> Vec<String> {
        let mut names: Vec<String> = Vec::new();
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "annotation" | "marker_annotation" => {
                        if let Some(name_node) = child.child_by_field_name("name") {
                            let ann_name = resolve_annotation_name(source, name_node);
                            if !ann_name.is_empty() {
                                names.push(ann_name);
                            }
                        }
                    }
                    "modifiers" => {
                        // Annotations can be nested inside modifiers node
                        for j in 0..child.named_child_count() {
                            if let Some(mod_child) = child.named_child(j) {
                                if mod_child.kind() == "annotation"
                                    || mod_child.kind() == "marker_annotation"
                                {
                                    if let Some(name_node) = mod_child.child_by_field_name("name") {
                                        let ann_name = resolve_annotation_name(source, name_node);
                                        if !ann_name.is_empty() {
                                            names.push(ann_name);
                                        }
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        names
    }

    fn check_modifier(&self, source: &[u8], node: Node, modifier: &str) -> bool {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                if child.kind() == "modifiers" {
                    for j in 0..child.named_child_count() {
                        if let Some(mod_node) = child.named_child(j) {
                            let text = get_text(source, Some(mod_node));
                            if text == modifier {
                                return true;
                            }
                        }
                    }
                }
            }
        }
        false
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Build a target qualified name for a call edge.
fn build_call_target(
    file_path: &str,
    class_stack: &[String],
    callee: &str,
) -> String {
    // For Java, class_stack has the current class name
    if let Some(class_name) = class_stack.last() {
        format!("{file_path}::{class_name}.{callee}")
    } else {
        format!("{file_path}::{callee}")
    }
}

/// Build a qualified target for cross-reference edges.
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

/// Resolve a type reference to its text representation.
fn resolve_type_name(source: &[u8], node: Node) -> String {
    match node.kind() {
        "type_identifier" => get_text(source, Some(node)),
        "scoped_type_identifier" => {
            let mut parts: Vec<String> = Vec::new();
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "type_identifier" {
                        parts.push(get_text(source, Some(child)));
                    }
                }
            }
            parts.join(".")
        }
        "generic_type" => {
            // Extract just the base type, ignoring type arguments
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "type_identifier" || child.kind() == "scoped_type_identifier" {
                        return resolve_type_name(source, child);
                    }
                }
            }
            get_text(source, Some(node))
        }
        "array_type" => {
            // Extract element type
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "type_identifier" | "scoped_type_identifier" | "generic_type" => {
                            return resolve_type_name(source, child);
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

/// Resolve an annotation name to a simple identifier.
fn resolve_annotation_name(source: &[u8], node: Node) -> String {
    match node.kind() {
        "identifier" => get_text(source, Some(node)),
        "scoped_identifier" => resolve_attribute_chain_java(source, node),
        _ => get_text(source, Some(node)),
    }
}

/// Resolve a dotted attribute chain (e.g., com.foo.Bar).
fn resolve_attribute_chain_java(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    match node.kind() {
        "identifier" => return get_text(source, Some(node)),
        "scoped_identifier" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "identifier" {
                        parts.push(get_text(source, Some(child)));
                    }
                }
            }
            parts.reverse();
            parts.join(".")
        }
        _ => return get_text(source, Some(node)),
    }
}

/// Recursively collect identifiers from a scoped_identifier or identifier node.
/// Tree-sitter-java represents dotted names as nested scoped_identifier nodes:
/// `com.foo.bar.MyClass` = scoped_identifier(scoped_identifier("com.foo.bar"), identifier("MyClass"))
fn collect_scoped_ids(source: &[u8], node: Node, parts: &mut Vec<String>) {
    match node.kind() {
        "identifier" => {
            parts.push(get_text(source, Some(node)));
        }
        "scoped_identifier" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    collect_scoped_ids(source, child, parts);
                }
            }
        }
        _ => {}
    }
}

/// Build import path from import_declaration node.
fn build_import_path(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" => parts.push(get_text(source, Some(child))),
                "scoped_identifier" => {
                    collect_scoped_ids(source, child, &mut parts);
                }
                "asterisk" => {} // `import foo.*` — skip asterisk
                _ => {}
            }
        }
    }
    parts.join(".")
}

/// Check if an import_declaration node includes the `static` keyword.
/// In tree-sitter-java, `static` is an unnamed child (not a named modifier node).
#[allow(dead_code)]
fn has_static_keyword(node: Node) -> bool {
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() && child.kind() == "static" {
                return true;
            }
        }
    }
    false
}

/// Check if an import_declaration node has an asterisk child (wildcard import).
fn has_asterisk(node: Node) -> bool {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "asterisk" {
                return true;
            }
        }
    }
    false
}

/// Check if text is a System call (System.out / System.err).
fn is_system_call(text: &str) -> bool {
    text.starts_with("System.out") || text.starts_with("System.err")
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

    /// Parse Java source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_java::LANGUAGE.into())
            .expect("set java language");
        let tree = parser.parse(source, None).expect("parse java source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "java".to_string());
        JavaExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes<'a>(ctx: &'a ExtractionContext, kind: NodeKind) -> Vec<&'a crate::db::models::NodeRecord> {
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
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_class() {
        let ctx = extract(
            "class MyClass {}\n",
            "src/MyClass.java",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_inheritance() {
        let ctx = extract(
            "class Child extends BaseClass implements Runnable {\n}\n",
            "src/Child.java",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Child");

        let extends = find_edges(&ctx, EdgeKind::Extends);
        assert!(extends.len() >= 1, "Expected at least 1 EXTENDS edge");

        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert!(implements.len() >= 1, "Expected at least 1 IMPLEMENTS edge");
    }

    #[test]
    fn test_extract_nested_class() {
        let ctx = extract(
            "class Outer {\n    class Inner {}\n}\n",
            "src/Outer.java",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 2);
        let names: Vec<&str> = classes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    // ------------------------------------------------------------------
    // Method extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_method() {
        let ctx = extract(
            "class Foo {\n    void bar() {}\n}\n",
            "src/Foo.java",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "bar");
    }

    #[test]
    fn test_extract_multiple_methods() {
        let ctx = extract(
            "class Foo {\n    void bar() {}\n    int baz() { return 0; }\n}\n",
            "src/Foo.java",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 2);
    }

    #[test]
    fn test_extract_constructor() {
        let ctx = extract(
            "class Foo {\n    Foo() {}\n}\n",
            "src/Foo.java",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(!methods.is_empty(), "Constructor should produce a method node");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract(
            "class Foo {\n    void bar() {\n        baz();\n    }\n}\n",
            "src/Foo.java",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"baz"), "Expected 'baz' in call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_chained_call() {
        let ctx = extract(
            "class Foo {\n    void bar() {\n        obj.foo().build();\n    }\n}\n",
            "src/Foo.java",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // Should find "build" (the final method), not "foo"
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"build"), "Chained call should extract final method 'build': {:?}", targets);
    }

    #[test]
    fn test_extract_instantiation() {
        let ctx = extract(
            "class Foo {\n    void bar() {\n        new ArrayList();\n    }\n}\n",
            "src/Foo.java",
        );
        let instantiates = find_edges(&ctx, EdgeKind::Instantiates);
        let targets: Vec<&str> = instantiates.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"ArrayList"), "Expected 'ArrayList' instantiation: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Interface extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_interface() {
        let ctx = extract(
            "interface MyInterface {\n    void doSomething();\n}\n",
            "src/MyInterface.java",
        );
        let interfaces = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(interfaces.len(), 1);
        assert_eq!(interfaces[0].name, "MyInterface");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "doSomething");
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract(
            "enum Color { RED, GREEN, BLUE }\n",
            "src/Color.java",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
    }

    // ------------------------------------------------------------------
    // Field extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_field() {
        let ctx = extract(
            "class Foo {\n    private String name;\n}\n",
            "src/Foo.java",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "name");
    }

    #[test]
    fn test_extract_local_variable() {
        let ctx = extract(
            "class Foo {\n    void bar() {\n        int x = 42;\n    }\n}\n",
            "src/Foo.java",
        );
        let writes = find_edges(&ctx, EdgeKind::Writes);
        let targets: Vec<&str> = writes.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"x"), "Expected 'x' write: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Annotation / decorates extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_override_annotation() {
        let ctx = extract(
            "class Foo {\n    @Override\n    public String toString() { return \"Foo\"; }\n}\n",
            "src/Foo.java",
        );
        let overrides = find_edges(&ctx, EdgeKind::Overrides);
        assert!(!overrides.is_empty(), "Expected OVERRIDES edge for @Override");
    }

    #[test]
    fn test_extract_decorates() {
        let ctx = extract(
            "@Deprecated\nclass Old {\n}\n",
            "src/Old.java",
        );
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> = decorates.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"Deprecated"), "Expected 'Deprecated' decorates: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Package and import extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_package_node() {
        let ctx = extract(
            "package com.example.app;\n\nclass Foo {}\n",
            "src/Foo.java",
        );
        let packages = find_nodes(&ctx, NodeKind::Package);
        assert!(!packages.is_empty(), "Expected package node");
    }

    #[test]
    fn test_extract_imports() {
        let ctx = extract(
            "package com.example;\n\nimport java.util.List;\nimport java.util.Map;\nclass Foo {}\n",
            "src/Foo.java",
        );
        // Both imports are class imports → REFERENCES edges
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(refs.len() >= 2, "Expected at least 2 REFERENCES edges for class imports");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"java.util.List"), "Expected 'java.util.List' REFERENCE: {:?}", targets);
        assert!(targets.contains(&"java.util.Map"), "Expected 'java.util.Map' REFERENCE: {:?}", targets);
    }

    #[test]
    fn test_extract_class_import_creates_references_edge() {
        let ctx = extract(
            "package com.example;\n\nimport com.foo.bar.MyClass;\nclass App {}\n",
            "src/App.java",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge for class import");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar.MyClass"
        );

        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 0, "No IMPORTS edge for class import (uses REFERENCES)");
    }

    #[test]
    fn test_extract_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "package com.example;\n\nimport com.foo.bar.*;\nclass App {}\n",
            "src/App.java",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1, "Expected 1 IMPORTS edge for wildcard import");
        assert_eq!(
            imports[0].target_text.as_deref().unwrap_or(""),
            "com.foo.bar"
        );

        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 0, "No REFERENCES edge for wildcard import");
    }

    #[test]
    fn test_extract_static_import_creates_references_edge() {
        let ctx = extract(
            "package com.example;\n\nimport static com.foo.Utils.helper;\nclass App {}\n",
            "src/App.java",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 1, "Expected 1 REFERENCES edge for static import");
        assert_eq!(
            refs[0].target_text.as_deref().unwrap_or(""),
            "com.foo.Utils.helper"
        );
    }

    #[test]
    fn test_extract_static_wildcard_import_creates_imports_edge() {
        let ctx = extract(
            "package com.example;\n\nimport static com.foo.Utils.*;\nclass App {}\n",
            "src/App.java",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert_eq!(imports.len(), 1, "Expected 1 IMPORTS edge for static wildcard import");
        assert!(imports[0].target_text.as_deref().unwrap_or("").contains("com.foo.Utils"));

        let refs = find_edges(&ctx, EdgeKind::References);
        assert_eq!(refs.len(), 0, "No REFERENCES edge for static wildcard import");
    }

    #[test]
    fn test_extract_imported_call_uses_qualified_target_text() {
        let ctx = extract(
            r#"package com.example;

import com.foo.Utils;

class App {
    void doWork() {
        Utils.helper();
    }
}
"#,
            "src/com/example/App.java",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("com.foo.Utils.helper")),
            "Expected qualified call target_text for imported method: {:?}", targets
        );
    }

    #[test]
    fn test_extract_static_import_call_uses_qualified_target() {
        let ctx = extract(
            r#"package com.example;

import static com.foo.Utils.helper;

class App {
    void doWork() {
        helper();
    }
}
"#,
            "src/com/example/App.java",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("com.foo.Utils.helper")),
            "Expected qualified call target for static imported method: {:?}", targets
        );
    }

    #[test]
    fn test_extract_imported_type_instantiation_uses_qualified_name() {
        let ctx = extract(
            r#"package com.example;

import com.foo.MyService;

class App {
    void doWork() {
        new MyService();
    }
}
"#,
            "src/com/example/App.java",
        );
        let instantiates = find_edges(&ctx, EdgeKind::Instantiates);
        let targets: Vec<&str> = instantiates.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(
            targets.iter().any(|t| t.contains("com.foo.MyService")),
            "Expected qualified instantiation target: {:?}", targets
        );
    }

    // ------------------------------------------------------------------
    // Lambda extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_lambda() {
        let ctx = extract(
            "class Foo {\n    void bar() {\n        Runnable r = () -> { doWork(); };\n    }\n}\n",
            "src/Foo.java",
        );
        let lambdas = find_nodes(&ctx, NodeKind::Lambda);
        assert_eq!(lambdas.len(), 1);
    }

    // ------------------------------------------------------------------
    // Record extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_record() {
        let ctx = extract(
            "record Point(int x, int y) {}\n",
            "src/Point.java",
        );
        let records = find_nodes(&ctx, NodeKind::Record);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].name, "Point");
    }

    // ------------------------------------------------------------------
    // Type reference extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_type_params() {
        let ctx = extract(
            "class Box<T extends Comparable<T>> {\n    T value;\n}\n",
            "src/Box.java",
        );
        let type_refs = find_edges(&ctx, EdgeKind::TypeRef);
        assert!(!type_refs.is_empty(), "Expected TYPE_REF edges for type parameters");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/Empty.java");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            r#"package com.example;

import java.util.List;

public class Service {
    private final Repository repo;

    public Service(Repository repo) {
        this.repo = repo;
    }

    public List<String> getNames() {
        return repo.findAll().stream()
            .map(it -> it.getName())
            .toList();
    }
}
"#,
            "src/Service.java",
        );
        // Just verify it doesn't crash — we should have nodes
        assert!(!ctx.result.nodes.is_empty());
    }
}
