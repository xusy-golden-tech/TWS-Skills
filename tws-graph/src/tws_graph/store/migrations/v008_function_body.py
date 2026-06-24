"""Migration v008: Add body column to nodes for function body text storage.

Used by CloneDetector (similar_to edges) and other analysis passes that
need access to the source code of function/method bodies.
"""

VERSION = 8
DESCRIPTION = "Add body column to nodes for function body text storage"

UP_SQL = """
ALTER TABLE nodes ADD COLUMN body TEXT;
"""

DOWN_SQL = """
-- SQLite does not support DROP COLUMN on earlier versions.
-- To roll back, rebuild the nodes table without the body column.
"""
