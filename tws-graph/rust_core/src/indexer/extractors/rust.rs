//! Rust language extractor.
//!
//! Extracts symbols and relationships from Rust source files (`.rs`)
//! using the tree-sitter-rust grammar.
//!
//! # Node kinds produced
//! - `function`: fn item at module level
//! - `method`: fn item inside an impl block
//! - `class`: struct_item (maps to `struct` in the schema)
//! - `interface`: trait_item (maps to `trait` in the schema)
//! - `enum`: enum_item
//! - `field`: struct field (field_declaration)
//! - `variable`: let_declaration
//!
//! # Edge kinds produced
//! - `calls`: call_expression + macro_invocation
//! - `contains`: containment (file -> struct -> method)
//! - `imports`: use_declaration + mod_item
//! - `implements`: impl_item for trait (e.g. `impl MyTrait for MyType`)
//! - `decorates`: derive/attribute macros (#[derive(...)], #[cfg_attr(...)])
//! - `type_ref`: type annotation references
//! - `reads`: variable/field read access
//! - `writes`: variable/field write (assignment)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Rust stdlib prefixes — filtered to reduce noise
// ---------------------------------------------------------------------------

/// Common Rust standard library / built-in items filtered from edges.
const RUST_STDLIB_PREFIXES: &[&str] = &[
    "std::", "core::", "alloc::",
];

fn is_rust_stdlib(s: &str) -> bool {
    RUST_STDLIB_PREFIXES.iter().any(|p| s.starts_with(p))
}

/// Simple names that should not produce edges.
const RUST_BUILTINS: &[&str] = &[
    "Some", "None", "Ok", "Err", "true", "false",
    "Self", "self", "String", "Vec", "Option", "Result",
    "Box", "Rc", "Arc", "RefCell", "Cell", "Mutex", "RwLock",
    "HashMap", "HashSet", "BTreeMap", "BTreeSet",
    "i8", "i16", "i32", "i64", "i128", "isize",
    "u8", "u16", "u32", "u64", "u128", "usize",
    "f32", "f64", "bool", "char", "str",
    "drop", "clone", "into", "from",
    "Copy", "Clone", "Debug", "PartialEq", "Eq", "PartialOrd", "Ord",
    "Hash", "Default", "Display", "Send", "Sync",
];

fn is_rust_builtin(s: &str) -> bool {
    RUST_BUILTINS.contains(&s) || is_rust_stdlib(s)
}

// ---------------------------------------------------------------------------
// RustExtractor
// ---------------------------------------------------------------------------

pub struct RustExtractor;

impl Extractor for RustExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["rs"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["rust"]
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

        let mut walker = Walker::new();
        walker.walk_source_file(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker — scope-aware tree walker
// ---------------------------------------------------------------------------

struct Walker {
    /// Stack of struct / enum / trait names for building method targets.
    type_stack: Vec<String>,
    /// Pending attribute items to apply to the next declaration.
    pending_attrs: Vec<String>,
}

impl Walker {
    fn new() -> Self {
        Self {
            type_stack: Vec::new(),
            pending_attrs: Vec::new(),
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
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_declaration(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    fn walk_declaration(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "function_item" => {
                let attrs = std::mem::take(&mut self.pending_attrs);
                self.extract_function(source, node, ctx, parent_id, NodeKind::Function, &attrs)?;
            }
            "struct_item" => {
                let attrs = std::mem::take(&mut self.pending_attrs);
                self.extract_struct(source, node, ctx, parent_id, &attrs)?;
            }
            "enum_item" => {
                let attrs = std::mem::take(&mut self.pending_attrs);
                self.extract_enum(source, node, ctx, parent_id, &attrs)?;
            }
            "trait_item" => {
                let attrs = std::mem::take(&mut self.pending_attrs);
                self.extract_trait(source, node, ctx, parent_id, &attrs)?;
            }
            "impl_item" => {
                self.extract_impl(source, node, ctx, parent_id)?;
            }
            "use_declaration" => {
                self.extract_use(source, node, ctx, parent_id)?;
            }
            "mod_item" => {
                self.extract_mod(source, node, ctx, parent_id)?;
            }
            "attribute_item" => {
                self.collect_attribute(source, node);
            }
            "inner_attribute_item" => {
                // #![...] at module level — skip or record
            }
            "macro_invocation" => {
                // Top-level macro invocation (e.g. macro_rules!)
                // Skip or could potentially extract macro definitions
            }
            _ => {}
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Struct extraction
    // ------------------------------------------------------------------

    fn extract_struct(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        let struct_id = ctx.add_node(NodeKind::Struct, &name, &node, extra);
        ctx.add_edge(parent_id, &struct_id, EdgeKind::Contains, line, None);

        // Derive macros → decorates edges
        for dec in decorators {
            if dec.starts_with("derive(") {
                let inner = &dec[7..dec.len().saturating_sub(1)]; // strip "derive(" and ")"
                for part in inner.split(',') {
                    let trait_name = part.trim();
                    if !trait_name.is_empty() {
                        let target_qn = build_qualified_target(&ctx.file_path, trait_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&struct_id, &target, EdgeKind::Decorates, line, Some(trait_name));
                    }
                }
            } else {
                let target_qn = build_qualified_target(&ctx.file_path, dec);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&struct_id, &target, EdgeKind::Decorates, line, Some(dec));
            }
        }

        // Extract struct fields
        self.type_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "struct");
        ctx.push_scope_node(&struct_id);

        if let Some(body) = node.child_by_field_name("body") {
            // body can be field_declaration_list or ordered_field_declaration_list
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    if child.kind() == "field_declaration" {
                        self.extract_field_declaration(source, child, ctx, &struct_id)?;
                    } else if child.kind() == "function_item" {
                        let fn_attrs: Vec<String> = vec![];
                        self.extract_function(source, child, ctx, &struct_id, NodeKind::Method, &fn_attrs)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.type_stack.pop();
        Ok(struct_id)
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
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, extra);
        ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

        // Derive macros → decorates
        for dec in decorators {
            if dec.starts_with("derive(") {
                let inner = &dec[7..dec.len().saturating_sub(1)];
                for part in inner.split(',') {
                    let trait_name = part.trim();
                    if !trait_name.is_empty() {
                        let target_qn = build_qualified_target(&ctx.file_path, trait_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&enum_id, &target, EdgeKind::Decorates, line, Some(trait_name));
                    }
                }
            }
        }

        // Walk enum body for methods
        self.type_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "enum");
        ctx.push_scope_node(&enum_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    if child.kind() == "function_item" {
                        let fn_attrs: Vec<String> = vec![];
                        self.extract_function(source, child, ctx, &enum_id, NodeKind::Method, &fn_attrs)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.type_stack.pop();
        Ok(enum_id)
    }

    // ------------------------------------------------------------------
    // Trait extraction
    // ------------------------------------------------------------------

    fn extract_trait(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let line = node.start_position().row as u32 + 1;
        let trait_id = ctx.add_node(NodeKind::Interface, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &trait_id, EdgeKind::Contains, line, None);

        // Derive-like decorators
        for dec in decorators {
            if !dec.starts_with("derive(") {
                let target_qn = build_qualified_target(&ctx.file_path, dec);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&trait_id, &target, EdgeKind::Decorates, line, Some(dec));
            }
        }

        // Walk trait body for methods
        self.type_stack.push(name.clone());
        ctx.push_scope_with_kind(&name, "trait");
        ctx.push_scope_node(&trait_id);

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    if child.kind() == "function_item"
                        || child.kind() == "declaration"
                        || child.kind() == "function_signature_item"
                    {
                        // Trait method (may or may not have a body)
                        let fn_attrs: Vec<String> = vec![];
                        self.extract_function(source, child, ctx, &trait_id, NodeKind::Method, &fn_attrs)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.type_stack.pop();
        Ok(trait_id)
    }

    // ------------------------------------------------------------------
    // Impl extraction
    // ------------------------------------------------------------------

    fn extract_impl(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Detect if this is `impl Trait for Type` (implements) or `impl Type`
        let trait_name = node.child_by_field_name("trait");
        let type_node = node.child_by_field_name("type");

        // Determine the type we're implementing methods for
        let impl_type = match type_node {
            Some(tn) => {
                let text = resolve_type_name(source, tn);
                if text.is_empty() { get_text(source, Some(tn)) } else { text }
            }
            None => String::new(),
        };

        if impl_type.is_empty() {
            return Ok(());
        }

        // If there's a trait, add implements edge
        if let Some(trait_node) = trait_name {
            let trait_name = resolve_type_name(source, trait_node);
            if !trait_name.is_empty() && !is_rust_builtin(&trait_name) {
                let target_qn = build_qualified_target(&ctx.file_path, &trait_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                // The source is the type being impl'd
                let type_id = ctx.make_qualified(&impl_type);
                let type_hash = hash_id(&ctx.file_path, &type_id);
                ctx.add_edge(&type_hash, &target, EdgeKind::Implements, line, Some(&trait_name));
            }
        }

        // Register the impl as a scope for methods
        self.type_stack.push(impl_type.clone());
        ctx.push_scope_with_kind(&impl_type, "impl");

        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    if child.kind() == "function_item" {
                        let fn_attrs: Vec<String> = vec![];
                        self.extract_function(source, child, ctx, parent_id, NodeKind::Method, &fn_attrs)?;
                    }
                }
            }
        }

        ctx.pop_scope();
        self.type_stack.pop();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        fn_kind: NodeKind,
        decorators: &[String],
    ) -> anyhow::Result<String> {
        let name = get_text(source, node.child_by_field_name("name"));
        if name.is_empty() {
            return Ok(String::new());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if !decorators.is_empty() {
            if let Ok(json) = serde_json::to_string(decorators) {
                extra.insert("decorators".to_string(), json);
            }
        }

        // Signature
        if let Some(params) = node.child_by_field_name("parameters") {
            let sig = get_text(source, Some(params));
            if !sig.is_empty() {
                extra.insert("signature".to_string(), format!("fn {}({})", name, sig));
            }
        }

        // Check if trait method without body (abstract)
        if node.child_by_field_name("body").is_none() && fn_kind == NodeKind::Method {
            extra.insert("is_abstract".to_string(), "true".to_string());
        }

        let func_id = ctx.add_node(fn_kind, &name, &node, extra);
        ctx.add_edge(parent_id, &func_id, EdgeKind::Contains, line, None);

        // Decorators → decorates edges
        for dec in decorators {
            if !dec.starts_with("derive(") {
                let target_qn = build_qualified_target(&ctx.file_path, dec);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&func_id, &target, EdgeKind::Decorates, line, Some(dec));
            }
        }

        // Return type → type_ref
        if let Some(ret_type) = node.child_by_field_name("return_type") {
            let type_text = resolve_type_name(source, ret_type);
            if !type_text.is_empty() && !is_rust_builtin(&type_text) {
                let target_qn = build_qualified_target(&ctx.file_path, &type_text);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&func_id, &target, EdgeKind::TypeRef, line, Some(&type_text));
            }
        }

        // Push scope and process body for calls
        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&func_id);

        if let Some(body) = node.child_by_field_name("body") {
            self.walk_body_for_calls_depth(source, body, ctx, &func_id, 0)?;
        }

        ctx.pop_scope();
        Ok(func_id)
    }

    // ------------------------------------------------------------------
    // Body walking for calls, macros, let declarations, assignments
    // ------------------------------------------------------------------

    /// Maximum recursion depth for `walk_body_for_calls` to prevent stack
    /// overflow on deeply-nested ASTs.  200 levels is far beyond what any
    /// legitimate source file should contain; hitting this is either a
    /// pathological file or a cycle in the tree-walking logic.
    const MAX_WALK_DEPTH: usize = 30;

    fn walk_body_for_calls(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        self.walk_body_for_calls_depth(source, node, ctx, parent_id, 0)
    }

    fn walk_body_for_calls_depth(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        depth: usize,
    ) -> anyhow::Result<()> {
        if depth >= Self::MAX_WALK_DEPTH {
            return Ok(());
        }
        let next_depth = depth + 1;

        match node.kind() {
            "call_expression" => {
                self.extract_call(source, node, ctx, parent_id)?;
                // Recurse into arguments for nested calls
                if let Some(args) = node.child_by_field_name("arguments") {
                    self.walk_body_for_calls_depth(source, args, ctx, parent_id, next_depth)?;
                }
            }
            "macro_invocation" => {
                self.extract_macro_call(source, node, ctx, parent_id)?;
            }
            "let_declaration" => {
                self.extract_let(source, node, ctx, parent_id)?;
                // Recurse into value and children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls_depth(source, child, ctx, parent_id, next_depth)?;
                    }
                }
            }
            "assignment_expression" | "compound_assignment_expr" => {
                // Record write
                if let Some(left) = node.child_by_field_name("left") {
                    self.record_write(source, left, ctx, parent_id);
                }
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls_depth(source, child, ctx, parent_id, next_depth)?;
                    }
                }
            }
            "field_expression" => {
                self.record_read(source, node, ctx, parent_id);
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls_depth(source, child, ctx, parent_id, next_depth)?;
                    }
                }
            }
            "function_item" | "struct_item" | "enum_item" | "trait_item" | "impl_item" => {
                // Nested declaration — walk as decl
                self.walk_declaration(source, node, ctx, parent_id)?;
            }
            // Recurse into structural nodes
            "block"
            | "if_expression"
            | "match_expression"
            | "match_arm"
            | "while_expression"
            | "loop_expression"
            | "for_expression"
            | "return_expression"
            | "unsafe_block"
            | "closure_expression"
            | "array_expression"
            | "tuple_expression"
            | "struct_expression"
            | "if_let_expression"
            | "while_let_expression" => {
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        self.walk_body_for_calls_depth(source, child, ctx, parent_id, next_depth)?;
                    }
                }
            }
            _ => {
                // Scan for call-related children
                for i in 0..node.named_child_count() {
                    if let Some(child) = node.named_child(i) {
                        let ck = child.kind();
                        if ck == "call_expression"
                            || ck == "macro_invocation"
                            || ck == "let_declaration"
                            || ck == "assignment_expression"
                            || ck == "block"
                            || ck == "field_expression"
                            || ck == "return_expression"
                            || ck == "if_expression"
                            || ck == "match_expression"
                        {
                            self.walk_body_for_calls_depth(source, child, ctx, parent_id, next_depth)?;
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

    fn extract_call(
        &self,
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
                    if !name.is_empty() && !is_rust_builtin(&name) {
                        let target_qn = build_call_target(&ctx.file_path, &self.type_stack, &name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                    }
                }
                "field_expression" => {
                    // self.method() or obj.method()
                    let full = resolve_field_chain(source, f);
                    if !full.is_empty() {
                        let callee = full.rsplitn(2, '.').next().unwrap_or(&full);
                        if !is_rust_builtin(callee) {
                            let target_qn = build_call_target(&ctx.file_path, &self.type_stack, callee);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&full));
                        }
                    }
                }
                "scoped_identifier" => {
                    let name = get_text(source, Some(f));
                    if !name.is_empty() {
                        let ident = name.rsplitn(2, "::").next().unwrap_or(&name);
                        if !is_rust_builtin(ident) && !is_rust_stdlib(&name) {
                            let target_qn = build_qualified_target(&ctx.file_path, ident);
                            let target = hash_id(&ctx.file_path, &target_qn);
                            ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&name));
                        }
                    }
                }
                "generic_function" => {
                    // foo::<Type>(args) — extract the inner function part.
                    // Recurse on the generic_function node itself, which has
                    // the same node shape as a call_expression (a "function"
                    // child containing the actual function name).
                    return self.extract_call(source, f, ctx, parent_id);
                }
                _ => {}
            },
            None => {}
        }

        Ok(())
    }

    /// Extract macro invocation as a calls edge.
    fn extract_macro_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if let Some(macro_node) = node.child_by_field_name("macro") {
            let macro_name = match macro_node.kind() {
                "identifier" => get_text(source, Some(macro_node)),
                "scoped_identifier" => {
                    let full = get_text(source, Some(macro_node));
                    full.rsplitn(2, "::").next().unwrap_or(&full).to_string()
                }
                _ => get_text(source, Some(macro_node)),
            };

            if !macro_name.is_empty() && !is_rust_builtin(&macro_name) {
                let target_qn = build_qualified_target(&ctx.file_path, &macro_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&macro_name));
            }
        }

        // Also walk token_tree for nested invocations
        if let Some(tt) = node.child_by_field_name("token_tree") {
            self.walk_body_for_calls_depth(source, tt, ctx, parent_id, 0)?;
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Let declaration extraction (variable)
    // ------------------------------------------------------------------

    fn extract_let(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Find the pattern (variable name)
        if let Some(pattern) = node.child_by_field_name("pattern") {
            let var_name = match pattern.kind() {
                "identifier" => get_text(source, Some(pattern)),
                "tuple_pattern" => {
                    // For tuple patterns, name the variable "[tuple]_{line}"
                    format!("[tuple]_{}", line)
                }
                "tuple_struct_pattern" => {
                    format!("[struct_pat]_{}", line)
                }
                _ => get_text(source, Some(pattern)),
            };

            if !var_name.is_empty() && !var_name.starts_with('_') {
                let var_id = ctx.add_node(NodeKind::Variable, &var_name, &node, HashMap::new());
                ctx.add_edge(parent_id, &var_id, EdgeKind::Writes, line, Some(&var_name));
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Field declaration extraction
    // ------------------------------------------------------------------

    fn extract_field_declaration(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        if let Some(name_node) = node.child_by_field_name("name") {
            let field_name = get_text(source, Some(name_node));
            if !field_name.is_empty() {
                let field_id = ctx.add_node(NodeKind::Field, &field_name, &node, HashMap::new());
                ctx.add_edge(parent_id, &field_id, EdgeKind::Contains, line, None);

                // Type reference for field
                if let Some(type_node) = node.child_by_field_name("type") {
                    let type_text = resolve_type_name(source, type_node);
                    if !type_text.is_empty() && !is_rust_builtin(&type_text) {
                        let target_qn = build_qualified_target(&ctx.file_path, &type_text);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(&field_id, &target, EdgeKind::TypeRef, line, Some(&type_text));
                    }
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Use declaration (import) extraction
    // ------------------------------------------------------------------

    fn extract_use(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Extract all imported paths
        let paths = self.extract_use_paths(source, node);
        for path in paths {
            if !path.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, &path);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&path));
            }
        }

        Ok(())
    }

    /// Recursively extract all use paths (handles use tree syntax).
    fn extract_use_paths(&self, source: &[u8], node: Node) -> Vec<String> {
        let mut paths = Vec::new();

        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                match child.kind() {
                    "identifier" | "scoped_identifier" => {
                        paths.push(get_text(source, Some(child)));
                    }
                    "scoped_use_list" => {
                        let prefix = if let Some(path_node) = child.child_by_field_name("path") {
                            get_text(source, Some(path_node))
                        } else {
                            String::new()
                        };

                        for j in 0..child.named_child_count() {
                            if let Some(sub) = child.named_child(j) {
                                match sub.kind() {
                                    "identifier" => {
                                        let name = get_text(source, Some(sub));
                                        if prefix.is_empty() {
                                            paths.push(name);
                                        } else {
                                            paths.push(format!("{}::{}", prefix, name));
                                        }
                                    }
                                    "use_list" => {
                                        let sub_paths = self.extract_use_paths(source, sub);
                                        for sp in sub_paths {
                                            if prefix.is_empty() {
                                                paths.push(sp);
                                            } else {
                                                paths.push(format!("{}::{}", prefix, sp));
                                            }
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }
                    }
                    "use_list" => {
                        let sub_paths = self.extract_use_paths(source, child);
                        paths.extend(sub_paths);
                    }
                    _ => {}
                }
            }
        }

        // If no named children matched, try getting the text of a direct path
        if paths.is_empty() {
            if let Some(arg) = node.child_by_field_name("argument") {
                paths.push(get_text(source, Some(arg)));
            }
        }

        paths
    }

    // ------------------------------------------------------------------
    // Mod item extraction
    // ------------------------------------------------------------------

    fn extract_mod(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // mod name;
        if let Some(name_node) = node.child_by_field_name("name") {
            let mod_name = get_text(source, Some(name_node));
            if !mod_name.is_empty() {
                // Treat as import for simplicity
                let target_qn = build_qualified_target(&ctx.file_path, &mod_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&mod_name));
            }
        }

        // If inline module, walk its body
        if let Some(body) = node.child_by_field_name("body") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_declaration(source, child, ctx, parent_id)?;
                }
            }
        }

        Ok(())
    }

    // ------------------------------------------------------------------
    // Attribute collection (for pending decorators)
    // ------------------------------------------------------------------

    fn collect_attribute(&mut self, source: &[u8], node: Node) {
        // Parse the attribute content
        let attr_text = get_text(source, Some(node));

        // Skip doc comments (#[doc = "..."] and inner attributes)
        if attr_text.starts_with("!") {
            return;
        }

        let inner = attr_text.trim_start_matches('#').trim_start_matches('[').trim_end_matches(']');

        if inner.starts_with("derive(") {
            let content = &inner[7..inner.len().saturating_sub(1)]; // strip "derive(" and trailing ")"
            let items: Vec<String> = content.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            if !items.is_empty() {
                let derive_str = format!("derive({})", items.join(", "));
                self.pending_attrs.push(derive_str);
            }
        } else {
            self.pending_attrs.push(inner.to_string());
        }
    }

    // ------------------------------------------------------------------
    // Read / Write tracking
    // ------------------------------------------------------------------

    fn record_write(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let line = node.start_position().row as u32 + 1;
        let name = match node.kind() {
            "identifier" => get_text(source, Some(node)),
            "field_expression" => resolve_field_chain(source, node),
            _ => get_text(source, Some(node)),
        };
        if !name.is_empty() {
            let target_qn = build_qualified_target(&ctx.file_path, &name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Writes, line, Some(&name));
        }
    }

    fn record_read(
        &self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) {
        let line = node.start_position().row as u32 + 1;
        // For field_expression, record the full chain as a read
        let name = resolve_field_chain(source, node);
        if !name.is_empty() && !name.starts_with("self.") {
            let target_qn = build_qualified_target(&ctx.file_path, &name);
            let target = hash_id(&ctx.file_path, &target_qn);
            ctx.add_edge(parent_id, &target, EdgeKind::Reads, line, Some(&name));
        }
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/// Build a target qualified name for a call edge.
fn build_call_target(file_path: &str, type_stack: &[String], callee: &str) -> String {
    if let Some(type_name) = type_stack.last() {
        format!("{file_path}::{type_name}.{callee}")
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
        "type_identifier" | "identifier" => get_text(source, Some(node)),
        "scoped_type_identifier" => get_text(source, Some(node)),
        "generic_type" => {
            // Extract base type from generic_type
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    match child.kind() {
                        "type_identifier" | "identifier" => return get_text(source, Some(child)),
                        "scoped_type_identifier" => return get_text(source, Some(child)),
                        "generic_type" => return resolve_type_name(source, child),
                        _ => {}
                    }
                }
            }
            get_text(source, Some(node))
        }
        "reference_type" | "pointer_type" | "array_type" | "slice_type" | "tuple_type" => {
            // Extract inner type
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

/// Resolve a field_expression chain like `self.field.method` to a dotted string.
fn resolve_field_chain(source: &[u8], node: Node) -> String {
    let mut parts: Vec<String> = Vec::new();

    match node.kind() {
        "identifier" => return get_text(source, Some(node)),
        "field_expression" => {
            if let Some(field_name) = node.child_by_field_name("field") {
                parts.push(get_text(source, Some(field_name)));
            }
            let mut current = node.child_by_field_name("value");
            loop {
                match current {
                    Some(obj) => match obj.kind() {
                        "identifier" => {
                            parts.push(get_text(source, Some(obj)));
                            break;
                        }
                        "field_expression" => {
                            if let Some(r#fn) = obj.child_by_field_name("field") {
                                parts.push(get_text(source, Some(r#fn)));
                            }
                            current = obj.child_by_field_name("value");
                        }
                        "call_expression" => {
                            if let Some(f) = obj.child_by_field_name("function") {
                                parts.push(format!("{}()", resolve_field_chain(source, f)));
                            }
                            break;
                        }
                        "self" | "Self" => {
                            parts.push(get_text(source, Some(obj)));
                            break;
                        }
                        _ => break,
                    },
                    None => break,
                }
            }
        }
        _ => return get_text(source, Some(node)),
    }

    parts.reverse();
    parts.join(".")
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

    /// Parse Rust source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_rust::LANGUAGE.into())
            .expect("set rust language");
        let tree = parser.parse(source, None).expect("parse rust source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "rust".to_string());
        RustExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    /// Helper: find edges of a given kind.
    fn find_edges(ctx: &ExtractionContext, kind: EdgeKind) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_function() {
        let ctx = extract("fn my_func() {}\n", "src/test.rs");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "my_func");
    }

    #[test]
    fn test_extract_function_with_params() {
        let ctx = extract("fn add(x: i32, y: i32) -> i32 { x + y }\n", "src/test.rs");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 1);
        assert_eq!(funcs[0].name, "add");
        assert!(funcs[0].signature.is_some());
    }

    #[test]
    fn test_extract_multiple_functions() {
        let ctx = extract("fn foo() {}\nfn bar() {}\n", "src/test.rs");
        let funcs = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(funcs.len(), 2);
        let names: Vec<&str> = funcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"foo"));
        assert!(names.contains(&"bar"));
    }

    // ------------------------------------------------------------------
    // Struct (class) extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_struct() {
        let ctx = extract("struct MyStruct {\n    x: i32,\n}\n", "src/test.rs");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "MyStruct");
    }

    #[test]
    fn test_extract_tuple_struct() {
        let ctx = extract("struct Point(i32, i32);\n", "src/test.rs");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Point");
    }

    #[test]
    fn test_extract_unit_struct() {
        let ctx = extract("struct Unit;\n", "src/test.rs");
        let structs = find_nodes(&ctx, NodeKind::Struct);
        assert_eq!(structs.len(), 1);
        assert_eq!(structs[0].name, "Unit");
    }

    #[test]
    fn test_extract_struct_fields() {
        let ctx = extract("struct Rect {\n    width: f64,\n    height: f64,\n}\n", "src/test.rs");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2);
        let names: Vec<&str> = fields.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"width"));
        assert!(names.contains(&"height"));
    }

    // ------------------------------------------------------------------
    // Enum extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_enum() {
        let ctx = extract("enum Color {\n    Red,\n    Green,\n    Blue,\n}\n", "src/test.rs");
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert_eq!(enums.len(), 1);
        assert_eq!(enums[0].name, "Color");
    }

    // ------------------------------------------------------------------
    // Trait (interface) extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_trait() {
        let ctx = extract("trait MyTrait {\n    fn do_thing(&self);\n}\n", "src/test.rs");
        let traits = find_nodes(&ctx, NodeKind::Interface);
        assert_eq!(traits.len(), 1);
        assert_eq!(traits[0].name, "MyTrait");

        // Should have one abstract method
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "do_thing");
    }

    // ------------------------------------------------------------------
    // Impl extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_impl_block() {
        let ctx = extract("struct Foo;\nimpl Foo {\n    fn new() -> Self { Foo }\n    fn bar(&self) {}\n}\n", "src/test.rs");
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 2);
        let names: Vec<&str> = methods.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"new"));
        assert!(names.contains(&"bar"));
    }

    #[test]
    fn test_extract_trait_impl() {
        let ctx = extract("trait MyTrait {}\nstruct MyType;\nimpl MyTrait for MyType {}\n", "src/test.rs");
        let implements = find_edges(&ctx, EdgeKind::Implements);
        assert!(implements.len() >= 1, "Expected IMPLEMENTS edge for trait impl");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract("fn foo() {\n    bar();\n}\n", "src/test.rs");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"bar"), "Expected 'bar' in call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_macro_call() {
        let ctx = extract("fn foo() {\n    println!(\"hello\");\n}\n", "src/test.rs");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"println"), "Expected 'println' in macro call targets: {:?}", targets);
    }

    #[test]
    fn test_extract_method_call() {
        let ctx = extract("fn foo() {\n    let v = vec![1, 2, 3];\n    v.len();\n}\n", "src/test.rs");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("len")), "Expected method call 'len': {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Use (import) extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_use_declaration() {
        let ctx = extract("use std::collections::HashMap;\nuse std::path::{Path, PathBuf};\nfn main() {}\n", "src/test.rs");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 1, "Expected at least 1 import edge");
    }

    #[test]
    fn test_extract_self_use() {
        let ctx = extract("use self::module::foo;\nfn main() {}\n", "src/test.rs");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected import edge for self::module::foo");
    }

    // ------------------------------------------------------------------
    // Derive macro (decorates) extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_derive_macro() {
        let ctx = extract("#[derive(Debug, Clone)]\nstruct MyStruct;\n", "src/test.rs");
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> = decorates.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"Debug"), "Expected 'Debug' in decorates: {:?}", targets);
        assert!(targets.contains(&"Clone"), "Expected 'Clone' in decorates: {:?}", targets);
    }

    #[test]
    fn test_extract_derive_on_enum() {
        let ctx = extract("#[derive(Debug)]\nenum Color { Red, Green }\n", "src/test.rs");
        let decorates = find_edges(&ctx, EdgeKind::Decorates);
        let targets: Vec<&str> = decorates.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"Debug"), "Expected 'Debug' in enum decorates: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Mod declaration extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_mod_declaration() {
        let ctx = extract("mod my_module;\nfn main() {}\n", "src/test.rs");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"my_module"), "Expected 'my_module' in imports: {:?}", targets);
    }

    // ------------------------------------------------------------------
    // Variable extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_let_variable() {
        let ctx = extract("fn foo() {\n    let x = 42;\n}\n", "src/test.rs");
        let writes = find_edges(&ctx, EdgeKind::Writes);
        let targets: Vec<&str> = writes.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"x"), "Expected 'x' write: {:?}", targets);
    }

    #[test]
    fn test_extract_multiple_let_variables() {
        let ctx = extract("fn foo() {\n    let a = 1;\n    let b = 2;\n    let c = 3;\n}\n", "src/test.rs");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 3);
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_file_contains_struct() {
        let ctx = extract("struct Foo;\n", "src/test.rs");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.rs");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_thread_spawn_closure() {
        let ctx = extract(
            r#"use std::sync::mpsc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use std::path::PathBuf;
use std::collections::HashMap;

pub struct Watcher {
    root: PathBuf,
    interval: Duration,
    running: Arc<AtomicBool>,
}
impl Watcher {
    pub fn start(&mut self) -> mpsc::Receiver<()> {
        let (tx, rx) = mpsc::channel();
        let root = self.root.clone();
        let interval = self.interval;
        let running = Arc::clone(&self.running);
        running.store(true, Ordering::SeqCst);
        std::thread::Builder::new()
            .name("test".into())
            .spawn(move || {
                let mut last: HashMap<PathBuf, u64> = HashMap::new();
                while running.load(Ordering::SeqCst) {
                    std::thread::sleep(interval);
                    if tx.send(()).is_err() {
                        running.store(false, Ordering::SeqCst);
                        return;
                    }
                }
            })
            .expect("spawn");
        rx
    }
}
"#,
            "src/watcher.rs",
        );
        assert!(!ctx.result.nodes.is_empty());
    }

    #[test]
    fn test_thread_spawn_via_registry() {
        use crate::indexer::registry::Registry;
        let source = r#"use std::sync::mpsc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
pub struct W { r: Arc<AtomicBool> }
impl W {
    pub fn start(&mut self) -> mpsc::Receiver<()> {
        let (tx, rx) = mpsc::channel();
        let r = Arc::clone(&self.r);
        r.store(true, Ordering::SeqCst);
        std::thread::spawn(move || {
            while r.load(Ordering::SeqCst) {
                std::thread::sleep(std::time::Duration::from_secs(1));
                if tx.send(()).is_err() { return; }
            }
        });
        rx
    }
}
"#;
        let mut parser = tree_sitter::Parser::new();
        parser.set_language(&tree_sitter_rust::LANGUAGE.into()).unwrap();
        let tree = parser.parse(source, None).unwrap();

        let mut ctx = ExtractionContext::new("src/w.rs".to_string(), "rust".to_string());
        let mut registry = Registry::new();
        registry.register(Box::new(super::RustExtractor));

        let extractor = registry.find_by_extension("rs").unwrap();
        extractor.extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract via registry should succeed");
        assert!(!ctx.result.nodes.is_empty());
    }

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            r#"use std::collections::HashMap;

#[derive(Debug)]
pub struct Service {
    name: String,
}

impl Service {
    pub fn new(name: &str) -> Self {
        let result = Self { name: name.to_string() };
        result
    }

    pub fn process(&self, data: Vec<i32>) -> Option<i32> {
        let filtered: Vec<i32> = data.iter().filter(|x| **x > 0).copied().collect();
        if filtered.is_empty() {
            return None;
        }
        Some(filtered.iter().sum())
    }
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let srv = Service::new("test");
    let r = srv.process(vec![1, 2, 3]);
    println!("{:?}", r);
    Ok(())
}
"#,
            "src/service.rs",
        );
        assert!(!ctx.result.nodes.is_empty());
    }
}
