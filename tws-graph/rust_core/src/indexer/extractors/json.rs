//! JSON language extractor.
//!
//! Extracts symbols and relationships from JSON source files (`.json`)
//! using the tree-sitter-json grammar.
//!
//! # Node kinds produced
//! - `json_key`: key paths using dot-joined notation
//!
//! # Edge kinds produced
//! - `contains`: containment (parent key -> child key)
//! - `imports`: `package.json` dependencies

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct JsonExtractor;

impl Extractor for JsonExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["json"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["json"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "json", &root, HashMap::new());

        // Check if this is a package.json for dependency extraction
        let is_package_json = ctx.file_path.ends_with("package.json")
            || ctx.file_path.contains("package.json");

        walk_json(source, root, ctx, &file_id, &Vec::new(), is_package_json)?;

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

fn walk_json(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    path_prefix: &[String],
    is_package_json: bool,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "object" => {
                    walk_json(source, child, ctx, parent_id, path_prefix, is_package_json)?;
                }
                "pair" => {
                    extract_pair(source, child, ctx, parent_id, path_prefix, is_package_json)?;
                }
                "array" => {
                    walk_json(source, child, ctx, parent_id, path_prefix, is_package_json)?;
                }
                _ => {
                    walk_json(source, child, ctx, parent_id, path_prefix, is_package_json)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_pair(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    path_prefix: &[String],
    is_package_json: bool,
) -> anyhow::Result<()> {
    // Key is the first named child, value is the last named child
    // (JSON grammar: pair → string(key) : value)
    let key_node = node.named_child(0);
    let key_text = get_json_key_text(source, key_node);
    if key_text.is_empty() {
        return Ok(());
    }

    let mut full_path = path_prefix.to_vec();
    full_path.push(key_text.clone());
    let path_str = full_path.join(".");

    let line = node.start_position().row as u32 + 1;
    let key_id = ctx.add_node(NodeKind::JsonKey, &path_str, &node, HashMap::new());
    ctx.add_edge(parent_id, &key_id, EdgeKind::Contains, line, None);

    // Handle package.json dependencies extraction
    if is_package_json && (key_text == "dependencies" || key_text == "devDependencies" || key_text == "peerDependencies") {
        // Value is the last named child
        if let Some(value) = node.named_child(node.named_child_count().saturating_sub(1)) {
            if value.kind() == "object" {
                extract_dependencies(source, value, &key_id, line, ctx);
            }
        }
    }

    // Recurse into value for nested objects/arrays
    if let Some(value) = node.named_child(node.named_child_count().saturating_sub(1)) {
        match value.kind() {
            "object" | "array" => {
                walk_json(source, value, ctx, &key_id, &full_path, is_package_json)?;
            }
            _ => {}
        }
    }

    Ok(())
}

/// Get the content text from a JSON key string node.
fn get_json_key_text(source: &[u8], key_node: Option<Node>) -> String {
    match key_node {
        Some(n) => {
            // JSON string node contains "string_content" child with the actual text
            if let Some(content) = find_child(n, "string_content") {
                return get_text(source, Some(content));
            }
            // Fallback: strip quotes from the full text
            let text = get_text(source, Some(n));
            text.trim_matches('"').to_string()
        }
        None => String::new(),
    }
}

/// Extract dependencies from a package.json dependencies object.
fn extract_dependencies(source: &[u8], deps_node: Node<'_>, parent_id: &str, line: u32, ctx: &mut ExtractionContext) {
    for i in 0..deps_node.named_child_count() {
        if let Some(child) = deps_node.named_child(i) {
            if child.kind() == "pair" {
                // Key is the first named child
                if let Some(key_node) = child.named_child(0) {
                    let dep_name = get_json_key_text(source, Some(key_node));
                    if !dep_name.is_empty() {
                        let target = hash_id(&ctx.file_path, &dep_name);
                        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&dep_name));
                    }
                }
            }
        }
    }
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
            .set_language(&tree_sitter_json::LANGUAGE.into())
            .expect("set json language");
        let tree = parser.parse(source, None).expect("parse json source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "json".to_string());
        JsonExtractor
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
        let ctx = extract("{\"name\": \"myapp\"}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        assert_eq!(keys.len(), 1, "Expected 1 json_key, got {}: {:?}", keys.len(), ctx.result.nodes.iter().map(|n| format!("kind={} name={}", n.kind, n.name)).collect::<Vec<_>>());
        assert_eq!(keys[0].name, "name");
    }

    #[test]
    fn test_extract_nested_keys() {
        let ctx = extract("{\"server\": {\"host\": \"localhost\", \"port\": 8080}}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"server"), "Expected 'server' in {:?}", names);
        assert!(names.contains(&"server.host"), "Expected 'server.host' in {:?}", names);
        assert!(names.contains(&"server.port"), "Expected 'server.port' in {:?}", names);
    }

    #[test]
    fn test_extract_deeply_nested() {
        let ctx = extract("{\"a\": {\"b\": {\"c\": \"value\"}}}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"a.b.c"), "Expected a.b.c in {:?}", names);
    }

    #[test]
    fn test_extract_array() {
        let ctx = extract("{\"items\": [\"one\", \"two\", \"three\"]}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        assert_eq!(keys.len(), 1);
        assert_eq!(keys[0].name, "items");
    }

    #[test]
    fn test_extract_package_json_deps() {
        let ctx = extract(
            "{\"dependencies\": {\"express\": \"^4.18.0\", \"lodash\": \"^4.17.21\"}}",
            "package.json",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"express"), "Expected express in {:?}", targets);
        assert!(targets.contains(&"lodash"), "Expected lodash in {:?}", targets);
    }

    #[test]
    fn test_extract_package_json_dev_deps() {
        let ctx = extract(
            "{\"devDependencies\": {\"jest\": \"^29.0.0\"}}",
            "package.json",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.contains(&"jest"), "Expected jest in {:?}", targets);
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("{}", "src/empty.json");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_multiple_keys() {
        let ctx = extract("{\"name\": \"x\", \"version\": \"1.0\", \"author\": \"me\"}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        assert_eq!(keys.len(), 3, "Expected 3 keys, got {:?}", keys.iter().map(|k| k.name.as_str()).collect::<Vec<_>>());
    }

    #[test]
    fn test_extract_nested_objects_in_array() {
        let ctx = extract("{\"users\": [{\"name\": \"Alice\"}, {\"name\": \"Bob\"}]}", "src/test.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        let names: Vec<&str> = keys.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"users"), "Expected users in {:?}", names);
    }

    #[test]
    fn test_extract_non_package_json_no_imports() {
        let ctx = extract("{\"dependencies\": {\"lib\": \"1.0\"}}", "src/config.json");
        let keys = find_nodes(&ctx, NodeKind::JsonKey);
        assert!(!keys.is_empty(), "Keys should exist even for non-package.json");
    }
}
