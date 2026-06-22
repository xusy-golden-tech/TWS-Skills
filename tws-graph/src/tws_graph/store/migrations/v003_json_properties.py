"""Migration v003: Add JSON properties columns to nodes and edges."""

VERSION = 3
DESCRIPTION = "Add JSON properties columns to nodes and edges"

UP_SQL = """
ALTER TABLE nodes ADD COLUMN properties TEXT DEFAULT '{}';
ALTER TABLE edges ADD COLUMN properties TEXT DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_framework ON nodes(framework);
CREATE INDEX IF NOT EXISTS idx_edges_source_target ON edges(source, target);
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_files_size_mtime ON files(size, modified_at);
"""

DOWN_SQL = """
-- SQLite does not support DROP COLUMN on earlier versions.  To roll
-- back properties columns, rebuild tables from backup.
"""
