//! HTML language extractor.
//!
//! Extracts symbols and relationships from HTML source files (`.html`, `.htm`)
//! using the tree-sitter-html grammar.
//!
//! # Node kinds produced
//! - `html_element`: HTML elements (with id uses `tag#id`, without `tag#N`)
//!
//! # Edge kinds produced
//! - `contains`: parent element contains child element
//! - `imports`: `<script src>`, `<link href>`
//! - `references`: `<a href>`, `<form action>`

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct HtmlExtractor;

impl Extractor for HtmlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["html", "htm"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["html"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "html", &root, HashMap::new());

        let mut counter: usize = 0;
        walk_html(source, root, ctx, &file_id, &mut counter)?;

        Ok(())
    }
}

fn walk_html(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    counter: &mut usize,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let kind = child.kind();
            if kind == "element" || kind == "script_element" || kind == "style_element" {
                let (tag_name, attrs) = get_element_info(source, child);
                *counter += 1;
                if tag_name.is_empty() {
                    continue;
                }

                let display_name = if let Some(id) = attrs.get("id") {
                    format!("{}#{}", tag_name, id)
                } else {
                    format!("{}#{}", tag_name, counter)
                };
                let mut extra = HashMap::new();
                extra.insert("tag".to_string(), tag_name.clone());
                if let Some(id_val) = attrs.get("id") {
                    extra.insert("id".to_string(), id_val.clone());
                }

                let line = child.start_position().row as u32 + 1;
                let node_id = ctx.add_node(NodeKind::HtmlElement, &display_name, &child, extra);
                ctx.add_edge(parent_id, &node_id, EdgeKind::Contains, line, None);

                // Check for script src / link href (imports)
                if tag_name == "script" {
                    if let Some(src) = attrs.get("src") {
                        let target = hash_id(&ctx.file_path, src);
                        ctx.add_edge(&node_id, &target, EdgeKind::Imports, line, Some(src));
                    }
                }
                if tag_name == "link" {
                    if let Some(href) = attrs.get("href") {
                        let target = hash_id(&ctx.file_path, href);
                        ctx.add_edge(&node_id, &target, EdgeKind::Imports, line, Some(href));
                    }
                }

                // Check for a href / form action (references)
                if tag_name == "a" {
                    if let Some(href) = attrs.get("href") {
                        let target = hash_id(&ctx.file_path, href);
                        ctx.add_edge(&node_id, &target, EdgeKind::References, line, Some(href));
                    }
                }
                if tag_name == "form" {
                    if let Some(action) = attrs.get("action") {
                        let target = hash_id(&ctx.file_path, action);
                        ctx.add_edge(&node_id, &target, EdgeKind::References, line, Some(action));
                    }
                }

                // Recurse into children (nested elements)
                walk_html(source, child, ctx, &node_id, counter)?;
            } else {
                walk_html(source, child, ctx, parent_id, counter)?;
            }
        }
    }
    Ok(())
}

/// Find a direct child node by kind (not recursively).
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

/// Extract tag name and attributes from an element node.
fn get_element_info(source: &[u8], element: Node) -> (String, HashMap<String, String>) {
    let mut tag_name = String::new();
    let mut attrs = HashMap::new();

    // For script_element and style_element, use kind as tag name
    if element.kind() == "script_element" {
        tag_name = "script".to_string();
    } else if element.kind() == "style_element" {
        tag_name = "style".to_string();
    }

    // Find start_tag child of element
    let start_tag = find_child(element, "start_tag");
    if let Some(st) = start_tag {
        // Get tag_name from start_tag
        if let Some(tn) = find_child(st, "tag_name") {
            if tag_name.is_empty() {
                tag_name = get_text(source, Some(tn));
            }
        }

        // Get attributes from start_tag
        for i in 0..st.named_child_count() {
            if let Some(child) = st.named_child(i) {
                if child.kind() == "attribute" {
                    let mut attr_name = String::new();
                    let mut attr_value = String::new();

                    // Find attribute_name
                    if let Some(an) = find_child(child, "attribute_name") {
                        attr_name = get_text(source, Some(an));
                    }

                    // Find quoted_attribute_value → attribute_value
                    if let Some(qv) = find_child(child, "quoted_attribute_value") {
                        if let Some(av) = find_child(qv, "attribute_value") {
                            attr_value = get_text(source, Some(av));
                        }
                    }

                    // Fallback: check for bare attribute_value directly
                    if attr_value.is_empty() {
                        if let Some(av) = find_child(child, "attribute_value") {
                            attr_value = get_text(source, Some(av));
                        }
                    }

                    if !attr_name.is_empty() {
                        attrs.insert(attr_name, attr_value);
                    }
                }
            }
        }
    }

    // Check for self_closing_tag instead of start_tag
    if tag_name.is_empty() {
        let sct = find_child(element, "self_closing_tag");
        if let Some(st) = sct {
            if let Some(tn) = find_child(st, "tag_name") {
                tag_name = get_text(source, Some(tn));
            }
        }
    }

    (tag_name, attrs)
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
            .set_language(&tree_sitter_html::LANGUAGE.into())
            .expect("set html language");
        let tree = parser.parse(source, None).expect("parse html source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "html".to_string());
        HtmlExtractor
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
    fn test_extract_simple_html_element() {
        let ctx = extract("<div></div>", "src/test.html");
        let elements = find_nodes(&ctx, NodeKind::HtmlElement);
        assert_eq!(elements.len(), 1);
        assert!(elements[0].name.starts_with("div#"), "Expected name to start with 'div#', got '{}'", elements[0].name);
    }

    #[test]
    fn test_extract_element_with_id() {
        let ctx = extract("<div id=\"main\"></div>", "src/test.html");
        let elements = find_nodes(&ctx, NodeKind::HtmlElement);
        assert_eq!(elements.len(), 1);
        assert_eq!(elements[0].name, "div#main");
    }

    #[test]
    fn test_extract_nested_elements() {
        let ctx = extract("<div><span>hello</span></div>", "src/test.html");
        let elements = find_nodes(&ctx, NodeKind::HtmlElement);
        assert_eq!(elements.len(), 2);

        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(contains.len() >= 2, "Expected at least 2 contains edges, got {}", contains.len());
    }

    #[test]
    fn test_extract_script_imports() {
        let ctx = extract("<script src=\"main.js\"></script>", "src/test.html");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected imports edge for script src");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("main.js")), "Expected main.js in {:?}", targets);
    }

    #[test]
    fn test_extract_link_imports() {
        let ctx = extract("<link href=\"style.css\" rel=\"stylesheet\">", "src/test.html");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(!imports.is_empty(), "Expected imports edge for link href");
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("style.css")), "Expected style.css in {:?}", targets);
    }

    #[test]
    fn test_extract_anchor_references() {
        let ctx = extract("<a href=\"https://example.com\">link</a>", "src/test.html");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected references edge for a href");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("example.com")), "Expected example.com in {:?}", targets);
    }

    #[test]
    fn test_extract_form_references() {
        let ctx = extract("<form action=\"/submit\"></form>", "src/test.html");
        let refs = find_edges(&ctx, EdgeKind::References);
        assert!(!refs.is_empty(), "Expected references edge for form action");
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("/submit")), "Expected /submit in {:?}", targets);
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.html");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_multiple_elements() {
        let ctx = extract("<div id=\"a\"></div><p id=\"b\">text</p><span></span>", "src/test.html");
        let elements = find_nodes(&ctx, NodeKind::HtmlElement);
        assert_eq!(elements.len(), 3);
    }

    #[test]
    fn test_extract_self_closing_element() {
        let ctx = extract("<br/><img src=\"photo.png\"/>", "src/test.html");
        let elements = find_nodes(&ctx, NodeKind::HtmlElement);
        assert!(elements.len() >= 1, "Expected at least 1 element, got {}", elements.len());
    }
}
