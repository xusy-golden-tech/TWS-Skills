"""MCP query tools: query_cypher, detect_cross_service, get_edge_distribution."""

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

    # -- get_edge_distribution ------------------------------------------------
    registry.register(
        ToolDefinition(
            name="get_edge_distribution",
            description=(
                "Get the distribution of edge kinds in the code graph. Returns "
                "count per edge kind (calls, imports, extends, implements, reads, "
                "writes, data_flows, throws, http_calls, env_accesses, grpc_server, "
                "grpc_client, grpc_service, test_edge, config_link, emits, "
                "listens_on, similar_to, etc.)."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        handler=lambda args: _get_edge_distribution(store_factory(), args),
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


def _get_edge_distribution(store: Store, args: dict) -> dict:
    """Get edge kind distribution."""
    try:
        db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
        if db_conn is None:
            return {
                "content": [{"type": "text", "text": json.dumps(
                    {"error": "Store does not expose a database connection"})}]
            }

        import sqlite3
        conn = db_conn if isinstance(db_conn, sqlite3.Connection) else db_conn.conn
        rows = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()

        distribution = {r["kind"]: r["cnt"] for r in rows}
        total_edges = sum(distribution.values())

        output = {
            "distribution": distribution,
            "total_edges": total_edges,
            "total_kinds": len(distribution),
        }
    except Exception as e:
        output = {"error": str(e)}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


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
        from tws_graph.services.channel_detector import ChannelDetector
        from tws_graph.services.grpc_detector import GrpcDetector

        routes = []
        channels = []
        grpc_services = []

        # Route detection (delegated to Rust core)
        try:
            from tws_graph.rust_bridge import rust_routes
            db_path = store._conn_mgr.db_path
            route_json = rust_routes(db_path, json_output=True)
            route_data = json.loads(route_json)
            if isinstance(route_data, list):
                routes = [
                    {
                        "node_id": r.get("node_id", ""),
                        "name": r.get("name", ""),
                        "method": r.get("method", ""),
                        "path": r.get("path", ""),
                        "file_path": r.get("file_path", ""),
                        "framework": r.get("framework", ""),
                    }
                    for r in route_data
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
