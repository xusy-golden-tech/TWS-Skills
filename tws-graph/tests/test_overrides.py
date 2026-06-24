"""TDD tests for overrides edge detection (P26a v5.2.0).

Tests the resolve_overrides function that creates overrides edges
between child class methods and parent class methods.
"""

import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.edge_resolver import resolve_overrides


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_id(qualified_name: str, file_path: str = "test.py") -> str:
    """Replicate the node ID hashing logic."""
    import hashlib
    return hashlib.sha256(f"{qualified_name}{file_path}".encode()).hexdigest()


def _setup_class_hierarchy(queries: QueryBuilder) -> dict:
    """Set up a simple class hierarchy in the test database.

    Creates:
      - ParentClass with method foo()
      - ChildClass extends ParentClass with method foo() (override)
      - ChildClass.bar() (new method, not override)
    """
    # Parent class with method foo
    parent_id = _make_node_id("test.py::ParentClass")
    parent_foo_id = _make_node_id("test.py::ParentClass::foo")

    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'class', 'ParentClass', 'test.py::ParentClass', 'test.py',
                'python', 1, 10, 1000)
    """, (parent_id,))
    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'method', 'foo', 'test.py::ParentClass::foo', 'test.py',
                'python', 3, 5, 1000)
    """, (parent_foo_id,))

    # Child class with method foo (overrides parent) and bar (new)
    child_id = _make_node_id("test.py::ChildClass")
    child_foo_id = _make_node_id("test.py::ChildClass::foo")
    child_bar_id = _make_node_id("test.py::ChildClass::bar")

    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'class', 'ChildClass', 'test.py::ChildClass', 'test.py',
                'python', 12, 25, 1000)
    """, (child_id,))
    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'method', 'foo', 'test.py::ChildClass::foo', 'test.py',
                'python', 15, 18, 1000)
    """, (child_foo_id,))
    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'method', 'bar', 'test.py::ChildClass::bar', 'test.py',
                'python', 20, 23, 1000)
    """, (child_bar_id,))

    # extends edge: ChildClass → ParentClass
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, 'dummy_target', 'extends', 'test.py:12', 'tree-sitter')
    """, (child_id,))

    # Update the extends edge to point to parent (simulate resolved edge)
    queries._exec("""
        UPDATE edges SET target = ? WHERE source = ? AND kind = 'extends'
    """, (parent_id, child_id))

    return {
        "parent_id": parent_id,
        "parent_foo_id": parent_foo_id,
        "child_id": child_id,
        "child_foo_id": child_foo_id,
        "child_bar_id": child_bar_id,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOverridesDetection:
    """Test that overrides edges are correctly detected."""

    def test_overrides_edge_created(self, queries):
        """A child method that overrides parent method should produce an overrides edge."""
        ids = _setup_class_hierarchy(queries)
        queries.conn.commit()

        resolve_overrides(queries)

        # Check: overrides edge from child.foo → parent.foo
        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides'"
        ).fetchall()
        assert len(edges) == 1, f"Expected 1 overrides edge, got {len(edges)}"
        assert edges[0]["source"] == ids["child_foo_id"]
        assert edges[0]["target"] == ids["parent_foo_id"]

    def test_no_overrides_for_new_method(self, queries):
        """A child method that does NOT exist in parent should not produce overrides."""
        ids = _setup_class_hierarchy(queries)
        queries.conn.commit()

        resolve_overrides(queries)

        # bar() is only in child, not in parent → no overrides
        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides' AND source = ?",
            (ids["child_bar_id"],)
        ).fetchall()
        assert len(edges) == 0

    def test_no_overrides_without_extends(self, queries):
        """Methods without extends relationship should not produce overrides."""
        # Insert two unrelated classes with same method name
        class_a_id = _make_node_id("test.py::ClassA")
        class_b_id = _make_node_id("test.py::ClassB")
        foo_a_id = _make_node_id("test.py::ClassA::foo")
        foo_b_id = _make_node_id("test.py::ClassB::foo")

        for node_id, kind, name, qname in [
            (class_a_id, "class", "ClassA", "test.py::ClassA"),
            (class_b_id, "class", "ClassB", "test.py::ClassB"),
            (foo_a_id, "method", "foo", "test.py::ClassA::foo"),
            (foo_b_id, "method", "foo", "test.py::ClassB::foo"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, 'test.py', 'python', 1, 2, 1000)
            """, (node_id, kind, name, qname))

        queries.conn.commit()

        resolve_overrides(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides'"
        ).fetchall()
        assert len(edges) == 0

    def test_multi_level_override(self, queries):
        """Overrides should be detected in multi-level inheritance (grandchild overrides grandparent)."""
        # GrandParent.foo, Parent extends GrandParent (no override), GrandChild extends Parent, GrandChild.foo
        gp_id = _make_node_id("test.py::GrandParent")
        p_id = _make_node_id("test.py::Parent")
        gc_id = _make_node_id("test.py::GrandChild")
        gp_foo_id = _make_node_id("test.py::GrandParent::foo")
        gc_foo_id = _make_node_id("test.py::GrandChild::foo")

        for node_id, kind, name, qname in [
            (gp_id, "class", "GrandParent", "test.py::GrandParent"),
            (p_id, "class", "Parent", "test.py::Parent"),
            (gc_id, "class", "GrandChild", "test.py::GrandChild"),
            (gp_foo_id, "method", "foo", "test.py::GrandParent::foo"),
            (gc_foo_id, "method", "foo", "test.py::GrandChild::foo"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, 'test.py', 'python', 1, 2, 1000)
            """, (node_id, kind, name, qname))

        # extends: GrandChild → Parent → GrandParent
        for child_id, parent_id in [(gc_id, p_id), (p_id, gp_id)]:
            queries._exec("""
                INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
                VALUES (?, ?, 'extends', 'test.py:1', 'tree-sitter')
            """, (child_id, parent_id))

        queries.conn.commit()

        resolve_overrides(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides'"
        ).fetchall()
        # GrandChild.foo overrides GrandParent.foo (via Parent which has no foo)
        assert len(edges) == 1
        assert edges[0]["source"] == gc_foo_id
        assert edges[0]["target"] == gp_foo_id

    def test_no_self_override(self, queries):
        """A class should not override its own methods."""
        class_id = _make_node_id("test.py::SelfClass")
        method_id = _make_node_id("test.py::SelfClass::foo")

        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, 'class', 'SelfClass', 'test.py::SelfClass', 'test.py',
                    'python', 1, 10, 1000)
        """, (class_id,))
        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, 'method', 'foo', 'test.py::SelfClass::foo', 'test.py',
                    'python', 3, 5, 1000)
        """, (method_id,))
        queries.conn.commit()

        resolve_overrides(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides'"
        ).fetchall()
        assert len(edges) == 0

    def test_overrides_provenance(self, queries):
        """Overrides edges should have provenance='heuristic'."""
        ids = _setup_class_hierarchy(queries)
        queries.conn.commit()

        resolve_overrides(queries)

        edge = queries._exec(
            "SELECT * FROM edges WHERE kind = 'overrides' LIMIT 1"
        ).fetchone()
        assert edge is not None
        assert edge["provenance"] == "heuristic"
