//! Clojure language extractor.
//!
//! Extracts symbols and relationships from Clojure source files
//! (`.clj`, `.cljs`, `.cljc`, `.edn`) using the tree-sitter-clojure grammar.
//!
//! # Node kinds produced
//! - `namespace`: ns declaration
//! - `var_def`: def/defonce definitions
//! - `function`: defn/defn- definitions
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function calls (list literals where first symbol is a function)
//! - `contains`: containment (file -> namespace/function)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

// ---------------------------------------------------------------------------
// Clojure core special forms and builtins — filtered from calls
// ---------------------------------------------------------------------------

const CLOJURE_SPECIAL_FORMS: &[&str] = &[
    "def", "defn", "defn-", "defonce", "defmacro", "defmulti", "defmethod",
    "defprotocol", "defrecord", "deftype", "definterface", "defstruct",
    "ns", "in-ns", "require", "use", "import", "refer", "refer-clojure",
    "fn", "fn*", "let", "let*", "loop", "loop*", "recur",
    "if", "if-not", "when", "when-not", "when-let", "when-first",
    "if-let", "cond", "condp", "case", "do", "doseq", "dotimes",
    "for", "while", "and", "or", "not", "try", "catch", "finally",
    "throw", "quote", "var", "delay", "future", "promise",
    "set!", "locking", "monitor-enter", "monitor-exit",
    "new", "this", "import", "ns-resolve",
    ".", "..", "->", "->>", "as->", "some->", "some->>",
    "doto", "memfn", "proxy", "reify", "gen-class", "gen-interface",
    "list", "vec", "vector", "hash-map", "array-map", "set", "sorted-set",
    "first", "rest", "next", "cons", "conj", "into", "assoc", "dissoc",
    "get", "get-in", "keys", "vals", "merge", "update", "update-in",
    "count", "empty", "empty?", "contains?", "seq", "map", "reduce",
    "filter", "remove", "take", "drop", "take-while", "drop-while",
    "comp", "partial", "juxt", "apply", "str", "prn", "pr", "println",
    "print", "format", "slurp", "spit", "atom", "swap!", "reset!",
    "deref", "@", "alter-var-root",
];

fn is_special_form(name: &str) -> bool {
    CLOJURE_SPECIAL_FORMS.contains(&name)
}

// ---------------------------------------------------------------------------
// ClojureExtractor
// ---------------------------------------------------------------------------

pub struct ClojureExtractor;

impl Extractor for ClojureExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["clj", "cljs", "cljc", "edn"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["clojure"]
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
                .unwrap_or("core")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking
// ---------------------------------------------------------------------------

fn walk_node(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // In Clojure, everything is a list_lit. We inspect list_lit nodes specially.
    if node.kind() == "list_lit" {
        return handle_list(source, node, ctx, parent_id);
    }

    // Recurse into children for other node types (vec_lit, etc.)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// List handling — dispatch based on first symbol
// ---------------------------------------------------------------------------

fn handle_list(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Get the first sym_lit child (the operator/function name)
    let op = get_nth_sym_text(source, node, 0);
    if op.is_empty() {
        // Walk children for nested lists
        walk_list_children(source, node, ctx, parent_id)?;
        return Ok(());
    }

    match op.as_str() {
        "ns" => {
            extract_namespace(source, node, ctx, parent_id)?;
        }
        "def" | "defonce" => {
            extract_var(source, node, ctx, parent_id)?;
            // Also walk children for nested calls
            walk_list_children(source, node, ctx, parent_id)?;
        }
        "defn" | "defn-" | "defmacro" => {
            extract_function(source, node, ctx, parent_id)?;
            // Walk children including the body for calls
            walk_list_children(source, node, ctx, parent_id)?;
        }
        _ => {
            // Regular function call
            extract_call(source, node, ctx, parent_id, &op)?;
            // Walk children for nested calls
            walk_list_children(source, node, ctx, parent_id)?;
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Namespace extraction: (ns myapp.core ...)
// ---------------------------------------------------------------------------

fn extract_namespace(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Namespace name is the second sym_lit (after "ns")
    let ns_name = get_nth_sym_text(source, node, 1);
    if ns_name.is_empty() {
        return Ok(());
    }

    let ns_id = ctx.add_node(NodeKind::Module, &ns_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &ns_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&ns_name, "namespace");
    ctx.push_scope_node(&ns_id);

    // Walk the rest of the list for require/import clauses
    walk_list_children(source, node, ctx, &ns_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Var extraction: (def name value) / (defonce name value)
// ---------------------------------------------------------------------------

fn extract_var(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Var name is the second sym_lit
    let var_name = get_nth_sym_text(source, node, 1);
    if var_name.is_empty() {
        return Ok(());
    }

    let var_id = ctx.add_node(NodeKind::Variable, &var_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &var_id, EdgeKind::Contains, line, None);

    Ok(())
}

// ---------------------------------------------------------------------------
// Function extraction: (defn name [args] body...)
// ---------------------------------------------------------------------------

fn extract_function(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Function name is the second sym_lit
    let fn_name = get_nth_sym_text(source, node, 1);
    if fn_name.is_empty() {
        return Ok(());
    }

    let fn_id = ctx.add_node(NodeKind::Function, &fn_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &fn_id, EdgeKind::Contains, line, None);

    ctx.push_scope_with_kind(&fn_name, "function");
    ctx.push_scope_node(&fn_id);

    walk_list_children(source, node, ctx, &fn_id)?;

    ctx.pop_scope();
    Ok(())
}

// ---------------------------------------------------------------------------
// Call extraction
// ---------------------------------------------------------------------------

fn extract_call(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    callee: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Filter special forms and builtins
    if is_special_form(callee) {
        return Ok(());
    }

    let target_qn = build_qualified_target(&ctx.file_path, callee);
    let target = hash_id(&ctx.file_path, &target_qn);
    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(callee));

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Get the text of the nth sym_lit child in a list_lit.
fn get_nth_sym_text(source: &[u8], node: Node, n: usize) -> String {
    let mut sym_count = 0;
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "sym_lit" {
                if sym_count == n {
                    // sym_lit contains a sym_name child with the actual text
                    if let Some(name_node) = find_child_by_kind(child, "sym_name") {
                        return get_text(source, Some(name_node));
                    }
                    return get_text(source, Some(child));
                }
                sym_count += 1;
            } else if child.kind() == "kwd_lit" {
                // Keyword literals like :require are counted as syms too in some contexts
                if sym_count == n {
                    if let Some(name_node) = find_child_by_kind(child, "kwd_name") {
                        return get_text(source, Some(name_node));
                    }
                    return get_text(source, Some(child));
                }
                sym_count += 1;
            }
        }
    }
    String::new()
}

/// Walk children of a list_lit, skipping the operator if we already handled it.
fn walk_list_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            // Only recurse into nested list_lit and vec_lit nodes
            match child.kind() {
                "list_lit" | "vec_lit" => {
                    walk_node(source, child, ctx, parent_id)?;
                }
                _ => {
                    // sym_lit, str_lit, num_lit, etc. are not structural
                }
            }
        }
    }
    Ok(())
}

fn walk_all_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
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
        let language: tree_sitter::Language = tree_sitter_clojure::LANGUAGE.into();
        parser.set_language(&language).expect("set clojure language");
        let tree = parser.parse(source, None).expect("parse clojure source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "clojure".to_string());
        ClojureExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges(ctx: &ExtractionContext, kind: EdgeKind) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    // ------------------------------------------------------------------
    // Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract("", "src/empty.clj");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // Namespace extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_namespace() {
        let ctx = extract("(ns myapp.core)\n", "src/test.clj");
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "myapp.core");
    }

    #[test]
    fn test_namespace_with_require() {
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.string :as str]))\n",
            "src/test.clj",
        );
        let modules = find_nodes(&ctx, NodeKind::Module);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "myapp.core");
    }

    // ------------------------------------------------------------------
    // Var extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_def_var() {
        let ctx = extract("(def x 42)\n", "src/test.clj");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "x");
    }

    #[test]
    fn test_defonce_var() {
        let ctx = extract("(defonce counter (atom 0))\n", "src/test.clj");
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "counter");
    }

    // ------------------------------------------------------------------
    // Function extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_defn_function() {
        let ctx = extract("(defn greet [name]\n  (str \"Hello, \" name))\n", "src/test.clj");
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "greet");
    }

    #[test]
    fn test_defn_macro() {
        let ctx = extract("(defmacro unless [test then]\n  (list 'if test nil then))\n", "src/test.clj");
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
        assert_eq!(functions[0].name, "unless");
    }

    #[test]
    fn test_multiple_definitions() {
        let ctx = extract(
            "(ns test)\n(def x 1)\n(defn f [y] (+ y x))\n",
            "src/test.clj",
        );
        let vars = find_nodes(&ctx, NodeKind::Variable);
        assert_eq!(vars.len(), 1);
        let functions = find_nodes(&ctx, NodeKind::Function);
        assert_eq!(functions.len(), 1);
    }

    // ------------------------------------------------------------------
    // Call extraction
    // ------------------------------------------------------------------

    #[test]
    fn test_function_call() {
        let ctx = extract(
            "(ns test)\n(defn f [x]\n  (inc x))\n",
            "src/test.clj",
        );
        // inc is not a special form, should be a call
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // inc might not be in special forms list
        assert!(!calls.is_empty(), "Expected some call edges");
    }

    #[test]
    fn test_call_in_body() {
        let ctx = extract(
            "(ns test)\n(defn run []\n  (my-custom-fn 42))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("my-custom-fn")));
    }

    #[test]
    fn test_nested_calls() {
        let ctx = extract(
            "(ns test)\n(defn process [data]\n  (log (transform data)))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // log and transform should be calls
        assert!(targets.iter().any(|t| *t == "log"), "Expected 'log' call");
        assert!(targets.iter().any(|t| *t == "transform"), "Expected 'transform' call");
    }

    #[test]
    fn test_special_forms_filtered() {
        let ctx = extract(
            "(ns test)\n(defn process [items]\n  (let [x (first items)]\n    (str x)))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        // let should be filtered out as a special form
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(!targets.contains(&"let"), "let should be filtered as special form");
        assert!(!targets.contains(&"defn"), "defn should be filtered as special form");
    }

    // ------------------------------------------------------------------
    // Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract("(ns test)\n(defn f [] (g))\n", "src/test.clj");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // Edge cases
    // ------------------------------------------------------------------

    #[test]
    fn test_only_comment() {
        let ctx = extract(";; This is a comment\n", "src/comment.clj");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_simple_call_outside_ns() {
        let ctx = extract(
            "(println \"hello world\")\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // println is in special forms list, so it may be filtered
        // But that's OK - we should at least not crash
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }
}
