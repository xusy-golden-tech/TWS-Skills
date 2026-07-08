//! Core traits and type definitions for TWS CodeGraph.
//!
//! Defines the `Extractor` trait for language-specific symbol extraction,
//! and `NodeKind` / `EdgeKind` enums for the graph schema.

use tree_sitter::Tree;

use crate::indexer::context::ExtractionContext;

// ---------------------------------------------------------------------------
// Extractor trait
// ---------------------------------------------------------------------------

/// Trait implemented by every language extractor.
///
/// Each extractor knows which file extensions and language names it handles,
/// and can produce nodes + edges from a tree-sitter parse tree.
pub trait Extractor {
    /// File extensions this extractor handles (e.g. `["py"]`).
    fn extensions(&self) -> Vec<&'static str>;

    /// Language names this extractor handles (e.g. `["python"]`).
    fn languages(&self) -> Vec<&'static str>;

    /// Extract symbols and edges from a parsed source file.
    ///
    /// # Arguments
    /// * `source` - Raw source bytes.
    /// * `tree`   - Tree-sitter concrete syntax tree.
    /// * `ctx`    - Mutable extraction context (accumulates nodes + edges).
    fn extract(
        &self,
        source: &[u8],
        tree: &Tree,
        ctx: &mut ExtractionContext,
    ) -> anyhow::Result<()>;
}

// ---------------------------------------------------------------------------
// NodeKind — symbol / structural node types
// ---------------------------------------------------------------------------

/// Kinds of nodes that can appear in the code graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum NodeKind {
    // --- programming-language generics ---
    File,
    Module,
    Namespace,
    Package,

    Class,
    Struct,
    Interface,
    Trait,
    Object,
    Enum,
    EnumMember,
    Union,

    Function,
    Method,
    Lambda,
    Closure,

    Variable,
    Constant,
    Field,
    Property,
    Attribute,
    Parameter,

    TypeAlias,
    TypeDef,
    Record,
    Instance,
    Signature,

    // --- structural / markup ---
    HtmlElement,
    CssRule,
    CssImport,
    CssKeyframes,
    CssMedia,
    MdHeading,
    MdCodeBlock,
    MdLink,
    MdImage,
    MdRefdef,

    // --- configuration ---
    TomlTable,
    TomlTableArray,
    YamlKey,
    YamlDocument,
    JsonKey,
    HclResource,
    HclData,
    HclModule,
    HclProvider,
    HclVariable,
    HclOutput,
    HclTerraform,
    HclLocals,
    HclBackend,
    HclRequiredProviders,
    HclProvisioner,
    K8sResource,
    KustomizeSection,
    DockerfileStage,
    DockerfileImage,

    // --- data ---
    SqlTable,
    SqlIndex,
    SqlView,
    SqlQuery,

    // --- proto / rpc ---
    ProtoFile,
    Service,
    RpcMethod,

    // --- shell ---
    BashFunction,
    BashVariable,

    // --- lua ---
    LuaTable,
}

// ---------------------------------------------------------------------------
// EdgeKind — 24 edge types for relationships
// ---------------------------------------------------------------------------

/// 24 relationship types between nodes in the code graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum EdgeKind {
    // --- structural (10) ---
    /// Function / method call.
    Calls,
    /// Module / package import.
    Imports,
    /// General symbol reference.
    References,
    /// Class inheritance.
    Extends,
    /// Interface / trait implementation.
    Implements,
    /// Method override.
    Overrides,
    /// Class / struct instantiation.
    Instantiates,
    /// Decorator / annotation application.
    Decorates,
    /// Type annotation reference.
    TypeRef,
    /// Containment (e.g. file contains class).
    Contains,

    // --- data flow (4) ---
    /// Data flow (cross-function / cross-file).
    DataFlows,
    /// Variable read.
    Reads,
    /// Variable write.
    Writes,
    /// Exception throw (including cross-function propagation).
    Throws,

    // --- environment / events (3) ---
    /// Environment variable access.
    EnvAccesses,
    /// Event emission.
    Emits,
    /// Event listener registration.
    ListensOn,

    // --- cross-service (4) ---
    /// HTTP call (activated in v7.4 cross-tier tracing).
    /// Created by CrossTierScanner during indexing from http_calls/http_routes tables
    /// and consumed by GraphTraverser during cross-language trace/impact queries.
    HttpCalls,
    /// gRPC service definition (reserved — not yet activated).
    GrpcService,
    /// gRPC client usage (reserved — not yet activated).
    GrpcClient,
    /// gRPC server registration (reserved — not yet activated).
    GrpcServer,

    // --- analysis (3) ---
    /// Code clone similarity.
    SimilarTo,
    /// Test-to-code edge.
    TestEdge,
    /// Configuration-to-code link.
    ConfigLink,
}

impl EdgeKind {
    /// Human-readable label for each edge kind.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Calls => "CALLS",
            Self::Imports => "IMPORTS",
            Self::References => "REFERENCES",
            Self::Extends => "EXTENDS",
            Self::Implements => "IMPLEMENTS",
            Self::Overrides => "OVERRIDES",
            Self::Instantiates => "INSTANTIATES",
            Self::Decorates => "DECORATES",
            Self::TypeRef => "TYPE_REF",
            Self::Contains => "CONTAINS",
            Self::DataFlows => "DATA_FLOWS",
            Self::Reads => "READS",
            Self::Writes => "WRITES",
            Self::Throws => "THROWS",
            Self::EnvAccesses => "ENV_ACCESSES",
            Self::Emits => "EMITS",
            Self::ListensOn => "LISTENS_ON",
            Self::HttpCalls => "HTTP_CALLS",
            Self::GrpcService => "GRPC_SERVICE",
            Self::GrpcClient => "GRPC_CLIENT",
            Self::GrpcServer => "GRPC_SERVER",
            Self::SimilarTo => "SIMILAR_TO",
            Self::TestEdge => "TEST_EDGE",
            Self::ConfigLink => "CONFIG_LINK",
        }
    }
}

impl std::fmt::Display for EdgeKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}
