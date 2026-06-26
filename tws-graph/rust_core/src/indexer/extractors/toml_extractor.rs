//! TOML language extractor.
//!
//! Extracts symbols and relationships from TOML source files (`.toml`)
//! using the tree-sitter-toml-ng grammar.
//!
//! # Node kinds produced
//! - `toml_table`: TOML table headers `[table]`
//! - `toml_table_array`: TOML array of tables `[[array]]`
//!
//! # Edge kinds produced
//! - `contains`: containment (parent table -> child table and key-value pairs)

use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct TomlExtractor;

impl Extractor for TomlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["toml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["toml"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::File, "toml", &root, HashMap::new());

        walk_toml(source, root, ctx, &file_id)?;

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

/// Get the key name from a table or table_array_element header.
/// In toml-ng grammar: table → [, bare_key/dotted_key, ], pair...
fn get_header_name(source: &[u8], node: Node) -> String {
    // Check for bare_key first
    if let Some(key_node) = find_child(node, "bare_key") {
        return get_text(source, Some(key_node));
    }
    // Check for dotted_key
    if let Some(key_node) = find_child(node, "dotted_key") {
        return get_text(source, Some(key_node));
    }
    // Fallback: iterate named children for key-like nodes
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let kind = child.kind();
            if kind == "bare_key" || kind == "dotted_key" {
                return get_text(source, Some(child));
            }
        }
    }
    String::new()
}

fn walk_toml(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "table" => {
                    extract_table(source, child, ctx, parent_id)?;
                }
                "table_array_element" => {
                    extract_table_array(source, child, ctx, parent_id)?;
                }
                "pair" => {
                    extract_pair(source, child, ctx, parent_id)?;
                }
                _ => {
                    walk_toml(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_table(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<(String, usize)> {
    let header_text = get_header_name(source, node);
    if header_text.is_empty() {
        return Ok((String::new(), 0));
    }

    let line = node.start_position().row as u32 + 1;
    let table_id = ctx.add_node(NodeKind::TomlTable, &header_text, &node, HashMap::new());
    ctx.add_edge(parent_id, &table_id, EdgeKind::Contains, line, None);

    let mut pair_count = 0;
    // Walk children to find pairs and nested tables
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "pair" => {
                    extract_pair(source, child, ctx, &table_id)?;
                    pair_count += 1;
                }
                "table" => {
                    extract_table(source, child, ctx, &table_id)?;
                }
                "table_array_element" => {
                    extract_table_array(source, child, ctx, parent_id)?;
                }
                _ => {}
            }
        }
    }

    Ok((header_text, pair_count))
}

fn extract_table_array(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let header_text = get_header_name(source, node);
    if header_text.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;
    let ta_id = ctx.add_node(NodeKind::TomlTableArray, &header_text, &node, HashMap::new());
    ctx.add_edge(parent_id, &ta_id, EdgeKind::Contains, line, None);

    // Walk children for pairs
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "pair" {
                extract_pair(source, child, ctx, &ta_id)?;
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
) -> anyhow::Result<()> {
    // In toml-ng grammar: pair → key_node = value_node
    // Key is the first named child (bare_key, dotted_key, quoted_key, etc.)
    let key_text = if let Some(key_node) = node.named_child(0) {
        get_text(source, Some(key_node))
    } else {
        String::new()
    };

    if key_text.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;
    let key_id = ctx.add_node(NodeKind::Field, &key_text, &node, HashMap::new());
    ctx.add_edge(parent_id, &key_id, EdgeKind::Contains, line, None);

    Ok(())
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
            .set_language(&tree_sitter_toml_ng::LANGUAGE.into())
            .expect("set toml language");
        let tree = parser.parse(source, None).expect("parse toml source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "toml".to_string());
        TomlExtractor
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
    fn test_extract_simple_table() {
        let ctx = extract("[package]\nname = \"myapp\"\n", "src/test.toml");
        let tables = find_nodes(&ctx, NodeKind::TomlTable);
        assert_eq!(tables.len(), 1, "Expected 1 toml_table, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        assert_eq!(tables[0].name, "package");
    }

    #[test]
    fn test_extract_dotted_table() {
        let ctx = extract("[package.metadata]\nkey = \"value\"\n", "src/test.toml");
        let tables = find_nodes(&ctx, NodeKind::TomlTable);
        assert_eq!(tables.len(), 1, "Expected 1 table, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        assert_eq!(tables[0].name, "package.metadata");
    }

    #[test]
    fn test_extract_multiple_tables() {
        let ctx = extract(
            "[package]\nname = \"app\"\n\n[dependencies]\nlib = \"1.0\"\n",
            "src/test.toml",
        );
        let tables = find_nodes(&ctx, NodeKind::TomlTable);
        assert_eq!(tables.len(), 2, "Expected 2 tables, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        let names: Vec<&str> = tables.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"package"));
        assert!(names.contains(&"dependencies"));
    }

    #[test]
    fn test_extract_table_array() {
        let ctx = extract("[[servers]]\nhost = \"a\"\n\n[[servers]]\nhost = \"b\"\n", "src/test.toml");
        let ta = find_nodes(&ctx, NodeKind::TomlTableArray);
        assert_eq!(ta.len(), 2, "Expected 2 table_arrays, got {:?}", ta.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        assert!(ta[0].name.contains("servers"));
        assert!(ta[1].name.contains("servers"));
    }

    #[test]
    fn test_extract_key_value_pairs() {
        let ctx = extract("[package]\nname = \"myapp\"\nversion = \"1.0\"\n", "src/test.toml");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2, "Expected 2 fields, got {:?}", fields.iter().map(|f| f.name.as_str()).collect::<Vec<_>>());
        let names: Vec<&str> = fields.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"name"));
        assert!(names.contains(&"version"));
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.toml");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_root_keys() {
        let ctx = extract("title = \"My Config\"\ndescription = \"A test config\"\n", "src/test.toml");
        let fields = find_nodes(&ctx, NodeKind::Field);
        assert_eq!(fields.len(), 2, "Expected 2 fields for root keys, got {:?}", fields.iter().map(|f| f.name.as_str()).collect::<Vec<_>>());
    }

    #[test]
    fn test_extract_deeply_nested_table() {
        let ctx = extract("[a.b.c.d]\nkey = \"value\"\n", "src/test.toml");
        let tables = find_nodes(&ctx, NodeKind::TomlTable);
        assert_eq!(tables.len(), 1, "Expected 1 table, got {:?}", tables.iter().map(|t| t.name.as_str()).collect::<Vec<_>>());
        // dotted_key creates the name as "a.b.c.d"
        assert_eq!(tables[0].name, "a.b.c.d", "Expected dotted name a.b.c.d, got '{}'", tables[0].name);
    }

    #[test]
    fn test_extract_contains_edges() {
        let ctx = extract("[package]\nname = \"app\"\n", "src/test.toml");
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(contains.len() >= 2, "Expected at least 2 contains edges (file->table, table->field), got {:?}", contains.iter().map(|e| format!("{} -> {}", e.source, e.target)).collect::<Vec<_>>());
    }

    #[test]
    fn test_extract_complex_toml() {
        let ctx = extract(
            "[package]\nname = \"app\"\nversion = \"1.0\"\n\n[dependencies]\nserde = \"1.0\"\n\n[[bin]]\nname = \"cli\"\npath = \"src/main.rs\"\n",
            "src/test.toml",
        );
        let tables = find_nodes(&ctx, NodeKind::TomlTable);
        assert!(!tables.is_empty(), "Should have tables, got {:?}", ctx.result.nodes.iter().map(|n| format!("{}:{}", n.kind, n.name)).collect::<Vec<_>>());

        let ta = find_nodes(&ctx, NodeKind::TomlTableArray);
        assert!(!ta.is_empty(), "Should have table arrays");

        let fields = find_nodes(&ctx, NodeKind::Field);
        assert!(fields.len() >= 4, "Expected at least 4 field nodes, got {}", fields.len());
    }
}
