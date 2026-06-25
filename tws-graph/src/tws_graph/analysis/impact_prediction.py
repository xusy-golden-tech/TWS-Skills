"""P40 v5.5.0 — Impact Prediction Engine.

Pre-refactor risk assessment combining impact analysis,
test coverage mapping, and complexity metrics.

Usage::

    from tws_graph.analysis.impact_prediction import predict_impact
    result = predict_impact(queries, node_id, depth=3)
    print(f"Risk: {result['risk_score']}/100, {result['total_affected']} affected")
"""

from __future__ import annotations
from collections import deque

from .test_coverage import is_test_file


def predict_impact(queries, symbol_id: str, depth: int = 3) -> dict:
    """Predict the impact of changing a symbol.

    Combines:
    - Direct dependents (inbound calls, depth 1)
    - Indirect dependents (transitive inbound calls, depth 2+)
    - Affected tests (test functions that cover affected nodes)
    - Risk score (0-100)

    Args:
        queries: QueryBuilder instance.
        symbol_id: Node ID of the symbol to analyze.
        depth: Maximum depth for transitive impact analysis.

    Returns:
        {
            "symbol": {"id": str, "name": str, "qualified_name": str, "file_path": str},
            "direct_dependents": [{"id": str, "name": str, ...}, ...],
            "indirect_dependents": [{"id": str, "name": str, ...}, ...],
            "affected_tests": [{"id": str, "name": str, ...}, ...],
            "affected_files": [str, ...],
            "direct_count": int,
            "indirect_count": int,
            "total_affected": int,
            "risk_score": int,
        }
    """
    empty = {
        "symbol": None,
        "direct_dependents": [],
        "indirect_dependents": [],
        "affected_tests": [],
        "affected_files": [],
        "direct_count": 0,
        "indirect_count": 0,
        "total_affected": 0,
        "risk_score": 0,
    }

    # Look up the symbol
    sym = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind "
        "FROM nodes WHERE id = ?", (symbol_id,)
    ).fetchone()
    if not sym:
        return empty

    symbol = dict(sym)

    # Build reverse adjacency (who calls whom)
    call_edges = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'calls'"
    ).fetchall()

    if not call_edges:
        return {**empty, "symbol": symbol}

    # reverse_adj[tgt] = {src1, src2, ...}
    reverse_adj: dict[str, set[str]] = {}
    forward_adj: dict[str, set[str]] = {}
    all_callers: set[str] = set()
    all_callees: set[str] = set()

    for e in call_edges:
        src, tgt = e["source"], e["target"]
        reverse_adj.setdefault(tgt, set()).add(src)
        forward_adj.setdefault(src, set()).add(tgt)
        all_callers.add(src)
        all_callees.add(tgt)

    # BFS inbound from symbol_id
    visited: dict[str, int] = {}  # node_id → depth
    queue: deque[str] = deque([symbol_id])
    visited[symbol_id] = 0

    while queue:
        current = queue.popleft()
        cur_depth = visited[current]
        if cur_depth >= depth:
            continue
        for caller in reverse_adj.get(current, set()):
            new_depth = cur_depth + 1
            if caller not in visited or visited[caller] > new_depth:
                visited[caller] = new_depth
                queue.append(caller)

    # Separate direct (depth=1) and indirect (depth>=2)
    direct_ids = {n for n, d in visited.items() if d == 1}
    indirect_ids = {n for n, d in visited.items() if d >= 2}

    # Load node info for direct and indirect dependents
    all_affected_ids = direct_ids | indirect_ids
    all_affected_ids_list = list(all_affected_ids)

    if not all_affected_ids_list:
        return {**empty, "symbol": symbol}

    # Batch load affected node info
    affected_info = _batch_load_nodes(queries, all_affected_ids_list)
    direct_deps = [affected_info[nid] for nid in direct_ids if nid in affected_info]
    indirect_deps = [affected_info[nid] for nid in indirect_ids if nid in affected_info]

    # Affected files
    affected_files_set: set[str] = set()
    for info in affected_info.values():
        fp = info.get("file_path", "")
        if fp and not is_test_file(fp):
            affected_files_set.add(fp)
    affected_files = sorted(affected_files_set)

    # Affected tests: test functions that call into the symbol or affected nodes
    affected_tests = _find_affected_tests(
        queries, all_affected_ids | {symbol_id}, forward_adj, reverse_adj
    )

    # Risk score based on fan-out × affected_count
    fan_out = len(forward_adj.get(symbol_id, set()))
    risk = min(100, int(
        (len(all_affected_ids) * 5) +       # each affected node = 5 points
        (fan_out * 10) +                      # each direct callee = 10 points
        (len(indirect_ids) * 3) +             # indirect = 3 points
        (len(affected_files) * 2)             # files = 2 points
    ))

    return {
        "symbol": symbol,
        "direct_dependents": direct_deps,
        "indirect_dependents": indirect_deps,
        "affected_tests": affected_tests,
        "affected_files": affected_files,
        "direct_count": len(direct_ids),
        "indirect_count": len(indirect_ids),
        "total_affected": len(all_affected_ids),
        "risk_score": risk,
    }


def _batch_load_nodes(queries, node_ids: list[str]) -> dict[str, dict]:
    """Load node info for a list of IDs."""
    if not node_ids:
        return {}
    placeholders = ",".join("?" * len(node_ids))
    rows = queries._exec(
        f"SELECT id, name, qualified_name, file_path, kind, start_line "
        f"FROM nodes WHERE id IN ({placeholders})",
        tuple(node_ids),
    ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def _find_affected_tests(
    queries,
    affected_ids: set[str],
    forward_adj: dict[str, set[str]],
    reverse_adj: dict[str, set[str]],
) -> list[dict]:
    """Find test functions that are affected by changes to the given nodes."""
    if not affected_ids:
        return []

    # Find all test nodes (functions in test files)
    all_test_nodes = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, start_line FROM nodes WHERE kind IN ('function','method')"
    ).fetchall()
    test_nodes = {
        r["id"]: dict(r)
        for r in all_test_nodes
        if is_test_file(r["file_path"] or "")
    }

    if not test_nodes:
        return []

    affected_test_ids: set[str] = set()

    # Strategy 1: Tests that directly call affected nodes
    for test_id in test_nodes:
        callees = forward_adj.get(test_id, set())
        if callees & affected_ids:
            affected_test_ids.add(test_id)

    # Strategy 2: Tests that are called by affected nodes (reverse: test_edge pattern)
    for affected_id in affected_ids:
        callees = forward_adj.get(affected_id, set())
        for callee in callees:
            if callee in test_nodes:
                affected_test_ids.add(callee)

    return [
        {"id": tid, **test_nodes[tid]}
        for tid in affected_test_ids
        if tid in test_nodes
    ]
