"""Migration v007: Add source column to unresolved_refs."""

VERSION = 7
DESCRIPTION = "Add source column to unresolved_refs for provenance tracking"

UP_SQL = """
ALTER TABLE unresolved_refs ADD COLUMN source TEXT NOT NULL DEFAULT 'tree-sitter';
"""

DOWN_SQL = """
-- SQLite does not support DROP COLUMN on earlier versions.  To roll
-- back the source column, rebuild the unresolved_refs table.
"""
