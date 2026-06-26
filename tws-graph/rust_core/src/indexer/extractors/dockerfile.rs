//! Dockerfile language extractor.
//!
//! Extracts symbols and relationships from Dockerfile source files
//! (`.dockerfile`, `Dockerfile`) using the tree-sitter-dockerfile grammar.
//!
//! # Node kinds produced
//! - `dockerfile`: the overall Dockerfile
//! - `dockerfile_stage`: individual `FROM` stages
//!
//! # Edge kinds produced
//! - `imports`: `FROM` base images, `COPY`/`ADD` source paths
//! - `calls`: `RUN` commands
//! - `env_accesses`: `ENV` variable declarations
//! - `contains`: containment

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct DockerfileExtractor;

impl Extractor for DockerfileExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["dockerfile"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["dockerfile"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();
        let file_id = ctx.add_node(NodeKind::DockerfileImage, "dockerfile", &root, HashMap::new());

        let mut stage_counter: usize = 0;
        walk_dockerfile(source, root, ctx, &file_id, &mut stage_counter)?;

        Ok(())
    }
}

fn walk_dockerfile(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    stage_counter: &mut usize,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "from_instruction" => {
                    extract_from(source, child, ctx, parent_id, stage_counter)?;
                }
                "run_instruction" => {
                    extract_run(source, child, ctx, parent_id)?;
                }
                "copy_instruction" | "add_instruction" => {
                    extract_copy(source, child, ctx, parent_id)?;
                }
                "env_instruction" => {
                    extract_env(source, child, ctx, parent_id)?;
                }
                _ => {
                    walk_dockerfile(source, child, ctx, parent_id, stage_counter)?;
                }
            }
        }
    }
    Ok(())
}

fn extract_from(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    stage_counter: &mut usize,
) -> anyhow::Result<()> {
    *stage_counter += 1;

    // Get the image name
    let full_text = get_text(source, Some(node));
    let image_name = extract_image_name(&full_text).unwrap_or_else(|| "unknown".to_string());

    // Get stage alias (AS name)
    let stage_name = extract_stage_alias(&full_text).unwrap_or_else(|| format!("stage_{}", stage_counter));

    let line = node.start_position().row as u32 + 1;
    let stage_id = ctx.add_node(NodeKind::DockerfileStage, &stage_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &stage_id, EdgeKind::Contains, line, None);

    // FROM imports the base image
    let target = hash_id(&ctx.file_path, &image_name);
    ctx.add_edge(&stage_id, &target, EdgeKind::Imports, line, Some(&image_name));

    Ok(())
}

fn extract_run(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let text = get_text(source, Some(node));
    let name = if text.len() > 60 {
        format!("RUN {}...", &text[4..64].replace('\n', " "))
    } else {
        text.replace('\n', " ")
    };

    let line = node.start_position().row as u32 + 1;
    let run_id = ctx.add_node(NodeKind::File, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &run_id, EdgeKind::Calls, line, Some(&name));

    Ok(())
}

fn extract_copy(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let text = get_text(source, Some(node));
    let line = node.start_position().row as u32 + 1;

    // Extract source paths from COPY/ADD instruction
    for src_path in extract_copy_sources(&text) {
        let target = hash_id(&ctx.file_path, &src_path);
        ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&src_path));
    }

    Ok(())
}

fn extract_env(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let text = get_text(source, Some(node));
    let line = node.start_position().row as u32 + 1;

    // Extract ENV variable names
    for var_name in extract_env_vars(&text) {
        let target = hash_id(&ctx.file_path, &var_name);
        ctx.add_edge(parent_id, &target, EdgeKind::EnvAccesses, line, Some(&var_name));
    }

    Ok(())
}

/// Extract image name from a FROM instruction (handles "FROM image:tag" and "FROM image:tag AS stage").
fn extract_image_name(text: &str) -> Option<String> {
    let rest = text.trim().strip_prefix("FROM")?.trim();
    let parts: Vec<&str> = rest.split_whitespace().collect();
    if parts.is_empty() {
        return None;
    }
    let image = parts[0];
    // Handle --platform=linux/amd64 prefix
    if image.starts_with("--") {
        if parts.len() > 1 {
            return Some(parts[1].to_string());
        }
        return None;
    }
    Some(image.to_string())
}

/// Extract stage alias from "AS name".
fn extract_stage_alias(text: &str) -> Option<String> {
    let upper = text.to_uppercase();
    if let Some(idx) = upper.find(" AS ") {
        let rest = &text[idx + 4..].trim();
        let alias: String = rest
            .split_whitespace()
            .next()
            .unwrap_or("")
            .to_string();
        if !alias.is_empty() {
            return Some(alias);
        }
    }
    None
}

/// Extract source file paths from COPY/ADD instructions.
fn extract_copy_sources(text: &str) -> Vec<String> {
    let mut sources = Vec::new();
    // Skip "COPY " or "ADD " prefix
    let rest = text.trim();
    let rest = if let Some(r) = rest.strip_prefix("COPY ") {
        r
    } else if let Some(r) = rest.strip_prefix("ADD ") {
        r
    } else {
        return sources;
    };

    let parts: Vec<&str> = rest.split_whitespace().collect();
    // All parts except the last are sources (last is destination)
    if parts.len() >= 2 {
        for part in &parts[..parts.len() - 1] {
            // Skip options like --chown=user:group
            if part.starts_with("--") {
                continue;
            }
            sources.push(part.to_string());
        }
    }
    sources
}

/// Extract ENV variable names.
fn extract_env_vars(text: &str) -> Vec<String> {
    let mut vars = Vec::new();
    let rest = text.trim();
    let rest = if let Some(r) = rest.strip_prefix("ENV ") {
        r
    } else {
        return vars;
    };

    let parts: Vec<&str> = rest.split_whitespace().collect();
    // ENV can have KEY=VALUE pairs or KEY VALUE pairs
    for part in &parts {
        if part.contains('=') {
            let key = part.split('=').next().unwrap_or("");
            if !key.is_empty() && key.chars().all(|c| c.is_uppercase() || c == '_' || c.is_ascii_digit()) {
                vars.push(key.to_string());
            }
        } else if part.chars().all(|c| c.is_uppercase() || c == '_' || c.is_ascii_digit()) {
            // KEY without = (the next part would be the value)
            vars.push(part.to_string());
        }
    }
    vars
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
            .set_language(&tree_sitter_containerfile::LANGUAGE.into())
            .expect("set dockerfile language");
        let tree = parser.parse(source, None).expect("parse dockerfile source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "dockerfile".to_string());
        DockerfileExtractor
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
    fn test_extract_simple_from() {
        let ctx = extract("FROM ubuntu:22.04\n", "Dockerfile");
        let stages = find_nodes(&ctx, NodeKind::DockerfileStage);
        assert_eq!(stages.len(), 1);
        assert_eq!(stages[0].name, "stage_1");

        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("ubuntu")), "Expected ubuntu in {:?}", targets);
    }

    #[test]
    fn test_extract_from_with_alias() {
        let ctx = extract("FROM node:18 AS builder\n", "Dockerfile");
        let stages = find_nodes(&ctx, NodeKind::DockerfileStage);
        assert_eq!(stages.len(), 1);
        assert_eq!(stages[0].name, "builder");

        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("node")), "Expected node in {:?}", targets);
    }

    #[test]
    fn test_extract_run_calls() {
        let ctx = extract("FROM ubuntu\nRUN apt-get update\n", "Dockerfile");
        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected calls edge for RUN");
        let targets: Vec<&str> = calls.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("apt-get")), "Expected apt-get in {:?}", targets);
    }

    #[test]
    fn test_extract_copy_imports() {
        let ctx = extract("FROM ubuntu\nCOPY package.json /app/\n", "Dockerfile");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("package.json")), "Expected package.json in {:?}", targets);
    }

    #[test]
    fn test_extract_env_access() {
        let ctx = extract("FROM ubuntu\nENV NODE_ENV=production\n", "Dockerfile");
        let envs = find_edges(&ctx, EdgeKind::EnvAccesses);
        assert!(!envs.is_empty(), "Expected ENV_ACCESSES edge for ENV");
    }

    #[test]
    fn test_extract_multi_stage() {
        let ctx = extract(
            "FROM node:18 AS builder\nRUN npm build\nFROM nginx:alpine\nCOPY --from=builder /app/dist /usr/share/nginx/html\n",
            "Dockerfile",
        );
        let stages = find_nodes(&ctx, NodeKind::DockerfileStage);
        assert_eq!(stages.len(), 2);
        let names: Vec<&str> = stages.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"builder"));
    }

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "Dockerfile");
        let files = find_nodes(&ctx, NodeKind::DockerfileImage);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_add_instruction() {
        let ctx = extract("FROM ubuntu\nADD archive.tar.gz /tmp/\n", "Dockerfile");
        let imports = find_edges(&ctx, EdgeKind::Imports);
        let targets: Vec<&str> = imports.iter().map(|e| e.target_text.as_deref().unwrap_or("")).collect();
        assert!(targets.iter().any(|t| t.contains("archive.tar.gz")), "Expected archive.tar.gz in {:?}", targets);
    }

    #[test]
    fn test_extract_complex_dockerfile() {
        let ctx = extract(
            "FROM python:3.11 AS base\nENV PYTHONUNBUFFERED=1\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . /app\n",
            "Dockerfile",
        );
        let stages = find_nodes(&ctx, NodeKind::DockerfileStage);
        assert_eq!(stages.len(), 1);
        assert_eq!(stages[0].name, "base");

        let calls = find_edges(&ctx, EdgeKind::Calls);
        assert!(!calls.is_empty(), "Expected calls edge");
    }
}
