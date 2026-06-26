"""
Bridge to Rust core (_core._core native library).
Provides drop-in replacements for performance-critical operations.
Set TWS_USE_RUST=0 to disable and fall back to Python implementations.
"""

import os


def _rust_available() -> bool:
    """Check if Rust core library is available and enabled."""
    if os.environ.get("TWS_USE_RUST", "1") == "0":
        return False
    try:
        from _core._core import ping
        return True
    except ImportError:
        return False


def rust_index(db_path: str, project_root: str) -> str:
    """Run index using Rust core. Returns summary string."""
    from _core._core import index
    return index(db_path, project_root)


def rust_search(db_path: str, query: str, limit: int = 50) -> list[dict]:
    """Search using Rust FTS5 engine."""
    from _core._core import search
    return search(db_path, query, limit)


def rust_calls(db_path: str, node_name: str, inbound: bool = False, depth: int = 5) -> str:
    """Find call targets/callers using Rust BFS."""
    from _core._core import calls
    return calls(db_path, node_name, inbound, depth)


def rust_impact(db_path: str, node_name: str, depth: int = 5) -> str:
    """Impact analysis using Rust BFS."""
    from _core._core import impact
    return impact(db_path, node_name, depth)


def rust_trace(db_path: str, src: str, tgt: str) -> str:
    """Find path between two symbols using Rust BFS."""
    from _core._core import trace
    return trace(db_path, src, tgt)


def rust_unresolved(db_path: str) -> str:
    """List unresolved references using Rust."""
    from _core._core import unresolved
    return unresolved(db_path)


def rust_export_dot(db_path: str, from_node: str, depth: int = 3, kind: str | None = None) -> str:
    """Export graph in Graphviz DOT format using Rust."""
    from _core._core import export_dot
    return export_dot(db_path, from_node, depth, kind)


def rust_export_mermaid(db_path: str, from_node: str, depth: int = 3, kind: str | None = None) -> str:
    """Export graph in Mermaid format using Rust."""
    from _core._core import export_mermaid
    return export_mermaid(db_path, from_node, depth, kind)


def rust_export_json(db_path: str, kind: str | None = None, limit: int | None = None) -> str:
    """Export graph in JSON format using Rust."""
    from _core._core import export_json
    return export_json(db_path, kind, limit)


def rust_cycles(db_path: str) -> str:
    """Detect cycles in the call graph using Rust."""
    from _core._core import cycles
    return cycles(db_path)


def rust_metrics(db_path: str) -> str:
    """Calculate module metrics (cohesion/coupling/instability) using Rust."""
    from _core._core import metrics
    return metrics(db_path)


def rust_health(db_path: str, worst: int = 10) -> str:
    """Code health scoring using Rust."""
    from _core._core import health
    return health(db_path, worst)


def rust_lint(skills_dir: str) -> str:
    """Validate skill files using Rust."""
    from _core._core import lint_skills
    return lint_skills(skills_dir)


def rust_semantic_search(db_path: str, query: str, limit: int | None = None) -> str:
    """Semantic search using Rust 11-signal ranking."""
    from _core._core import semantic_search
    return semantic_search(db_path, query, limit)


def rust_gql_query(db_path: str, query: str) -> str:
    """Execute GQL graph query using Rust."""
    from _core._core import gql_query
    return gql_query(db_path, query)


def rust_taint(db_path: str) -> str:
    """Security taint analysis (source->sink) using Rust."""
    from _core._core import taint
    return taint(db_path)


def rust_predict_impact(db_path: str, name: str) -> str:
    """Predict change impact (radius + risk + test suggestions) using Rust."""
    from _core._core import predict_impact
    return predict_impact(db_path, name)


def rust_layers(db_path: str) -> str:
    """Detect architecture layer violations using Rust."""
    from _core._core import layers
    return layers(db_path)


def rust_clones(db_path: str) -> str:
    """Detect code clones using Rust."""
    from _core._core import clones
    return clones(db_path)


def rust_community(db_path: str) -> str:
    """Detect communities in call graph using Rust."""
    from _core._core import community
    return community(db_path)


def rust_centrality(db_path: str) -> str:
    """Compute graph centrality using Rust."""
    from _core._core import centrality
    return centrality(db_path)


def rust_dead_code(db_path: str) -> str:
    """Detect potential dead code using Rust."""
    from _core._core import dead_code
    return dead_code(db_path)


def rust_entry_points(db_path: str) -> str:
    """Identify project entry points using Rust."""
    from _core._core import entry_points
    return entry_points(db_path)


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


def rust_snapshot_diff(db_path: str, a: str, b: str) -> str:
    """Compare two snapshots using Rust."""
    from _core._core import snapshot_diff
    return snapshot_diff(db_path, a, b)


def rust_lsp_setup(db_path: str) -> str:
    """Detect installed LSP servers using Rust."""
    from _core._core import lsp_setup
    return lsp_setup(db_path)


def rust_federate_search(db_path: str, query: str) -> str:
    """Cross-repo federated search using Rust."""
    from _core._core import federate_search
    return federate_search(db_path, query)
