"""Migration v001: Initial schema — nodes, edges, files, unresolved_refs, schema_versions."""

VERSION = 1
DESCRIPTION = "Initial schema: nodes, edges, files, unresolved_refs, schema_versions"

UP_SQL = """
-- =============================================================================
-- Schema version tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL,
    description TEXT
);

-- =============================================================================
-- Nodes: every code symbol
-- =============================================================================
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    signature       TEXT,
    docstring       TEXT,
    visibility      TEXT,
    is_abstract     INTEGER DEFAULT 0,
    is_exported     INTEGER DEFAULT 0,
    decorators      TEXT,
    framework       TEXT,
    updated_at      INTEGER NOT NULL
);

-- =============================================================================
-- Edges: relationships between symbols
-- =============================================================================
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target      TEXT NOT NULL,
    target_text TEXT,
    kind        TEXT NOT NULL,
    source_loc  TEXT,
    provenance  TEXT DEFAULT 'tree-sitter'
);

-- =============================================================================
-- Files: tracked source files
-- =============================================================================
CREATE TABLE IF NOT EXISTS files (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    language     TEXT NOT NULL,
    node_count   INTEGER DEFAULT 0,
    indexed_at   INTEGER NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,
    modified_at  INTEGER NOT NULL DEFAULT 0
);

-- =============================================================================
-- Unresolved references: cross-file / external calls not yet resolved
-- =============================================================================
CREATE TABLE IF NOT EXISTS unresolved_refs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    reference_name  TEXT NOT NULL,
    reference_kind  TEXT NOT NULL DEFAULT 'call',
    line            INTEGER NOT NULL DEFAULT 0,
    col             INTEGER NOT NULL DEFAULT 0,
    candidates      TEXT,
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL
);

-- =============================================================================
-- Indexes
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);

CREATE INDEX IF NOT EXISTS idx_urefs_from_node ON unresolved_refs(from_node_id);
CREATE INDEX IF NOT EXISTS idx_urefs_name ON unresolved_refs(reference_name);
CREATE INDEX IF NOT EXISTS idx_urefs_file ON unresolved_refs(file_path);
CREATE INDEX IF NOT EXISTS idx_urefs_lang ON unresolved_refs(language);

-- =============================================================================
-- FTS5: full-text search over node names, signatures, and docstrings
-- =============================================================================
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS nodes_fts_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_fts_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;
"""

DOWN_SQL = """
-- SQLite does not support DROP COLUMN; full rollback would require
-- dropping all tables and rebuilding from backup.
DROP TABLE IF EXISTS nodes_fts;
DROP TRIGGER IF EXISTS nodes_fts_ai;
DROP TRIGGER IF EXISTS nodes_fts_ad;
DROP TRIGGER IF EXISTS nodes_fts_au;
DROP TABLE IF EXISTS unresolved_refs;
DROP TABLE IF EXISTS edges;
DROP TABLE IF EXISTS nodes;
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS schema_versions;
"""
