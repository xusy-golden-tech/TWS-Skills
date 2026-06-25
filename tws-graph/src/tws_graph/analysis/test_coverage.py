"""Test-to-code coverage mapping (P35 v5.4.0).

Analyzes calls edges from test functions to production functions
to build a coverage map and identify untested code.

Usage::

    from tws_graph.analysis.test_coverage import build_coverage_map
    cov = build_coverage_map(queries)
    print(f"Uncovered: {len(cov['uncovered'])}")
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# P35a: Test file detection
# ---------------------------------------------------------------------------

_TEST_PATH_PATTERNS: tuple[str, ...] = (
    "test_",
    "_test.",
    "test/",
    "tests/",
    "Test.",
    "Test",
    ".test.",
    ".spec.",
    "spec/",
)


def is_test_file(file_path: str | None) -> bool:
    """Check if a file path matches known test file patterns.

    Supports: Python (test_*.py, *_test.py), Java (*Test.java, test/),
    TypeScript (*.test.ts, *.spec.ts), Go (*_test.go).
    """
    if not file_path:
        return False

    # Normalize path separators
    fp = file_path.replace("\\", "/")

    # Common test directory patterns
    if "/test/" in fp or "/tests/" in fp or "/spec/" in fp:
        return True
    if fp.startswith("test/") or fp.startswith("tests/") or fp.startswith("spec/"):
        return True

    # File name patterns
    basename = fp.rsplit("/", 1)[-1] if "/" in fp else fp
    if basename.startswith("test_"):
        return True
    if basename.endswith("_test.py") or basename.endswith("_test.go"):
        return True
    if "Test" in basename and basename.endswith((".java", ".kt", ".scala")):
        return True
    if basename.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
        return True
    if basename.endswith((".test.js", ".spec.js", ".test.jsx", ".spec.jsx")):
        return True

    return False


# ---------------------------------------------------------------------------
# P35b + P35c: Coverage mapping and gap report
# ---------------------------------------------------------------------------


def build_coverage_map(queries) -> dict:
    """Build test→code coverage mapping from the call graph.

    Returns:
        {
            "coverage": {production_node_id: [test_node_ids]},
            "test_to_code": {test_node_id: [production_node_ids]},
            "uncovered": [node_dicts for production functions with no test],
            "total_production_functions": int,
            "total_test_functions": int,
            "coverage_pct": float,
        }
    """
    # Get all function/method nodes
    all_nodes = queries._exec(
        "SELECT id, name, qualified_name, file_path, kind, start_line "
        "FROM nodes WHERE kind IN ('function', 'method')"
    ).fetchall()

    if not all_nodes:
        return {
            "coverage": {},
            "test_to_code": {},
            "uncovered": [],
            "total_production_functions": 0,
            "total_test_functions": 0,
            "coverage_pct": 0.0,
        }

    # Separate test and production nodes
    test_nodes: dict[str, dict] = {}
    prod_nodes: dict[str, dict] = {}

    for row in all_nodes:
        fpath = row["file_path"]
        node_dict = {
            "id": row["id"],
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "file_path": fpath,
            "kind": row["kind"],
            "line": row["start_line"],
        }
        if is_test_file(fpath):
            test_nodes[row["id"]] = node_dict
            # Also include test helpers in test files
        else:
            prod_nodes[row["id"]] = node_dict

    if not test_nodes or not prod_nodes:
        return {
            "coverage": {},
            "test_to_code": {},
            "uncovered": list(prod_nodes.values()),
            "total_production_functions": len(prod_nodes),
            "total_test_functions": len(test_nodes),
            "coverage_pct": 0.0,
        }

    # Get all calls edges from test nodes to production nodes
    coverage: dict[str, list[str]] = {}  # prod_id → [test_ids]
    test_to_code: dict[str, list[str]] = {}  # test_id → [prod_ids]

    # Batch query: all edges where source is a test node
    test_ids = list(test_nodes.keys())
    chunk_size = 900
    for i in range(0, len(test_ids), chunk_size):
        chunk = test_ids[i:i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        edges = queries._exec(
            f"SELECT source, target FROM edges "
            f"WHERE kind = 'calls' AND source IN ({placeholders})",
            chunk,
        ).fetchall()

        for edge in edges:
            tgt = edge["target"]
            if tgt in prod_nodes:
                src = edge["source"]
                coverage.setdefault(tgt, []).append(src)
                test_to_code.setdefault(src, []).append(tgt)

    # Build uncovered list (node IDs)
    uncovered = [
        nid
        for nid in prod_nodes
        if nid not in coverage
    ]

    total_prod = len(prod_nodes)
    covered = len(coverage)
    pct = (covered / total_prod * 100) if total_prod > 0 else 0.0

    return {
        "coverage": coverage,
        "test_to_code": test_to_code,
        "uncovered": uncovered,
        "uncovered_details": [prod_nodes[nid] for nid in uncovered],
        "total_production_functions": total_prod,
        "total_test_functions": len(test_nodes),
        "coverage_pct": round(pct, 1),
    }
