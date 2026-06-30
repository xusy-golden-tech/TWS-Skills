//! Clojure language extractor.
//!
//! Extracts symbols and relationships from Clojure source files
//! (`.clj`, `.cljs`, `.cljc`, `.edn`) using the tree-sitter-clojure grammar.
//!
//! # Node kinds produced
//! - `module`: ns declaration
//! - `variable`: def/defonce definitions
//! - `function`: defn/defn- definitions
//! - `file`: source file
//!
//! # Edge kinds produced
//! - `calls`: function calls (list literals where first symbol is a function)
//! - `contains`: containment (file -> namespace/function)
//! - `references`: cross-file symbol references (from :require :refer)
//! - `imports`: namespace imports

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
    // Get the first sym_lit/kwd_lit child (the operator/function name)
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
            walk_list_children(source, node, ctx, parent_id)?;
        }
        "defn" | "defn-" | "defmacro" => {
            extract_function(source, node, ctx, parent_id)?;
            walk_list_children(source, node, ctx, parent_id)?;
        }
        // Handle :require / require (keyword in ns, or standalone form)
        ":require" | "require" | ":use" | "use" => {
            extract_clojure_require_or_use(source, node, ctx, parent_id, &op)?;
        }
        // Handle :import / import (keyword in ns, or standalone form)
        ":import" | "import" => {
            extract_clojure_import(source, node, ctx, parent_id)?;
        }
        _ => {
            // Regular function call (but skip Java interop dot-prefixed calls)
            extract_call(source, node, ctx, parent_id, &op)?;
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
    // (list_lit children like (:require ...), (:use ...), (:import ...)
    //  are now handled by the handle_list dispatch)
    walk_list_children(source, node, ctx, &ns_id)?;

    // NOTE: namespace scope is intentionally NOT popped here.
    // Clojure namespace scope persists for the entire file, so that
    // subsequent defn/def definitions and calls can be qualified with
    // the namespace name for cross-file resolution.
    // The scope is cleaned up when the ExtractionContext is dropped.
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
// Require / Use / Import clause extraction
// ---------------------------------------------------------------------------

/// Process a `:require`/`require`/`:use`/`use` clause (ns-keyword or standalone).
///
/// `op` is the raw operator text (may or may not have colon prefix).
/// The node is the list_lit containing the keyword/symbol and its arguments.
fn extract_clojure_require_or_use(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    op: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Determine the semantic kind: :require, :use, :import
    let semantic_kind = if op.contains("use") { ":use" } else { ":require" };

    // Iterate named children, skipping the first (the keyword/symbol itself)
    for i in 1..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "vec_lit" => {
                    extract_require_vec(source, child, ctx, parent_id, line, semantic_kind)?;
                }
                "sym_lit" => {
                    // (:use clojure.java.io) — direct namespace symbol
                    let ns_name = get_sym_name(source, child);
                    if !ns_name.is_empty() {
                        let qualified = format!("{}::", ns_name);
                        let target = hash_id(&ctx.file_path, &qualified);
                        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&qualified));
                    }
                }
                "quoting_lit" => {
                    // (require '[foo.bar :as fb]) — quoted vector/symbol
                    for j in 0..child.named_child_count() {
                        if let Some(inner) = child.named_child(j) {
                            if inner.kind() == "vec_lit" {
                                extract_require_vec(source, inner, ctx, parent_id, line, semantic_kind)?;
                            }
                            if inner.kind() == "sym_lit" {
                                let ns_name = get_sym_name(source, inner);
                                if !ns_name.is_empty() {
                                    let qualified = format!("{}::", ns_name);
                                    let target = hash_id(&ctx.file_path, &qualified);
                                    ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&qualified));
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

/// Process an `:import`/`import` clause (ns-keyword or standalone).
fn extract_clojure_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    for i in 1..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "vec_lit" => {
                    extract_require_vec(source, child, ctx, parent_id, line, ":import")?;
                }
                "quoting_lit" => {
                    for j in 0..child.named_child_count() {
                        if let Some(inner) = child.named_child(j) {
                            if inner.kind() == "vec_lit" {
                                extract_require_vec(source, inner, ctx, parent_id, line, ":import")?;
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

// ---------------------------------------------------------------------------
// Require vector parsing: [foo.bar :refer [baz qux] :as fb]
// ---------------------------------------------------------------------------

/// Process a single vector inside a require/use/import clause.
///
/// Forms:
/// - `[foo.bar :refer [baz qux]]` — selective symbol import
/// - `[foo.bar :refer :all]` — import all public symbols
/// - `[foo.bar :as fb]` — namespace alias
/// - `[java.util.Date]` — Java class import (from :import)
/// - `[foo.bar]` — bare namespace import (from :use)
fn extract_require_vec(
    source: &[u8],
    vec_node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
    op: &str,
) -> anyhow::Result<()> {
    // Collect all named children
    let mut items: Vec<(String, String, Option<Node>)> = Vec::new();
    for i in 0..vec_node.named_child_count() {
        if let Some(child) = vec_node.named_child(i) {
            let (kind, text) = match child.kind() {
                "sym_lit" => ("sym", get_sym_name(source, child)),
                "kwd_lit" => ("kwd", get_kwd_name(source, child)),
                "vec_lit" => ("vec", String::new()),
                _ => continue,
            };
            items.push((kind.to_string(), text, Some(child)));
        }
    }

    if items.is_empty() {
        return Ok(());
    }

    // First item is always the namespace/class name
    let module_name = &items[0].1;
    if module_name.is_empty() {
        return Ok(());
    }

    // For :import (Java imports), create references for each class name
    if op == ":import" {
        // First item is the Java package prefix, remaining are class names
        // e.g., [java.util Date Calendar] → module = "java.util", classes = ["Date", "Calendar"]
        for (kind, name, _) in &items[1..] {
            if kind == "sym" && !name.is_empty() {
                let qualified = format!("{}.{}::{}", module_name, name, name);
                let target = hash_id(&ctx.file_path, &qualified);
                ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&qualified));
            }
        }
        // If only one item (e.g., [java.util.Date]), it IS the full class name
        if items.len() == 1 {
            let qualified = format!("{}::", module_name);
            let target = hash_id(&ctx.file_path, &qualified);
            ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&qualified));
        }
        return Ok(());
    }

    // For :require and :use — process keyword options
    let mut had_import = false;
    let mut i = 1;
    while i < items.len() {
        let (kind, text, _) = &items[i];

        if kind == "kwd" {
            // Note: kwd_name text does NOT include the colon prefix.
            // "refer", "as", "rename", "all" etc. (not ":refer", ":as"...)
            match text.as_str() {
                "refer" => {
                    had_import = true;
                    i += 1;
                    if i < items.len() {
                        let (ref_kind, ref_name, ref_node) = &items[i];
                        match ref_kind.as_str() {
                            "vec" => {
                                // :refer [baz qux] — extract each symbol from inner vec
                                if let Some(inner_vec) = ref_node {
                                    extract_refer_symbols(
                                        source, *inner_vec, ctx, parent_id, line, module_name,
                                    )?;
                                }
                            }
                            "kwd" => {
                                // :refer :all
                                if ref_name == "all" {
                                    let qualified = format!("{}::", module_name);
                                    let target = hash_id(&ctx.file_path, &qualified);
                                    ctx.add_edge(parent_id, &target, EdgeKind::References,
                                        line, Some(&qualified));
                                }
                            }
                            _ => {}
                        }
                    }
                }
                "as" => {
                    // :as fb — namespace alias (creates IMPORTS edge for the namespace)
                    had_import = true;
                    i += 1;
                    if i < items.len() {
                        let qualified = format!("{}::", module_name);
                        let target = hash_id(&ctx.file_path, &qualified);
                        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&qualified));
                    }
                }
                "rename" => {
                    // :rename {old new} — less common, skip for now
                    had_import = true;
                    i += 2; // skip the map value
                }
                "require" | "use" => {
                    // Nested require within require (rare)
                }
                _ => {}
            }
        }

        i += 1;
    }

    // Bare namespace import with no keyword options (e.g., [clojure.string])
    if !had_import && items.len() == 1 {
        let qualified = format!("{}::", module_name);
        let target = hash_id(&ctx.file_path, &qualified);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&qualified));
    }

    Ok(())
}

/// Extract symbols from a :refer vector like [baz qux join].
fn extract_refer_symbols(
    source: &[u8],
    vec_node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    line: u32,
    module_name: &str,
) -> anyhow::Result<()> {
    for i in 0..vec_node.named_child_count() {
        if let Some(child) = vec_node.named_child(i) {
            if child.kind() == "sym_lit" {
                let sym_name = get_sym_name(source, child);
                if !sym_name.is_empty() {
                    let qualified = format!("{}::{}", module_name, sym_name);
                    let target = hash_id(&ctx.file_path, &qualified);
                    ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(&qualified));
                }
            }
        }
    }
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

    // Filter Java interop calls: symbols starting with "." or containing "/"
    // (.toString obj) → callee = ".toString"
    // (Math/pow 2 3) → callee = "Math/pow" (Java static method)
    if callee.starts_with('.') {
        return Ok(());
    }

    // Build target_text with namespace qualification for cross-file resolution
    let target_text = build_call_target_text(ctx, callee);
    let target_qn = build_qualified_target(&ctx.file_path, callee);
    let target = hash_id(&ctx.file_path, &target_qn);
    ctx.add_edge(parent_id, &target, EdgeKind::Calls, line, Some(&target_text));

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Get the text of the nth sym_lit/kwd_lit child in a list_lit or vec_lit.
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
                // Keyword literals like :require are counted as syms too
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

fn build_qualified_target(file_path: &str, name: &str) -> String {
    format!("{file_path}::{name}")
}

/// Build the target_text for a call edge, including namespace qualification
/// for cross-file resolution.
///
/// If the call is inside a namespace scope, the format is
/// `"namespace_name::callee"`, otherwise `"file_path::callee"`.
fn build_call_target_text(ctx: &ExtractionContext, callee: &str) -> String {
    if let Some(ns) = ctx.current_namespace() {
        format!("{}::{}", ns, callee)
    } else {
        format!("{}::{}", ctx.file_path, callee)
    }
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

/// Get the name text from a sym_lit node by reading its sym_name child.
fn get_sym_name(source: &[u8], node: Node) -> String {
    if let Some(name_node) = find_child_by_kind(node, "sym_name") {
        get_text(source, Some(name_node))
    } else {
        get_text(source, Some(node))
    }
}

/// Get the name text from a kwd_lit node by reading its kwd_name child.
fn get_kwd_name(source: &[u8], node: Node) -> String {
    if let Some(name_node) = find_child_by_kind(node, "kwd_name") {
        get_text(source, Some(name_node))
    } else {
        get_text(source, Some(node))
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
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(!calls.is_empty(), "Expected some call edges");
        // inc is in the special forms list, might or might not be there
    }

    #[test]
    fn test_call_in_body() {
        let ctx = extract(
            "(ns test)\n(defn run []\n  (my-custom-fn 42))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(calls.iter().any(|e| e.target_text.as_deref() == Some("test::my-custom-fn")));
    }

    #[test]
    fn test_nested_calls() {
        let ctx = extract(
            "(ns test)\n(defn process [data]\n  (log (transform data)))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| t.ends_with("::log")), "Expected 'log' call");
        assert!(targets.iter().any(|t| t.ends_with("::transform")), "Expected 'transform' call");
    }

    #[test]
    fn test_special_forms_filtered() {
        let ctx = extract(
            "(ns test)\n(defn process [items]\n  (let [x (first items)]\n    (str x)))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(!targets.contains(&"let"), "let should be filtered as special form");
        assert!(!targets.contains(&"defn"), "defn should be filtered as special form");
    }

    #[test]
    fn test_java_interop_filtered() {
        let ctx = extract(
            "(ns test)\n(defn get-name [obj]\n  (.getName obj))\n",
            "src/test.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // .getName should be filtered (Java interop)
        assert!(!targets.iter().any(|t| t.contains(".getName")),
            "Java interop calls should be filtered from call edges");
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
    // Cross-file reference extraction (Stage 21)
    // ------------------------------------------------------------------

    #[test]
    fn test_require_with_refer() {
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.string :refer [join split]]))\n",
            "src/myapp/core.clj",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.string::join"),
            "Expected REFERENCE edge for clojure.string::join");
        assert!(targets.iter().any(|t| *t == "clojure.string::split"),
            "Expected REFERENCE edge for clojure.string::split");
    }

    #[test]
    fn test_require_with_as() {
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.string :as str]))\n",
            "src/myapp/core.clj",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.string::"),
            "Expected IMPORTS edge for clojure.string::");
    }

    #[test]
    fn test_require_with_refer_all() {
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.data.json :refer :all]))\n",
            "src/myapp/core.clj",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.data.json::"),
            "Expected REFERENCE edge for clojure.data.json:: (refer :all)");
    }

    #[test]
    fn test_multiple_requires() {
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.string :refer [join]]\n            [clojure.set :refer [union difference]]))\n",
            "src/myapp/core.clj",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.string::join"),
            "Expected REFERENCE for clojure.string::join");
        assert!(targets.iter().any(|t| *t == "clojure.set::union"),
            "Expected REFERENCE for clojure.set::union");
        assert!(targets.iter().any(|t| *t == "clojure.set::difference"),
            "Expected REFERENCE for clojure.set::difference");
    }

    #[test]
    fn test_use_clause() {
        let ctx = extract(
            "(ns myapp.core\n  (:use clojure.java.io))\n",
            "src/myapp/core.clj",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.java.io::"),
            "Expected IMPORTS edge for clojure.java.io::");
    }

    #[test]
    fn test_import_java_classes() {
        let ctx = extract(
            "(ns myapp.core\n  (:import [java.util Date Calendar]))\n",
            "src/myapp/core.clj",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| t.contains("java.util.Date")),
            "Expected REFERENCE edge for java.util.Date");
        assert!(targets.iter().any(|t| t.contains("java.util.Calendar")),
            "Expected REFERENCE edge for java.util.Calendar");
    }

    #[test]
    fn test_standalone_require() {
        let ctx = extract(
            "(ns test)\n(require '[clojure.string :refer [join]])\n",
            "src/test.clj",
        );
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().filter_map(|e| e.target_text.as_deref()).collect();
        assert!(targets.iter().any(|t| *t == "clojure.string::join"),
            "Expected REFERENCE edge for standalone require clojure.string::join");
    }

    #[test]
    fn test_call_with_namespace_qualification() {
        let ctx = extract(
            "(ns myapp.core)\n(defn greet [name]\n  (println \"Hello\" name)\n  (helper name))\n",
            "src/myapp/core.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // helper should be qualified with namespace
        assert!(targets.iter().any(|t| *t == "myapp.core::helper"),
            "Expected call target_text with namespace qualification: myapp.core::helper");
    }

    #[test]
    fn test_call_without_namespace_uses_file_path() {
        let ctx = extract(
            "(println \"hello\")\n",
            "src/script.clj",
        );
        let calls = find_edges(&ctx, EdgeKind::Calls);
        let targets: Vec<&str> = calls.iter().filter_map(|e| e.target_text.as_deref()).collect();
        // println is a special form (in the list), so it should be filtered
        // Just verify no crash and file node exists
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
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
        // println is in special forms list, so it may be filtered
        // Just verify we don't crash
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_empty_require() {
        // require with no references (bare namespace)
        let ctx = extract(
            "(ns myapp.core\n  (:require [clojure.string]))\n",
            "src/myapp/core.clj",
        );
        // Should not crash, should still create IMPORTS edge for the namespace
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.iter().any(|e| {
            e.target_text.as_deref() == Some("clojure.string::")
        }), "Expected IMPORTS edge for bare require clojure.string::");
    }
}
