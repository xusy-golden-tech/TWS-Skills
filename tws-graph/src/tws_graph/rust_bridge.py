"""
Bridge to Rust core (_core._core native library).
Provides drop-in replacements for performance-critical operations.
Set TWS_USE_RUST=0 to disable and fall back to Python implementations.
"""

import json
import os
from typing import Optional


def _rust_available() -> bool:
    """Check if Rust core library is available and enabled."""
    if os.environ.get("TWS_USE_RUST", "1") == "0":
        return False
    try:
        from _core._core import ping
        return True
    except ImportError:
        return False


def rust_index(db_path: str, project_root: str, twsignore_path: str | None = None,
               include_patterns: list[str] | None = None,
               exclude_patterns: list[str] | None = None,
               no_cross_tier: bool = False,
               no_cross_ffi: bool = False) -> str:
    """Run index using Rust core. Returns summary string.

    Args:
        db_path: Path to the SQLite index database.
        project_root: Root directory of the project to index.
        twsignore_path: Optional path to a .twsignore file.  None means
            auto-detect root/.twsignore; '' means suppress ignore entirely.
        include_patterns: Optional list of glob patterns. If given, only
            files matching at least one pattern are indexed.
        exclude_patterns: Optional list of glob patterns. Files matching
            any pattern are excluded from indexing.
        no_cross_tier: If True, skip cross-tier HTTP call/route scanning phase.
        no_cross_ffi: If True, skip cross-tier FFI import/export scanning phase.
    """
    from _core._core import index
    return index(db_path, project_root, twsignore_path, no_cross_tier, no_cross_ffi, include_patterns, exclude_patterns)


def rust_search(db_path: str, query: str, limit: int = 50,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None) -> list[dict]:
    """Search using Rust FTS5 engine.

    Args:
        include_paths: Optional list of glob patterns. Only results whose
            file_path matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Results whose
            file_path matches any pattern are excluded.
    """
    from _core._core import search
    return search(db_path, query, limit, include_paths, exclude_paths)


def rust_calls(db_path: str, node_name: str, inbound: bool = True, depth: int = 5,
               format: str | None = None,
               include_paths: list[str] | None = None,
               exclude_paths: list[str] | None = None,
               no_cross: bool = False) -> str:
    """Find call targets/callers using Rust BFS.

    Default: inbound=True (show callers), matching CLI default behavior.

    Args:
        format: Output format. "json" for JSON, "brief" for old format,
            None for rich tree format (default).
        include_paths: Optional list of glob patterns. Only results whose
            file_path matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Results whose
            file_path matches any pattern are excluded.
        no_cross: If True, disable cross-language tracing.
    """
    from _core._core import calls
    return calls(db_path, node_name, inbound, depth, format, include_paths, exclude_paths, no_cross)


def rust_impact(db_path: str, node_name: str, depth: int = 5,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None,
                no_cross: bool = False) -> str:
    """Impact analysis using Rust BFS.

    Args:
        include_paths: Optional list of glob patterns. Only results whose
            file_path matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Results whose
            file_path matches any pattern are excluded.
        no_cross: If True, disable cross-language impact analysis.
    """
    from _core._core import impact
    return impact(db_path, node_name, depth, include_paths, exclude_paths, no_cross)


def rust_trace(db_path: str, src: str, tgt: str,
               include_paths: list[str] | None = None,
               exclude_paths: list[str] | None = None,
               no_cross: bool = False) -> str:
    """Find path between two symbols using Rust BFS.

    Args:
        include_paths: Optional list of glob patterns. Only path nodes whose
            file_path matches at least one pattern are included.
        exclude_paths: Optional list of glob patterns. Path nodes whose
            file_path matches any pattern are excluded.
        no_cross: If True, disable cross-language tracing.
    """
    from _core._core import trace
    return trace(db_path, src, tgt, include_paths, exclude_paths, no_cross)


def rust_unresolved(db_path: str,
                    include_paths: list[str] | None = None,
                    exclude_paths: list[str] | None = None) -> str:
    """List unresolved references using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only refs whose
            file_path matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Refs whose
            file_path matches any pattern are excluded.
    """
    from _core._core import unresolved
    return unresolved(db_path, include_paths, exclude_paths)


def rust_resolve(db_path: str, project_root: str) -> str:
    """Run cross-file reference resolution using Rust.

    Scans dangling edges and attempts to resolve module references
    using language-specific resolvers. Returns a JSON string with
    resolution statistics.

    Args:
        db_path: Path to the SQLite index database.
        project_root: Root directory of the project.

    Returns:
        JSON string: {"resolved": N, "unresolved": N, "already_valid": N, "external": N}
    """
    from _core._core import resolve_refs
    return resolve_refs(db_path, project_root)


def rust_export_dot(db_path: str, from_node: str | None = None, depth: int = 3, kind: str | None = None,
                    include_paths: list[str] | None = None,
                    exclude_paths: list[str] | None = None,
                    to_node: str | None = None) -> str:
    """Export graph in Graphviz DOT format using Rust.

    Args:
        from_node: Optional starting node ID for forward BFS (outbound edges).
        include_paths: Optional list of glob patterns. Only nodes whose
            file_path matches at least one pattern are included.
        exclude_paths: Optional list of glob patterns. Nodes whose
            file_path matches any pattern are excluded.
        to_node: Optional target node ID for reverse BFS (inbound edges).
            Mutually exclusive with from_node.
    """
    from _core._core import export_dot
    return export_dot(db_path, from_node, to_node, depth, kind, include_paths, exclude_paths)


def rust_export_mermaid(db_path: str, from_node: str | None = None, depth: int = 3, kind: str | None = None,
                        include_paths: list[str] | None = None,
                        exclude_paths: list[str] | None = None,
                        group_by_file: bool = False,
                        to_node: str | None = None) -> str:
    """Export graph in Mermaid format using Rust.

    Args:
        from_node: Optional starting node ID for forward BFS (outbound edges).
        include_paths: Optional list of glob patterns. Only nodes whose
            file_path matches at least one pattern are included.
        exclude_paths: Optional list of glob patterns. Nodes whose
            file_path matches any pattern are excluded.
        group_by_file: If True, wrap nodes in subgraphs grouped by source file.
        to_node: Optional target node ID for reverse BFS (inbound edges).
            Mutually exclusive with from_node.
    """
    from _core._core import export_mermaid
    return export_mermaid(db_path, from_node, to_node, depth, kind, include_paths, exclude_paths, group_by_file)


def rust_export_json(db_path: str, kind: str | None = None, limit: int | None = None,
                     include_paths: list[str] | None = None,
                     exclude_paths: list[str] | None = None) -> str:
    """Export graph in JSON format using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only nodes whose
            file_path matches at least one pattern are included.
        exclude_paths: Optional list of glob patterns. Nodes whose
            file_path matches any pattern are excluded.
    """
    from _core._core import export_json
    return export_json(db_path, kind, limit, include_paths, exclude_paths)


def rust_cycles(db_path: str,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None) -> str:
    """Detect cycles in the call graph using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only cycles involving
            at least one node matching a pattern are returned.
        exclude_paths: Optional list of glob patterns. Cycles where all nodes
            match excluded patterns are filtered out.
    """
    from _core._core import cycles
    return cycles(db_path, include_paths, exclude_paths)


def rust_metrics(db_path: str,
                  include_paths: list[str] | None = None,
                  exclude_paths: list[str] | None = None) -> str:
    """Calculate module metrics (cohesion/coupling/instability) using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only modules whose
            name matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Modules whose
            name matches any pattern are excluded.
    """
    from _core._core import metrics
    return metrics(db_path, include_paths, exclude_paths)


def rust_health(db_path: str, worst: int = 10,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None) -> str:
    """Code health scoring using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only files whose
            path matches at least one pattern are scored.
        exclude_paths: Optional list of glob patterns. Files whose
            path matches any pattern are excluded from scoring.
    """
    from _core._core import health
    return health(db_path, worst, include_paths, exclude_paths)


def rust_lint(skills_dir: str, json_output: bool = False) -> str:
    """Validate skill files using Rust."""
    from _core._core import lint_skills
    return lint_skills(skills_dir, json_output)


def rust_semantic_search(db_path: str, query: str, limit: int | None = None) -> str:
    """Semantic search using Rust 11-signal ranking."""
    from _core._core import semantic_search
    return semantic_search(db_path, query, limit)


def rust_gql_query(db_path: str, query: str,
                   include_paths: list[str] | None = None,
                   exclude_paths: list[str] | None = None) -> str:
    """Execute GQL graph query using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only results whose
            file_path matches at least one pattern are returned.
        exclude_paths: Optional list of glob patterns. Results whose
            file_path matches any pattern are excluded.
    """
    from _core._core import gql_query
    return gql_query(db_path, query, include_paths, exclude_paths)


def rust_taint(db_path: str,
               include_paths: list[str] | None = None,
               exclude_paths: list[str] | None = None) -> str:
    """Security taint analysis (source->sink) using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only taint paths
            involving at least one node in matching files are returned.
        exclude_paths: Optional list of glob patterns. Taint paths where
            all nodes match excluded patterns are filtered out.
    """
    from _core._core import taint
    return taint(db_path, include_paths, exclude_paths)


def rust_predict_impact(db_path: str, name: str,
                        include_paths: list[str] | None = None,
                        exclude_paths: list[str] | None = None) -> str:
    """Predict change impact (radius + risk + test suggestions) using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only affected files
            matching at least one pattern are included in the report.
        exclude_paths: Optional list of glob patterns. Affected files
            matching any pattern are excluded from the report.
    """
    from _core._core import predict_impact
    return predict_impact(db_path, name, include_paths, exclude_paths)


def rust_layers(db_path: str,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None) -> str:
    """Detect architecture layer violations using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only violations where
            at least one involved file matches a pattern are returned.
        exclude_paths: Optional list of glob patterns. Violations where
            all involved files match excluded patterns are filtered out.
    """
    from _core._core import layers
    return layers(db_path, include_paths, exclude_paths)


def rust_clones(db_path: str,
                threshold: float | None = None,
                include_paths: list[str] | None = None,
                exclude_paths: list[str] | None = None) -> str:
    """Detect code clones using Rust.

    Args:
        threshold: Similarity threshold (0.0-1.0). Default: 0.8.
        include_paths: Optional list of glob patterns. Only clone pairs where
            at least one file matches a pattern are returned.
        exclude_paths: Optional list of glob patterns. Clone pairs where
            all files match excluded patterns are filtered out.
    """
    from _core._core import clones
    return clones(db_path, threshold, include_paths, exclude_paths)


def rust_community(db_path: str,
                   include_paths: list[str] | None = None,
                   exclude_paths: list[str] | None = None) -> str:
    """Detect communities in call graph using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only community members
            whose file_path matches a pattern are included.
        exclude_paths: Optional list of glob patterns. Community members
            whose file_path matches any pattern are excluded.
    """
    from _core._core import community
    return community(db_path, include_paths, exclude_paths)


def rust_centrality(db_path: str,
                    include_paths: list[str] | None = None,
                    exclude_paths: list[str] | None = None) -> str:
    """Compute graph centrality using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only nodes whose
            file_path matches a pattern are included in centrality.
        exclude_paths: Optional list of glob patterns. Nodes whose
            file_path matches any pattern are excluded.
    """
    from _core._core import centrality
    return centrality(db_path, include_paths, exclude_paths)


def rust_dead_code(db_path: str,
                   include_paths: list[str] | None = None,
                   exclude_paths: list[str] | None = None) -> str:
    """Detect potential dead code using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only dead code items
            whose file_path matches a pattern are returned.
        exclude_paths: Optional list of glob patterns. Dead code items
            whose file_path matches any pattern are excluded.
    """
    from _core._core import dead_code
    return dead_code(db_path, include_paths, exclude_paths)


def rust_entry_points(db_path: str,
                      include_paths: list[str] | None = None,
                      exclude_paths: list[str] | None = None) -> str:
    """Identify project entry points using Rust.

    Args:
        include_paths: Optional list of glob patterns. Only entry points
            whose file_path matches a pattern are returned.
        exclude_paths: Optional list of glob patterns. Entry points whose
            file_path matches any pattern are excluded.
    """
    from _core._core import entry_points
    return entry_points(db_path, include_paths, exclude_paths)


def rust_hooks_install(repo_root: str) -> str:
    """Install git hooks for auto-sync using Rust."""
    from _core._core import hooks_install
    return hooks_install(repo_root)


def rust_hooks_remove(repo_root: str) -> str:
    """Remove git hooks using Rust."""
    from _core._core import hooks_remove
    return hooks_remove(repo_root)


def rust_hooks_status(repo_root: str) -> str:
    """Check git hooks status using Rust."""
    from _core._core import hooks_status
    return hooks_status(repo_root)


def rust_watch_start(db_path: str, root: str, interval: float = 2.0) -> str:
    """Start file watcher using Rust."""
    from _core._core import watch_start
    return watch_start(db_path, root, interval)


def rust_snapshot_create(db_path: str, name: str) -> str:
    """Create named snapshot using Rust."""
    from _core._core import snapshot_create
    return snapshot_create(db_path, name)


def rust_snapshot_list(db_path: str) -> str:
    """List all snapshots using Rust."""
    from _core._core import snapshot_list
    return snapshot_list(db_path)


def rust_snapshot_diff(db_path: str, a: str, b: str, brief: bool = False) -> str:
    """Compare two snapshots using Rust."""
    from _core._core import snapshot_diff
    return snapshot_diff(db_path, a, b, brief)


def rust_lsp_setup(db_path: str) -> str:
    """Detect installed LSP servers using Rust."""
    from _core._core import lsp_setup
    return lsp_setup(db_path)


def rust_federate_search(db_path: str, query: str) -> str:
    """Cross-repo federated search using Rust."""
    from _core._core import federate_search
    return federate_search(db_path, query)


def rust_routes(db_path: str, unmatched: bool = False, url_filter: Optional[str] = None,
                method_filter: Optional[str] = None, json_output: bool = False) -> str:
    """查询路由全景（HTTP 调用与路由定义匹配状态）"""
    if not _rust_available():
        return json.dumps({"error": "Rust core not available"})
    from _core._core import routes
    return routes(db_path, unmatched, url_filter, method_filter, json_output)


def rust_trace_request(db_path: str, url: str, method: str = "GET",
                       no_cross: bool = False) -> str:
    """给定 URL 和方法，输出完整调用链（前端调用者 + 后端处理链）"""
    if not _rust_available():
        return json.dumps({"error": "Rust core not available"})
    from _core._core import trace_request
    return trace_request(db_path, url, method, no_cross)
