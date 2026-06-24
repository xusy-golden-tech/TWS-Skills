"""MCP search tools: search_symbols, semantic_search."""

from __future__ import annotations

import json
from typing import Callable

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

# Store factory type: callable that returns a Store (creates new connection each time)
StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register search-related tools on the given registry."""

    # -- search_symbols -------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="search_symbols",
            description=(
                "Full-text search for code symbols (functions, classes, methods, etc.) "
                "using the tws-graph FTS5 index. Supports qualifiers: kind:, lang:, path:. "
                "Returns ranked results with name, qualified_name, kind, file_path, language, "
                "signature, and docstring."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query. Can include qualifiers: "
                            "kind:function|class|method|variable|... "
                            "lang:python|typescript|java|go|... "
                            "path:src/utils"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        handler=lambda args: _search_symbols(store_factory(), args),
    )

    # -- semantic_search ------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="semantic_search",
            description=(
                "Semantic (relevance-ranked) search for code symbols. Uses 11-signal "
                "fusion ranking on top of FTS5 BM25 pre-filtering: BM25, qualified name "
                "match, docstring match, AST similarity, API signature similarity, clone "
                "similarity, module proximity, graph diffusion, caller/callee proximity, "
                "graph centrality, and dataflow connection. Returns top-ranked results "
                "with relevance scores."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language or keyword search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        handler=lambda args: _semantic_search(store_factory(), args),
    )


# ============================================================================
# Handler implementations
# ============================================================================


def _search_symbols(store: Store, args: dict) -> dict:
    """FTS5 keyword search for symbols."""
    query = args.get("query", "")
    limit = args.get("limit", 20)

    if not query or not query.strip():
        return {
            "content": [{"type": "text", "text": json.dumps({"results": [], "count": 0})}]
        }

    from tws_graph.db.queries import QueryBuilder

    db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
    if db_conn is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"error": "Store does not expose a database connection"}
                    ),
                }
            ]
        }

    queries = QueryBuilder(db_conn)
    try:
        results = queries.search_nodes_field_qualified(query, limit)
    except Exception as e:
        results = queries.search_nodes(query, limit) if hasattr(queries, "search_nodes") else []

    output = {
        "results": [
            {
                "id": r["id"],
                "name": r["name"],
                "qualified_name": r["qualified_name"],
                "kind": r["kind"],
                "file_path": r["file_path"],
                "language": r["language"],
                "signature": r.get("signature"),
                "docstring": r.get("docstring"),
            }
            for r in results
        ],
        "count": len(results),
    }

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _semantic_search(store: Store, args: dict) -> dict:
    """Relevance-ranked semantic search."""
    query = args.get("query", "")
    limit = args.get("limit", 20)

    if not query or not query.strip():
        return {
            "content": [{"type": "text", "text": json.dumps({"results": [], "count": 0})}]
        }

    try:
        from tws_graph.search.semantic import semantic_query

        result = semantic_query(store, query, limit=limit)
        output = {
            "results": [
                {
                    "id": r.get("id", ""),
                    "name": r.get("name", ""),
                    "qualified_name": r.get("qualified_name", ""),
                    "kind": r.get("kind", ""),
                    "file_path": r.get("file_path", ""),
                    "language": r.get("language", ""),
                    "_score": getattr(r, "_score", None) if not isinstance(r, dict) else r.get("_score"),
                }
                for r in result.results[:limit]
            ],
            "count": min(len(result.results), limit),
            "full_candidate_count": result.candidate_count,
            "duration_ms": getattr(result, "duration_ms", None),
            "signals_used": getattr(result, "signals_used", []),
        }
    except Exception as e:
        output = {"error": str(e), "results": [], "count": 0}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
