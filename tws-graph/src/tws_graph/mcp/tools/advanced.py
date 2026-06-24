"""MCP advanced tools: find_clones, get_git_diff_impact, get_config_links."""

from __future__ import annotations

import json
from typing import Callable

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register advanced-analysis tools on the given registry."""

    # -- find_clones ----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="find_clones",
            description=(
                "Detect code clones (near-duplicate functions/methods) using "
                "MinHash + LSH. Uses AST token extraction, MinHash signatures, "
                "and LSH bucketing to efficiently find similar function pairs. "
                "Returns clone pairs with Jaccard similarity scores."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "description": "Minimum Jaccard similarity (0.0-1.0, default: 0.8)",
                        "default": 0.8,
                    },
                },
            },
        ),
        handler=lambda args: _find_clones(store_factory(), args),
    )

    # -- get_git_diff_impact --------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_git_diff_impact",
            description=(
                "Analyze the impact of code changes between the current state and "
                "a base branch/commit. Uses the tws-graph diff/snapshot system or "
                "direct Store comparison. Returns changed symbols with their impact "
                "radius (number of affected dependents) and risk level classification."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "base_branch": {
                        "type": "string",
                        "description": "Base git reference to compare against (default: HEAD~1)",
                        "default": "HEAD~1",
                    },
                },
            },
        ),
        handler=lambda args: _get_git_diff_impact(store_factory(), args),
    )

    # -- get_config_links -----------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_config_links",
            description=(
                "Discover associations between code constants and configuration file "
                "keys. Scans .env, .yaml, .json, .toml, .properties files and maps "
                "code-level constants to config keys via exact match, prefix match, "
                "and contains match strategies with confidence scores."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _get_config_links(store_factory(), args),
    )


# ============================================================================
# Handler implementations
# ============================================================================


def _find_clones(store: Store, args: dict) -> dict:
    """Detect code clones."""
    threshold = args.get("threshold", 0.8)

    try:
        from tws_graph.graph.algorithms.similarity import CloneDetector

        detector = CloneDetector(threshold=threshold)
        result = detector.run(store)

        pairs = []
        for pair in result.data.get("pairs", []):
            pairs.append({
                "node_a_id": pair.get("node_a_id", ""),
                "node_a_name": pair.get("node_a_name", ""),
                "node_b_id": pair.get("node_b_id", ""),
                "node_b_name": pair.get("node_b_name", ""),
                "similarity": pair.get("similarity", 0),
            })

        output = {
            "results": pairs,
            "count": len(pairs),
            "threshold": threshold,
            "duration_ms": result.duration_ms,
        }
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _get_git_diff_impact(store: Store, args: dict) -> dict:
    """Analyze git diff impact."""
    base_branch = args.get("base_branch", "HEAD~1")

    try:
        from tws_graph.analysis.git_diff import GitDiffAnalyzer

        analyzer = GitDiffAnalyzer(store, base=base_branch)
        impacts = analyzer.analyze()

        results = [
            {
                "node_id": imp.node_id,
                "node_name": imp.node_name,
                "change_type": imp.change_type,
                "impact_radius": imp.impact_radius,
                "risk_level": imp.risk_level.value if hasattr(imp.risk_level, "value") else str(imp.risk_level),
                "affected_files": imp.affected_files,
                "affected_nodes": imp.affected_nodes[:20],  # limit to top 20
            }
            for imp in impacts
        ]

        # Summary by risk level
        risk_summary: dict[str, int] = {}
        for r in results:
            rl = r["risk_level"]
            risk_summary[rl] = risk_summary.get(rl, 0) + 1

        output = {
            "results": results,
            "count": len(results),
            "base": base_branch,
            "risk_summary": risk_summary,
        }
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _get_config_links(store: Store, args: dict) -> dict:
    """Discover config links."""
    try:
        from tws_graph.analysis.config_links import ConfigLinkAnalyzer

        analyzer = ConfigLinkAnalyzer(store)
        links = analyzer.analyze()

        results = [
            {
                "node_id": link.node_id,
                "node_name": link.node_name,
                "file_path": link.file_path,
                "config_file": link.config_file,
                "config_key": link.config_key,
                "confidence": link.confidence,
                "derivation": link.derivation,
            }
            for link in links
        ]

        output = {"results": results, "count": len(results)}
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
