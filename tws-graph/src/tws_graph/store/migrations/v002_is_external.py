"""Migration v002: Add is_external column to unresolved_refs."""

VERSION = 2
DESCRIPTION = "Add is_external column to unresolved_refs"

UP_SQL = """
ALTER TABLE unresolved_refs ADD COLUMN is_external INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_urefs_is_external ON unresolved_refs(is_external);
"""

DOWN_SQL = """
-- SQLite 3.35+ supports DROP COLUMN but not all versions.  In older
-- versions the only way to remove is_external is to rebuild the table.
-- This is intentionally left blank: rolling back past v1 already drops
-- the whole table (see v001_initial/DOWN_SQL).
"""
