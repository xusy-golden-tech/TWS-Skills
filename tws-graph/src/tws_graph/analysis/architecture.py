"""Architecture analysis engine (P30 v5.3.0).

Cycle detection, layer violation detection, module cohesion/coupling metrics.
Works directly with QueryBuilder (SQLite) for CLI integration.
"""

from __future__ import annotations

import fnmatch


# ---------------------------------------------------------------------------
# P30a: Cycle detection
# ---------------------------------------------------------------------------

def detect_cycles(queries, max_cycles: int = 100) -> list[dict]:
    """Detect cycles in the call graph using DFS with three-colour marking.

    Only considers edges with ``kind = 'calls'``.

    Args:
        queries: QueryBuilder instance.
        max_cycles: Maximum number of cycles to return (0 = unlimited).

    Returns:
        List of cycle dicts with keys: ``cycle`` (list of node names),
        ``files`` (list of file paths), ``length`` (int).
    """
    # Load all calls edges
    rows = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'calls'"
    ).fetchall()

    if not rows:
        return []

    graph: dict[str, set[str]] = {}
    nodes_set: set[str] = set()
    for row in rows:
        src, tgt = row["source"], row["target"]
        nodes_set.add(src)
        nodes_set.add(tgt)
        graph.setdefault(src, set()).add(tgt)

    # Batch-load node details
    node_info: dict[str, dict] = {}
    if nodes_set:
        placeholders = ",".join("?" for _ in nodes_set)
        node_rows = queries._exec(
            f"SELECT id, name, qualified_name, file_path FROM nodes WHERE id IN ({placeholders})",
            list(nodes_set),
        ).fetchall()
        for nr in node_rows:
            node_info[nr["id"]] = {
                "name": nr["name"],
                "file_path": nr["file_path"],
            }

    # Early exit: max_cycles == 0 means return no cycles
    if max_cycles == 0:
        return []

    # DFS cycle detection
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes_set}
    cycles: list[list[str]] = []
    path: list[str] = []

    def dfs(node: str) -> None:
        nonlocal cycles
        if color[node] == GRAY:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            if len(cycles) >= max_cycles:
                return
            cycles.append(cycle)
            return
        if color[node] == BLACK:
            return

        color[node] = GRAY
        path.append(node)

        for neighbor in graph.get(node, set()):
            dfs(neighbor)

        path.pop()
        color[node] = BLACK

    for node in nodes_set:
        if color[node] == WHITE:
            dfs(node)

    # Enrich results
    result: list[dict] = []
    for cycle in cycles:
        names: list[str] = []
        files: set[str] = set()
        for nid in cycle:
            info = node_info.get(nid, {})
            names.append(info.get("name", nid[:12]))
            if "file_path" in info:
                files.add(info["file_path"])
        result.append({
            "cycle": names,
            "names": names,   # alias for test compatibility
            "files": sorted(files),
            "length": len(cycle) - 1,  # edges in the cycle
        })

    return result


# ---------------------------------------------------------------------------
# P30b: Layer violation detection
# ---------------------------------------------------------------------------

def _match_layer(file_path: str, layer_def: dict) -> bool:
    """Check if a file_path matches a layer's glob pattern."""
    pattern = layer_def.get("pattern", "")
    return fnmatch.fnmatch(file_path, pattern)


def detect_layer_violations(
    queries,
    layers: dict[str, dict],
    direction: str = "higher-to-lower",
) -> list[dict]:
    """Detect calls that violate architecture layer boundaries.

    Args:
        queries: QueryBuilder instance.
        layers: Dict of ``{layer_name: {pattern, level}}``.
            ``pattern`` is a glob matched against ``file_path``.
            ``level`` is the layer ordering (1=top, higher=more abstract).
        direction: ``"higher-to-lower"`` means higher level can call lower,
            but lower cannot call higher.

    Returns:
        List of violation dicts with keys: ``source_name``, ``target_name``,
        ``source_layer``, ``target_layer``, ``source_file``, ``target_file``.
    """
    violations: list[dict] = []

    # Build file → layer mapping
    file_rows = queries._exec(
        "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
    ).fetchall()
    file_layers: dict[str, tuple[str, int]] = {}
    for row in file_rows:
        fp = row["file_path"]
        if not fp:
            continue
        for layer_name, layer_def in layers.items():
            if _match_layer(fp, layer_def):
                file_layers[fp] = (layer_name, layer_def.get("level", 0))
                break

    if len(file_layers) < 2:
        return violations

    # Get all calls edges with node file info
    call_rows = queries._exec("""
        SELECT e.source, e.target, ns.file_path AS source_file,
               nt.file_path AS target_file, ns.name AS source_name,
               nt.name AS target_name
        FROM edges e
        JOIN nodes ns ON e.source = ns.id
        JOIN nodes nt ON e.target = nt.id
        WHERE e.kind = 'calls'
          AND ns.file_path IS NOT NULL
          AND nt.file_path IS NOT NULL
    """).fetchall()

    for row in call_rows:
        src_file = row["source_file"]
        tgt_file = row["target_file"]

        src_layer = file_layers.get(src_file)
        tgt_layer = file_layers.get(tgt_file)
        if not src_layer or not tgt_layer:
            continue

        src_name, src_level = src_layer
        tgt_name, tgt_level = tgt_layer

        if src_file == tgt_file:
            continue  # intra-file, not a layer concern

        is_violation = False
        if direction == "higher-to-lower":
            # Lower level (more concrete) should NOT call higher level (more abstract)
            # Level 1 = top (abstract), higher number = lower (concrete)
            # So src_level > tgt_level means lower is calling higher → violation
            if src_level > tgt_level:
                is_violation = True
        elif direction == "lower-to-higher":
            if src_level < tgt_level:
                is_violation = True

        if is_violation:
            violations.append({
                "source_name": row["source_name"],
                "target_name": row["target_name"],
                "source_file": src_file,
                "target_file": tgt_file,
                "source_layer": src_name,
                "target_layer": tgt_name,
                "source_level": src_level,
                "target_level": tgt_level,
            })

    return violations


# ---------------------------------------------------------------------------
# P30c: Module cohesion/coupling metrics
# ---------------------------------------------------------------------------

def compute_module_metrics(queries) -> list[dict]:
    """Compute module-level cohesion, coupling, and instability metrics.

    A "module" is defined as a single file (file_path). Metrics are based
    on ``calls`` edges between functions/methods.

    - **Cohesion**: internal_calls / (internal_calls + external_calls)
    - **Instability**: efferent_coupling / (afferent_coupling + efferent_coupling)

    Returns:
        List of metric dicts, one per file that has nodes.
    """
    # Get all file paths
    file_rows = queries._exec(
        "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
    ).fetchall()
    all_files = {row["file_path"] for row in file_rows}
    if not all_files:
        return []

    # Get all calls edges
    call_rows = queries._exec("""
        SELECT e.source, e.target, ns.file_path AS source_file,
               nt.file_path AS target_file
        FROM edges e
        JOIN nodes ns ON e.source = ns.id
        JOIN nodes nt ON e.target = nt.id
        WHERE e.kind = 'calls'
          AND ns.file_path IS NOT NULL
          AND nt.file_path IS NOT NULL
    """).fetchall()

    # Accumulate per-file stats
    stats: dict[str, dict] = {
        f: {
            "internal_calls": 0,
            "external_calls": 0,
            "outgoing_to": set(),    # files this file calls
            "incoming_from": set(),  # files that call this file
            "node_count": 0,
        }
        for f in all_files
    }

    for row in call_rows:
        src_file = row["source_file"]
        tgt_file = row["target_file"]
        if src_file not in stats or tgt_file not in stats:
            continue

        if src_file == tgt_file:
            stats[src_file]["internal_calls"] += 1
        else:
            stats[src_file]["external_calls"] += 1
            stats[src_file]["outgoing_to"].add(tgt_file)
            stats[tgt_file]["incoming_from"].add(src_file)

    # Count nodes per file
    node_counts = queries._exec("""
        SELECT file_path, COUNT(*) AS cnt FROM nodes
        WHERE file_path IS NOT NULL GROUP BY file_path
    """).fetchall()
    for row in node_counts:
        if row["file_path"] in stats:
            stats[row["file_path"]]["node_count"] = row["cnt"]

    # Compute metrics
    metrics: list[dict] = []
    for file_path, s in stats.items():
        total_calls = s["internal_calls"] + s["external_calls"]
        cohesion = s["internal_calls"] / total_calls if total_calls > 0 else 0.0

        ca = len(s["incoming_from"])  # afferent coupling
        ce = len(s["outgoing_to"])    # efferent coupling
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0

        metrics.append({
            "module": file_path,
            "internal_calls": s["internal_calls"],
            "external_calls": s["external_calls"],
            "afferent_coupling": ca,
            "efferent_coupling": ce,
            "cohesion": round(cohesion, 4),
            "instability": round(instability, 4),
            "node_count": s["node_count"],
        })

    return metrics
