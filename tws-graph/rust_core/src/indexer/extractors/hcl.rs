//! HCL (HashiCorp Configuration Language) / Terraform extractor.
//!
//! Extracts symbols from HCL/Terraform source files (`.hcl`, `.tf`, `.tfvars`)
//! using the tree-sitter-hcl grammar.
//!
//! # Node kinds produced
//! - `hcl_resource`: resource blocks (e.g., `resource "aws_instance" "example"`)
//! - `hcl_data`: data source blocks (e.g., `data "aws_ami" "ubuntu"`)
//! - `hcl_module`: module blocks
//! - `hcl_provider`: provider blocks
//! - `hcl_variable`: variable definitions
//! - `hcl_output`: output definitions
//! - `hcl_terraform`: terraform configuration block
//! - `hcl_locals`: locals block
//! - `hcl_backend`: backend configuration block
//! - `hcl_required_providers`: required_providers block
//! - `hcl_provisioner`: provisioner blocks
//!
//! # Edge kinds produced
//! - `contains`: containment (file → block)

use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct HclExtractor;

impl Extractor for HclExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["hcl", "tf", "tfvars"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["hcl"]
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
                .unwrap_or("file")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::File, &file_name, &root, HashMap::new());

        walk_hcl(source, root, ctx, &file_id)?;

        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking
// ---------------------------------------------------------------------------

fn walk_hcl(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "block" => extract_block(source, child, ctx, parent_id)?,
                "body" => walk_hcl(source, child, ctx, parent_id)?,
                _ => {
                    // Recurse into any structural nodes that might contain blocks
                    walk_hcl(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

/// Extract a block node — maps the block type (first identifier) to a NodeKind.
///
/// HCL block structure:
///   block → identifier [label1] [label2] body
///
/// Examples:
///   resource "aws_instance" "example" { ... }
///   data "aws_ami" "ubuntu" { ... }
///   module "vpc" { ... }
fn extract_block(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // First named child is usually the block type identifier
    let mut block_type = String::new();
    let mut labels: Vec<String> = Vec::new();

    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "identifier" | "string_lit" | "template_lit" => {
                    let text = get_text(source, Some(child));
                    if !text.is_empty() {
                        let cleaned = text.trim_matches('"').to_string();
                        if block_type.is_empty() {
                            block_type = cleaned;
                        } else {
                            labels.push(cleaned);
                        }
                    }
                }
                "body" => {
                    // Don't recurse labels from body
                }
                _ => {}
            }
        }
    }

    if block_type.is_empty() {
        return Ok(());
    }

    // Build full name from type + labels
    let full_name = if labels.is_empty() {
        block_type.clone()
    } else {
        format!("{}.{}", block_type, labels.join("."))
    };

    // Map block type to NodeKind
    let kind = match block_type.as_str() {
        "resource" => NodeKind::HclResource,
        "data" => NodeKind::HclData,
        "module" => NodeKind::HclModule,
        "provider" => NodeKind::HclProvider,
        "variable" => NodeKind::HclVariable,
        "output" => NodeKind::HclOutput,
        "terraform" => NodeKind::HclTerraform,
        "locals" => NodeKind::HclLocals,
        "backend" => NodeKind::HclBackend,
        "required_providers" => NodeKind::HclRequiredProviders,
        "provisioner" => NodeKind::HclProvisioner,
        _ => return Ok(()), // unknown block type, skip
    };

    let block_id = ctx.add_node(kind, &full_name, &node, HashMap::new());
    ctx.add_edge(parent_id, &block_id, EdgeKind::Contains, line, None);

    // Walk body for nested blocks (e.g., backend inside terraform, provisioner inside resource)
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "body" {
                walk_hcl(source, child, ctx, &block_id)?;
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Get the UTF-8 text of a node from the source bytes.
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
            .set_language(&tree_sitter_hcl::LANGUAGE.into())
            .expect("set hcl language");
        let tree = parser.parse(source, None).expect("parse hcl source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "hcl".to_string());
        HclExtractor
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
    // Resource extraction
    // ==================================================================

    #[test]
    fn test_extract_empty_file() {
        let ctx = extract("", "src/empty.tf");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_resource() {
        let ctx = extract(
            r#"resource "aws_instance" "example" {
  ami = "ami-123"
}
"#,
            "src/main.tf",
        );
        let resources = find_nodes(&ctx, NodeKind::HclResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "resource.aws_instance.example");
    }

    #[test]
    fn test_extract_multiple_resources() {
        let ctx = extract(
            r#"resource "aws_instance" "web" {
  ami = "ami-123"
}
resource "aws_instance" "db" {
  ami = "ami-456"
}
"#,
            "src/main.tf",
        );
        let resources = find_nodes(&ctx, NodeKind::HclResource);
        assert_eq!(resources.len(), 2);
        let names: Vec<&str> = resources.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"resource.aws_instance.web"));
        assert!(names.contains(&"resource.aws_instance.db"));
    }

    // ==================================================================
    // Data source extraction
    // ==================================================================

    #[test]
    fn test_extract_data_source() {
        let ctx = extract(
            r#"data "aws_ami" "ubuntu" {
  most_recent = true
}
"#,
            "src/data.tf",
        );
        let data = find_nodes(&ctx, NodeKind::HclData);
        assert_eq!(data.len(), 1);
        assert_eq!(data[0].name, "data.aws_ami.ubuntu");
    }

    // ==================================================================
    // Module extraction
    // ==================================================================

    #[test]
    fn test_extract_module() {
        let ctx = extract(
            r#"module "vpc" {
  source = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
}
"#,
            "src/modules.tf",
        );
        let modules = find_nodes(&ctx, NodeKind::HclModule);
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0].name, "module.vpc");
    }

    // ==================================================================
    // Provider extraction
    // ==================================================================

    #[test]
    fn test_extract_provider() {
        let ctx = extract(
            r#"provider "aws" {
  region = "us-west-2"
}
"#,
            "src/provider.tf",
        );
        let providers = find_nodes(&ctx, NodeKind::HclProvider);
        assert_eq!(providers.len(), 1);
        assert_eq!(providers[0].name, "provider.aws");
    }

    // ==================================================================
    // Variable extraction
    // ==================================================================

    #[test]
    fn test_extract_variable() {
        let ctx = extract(
            r#"variable "instance_type" {
  description = "EC2 instance type"
  default     = "t2.micro"
}
"#,
            "src/vars.tf",
        );
        let variables = find_nodes(&ctx, NodeKind::HclVariable);
        assert_eq!(variables.len(), 1);
        assert_eq!(variables[0].name, "variable.instance_type");
    }

    // ==================================================================
    // Output extraction
    // ==================================================================

    #[test]
    fn test_extract_output() {
        let ctx = extract(
            r#"output "instance_ip" {
  value = aws_instance.example.public_ip
}
"#,
            "src/outputs.tf",
        );
        let outputs = find_nodes(&ctx, NodeKind::HclOutput);
        assert_eq!(outputs.len(), 1);
        assert_eq!(outputs[0].name, "output.instance_ip");
    }

    // ==================================================================
    // Terraform block extraction
    // ==================================================================

    #[test]
    fn test_extract_terraform_block() {
        let ctx = extract(
            r#"terraform {
  required_version = ">= 1.0"
  backend "s3" {
    bucket = "my-state"
  }
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}
"#,
            "src/terraform.tf",
        );
        let tf_blocks = find_nodes(&ctx, NodeKind::HclTerraform);
        assert_eq!(tf_blocks.len(), 1);
        assert_eq!(tf_blocks[0].name, "terraform");

        // Nested backend block should also be extracted
        let backends = find_nodes(&ctx, NodeKind::HclBackend);
        assert_eq!(backends.len(), 1);
        assert_eq!(backends[0].name, "backend.s3");

        // Nested required_providers
        let req_providers = find_nodes(&ctx, NodeKind::HclRequiredProviders);
        assert_eq!(req_providers.len(), 1);
        assert_eq!(req_providers[0].name, "required_providers");
    }

    // ==================================================================
    // Locals extraction
    // ==================================================================

    #[test]
    fn test_extract_locals() {
        let ctx = extract(
            r#"locals {
  name = "myapp"
  env  = var.environment
}
"#,
            "src/locals.tf",
        );
        let locals = find_nodes(&ctx, NodeKind::HclLocals);
        assert_eq!(locals.len(), 1);
        assert_eq!(locals[0].name, "locals");
    }

    // ==================================================================
    // Provisioner extraction
    // ==================================================================

    #[test]
    fn test_extract_provisioner() {
        let ctx = extract(
            r#"resource "aws_instance" "web" {
  ami = "ami-123"

  provisioner "local-exec" {
    command = "echo Hello"
  }
}
"#,
            "src/main.tf",
        );
        let provisioners = find_nodes(&ctx, NodeKind::HclProvisioner);
        assert_eq!(provisioners.len(), 1);
        assert_eq!(provisioners[0].name, "provisioner.local-exec");
    }

    // ==================================================================
    // Complex file
    // ==================================================================

    #[test]
    fn test_extract_complex_file() {
        let ctx = extract(
            r#"terraform {
  required_version = ">= 1.0"
}

provider "aws" {
  region = "us-east-1"
}

module "networking" {
  source = "./modules/networking"
}

resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
}
"#,
            "src/main.tf",
        );
        let tf = find_nodes(&ctx, NodeKind::HclTerraform);
        assert_eq!(tf.len(), 1);

        let providers = find_nodes(&ctx, NodeKind::HclProvider);
        assert_eq!(providers.len(), 1);

        let modules = find_nodes(&ctx, NodeKind::HclModule);
        assert_eq!(modules.len(), 1);

        let resources = find_nodes(&ctx, NodeKind::HclResource);
        assert_eq!(resources.len(), 1);

        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(contains.len() >= 4, "Expected >=4 CONTAINS edges, got {}", contains.len());
    }

    // ==================================================================
    // Edge cases
    // ==================================================================

    #[test]
    fn test_no_crash_on_unrecognized_blocks() {
        let ctx = extract(
            r#"custom_block "foo" "bar" {
  key = "value"
}
"#,
            "src/custom.tf",
        );
        // Unrecognized block types should not cause errors
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_resource_without_labels() {
        let ctx = extract(
            r#"resource {
  name = "anonymous"
}
"#,
            "src/anon.tf",
        );
        // Resource without labels should still produce a node
        let resources = find_nodes(&ctx, NodeKind::HclResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "resource");
    }
}
