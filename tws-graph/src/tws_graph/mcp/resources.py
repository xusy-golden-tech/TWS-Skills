"""MCP resource handlers: tws://stats, tws://languages, tws://health."""

from __future__ import annotations

import os
import time
import json
from typing import Callable

from tws_graph.mcp.registry import ResourceRegistry, ResourceDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]

# Server start time for uptime calculation
_SERVER_START_TIME: float = time.time()


def register_resources(registry: ResourceRegistry, store_factory: StoreFactory) -> None:
    """Register all MCP resources on the given registry."""

    # -- tws://stats ----------------------------------------------------------
    registry.register(
        ResourceDefinition(
            uri="tws://stats",
            name="Code Graph Statistics",
            description="Node count, edge count, file count, languages, index time",
            mime_type="application/json",
        ),
        handler=lambda uri: _stats(store_factory()),
    )

    # -- tws://languages ------------------------------------------------------
    registry.register(
        ResourceDefinition(
            uri="tws://languages",
            name="Language Distribution",
            description="Per-language node count and file count",
            mime_type="application/json",
        ),
        handler=lambda uri: _languages(store_factory()),
    )

    # -- tws://health ---------------------------------------------------------
    registry.register(
        ResourceDefinition(
            uri="tws://health",
            name="Health Status",
            description="Server health: index ready, LSP available, uptime",
            mime_type="application/json",
        ),
        handler=lambda uri: _health(store_factory()),
    )


def reset_start_time() -> None:
    """Reset the server start time (useful for testing)."""
    global _SERVER_START_TIME
    _SERVER_START_TIME = time.time()


# ============================================================================
# Handler implementations
# ============================================================================


def _stats(store: Store) -> dict:
    """Collect and return code graph statistics."""
    try:
        node_count = store.count_nodes() if hasattr(store, "count_nodes") else 0
        edge_count = store.count_edges() if hasattr(store, "count_edges") else 0

        # Count files
        file_count = 0
        try:
            if hasattr(store, "iter_all_nodes"):
                files: set[str] = set()
                for node in store.iter_all_nodes(batch_size=10000):
                    files.add(node.get("file_path", ""))
                file_count = len(files)
        except Exception:
            pass

        # Language distribution
        languages: dict[str, int] = {}
        try:
            if hasattr(store, "iter_all_nodes"):
                for node in store.iter_all_nodes(batch_size=10000):
                    lang = node.get("language", "unknown")
                    languages[lang] = languages.get(lang, 0) + 1
        except Exception:
            pass

        # Index time
        index_time = None
        try:
            db_conn = getattr(store, "_conn", None) or getattr(store, "conn", None)
            if db_conn:
                import sqlite3
                conn = db_conn if isinstance(db_conn, sqlite3.Connection) else db_conn.conn
                cursor = conn.execute(
                    "SELECT MAX(indexed_at) FROM files"
                )
                result = cursor.fetchone()
                if result and result[0]:
                    from datetime import datetime, timezone
                    index_time = datetime.fromtimestamp(
                        result[0] / 1000, tz=timezone.utc
                    ).isoformat()
        except Exception:
            pass

        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "file_count": file_count,
            "languages": languages,
            "index_time": index_time,
        }
    except Exception as e:
        return {"error": str(e), "node_count": 0, "edge_count": 0, "file_count": 0}


def _languages(store: Store) -> dict:
    """Return per-language distribution."""
    try:
        lang_nodes: dict[str, int] = {}
        lang_files: dict[str, set[str]] = {}

        if hasattr(store, "iter_all_nodes"):
            for node in store.iter_all_nodes(batch_size=10000):
                lang = node.get("language", "unknown")
                lang_nodes[lang] = lang_nodes.get(lang, 0) + 1
                if lang not in lang_files:
                    lang_files[lang] = set()
                lang_files[lang].add(node.get("file_path", ""))

        languages = [
            {
                "name": lang,
                "node_count": count,
                "file_count": len(lang_files.get(lang, set())),
            }
            for lang, count in sorted(lang_nodes.items(), key=lambda x: -x[1])
        ]

        return {"languages": languages, "total_languages": len(languages)}
    except Exception as e:
        return {"error": str(e), "languages": []}


def _health(store: Store) -> dict:
    """Return health status."""
    try:
        index_ready = False
        try:
            if hasattr(store, "count_nodes"):
                count = store.count_nodes()
                index_ready = count > 0
        except Exception:
            pass

        lsp_available = False
        try:
            from tws_graph.lsp.adapters import get_all_adapters
            from tws_graph.lsp.discovery import discover_all

            adapters = get_all_adapters()
            discovery = discover_all(adapters)
            lsp_available = any(r.available for r in discovery.values())
        except Exception:
            pass

        uptime = time.time() - _SERVER_START_TIME
        status = "ok" if index_ready else "degraded"

        return {
            "status": status,
            "index_ready": index_ready,
            "lsp_available": lsp_available,
            "uptime_seconds": round(uptime, 1),
        }
    except Exception as e:
        return {
            "status": "error",
            "index_ready": False,
            "lsp_available": False,
            "uptime_seconds": 0,
            "error": str(e),
        }
