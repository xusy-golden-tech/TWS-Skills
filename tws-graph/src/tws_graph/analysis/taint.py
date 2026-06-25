"""Security taint analysis (P31 v5.3.0).

Source→sink path tracking through data_flows edges.
BFS traversal from each identified source node, following data_flows
edges to find reachable sink nodes.

Usage::

    from tws_graph.analysis.taint import find_taint_paths, find_sources, find_sinks
    paths = find_taint_paths(queries, max_depth=5)
"""

from __future__ import annotations
from collections import deque

# ---------------------------------------------------------------------------
# Source patterns — sensitive data origins
# ---------------------------------------------------------------------------

_SOURCE_PATTERNS: frozenset[str] = frozenset({
    # Python
    "os::environ", "os::environ::get", "os::getenv",
    "input",
    "open",  # file read when mode='r'
    "pathlib::Path::read_text", "pathlib::Path::read_bytes",
    "json::load", "json::loads",
    "tomllib::load", "configparser::ConfigParser::read",
    "requests::Response::json", "requests::get", "requests::post",
    "flask::request::get_json", "flask::request::args",
    "django::http::HttpRequest::GET", "django::http::HttpRequest::POST",
    # TypeScript/JavaScript
    "process::env",
    "fetch", "window::fetch",
    "fs::readFileSync", "fs::readFile",
    "document::getElementById",
    "express::req::body", "express::req::query", "express::req::params",
    # General
    "read", "readline", "readlines",
    "sys::stdin::read", "sys::stdin::readline",
})


def is_source_node(qualified_name: str) -> bool:
    """Check if a node's qualified_name matches a known source pattern.

    Matching is suffix-based: ``os::environ::get`` matches because it
    ends with ``os::environ::get``.
    """
    if not qualified_name:
        return False
    for pattern in _SOURCE_PATTERNS:
        if qualified_name.endswith(pattern) or qualified_name == pattern:
            return True
    return False


# ---------------------------------------------------------------------------
# Sink patterns — dangerous operations
# ---------------------------------------------------------------------------

_SINK_PATTERNS: frozenset[str] = frozenset({
    # Python command execution
    "os::system", "os::popen", "os::execv", "os::execve",
    "subprocess::run", "subprocess::call", "subprocess::Popen",
    "subprocess::check_output", "subprocess::check_call",
    # Python code injection
    "eval", "exec", "compile",
    # Python SQL
    "execute", "executemany", "executescript",
    "cursor::execute", "Cursor::execute",
    "connection::execute", "Connection::execute",
    # Python file write
    "open",  # when mode='w' or 'a'
    "write", "writelines",
    # Python network (data exfiltration sink)
    "requests::post", "requests::put", "requests::patch",
    "httpx::post", "httpx::Client::post",
    "smtplib::SMTP::sendmail",
    # TypeScript/JavaScript
    "child_process::exec", "child_process::execSync",
    "child_process::spawn", "child_process::fork",
    "eval", "Function",
    "document::write", "innerHTML",
    "fetch", "XMLHttpRequest::send",
    # SQL (TS/JS)
    "query",  # db.query()
    "connection::query", "pool::query",
    # File write (TS/JS)
    "fs::writeFileSync", "fs::writeFile",
    # General
    "send",  # network send
})


def is_sink_node(qualified_name: str) -> bool:
    """Check if a node's qualified_name matches a known sink pattern."""
    if not qualified_name:
        return False
    for pattern in _SINK_PATTERNS:
        if qualified_name.endswith(pattern) or qualified_name == pattern:
            return True
    return False


# ---------------------------------------------------------------------------
# Source/Sink finding in graph
# ---------------------------------------------------------------------------

def find_sources(queries) -> list[dict]:
    """Find all source nodes in the graph.

    Returns list of dicts with keys: id, name, qualified_name, file_path.
    """
    all_nodes = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, start_line "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    sources: list[dict] = []
    for row in all_nodes:
        qn = row["qualified_name"]
        if qn and is_source_node(qn):
            sources.append({
                "id": row["id"],
                "name": row["name"],
                "qualified_name": qn,
                "file_path": row["file_path"],
                "kind": row["kind"],
                "line": row["start_line"],
            })
    return sources


def find_sinks(queries) -> list[dict]:
    """Find all sink nodes in the graph."""
    all_nodes = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, start_line "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    sinks: list[dict] = []
    for row in all_nodes:
        qn = row["qualified_name"]
        if qn and is_sink_node(qn):
            sinks.append({
                "id": row["id"],
                "name": row["name"],
                "qualified_name": qn,
                "file_path": row["file_path"],
                "kind": row["kind"],
                "line": row["start_line"],
            })
    return sinks


# ---------------------------------------------------------------------------
# BFS path finding
# ---------------------------------------------------------------------------

def find_taint_paths(
    queries,
    source_fn=None,
    sink_fn=None,
    max_depth: int = 5,
) -> list[dict]:
    """Find taint paths from source nodes to sink nodes through data_flows edges.

    1. Identify source nodes and sink nodes.
    2. Build adjacency list from all ``data_flows`` edges.
    3. For each source, BFS to find reachable sinks within max_depth.

    Args:
        queries: QueryBuilder instance.
        source_fn: Optional callable ``(qualified_name) -> bool`` for custom source detection.
        sink_fn: Optional callable ``(qualified_name) -> bool`` for custom sink detection.
        max_depth: Maximum BFS depth (edges traversed).

    Returns:
        List of path dicts, each with ``path`` (list of node dicts) and
        ``source_type`` / ``sink_type`` labels.
    """
    _is_source = source_fn if source_fn else is_source_node
    _is_sink = sink_fn if sink_fn else is_sink_node

    # Build adjacency list from data_flows edges
    edges = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'data_flows'"
    ).fetchall()

    if not edges:
        return []

    adjacency: dict[str, set[str]] = {}
    node_set: set[str] = set()
    for row in edges:
        src, tgt = row["source"], row["target"]
        node_set.add(src)
        node_set.add(tgt)
        adjacency.setdefault(src, set()).add(tgt)

    # Batch-load node details
    node_info: dict[str, dict] = {}
    if node_set:
        batch = list(node_set)
        # Process in chunks to avoid SQLite placeholder limits
        chunk_size = 900
        for i in range(0, len(batch), chunk_size):
            chunk = batch[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = queries._exec(
                f"SELECT id, name, qualified_name, file_path, kind, start_line "
                f"FROM nodes WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for nr in rows:
                node_info[nr["id"]] = {
                    "id": nr["id"],
                    "name": nr["name"],
                    "qualified_name": nr["qualified_name"],
                    "file_path": nr["file_path"],
                    "kind": nr["kind"],
                    "line": nr["start_line"],
                }

    # Identify sources and sinks
    source_ids: list[str] = []
    sink_ids: set[str] = set()
    for nid, info in node_info.items():
        qn = info.get("qualified_name", "")
        if qn and _is_source(qn):
            source_ids.append(nid)
        if qn and _is_sink(qn):
            sink_ids.add(nid)

    if not source_ids or not sink_ids:
        return []

    # BFS from each source
    paths: list[dict] = []
    seen_paths: set[tuple[str, str]] = set()  # (source_id, sink_id) deduplication

    for src_id in source_ids:
        # BFS
        visited: dict[str, str | None] = {src_id: None}  # node → parent
        queue: deque[tuple[str, int]] = deque([(src_id, 0)])

        while queue:
            current, depth = queue.popleft()

            # Check if current node is a sink (regardless of depth)
            if current in sink_ids and current != src_id:
                # Reconstruct path
                path_nodes: list[dict] = []
                node = current
                while node is not None:
                    info = node_info.get(node)
                    if info:
                        path_nodes.append(dict(info))
                    else:
                        path_nodes.append({"id": node, "name": "?", "qualified_name": "?"})
                    node = visited.get(node)
                path_nodes.reverse()

                src_info = node_info.get(src_id, {})
                sink_info = node_info.get(current, {})

                edge_key = (src_id, current)
                if edge_key not in seen_paths:
                    seen_paths.add(edge_key)
                    paths.append({
                        "path": path_nodes,
                        "source": src_info.get("qualified_name", src_id),
                        "sink": sink_info.get("qualified_name", current),
                        "source_type": _classify_source(src_info.get("qualified_name", "")),
                        "sink_type": _classify_sink(sink_info.get("qualified_name", "")),
                        "depth": depth,
                    })

            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited and depth < max_depth:
                    visited[neighbor] = current
                    queue.append((neighbor, depth + 1))

    return paths


def _classify_source(qname: str) -> str:
    """Classify a source node by data origin type."""
    if any(p in qname for p in ("environ", "getenv", "env", "process.env")):
        return "environment"
    if any(p in qname for p in ("input", "readline", "stdin")):
        return "user_input"
    if any(p in qname for p in ("open", "read", "load")):
        return "file_read"
    if any(p in qname for p in ("get_json", "req.body", "request")):
        return "http_request"
    return "unknown"


def _classify_sink(qname: str) -> str:
    """Classify a sink node by danger type."""
    if any(p in qname for p in ("system", "popen", "execv", "subprocess",
                                 "child_process", "spawn", "fork")):
        return "command_execution"
    if any(p in qname for p in ("eval", "exec", "compile", "Function")):
        return "code_injection"
    if any(p in qname for p in ("execute", "executemany", "query")):
        return "sql"
    if any(p in qname for p in ("write", "writeFile", "innerHTML")):
        return "file_or_dom_write"
    if any(p in qname for p in ("post", "sendmail", "fetch", "send")):
        return "data_exfiltration"
    return "unknown"
