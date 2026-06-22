"""Store module — abstract graph data storage layer.

Public exports:
    - Store (ABC)           — from interface.py
    - SqliteStore           — from sqlite_store.py
    - MemoryStore           — from memory_store.py
    - Type definitions      — from types.py
    - Exception classes     — from exceptions.py

Internal modules (query_builder, connection, migrations) are NOT exported.
"""

from .exceptions import (
    StoreError,
    StoreClosedError,
    TransactionError,
    NodeNotFoundError,
    EdgeNotFoundError,
    SchemaVersionError,
    MigrationError,
)
from .interface import Store
from .memory_store import MemoryStore
from .sqlite_store import SqliteStore
from .types import (  # noqa: F401 — re-export for convenience
    Direction,
    EdgeRecord,
    FileRecord,
    NodeRecord,
    SearchResult,
    StoreStats,
    UnresolvedRefRecord,
)

__all__ = [
    # Interface
    "Store",
    # Implementations
    "SqliteStore",
    "MemoryStore",
    # Exceptions
    "StoreError",
    "StoreClosedError",
    "TransactionError",
    "NodeNotFoundError",
    "EdgeNotFoundError",
    "SchemaVersionError",
    "MigrationError",
    # Types
    "NodeRecord",
    "EdgeRecord",
    "FileRecord",
    "UnresolvedRefRecord",
    "StoreStats",
    "SearchResult",
    "Direction",
]
