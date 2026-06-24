-- TWS Code Graph Schema v1

-- =============================================================================
-- Schema version tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL,
    description TEXT
);

INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
VALUES (1, CAST(strftime('%s', 'now') AS INTEGER) * 1000, 'Initial schema v1');

-- =============================================================================
-- Nodes: every code symbol
-- =============================================================================
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,       -- hash(qualified_name + file_path), no line number
    kind            TEXT NOT NULL,          -- function/class/method/interface/route/variable
    name            TEXT NOT NULL,          -- simple name, e.g. "calculateTotal"
    qualified_name  TEXT NOT NULL,          -- "src/utils.py::MathHelper.calculateTotal"
    file_path       TEXT NOT NULL,          -- relative to project root
    language        TEXT NOT NULL,          -- python/typescript/java
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    signature       TEXT,                   -- function signature
    docstring       TEXT,                   -- first 200 chars
    visibility      TEXT,                   -- public/private/protected/internal
    is_abstract     INTEGER DEFAULT 0,
    is_exported     INTEGER DEFAULT 0,
    decorators      TEXT,                   -- JSON array
    framework       TEXT,                   -- fastapi/express/spring (route nodes only)
    properties      TEXT DEFAULT '{}',      -- JSON object for arbitrary properties
    body            TEXT,                   -- function/method body source text
    updated_at      INTEGER NOT NULL
);

-- =============================================================================
-- Edges: relationships between symbols
-- =============================================================================
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target      TEXT NOT NULL,              -- may reference nodes not yet indexed (cross-file calls)
    target_text TEXT,                       -- unhashed qualified name used to compute target (for post-processing)
    kind        TEXT NOT NULL,              -- calls/imports/extends/implements/references/contains
    source_loc  TEXT,                       -- "file:line:col"
    provenance  TEXT DEFAULT 'tree-sitter', -- tree-sitter | heuristic | resolved | unresolved | ambiguous
    properties  TEXT DEFAULT '{}'           -- JSON metadata (e.g. similarity score for similar_to edges)
);

-- =============================================================================
-- Files: tracked source files
-- =============================================================================
CREATE TABLE IF NOT EXISTS files (
    path         TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,             -- SHA256
    language     TEXT NOT NULL,
    node_count   INTEGER DEFAULT 0,
    indexed_at   INTEGER NOT NULL,
    size         INTEGER NOT NULL DEFAULT 0,        -- file size in bytes (for stat pre-filter)
    modified_at  INTEGER NOT NULL DEFAULT 0         -- mtime in seconds (for stat pre-filter)
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
-- P28 v5.2.0 performance indices
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_edges_kind_provenance ON edges(kind, provenance);

-- =============================================================================
-- Unresolved references: cross-file / external calls not yet resolved
-- =============================================================================
CREATE TABLE IF NOT EXISTS unresolved_refs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    reference_name  TEXT NOT NULL,
    reference_kind  TEXT NOT NULL DEFAULT 'call',  -- call / import / reference
    line            INTEGER NOT NULL DEFAULT 0,
    col             INTEGER NOT NULL DEFAULT 0,
    candidates      TEXT,                           -- JSON array of candidate node_ids
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL,
    is_external     INTEGER NOT NULL DEFAULT 0   -- 0=internal (project), 1=external (SDK/lib)
);

CREATE INDEX IF NOT EXISTS idx_urefs_from_node ON unresolved_refs(from_node_id);
CREATE INDEX IF NOT EXISTS idx_urefs_name ON unresolved_refs(reference_name);
CREATE INDEX IF NOT EXISTS idx_urefs_file ON unresolved_refs(file_path);
CREATE INDEX IF NOT EXISTS idx_urefs_lang ON unresolved_refs(language);
CREATE INDEX IF NOT EXISTS idx_urefs_is_external ON unresolved_refs(is_external);

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

-- Triggers to keep FTS index in sync with nodes table
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
