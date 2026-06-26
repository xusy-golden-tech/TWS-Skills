//! Kustomize language extractor.
//!
//! Extracts symbols from Kustomize (kustomization.yaml) files.
//! Since kustomize files are YAML-based, this extractor uses tree-sitter-yaml
//! to parse the file, then scans for recognized kustomize-specific top-level
//! section keys.
//!
//! # Node kinds produced
//! - `kustomize_section`: recognized kustomize section (resources, bases,
//!   patches, configMapGenerator, secretGenerator, images, etc.)
//!
//! # Edge kinds produced
//! - `contains`: containment (file -> kustomize_section)

use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

/// Known Kustomize top-level section keys.
const KUSTOMIZE_KEYS: &[&str] = &[
    "resources",
    "bases",
    "patches",
    "patchesStrategicMerge",
    "patchesJson6902",
    "configMapGenerator",
    "secretGenerator",
    "images",
    "namePrefix",
    "nameSuffix",
    "commonLabels",
    "commonAnnotations",
    "namespace",
    "vars",
    "generators",
    "transformers",
    "replacements",
    "components",
    "configurations",
    "crds",
    "openapi",
    "sortOptions",
    "labels",
    "annotations",
    "buildMetadata",
];

pub struct KustomizeExtractor;

impl Extractor for KustomizeExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["yaml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["kustomize"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();

        let file_id = ctx.add_node(NodeKind::File, "kustomization", &root, HashMap::new());

        walk_kustomize(source, root, ctx, &file_id, false)?;

        Ok(())
    }
}

fn walk_kustomize(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    is_top_level: bool,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "block_mapping_pair" | "flow_pair" => {
                    extract_kustomize_pair(source, child, ctx, parent_id, is_top_level)?;
                }
                "block_mapping" | "flow_mapping" => {
                    // A mapping container — children are mapping pairs.
                    // For top-level, the pairs inside a top-level mapping are top-level.
                    let pair_is_toplevel = is_top_level;
                    walk_kustomize(source, child, ctx, parent_id, pair_is_toplevel)?;
                }
                "block_sequence" | "flow_sequence" => {
                    // Inside a recognized section — not top-level
                    walk_kustomize(source, child, ctx, parent_id, false)?;
                }
                "block_node" | "flow_node" => {
                    // Wrapper node — recurse through it
                    walk_kustomize(source, child, ctx, parent_id, is_top_level)?;
                }
                "document" => {
                    walk_kustomize(source, child, ctx, parent_id, true)?;
                }
                _ => {
                    walk_kustomize(source, child, ctx, parent_id, is_top_level)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_kustomize_pair(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    is_top_level: bool,
) -> anyhow::Result<()> {
    // Get the key text
    let key_text = if let Some(key_node) = node.named_child(0) {
        extract_scalar_text(source, key_node)
    } else {
        String::new()
    };

    if key_text.is_empty() {
        return Ok(());
    }

    let line = node.start_position().row as u32 + 1;

    if is_top_level && KUSTOMIZE_KEYS.contains(&key_text.as_str()) {
        // This is a recognized kustomize section
        let section_id = ctx.add_node(
            NodeKind::KustomizeSection,
            &key_text,
            &node,
            HashMap::new(),
        );
        ctx.add_edge(parent_id, &section_id, EdgeKind::Contains, line, None);

        // Recurse into the value to find nested sections (but not as top-level)
        if node.named_child_count() >= 2 {
            if let Some(value) = node.named_child(node.named_child_count() - 1) {
                walk_kustomize(source, value, ctx, &section_id, false)?;
            }
        }
    } else {
        // Not a top-level kustomize key — recurse to find nested sections
        if node.named_child_count() >= 2 {
            if let Some(value) = node.named_child(node.named_child_count() - 1) {
                walk_kustomize(source, value, ctx, parent_id, false)?;
            }
        }
    }

    Ok(())
}

/// Extract scalar text from a YAML node, following flow_node / block_node wrappers.
fn extract_scalar_text(source: &[u8], node: Node) -> String {
    match node.kind() {
        "flow_node" | "block_node" => {
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

        let mut ctx = ExtractionContext::new(file_path.to_string(), "kustomize".to_string());
        KustomizeExtractor
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

    // ==================================================================
    // Basic extraction
    // ==================================================================

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "kustomization.yaml");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_resources_section() {
        let ctx = extract(
            "resources:\n  - deployment.yaml\n  - service.yaml\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].name, "resources");
    }

    #[test]
    fn test_extract_multiple_sections() {
        let ctx = extract(
            "resources:\n  - deploy.yaml\nbases:\n  - ../base\nimages:\n  - name: myapp\n    newTag: v1.0\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 3);
        let names: Vec<&str> = sections.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"resources"));
        assert!(names.contains(&"bases"));
        assert!(names.contains(&"images"));
    }

    #[test]
    fn test_extract_configmap_generator() {
        let ctx = extract(
            "configMapGenerator:\n  - name: app-config\n    files:\n      - config.properties\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].name, "configMapGenerator");
    }

    #[test]
    fn test_extract_secret_generator() {
        let ctx = extract(
            "secretGenerator:\n  - name: db-credentials\n    literals:\n      - password=secret\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].name, "secretGenerator");
    }

    #[test]
    fn test_extract_patches() {
        let ctx = extract(
            "patchesStrategicMerge:\n  - patch.yaml\npatchesJson6902:\n  - target:\n      kind: Deployment\n    path: patch.yaml\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 2);
        let names: Vec<&str> = sections.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"patchesStrategicMerge"));
        assert!(names.contains(&"patchesJson6902"));
    }

    #[test]
    fn test_extract_transformer_blocks() {
        let ctx = extract(
            "transformers:\n  - transformer1.yaml\n  - transformer2.yaml\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].name, "transformers");
    }

    #[test]
    fn test_extract_namespace() {
        let ctx = extract(
            "namespace: my-namespace\nresources:\n  - deploy.yaml\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 2);
        let names: Vec<&str> = sections.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"namespace"));
        assert!(names.contains(&"resources"));
    }

    #[test]
    fn test_extract_name_prefix_suffix() {
        let ctx = extract(
            "namePrefix: prod-\nnameSuffix: -v1\ncommonLabels:\n  env: production\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 3);
        let names: Vec<&str> = sections.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"namePrefix"));
        assert!(names.contains(&"nameSuffix"));
        assert!(names.contains(&"commonLabels"));
    }

    #[test]
    fn test_extract_containment_edges() {
        let ctx = extract(
            "resources:\n  - deploy.yaml\nbases:\n  - ../base\n",
            "kustomization.yaml",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(contains.len() >= 2, "Expected >=2 CONTAINS edges, got {}", contains.len());
    }

    #[test]
    fn test_no_crash_on_non_kustomize_yaml() {
        let ctx = extract(
            "app:\n  name: myapp\n  version: \"1.0\"\n",
            "config.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 0);
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_generators_section() {
        let ctx = extract(
            "generators:\n  - plugin-generator.yaml\n",
            "kustomization.yaml",
        );
        let sections = find_nodes(&ctx, NodeKind::KustomizeSection);
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].name, "generators");
    }
}
