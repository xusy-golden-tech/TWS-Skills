"""Migration v005: Create community_assignments table for community detection results."""

VERSION = 5
DESCRIPTION = "Create community_assignments table for community detection results"

UP_SQL = """
CREATE TABLE IF NOT EXISTS community_assignments (
    node_id         TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    community_id    INTEGER NOT NULL,
    modularity      REAL,
    computed_at     INTEGER NOT NULL,
    algorithm       TEXT NOT NULL DEFAULT 'louvain',
    PRIMARY KEY (node_id, community_id)
);

CREATE INDEX IF NOT EXISTS idx_community_nodes ON community_assignments(node_id);
CREATE INDEX IF NOT EXISTS idx_community_id ON community_assignments(community_id);
"""

DOWN_SQL = """
DROP TABLE IF EXISTS community_assignments;
"""
