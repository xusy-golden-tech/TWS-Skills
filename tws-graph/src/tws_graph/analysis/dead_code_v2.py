"""Dead code detection v2 (P36 v5.4.0).

Cross-file reachability analysis from entry points.
Classifies dead code as unreachable (no call path from any entry)
vs unused (reachable but no external callers).

Usage::

    from tws_graph.analysis.dead_code_v2 import detect_dead_code
    result = detect_dead_code(queries)
    print(f"Dead: {result['dead_count']}, Live: {result['live_count']}")
"""

from __future__ import annotations
from collections import deque

from .test_coverage import is_test_file


# ---------------------------------------------------------------------------
# P36a: Entry point detection
# ---------------------------------------------------------------------------

# Known entry point patterns by language
_ENTRY_NAME_PATTERNS: frozenset[str] = frozenset({
    "main", "__main__", "run", "start", "serve", "create_app",
    "handler", "handle", "handlerFunc", "mainCRTStartup",
    "WinMain", "wWinMain", "DllMain",
})

_ENTRY_DECORATOR_PATTERNS: frozenset[str] = frozenset({
    "click.command", "click.group",
    "typer", "app.command",
    "argparse",
    "flask.cli", "flask.route",
    "fastapi",
    "celery.task",
    "spring", "RestController", "GetMapping", "PostMapping",
    "express", "router.get", "router.post",
})


def find_entry_points(queries) -> set[str]:
    """Find likely entry point nodes in the graph.

    Uses heuristics:
    1. Functions named ``main`` / ``run`` / ``start`` etc.
    2. Functions with CLI/web framework decorators
    3. Route kind nodes
    4. Functions with no inbound calls (leaf producers)
    """
    entries: set[str] = set()

    # 1. Name-based entry points
    all_functions = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, decorators "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    if not all_functions:
        return entries

    all_ids: set[str] = set()
    for row in all_functions:
        nid = row["id"]
        all_ids.add(nid)
        name = row["name"] or ""

        # Name heuristic
        if name in _ENTRY_NAME_PATTERNS:
            # Exclude test files from entry points
            if not is_test_file(row["file_path"]):
                entries.add(nid)
                continue

        # Decorator heuristic
        decorators = row["decorators"] or ""
        if decorators:
            for pat in _ENTRY_DECORATOR_PATTERNS:
                if pat.lower() in decorators.lower():
                    if not is_test_file(row["file_path"]):
                        entries.add(nid)
                        break

        # Route kind
        if row["kind"] == "route":
            if not is_test_file(row["file_path"]):
                entries.add(nid)

    # 2. Functions with zero inbound calls but have outbound calls (leaf entry points)
    # Only if we have very few name-based entries and many nodes
    if len(entries) < max(5, len(all_ids) // 100):
        for row in all_functions:
            nid = row["id"]
            if nid in entries or is_test_file(row["file_path"]):
                continue
            inbound = queries._exec(
                "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND target = ?",
                (nid,),
            ).fetchone()
            if inbound and inbound["cnt"] == 0:
                # Only consider as entry if it has outbound calls (not isolated)
                outbound = queries._exec(
                    "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND source = ?",
                    (nid,),
                ).fetchone()
                if outbound and outbound["cnt"] > 0:
                    entries.add(nid)

    return entries


# ---------------------------------------------------------------------------
# P36a: Reachability analysis (BFS)
# ---------------------------------------------------------------------------


def find_reachable(queries, entry_ids: set[str]) -> set[str]:
    """BFS from entry points through calls edges to find reachable nodes.

    Returns set of all reachable node IDs.
    """
    if not entry_ids:
        return set()

    # Build adjacency list from calls edges
    edges = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'calls'"
    ).fetchall()

    if not edges:
        return entry_ids.copy()

    adjacency: dict[str, set[str]] = {}
    for e in edges:
        src, tgt = e["source"], e["target"]
        adjacency.setdefault(src, set()).add(tgt)

    # BFS
    visited: set[str] = set()
    queue: deque[str] = deque(entry_ids)

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adjacency.get(current, set()):
            if neighbor not in visited:
                queue.append(neighbor)

    return visited


# ---------------------------------------------------------------------------
# P36b + P36c: Dead code detection and report
# ---------------------------------------------------------------------------


def detect_dead_code(queries) -> dict:
    """Detect dead code via cross-file reachability analysis.

    Returns:
        {
            "dead_nodes": [node_id, ...],
            "dead_details": [{"name": ..., "file_path": ..., "reason": ...}, ...],
            "live_count": int,
            "dead_count": int,
            "total_analyzed": int,
            "by_file": {file_path: [node_ids]},
        }
    """
    # Get all function/method nodes (exclude test files)
    all_nodes = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, start_line "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    if not all_nodes:
        return {
            "dead_nodes": [],
            "dead_details": [],
            "live_count": 0,
            "dead_count": 0,
            "total_analyzed": 0,
            "by_file": {},
        }

    # Separate production and test nodes
    prod_nodes: dict[str, dict] = {}
    for row in all_nodes:
        nid = row["id"]
        fpath = row["file_path"] or ""
        if not is_test_file(fpath):
            prod_nodes[nid] = {
                "id": nid,
                "name": row["name"],
                "qualified_name": row["qualified_name"],
                "file_path": fpath,
                "kind": row["kind"],
                "line": row["start_line"],
            }

    if not prod_nodes:
        return {
            "dead_nodes": [],
            "dead_details": [],
            "live_count": 0,
            "dead_count": 0,
            "total_analyzed": 0,
            "by_file": {},
        }

    # Find entry points and reachable set
    entries = find_entry_points(queries)
    # Only consider entries that are in prod_nodes
    prod_entries = entries & set(prod_nodes.keys())

    if not prod_entries:
        # Fallback: functions with no inbound but have outbound calls are entries
        all_prod_ids = set(prod_nodes.keys())
        prod_entries = set()
        for row in all_nodes:
            nid = row["id"]
            if nid not in all_prod_ids:
                continue
            inbound = queries._exec(
                "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND target = ?",
                (nid,),
            ).fetchone()
            if inbound and inbound["cnt"] == 0:
                # Must have outbound calls to be a valid entry
                outbound = queries._exec(
                    "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND source = ?",
                    (nid,),
                ).fetchone()
                if outbound and outbound["cnt"] > 0:
                    prod_entries.add(nid)

    reachable = find_reachable(queries, prod_entries)

    # Dead = production nodes NOT reachable
    dead_ids: list[str] = []
    dead_details: list[dict] = []
    by_file: dict[str, list[str]] = {}

    for nid, info in prod_nodes.items():
        if nid not in reachable:
            # Determine reason
            has_inbound = queries._exec(
                "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND target = ?",
                (nid,),
            ).fetchone()
            has_outbound = queries._exec(
                "SELECT COUNT(*) as cnt FROM edges WHERE kind = 'calls' AND source = ?",
                (nid,),
            ).fetchone()

            if has_outbound and has_outbound["cnt"] > 0:
                reason = "unreachable"  # calls others but no call path from entry
            elif has_inbound and has_inbound["cnt"] > 0:
                reason = "unreachable_from_entry"  # has callers but not from entry chain
            else:
                reason = "no_callers"  # completely disconnected

            dead_ids.append(nid)
            detail = dict(info)
            detail["reason"] = reason
            dead_details.append(detail)

            fp = info["file_path"]
            by_file.setdefault(fp, []).append(nid)

    live_count = len(prod_nodes) - len(dead_ids)

    return {
        "dead_nodes": dead_ids,
        "dead_details": dead_details,
        "live_count": live_count,
        "dead_count": len(dead_ids),
        "total_analyzed": len(prod_nodes),
        "by_file": by_file,
    }
