"""P41 v5.5.0 — Code Health Scores.

Unified per-file quality scoring combining:
- Test coverage (from P35)
- Dead code ratio
- External coupling
- Function count (size proxy)

Usage::

    from tws_graph.analysis.code_health import compute_health_scores
    scores = compute_health_scores(queries)
    for s in scores[:5]:
        print(f"{s['file_path']}: {s['score']}/100")
"""

from __future__ import annotations

from .test_coverage import is_test_file


def compute_health_scores(queries) -> list[dict]:
    """Compute health scores for all production files.

    Score components:
    - Coverage (40%): percentage of functions with test coverage
    - Dead code (25%): percentage of functions that are NOT dead
    - Coupling (20%): lower external deps = higher score
    - Size (15%): number of functions (penalizes very large files)

    Returns:
        List of dicts sorted by score descending:
        {
            "file_path": str,
            "score": int (0-100),
            "func_count": int,
            "tested_count": int,
            "dead_count": int,
            "coverage_pct": float,
            "external_deps": int,
        }
    """
    # Get all production files with functions
    all_funcs = queries._exec(
        "SELECT id, name, file_path FROM nodes WHERE kind IN ('function','method')"
    ).fetchall()

    if not all_funcs:
        return []

    # Group by file, exclude tests
    files: dict[str, dict] = {}
    for f in all_funcs:
        fp = f["file_path"] or ""
        if is_test_file(fp):
            continue
        files.setdefault(fp, {
            "file_path": fp,
            "func_count": 0,
            "func_ids": [],
            "tested_count": 0,
            "dead_count": 0,
            "external_deps": 0,
        })
        files[fp]["func_count"] += 1
        files[fp]["func_ids"].append(f["id"])

    if not files:
        return []

    # Build test coverage: count functions that have inbound calls from test files
    all_test_funcs = queries._exec(
        "SELECT id FROM nodes WHERE kind IN ('function','method')"
    ).fetchall()
    test_func_ids: set[str] = set()
    for t in all_test_funcs:
        nid = t["id"]
        fp_row = queries._exec(
            "SELECT file_path FROM nodes WHERE id = ?", (nid,)
        ).fetchone()
        if fp_row and is_test_file(fp_row["file_path"] or ""):
            test_func_ids.add(nid)

    # Get calls from test functions
    if test_func_ids:
        placeholders = ",".join("?" * len(test_func_ids))
        test_calls = queries._exec(
            f"SELECT target FROM edges WHERE kind = 'calls' AND source IN ({placeholders})",
            tuple(test_func_ids),
        ).fetchall()
        tested_prod_ids: set[str] = {r["target"] for r in test_calls}
    else:
        tested_prod_ids = set()

    # Count tested functions per file
    for fp, info in files.items():
        info["tested_count"] = sum(
            1 for fid in info["func_ids"] if fid in tested_prod_ids
        )

    # Count dead functions (no inbound calls from non-test functions)
    all_calls = queries._exec(
        "SELECT target FROM edges WHERE kind = 'calls'"
    ).fetchall()
    called_ids: set[str] = set()
    for c in all_calls:
        tgt = c["target"]
        # Check if caller is not a test
        caller = queries._exec(
            "SELECT file_path FROM nodes WHERE id IN "
            "(SELECT source FROM edges WHERE kind = 'calls' AND target = ? LIMIT 1)",
            (tgt,),
        ).fetchone()
        if caller and not is_test_file(caller["file_path"] or ""):
            called_ids.add(tgt)

    for fp, info in files.items():
        info["dead_count"] = sum(
            1 for fid in info["func_ids"] if fid not in called_ids
        )

    # Count external dependencies (calls/imports to other files)
    for fp, info in files.items():
        deps = queries._exec(
            "SELECT COUNT(DISTINCT e.target) as cnt FROM edges e "
            "JOIN nodes nt ON e.target = nt.id "
            "WHERE e.kind IN ('calls','imports') "
            "AND e.source IN (" + ",".join("?" * len(info["func_ids"])) + ") "
            "AND nt.file_path != ? AND nt.file_path NOT LIKE 'tests/%'",
            tuple(info["func_ids"]) + (fp,),
        ).fetchone()
        info["external_deps"] = deps["cnt"] if deps else 0

    # Compute scores
    max_func_count = max(info["func_count"] for info in files.values()) or 1
    max_deps = max(info["external_deps"] for info in files.values()) or 1

    scores = []
    for fp, info in files.items():
        fc = info["func_count"]
        coverage_pct = (info["tested_count"] / fc * 100) if fc > 0 else 0
        alive_pct = ((fc - info["dead_count"]) / fc * 100) if fc > 0 else 100
        coupling_score = max(0, 100 - (info["external_deps"] / max(max_deps, 1) * 100))
        size_score = max(0, 100 - (fc / max(max_func_count, 1) * 100))

        score = int(
            coverage_pct * 0.40 +
            alive_pct * 0.25 +
            coupling_score * 0.20 +
            size_score * 0.15
        )

        scores.append({
            "file_path": fp,
            "score": score,
            "func_count": fc,
            "tested_count": info["tested_count"],
            "dead_count": info["dead_count"],
            "coverage_pct": round(coverage_pct, 1),
            "external_deps": info["external_deps"],
        })

    scores.sort(key=lambda s: s["score"], reverse=True)
    return scores
