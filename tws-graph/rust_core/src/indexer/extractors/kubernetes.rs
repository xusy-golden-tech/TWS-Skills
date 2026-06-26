//! Kubernetes manifest extractor.
//!
//! Extracts symbols from Kubernetes YAML manifest files (`.yaml`, `.yml`).
//! Since Kubernetes manifests are YAML-based, this extractor uses tree-sitter-yaml
//! to parse the file, then scans for objects that have `apiVersion` and `kind` fields.
//!
//! # Node kinds produced
//! - `k8s_resource`: Kubernetes resource objects (Deployment, Service, ConfigMap,
//!   Secret, Ingress, Pod, StatefulSet, DaemonSet, Job, CronJob, Namespace, etc.)
//!
//! # Edge kinds produced
//! - `contains`: containment (file -> k8s_resource)

use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct KubernetesExtractor;

impl Extractor for KubernetesExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["yaml", "yml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["kubernetes"]
    }
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()> {
        let root = tree.root_node();

        let file_id = ctx.add_node(NodeKind::File, "k8s-manifest", &root, HashMap::new());

        walk_k8s(source, root, ctx, &file_id)?;

        Ok(())
    }
}

/// Known Kubernetes resource kinds that we recognize.
const K8S_RESOURCE_KINDS: &[&str] = &[
    "Deployment",
    "Service",
    "ConfigMap",
    "Secret",
    "Ingress",
    "Pod",
    "StatefulSet",
    "DaemonSet",
    "Job",
    "CronJob",
    "Namespace",
    "PersistentVolume",
    "PersistentVolumeClaim",
    "ServiceAccount",
    "ClusterRole",
    "ClusterRoleBinding",
    "Role",
    "RoleBinding",
    "ReplicaSet",
    "ReplicationController",
    "NetworkPolicy",
    "HorizontalPodAutoscaler",
    "LimitRange",
    "ResourceQuota",
    "Endpoints",
    "EndpointSlice",
    "StorageClass",
    "CustomResourceDefinition",
    "MutatingWebhookConfiguration",
    "ValidatingWebhookConfiguration",
    "APIService",
    "PriorityClass",
    "PodDisruptionBudget",
    "VolumeAttachment",
    "CSIDriver",
    "CSINode",
    "CSIStorageCapacity",
];

fn walk_k8s(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "block_mapping" | "flow_mapping" => {
                    // Check if this mapping represents a K8s resource
                    if let Some(resource_info) = try_extract_k8s_resource(source, child) {
                        extract_k8s_resource(source, child, ctx, parent_id, &resource_info)?;
                    } else {
                        // Not a K8s resource — recurse to find nested resources
                        walk_k8s(source, child, ctx, parent_id)?;
                    }
                }
                "block_mapping_pair" | "flow_pair" => {
                    // Recurse into value for nested resources
                    if node.named_child_count() >= 2 {
                        if let Some(value) = child.named_child(child.named_child_count() - 1) {
                            walk_k8s(source, value, ctx, parent_id)?;
                        }
                    }
                }
                "document" | "block_node" | "flow_node" => {
                    walk_k8s(source, child, ctx, parent_id)?;
                }
                "block_sequence" | "flow_sequence" => {
                    // Could be a list of resources
                    walk_k8s(source, child, ctx, parent_id)?;
                }
                _ => {
                    walk_k8s(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

/// Resource info extracted from a mapping node.
struct K8sResourceInfo {
    kind: String,
    name: String,
}

/// Try to extract apiVersion and kind from a mapping node to determine if it's a K8s resource.
/// Recursively collects all key-value pairs from a YAML mapping, then checks for K8s signature.
fn try_extract_k8s_resource(source: &[u8], node: Node) -> Option<K8sResourceInfo> {
    // Collect all key→value mappings from this node and its nested children
    let mut map: HashMap<String, String> = HashMap::new();
    collect_yaml_map(source, node, &mut map, &Vec::new());

    let kind = map.get("kind")?.clone();
    let name = map
        .get("metadata.name")
        .cloned()
        .unwrap_or_else(|| "unnamed".to_string());

    if K8S_RESOURCE_KINDS.contains(&kind.as_str()) {
        Some(K8sResourceInfo { kind, name })
    } else {
        None
    }
}

/// Recursively collect all key→value pairs from a YAML mapping into a flat map.
/// Nested keys are stored as "parent_key.child_key".
fn collect_yaml_map(
    source: &[u8],
    node: Node,
    map: &mut HashMap<String, String>,
    prefix: &[String],
) {
    match node.kind() {
        "block_mapping_pair" | "flow_pair" => {
            let key = get_pair_key(source, node);
            if key.is_empty() {
                return;
            }

            let mut full_key = prefix.to_vec();
            full_key.push(key.clone());
            let key_str = full_key.join(".");

            // Get the value — if it's a scalar, store it; if it's a mapping, recurse
            if node.named_child_count() >= 2 {
                let value_node = node.named_child(node.named_child_count() - 1).unwrap();
                match value_node.kind() {
                    "flow_node" | "block_node" => {
                        // Check if value contains a scalar or a mapping
                        for i in 0..value_node.named_child_count() {
                            if let Some(vc) = value_node.named_child(i) {
                                match vc.kind() {
                                    "block_mapping" | "flow_mapping" => {
                                        collect_yaml_map(source, vc, map, &full_key);
                                    }
                                    "plain_scalar" | "double_quote_scalar" | "single_quote_scalar" | "integer_scalar" | "boolean_scalar" | "null_scalar" | "string_scalar" => {
                                        let val = extract_scalar_text(source, vc);
                                        map.insert(key_str.clone(), val);
                                    }
                                    "block_sequence" | "flow_sequence" => {
                                        // Don't recurse into lists
                                    }
                                    _ => {
                                        collect_yaml_map(source, vc, map, &full_key);
                                    }
                                }
                            }
                        }
                    }
                    "block_mapping" | "flow_mapping" => {
                        collect_yaml_map(source, value_node, map, &full_key);
                    }
                    "plain_scalar" | "double_quote_scalar" | "single_quote_scalar" | "integer_scalar" | "boolean_scalar" | "null_scalar" | "string_scalar" => {
                        let val = extract_scalar_text(source, value_node);
                        map.insert(key_str, val);
                    }
                    _ => {
                        collect_yaml_map(source, value_node, map, &full_key);
                    }
                }
            }
        }
        "block_mapping" | "flow_mapping" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    collect_yaml_map(source, child, map, prefix);
                }
            }
        }
        "block_node" | "flow_node" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    collect_yaml_map(source, child, map, prefix);
                }
            }
        }
        "document" => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    collect_yaml_map(source, child, map, prefix);
                }
            }
        }
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    collect_yaml_map(source, child, map, prefix);
                }
            }
        }
    }
}

/// Extract a K8s resource node from a mapping that we've identified as a K8s resource.
fn extract_k8s_resource(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
    info: &K8sResourceInfo,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;
    let full_name = format!("{}.{}", info.kind, info.name);

    let resource_id = ctx.add_node(
        NodeKind::K8sResource,
        &full_name,
        &node,
        HashMap::new(),
    );
    ctx.add_edge(parent_id, &resource_id, EdgeKind::Contains, line, None);

    Ok(())
}

/// Get the key text from a mapping pair.
fn get_pair_key(source: &[u8], node: Node) -> String {
    if let Some(key_node) = node.named_child(0) {
        extract_scalar_text(source, key_node)
    } else {
        String::new()
    }
}

/// Extract scalar text from a YAML node.
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

        let mut ctx = ExtractionContext::new(file_path.to_string(), "kubernetes".to_string());
        KubernetesExtractor
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
        let ctx = extract("", "deploy.yaml");
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn test_extract_deployment() {
        let ctx = extract(
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: myapp\nspec:\n  replicas: 3\n",
            "deploy.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Deployment.myapp");
    }

    #[test]
    fn test_extract_service() {
        let ctx = extract(
            "apiVersion: v1\nkind: Service\nmetadata:\n  name: myapp-svc\nspec:\n  ports:\n    - port: 80\n",
            "svc.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Service.myapp-svc");
    }

    #[test]
    fn test_extract_configmap() {
        let ctx = extract(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\ndata:\n  key: value\n",
            "cm.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "ConfigMap.app-config");
    }

    #[test]
    fn test_extract_secret() {
        let ctx = extract(
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: db-credentials\ntype: Opaque\n",
            "secret.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Secret.db-credentials");
    }

    #[test]
    fn test_extract_ingress() {
        let ctx = extract(
            "apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: main-ingress\nspec:\n  rules: []\n",
            "ingress.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Ingress.main-ingress");
    }

    #[test]
    fn test_extract_statefulset() {
        let ctx = extract(
            "apiVersion: apps/v1\nkind: StatefulSet\nmetadata:\n  name: db\nspec:\n  serviceName: db\n",
            "sts.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "StatefulSet.db");
    }

    #[test]
    fn test_extract_daemonset() {
        let ctx = extract(
            "apiVersion: apps/v1\nkind: DaemonSet\nmetadata:\n  name: log-agent\n",
            "ds.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "DaemonSet.log-agent");
    }

    #[test]
    fn test_extract_job() {
        let ctx = extract(
            "apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: data-migration\n",
            "job.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Job.data-migration");
    }

    #[test]
    fn test_extract_resource_without_name() {
        let ctx = extract(
            "apiVersion: v1\nkind: Namespace\n",
            "ns.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "Namespace.unnamed");
    }

    #[test]
    fn test_extract_multiple_resources_in_file() {
        let ctx = extract(
            "---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n---\napiVersion: v1\nkind: Service\nmetadata:\n  name: web-svc\n",
            "all.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 2);
        let names: Vec<&str> = resources.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Deployment.web"));
        assert!(names.contains(&"Service.web-svc"));
    }

    #[test]
    fn test_no_crash_on_non_k8s_yaml() {
        let ctx = extract(
            "app:\n  name: myapp\n  version: \"1.0\"\n",
            "config.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 0);
        let files = find_nodes(&ctx, NodeKind::File);
        assert_eq!(files.len(), 1);
    }

    #[test]
    fn debug_k8s_structure() {
        let source = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: myapp\nspec:\n  replicas: 3\n";
        let mut parser = Parser::new();
        parser
            .set_language(&tree_sitter_yaml::LANGUAGE.into())
            .expect("set yaml language");
        let tree = parser.parse(source, None).expect("parse yaml source");
        let root = tree.root_node();

        fn print_node(source: &[u8], node: Node, indent: usize) {
            let prefix = "  ".repeat(indent);
            let text = node.utf8_text(source).unwrap_or("").lines().next().unwrap_or("").to_string();
            let text_short = if text.len() > 60 { format!("{}...", &text[..60]) } else { text };
            println!("{}{}: kind={:30} text={}", prefix, node.id(), node.kind(), text_short);
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    print_node(source, child, indent + 1);
                }
            }
        }
        println!("Root kind: {}", root.kind());
        print_node(source.as_bytes(), root, 0);

        let ctx = extract(source, "deploy.yaml");
        println!("\n=== EXTRACTED NODES ===");
        for n in &ctx.result.nodes {
            println!("  kind={:25} name={}", n.kind, n.name);
        }
    }

    #[test]
    fn test_extract_cronjob() {
        let ctx = extract(
            "apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: cleanup\nspec:\n  schedule: \"0 0 * * *\"\n",
            "cronjob.yaml",
        );
        let resources = find_nodes(&ctx, NodeKind::K8sResource);
        assert_eq!(resources.len(), 1);
        assert_eq!(resources[0].name, "CronJob.cleanup");
    }
}
