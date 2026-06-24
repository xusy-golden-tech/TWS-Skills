"""MCP code tools: get_code, get_dependencies, get_impact, trace_path."""

from __future__ import annotations

import json
from typing import Callable

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register code-exploration tools on the given registry."""

    # -- get_code -------------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_code",
            description=(
                "Retrieve the source code and metadata for a specific symbol by its "
                "node ID. Returns the symbol's name, qualified_name, kind, file_path, "
                "language, signature, docstring, line range, and source code snippet."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "string",
                        "description": "The unique node identifier (SHA256 hash) of the symbol",
                    },
                },
                "required": ["symbol_id"],
            },
        ),
        handler=lambda args: _get_code(store_factory(), args),
    )

    # -- get_dependencies -----------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_dependencies",
            description=(
                "Get the callers (inbound) or callees (outbound) of a symbol. "
                "Returns the dependency graph up to the specified depth."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "string",
                        "description": "The unique node identifier of the symbol",
                    },
                    "direction": {
                        "type": "string",
                        "description": "inbound (who calls this) or outbound (this calls whom)",
                        "enum": ["inbound", "outbound"],
                        "default": "inbound",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Max traversal depth (default: 1)",
                        "default": 1,
                    },
                },
                "required": ["symbol_id"],
            },
        ),
        handler=lambda args: _get_dependencies(store_factory(), args),
    )

    # -- get_impact -----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_impact",
            description=(
                "Calculate the impact radius of a symbol -- how many other symbols "
                "would be affected if this symbol changes. Uses BFS on the call graph."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_id": {
                        "type": "string",
                        "description": "The unique node identifier of the symbol",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Max traversal depth (default: 2)",
                        "default": 2,
                    },
                },
                "required": ["symbol_id"],
            },
        ),
        handler=lambda args: _get_impact(store_factory(), args),
    )

    # -- trace_path -----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="trace_path",
            description=(
                "Find call paths between two symbols. Uses BFS to discover the shortest "
                "paths through the call graph from the source to the target symbol."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "string",
                        "description": "The source node ID",
                    },
                    "to_id": {
                        "type": "string",
                        "description": "The target node ID",
                    },
                },
                "required": ["from_id", "to_id"],
            },
        ),
        handler=lambda args: _trace_path(store_factory(), args),
    )


# ============================================================================
# Handler implementations
# ============================================================================

_CALL_KINDS = ["calls", "references", "imports"]


def _get_code(store: Store, args: dict) -> dict:
    """Retrieve source code for a symbol."""
    symbol_id = args.get("symbol_id", "")

    node = store.get_node_by_id(symbol_id) if symbol_id else None
    if not node:
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": f"Symbol not found: {symbol_id}"})}
            ]
        }

    # Try to read source code from the file
    snippet = None
    try:
        import os

        file_path = node.get("file_path", "")
        if file_path and os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            start = max(0, node.get("start_line", 1) - 1)
            end = min(len(lines), node.get("end_line", start + 1))
            snippet = "".join(lines[start:end])
    except Exception:
        pass

    result = {
        "id": node.get("id", symbol_id),
        "name": node.get("name", ""),
        "qualified_name": node.get("qualified_name", ""),
        "kind": node.get("kind", ""),
        "file_path": node.get("file_path", ""),
        "language": node.get("language", ""),
        "start_line": node.get("start_line"),
        "end_line": node.get("end_line"),
        "signature": node.get("signature"),
        "docstring": node.get("docstring"),
        "visibility": node.get("visibility"),
        "source_code": snippet,
    }

    return {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
        ]
    }


def _get_dependencies(store: Store, args: dict) -> dict:
    """Get callers or callees of a symbol."""
    symbol_id = args.get("symbol_id", "")
    direction = args.get("direction", "inbound")
    depth = args.get("depth", 1)

    if not symbol_id:
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": "symbol_id is required"})}
            ]
        }

    try:
        from tws_graph.db.queries import QueryBuilder

        db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
        if db_conn is None:
            raise RuntimeError("No DB connection available")

        queries = QueryBuilder(db_conn)
        from tws_graph.graph.traversal import GraphTraverser

        traverser = GraphTraverser(queries)

        if direction in ("inbound", "outbound", "both"):
            result = traverser.get_calls(
                node_id=symbol_id,
                direction=direction,
                max_depth=depth,
                edge_kinds=_CALL_KINDS,
            )
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"error": f"Invalid direction: {direction}. Use 'inbound' or 'outbound'."}
                        ),
                    }
                ]
            }

        nodes = [
            {
                "id": nid,
                "name": ndata.get("name", ""),
                "qualified_name": ndata.get("qualified_name", ""),
                "kind": ndata.get("kind", ""),
                "file_path": ndata.get("file_path", ""),
            }
            for nid, ndata in result.get("nodes", {}).items()
        ]

        edges = [
            {
                "source": e.get("source", ""),
                "target": e.get("target", ""),
                "kind": e.get("kind", ""),
            }
            for e in result.get("edges", [])
        ]

        output = {"nodes": nodes, "edges": edges, "count": len(nodes)}
    except Exception as e:
        output = {"error": str(e)}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _get_impact(store: Store, args: dict) -> dict:
    """Calculate impact radius of a symbol."""
    symbol_id = args.get("symbol_id", "")
    depth = args.get("depth", 2)

    if not symbol_id:
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": "symbol_id is required"})}
            ]
        }

    try:
        db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
        if db_conn is None:
            raise RuntimeError("No DB connection available")

        from tws_graph.db.queries import QueryBuilder

        queries = QueryBuilder(db_conn)
        from tws_graph.graph.traversal import GraphTraverser

        traverser = GraphTraverser(queries)

        # Get outbound calls to find what this symbol depends on (impact calculation)
        result = traverser.get_calls(
            node_id=symbol_id,
            direction="inbound",  # who depends on me
            max_depth=depth,
            edge_kinds=_CALL_KINDS,
        )

        nodes = result.get("nodes", {})
        impact_radius = len(nodes) - 1  # exclude self

        my_node = store.get_node_by_id(symbol_id)
        node_info = {}
        if my_node:
            node_info = {
                "id": my_node.get("id"),
                "name": my_node.get("name"),
                "qualified_name": my_node.get("qualified_name"),
                "kind": my_node.get("kind"),
                "file_path": my_node.get("file_path"),
            }

        # Classify risk level
        if impact_radius == 0:
            risk_level = "low"
        elif impact_radius <= 5:
            risk_level = "medium"
        elif impact_radius <= 15:
            risk_level = "high"
        else:
            risk_level = "critical"

        affected_nodes = [
            {
                "id": nid,
                "name": ndata.get("name", ""),
                "qualified_name": ndata.get("qualified_name", ""),
                "kind": ndata.get("kind", ""),
                "file_path": ndata.get("file_path", ""),
            }
            for nid, ndata in nodes.items()
            if nid != symbol_id
        ]

        output = {
            "symbol": node_info,
            "impact_radius": impact_radius,
            "risk_level": risk_level,
            "affected_nodes": affected_nodes,
        }
    except Exception as e:
        output = {"error": str(e)}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _trace_path(store: Store, args: dict) -> dict:
    """Find paths between two symbols."""
    from_id = args.get("from_id", "")
    to_id = args.get("to_id", "")

    if not from_id or not to_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": "from_id and to_id are required"}),
                }
            ]
        }

    try:
        db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
        if db_conn is None:
            raise RuntimeError("No DB connection available")

        from tws_graph.db.queries import QueryBuilder

        queries = QueryBuilder(db_conn)
        result = queries.find_paths(from_id, to_id, max_depth=8)

        paths = []
        if result:
            for path in result:
                if isinstance(path, list):
                    nodes_in_path = []
                    for nid in path:
                        node = store.get_node_by_id(nid)
                        if node:
                            nodes_in_path.append(
                                {
                                    "id": node.get("id", nid),
                                    "name": node.get("name", ""),
                                    "qualified_name": node.get("qualified_name", ""),
                                    "kind": node.get("kind", ""),
                                }
                            )
                        else:
                            nodes_in_path.append({"id": nid})
                    paths.append(nodes_in_path)

        output = {"paths": paths, "count": len(paths)}
    except Exception as e:
        output = {"error": str(e), "paths": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
