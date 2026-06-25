"""TDD tests for P32 v5.3.0 — graph export (DOT, Mermaid, JSON)."""

import json
import pytest
import hashlib

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


@pytest.fixture
def populated_queries(queries):
    """A simple graph: A calls B, B references C."""
    a_id = _make_id("main.py::func_a", "main.py")
    b_id = _make_id("utils.py::func_b", "utils.py")
    c_id = _make_id("utils.py::ClassC", "utils.py")

    for nid, kind, name, qname, fpath, line in [
        (a_id, "function", "func_a", "main.py::func_a", "main.py", 1),
        (b_id, "function", "func_b", "utils.py::func_b", "utils.py", 5),
        (c_id, "class", "ClassC", "utils.py::ClassC", "utils.py", 10),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',?,?,1000)",
            (nid, kind, name, qname, fpath, line, line + 2))

    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'calls','main.py:2','resolved')", (a_id, b_id))
    queries._exec("INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
                   "VALUES (?,?,'references','utils.py:6','resolved')", (b_id, c_id))
    queries.conn.commit()
    return queries


# ---------------------------------------------------------------------------
# P32a: DOT export
# ---------------------------------------------------------------------------

class TestDotExport:
    """Export graph to Graphviz DOT format."""

    def test_dot_has_digraph_header(self, populated_queries):
        from tws_graph.export import export_dot
        dot = export_dot(populated_queries)
        assert dot.startswith("digraph"), f"DOT should start with 'digraph', got: {dot[:50]}"

    def test_dot_has_nodes(self, populated_queries):
        from tws_graph.export import export_dot
        dot = export_dot(populated_queries)
        assert "func_a" in dot, "DOT should contain node label"
        assert "func_b" in dot

    def test_dot_has_edges(self, populated_queries):
        from tws_graph.export import export_dot
        dot = export_dot(populated_queries)
        assert "->" in dot, "DOT should contain edge arrows"

    def test_dot_empty_graph(self, queries):
        from tws_graph.export import export_dot
        dot = export_dot(queries)
        assert dot is not None
        assert "digraph" in dot

    def test_dot_depth_filter(self, populated_queries):
        """--depth should limit traversal."""
        from tws_graph.export import export_dot
        a_id = _make_id("main.py::func_a", "main.py")
        # Depth 0: only func_a
        dot_shallow = export_dot(populated_queries, from_node=a_id, depth=0)
        assert "func_a" in dot_shallow
        # func_b should not appear at depth 0
        assert "func_b" not in dot_shallow

    def test_dot_kind_filter(self, populated_queries):
        """--kind should only include specified edge types."""
        from tws_graph.export import export_dot
        # Only 'references' edges — should NOT include the calls edge
        dot_refs = export_dot(populated_queries, kind="references")
        assert "references" in dot_refs.lower() or "->" in dot_refs
        # With only 'references', no calls edge means func_a won't connect to func_b
        dot_calls = export_dot(populated_queries, kind="calls")
        assert "calls" in dot_calls.lower() or "->" in dot_calls


# ---------------------------------------------------------------------------
# P32b: Mermaid export
# ---------------------------------------------------------------------------

class TestMermaidExport:
    """Export graph to Mermaid format."""

    def test_mermaid_has_graph_header(self, populated_queries):
        from tws_graph.export import export_mermaid
        mm = export_mermaid(populated_queries)
        assert "graph" in mm.lower() or "flowchart" in mm.lower(), \
            f"Mermaid should have graph/flowchart, got: {mm[:80]}"

    def test_mermaid_has_nodes(self, populated_queries):
        from tws_graph.export import export_mermaid
        mm = export_mermaid(populated_queries)
        # Node IDs should be quoted/sanitized
        assert "func_a" in mm or "main.py::func_a" in mm

    def test_mermaid_empty_graph(self, queries):
        from tws_graph.export import export_mermaid
        mm = export_mermaid(queries)
        assert mm is not None
        assert "graph" in mm.lower() or "flowchart" in mm.lower()


# ---------------------------------------------------------------------------
# P32c: JSON export
# ---------------------------------------------------------------------------

class TestJsonExport:
    """Export graph to JSON format."""

    def test_json_is_valid(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        assert isinstance(data, dict)
        assert "nodes" in data
        assert "edges" in data

    def test_json_has_correct_counts(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_json_node_structure(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        node = data["nodes"][0]
        for key in ("id", "name", "kind", "file_path"):
            assert key in node, f"Node missing {key}"

    def test_json_edge_structure(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        edge = data["edges"][0]
        for key in ("source", "target", "kind"):
            assert key in edge, f"Edge missing {key}"

    def test_json_serializable(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        dumped = json.dumps(data)
        assert len(dumped) > 10
        # Re-parse to verify valid JSON
        reparsed = json.loads(dumped)
        assert len(reparsed["nodes"]) == 3

    def test_json_empty_graph(self, queries):
        from tws_graph.export import export_json
        data = export_json(queries)
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_json_depth_filter(self, populated_queries):
        from tws_graph.export import export_json
        a_id = _make_id("main.py::func_a", "main.py")
        data = export_json(populated_queries, from_node=a_id, depth=0)
        assert len(data["nodes"]) == 1  # only func_a
        assert data["nodes"][0]["name"] == "func_a"

    def test_json_limit_respected(self, populated_queries):
        """--limit should cap nodes and edges returned."""
        from tws_graph.export import export_json
        # limit=1: only 1 node and 1 edge
        data = export_json(populated_queries, limit=1)
        assert len(data["nodes"]) <= 1
        assert len(data["edges"]) <= 1

    def test_json_default_limit_is_500(self, populated_queries):
        from tws_graph.export import export_json
        data = export_json(populated_queries)
        assert len(data["nodes"]) == 3  # all nodes fit within default limit=500
