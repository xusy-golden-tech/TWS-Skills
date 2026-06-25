"""Graph export (P32 v5.3.0).

Export the code symbol graph to standard formats:
- DOT (Graphviz) — for rendering dependency diagrams
- Mermaid — for embedding in Markdown documents
- JSON — for programmatic processing
"""

from __future__ import annotations


def export_dot(
    queries,
    from_node: str | None = None,
    depth: int = 5,
    kind: str | None = None,
    limit: int = 500,
) -> str:
    """Export graph as Graphviz DOT format.

    Args:
        queries: QueryBuilder instance.
        from_node: Optional node ID — export subgraph centered on this node.
        depth: BFS depth when ``from_node`` is specified.
        kind: Optional edge kind filter (e.g. ``"calls"``).

    Returns:
        DOT-format string.
    """
    nodes, edges = _load_graph(queries, from_node, depth, kind, limit)

    lines = ["digraph code_graph {"]
    lines.append("  rankdir=LR;")
    lines.append("  node [shape=box, style=rounded];")

    # Emit nodes
    emitted: set[str] = set()
    for n in nodes:
        nid = n["id"]
        if nid in emitted:
            continue
        emitted.add(nid)
        label = _sanitize_dot_label(f"{n['name']}\\n({n.get('kind','?')})")
        lines.append(f'  "{nid}" [label="{label}"];')

    # Emit edges
    for e in edges:
        src, tgt = e["source"], e["target"]
        # Only emit edges where both endpoints are in the node set
        if src not in emitted:
            emitted.add(src)
            # Lookup source node
            src_node = _lookup_node(queries, src)
            if src_node:
                label = _sanitize_dot_label(f"{src_node['name']}\\n({src_node.get('kind','?')})")
                lines.append(f'  "{src}" [label="{label}"];')

        if tgt not in emitted:
            emitted.add(tgt)
            tgt_node = _lookup_node(queries, tgt)
            if tgt_node:
                label = _sanitize_dot_label(f"{tgt_node['name']}\\n({tgt_node.get('kind','?')})")
                lines.append(f'  "{tgt}" [label="{label}"];')

        edge_kind = e.get("kind", "")
        lines.append(f'  "{src}" -> "{tgt}" [label="{edge_kind}"];')

    lines.append("}")
    return "\n".join(lines)


def export_mermaid(
    queries,
    from_node: str | None = None,
    depth: int = 5,
    kind: str | None = None,
    limit: int = 500,
) -> str:
    """Export graph as Mermaid flowchart.

    Args:
        queries: QueryBuilder instance.
        from_node: Optional node ID for subgraph export.
        depth: BFS depth limit.
        kind: Optional edge kind filter.
        limit: Maximum number of edges to export.

    Returns:
        Mermaid-format string.
    """
    nodes, edges = _load_graph(queries, from_node, depth, kind, limit)

    lines = ["flowchart LR"]

    # Build node ID → safe alias mapping (Mermaid doesn't like special chars)
    aliases: dict[str, str] = {}
    for i, n in enumerate(nodes):
        aliases[n["id"]] = f"N{i}"

    # Emit nodes
    for n in nodes:
        alias = aliases[n["id"]]
        label = _sanitize_mermaid_label(f"{n['name']}<br/>({n.get('kind','?')})")
        lines.append(f"  {alias}[\"{label}\"]")

    # Emit edges
    for e in edges:
        src_alias = aliases.get(e["source"], e["source"][:12])
        tgt_alias = aliases.get(e["target"], e["target"][:12])
        edge_kind = e.get("kind", "")
        lines.append(f"  {src_alias} -->|{edge_kind}| {tgt_alias}")

    return "\n".join(lines)


def export_json(
    queries,
    from_node: str | None = None,
    depth: int = 5,
    kind: str | None = None,
    limit: int = 500,
) -> dict:
    """Export graph as JSON-serializable dict.

    Args:
        queries: QueryBuilder instance.
        from_node: Optional node ID for subgraph export.
        depth: BFS depth limit.
        kind: Optional edge kind filter.
        limit: Maximum number of edges to export.

    Returns:
        ``{"nodes": [...], "edges": [...]}``
    """
    nodes, edges = _load_graph(queries, from_node, depth, kind, limit)

    return {
        "nodes": [
            {
                "id": n["id"],
                "name": n["name"],
                "kind": n.get("kind", ""),
                "qualified_name": n.get("qualified_name", ""),
                "file_path": n.get("file_path", ""),
                "language": n.get("language", ""),
                "start_line": n.get("start_line", 0),
                "end_line": n.get("end_line", 0),
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": e["source"],
                "target": e["target"],
                "kind": e.get("kind", ""),
                "source_loc": e.get("source_loc", ""),
                "provenance": e.get("provenance", ""),
            }
            for e in edges
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_graph(queries, from_node=None, depth=5, kind=None, limit=500):
    """Load nodes and edges, optionally scoped to a subgraph."""
    if from_node:
        return _load_subgraph(queries, from_node, depth, kind)
    else:
        return _load_full_graph(queries, kind, limit)


def _load_full_graph(queries, kind=None, limit=500):
    """Load all nodes and edges, capped by limit."""
    nodes = [dict(r) for r in queries._exec(
        "SELECT * FROM nodes LIMIT ?", (limit,)
    ).fetchall()]
    if kind:
        edges = [dict(r) for r in queries._exec(
            "SELECT * FROM edges WHERE kind = ? LIMIT ?", (kind, limit)
        ).fetchall()]
    else:
        edges = [dict(r) for r in queries._exec(
            "SELECT * FROM edges LIMIT ?", (limit,)
        ).fetchall()]
    return nodes, edges


def _load_subgraph(queries, from_node, depth, kind=None):
    """BFS from from_node up to depth, collecting nodes and edges."""
    from collections import deque

    kind_clause = "AND e.kind = ?" if kind else ""
    kind_params = [kind] if kind else []

    visited: set[str] = {from_node}
    node_rows: dict[str, dict] = {}
    edge_rows: list[dict] = []
    queue: deque[tuple[str, int]] = deque([(from_node, 0)])

    # Load the root node
    root = queries._exec("SELECT * FROM nodes WHERE id = ?", (from_node,)).fetchone()
    if root:
        node_rows[from_node] = dict(root)

    while queue:
        current, d = queue.popleft()
        if d >= depth:
            continue

        # Get outgoing edges
        params = [current] + kind_params
        edges = queries._exec(
            f"SELECT e.* FROM edges e WHERE e.source = ? {kind_clause}", params
        ).fetchall()

        for e in edges:
            edge_rows.append(dict(e))
            neighbor = e["target"]
            if neighbor not in visited:
                visited.add(neighbor)
                n = queries._exec("SELECT * FROM nodes WHERE id = ?", (neighbor,)).fetchone()
                if n:
                    node_rows[neighbor] = dict(n)
                queue.append((neighbor, d + 1))

    return list(node_rows.values()), edge_rows


def _lookup_node(queries, node_id: str) -> dict | None:
    """Quick node lookup by ID."""
    row = queries._exec("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return dict(row) if row else None


def _sanitize_dot_label(text: str) -> str:
    """Escape special characters for DOT labels."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _sanitize_mermaid_label(text: str) -> str:
    """Escape special characters for Mermaid labels."""
    return text.replace('"', "'").replace("\n", " ")
