//! Ruby language extractor.
//!
//! Extracts symbols and relationships from Ruby source files (`.rb`)
//! using the tree-sitter-ruby grammar.
//!
//! # Node kinds produced
//! - `class`: class definition
//! - `module`: module definition
//! - `method`: method/singleton_method definitions
//! - `constant`: constant assignment (CONST = value)
//! - `variable`: module-level variable assignment
//! - `attribute`: attr_accessor/attr_reader/attr_writer declarations
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: call expressions (regular method calls)
//! - `contains`: containment (file → class → method)
//! - `imports`: require/require_relative/load calls
//! - `implements`: include calls (mixin inclusion)
//! - `extends`: extend calls (class-level extension)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Ruby built-in names — filtered from calls to reduce noise
// ---------------------------------------------------------------------------

const RUBY_BUILTINS: &[&str] = &[
    "puts", "print", "p", "pp", "raise", "fail", "exit", "abort",
    "loop", "lambda", "proc", "binding", "eval", "class_eval",
    "instance_eval", "define_method", "send", "public_send", "__send__",
    "respond_to?", "is_a?", "kind_of?", "instance_of?", "nil?", "equal?",
    "eql?", "hash", "to_s", "to_i", "to_f", "to_a", "to_h", "inspect",
    "freeze", "frozen?", "tainted?", "taint", "untaint", "methods",
    "public_methods", "protected_methods", "private_methods",
    "instance_variables", "class", "superclass", "ancestors",
    "dup", "clone", "display", "object_id", "__id__",
];

fn is_ruby_builtin(name: &str) -> bool {
    RUBY_BUILTINS.contains(&name)
}

// ---------------------------------------------------------------------------
// RubyExtractor
// ---------------------------------------------------------------------------

pub struct RubyExtractor;

impl Extractor for RubyExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["rb"]
    }

    fn languages(&self) -> Vec<&'static str> {
        vec!["ruby"]
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
        walker.walk_program(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Walker
// ---------------------------------------------------------------------------

struct Walker {
    /// Track whether we are inside a class scope (for method context).
    in_class: bool,
}

impl Walker {
    fn new() -> Self {
        Self { in_class: false }
    }

    /// Walk the program node (top-level).
    fn walk_program(
        &mut self,
        source: &[u8],
        program: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..program.named_child_count() {
            if let Some(child) = program.named_child(i) {
                self.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    /// Main recursive dispatcher.
    fn walk_node(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            "class" => {
                self.extract_class(source, node, ctx, parent_id)?;
            }
            "module" => {
                self.extract_module(source, node, ctx, parent_id)?;
            }
            "method" => {
                self.extract_method(source, node, ctx, parent_id, false)?;
            }
            "singleton_method" => {
                self.extract_method(source, node, ctx, parent_id, true)?;
            }
            "call" => {
                self.extract_call(source, node, ctx, parent_id)?;
            }
            "assignment" => {
                self.extract_assignment(source, node, ctx, parent_id)?;
            }
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

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    fn extract_class(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        // Class name is in a "constant" child node
        let const_node = find_child_by_kind(node, "constant");
        let name = get_text(source, const_node);
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let line = node.start_position().row as u32 + 1;
        let class_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        // Superclass: look for superclass child (another constant)
        if let Some(superclass) = find_child_by_kind(node, "superclass") {
            let sc_name = get_text(source, find_child_by_kind(superclass, "constant"));
            if !sc_name.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, &sc_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(&class_id, &target, EdgeKind::Extends, line, Some(&sc_name));
            }
        }

        ctx.push_scope_with_kind(&name, "class");
        ctx.push_scope_node(&class_id);
        self.in_class = true;

        // Walk body_statement
        if let Some(body) = find_child_by_kind(node, "body_statement") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_node(source, child, ctx, &class_id)?;
                }
            }
        }

        ctx.pop_scope();
        self.in_class = false;
        Ok(())
    }

    // ------------------------------------------------------------------
    // Module extraction
    // ------------------------------------------------------------------

    fn extract_module(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let const_node = find_child_by_kind(node, "constant");
        let name = get_text(source, const_node);
        if name.is_empty() {
            for i in 0..node.named_child_count() {
                if let Some(c) = node.named_child(i) {
                    self.walk_node(source, c, ctx, parent_id)?;
                }
            }
            return Ok(());
        }

        let line = node.start_position().row as u32 + 1;
        let module_id = ctx.add_node(NodeKind::Module, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &module_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "module");
        ctx.push_scope_node(&module_id);

        if let Some(body) = find_child_by_kind(node, "body_statement") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_node(source, child, ctx, &module_id)?;
                }
            }
        }

        ctx.pop_scope();
        Ok(())
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
        is_singleton: bool,
    ) -> anyhow::Result<()> {
        // Method name: find the last identifier child that is not "self"
        let name = resolve_method_name(source, node);
        if name.is_empty() {
            return Ok(());
        }

        let mut extra = HashMap::new();
        let line = node.start_position().row as u32 + 1;

        if is_singleton {
            extra.insert("decorators".to_string(), "[\"class_method\"]".to_string());
        }

        let method_id = ctx.add_node(NodeKind::Method, &name, &node, extra);
        ctx.add_edge(parent_id, &method_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "method");
        ctx.push_scope_node(&method_id);

        if let Some(body) = find_child_by_kind(node, "body_statement") {
            for i in 0..body.named_child_count() {
                if let Some(child) = body.named_child(i) {
                    self.walk_node(source, child, ctx, &method_id)?;
                }
            }
        }

        ctx.pop_scope();
        Ok(())
    }

    // ------------------------------------------------------------------
    // Assignment extraction (constants and variables)
    // ------------------------------------------------------------------

    fn extract_assignment(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Check for constant assignment: CONST = value
        let const_node = find_child_by_kind(node, "constant");
        if let Some(cn) = const_node {
            let const_name = get_text(source, Some(cn));
            if !const_name.is_empty() && is_constant_name(&const_name) {
                let const_id = ctx.add_node(NodeKind::Constant, &const_name, &node, HashMap::new());
                ctx.add_edge(parent_id, &const_id, EdgeKind::Contains, line, None);
            }
        }

        // Walk children for calls
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let ck = child.kind();
                if ck == "call"
                    || ck == "class"
                    || ck == "module"
                    || ck == "method"
                {
                    self.walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
        Ok(())
    }

    // ------------------------------------------------------------------
    // Call extraction (handles all Ruby call expressions)
    // ------------------------------------------------------------------

    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Get the first identifier (method name)
        let method_ident = find_child_by_kind(node, "identifier");
        let method_name = get_text(source, method_ident);

        if method_name.is_empty() {
            // Walk children for nested calls
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    let ck = child.kind();
                    if ck == "call"
                        || ck == "class"
                        || ck == "module"
                        || ck == "method"
                        || ck == "assignment"
                    {
                        self.walk_node(source, child, ctx, parent_id)?;
                    }
                }
            }
            return Ok(());
        }

        // --- require / require_relative / load ---
        if method_name == "require"
            || method_name == "require_relative"
            || method_name == "load"
        {
            let arg_list = find_child_by_kind(node, "argument_list");
            if let Some(args) = arg_list {
                let str_node = find_child_by_kind(args, "string");
                if let Some(sn) = str_node {
                    let text = get_text(source, Some(sn));
                    let module_name = text.trim_matches(|c| c == '\'' || c == '"');
                    if !module_name.is_empty() {
                        let target_qn = build_qualified_target(&ctx.file_path, module_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Imports,
                            line,
                            Some(module_name),
                        );
                    }
                }
                // Also check for string_content inside interpolated strings
                let str_content = find_child_by_kind(args, "string_content");
                if let Some(sc) = str_content {
                    let module_name = get_text(source, Some(sc));
                    if !module_name.is_empty() {
                        let target_qn = build_qualified_target(&ctx.file_path, &module_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Imports,
                            line,
                            Some(&module_name),
                        );
                    }
                }
            }
            // Walk children for nested calls in arguments
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "argument_list" {
                        for j in 0..child.named_child_count() {
                            if let Some(inner) = child.named_child(j) {
                                if inner.kind() == "call" {
                                    self.walk_node(source, inner, ctx, parent_id)?;
                                }
                            }
                        }
                    }
                }
            }
            return Ok(());
        }

        // --- include (mixin) ---
        if method_name == "include" {
            let arg_list = find_child_by_kind(node, "argument_list");
            if let Some(args) = arg_list {
                let const_name = resolve_argument_constant(source, args);
                if !const_name.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &const_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        parent_id,
                        &target,
                        EdgeKind::Implements,
                        line,
                        Some(&const_name),
                    );
                }
            }
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "argument_list" {
                        for j in 0..child.named_child_count() {
                            if let Some(inner) = child.named_child(j) {
                                if inner.kind() == "call" {
                                    self.walk_node(source, inner, ctx, parent_id)?;
                                }
                            }
                        }
                    }
                }
            }
            return Ok(());
        }

        // --- extend ---
        if method_name == "extend" {
            let arg_list = find_child_by_kind(node, "argument_list");
            if let Some(args) = arg_list {
                let const_name = resolve_argument_constant(source, args);
                if !const_name.is_empty() {
                    let target_qn = build_qualified_target(&ctx.file_path, &const_name);
                    let target = hash_id(&ctx.file_path, &target_qn);
                    ctx.add_edge(
                        parent_id,
                        &target,
                        EdgeKind::Extends,
                        line,
                        Some(&const_name),
                    );
                }
            }
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "argument_list" {
                        for j in 0..child.named_child_count() {
                            if let Some(inner) = child.named_child(j) {
                                if inner.kind() == "call" {
                                    self.walk_node(source, inner, ctx, parent_id)?;
                                }
                            }
                        }
                    }
                }
            }
            return Ok(());
        }

        // --- attr_accessor / attr_reader / attr_writer ---
        if method_name == "attr_accessor"
            || method_name == "attr_reader"
            || method_name == "attr_writer"
        {
            let arg_list = find_child_by_kind(node, "argument_list");
            if let Some(args) = arg_list {
                for i in 0..args.named_child_count() {
                    if let Some(child) = args.named_child(i) {
                        let attr_name = match child.kind() {
                            "simple_symbol" => {
                                let text = get_text(source, Some(child));
                                text.trim_start_matches(':').to_string()
                            }
                            "string" => {
                                let text = get_text(source, Some(child));
                                text.trim_matches(|c| c == '\'' || c == '"')
                                    .to_string()
                            }
                            "identifier" => get_text(source, Some(child)),
                            _ => continue,
                        };
                        if !attr_name.is_empty() {
                            let attr_id = ctx.add_node(
                                NodeKind::Attribute,
                                &attr_name,
                                &node,
                                HashMap::new(),
                            );
                            ctx.add_edge(
                                parent_id,
                                &attr_id,
                                EdgeKind::Contains,
                                line,
                                None,
                            );
                        }
                    }
                }
            }
            return Ok(());
        }

        // --- regular method call ---
        if !is_ruby_builtin(&method_name) {
            let callee_name = resolve_call_target(source, node, &method_name);
            if !callee_name.is_empty() {
                let target_qn = build_qualified_target(&ctx.file_path, &callee_name);
                let target = hash_id(&ctx.file_path, &target_qn);
                ctx.add_edge(
                    parent_id,
                    &target,
                    EdgeKind::Calls,
                    line,
                    Some(&callee_name),
                );
            }
        }

        // Walk children for nested calls
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let ck = child.kind();
                if ck == "call"
                    || ck == "class"
                    || ck == "module"
                    || ck == "method"
                    || ck == "do_block"
                    || ck == "block"
                {
                    self.walk_node(source, child, ctx, parent_id)?;
                }
                if ck == "argument_list" {
                    for j in 0..child.named_child_count() {
                        if let Some(inner) = child.named_child(j) {
                            if inner.kind() == "call" || inner.kind() == "do_block" {
                                self.walk_node(source, inner, ctx, parent_id)?;
                            }
                        }
                    }
                }
            }
        }

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

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

/// Find the first direct named child with the given kind.
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

/// Check if a name follows Ruby constant naming convention (starts with uppercase).
fn is_constant_name(name: &str) -> bool {
    name.chars()
        .next()
        .map(|c| c.is_uppercase())
        .unwrap_or(false)
}

/// Resolve the actual method name from a method/singleton_method node.
/// Skips "self." prefix and returns the actual method identifier.
fn resolve_method_name(source: &[u8], node: Node) -> String {
    let mut identifiers: Vec<String> = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "identifier" {
                identifiers.push(get_text(source, Some(child)));
            }
        }
    }

    if identifiers.is_empty() {
        return String::new();
    }

    // If first is "self", return the second; otherwise return the last
    if identifiers.len() >= 2 && identifiers[0] == "self" {
        return identifiers[1].clone();
    }
    identifiers.last().cloned().unwrap_or_default()
}

/// Resolve the constant name from an argument_list, handling both simple
/// `constant` nodes and `scope_resolution` nodes (e.g. `ActiveSupport::Concern`).
fn resolve_argument_constant(source: &[u8], arg_list: Node) -> String {
    // Try simple constant first
    let const_node = find_child_by_kind(arg_list, "constant");
    if let Some(cn) = const_node {
        return get_text(source, Some(cn));
    }
    // Try scope_resolution (e.g. ActiveSupport::Concern)
    let sr = find_child_by_kind(arg_list, "scope_resolution");
    if let Some(sr_node) = sr {
        let mut parts: Vec<String> = Vec::new();
        for i in 0..sr_node.named_child_count() {
            if let Some(child) = sr_node.named_child(i) {
                if child.kind() == "constant" {
                    parts.push(get_text(source, Some(child)));
                }
            }
        }
        return parts.join("::");
    }
    String::new()
}

/// Resolve the target name for a Ruby call expression.
/// Handles chained calls (obj.method → "obj.method") and simple calls.
fn resolve_call_target(source: &[u8], node: Node, first_ident: &str) -> String {
    // Collect all identifiers and receiver parts
    let mut identifiers: Vec<String> = Vec::new();
    let mut receiver: Option<String> = None;

    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" => {
                    identifiers.push(get_text(source, Some(child)));
                }
                "instance_variable" | "constant" => {
                    receiver = Some(get_text(source, Some(child)));
                }
                _ => {}
            }
        }
    }

    if identifiers.is_empty() {
        return first_ident.to_string();
    }

    // The last identifier is the method name
    let method_name = identifiers.last().cloned().unwrap_or_default();

    // If multiple identifiers, the first is the receiver
    let receiver = if identifiers.len() > 1 {
        identifiers.first().cloned()
    } else {
        receiver
    };

    match receiver {
        Some(recv) => format!("{}.{}", recv, method_name),
        None => method_name,
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

    /// Parse Ruby source and invoke the extractor.
    fn extract(source: &str, file_path: &str) -> ExtractionContext {
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_ruby::LANGUAGE.into())
            .expect("set ruby language");
        let tree = parser.parse(source, None).expect("parse ruby source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "ruby".to_string());
        RubyExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    /// Helper: find nodes of a given kind.
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

    /// Helper: find edges of a given kind.
    fn find_edges(
        ctx: &ExtractionContext,
        kind: EdgeKind,
    ) -> Vec<&crate::db::models::EdgeRecord> {
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
    fn test_extract_class() {
        let ctx = extract("class MyClass\nend\n", "src/test.rb");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "MyClass");
    }

    #[test]
    fn test_extract_class_with_inheritance() {
        let ctx = extract("class Child < Parent\nend\n", "src/test.rb");
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1);
        assert_eq!(classes[0].name, "Child");

        let extends = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(targets.contains(&"Parent"), "Expected Parent extends: {:?}", targets);
    }

    #[test]
    fn test_class_contains_edge() {
        let ctx = extract("class Foo\nend\n", "src/test.rb");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Module extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_module() {
        let ctx = extract("module MyModule\nend\n", "src/test.rb");
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "MyModule");
    }

    #[test]
    fn test_extract_nested_module() {
        let ctx = extract(
            "module Outer\n  module Inner\n  end\nend\n",
            "src/test.rb",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 2, "Expected 2 modules");
        let names: Vec<&str> = modules.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    // ------------------------------------------------------------------
    // Method extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_instance_method() {
        let ctx = extract(
            "class MyClass\n  def my_method\n  end\nend\n",
            "src/test.rb",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "my_method");
    }

    #[test]
    fn test_extract_singleton_method() {
        let ctx = extract(
            "class MyClass\n  def self.create\n  end\nend\n",
            "src/test.rb",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 1);
        assert_eq!(methods[0].name, "create");
        // Check class_method decorator
        if let Some(decorators) = &methods[0].decorators {
            assert!(
                decorators.contains("class_method"),
                "Expected class_method decorator"
            );
        }
    }

    #[test]
    fn test_extract_multiple_methods() {
        let ctx = extract(
            "class MyClass\n  def method_a; end\n  def method_b; end\nend\n",
            "src/test.rb",
        );
        let methods = find_nodes(&ctx, NodeKind::Method);
        assert_eq!(methods.len(), 2);
    }

    // ------------------------------------------------------------------
    // Constant extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_constant() {
        let ctx = extract("MAX_SIZE = 100\n", "src/test.rb");
        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert_eq!(constants.len(), 1);
        assert_eq!(constants[0].name, "MAX_SIZE");
    }

    #[test]
    fn test_no_constant_for_lowercase() {
        let ctx = extract("my_var = 42\n", "src/test.rb");
        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert!(constants.is_empty(), "Lowercase should not be a constant");
    }

    // ------------------------------------------------------------------
    // attr_accessor / attr_reader / attr_writer
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_attr_accessor() {
        let ctx = extract(
            "class User\n  attr_accessor :name, :email\nend\n",
            "src/test.rb",
        );
        let attributes = find_nodes(&ctx, NodeKind::Attribute);
        assert_eq!(attributes.len(), 2);
        let names: Vec<&str> = attributes.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"name"));
        assert!(names.contains(&"email"));
    }

    #[test]
    fn test_extract_attr_reader() {
        let ctx = extract(
            "class User\n  attr_reader :id\nend\n",
            "src/test.rb",
        );
        let attributes = find_nodes(&ctx, NodeKind::Attribute);
        assert_eq!(attributes.len(), 1);
        assert_eq!(attributes[0].name, "id");
    }

    // ------------------------------------------------------------------
    // Require / require_relative / load
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_require() {
        let ctx = extract("require 'json'\n", "src/test.rb");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"json"),
            "Expected 'json' in imports: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_require_relative() {
        let ctx = extract("require_relative '../lib/helper'\n", "src/test.rb");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.iter().any(|t| t.contains("helper")),
            "Expected 'helper' in imports: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Include and extend
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_include() {
        let ctx = extract(
            "class MyClass\n  include Enumerable\nend\n",
            "src/test.rb",
        );
        let implements = find_edges(&ctx, EdgeKind::Implements);
        let targets: Vec<&str> = implements
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"Enumerable"),
            "Expected Enumerable in implements: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_extend() {
        let ctx = extract(
            "class MyClass\n  extend ActiveSupport::Concern\nend\n",
            "src/test.rb",
        );
        let extends = find_edges(&ctx, EdgeKind::Extends);
        let targets: Vec<&str> = extends
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        // Note: tree-sitter parses ActiveSupport::Concern as nested constants,
        // so we look for the first constant (ActiveSupport)
        assert!(
            targets.iter().any(|t| t.contains("ActiveSupport") || t.contains("Concern")),
            "Expected ActiveSupport or Concern in extends: {:?}",
            targets
        );
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_extract_simple_call() {
        let ctx = extract(
            "def foo\n  bar()\nend\n",
            "src/test.rb",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(
            targets.contains(&"bar"),
            "Expected 'bar' in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_extract_chained_call() {
        let ctx = extract(
            "def foo\n  obj.method()\nend\n",
            "src/test.rb",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        // Chained calls produce "obj.method" as target_text
        assert!(
            targets.iter().any(|t| t.contains("method")),
            "Expected 'method' in call targets: {:?}",
            targets
        );
    }

    #[test]
    fn test_filter_builtin_calls() {
        let ctx = extract(
            "def foo\n  puts 'hello'\n  p 'world'\nend\n",
            "src/test.rb",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls
            .iter()
            .map(|e| e.target_text.as_deref().unwrap_or(""))
            .collect();
        assert!(!targets.contains(&"puts"), "Should filter puts");
        assert!(!targets.contains(&"p"), "Should filter p");
    }

    // ------------------------------------------------------------------
    // File node
    // ------------------------------------------------------------------

    #[test]
    fn test_file_node_exists() {
        let ctx = extract("# comment only\n", "src/test.rb");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.rb");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_comment_only() {
        let ctx = extract("# just a comment\n", "src/comments.rb");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_no_crash_on_complex_code() {
        let ctx = extract(
            r#"require 'json'

module MyApp
  class User
    include Comparable

    attr_accessor :name, :email

    MAX_LOGIN_ATTEMPTS = 5

    def initialize(name, email)
      @name = name
      @email = email
    end

    def self.from_json(json_str)
      data = JSON.parse(json_str)
      new(data['name'], data['email'])
    end

    def save
      validate!
      db.insert(self)
    end

    private

    def validate!
      raise "Invalid" unless valid?
    end
  end
end
"#,
            "src/user.rb",
        );
        // Verify it doesn't crash and produces nodes
        assert!(!ctx.result.nodes.is_empty());

        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1, "Expected 1 module");

        let classes = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(classes.len(), 1, "Expected 1 class");

        let methods = find_nodes(&ctx, NodeKind::Method);
        assert!(methods.len() >= 4, "Expected at least 4 methods, got {}", methods.len());

        let attributes = find_nodes(&ctx, NodeKind::Attribute);
        assert_eq!(attributes.len(), 2, "Expected 2 attributes");

        let constants = find_nodes(&ctx, NodeKind::Constant);
        assert_eq!(constants.len(), 1, "Expected 1 constant");

        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected some CALLS edges");

        let requires = find_edges(&ctx, EdgeKind::Imports);
        assert!(!requires.is_empty(), "Expected IMPORTS edges for require");
    }
}
