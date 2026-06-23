"""P13 Performance benchmark suite for tws-graph core operations.

Uses ``time.perf_counter()`` for high-resolution timing.  Each benchmark
records elapsed time, throughput, and data scale.

Baselines are stored in ``tests/benchmarks/baselines.json``.  When a
measurement exceeds 5x the historical baseline a ``REGRESSION`` warning is
emitted.  No hard thresholds are enforced — the baseline check is advisory.

Run with::

    pytest tests/benchmarks/ --run-slow
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import warnings
from pathlib import Path

import pytest

from tws_graph.store.sqlite_store import SqliteStore


def _init_store(db_path: str, auto_flush_size: int = 10000) -> SqliteStore:
    """Create and return a SqliteStore with full schema pre-initialised.

    Uses the Store migration system to create the complete schema, matching
    what ``cli._ensure_store()`` does for new databases.
    """
    import sqlite3 as _sqlite3

    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    # Run full migration system to create the schema
    from tws_graph.store.migrations import MigrationRunner
    from tws_graph.store.connection import configure_connection

    conn = _sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = _sqlite3.Row
    configure_connection(conn)
    runner = MigrationRunner(conn)
    runner.migrate()
    conn.close()

    return SqliteStore(db_path, auto_flush_size=auto_flush_size)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASELINE_PATH = Path(__file__).parent / "baselines.json"
REGRESSION_FACTOR = 5.0  # warn if elapsed > baseline * FACTOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_id(qualified_name: str, file_path: str) -> str:
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load_baselines() -> dict:
    """Load baseline measurements from JSON, or return empty dict."""
    if BASELINE_PATH.exists():
        try:
            with open(BASELINE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_baselines(data: dict) -> None:
    """Persist baseline measurements to JSON."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _check_regression(name: str, elapsed_ms: float, baselines: dict) -> None:
    """Emit a warning if *elapsed_ms* exceeds *REGRESSION_FACTOR* x baseline."""
    entry = baselines.get(name)
    if entry is None:
        return
    baseline = entry.get("baseline_ms")
    if baseline is None:
        return
    if elapsed_ms > baseline * REGRESSION_FACTOR:
        warnings.warn(
            f"[REGRESSION] {name}: {elapsed_ms:.2f} ms "
            f"(baseline: {baseline:.2f} ms, "
            f"ratio: {elapsed_ms / baseline:.1f}x)"
        )


def _record_benchmark(
    name: str,
    elapsed_ms: float,
    scale: str,
    throughput_ops: float | None = None,
    update_baseline: bool = False,
) -> None:
    """Record benchmark result and optionally update baseline.

    Args:
        name: Human-readable benchmark name.
        elapsed_ms: Measured wall-clock time in milliseconds.
        scale: Data-scale description (e.g. "100_files").
        throughput_ops: Operations per second, if applicable.
        update_baseline: If True, overwrite the stored baseline for *name*.
    """
    baselines = _load_baselines()
    _check_regression(name, elapsed_ms, baselines)

    if update_baseline:
        entry = {
            "baseline_ms": round(elapsed_ms, 2),
            "scale": scale,
        }
        if throughput_ops is not None:
            entry["throughput_ops"] = round(throughput_ops, 2)
        baselines[name] = entry
        _save_baselines(baselines)


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

def _generate_nodes(
    count: int,
    file_path: str = "src/module.py",
    language: str = "python",
) -> list[dict]:
    """Generate *count* synthetic node dicts.

    Each node is a realistic-looking function or class symbol.  All nodes
    belong to the same *file_path* unless *file_path* contains ``{i}``.
    """
    nodes: list[dict] = []
    now = _now_ms()
    for i in range(count):
        fp = file_path.format(i=i) if "{" in file_path else file_path
        name = f"func_process_item_{i}"
        qname = f"{fp}::{name}"
        nodes.append({
            "id": _hash_id(qname, fp),
            "kind": "function",
            "name": name,
            "qualified_name": qname,
            "file_path": fp,
            "language": language,
            "start_line": i * 10 + 1,
            "end_line": i * 10 + 8,
            "signature": f"def {name}(x: int) -> int",
            "docstring": f"Process item {i}.",
            "visibility": "public",
            "is_abstract": 0,
            "is_exported": 1,
            "decorators": None,
            "framework": None,
            "properties": "{}",
            "updated_at": now,
        })
    return nodes


def _generate_edges(
    sources: list[str],
    targets: list[str],
    kind: str = "calls",
) -> list[dict]:
    """Create edges connecting each source to a matching-index target.

    If *sources* and *targets* have different lengths, cycles through the
    shorter list.
    """
    edges: list[dict] = []
    n = max(len(sources), len(targets))
    for i in range(n):
        src = sources[i % len(sources)] if sources else ""
        tgt = targets[i % len(targets)] if targets else ""
        edges.append({
            "source": src,
            "target": tgt,
            "target_text": None,
            "kind": kind,
            "source_loc": f"test.py:{i * 10}:5",
            "provenance": "tree-sitter",
            "properties": None,
        })
    return edges


# =============================================================================
# Benchmark 1 — Index building time
# =============================================================================

@pytest.mark.slow
def test_benchmark_index_build(tmp_path: Path) -> None:
    """Measure time to build an index with 100 files / ~500 nodes.

    Simulates inserting nodes and edges for 100 Python source files, then
    flushing to SQLite.
    """
    db_path = str(tmp_path / "bench_index.db")
    store = _init_store(db_path, auto_flush_size=5000)

    t0 = time.perf_counter()

    # Insert 500 nodes across 100 files (5 nodes per file)
    all_node_ids: list[str] = []
    for fi in range(100):
        fp = f"src/pkg/module_{fi}.py"
        nodes = _generate_nodes(5, file_path=fp)
        store.insert_nodes(nodes)
        all_node_ids.extend(n["id"] for n in nodes)

    # Insert edges (calls between consecutive nodes)
    expected_edges = 0
    if len(all_node_ids) >= 2:
        edges = _generate_edges(all_node_ids[:-1], all_node_ids[1:])
        store.insert_edges(edges)
        expected_edges = len(edges)

    store.flush()

    elapsed_ms = (time.perf_counter() - t0) * 1000
    node_count = store.count_nodes()
    edge_count = store.count_edges()

    store.close()

    _record_benchmark(
        "index_build",
        elapsed_ms,
        scale=f"{node_count}_nodes_{edge_count}_edges",
        throughput_ops=node_count / (elapsed_ms / 1000) if elapsed_ms > 0 else 0,
    )

    assert node_count == 500
    assert edge_count == expected_edges
    assert elapsed_ms > 0


# =============================================================================
# Benchmark 2 — FTS5 search latency
# =============================================================================

@pytest.mark.slow
def test_benchmark_fts_search(tmp_path: Path) -> None:
    """Measure FTS5 full-text-search latency for a single keyword query.

    Populates 300 nodes with diverse names, then times a search.
    """
    db_path = str(tmp_path / "bench_fts.db")
    store = _init_store(db_path)

    # Populate with nodes using distinct names
    nodes = _generate_nodes(300, file_path="src/lib/core.py")
    # Make every 10th name more distinctive
    for i, n in enumerate(nodes):
        if i % 10 == 0:
            n["name"] = "api_request_handler_v2"
            n["qualified_name"] = f"src/lib/core.py::api_request_handler_v2"
            n["id"] = _hash_id(n["qualified_name"], n["file_path"])
        elif i % 5 == 0:
            n["name"] = "handle_api"
            n["qualified_name"] = f"src/lib/core.py::handle_api"
            n["id"] = _hash_id(n["qualified_name"], n["file_path"])
    store.insert_nodes(nodes)
    store.flush()

    # Warm up
    _ = store.fts_search("api", limit=20)

    # Measure
    t0 = time.perf_counter()
    results = store.fts_search("api", limit=20)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    store.close()

    _record_benchmark(
        "fts_search",
        elapsed_ms,
        scale="300_nodes_single_keyword",
    )

    assert len(results) >= 1
    assert elapsed_ms > 0


# =============================================================================
# Benchmark 3 — Edge insertion batch performance
# =============================================================================

@pytest.mark.slow
def test_benchmark_edge_insert_batch(tmp_path: Path) -> None:
    """Measure edge insertion throughput for a batch of 500 edges."""
    db_path = str(tmp_path / "bench_edges.db")
    store = _init_store(db_path, auto_flush_size=10000)

    # Pre-populate nodes
    nodes = _generate_nodes(100, file_path="src/pkg/module.py")
    store.insert_nodes(nodes)
    store.flush()
    node_ids = [n["id"] for n in nodes]

    # Generate 500 edges with various kinds
    edges = []
    kinds = ["calls", "references", "imports", "contains"]
    for i in range(500):
        src = node_ids[i % len(node_ids)]
        tgt = node_ids[(i + 1) % len(node_ids)]
        edges.append({
            "source": src,
            "target": tgt,
            "target_text": None,
            "kind": kinds[i % len(kinds)],
            "source_loc": f"src/pkg/module.py:{i}:1",
            "provenance": "tree-sitter",
            "properties": None,
        })

    t0 = time.perf_counter()
    store.insert_edges(edges)
    store.flush()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    edge_count = store.count_edges()
    store.close()

    throughput = 500 / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    _record_benchmark(
        "edge_insert_batch",
        elapsed_ms,
        scale="500_edges",
        throughput_ops=throughput,
    )

    assert edge_count == 500
    assert elapsed_ms > 0


# =============================================================================
# Benchmark 4 — Read query latency (large file vs many small files)
# =============================================================================

@pytest.mark.slow
def test_benchmark_read_query(tmp_path: Path) -> None:
    """Compare read performance: single large file vs many small files.

    Scenario A: 200 nodes in a single file — read all by file_path.
    Scenario B: 200 nodes across 50 files — read all by iterating files.
    """
    db_path = str(tmp_path / "bench_read.db")
    store = _init_store(db_path, auto_flush_size=10000)

    # Scenario A — single large file
    large_nodes = _generate_nodes(200, file_path="src/huge_module.py")
    store.insert_nodes(large_nodes)

    # Scenario B — many small files
    for fi in range(50):
        small_nodes = _generate_nodes(4, file_path=f"src/small/pkg{fi}.py")
        store.insert_nodes(small_nodes)

    store.flush()

    # Measure A: read all nodes from the large file
    t0 = time.perf_counter()
    results_a = list(store.iter_nodes_by_file("src/huge_module.py"))
    elapsed_a_ms = (time.perf_counter() - t0) * 1000

    # Measure B: read nodes from many small files
    t0 = time.perf_counter()
    total_b = 0
    for fi in range(50):
        results_b = list(store.iter_nodes_by_file(f"src/small/pkg{fi}.py"))
        total_b += len(results_b)
    elapsed_b_ms = (time.perf_counter() - t0) * 1000

    store.close()

    _record_benchmark(
        "read_query_large_file",
        elapsed_a_ms,
        scale="200_nodes_1_file",
        throughput_ops=200 / (elapsed_a_ms / 1000) if elapsed_a_ms > 0 else 0,
    )
    _record_benchmark(
        "read_query_many_small_files",
        elapsed_b_ms,
        scale="200_nodes_50_files",
        throughput_ops=200 / (elapsed_b_ms / 1000) if elapsed_b_ms > 0 else 0,
    )

    assert len(results_a) == 200
    assert total_b == 200
    assert elapsed_a_ms > 0
    assert elapsed_b_ms > 0


# =============================================================================
# Benchmark 5 — Impact analysis (depth-2 traversal)
# =============================================================================

@pytest.mark.slow
def test_benchmark_impact_analysis(tmp_path: Path) -> None:
    """Measure graph traversal latency for impact analysis at depth=2.

    Builds a linear chain of 50 nodes, then performs get_neighbors
    and BFS find_paths.
    """
    db_path = str(tmp_path / "bench_impact.db")
    store = _init_store(db_path)

    # Build a linear chain: node_0 -> node_1 -> ... -> node_49
    nodes = _generate_nodes(50, file_path="src/chain.py")
    store.insert_nodes(nodes)
    node_ids = [n["id"] for n in nodes]

    edges = []
    for i in range(len(node_ids) - 1):
        edges.append({
            "source": node_ids[i],
            "target": node_ids[i + 1],
            "target_text": None,
            "kind": "calls",
            "source_loc": f"src/chain.py:{i * 10}:1",
            "provenance": "tree-sitter",
            "properties": None,
        })
    store.insert_edges(edges)
    store.flush()

    # Measure get_neighbors (depth=1) for the root
    t0 = time.perf_counter()
    neighbors = store.get_neighbors(node_ids[0], direction="out")
    elapsed_1_ms = (time.perf_counter() - t0) * 1000

    # Measure BFS find_paths (depth=2)
    t0 = time.perf_counter()
    path = store.find_paths(node_ids[0], node_ids[2], max_depth=2)
    elapsed_bfs_ms = (time.perf_counter() - t0) * 1000

    store.close()

    _record_benchmark(
        "impact_neighbors_depth1",
        elapsed_1_ms,
        scale="50_node_chain",
    )
    _record_benchmark(
        "impact_bfs_depth2",
        elapsed_bfs_ms,
        scale="50_node_chain",
    )

    assert len(neighbors) == 1
    assert path is not None
    assert len(path) == 3  # node_0, node_1, node_2
    assert elapsed_1_ms > 0
    assert elapsed_bfs_ms > 0


# =============================================================================
# Benchmark 6 — Invalidation check latency
# =============================================================================

@pytest.mark.slow
def test_benchmark_invalidation_check(tmp_path: Path) -> None:
    """Measure invalidation tracker check latency for 100 tracked files."""
    from tws_graph.analysis.invalidation import (
        InvalidationTracker,
        AnalyzerRegistration,
    )

    db_path = str(tmp_path / "bench_inval.db")
    store = _init_store(db_path)

    # Set up files in the store
    now_ms = _now_ms()
    for fi in range(100):
        fp = f"src/pkg/module_{fi}.py"
        store.upsert_file(
            path=fp,
            content_hash=hashlib.sha256(f"content_{fi}".encode()).hexdigest(),
            language="python",
            node_count=5,
            size=1024,
            modified_at=now_ms,
        )
    store.flush()

    # Set up invalidation tracker with its own DB
    inval_db = str(tmp_path / "inval_tracker.db")
    tracker = InvalidationTracker(inval_db)

    # Register some analyzers
    tracker.register(AnalyzerRegistration(
        analyzer_name="dead_code",
        version=1,
        file_patterns=["src/pkg/module_*.py"],
        node_kinds=["function", "class", "method"],
    ))
    tracker.register(AnalyzerRegistration(
        analyzer_name="complexity",
        version=1,
        file_patterns=["src/pkg/module_*.py"],
        node_kinds=["function", "class", "method"],
    ))

    # Build list of all file paths
    all_paths = [f"src/pkg/module_{fi}.py" for fi in range(100)]

    # Mark all files as valid to establish baselines
    tracker.mark_valid("dead_code", all_paths)
    tracker.mark_valid("complexity", all_paths)

    # First check_invalidation stabilises the sentinel baselines
    tracker.check_invalidation(store)

    # Measure check_invalidation (second call — all files should be fresh)
    t0 = time.perf_counter()
    stale = tracker.check_invalidation(store)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    tracker.close()
    store.close()

    _record_benchmark(
        "invalidation_check",
        elapsed_ms,
        scale="100_tracked_files_2_analyzers",
    )

    # No files should be stale (baselines stabilised)
    total_stale = sum(len(v) for v in stale.values())
    assert total_stale == 0
    assert elapsed_ms > 0


# =============================================================================
# Benchmark 7 — Flush throughput
# =============================================================================

@pytest.mark.slow
def test_benchmark_flush_throughput(tmp_path: Path) -> None:
    """Measure flush throughput for batches of different sizes.

    Tests 3 scenarios:
      - 100 nodes + 50 edges (small batch)
      - 1000 nodes + 500 edges (medium batch)
      - 5000 nodes + 2000 edges (large batch)
    """
    db_path = str(tmp_path / "bench_flush.db")
    results: list[tuple[str, float, int]] = []

    scenarios = [
        ("flush_small", 100, 50),
        ("flush_medium", 1000, 500),
        ("flush_large", 5000, 2000),
    ]

    for label, n_nodes, n_edges in scenarios:
        store = _init_store(db_path, auto_flush_size=100000)
        nodes = _generate_nodes(n_nodes, file_path=f"src/flush_test.py")
        store.insert_nodes(nodes)
        node_ids = [n["id"] for n in nodes]

        edges_src = node_ids[:n_edges] if len(node_ids) >= n_edges else node_ids * (n_edges // len(node_ids) + 1)
        edges_tgt = (node_ids[1:] + node_ids[:1])[:n_edges]
        edges = _generate_edges(edges_src, edges_tgt)
        store.insert_edges(edges)

        t0 = time.perf_counter()
        store.flush()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        total_ops = n_nodes + n_edges
        throughput = total_ops / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

        _record_benchmark(
            label,
            elapsed_ms,
            scale=f"{n_nodes}_nodes_{n_edges}_edges",
            throughput_ops=throughput,
        )

        count_n = store.count_nodes()
        count_e = store.count_edges()
        store.close()

        # Fresh DB for each scenario
        if os.path.exists(db_path):
            os.remove(db_path)

        assert count_n == n_nodes
        assert count_e == n_edges

    assert True  # All scenarios passed
