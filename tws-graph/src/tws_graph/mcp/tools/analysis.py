"""MCP analysis tools: get_complexity, find_dead_code, get_test_coverage, get_entry_points."""

from __future__ import annotations

import json
from typing import Callable

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register code-analysis tools on the given registry."""

    # -- get_complexity -------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_complexity",
            description=(
                "Analyze code complexity metrics. If symbol_id is provided, returns "
                "complexity for that specific function/method. Otherwise, returns "
                "top-20 most complex functions. Metrics include: cyclomatic complexity, "
                "cognitive complexity, Halstead volume/difficulty/effort, lines of code, "
                "and risk level classification."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "string",
                        "description": "Optional: specific node ID to analyze",
                    },
                },
            },
        ),
        handler=lambda args: _get_complexity(store_factory(), args),
    )

    # -- find_dead_code -------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="find_dead_code",
            description=(
                "Detect potentially unused (dead) code. Uses degree-based analysis: "
                "functions/methods with zero incoming 'calls' or 'references' edges "
                "(excluding known entry points). Returns list of dead code candidates "
                "with their location and in/out degree information."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _find_dead_code(store_factory(), args),
    )

    # -- get_test_coverage ----------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_test_coverage",
            description=(
                "Analyze test-to-source code associations using three heuristic "
                "strategies: naming convention (0.9 confidence), call graph (0.8), "
                "and import reference (0.5). Returns which source functions are "
                "exercised by which tests, with confidence scores."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _get_test_coverage(store_factory(), args),
    )

    # -- get_entry_points -----------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_entry_points",
            description=(
                "Identify project entry points across supported languages (Python, "
                "TypeScript, Java, Go, Kotlin, Rust). Detects: main functions, test "
                "functions, CLI entry points, route handlers, init/constructors, "
                "and lifecycle hooks. Returns classification, confidence, and evidence "
                "for each entry point."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _get_entry_points(store_factory(), args),
    )


# ============================================================================
# Handler implementations
# ============================================================================


def _get_complexity(store: Store, args: dict) -> dict:
    """Analyze code complexity."""
    symbol_id = args.get("symbol_id")

    try:
        from tws_graph.analysis.complexity import ComplexityAnalyzer

        analyzer = ComplexityAnalyzer(store)
        metrics_list = analyzer.analyze()

        if symbol_id:
            # Filter for the specific symbol
            metrics_list = [m for m in metrics_list if m.node_id == symbol_id]
            if not metrics_list:
                node = store.get_node_by_id(symbol_id)
                node_name = node.get("qualified_name", symbol_id) if node else symbol_id
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"error": f"No complexity data for: {node_name}"}
                            ),
                        }
                    ]
                }

        # Sort by cyclomatic complexity descending, take top 20
        metrics_list.sort(key=lambda m: m.cyclomatic, reverse=True)
        if not symbol_id:
            metrics_list = metrics_list[:20]

        results = [
            {
                "node_id": m.node_id,
                "qualified_name": m.qualified_name,
                "file_path": m.file_path,
                "language": m.language,
                "cyclomatic": m.cyclomatic,
                "cognitive": m.cognitive,
                "halstead_volume": m.halstead_volume,
                "halstead_difficulty": m.halstead_difficulty,
                "halstead_effort": m.halstead_effort,
                "lines_of_code": m.lines_of_code,
                "risk_level": m.risk_level,
            }
            for m in metrics_list
        ]

        output = {"results": results, "count": len(results)}
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _find_dead_code(store: Store, args: dict) -> dict:
    """Detect dead code."""
    try:
        from tws_graph.analysis.dead_code import DeadCodeDetector

        detector = DeadCodeDetector(store)
        candidates = detector.detect()

        results = [
            {
                "node_id": c.node_id,
                "qualified_name": c.qualified_name,
                "kind": c.kind,
                "file_path": c.file_path,
                "language": c.language,
                "in_degree": c.in_degree,
                "out_degree": c.out_degree,
                "is_entry_point": c.is_entry_point,
            }
            for c in candidates
        ]

        output = {"results": results, "count": len(results)}
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _get_test_coverage(store: Store, args: dict) -> dict:
    """Analyze test coverage via heuristic strategies."""
    try:
        from tws_graph.analysis.test_edges import TestEdgeAnalyzer

        analyzer = TestEdgeAnalyzer(store)
        edges = analyzer.analyze()

        results = [
            {
                "test_node_id": e.test_node_id,
                "test_name": e.test_name,
                "test_file_path": e.test_file_path,
                "source_node_id": e.source_node_id,
                "source_name": e.source_name,
                "source_file_path": e.source_file_path,
                "confidence": e.confidence,
                "derivation": e.derivation,
            }
            for e in edges
        ]

        # Group by source to compute coverage stats
        source_coverage: dict[str, set] = {}
        for e in edges:
            if e.source_node_id not in source_coverage:
                source_coverage[e.source_node_id] = set()
            source_coverage[e.source_node_id].add(e.test_node_id)

        output = {
            "results": results,
            "count": len(results),
            "summary": {
                "total_test_edges": len(results),
                "sources_with_tests": len(source_coverage),
            },
        }
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _get_entry_points(store: Store, args: dict) -> dict:
    """Identify project entry points."""
    try:
        from tws_graph.analysis.entry_point import EntryPointDetector

        detector = EntryPointDetector(store)
        results_list = detector.detect()

        results = [
            {
                "node_id": r.node_id,
                "qualified_name": r.qualified_name,
                "kind": r.kind,
                "file_path": r.file_path,
                "entry_type": r.entry_type,
                "confidence": r.confidence,
                "evidence": r.evidence,
            }
            for r in results_list
        ]

        # Group by entry type
        types_count: dict[str, int] = {}
        for r in results:
            t = r["entry_type"]
            types_count[t] = types_count.get(t, 0) + 1

        output = {
            "results": results,
            "count": len(results),
            "by_type": types_count,
        }
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
