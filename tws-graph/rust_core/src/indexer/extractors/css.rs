//! CSS language extractor.
//!
//! Extracts symbols and relationships from CSS source files (`.css`)
//! using the tree-sitter-css grammar.
//!
//! # Node kinds produced
//! - `css_rule`: CSS rule set selectors
//! - `css_import`: `@import` statements
//! - `css_keyframes`: `@keyframes` blocks
//! - `css_media`: `@media` blocks
//!
//! # Edge kinds produced
//! - `references`: references between rules
//! - `imports`: `@import url`
//! - `contains`: containment (file -> rule)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct CssExtractor;

impl Extractor for CssExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["css"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["css"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "css", &root, HashMap::new());

        walk_stylesheet(source, root, ctx, &file_id)?;

        Ok(())
    }
}

/// Find a direct named child by kind.
fn find_child<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}

fn walk_stylesheet(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "rule_set" => {
                    extract_rule_set(source, child, ctx, parent_id)?;
                }
                "import_statement" => {
                    extract_import(source, child, ctx, parent_id)?;
                }
                "keyframes_statement" => {
                    extract_keyframes(source, child, ctx, parent_id)?;
                }
                "media_statement" => {
                    extract_media(source, child, ctx, parent_id)?;
                }
                _ => {
                    walk_stylesheet(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_rule_set(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let mut selectors = Vec::new();

    // selectors is a named child of rule_set (not a field)
    if let Some(selectors_node) = find_child(node, "selectors") {
        for i in 0..selectors_node.named_child_count() {
            if let Some(sel) = selectors_node.named_child(i) {
                let text = get_text(source, Some(sel));
                if !text.is_empty() {
                    selectors.push(text);
                }
            }
        }
    }

    let name = if selectors.is_empty() {
        "rule".to_string()
    } else {
        selectors.join(", ")
    };

    let line = node.start_position().row as u32 + 1;
    let rule_id = ctx.add_node(NodeKind::CssRule, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &rule_id, EdgeKind::Contains, line, None);

    // Extract references from selectors
    for sel in &selectors {
        let ref_name = sel.trim();
        if !ref_name.is_empty() && ref_name != "*" {
            let target = hash_id(&ctx.file_path, ref_name);
            ctx.add_edge(&rule_id, &target, EdgeKind::References, line, Some(ref_name));
        }
    }

    Ok(())
}

fn extract_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let import_text = get_text(source, Some(node));
    let name = if import_text.len() > 60 {
        format!("{}...", import_text.chars().take(60).collect::<String>())
    } else {
        import_text.clone()
    };

    let line = node.start_position().row as u32 + 1;
    let import_id = ctx.add_node(NodeKind::CssImport, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &import_id, EdgeKind::Contains, line, None);

    // Try to extract URL from @import
    if let Some(url) = extract_url_from_import(&import_text) {
        let target = hash_id(&ctx.file_path, &url);
        ctx.add_edge(&import_id, &target, EdgeKind::Imports, line, Some(&url));
    }

    Ok(())
}

fn extract_keyframes(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // keyframes_name is a named child, not a field
    let name = find_child(node, "keyframes_name")
        .map(|n| get_text(source, Some(n)))
        .unwrap_or_else(|| "keyframes".to_string());

    let line = node.start_position().row as u32 + 1;
    let kf_id = ctx.add_node(NodeKind::CssKeyframes, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &kf_id, EdgeKind::Contains, line, None);

    Ok(())
}

fn extract_media(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Build query text from the query children (everything between @media and {)
    let mut query_parts = Vec::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let kind = child.kind();
            if kind != "block" {
                query_parts.push(get_text(source, Some(child)));
            }
        }
    }
    let query = if query_parts.is_empty() {
        let full = get_text(source, Some(node));
        if full.len() > 60 {
            format!("{}...", full.chars().take(60).collect::<String>())
        } else {
            full
        }
    } else {
        query_parts.join(" ")
    };

    let line = node.start_position().row as u32 + 1;
    let media_id = ctx.add_node(NodeKind::CssMedia, &query, &node, HashMap::new());
    ctx.add_edge(parent_id, &media_id, EdgeKind::Contains, line, None);

    // Recurse into media block for nested rules
    if let Some(body) = find_child(node, "block") {
        walk_stylesheet(source, body, ctx, &media_id)?;
    }

    Ok(())
}

fn extract_url_from_import(text: &str) -> Option<String> {
    if let Some(start) = text.find("url(") {
        let rest = &text[start + 4..];
        let end = rest.find(')')?;
        let url = rest[..end].trim_matches('"').trim_matches('\'').to_string();
        return Some(url);
    }
    if let Some(start) = text.find('"') {
        let rest = &text[start + 1..];
        if let Some(end) = rest.find('"') {
            return Some(rest[..end].to_string());
        }
    }
    if let Some(start) = text.find('\'') {
        let rest = &text[start + 1..];
        if let Some(end) = rest.find('\'') {
            return Some(rest[..end].to_string());
        }
    }
    None
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
            .set_language(&tree_sitter_css::LANGUAGE.into())
            .expect("set css language");
        let tree = parser.parse(source, None).expect("parse css source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "css".to_string());
        CssExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes<'a>(ctx: &'a ExtractionContext, kind: NodeKind) -> Vec<&'a crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result.nodes.iter().filter(|n| n.kind == kind_str).collect()
    }

    fn find_edges<'a>(ctx: &'a ExtractionContext, kind: EdgeKind) -> Vec<&'a crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result.edges.iter().filter(|e| e.kind == kind_str).collect()
    }

    #[test]
    fn test_extract_simple_rule() {
        let ctx = extract("h1 { color: red; }", "src/test.css");
        let rules = find_nodes(&ctx, NodeKind::CssRule);
        assert_eq!(rules.len(), 1, "Expected 1 rule, got {} nodes", ctx.result.nodes.len());
        assert_eq!(rules[0].name, "h1");
    }

    #[test]
    fn test_extract_multiple_selectors() {
        let ctx = extract("h1, h2, h3 { margin: 0; }", "src/test.css");
        let rules = find_nodes(&ctx, NodeKind::CssRule);
        assert_eq!(rules.len(), 1);
        assert!(rules[0].name.contains("h1"));
        assert!(rules[0].name.contains("h2"));
        assert!(rules[0].name.contains("h3"));
    }

    #[test]
    fn test_extract_import() {
        let ctx = extract("@import url(\"styles.css\");", "src/test.css");
        let imports = find_nodes(&ctx, NodeKind::CssImport);
        assert_eq!(imports.len(), 1, "Expected 1 css_import node");

        let edges = find_edges(&ctx, EdgeKind::Imports);
        assert!(!edges.is_empty(), "Expected imports edge for @import");
    }

    #[test]
    fn test_extract_keyframes() {
        let ctx = extract("@keyframes slide { from { left: 0; } to { left: 100px; } }", "src/test.css");
        let kf = find_nodes(&ctx, NodeKind::CssKeyframes);
        assert_eq!(kf.len(), 1, "Expected 1 keyframes node, got {}", kf.len());
        assert_eq!(kf[0].name, "slide");
    }

    #[test]
    fn test_extract_media_query() {
        let ctx = extract("@media screen and (max-width: 600px) { body { font-size: 14px; } }", "src/test.css");
        let media = find_nodes(&ctx, NodeKind::CssMedia);
        assert_eq!(media.len(), 1, "Expected 1 media node, got {}", media.len());
        assert!(media[0].name.contains("max-width") || media[0].name.contains("screen"),
            "Expected media query to contain screen or max-width, got '{}'", media[0].name);
    }

    #[test]
    fn test_extract_multiple_rules() {
        let ctx = extract("h1 { color: red; }\np { color: blue; }\n.foo { color: green; }", "src/test.css");
        let rules = find_nodes(&ctx, NodeKind::CssRule);
        assert_eq!(rules.len(), 3);
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.css");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_class_selector() {
        let ctx = extract(".container { width: 100%; }", "src/test.css");
        let rules = find_nodes(&ctx, NodeKind::CssRule);
        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].name, ".container");

        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected references edge for class selector");
    }

    #[test]
    fn test_extract_id_selector() {
        let ctx = extract("#main { width: 100%; }", "src/test.css");
        let rules = find_nodes(&ctx, NodeKind::CssRule);
        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].name, "#main");
    }

    #[test]
    fn test_import_with_quoted_string() {
        let ctx = extract("@import \"theme.css\";", "src/test.css");
        let imports = find_nodes(&ctx, NodeKind::CssImport);
        assert_eq!(imports.len(), 1);
    }
}
