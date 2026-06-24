"""MCP query tools: query_cypher, detect_cross_service."""

from __future__ import annotations

import json
from typing import Callable

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register query tools on the given registry."""

    # -- query_cypher ---------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="query_cypher",
            description=(
                "Execute a Cypher query against the code graph. Supports MATCH, "
                "WHERE, RETURN, ORDER BY, LIMIT, UNION, WITH clauses. Examples: "
                "'MATCH (n) WHERE n.kind = \"function\" RETURN n.name, n.file_path LIMIT 10', "
                "'MATCH (a)-[e:calls]->(b) RETURN a.name, b.name, e.kind'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Cypher query string (Cypher-like syntax for code graph)",
                    },
                },
                "required": ["query"],
            },
        ),
        handler=lambda args: _query_cypher(store_factory(), args),
    )

    # -- detect_cross_service -------------------------------------------------
    registry.register(
        ToolDefinition(
            name="detect_cross_service",
            description=(
                "Detect cross-service communication patterns (HTTP routes, message "
                "channels, gRPC services) across supported languages (Python, "
                "TypeScript, Java, Go). Returns structured results for routes, "
                "channels (Kafka/RabbitMQ/Redis), and gRPC services."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _detect_cross_service(store_factory(), args),
    )


# ============================================================================
# Handler implementations
# ============================================================================


def _query_cypher(store: Store, args: dict) -> dict:
    """Execute Cypher query."""
    query = args.get("query", "")

    if not query or not query.strip():
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": "Query is required"})}
            ]
        }

    try:
        from tws_graph.cypher import CypherEngine

        engine = CypherEngine()
        result_set = engine.execute(query, store)

        rows = []
        for row in result_set.rows:
            rows.append(dict(row.data))

        output = {
            "columns": result_set.columns,
            "rows": rows,
            "total_count": result_set.total_count,
        }
    except Exception as e:
        output = {"error": str(e), "columns": [], "rows": [], "total_count": 0}

    return {
        "content": [
            {"type": "text", "text": json.dumps(output, ensure_ascii=False)}
        ]
    }


def _detect_cross_service(store: Store, args: dict) -> dict:
    """Detect cross-service communication."""
    try:
        from tws_graph.services.route_detector import RouteDetector
        from tws_graph.services.channel_detector import ChannelDetector
        from tws_graph.services.grpc_detector import GrpcDetector

        routes = []
        channels = []
        grpc_services = []

        # Route detection
        try:
            route_detector = RouteDetector(store)
            route_results = route_detector.detect()
            routes = [
                {
                    "node_id": r.get("node_id", ""),
                    "name": r.get("name", ""),
                    "method": r.get("method", ""),
                    "path": r.get("path", ""),
                    "file_path": r.get("file_path", ""),
                    "framework": r.get("framework", ""),
                }
                for r in route_results
            ]
        except Exception:
            pass

        # Channel detection (Kafka, RabbitMQ, Redis, etc.)
        try:
            channel_detector = ChannelDetector(store)
            channel_results = channel_detector.detect()
            channels = [
                {
                    "node_id": c.get("node_id", ""),
                    "name": c.get("name", ""),
                    "channel_type": c.get("channel_type", ""),
                    "direction": c.get("direction", ""),
                    "topic": c.get("topic", ""),
                    "file_path": c.get("file_path", ""),
                }
                for c in channel_results
            ]
        except Exception:
            pass

        # gRPC detection
        try:
            grpc_detector = GrpcDetector(store)
            grpc_results = grpc_detector.detect()
            grpc_services = [
                {
                    "node_id": g.get("node_id", ""),
                    "name": g.get("name", ""),
                    "service_name": g.get("service_name", ""),
                    "method_name": g.get("method_name", ""),
                    "role": g.get("role", ""),
                    "file_path": g.get("file_path", ""),
                }
                for g in grpc_results
            ]
        except Exception:
            pass

        output = {
            "routes": {"results": routes, "count": len(routes)},
            "channels": {"results": channels, "count": len(channels)},
            "grpc_services": {"results": grpc_services, "count": len(grpc_services)},
            "summary": {
                "total_routes": len(routes),
                "total_channels": len(channels),
                "total_grpc_services": len(grpc_services),
            },
        }
    except Exception as e:
        output = {"error": str(e)}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
