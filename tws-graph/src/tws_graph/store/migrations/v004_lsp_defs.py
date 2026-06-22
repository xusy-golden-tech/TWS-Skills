"""Migration v004: Create lsp_defs table for per-module definition index."""

VERSION = 4
DESCRIPTION = "Create lsp_defs table for per-module def index"

UP_SQL = """
CREATE TABLE IF NOT EXISTS lsp_defs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module_path     TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    simple_name     TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    parent_scope    TEXT,
    UNIQUE(module_path, qualified_name)
);

CREATE INDEX IF NOT EXISTS idx_lsp_defs_module ON lsp_defs(module_path);
CREATE INDEX IF NOT EXISTS idx_lsp_defs_simple_name ON lsp_defs(simple_name);
CREATE INDEX IF NOT EXISTS idx_lsp_defs_qualified ON lsp_defs(qualified_name);
CREATE INDEX IF NOT EXISTS idx_lsp_defs_node ON lsp_defs(node_id);
"""

DOWN_SQL = """
DROP TABLE IF EXISTS lsp_defs;
"""
