"""Framework route detection.

Detects web/API frameworks in a project and extracts route nodes + handler edges.
Each framework gets its own resolver class implementing ``BaseFrameworkResolver``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .base import BaseFrameworkResolver
from ..db.queries import QueryBuilder


@dataclass
class FrameworkDetectionResult:
    frameworks_detected: list[str] = field(default_factory=list)
    routes_found: int = 0


def detect_frameworks(root_dir: str, queries: QueryBuilder) -> FrameworkDetectionResult:
    """Auto-detect frameworks and extract routes into the graph."""
    from .fastapi import FastAPIResolver

    result = FrameworkDetectionResult()
    resolvers: list[BaseFrameworkResolver] = [
        FastAPIResolver(),
    ]

    for resolver in resolvers:
        if resolver.detect(root_dir):
            result.frameworks_detected.append(resolver.framework_name)
            routes = resolver.extract_routes(root_dir, queries)
            if routes:
                queries.conn.execute("BEGIN")
                try:
                    queries.insert_nodes(routes["nodes"])
                    queries.insert_edges(routes["edges"])
                    queries.conn.execute("COMMIT")
                except Exception:
                    queries.conn.execute("ROLLBACK")
                result.routes_found += len(routes["nodes"])

    return result
