"""Compat shim — re-exports from ``store.query_builder``.

The QueryBuilder implementation was migrated to ``store/query_builder.py``
(see ``design-store-schema.md`` Section 6.4).  This module exists for
backward compatibility so existing import paths continue to work:

    from tws_graph.db.queries import QueryBuilder, _edit_distance, ...

New code should go through the Store interface instead of importing
QueryBuilder directly.
"""

from ..store.query_builder import (  # noqa: F401 — re-export
    QueryBuilder,
    _hash_id,
    _now_ms,
    _to_json,
    _edit_distance,
    _parse_field_qualifiers,
)

# Also re-export QueryBuilder's static method for direct import
_build_fts_query = QueryBuilder._build_fts_query
