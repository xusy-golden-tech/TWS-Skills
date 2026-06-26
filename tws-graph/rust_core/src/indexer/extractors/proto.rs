//! Protobuf language extractor.
//!
//! Extracts symbols and relationships from Protocol Buffer source files
//! (`.proto`) using the tree-sitter-proto grammar.
//!
//! # Node kinds produced
//! - `proto_file`: file-level node
//! - `service`: service definition
//! - `rpc_method`: RPC method definition
//! - `message` (as `class`): message definition
//! - `enum`: enum definition
//! - `enum_member`: enum value definition
//!
//! # Edge kinds produced
//! - `contains`: containment
//! - `imports`: import statements
//! - `calls`: (not applicable, but rpc methods use request/response types)

use crate::db::hash_id;
use crate::indexer::context::ExtractionContext;
use crate::traits::{EdgeKind, Extractor, NodeKind};
use std::collections::HashMap;
use tree_sitter::Node;
use tree_sitter::Tree;

pub struct ProtoExtractor;

impl Extractor for ProtoExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["proto"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["proto"]
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
                .unwrap_or("module")
                .to_string()
        };
        let file_id = ctx.add_node(NodeKind::ProtoFile, &file_name, &root, HashMap::new());

        walk_node(source, root, ctx, &file_id)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Tree walking
// ---------------------------------------------------------------------------

fn walk_node(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    match node.kind() {
        "message" => extract_message(source, node, ctx, parent_id)?,
        "service" => extract_service(source, node, ctx, parent_id)?,
        "enum" => extract_enum(source, node, ctx, parent_id)?,
        "import" => extract_import(source, node, ctx, parent_id)?,
        _ => {
            for i in 0..node.named_child_count() {
                if let Some(child) = node.named_child(i) {
                    walk_node(source, child, ctx, parent_id)?;
                }
            }
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Message extraction
// ---------------------------------------------------------------------------

fn extract_message(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;
    // Message name is under message_name → identifier
    let name = get_name_child_text(source, node, "message_name");
    if name.is_empty() {
        return Ok(());
    }

    let msg_id = ctx.add_node(NodeKind::Class, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &msg_id, EdgeKind::Contains, line, None);

    // Walk body for nested messages, enums, etc.
    walk_all_children(source, node, ctx, &msg_id)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Enum extraction
// ---------------------------------------------------------------------------

fn extract_enum(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;
    // Enum name is under enum_name → identifier
    let name = get_name_child_text(source, node, "enum_name");
    if name.is_empty() {
        return Ok(());
    }

    let enum_id = ctx.add_node(NodeKind::Enum, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &enum_id, EdgeKind::Contains, line, None);

    // Extract enum members (values) — they are `enum_field` children inside `enum_body`
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "enum_body" {
                for j in 0..child.named_child_count() {
                    if let Some(member) = child.named_child(j) {
                        if member.kind() == "enum_field" {
                            let member_line = member.start_position().row as u32 + 1;
                            // enum_field has identifier as direct child
                            let mname = get_child_text(source, member, "identifier");
                            if !mname.is_empty() {
                                let member_id = ctx.add_node(NodeKind::EnumMember, &mname, &member, HashMap::new());
                                ctx.add_edge(&enum_id, &member_id, EdgeKind::Contains, member_line, None);
                            }
                        }
                    }
                }
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Service extraction
// ---------------------------------------------------------------------------

fn extract_service(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;
    // Service name is under service_name → identifier
    let name = get_name_child_text(source, node, "service_name");
    if name.is_empty() {
        return Ok(());
    }

    let svc_id = ctx.add_node(NodeKind::Service, &name, &node, HashMap::new());
    ctx.add_edge(parent_id, &svc_id, EdgeKind::Contains, line, None);

    // Extract RPC methods from service body
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "rpc" {
                let rpc_line = child.start_position().row as u32 + 1;
                // RPC name is under rpc_name → identifier
                let rpc_name = get_name_child_text(source, child, "rpc_name");
                if !rpc_name.is_empty() {
                    let rpc_id = ctx.add_node(NodeKind::RpcMethod, &rpc_name, &child, HashMap::new());
                    ctx.add_edge(&svc_id, &rpc_id, EdgeKind::Contains, rpc_line, None);
                }
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Import extraction
// ---------------------------------------------------------------------------

fn extract_import(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    let line = node.start_position().row as u32 + 1;

    // Import path is typically a string literal child
    let import_path = get_child_text(source, node, "string");
    if import_path.is_empty() {
        return Ok(());
    }

    let path = import_path.trim_matches('"').to_string();
    let target_qn = format!("{}::{}", ctx.file_path, path);
    let target = hash_id(&ctx.file_path, &target_qn);
    ctx.add_edge(parent_id, &target, EdgeKind::Imports, line, Some(&path));

    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn walk_all_children(
    source: &[u8],
    node: Node,
    ctx: &mut ExtractionContext,
    parent_id: &str,
) -> anyhow::Result<()> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            walk_node(source, child, ctx, parent_id)?;
        }
    }
    Ok(())
}

/// Get the name from a `*_name` child (e.g., message_name, service_name, enum_name).
/// These nodes wrap an `identifier` child whose text is the actual name.
fn get_name_child_text(source: &[u8], node: Node, name_kind: &str) -> String {
    if let Some(name_node) = find_child_by_kind(node, name_kind) {
        // First try to find a direct identifier child
        if let Some(id_node) = find_child_by_kind(name_node, "identifier") {
            if let Ok(text) = id_node.utf8_text(source) {
                return text.to_string();
            }
        }
        // Fallback: use the name node's own text
        if let Ok(text) = name_node.utf8_text(source) {
            return text.to_string();
        }
    }
    String::new()
}

fn find_child_by_kind<'a>(node: Node<'a>, kind: &str) -> Option<Node<'a>> {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                return Some(child);
            }
        }
    }
    None
}

fn get_child_text(source: &[u8], node: Node, kind: &str) -> String {
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if child.kind() == kind {
                if let Ok(text) = child.utf8_text(source) {
                    return text.to_string();
                }
            }
        }
    }
    String::new()
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
        let language: tree_sitter::Language = tree_sitter_proto::LANGUAGE.into();
        parser.set_language(&language).expect("set proto language");
        let tree = parser.parse(source, None).expect("parse proto source");

        let mut ctx = ExtractionContext::new(file_path.to_string(), "proto".to_string());
        ProtoExtractor
            .extract(source.as_bytes(), &tree, &mut ctx)
            .expect("extract should succeed");
        ctx
    }

    fn find_nodes(ctx: &ExtractionContext, kind: NodeKind) -> Vec<&crate::db::models::NodeRecord> {
        let kind_str = crate::indexer::context::node_kind_to_str(kind);
        ctx.result
            .nodes
            .iter()
            .filter(|n| n.kind == kind_str)
            .collect()
    }

    fn find_edges(ctx: &ExtractionContext, kind: EdgeKind) -> Vec<&crate::db::models::EdgeRecord> {
        let kind_str = kind.as_str();
        ctx.result
            .edges
            .iter()
            .filter(|e| e.kind == kind_str)
            .collect()
    }

    // ------------------------------------------------------------------
    // 1. Empty file
    // ------------------------------------------------------------------

    #[test]
    fn test_empty_file() {
        let ctx = extract(r#"syntax = "proto3";"#, "empty.proto");
        let files = find_nodes(&ctx, NodeKind::ProtoFile);
        assert_eq!(files.len(), 1);
    }

    // ------------------------------------------------------------------
    // 2. Message definition
    // ------------------------------------------------------------------

    #[test]
    fn test_message_definition() {
        let ctx = extract(
            r#"syntax = "proto3";
message User {
  string name = 1;
  int32 age = 2;
}
"#,
            "user.proto",
        );
        let msgs = find_nodes(&ctx, NodeKind::Class);
        assert!(!msgs.is_empty(), "Expected at least 1 message");
        let names: Vec<_> = msgs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"User"));
    }

    // ------------------------------------------------------------------
    // 3. Multiple messages
    // ------------------------------------------------------------------

    #[test]
    fn test_multiple_messages() {
        let ctx = extract(
            r#"syntax = "proto3";
message Foo { string a = 1; }
message Bar { int32 b = 1; }
message Baz { bool c = 1; }
"#,
            "test.proto",
        );
        let msgs = find_nodes(&ctx, NodeKind::Class);
        assert_eq!(msgs.len(), 3, "Expected 3 messages");
    }

    // ------------------------------------------------------------------
    // 4. Nested message
    // ------------------------------------------------------------------

    #[test]
    fn test_nested_message() {
        let ctx = extract(
            r#"syntax = "proto3";
message Outer {
  message Inner { string val = 1; }
}
"#,
            "test.proto",
        );
        let msgs = find_nodes(&ctx, NodeKind::Class);
        assert!(msgs.len() >= 2, "Expected >= 2 messages, got {}", msgs.len());
        let names: Vec<_> = msgs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"Outer"));
        assert!(names.contains(&"Inner"));
    }

    // ------------------------------------------------------------------
    // 5. Service definition
    // ------------------------------------------------------------------

    #[test]
    fn test_service_definition() {
        let ctx = extract(
            r#"syntax = "proto3";
service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
}
"#,
            "test.proto",
        );
        let svcs = find_nodes(&ctx, NodeKind::Service);
        assert!(!svcs.is_empty(), "Expected at least 1 service");
        assert!(svcs.iter().any(|s| s.name == "UserService"));
    }

    // ------------------------------------------------------------------
    // 6. RPC method definition
    // ------------------------------------------------------------------

    #[test]
    fn test_rpc_method() {
        let ctx = extract(
            r#"syntax = "proto3";
service Greeter {
  rpc SayHello(HelloRequest) returns (HelloReply);
  rpc SayGoodbye(GoodbyeRequest) returns (GoodbyeReply);
}
"#,
            "test.proto",
        );
        let rpcs = find_nodes(&ctx, NodeKind::RpcMethod);
        assert_eq!(rpcs.len(), 2, "Expected 2 RPC methods");
        let names: Vec<_> = rpcs.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"SayHello"));
        assert!(names.contains(&"SayGoodbye"));
    }

    // ------------------------------------------------------------------
    // 7. Enum definition
    // ------------------------------------------------------------------

    #[test]
    fn test_enum_definition() {
        let ctx = extract(
            r#"syntax = "proto3";
enum Color {
  RED = 0;
  GREEN = 1;
  BLUE = 2;
}
"#,
            "test.proto",
        );
        let enums = find_nodes(&ctx, NodeKind::Enum);
        assert!(!enums.is_empty(), "Expected at least 1 enum");
        assert!(enums.iter().any(|e| e.name == "Color"));
    }

    // ------------------------------------------------------------------
    // 8. Enum values
    // ------------------------------------------------------------------

    #[test]
    fn test_enum_values() {
        let ctx = extract(
            r#"syntax = "proto3";
enum Status {
  UNKNOWN = 0;
  ACTIVE = 1;
  INACTIVE = 2;
}
"#,
            "test.proto",
        );
        let members = find_nodes(&ctx, NodeKind::EnumMember);
        assert_eq!(members.len(), 3, "Expected 3 enum members");
        let names: Vec<_> = members.iter().map(|n| n.name.as_str()).collect();
        assert!(names.contains(&"UNKNOWN"));
        assert!(names.contains(&"ACTIVE"));
        assert!(names.contains(&"INACTIVE"));
    }

    // ------------------------------------------------------------------
    // 9. Import statements
    // ------------------------------------------------------------------

    #[test]
    fn test_import() {
        let ctx = extract(
            r#"syntax = "proto3";
import "common.proto";
import "google/protobuf/timestamp.proto";
"#,
            "test.proto",
        );
        let imports = find_edges(&ctx, EdgeKind::Imports);
        assert!(imports.len() >= 2, "Expected >= 2 import edges, got {}", imports.len());
    }

    // ------------------------------------------------------------------
    // 10. Contains edges
    // ------------------------------------------------------------------

    #[test]
    fn test_contains_edges() {
        let ctx = extract(
            r#"syntax = "proto3";
message Person {
  string name = 1;
}
"#,
            "test.proto",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        assert!(!contains.is_empty(), "Expected CONTAINS edges");
    }

    // ------------------------------------------------------------------
    // 11. Service contains RPC
    // ------------------------------------------------------------------

    #[test]
    fn test_service_contains_rpc() {
        let ctx = extract(
            r#"syntax = "proto3";
service Echo {
  rpc Ping(PingReq) returns (PingResp);
}
"#,
            "test.proto",
        );
        let contains = find_edges(&ctx, EdgeKind::Contains);
        // File -> Service, Service -> RpcMethod
        assert!(contains.len() >= 2, "Expected >= 2 CONTAINS edges, got {}", contains.len());
    }

    // ------------------------------------------------------------------
    // 12. File node exists
    // ------------------------------------------------------------------

    #[test]
    fn test_file_node_present() {
        let ctx = extract(
            r#"syntax = "proto3";
message Simple {}
"#,
            "simple.proto",
        );
        let files = find_nodes(&ctx, NodeKind::ProtoFile);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].name, "simple.proto");
    }
}
