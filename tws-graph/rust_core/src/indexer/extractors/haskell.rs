//! Haskell language extractor.
//!
//! Extracts symbols and relationships from Haskell source files (`.hs`)
//! using the tree-sitter-haskell grammar.
//!
//! # Node kinds produced
//! - `module`: module declaration
//! - `function`: function definition / binding
//! - `signature`: type signature
//! - `type_def`: data/newtype declarations
//! - `class`: class declaration
//! - `instance`: instance declaration
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function application (apply nodes)
//! - `contains`: containment (file -> module -> function)
//! - `imports`: import declarations
//! - `references`: cross-file references for imported symbols (v7.3.0)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// HaskellWalker — scope-aware tree walker with imported_names tracking
// ---------------------------------------------------------------------------

struct HaskellWalker {
    /// File node id (used as parent for top-level declarations).
    file_id: String,
    /// Maps local name → qualified import reference (module::symbol).
    imported_names: HashMap<String, String>,
}

impl HaskellWalker {
    fn new(file_id: String) -> Self {
        Self {
            file_id,
            imported_names: HashMap::new(),
        }
    }

    /// Qualify a call target name using imported_names.
    fn qualify_call_target(&self, name: &str) -> String {
        // Direct match: name was imported via import Foo (bar)
        if let Some(qualified) = self.imported_names.get(name) {
            return qualified.clone();
        }
        // Check if the first segment of a qualified name maps to an imported module
        if let Some(dot_pos) = name.find('.') {
            let first = &name[..dot_pos];
            if let Some(qualified) = self.imported_names.get(first) {
                let rest = &name[dot_pos + 1..];
                return format!("{}::{}", qualified, rest);
            }
        }
        name.to_string()
    }
}

// ---------------------------------------------------------------------------
// HaskellExtractor
// ---------------------------------------------------------------------------

pub struct HaskellExtractor;

impl Extractor for HaskellExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["hs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["haskell"]
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
                .unwrap_or("Main")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        let mut walker = HaskellWalker::new(file_id.clone());
        walker.walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking (Walker methods)
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn walk_node(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        match node.kind() {
            // Module declaration — the grammar uses "module" both for module decl
            // and module references. We detect the declaration by checking if the
            // node's parent is "header".
            "module" => {
                if let Some(parent) = node.parent() {
                    if parent.kind() == "header" {
                        return self.extract_module(source, node, ctx, parent_id);
                    }
                }
                self.walk_all_children(source, node, ctx, parent_id)?;
            }
            // Import
            "import" => {
                return self.extract_import(source, node, ctx, parent_id);
            }
            // Type signature
            "signature" => {
                return self.extract_signature(source, node, ctx, parent_id);
            }
            // Function definition (with patterns)
            "function" => {
                return self.extract_function(source, node, ctx, parent_id);
            }
            // Simple binding (non-pattern)
            "bind" => {
                return self.extract_bind(source, node, ctx, parent_id);
            }
            // Function application (calls)
            "apply" => {
                return self.extract_call(source, node, ctx, parent_id);
            }
            // Infix application
            "infix" => {
                return self.extract_call(source, node, ctx, parent_id);
            }
            // Data type declaration
            "data_type" => {
                return self.extract_type_def(source, node, ctx, parent_id, "data");
            }
            // Newtype declaration
            "newtype" => {
                return self.extract_type_def(source, node, ctx, parent_id, "newtype");
            }
            // Class declaration
            "class" => {
                return self.extract_class(source, node, ctx, parent_id);
            }
            // Instance declaration
            "instance" => {
                return self.extract_instance(source, node, ctx, parent_id);
            }
            _ => {
                self.walk_all_children(source, node, ctx, parent_id)?;
            }
        }
        Ok(())
    }

    fn walk_all_children(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                self.walk_node(source, child, ctx, parent_id)?;
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Module extraction
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_module(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Module name from module_id child
        let mod_name = if let Some(mid) = find_child_by_kind(node, "module_id") {
            get_text(source, Some(mid))
        } else {
            String::new()
        };

        if mod_name.is_empty() {
            return Ok(());
        }

        let mod_id = ctx.add_node(NodeKind::Module, &mod_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &mod_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&mod_name, "module");
        ctx.push_scope_node(&mod_id);

        self.walk_all_children(source, node, ctx, &mod_id)?;

        ctx.pop_scope();
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Import extraction (v7.3.0: enhanced with REFERENCES + imported_names)
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_import(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Extract module name via field "module" (tree-sitter-haskell grammar)
        let mod_name = if let Some(mod_node) = node.child_by_field_name("module") {
            collect_module_id_parts(source, mod_node)
        } else {
            // Fallback: search for child with kind "module"
            let mn = find_child_by_kind(node, "module");
            mn.map(|n| collect_module_id_parts(source, n))
                .unwrap_or_default()
        };

        if mod_name.is_empty() {
            return Ok(());
        }

        // Core IMPORTS edge (always present, for the module-level relationship)
        let target_qn = build_qualified_target(&ctx.file_path, &mod_name);
        let target = hash_id(&ctx.file_path, &target_qn);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&mod_name));

        // Check for qualified import (at either position: before or after module)
        let is_qualified = has_unnamed_child(node, "qualified");

        // Check for `as` alias: import qualified Foo as F
        // Grammar: seq("as", FIELD("alias", module))
        let alias_name = if let Some(alias_node) = node.child_by_field_name("alias") {
            collect_module_id_parts(source, alias_node)
        } else {
            find_import_alias(source, node)
        };

        // Add module-level REFERENCES edge
        ctx.add_edge(
            parent_id,
            &target,
            EdgeKind::References,
            line,
            Some(&format!("{}::", mod_name)),
        );

        // Populate imported_names: the module basename maps to the full module
        if !is_qualified {
            let basename = module_basename(&mod_name);
            if !basename.is_empty() {
                self.imported_names
                    .insert(basename.clone(), mod_name.clone());
            }
        }

        // If there's an alias, map alias → module
        if !alias_name.is_empty() {
            self.imported_names
                .insert(alias_name.clone(), mod_name.clone());
            let alias_target = hash_id(
                &ctx.file_path,
                &format!("{}::{}", ctx.file_path, alias_name),
            );
            ctx.add_edge(
                parent_id,
                &alias_target,
                EdgeKind::References,
                line,
                Some(&format!("{}::{}", mod_name, alias_name)),
            );
        }

        // Handle import list: import Foo (bar, baz) or import Foo hiding (bar)
        // Grammar: optional("hiding") + FIELD("names", import_list)
        // import_list contains FIELD("name", import_name) children
        let is_hiding = has_unnamed_child(node, "hiding");

        if let Some(names_node) = node.child_by_field_name("names") {
            self.extract_import_names(
                source, names_node, ctx, parent_id, line, &mod_name, is_hiding,
            )?;
        }

        Ok(())
    }

    /// Extract individual import names from an import_list node.
    /// The import_list contains `import_name` children (aliased from _ie_entity).
    fn extract_import_names(
        &mut self,
        source: &[u8],
        list_node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
        mod_name: &str,
        is_hiding: bool,
    ) -> anyhow::Result<()> {
        self.walk_import_list(source, list_node, ctx, parent_id, line, mod_name, is_hiding)
    }

    fn walk_import_list(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        line: u32,
        mod_name: &str,
        is_hiding: bool,
    ) -> anyhow::Result<()> {
        for i in 0..node.named_child_count() {
            if let Some(child) = node.named_child(i) {
                let item_name = match child.kind() {
                    "import_name" => get_text(source, Some(child)),
                    "variable" => get_text(source, Some(child)),
                    "name" => get_text(source, Some(child)),
                    "type" => get_text(source, Some(child)),
                    "operator" => get_text(source, Some(child)),
                    "import" => {
                        // Nested import node (could wrap variable/name)
                        if let Some(v) = find_child_by_kind(child, "variable") {
                            get_text(source, Some(v))
                        } else if let Some(n) = find_child_by_kind(child, "name") {
                            get_text(source, Some(n))
                        } else {
                            get_text(source, Some(child))
                        }
                    }
                    _ => {
                        // Recurse into nested structures (e.g. import_list -> import)
                        if child.kind() == "import_list" || child.kind() == "names" {
                            self.walk_import_list(
                                source, child, ctx, parent_id, line, mod_name, is_hiding,
                            )?;
                            continue;
                        }
                        continue;
                    }
                };

                if !item_name.is_empty() && !item_name.contains("..") {
                    if !is_hiding {
                        // Selective import: add REFERENCES edge for this symbol
                        let ref_target = hash_id(
                            &ctx.file_path,
                            &format!("{}::{}", mod_name, item_name),
                        );
                        ctx.add_edge(
                            parent_id,
                            &ref_target,
                            EdgeKind::References,
                            line,
                            Some(&format!("{}::{}", mod_name, item_name)),
                        );
                        // Populate imported_names map for call qualification
                        self.imported_names.insert(
                            item_name.clone(),
                            format!("{}::{}", mod_name, item_name),
                        );
                    }
                    // For hiding, we still track the module but don't map individual names
                }
            }
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Type signature extraction
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_signature(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Signature name from variable child (or operator from prefix_id)
        let name = if let Some(var_node) = find_child_by_kind(node, "variable") {
            get_text(source, Some(var_node))
        } else if let Some(pref) = find_child_by_kind(node, "prefix_id") {
            get_text(source, Some(pref))
        } else {
            String::new()
        };

        if name.is_empty() {
            return Ok(());
        }

        let sig_id = ctx.add_node(NodeKind::Signature, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &sig_id, EdgeKind::Contains, line, None);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Function extraction
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_function(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let name_node = find_child_by_kind(node, "variable");
        let name = get_text(source, name_node);

        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&fn_id);

        self.walk_all_children(source, node, ctx, &fn_id)?;

        ctx.pop_scope();
        Ok(())
    }

    fn extract_bind(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let name_node = find_child_by_kind(node, "variable");
        let name = get_text(source, name_node);

        if name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        // Check if this name was already extracted (e.g., from a function with patterns)
        if ctx.result.nodes.iter().any(|n| n.kind == "function" && n.name == name) {
            self.walk_all_children(source, node, ctx, parent_id)?;
            return Ok(());
        }

        let fn_id = ctx.add_node(NodeKind::Function, &name, &node, HashMap::new());
        ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&name, "function");
        ctx.push_scope_node(&fn_id);

        self.walk_all_children(source, node, ctx, &fn_id)?;

        ctx.pop_scope();
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Type definition extraction (data/newtype)
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_type_def(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
        def_kind: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let type_name =
            find_first_child_text_by_kind(source, node, "name").unwrap_or_default();

        if type_name.is_empty() {
            return Ok(());
        }

        let mut extra = HashMap::new();
        extra.insert("type_kind".to_string(), def_kind.to_string());

        let type_id = ctx.add_node(NodeKind::TypeDef, &type_name, &node, extra);
        ctx.add_edge(parent_id, &type_id, EdgeKind::Contains, line, None);

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Class extraction
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_class(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let class_name = if let Some(name_node) = find_child_by_kind(node, "name") {
            get_text(source, Some(name_node))
        } else {
            String::new()
        };

        if class_name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let class_id = ctx.add_node(NodeKind::Class, &class_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &class_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&class_name, "class");
        ctx.push_scope_node(&class_id);

        self.walk_all_children(source, node, ctx, &class_id)?;

        ctx.pop_scope();
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Instance extraction
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_instance(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        let class_name =
            find_first_child_text_by_kind(source, node, "name").unwrap_or_default();

        let type_name = if let Some(tp) = find_child_by_kind(node, "type_patterns") {
            find_first_child_text_by_kind(source, tp, "name").unwrap_or_default()
        } else {
            String::new()
        };

        let instance_name = if type_name.is_empty() {
            class_name.clone()
        } else {
            format!("{} {}", class_name, type_name)
        };

        if instance_name.is_empty() {
            return self.walk_all_children(source, node, ctx, parent_id);
        }

        let inst_id = ctx.add_node(NodeKind::Instance, &instance_name, &node, HashMap::new());
        ctx.add_edge(parent_id, &inst_id, EdgeKind::Contains, line, None);

        ctx.push_scope_with_kind(&instance_name, "instance");
        ctx.push_scope_node(&inst_id);

        self.walk_all_children(source, node, ctx, &inst_id)?;

        ctx.pop_scope();
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Call extraction (apply and infix nodes) — v7.3.0: enhanced with
// imported_names qualification
// ---------------------------------------------------------------------------

impl HaskellWalker {
    fn extract_call(
        &mut self,
        source: &[u8],
        node: Node,
        ctx: &mut ExtractionContext,
        parent_id: &str,
    ) -> anyhow::Result<()> {
        let line = node.start_position().row as u32 + 1;

        // Get the callee name from the first named child
        if node.named_child_count() > 0 {
            if let Some(first_child) = node.named_child(0) {
                let callee = match first_child.kind() {
                    "variable" => Some(get_text(source, Some(first_child))),
                    "name" => Some(get_text(source, Some(first_child))),
                    "apply" | "infix_id" => {
                        let v = find_deep_variable_text(source, first_child);
                        if v.is_empty() {
                            None
                        } else {
                            Some(v)
                        }
                    }
                    _ => None,
                };

                if let Some(callee_name) = callee {
                    if !callee_name.is_empty() {
                        // v7.3.0: qualify call target using imported_names
                        let qualified_name = self.qualify_call_target(&callee_name);
                        let target_qn =
                            build_qualified_target(&ctx.file_path, &qualified_name);
                        let target = hash_id(&ctx.file_path, &target_qn);
                        ctx.add_edge(
                            parent_id,
                            &target,
                            EdgeKind::Calls,
                            line,
                            Some(&qualified_name),
                        );
                    }
                }
            }
        }

        // Recurse into children for nested calls
        self.walk_all_children(source, node, ctx, parent_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn find_deep_variable_text(source: &[u8], node: Node) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "variable" {
                return get_text(source, Some(child));
            }
            let result = find_deep_variable_text(source, child);
            if !result.is_empty() {
                return result;
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

/// Check if a node has an unnamed child with the given kind.
fn has_unnamed_child(node: Node, kind: &str) -> bool {
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() && child.kind() == kind {
                return true;
            }
        }
    }
    false
}

/// Extract the alias name from an import's `as` clause.
/// `import qualified Foo as F` → returns "F"
fn find_import_alias(source: &[u8], node: Node) -> String {
    // The `as` keyword is an unnamed child; the module id after it is a named child.
    // We look for a module_id that comes after an `as` unnamed child.
    let mut saw_as = false;
    for i in 0..node.child_count() {
        if let Some(child) = node.child(i) {
            if !child.is_named() && child.kind() == "as" {
                saw_as = true;
                continue;
            }
            if saw_as && child.is_named() {
                if child.kind() == "module_id" || child.kind() == "module" {
                    return get_text(source, Some(child));
                }
                if child.kind() == "modid" || child.kind() == "name" {
                    return get_text(source, Some(child));
                }
                // Some grammars nest the alias module id differently
                if let Some(mid) = find_child_by_kind(child, "module_id") {
                    return get_text(source, Some(mid));
                }
            }
        }
    }
    String::new()
}

/// Concatenate all module_id children (e.g., "Data" + "List" => "Data.List").
fn collect_module_id_parts(source: &[u8], node: Node) -> String {
    let mut parts = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "module_id" {
                parts.push(get_text(source, Some(child)));
            }
        }
    }
    parts.join(".")
}

fn find_first_child_text_by_kind(source: &[u8], node: Node, kind: &str) -> Option<String> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(get_text(source, Some(child)));
            }
        }
    }
    None
}

/// Extract the basename (last segment) of a dotted module name.
/// "Data.List" → "List", "Prelude" → "Prelude"
fn module_basename(module_name: &str) -> String {
    module_name
        .split('.')
        .last()
        .unwrap_or(module_name)
        .to_string()
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
        let language: tree_sitter::Language = tree_sitter_haskell::LANGUAGE.into();
        parser
            .set_language(&language)
            .expect("set haskell language");
        let tree = parser.parse(source, None).expect("parse haskell source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "haskell".to_string());
        HaskellExtractor
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
    // Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.hs");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Module extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_module_declaration() {
        let ctx = extract(
            "module Main where\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert!(!modules.is_empty(), "Expected module node");
        assert_eq!(modules[0].name, "Main");
    }

    // ------------------------------------------------------------------
    // Import extraction (basic)
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract(
            "module Main where\nimport Data.List\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge");
        assert!(
            imports
                .iter()
                .any(|e| e.target_text.as_deref() == Some("Data.List"))
        );
    }

    #[test]
    fn test_qualified_import() {
        let ctx = extract(
            "module Main where\nimport qualified Data.Map as Map\nmain = return ()\n",
            "src/test.hs",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected IMPORTS edge for qualified import");
        assert!(
            imports
                .iter()
                .any(|e| e.target_text.as_deref() == Some("Data.Map")),
            "Expected Data.Map in imports"
        );
    }

    // ------------------------------------------------------------------
    // v7.3.0: REFERENCES edges for imports
    // ------------------------------------------------------------------

    #[test]
    fn test_import_references_edge() {
        let ctx = extract(
            "module Main where\nimport Data.List\nmain = sort [1,2,3]\n",
            "src/test.hs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(
            !refs.is_empty(),
            "Expected REFERENCES edges for imports"
        );
        // Should have a module-level REFERENCES edge: "Data.List::"
        assert!(
            refs.iter()
                .any(|e| e.target_text.as_deref() == Some("Data.List::")),
            "Expected module-level REFERENCES edge: Data.List::"
        );
    }

    #[test]
    fn test_selective_import_references() {
        let ctx = extract(
            "module Main where\nimport Data.List (sort, nub)\nmain = return ()\n",
            "src/test.hs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        // Should have REFERENCES edges for sort and nub
        assert!(
            refs.len() >= 3,
            "Expected at least 3 REFERENCES edges (module + 2 symbols), got {}",
            refs.len()
        );
        // Check for specific symbol references
        assert!(
            refs.iter()
                .any(|e| e.target_text.as_deref() == Some("Data.List::sort")),
            "Expected REFERENCES edge for Data.List::sort"
        );
        assert!(
            refs.iter()
                .any(|e| e.target_text.as_deref() == Some("Data.List::nub")),
            "Expected REFERENCES edge for Data.List::nub"
        );
    }

    #[test]
    fn test_hiding_import_no_references_for_hidden() {
        let ctx = extract(
            "module Main where\nimport Prelude hiding (map, filter)\nmain = return ()\n",
            "src/test.hs",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        // Module-level reference should exist, but no references for map/filter
        // since they are hidden
        assert!(
            refs.iter()
                .any(|e| e.target_text.as_deref() == Some("Prelude::")),
            "Expected module-level REFERENCES edge for Prelude"
        );
        // The hidden symbols (map, filter) should NOT have references
        assert!(
            !refs
                .iter()
                .any(|e| e.target_text.as_deref() == Some("Prelude::map")),
            "Hidden symbol 'map' should NOT have a REFERENCES edge"
        );
    }

    // ------------------------------------------------------------------
    // v7.3.0: Call qualification via imported_names
    // ------------------------------------------------------------------

    #[test]
    fn test_qualified_call_with_imported_names() {
        let ctx = extract(
            "module Main where\nimport Data.List (sort)\nmain = sort [3,1,2]\n",
            "src/test.hs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edges");
        // The call to `sort` should be qualified as Data.List::sort
        assert!(
            calls
                .iter()
                .any(|e| e.target_text.as_deref() == Some("Data.List::sort")),
            "Expected qualified call target 'Data.List::sort', got: {:?}",
            calls
                .iter()
                .map(|e| &e.target_text)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_qualified_call_with_module_import() {
        let ctx = extract(
            "module Main where\nimport Data.Map\nmain = lookup \"key\" mymap\n",
            "src/test.hs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // Call to lookup — since we imported Data.Map (unqualified),
        // the basename "Map" maps to "Data.Map", but the call is just "lookup"
        // which won't be directly in imported_names. It stays as bare "lookup".
        assert!(
            calls.iter().any(|e| {
                let tt = e.target_text.as_deref().unwrap_or("");
                tt.contains("lookup")
            }),
            "Expected a call to lookup: {:?}",
            calls.iter().map(|e| &e.target_text).collect::<Vec<_>>()
        );
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_with_patterns() {
        let ctx = extract(
            "fact :: Int -> Int\nfact 0 = 1\nfact n = n * fact (n - 1)\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(!functions.is_empty(), "Expected function node");
        assert!(functions.iter().any(|f| f.name == "fact"));
    }

    #[test]
    fn test_simple_function() {
        let ctx = extract(
            "module Main where\ngreet name = \"Hello, \" ++ name\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(!functions.is_empty(), "Expected function nodes");
    }

    // ------------------------------------------------------------------
    // Signature extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_signature() {
        let ctx = extract(
            "module Main where\ndouble :: Int -> Int\ndouble x = x * 2\n",
            "src/test.hs",
        );
        let sigs = find_nodes(&ctx, NodeKind::Signature);
        assert!(!sigs.is_empty(), "Expected signature node");
        assert_eq!(sigs[0].name, "double");
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_call() {
        let ctx = extract(
            "module Main where\nmain = putStrLn \"hello\"\n",
            "src/test.hs",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected CALLS edge");
        assert!(
            calls
                .iter()
                .any(|e| e.target_text.as_deref() == Some("putStrLn"))
        );
    }

    // ------------------------------------------------------------------
    // Type definition extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_data_declaration() {
        let ctx = extract(
            "module Main where\ndata Tree a = Leaf a | Node (Tree a) (Tree a)\n",
            "src/test.hs",
        );
        let types = find_nodes(&ctx, NodeKind::TypeDef);
        assert!(
            !types.is_empty(),
            "Expected TypeDef node for data declaration"
        );
        assert_eq!(types[0].name, "Tree");
    }

    #[test]
    fn test_newtype_declaration() {
        let ctx = extract(
            "module Main where\nnewtype Age = Age Int\n",
            "src/test.hs",
        );
        let types = find_nodes(&ctx, NodeKind::TypeDef);
        assert!(
            !types.is_empty(),
            "Expected TypeDef node for newtype"
        );
    }

    // ------------------------------------------------------------------
    // Class extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_class_declaration() {
        let ctx = extract(
            "module Main where\nclass Eq a where\n  (==) :: a -> a -> Bool\n",
            "src/test.hs",
        );
        let classes = find_nodes(&ctx, NodeKind::Class);
        assert!(!classes.is_empty(), "Expected class node");
    }

    // ------------------------------------------------------------------
    // Instance extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_instance_declaration() {
        let ctx = extract(
            "module Main where\ninstance Eq Int where\n  x == y = True\n",
            "src/test.hs",
        );
        let instances = find_nodes(&ctx, NodeKind::Instance);
        assert!(!instances.is_empty(), "Expected instance node");
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_file_contains_module() {
        let ctx = extract(
            "module Main where\nmain = return ()\n",
            "src/test.hs",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edge");
    }

    // ------------------------------------------------------------------
    // Nested / multiple
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_functions() {
        let ctx = extract(
            "module Main where\na = 1\nb = 2\nc = a + b\n",
            "src/test.hs",
        );
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert!(functions.len() >= 2, "Expected at least 2 function nodes");
    }

    #[test]
    fn test_no_explicit_module() {
        let ctx = extract("main = putStrLn \"hello\"\n", "src/test.hs");
        // Should not crash on files without explicit module declaration
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }
}
