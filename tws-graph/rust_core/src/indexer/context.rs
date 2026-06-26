//! Extraction context — accumulates nodes and edges during a parse run.
//!
//! The context is passed through each extractor invocation. It collects
//! [`NodeRecord`]s and [`EdgeRecord`]s, manages a scope stack for building
//! qualified names, and provides helpers for adding nodes and edges.

use crate::db::models::{EdgeRecord, NodeRecord};
use crate::db::hash_id;
use crate::traits::{EdgeKind, NodeKind};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

// ---------------------------------------------------------------------------
// ExtractionResult
// ---------------------------------------------------------------------------

/// Accumulated output of an extraction pass over a single file.
#[derive(Debug, Clone, Default)]
pub struct ExtractionResult {
    /// Symbol nodes discovered in the file.
    pub nodes: Vec<NodeRecord>,
    /// Relationship edges discovered in the file.
    pub edges: Vec<EdgeRecord>,
}

impl ExtractionResult {
    /// Create an empty result.
    pub fn new() -> Self {
        Self::default()
    }
}

// ---------------------------------------------------------------------------
// ScopeInfo
// ---------------------------------------------------------------------------

/// Information about a lexical scope (class, function, module, etc.).
#[derive(Debug, Clone)]
pub struct ScopeInfo {
    /// Name of the scope (e.g. `"MyClass"`, `"calculate"`).
    pub name: String,
    /// Kind of the scope (e.g. `"class"`, `"function"`, `"method"`).
    pub kind: String,
}

// ---------------------------------------------------------------------------
// ExtractionContext
// ---------------------------------------------------------------------------

/// Mutable context passed through each extractor invocation.
///
/// Collects [`NodeRecord`]s and [`EdgeRecord`]s as the extractor walks
/// a tree-sitter CST.  Manages a scope stack for building fully-qualified
/// symbol names.
pub struct ExtractionContext {
    /// Project-relative path of the file being indexed.
    pub file_path: String,

    /// Detected language (e.g. `"python"`).
    pub language: String,

    /// Accumulated extraction output (nodes + edges).
    pub result: ExtractionResult,

    /// Stack of scope names for building qualified symbol names.
    /// E.g. `["MyClass", "my_method"]` produces
    /// `file_path::MyClass.my_method.inner_name`.
    name_stack: Vec<String>,

    /// Stack of node IDs corresponding to enclosing scopes.
    /// E.g. when inside a class, the top is the class node's hash ID.
    node_stack: Vec<String>,

    /// Full scope information (name + kind) for each enclosing scope.
    scope_stack: Vec<ScopeInfo>,
}

impl ExtractionContext {
    /// Create a new extraction context for the given file.
    pub fn new(file_path: String, language: String) -> Self {
        Self {
            file_path,
            language,
            result: ExtractionResult::new(),
            name_stack: Vec::new(),
            node_stack: Vec::new(),
            scope_stack: Vec::new(),
        }
    }

    // ------------------------------------------------------------------
    // Qualified name construction
    // ------------------------------------------------------------------

    /// Build a fully-qualified name for a symbol based on the current
    /// scope stack.
    ///
    /// Format: `"{file_path}::{[name_stack].}{name}"`
    ///
    /// # Examples
    ///
    /// ```
    /// // At module level:
    /// //   file_path="src/lib.rs", name="my_func"
    /// //   → "src/lib.rs::my_func"
    /// //
    /// // Inside a class:
    /// //   file_path="src/lib.rs", name_stack=["MyStruct"], name="my_method"
    /// //   → "src/lib.rs::MyStruct.my_method"
    /// ```
    pub fn make_qualified(&self, name: &str) -> String {
        if self.name_stack.is_empty() {
            format!("{}::{}", self.file_path, name)
        } else {
            format!("{}::{}.{}", self.file_path, self.name_stack.join("."), name)
        }
    }

    // ------------------------------------------------------------------
    // Node & edge accumulation
    // ------------------------------------------------------------------

    /// Add a node (symbol) to the extraction result.
    ///
    /// Generates a deterministic hash ID from the file path and qualified
    /// name, constructs a [`NodeRecord`], appends it to the result, and
    /// returns the hash ID.
    ///
    /// The `node` parameter provides line/column information from the CST.
    /// `extra` is an optional map of extension properties (serialized to JSON).
    pub fn add_node(
        &mut self,
        kind: NodeKind,
        name: &str,
        node: &tree_sitter::Node,
        mut extra: HashMap<String, String>,
    ) -> String {
        let qualified_name = self.make_qualified(name);
        let id = hash_id(&self.file_path, &qualified_name);
        let start_pos = node.start_position();
        let end_pos = node.end_position();

        // Extract known fields from extra before serialising the rest
        let signature = extra.remove("signature");
        let decorators_json = extra.remove("decorators");
        let is_abstract_val = extra
            .remove("is_abstract")
            .map(|v| v == "true")
            .unwrap_or(false);

        let properties_json = if extra.is_empty() {
            None
        } else {
            serde_json::to_string(&extra).ok()
        };

        let record = NodeRecord {
            id: id.clone(),
            kind: node_kind_to_str(kind).to_string(),
            name: name.to_string(),
            qualified_name,
            file_path: self.file_path.clone(),
            language: self.language.clone(),
            start_line: (start_pos.row + 1) as i64,
            end_line: (end_pos.row + 1) as i64,
            signature,
            docstring: None,
            visibility: None,
            is_abstract: if is_abstract_val { 1 } else { 0 },
            is_exported: 0,
            decorators: decorators_json,
            framework: None,
            properties: properties_json,
            body: None,
            body_hash: None,
            updated_at: now_ms(),
        };

        self.result.nodes.push(record);
        id
    }

    /// Add an edge (relationship) between two nodes.
    ///
    /// `source` and `target` are node hash IDs.  `target_text` is the
    /// unhashed qualified name used to compute `target` (for post-processing
    /// resolution).  `line` is the 1-based line number where the relationship
    /// occurs.
    pub fn add_edge(
        &mut self,
        source: &str,
        target: &str,
        kind: EdgeKind,
        line: u32,
        target_text: Option<&str>,
    ) {
        let source_loc = Some(format!("{}:{}:0", self.file_path, line));

        let record = EdgeRecord {
            id: None,
            source: source.to_string(),
            target: target.to_string(),
            target_text: target_text.map(|s| s.to_string()),
            kind: kind.as_str().to_string(),
            source_loc,
            provenance: Some("tree-sitter".to_string()),
            properties: Some("{}".to_string()),
        };

        self.result.edges.push(record);
    }

    // ------------------------------------------------------------------
    // Scope management
    // ------------------------------------------------------------------

    /// Push a named scope onto all three scope stacks.
    ///
    /// After calling this, [`make_qualified`] will include `name` in the
    /// qualified name chain.
    pub fn push_scope(&mut self, name: &str) {
        self.name_stack.push(name.to_string());
        self.scope_stack.push(ScopeInfo {
            name: name.to_string(),
            kind: "scope".to_string(),
        });
    }

    /// Push a named scope with an explicit kind (e.g. `"class"`, `"function"`).
    pub fn push_scope_with_kind(&mut self, name: &str, kind: &str) {
        self.name_stack.push(name.to_string());
        self.scope_stack.push(ScopeInfo {
            name: name.to_string(),
            kind: kind.to_string(),
        });
    }

    /// Pop the innermost scope from all three stacks.
    ///
    /// Returns `true` if a scope was actually popped.
    pub fn pop_scope(&mut self) -> bool {
        let had_name = self.name_stack.pop().is_some();
        self.scope_stack.pop();
        // Also pop node_stack if it exists at this depth
        self.node_stack.pop();
        had_name
    }

    /// Push a node ID onto the node stack (for tracking enclosing scope nodes).
    pub fn push_scope_node(&mut self, node_id: &str) {
        self.node_stack.push(node_id.to_string());
    }

    /// Return the node ID of the current innermost scope, if any.
    pub fn current_scope_node_id(&self) -> Option<&str> {
        self.node_stack.last().map(|s| s.as_str())
    }

    /// Return the depth of the scope stack.
    pub fn scope_depth(&self) -> usize {
        self.name_stack.len()
    }

    /// Execute a closure within a temporary named scope.
    ///
    /// The scope is pushed before `f` runs and popped after it returns
    /// (even if it panics, via `Drop`-style cleanup in practice — note
    /// that [`std::panic::catch_unwind`] is required for true RAII in
    /// the presence of panics, which this method does **not** provide).
    pub fn with_scope<F: FnOnce(&mut Self)>(&mut self, name: &str, f: F) {
        self.push_scope(name);
        f(self);
        self.pop_scope();
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Current time in milliseconds since UNIX epoch.
fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

/// Convert a [`NodeKind`] enum variant to its canonical snake_case string.
///
/// This mapping matches the kind strings used in the `nodes` table and
/// the `kind` qualifier in `tws-graph search`.
pub(crate) fn node_kind_to_str(kind: NodeKind) -> &'static str {
    match kind {
        // --- programming-language generics ---
        NodeKind::File => "file",
        NodeKind::Module => "module",
        NodeKind::Namespace => "namespace",
        NodeKind::Package => "package",

        NodeKind::Class => "class",
        NodeKind::Struct => "struct",
        NodeKind::Interface => "interface",
        NodeKind::Trait => "trait",
        NodeKind::Object => "object",
        NodeKind::Enum => "enum",
        NodeKind::EnumMember => "enum_member",
        NodeKind::Union => "union",

        NodeKind::Function => "function",
        NodeKind::Method => "method",
        NodeKind::Lambda => "lambda",
        NodeKind::Closure => "closure",

        NodeKind::Variable => "variable",
        NodeKind::Constant => "constant",
        NodeKind::Field => "field",
        NodeKind::Property => "property",
        NodeKind::Attribute => "attribute",
        NodeKind::Parameter => "parameter",

        NodeKind::TypeAlias => "type_alias",
        NodeKind::TypeDef => "type_def",
        NodeKind::Record => "record",
        NodeKind::Instance => "instance",
        NodeKind::Signature => "signature",

        // --- structural / markup ---
        NodeKind::HtmlElement => "html_element",
        NodeKind::CssRule => "css_rule",
        NodeKind::CssImport => "css_import",
        NodeKind::CssKeyframes => "css_keyframes",
        NodeKind::CssMedia => "css_media",
        NodeKind::MdHeading => "md_heading",
        NodeKind::MdCodeBlock => "md_code_block",
        NodeKind::MdLink => "md_link",
        NodeKind::MdImage => "md_image",
        NodeKind::MdRefdef => "md_refdef",

        // --- configuration ---
        NodeKind::TomlTable => "toml_table",
        NodeKind::TomlTableArray => "toml_table_array",
        NodeKind::YamlKey => "yaml_key",
        NodeKind::YamlDocument => "yaml_document",
        NodeKind::JsonKey => "json_key",
        NodeKind::HclResource => "hcl_resource",
        NodeKind::HclData => "hcl_data",
        NodeKind::HclModule => "hcl_module",
        NodeKind::HclProvider => "hcl_provider",
        NodeKind::HclVariable => "hcl_variable",
        NodeKind::HclOutput => "hcl_output",
        NodeKind::HclTerraform => "hcl_terraform",
        NodeKind::HclLocals => "hcl_locals",
        NodeKind::HclBackend => "hcl_backend",
        NodeKind::HclRequiredProviders => "hcl_required_providers",
        NodeKind::HclProvisioner => "hcl_provisioner",
        NodeKind::K8sResource => "k8s_resource",
        NodeKind::KustomizeSection => "kustomize_section",
        NodeKind::DockerfileStage => "dockerfile_stage",
        NodeKind::DockerfileImage => "dockerfile",

        // --- data ---
        NodeKind::SqlTable => "sql_table",
        NodeKind::SqlIndex => "sql_index",
        NodeKind::SqlView => "sql_view",
        NodeKind::SqlQuery => "sql_query",

        // --- proto / rpc ---
        NodeKind::ProtoFile => "proto_file",
        NodeKind::Service => "service",
        NodeKind::RpcMethod => "rpc_method",

        // --- shell ---
        NodeKind::BashFunction => "bash_function",
        NodeKind::BashVariable => "bash_variable",

        // --- lua ---
        NodeKind::LuaTable => "lua_table",
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // ------------------------------------------------------------------
    // ExtractionResult
    // ------------------------------------------------------------------

    #[test]
    fn test_extraction_result_default_is_empty() {
        let r = ExtractionResult::default();
        assert!(r.nodes.is_empty());
        assert!(r.edges.is_empty());
    }

    #[test]
    fn test_extraction_result_new() {
        let r = ExtractionResult::new();
        assert!(r.nodes.is_empty());
        assert!(r.edges.is_empty());
    }

    // ------------------------------------------------------------------
    // ExtractionContext — construction
    // ------------------------------------------------------------------

    #[test]
    fn test_new_context() {
        let ctx = ExtractionContext::new("src/main.rs".into(), "rust".into());
        assert_eq!(ctx.file_path, "src/main.rs");
        assert_eq!(ctx.language, "rust");
        assert_eq!(ctx.scope_depth(), 0);
        assert!(ctx.result.nodes.is_empty());
        assert!(ctx.result.edges.is_empty());
        assert!(ctx.current_scope_node_id().is_none());
    }

    // ------------------------------------------------------------------
    // Qualified name
    // ------------------------------------------------------------------

    #[test]
    fn test_make_qualified_no_scope() {
        let ctx = ExtractionContext::new("src/lib.rs".into(), "rust".into());
        let qname = ctx.make_qualified("my_func");
        assert_eq!(qname, "src/lib.rs::my_func");
    }

    #[test]
    fn test_make_qualified_one_scope() {
        let mut ctx = ExtractionContext::new("src/lib.rs".into(), "rust".into());
        ctx.push_scope("MyStruct");
        let qname = ctx.make_qualified("my_method");
        assert_eq!(qname, "src/lib.rs::MyStruct.my_method");
    }

    #[test]
    fn test_make_qualified_nested_scopes() {
        let mut ctx = ExtractionContext::new("src/lib.rs".into(), "rust".into());
        ctx.push_scope("MyStruct");
        ctx.push_scope("my_method");
        let qname = ctx.make_qualified("inner_var");
        assert_eq!(qname, "src/lib.rs::MyStruct.my_method.inner_var");
    }

    #[test]
    fn test_make_qualified_after_pop() {
        let mut ctx = ExtractionContext::new("src/lib.rs".into(), "rust".into());
        ctx.push_scope("MyStruct");
        ctx.push_scope("my_method");
        ctx.pop_scope(); // pop my_method
        let qname = ctx.make_qualified("another_method");
        assert_eq!(qname, "src/lib.rs::MyStruct.another_method");
    }

    // ------------------------------------------------------------------
    // Scope management
    // ------------------------------------------------------------------

    #[test]
    fn test_push_pop_scope() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());
        assert_eq!(ctx.scope_depth(), 0);

        ctx.push_scope("outer");
        assert_eq!(ctx.scope_depth(), 1);

        ctx.push_scope("inner");
        assert_eq!(ctx.scope_depth(), 2);

        let popped = ctx.pop_scope();
        assert!(popped);
        assert_eq!(ctx.scope_depth(), 1);

        let popped = ctx.pop_scope();
        assert!(popped);
        assert_eq!(ctx.scope_depth(), 0);

        // Popping an empty stack returns false without panicking
        let popped = ctx.pop_scope();
        assert!(!popped);
    }

    #[test]
    fn test_push_scope_with_kind() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());
        ctx.push_scope_with_kind("MyClass", "class");
        assert_eq!(ctx.scope_depth(), 1);
    }

    #[test]
    fn test_with_scope() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());

        ctx.with_scope("temp_scope", |c| {
            assert_eq!(c.scope_depth(), 1);
            assert_eq!(
                c.make_qualified("inner"),
                "f.rs::temp_scope.inner"
            );
        });

        // Scope should be popped after with_scope returns
        assert_eq!(ctx.scope_depth(), 0);
        assert_eq!(ctx.make_qualified("outer"), "f.rs::outer");
    }

    #[test]
    fn test_with_scope_nested() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());

        ctx.with_scope("outer", |c| {
            c.with_scope("inner", |c| {
                assert_eq!(c.scope_depth(), 2);
                assert_eq!(
                    c.make_qualified("x"),
                    "f.rs::outer.inner.x"
                );
            });
            assert_eq!(c.scope_depth(), 1);
        });

        assert_eq!(ctx.scope_depth(), 0);
    }

    // ------------------------------------------------------------------
    // Scope node tracking
    // ------------------------------------------------------------------

    #[test]
    fn test_push_scope_node_and_current() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());

        ctx.push_scope_node("abc123");
        assert_eq!(ctx.current_scope_node_id(), Some("abc123"));

        ctx.push_scope_node("def456");
        assert_eq!(ctx.current_scope_node_id(), Some("def456"));

        ctx.pop_scope(); // pops node_stack too
        assert_eq!(ctx.current_scope_node_id(), Some("abc123"));

        ctx.pop_scope();
        assert_eq!(ctx.current_scope_node_id(), None);
    }

    // ------------------------------------------------------------------
    // Edge creation (does not require tree-sitter Node)
    // ------------------------------------------------------------------

    #[test]
    fn test_add_edge_creates_record() {
        let mut ctx = ExtractionContext::new("src/main.rs".into(), "rust".into());

        ctx.add_edge(
            "src_node_id",
            "tgt_node_id",
            EdgeKind::Calls,
            42,
            Some("target_func"),
        );

        assert_eq!(ctx.result.edges.len(), 1);
        let edge = &ctx.result.edges[0];
        assert_eq!(edge.source, "src_node_id");
        assert_eq!(edge.target, "tgt_node_id");
        assert_eq!(edge.kind, "CALLS");
        assert_eq!(edge.target_text.as_deref(), Some("target_func"));
        assert!(edge.source_loc.as_deref().unwrap().contains(":42:"));
        assert_eq!(edge.provenance.as_deref(), Some("tree-sitter"));
    }

    #[test]
    fn test_add_edge_without_target_text() {
        let mut ctx = ExtractionContext::new("src/main.rs".into(), "rust".into());

        ctx.add_edge("src", "tgt", EdgeKind::Imports, 10, None);

        assert_eq!(ctx.result.edges.len(), 1);
        let edge = &ctx.result.edges[0];
        assert_eq!(edge.target_text, None);
        assert_eq!(edge.kind, "IMPORTS");
    }

    #[test]
    fn test_multiple_edges() {
        let mut ctx = ExtractionContext::new("f.rs".into(), "rust".into());

        ctx.add_edge("a", "b", EdgeKind::Calls, 1, None);
        ctx.add_edge("a", "c", EdgeKind::Calls, 2, None);
        ctx.add_edge("b", "d", EdgeKind::References, 3, None);

        assert_eq!(ctx.result.edges.len(), 3);
    }

    // ------------------------------------------------------------------
    // Edge kind round-trips
    // ------------------------------------------------------------------

    #[test]
    fn test_all_edge_kinds_produce_valid_strings() {
        // Verify every EdgeKind variant produces a non-empty kind string.
        let kinds = [
            EdgeKind::Calls,
            EdgeKind::Imports,
            EdgeKind::References,
            EdgeKind::Extends,
            EdgeKind::Implements,
            EdgeKind::Overrides,
            EdgeKind::Instantiates,
            EdgeKind::Decorates,
            EdgeKind::TypeRef,
            EdgeKind::Contains,
            EdgeKind::DataFlows,
            EdgeKind::Reads,
            EdgeKind::Writes,
            EdgeKind::Throws,
            EdgeKind::EnvAccesses,
            EdgeKind::Emits,
            EdgeKind::ListensOn,
            EdgeKind::HttpCalls,
            EdgeKind::GrpcService,
            EdgeKind::GrpcClient,
            EdgeKind::GrpcServer,
            EdgeKind::SimilarTo,
            EdgeKind::TestEdge,
            EdgeKind::ConfigLink,
        ];

        for kind in &kinds {
            let s = kind.as_str();
            assert!(!s.is_empty(), "EdgeKind {:?} has empty as_str()", kind);
            assert!(
                s.chars().all(|c| c.is_uppercase() || c == '_'),
                "EdgeKind {:?} as_str() = {:?} is not UPPER_SNAKE",
                kind,
                s
            );
        }
    }

    // ------------------------------------------------------------------
    // Node kind to string
    // ------------------------------------------------------------------

    #[test]
    fn test_node_kind_to_str_is_snake_case() {
        // Spot-check that common kinds produce the expected strings
        assert_eq!(node_kind_to_str(NodeKind::File), "file");
        assert_eq!(node_kind_to_str(NodeKind::Module), "module");
        assert_eq!(node_kind_to_str(NodeKind::Class), "class");
        assert_eq!(node_kind_to_str(NodeKind::Function), "function");
        assert_eq!(node_kind_to_str(NodeKind::Method), "method");
        assert_eq!(node_kind_to_str(NodeKind::Variable), "variable");
        assert_eq!(node_kind_to_str(NodeKind::Interface), "interface");
        assert_eq!(node_kind_to_str(NodeKind::Enum), "enum");
        assert_eq!(node_kind_to_str(NodeKind::Struct), "struct");
        assert_eq!(node_kind_to_str(NodeKind::Constant), "constant");
        assert_eq!(node_kind_to_str(NodeKind::Field), "field");
        assert_eq!(node_kind_to_str(NodeKind::TypeAlias), "type_alias");
    }

    #[test]
    fn test_node_kind_to_str_all_variants_non_empty() {
        // Verify every variant returns a non-empty string.
        // We enumerate all variants via a helper.
        let all = all_node_kinds();
        for kind in all {
            let s = node_kind_to_str(kind);
            assert!(
                !s.is_empty(),
                "NodeKind::{kind:?} has empty kind string"
            );
        }
    }

    #[test]
    fn test_node_kind_to_str_no_uppercase() {
        // All kind strings should be lowercase with underscores (no CamelCase).
        let all = all_node_kinds();
        for kind in all {
            let s = node_kind_to_str(kind);
            assert!(
                s.chars().all(|c| c.is_lowercase() || c == '_' || c.is_ascii_digit()),
                "NodeKind::{kind:?} → {s:?} has unexpected characters"
            );
        }
    }

    /// Return every `NodeKind` variant (for exhaustive testing).
    fn all_node_kinds() -> Vec<NodeKind> {
        vec![
            NodeKind::File,
            NodeKind::Module,
            NodeKind::Namespace,
            NodeKind::Package,
            NodeKind::Class,
            NodeKind::Struct,
            NodeKind::Interface,
            NodeKind::Trait,
            NodeKind::Object,
            NodeKind::Enum,
            NodeKind::EnumMember,
            NodeKind::Union,
            NodeKind::Function,
            NodeKind::Method,
            NodeKind::Lambda,
            NodeKind::Closure,
            NodeKind::Variable,
            NodeKind::Constant,
            NodeKind::Field,
            NodeKind::Property,
            NodeKind::Attribute,
            NodeKind::Parameter,
            NodeKind::TypeAlias,
            NodeKind::TypeDef,
            NodeKind::Record,
            NodeKind::Instance,
            NodeKind::Signature,
            NodeKind::HtmlElement,
            NodeKind::CssRule,
            NodeKind::CssImport,
            NodeKind::CssKeyframes,
            NodeKind::CssMedia,
            NodeKind::MdHeading,
            NodeKind::MdCodeBlock,
            NodeKind::MdLink,
            NodeKind::MdImage,
            NodeKind::MdRefdef,
            NodeKind::TomlTable,
            NodeKind::TomlTableArray,
            NodeKind::YamlKey,
            NodeKind::YamlDocument,
            NodeKind::JsonKey,
            NodeKind::HclResource,
            NodeKind::HclData,
            NodeKind::HclModule,
            NodeKind::HclProvider,
            NodeKind::HclVariable,
            NodeKind::HclOutput,
            NodeKind::HclTerraform,
            NodeKind::HclLocals,
            NodeKind::HclBackend,
            NodeKind::HclRequiredProviders,
            NodeKind::HclProvisioner,
            NodeKind::K8sResource,
            NodeKind::KustomizeSection,
            NodeKind::DockerfileStage,
            NodeKind::DockerfileImage,
            NodeKind::SqlTable,
            NodeKind::SqlIndex,
            NodeKind::SqlView,
            NodeKind::SqlQuery,
            NodeKind::ProtoFile,
            NodeKind::Service,
            NodeKind::RpcMethod,
            NodeKind::BashFunction,
            NodeKind::BashVariable,
            NodeKind::LuaTable,
        ]
    }
}
