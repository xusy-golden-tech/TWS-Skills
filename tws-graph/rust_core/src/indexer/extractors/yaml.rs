//! YAML language extractor.
//!
//! Extracts symbols and relationships from YAML source files (`.yaml`, `.yml`)
//! using the tree-sitter-yaml grammar.
//!
//! # Node kinds produced
//! - `yaml_key`: key paths using dot-joined notation
//! - `yaml_document`: individual YAML documents
//!
//! # Edge kinds produced
//! - `contains`: containment (parent key -> child key)
//! - `references`: `${VAR}` variable references
//! - `imports`: `!!include` / `!!import` tags

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct YamlExtractor;

impl Extractor for YamlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["yaml", "yml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["yaml"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "yaml", &root, HashMap::new());

        walk_yaml(source, root, ctx, &file_id, &Vec::new())?;

        Ok(())
    }
}

fn walk_yaml(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    path_prefix: &[String],
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "document" => {
                    let doc_id = ctx.add_node(NodeKind::YamlDocument, "document", &child, HashMap::new());
                    let line = child.start_position().row as u32 + 1;
                    ctx.add_edge(parent_id, &doc_id, EdgeKind::Contains, line, None);
                    walk_yaml(source, child, ctx, &doc_id, &Vec::new())?;
                }
                "block_mapping_pair" | "flow_pair" => {
                    extract_mapping_pair(source, child, ctx, parent_id, path_prefix)?;
                }
                "block_node" | "flow_node" | "stream" => {
                    walk_yaml(source, child, ctx, parent_id, path_prefix)?;
                }
                "block_mapping" | "flow_mapping" | "block_sequence" | "flow_sequence" => {
                    walk_yaml(source, child, ctx, parent_id, path_prefix)?;
                }
                _ => {
                    walk_yaml(source, child, ctx, parent_id, path_prefix)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_mapping_pair(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    path_prefix: &[String],
) -> anyhow::Result<()> {
    // YAML grammar: block_mapping_pair → key_node : value_node
    // The key is the first named child
    let key_text = if let Some(key_node) = node.named_child(0) {
        extract_scalar_text(source, key_node)
    } else {
        String::new()
    };

    if key_text.is_empty() {
        return Ok(());
    }

    let mut full_path = path_prefix.to_vec();
    full_path.push(key_text.clone());
    let path_str = full_path.join(".");

    // Check for !!include / !!import tags
    let node_text = get_text(source, Some(node));
    let has_include = node_text.contains("!!include") || node_text.contains("!!import");

    let line = node.start_position().row as u32 + 1;
    let key_id = ctx.add_node(NodeKind::YamlKey, &path_str, &node, HashMap::new());
    ctx.add_edge(parent_id, &key_id, EdgeKind::Contains, line, None);

    // Check for ${VAR} references in the full node text
    extract_var_refs(&node_text, &key_id, line, ctx);

    // Handle !!include / !!import
    if has_include {
        if let Some(include_path) = extract_include_path(&node_text) {
            let target = hash_id(&ctx.file_path, &include_path);
            ctx.add_edge(&key_id, &target, EdgeKind::Imports, line, Some(&include_path));
        }
    }

    // Recurse into value for nested mappings
    // Value is typically the last named child (after the ':' anonymous node)
    if node.named_child_count() >= 2 {
        if let Some(value) = node.named_child(node.named_child_count() - 1) {
            match value.kind() {
                "block_mapping" | "flow_mapping" | "block_sequence" | "flow_sequence" => {
                    walk_yaml(source, value, ctx, &key_id, &full_path)?;
                }
                "block_node" | "flow_node" => {
                    walk_yaml(source, value, ctx, &key_id, &full_path)?;
                }
                _ => {}
            }
        }
    }

    Ok(())
}

/// Recursively extract the scalar text from a YAML node (going through flow_node → plain_scalar → string_scalar).
fn extract_scalar_text(source: &[u8], node: Node) -> String {
    match node.kind() {
        "flow_node" | "block_node" => {
            // Get the embedded scalar
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    let text = extract_scalar_text(source, child);
                    if !text.is_empty() {
                        return text;
                    }
                }
            }
            get_text(source, Some(node))
        }
        "plain_scalar" | "double_quote_scalar" | "single_quote_scalar" => {
            // Find string_scalar child
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    if child.kind() == "string_scalar" {
                        return get_text(source, Some(child));
                    }
                }
            }
            get_text(source, Some(node))
        }
        _ => get_text(source, Some(node)),
    }
}

/// Extract `${VAR}` variable references from text.
fn extract_var_refs(text: &str, parent_id: &str, line: u32, ctx: &mut ExtractionContext) {
    let mut pos = 0;
    while let Some(idx) = text[pos..].find("${") {
        let abs = pos + idx;
        let start = abs + 2;
        if let Some(end) = text[start..].find('}') {
            let var_name = &text[start..start + end];
            if !var_name.is_empty() {
                let target = hash_id(&ctx.file_path, var_name);
                ctx.add_edge(parent_id, &target, EdgeKind::References, line, Some(var_name));
            }
            pos = start + end + 1;
        } else {
            break;
        }
    }
}

/// Extract path from !!include or !!import directives.
fn extract_include_path(text: &str) -> Option<String> {
    for tag in &["!!include", "!!import"] {
        if let Some(idx) = text.find(tag) {
            let rest = &text[idx + tag.len()..].trim();
            let path: String = rest
                .chars()
                .take_while(|c| !c.is_whitespace())
                .collect();
            if !path.is_empty() {
                return Some(path);
            }
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
            .set_language(&tree_sitter_yaml::LANGUAGE.into())
            .expect("set yaml language");
        let tree = parser.parse(source, None).expect("parse yaml source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "yaml".to_string());
        YamlExtractor
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
    fn test_extract_simple_key() {
        let ctx = extract("name: myapp\n", "src/test.yaml");
        let keys = find_nodes(&ctx, NodeKind::YamlKey);
        assert_eq!(keys.len(), 1, "Expected 1 yaml_key, got {:?}", keys.iter().map(|k| k.name.as_str()).collect::<Vec<_>>());
        assert_eq!(keys[0].name, "name");
    }

    #[test]
    fn test_extract_nested_keys() {
        let ctx = extract("server:\n  host: localhost\n  port: 8080\n", "src/test.yaml");
        let keys = find_nodes(&ctx, NodeKind::YamlKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"server"), "Expected server in {:?}", names);
        assert!(names.contains(&"server.host"), "Expected server.host in {:?}", names);
        assert!(names.contains(&"server.port"), "Expected server.port in {:?}", names);
    }

    #[test]
    fn test_extract_deeply_nested() {
        let ctx = extract("a:\n  b:\n    c: value\n", "src/test.yaml");
        let keys = find_nodes(&ctx, NodeKind::YamlKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"a.b.c"), "Expected a.b.c in {:?}", names);
    }

    #[test]
    fn test_extract_variable_reference() {
        let ctx = extract("password: ${DB_PASSWORD}\n", "src/test.yaml");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("DB_PASSWORD")), "Expected DB_PASSWORD in {:?}", targets);
    }

    #[test]
    fn test_extract_list_items() {
        let ctx = extract("items:\n  - one\n  - two\n", "src/test.yaml");
        let keys = find_nodes(&ctx, NodeKind::YamlKey);
        assert!(!keys.is_empty(), "Expected at least one key");
        assert!(keys.iter().any(|k| k.name == "items"));
    }

    #[test]
    fn test_extract_multiple_documents() {
        let ctx = extract("---\ndoc1: value1\n---\ndoc2: value2\n", "src/test.yaml");
        let docs = find_nodes(&ctx, NodeKind::YamlDocument);
        assert_eq!(docs.len(), 2, "Expected 2 documents, got {:?}", docs.iter().map(|d| d.name.as_str()).collect::<Vec<_>>());
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.yaml");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_complex_nested() {
        let ctx = extract(
            "app:\n  name: myapp\n  database:\n    host: localhost\n    port: 5432\n  cache:\n    host: redis\n",
            "src/test.yaml",
        );
        let keys = find_nodes(&ctx, NodeKind::YamlKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"app.database.host"), "Expected app.database.host in {:?}", names);
        assert!(names.contains(&"app.cache.host"), "Expected app.cache.host in {:?}", names);
    }

    #[test]
    fn test_extract_multiple_var_refs() {
        let ctx = extract("url: http://${HOST}:${PORT}/api\n", "src/test.yaml");
        let refs = find_edges(&ctx, EdgeKind::References);
        let targets: Vec<&str> = refs.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("HOST")), "Expected HOST in {:?}", targets);
        assert!(targets.iter().any(|t| t.contains("PORT")), "Expected PORT in {:?}", targets);
    }

    #[test]
    fn test_extract_include_tag() {
        let ctx = extract("config: !!include config/base.yaml\n", "src/test.yaml");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 1, "Expected at least 1 import edge for !!include, got {}", imports.len());
    }
}
