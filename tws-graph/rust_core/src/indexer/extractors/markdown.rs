//! Markdown language extractor.
//!
//! Extracts symbols and relationships from Markdown source files
//! (`.md`, `.markdown`, `.mdx`) using the tree-sitter-markdown grammar.
//!
//! # Node kinds produced
//! - `md_heading`: ATX heading with level
//! - `md_code_block`: fenced code blocks with language
//! - `md_link`: inline links `[text](url)`
//! - `md_image`: inline images `![alt](url)`
//! - `md_refdef`: link reference definitions `[label]: url`
//!
//! # Edge kinds produced
//! - `contains`: heading contains sub-headings / blocks
//! - `references`: links and images reference URLs

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct MarkdownExtractor;

impl Extractor for MarkdownExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["md", "markdown", "mdx"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["markdown"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "markdown", &root, HashMap::new());

        let mut heading_stack: Vec<(String, u32)> = Vec::new();
        walk_markdown(source, root, ctx, &file_id, &mut heading_stack)?;

        Ok(())
    }
}

/// Get heading level from ATX heading (count of '#' characters).
fn heading_level(source: &[u8], heading_node: Node) -> u32 {
    let text = get_text(source, Some(heading_node));
    text.chars().take_while(|c| *c == '#').count() as u32
}

fn walk_markdown(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    heading_stack: &mut Vec<(String, u32)>,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "atx_heading" => {
                    extract_heading(source, child, ctx, parent_id, heading_stack)?;
                }
                "fenced_code_block" => {
                    extract_code_block(source, child, ctx, parent_id)?;
                }
                "link_reference_definition" => {
                    extract_refdef(source, child, ctx, parent_id)?;
                }
                "section" => {
                    // Walk into sections to find nested content
                    walk_markdown(source, child, ctx, parent_id, heading_stack)?;
                }
                _ => {
                    // Check for inline links and images
                    extract_inline_nodes(source, child, ctx, parent_id)?;
                    // Recurse to find nested nodes
                    walk_markdown(source, child, ctx, parent_id, heading_stack)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_heading(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    heading_stack: &mut Vec<(String, u32)>,
) -> anyhow::Result<()> {
    let level = heading_level(source, node);
    let content = get_text(source, Some(node));
    // Strip the leading '#' characters and whitespace
    let name = content.trim_start_matches('#').trim().to_string();
    if name.is_empty() {
        return Ok(());
    }

    // Pop headings of equal or higher level to maintain the stack
    while let Some((_, l)) = heading_stack.last() {
        if *l >= level {
            heading_stack.pop();
        } else {
            break;
        }
    }

    // Determine parent: use nearest lower-level heading or file
    let actual_parent = if let Some((_, _)) = heading_stack.last() {
        // Use the file as parent for simplicity — headings are siblings in the flat model
        parent_id.to_string()
    } else {
        parent_id.to_string()
    };

    let line = node.start_position().row as u32 + 1;
    let heading_id = ctx.add_node(NodeKind::MdHeading, &name, &node, HashMap::new());
    ctx.add_edge(&actual_parent, &heading_id, EdgeKind::Contains, line, None);

    heading_stack.push((name, level));

    Ok(())
}

fn extract_code_block(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    // Get language from info string
    let mut lang = String::new();
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "info_string" {
                lang = get_text(source, Some(child));
                break;
            }
        }
    }
    let name = if lang.is_empty() {
        "code_block".to_string()
    } else {
        format!("code_block:{}", lang)
    };

    let line = node.start_position().row as u32 + 1;
    let cb_id = ctx.add_node(NodeKind::MdCodeBlock, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &cb_id, EdgeKind::Contains, line, None);

    Ok(())
}

fn extract_inline_nodes(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    match node.kind() {
        "inline" => {
            let text = get_text(source, Some(node));
            // Image starts with ![alt](url), link is [text](url)
            if text.starts_with("![") {
                // Image
                if let Some(url) = extract_image_url(&text) {
                    let name = format!("image:{}", if url.len() > 40 { url.chars().take(40).collect::<String>() } else { url.to_string() });
                    let line = node.start_position().row as u32 + 1;
                    let img_id = ctx.add_node(NodeKind::MdImage, &name, &node, HashMap::new());
                    ctx.add_edge(parent_id, &img_id, EdgeKind::Contains, line, None);
                    let target = hash_id(&ctx.file_path, &url);
                    ctx.add_edge(&img_id, &target, EdgeKind::References, line, Some(&url));
                }
            } else {
                // Could contain one or more links
                for url in extract_all_link_urls(&text) {
                    let name = format!("link:{}", if url.len() > 40 { url.chars().take(40).collect::<String>() } else { url.to_string() });
                    let line = node.start_position().row as u32 + 1;
                    let link_id = ctx.add_node(NodeKind::MdLink, &name, &node, HashMap::new());
                    ctx.add_edge(parent_id, &link_id, EdgeKind::Contains, line, None);
                    let target = hash_id(&ctx.file_path, &url);
                    ctx.add_edge(&link_id, &target, EdgeKind::References, line, Some(&url));
                }
            }
        }
        _ => {}
    }
    Ok(())
}

fn extract_refdef(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let text = get_text(source, Some(node));
    let name = if text.len() > 60 {
        format!("refdef:{}...", text.chars().take(60).collect::<String>())
    } else {
        format!("refdef:{}", text)
    };

    let line = node.start_position().row as u32 + 1;
    let ref_id = ctx.add_node(NodeKind::MdRefdef, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &ref_id, EdgeKind::Contains, line, None);

    Ok(())
}

/// Extract URL from an inline link text like `[text](url)`.
fn extract_link_url(text: &str) -> Option<String> {
    if let Some(start) = text.find("](") {
        let rest = &text[start + 2..];
        if let Some(end) = rest.find(')') {
            return Some(rest[..end].to_string());
        }
    }
    None
}

/// Extract all URLs from an inline text that may contain multiple links.
fn extract_all_link_urls(text: &str) -> Vec<String> {
    let mut urls = Vec::new();
    let mut search = text;
    while let Some(start) = search.find("](") {
        let rest = &search[start + 2..];
        if let Some(end) = rest.find(')') {
            urls.push(rest[..end].to_string());
            search = &rest[end + 1..];
        } else {
            break;
        }
    }
    urls
}

/// Extract URL from an image text like `![alt](url)`.
fn extract_image_url(text: &str) -> Option<String> {
    if let Some(start) = text.find("](") {
        let rest = &text[start + 2..];
        if let Some(end) = rest.find(')') {
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
            .set_language(&tree_sitter_md::LANGUAGE.into())
            .expect("set markdown language");
        let tree = parser.parse(source, None).expect("parse markdown source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "markdown".to_string());
        MarkdownExtractor
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
    fn test_extract_heading() {
        let ctx = extract("# Introduction\n\nSome text.\n", "src/test.md");
        let headings = find_nodes(&ctx, NodeKind::MdHeading);
        assert_eq!(headings.len(), 1);
        assert_eq!(headings[0].name, "Introduction");
    }

    #[test]
    fn test_extract_multiple_headings() {
        let ctx = extract("# H1\n## H2\n### H3\n", "src/test.md");
        let headings = find_nodes(&ctx, NodeKind::MdHeading);
        assert_eq!(headings.len(), 3);
        let names: Vec<&str> = headings.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"H1"));
        assert!(names.contains(&"H2"));
        assert!(names.contains(&"H3"));
    }

    #[test]
    fn test_extract_code_block() {
        let ctx = extract("```rust\nfn main() {}\n```\n", "src/test.md");
        let blocks = find_nodes(&ctx, NodeKind::MdCodeBlock);
        assert_eq!(blocks.len(), 1);
        assert!(blocks[0].name.contains("rust"));
    }

    #[test]
    fn test_extract_code_block_no_lang() {
        let ctx = extract("```\nplain text\n```\n", "src/test.md");
        let blocks = find_nodes(&ctx, NodeKind::MdCodeBlock);
        assert_eq!(blocks.len(), 1);
    }

    #[test]
    fn test_extract_inline_link() {
        let ctx = extract("[Click here](https://example.com)\n", "src/test.md");
        let links = find_nodes(&ctx, NodeKind::MdLink);
        assert!(links.len() >= 1, "Expected at least 1 link, got {}", links.len());

        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("example.com")), "Expected example.com in {:?}", targets);
    }

    #[test]
    fn test_extract_image() {
        let ctx = extract("![Alt text](https://example.com/img.png)\n", "src/test.md");
        let images = find_nodes(&ctx, NodeKind::MdImage);
        assert!(images.len() >= 1, "Expected at least 1 image, got {}", images.len());
    }

    #[test]
    fn test_extract_refdef() {
        let ctx = extract("[label]: https://example.com \"Title\"\n", "src/test.md");
        let refdefs = find_nodes(&ctx, NodeKind::MdRefdef);
        assert_eq!(refdefs.len(), 1);
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.md");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_multiple_links() {
        let ctx = extract(
            "[Link1](http://a.com) and [Link2](http://b.com)\n",
            "src/test.md",
        );
        let links = find_nodes(&ctx, NodeKind::MdLink);
        assert!(links.len() >= 2, "Expected at least 2 links, got {}", links.len());
    }

    #[test]
    fn test_extract_complex_document() {
        let ctx = extract(
            "# Title\n\nSome text with [a link](http://x.com).\n\n## Subtitle\n\n```python\nprint('hello')\n```\n\n![img](http://y.com/pic.png)\n",
            "src/test.md",
        );
        let headings = find_nodes(&ctx, NodeKind::MdHeading);
        assert!(headings.len() >= 2, "Expected at least 2 headings");

        let blocks = find_nodes(&ctx, NodeKind::MdCodeBlock);
        assert!(blocks.len() >= 1, "Expected at least 1 code block");

        let links = find_nodes(&ctx, NodeKind::MdLink);
        assert!(links.len() >= 1, "Expected at least 1 link");
    }
}
