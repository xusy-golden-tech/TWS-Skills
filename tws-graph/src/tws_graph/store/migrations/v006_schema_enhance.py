"""Migration v006: Enhance schema_versions with checksum and enrich descriptions."""

VERSION = 6
DESCRIPTION = "Enhance schema_versions with checksum column and enrich descriptions"

UP_SQL = """
ALTER TABLE schema_versions ADD COLUMN checksum TEXT;

UPDATE schema_versions
SET description = 'Initial schema v1'
WHERE version = 1 AND (description IS NULL OR description = '');

UPDATE schema_versions
SET description = 'Add is_external to unresolved_refs'
WHERE version = 2 AND (description IS NULL OR description = '');
"""

DOWN_SQL = """
-- SQLite 3.35+ would allow ALTER TABLE schema_versions DROP COLUMN checksum,
-- but we leave this blank for compatibility with older versions.
"""
