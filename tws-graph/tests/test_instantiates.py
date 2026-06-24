"""TDD tests for instantiates edge detection (P26b v5.2.0).

Tests the resolve_instantiates function that creates instantiates edges
between calling functions and the classes they instantiate.
"""

import pytest

from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder
from tws_graph.edge_resolver import resolve_instantiates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_id(qualified_name: str, file_path: str = "test.py") -> str:
    import hashlib
    return hashlib.sha256(f"{qualified_name}{file_path}".encode()).hexdigest()


def _setup_instantiation_scenario(queries: QueryBuilder) -> dict:
    """Set up: function foo() creates OrderService() instance.

    Returns dict with node IDs.
    """
    # Class OrderService with __init__
    class_id = _make_node_id("test.py::OrderService")
    init_id = _make_node_id("test.py::OrderService::__init__")

    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'class', 'OrderService', 'test.py::OrderService', 'test.py',
                'python', 1, 20, 1000)
    """, (class_id,))
    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'method', '__init__', 'test.py::OrderService::__init__', 'test.py',
                'python', 3, 5, 1000)
    """, (init_id,))

    # Function create_order that calls OrderService()
    func_id = _make_node_id("test.py::create_order")
    queries._exec("""
        INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                     language, start_line, end_line, updated_at)
        VALUES (?, 'function', 'create_order', 'test.py::create_order', 'test.py',
                'python', 22, 30, 1000)
    """, (func_id,))

    # calls edge: create_order → OrderService.__init__
    queries._exec("""
        INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
        VALUES (?, ?, 'calls', 'test.py:25', 'resolved')
    """, (func_id, init_id))

    return {
        "class_id": class_id,
        "init_id": init_id,
        "func_id": func_id,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInstantiatesDetection:
    """Test that instantiates edges are correctly detected."""

    def test_instantiates_from_init_call(self, queries):
        """A call to __init__ should produce an instantiates edge to the class."""
        ids = _setup_instantiation_scenario(queries)
        queries.conn.commit()

        resolve_instantiates(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'instantiates'"
        ).fetchall()
        assert len(edges) == 1, f"Expected 1 instantiates edge, got {len(edges)}"
        assert edges[0]["source"] == ids["func_id"]
        assert edges[0]["target"] == ids["class_id"]

    def test_instantiates_provenance(self, queries):
        """Instantiates edges should have provenance='heuristic'."""
        ids = _setup_instantiation_scenario(queries)
        queries.conn.commit()

        resolve_instantiates(queries)

        edge = queries._exec(
            "SELECT * FROM edges WHERE kind = 'instantiates' LIMIT 1"
        ).fetchone()
        assert edge is not None
        assert edge["provenance"] == "heuristic"

    def test_instantiates_target_is_class(self, queries):
        """The target of an instantiates edge must always be a class node."""
        ids = _setup_instantiation_scenario(queries)
        queries.conn.commit()

        resolve_instantiates(queries)

        targets = queries._exec(
            "SELECT DISTINCT n.kind FROM edges e JOIN nodes n ON e.target = n.id WHERE e.kind = 'instantiates'"
        ).fetchall()
        for row in targets:
            assert row["kind"] == "class", f"Expected class, got {row['kind']}"

    def test_no_instantiates_for_regular_call(self, queries):
        """A regular function call (not to __init__) should not produce instantiates."""
        # Create two functions with a regular call between them
        callee_id = _make_node_id("test.py::helper")
        caller_id = _make_node_id("test.py::main")

        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, 'function', 'helper', 'test.py::helper', 'test.py',
                    'python', 1, 5, 1000)
        """, (callee_id,))
        queries._exec("""
            INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                         language, start_line, end_line, updated_at)
            VALUES (?, 'function', 'main', 'test.py::main', 'test.py',
                    'python', 7, 12, 1000)
        """, (caller_id,))
        queries._exec("""
            INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
            VALUES (?, ?, 'calls', 'test.py:10', 'resolved')
        """, (caller_id, callee_id))

        queries.conn.commit()

        resolve_instantiates(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'instantiates'"
        ).fetchall()
        assert len(edges) == 0, f"Expected 0 instantiates edges for regular call, got {len(edges)}"

    def test_constructor_call_across_languages(self, queries):
        """Test different constructor name patterns: __init__ (Python), constructor (TS), <init> (Java)."""
        # Python: __init__
        py_class_id = _make_node_id("a.py::PyClass")
        py_init_id = _make_node_id("a.py::PyClass::__init__")
        py_func_id = _make_node_id("a.py::make_py")

        # TypeScript: constructor
        ts_class_id = _make_node_id("b.ts::TsClass")
        ts_ctor_id = _make_node_id("b.ts::TsClass::constructor")
        ts_func_id = _make_node_id("b.ts::make_ts")

        # Java: <init>
        java_class_id = _make_node_id("c.java::JavaClass")
        java_init_id = _make_node_id("c.java::JavaClass::<init>")
        java_func_id = _make_node_id("c.java::make_java")

        for fid, cid, kind, name, qname, lang, filepath in [
            (py_class_id, None, "class", "PyClass", "a.py::PyClass", "python", "a.py"),
            (py_init_id, None, "method", "__init__", "a.py::PyClass::__init__", "python", "a.py"),
            (py_func_id, py_init_id, "function", "make_py", "a.py::make_py", "python", "a.py"),
            (ts_class_id, None, "class", "TsClass", "b.ts::TsClass", "typescript", "b.ts"),
            (ts_ctor_id, None, "method", "constructor", "b.ts::TsClass::constructor", "typescript", "b.ts"),
            (ts_func_id, ts_ctor_id, "function", "make_ts", "b.ts::make_ts", "typescript", "b.ts"),
            (java_class_id, None, "class", "JavaClass", "c.java::JavaClass", "java", "c.java"),
            (java_init_id, None, "method", "<init>", "c.java::JavaClass::<init>", "java", "c.java"),
            (java_func_id, java_init_id, "function", "make_java", "c.java::make_java", "java", "c.java"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 2, 1000)
            """, (fid, kind, name, qname, filepath, lang))

        # Create calls edges for each
        for func_id, constructor_id in [(py_func_id, py_init_id), (ts_func_id, ts_ctor_id), (java_func_id, java_init_id)]:
            queries._exec("""
                INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
                VALUES (?, ?, 'calls', 'test:1', 'resolved')
            """, (func_id, constructor_id))

        queries.conn.commit()

        resolve_instantiates(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'instantiates'"
        ).fetchall()
        assert len(edges) == 3, f"Expected 3 instantiates edges, got {len(edges)}"

        targets = {e["target"] for e in edges}
        assert py_class_id in targets
        assert ts_class_id in targets
        assert java_class_id in targets

    def test_multiple_instantiations(self, queries):
        """Multiple calls to the same class constructor should produce multiple instantiates edges."""
        class_id = _make_node_id("test.py::MyClass")
        init_id = _make_node_id("test.py::MyClass::__init__")
        func_a_id = _make_node_id("test.py::create_a")
        func_b_id = _make_node_id("test.py::create_b")

        for fid, kind, name, qname in [
            (class_id, "class", "MyClass", "test.py::MyClass"),
            (init_id, "method", "__init__", "test.py::MyClass::__init__"),
            (func_a_id, "function", "create_a", "test.py::create_a"),
            (func_b_id, "function", "create_b", "test.py::create_b"),
        ]:
            queries._exec("""
                INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path,
                                             language, start_line, end_line, updated_at)
                VALUES (?, ?, ?, ?, 'test.py', 'python', 1, 2, 1000)
            """, (fid, kind, name, qname))

        # Both functions call __init__
        for func_id in [func_a_id, func_b_id]:
            queries._exec("""
                INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance)
                VALUES (?, ?, 'calls', 'test.py:1', 'resolved')
            """, (func_id, init_id))

        queries.conn.commit()

        resolve_instantiates(queries)

        edges = queries._exec(
            "SELECT * FROM edges WHERE kind = 'instantiates'"
        ).fetchall()
        assert len(edges) == 2, f"Expected 2 instantiates edges, got {len(edges)}"
        sources = {e["source"] for e in edges}
        assert func_a_id in sources
        assert func_b_id in sources
