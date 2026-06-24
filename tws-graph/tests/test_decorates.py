"""TDD tests for decorates edge detection (P26c v5.2.0).

Tests that decorator/annotation application produces decorates edges.
"""

import pytest

from tws_graph.indexer.parser import extract_full


class TestDecoratesDetection:
    """Test that decorates edges are correctly detected during extraction."""

    def test_python_decorator_simple(self):
        """A simple @decorator on a function should produce a decorates edge."""
        src = """
def my_decorator(func):
    return func

@my_decorator
def decorated_func():
    pass
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1, f"Expected 1 decorates edge, got {len(dec_edges)}"
        assert dec_edges[0]["target_text"] == "@my_decorator"

    def test_python_decorator_staticmethod(self):
        """@staticmethod should produce a decorates edge."""
        src = """
class MyClass:
    @staticmethod
    def my_method():
        pass
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1, f"Expected 1 decorates edge, got {len(dec_edges)}"
        assert dec_edges[0]["target_text"] == "@staticmethod"

    def test_python_decorator_call_expression(self):
        """@app.route('/path') should produce a decorates edge with 'app.route'."""
        src = """
@app.route("/home")
def home():
    pass
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1
        assert "@app.route" in dec_edges[0]["target_text"]

    def test_python_multiple_decorators(self):
        """Multiple decorators on one function should produce multiple decorates edges."""
        src = """
@staticmethod
@login_required
def secure_method():
    pass
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 2, f"Expected 2 decorates edges, got {len(dec_edges)}"
        target_texts = {e["target_text"] for e in dec_edges}
        assert "@staticmethod" in target_texts
        assert "@login_required" in target_texts

    def test_python_class_decorator(self):
        """Decorators on classes should also produce decorates edges."""
        src = """
@dataclass
class User:
    name: str
    age: int
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1
        assert dec_edges[0]["target_text"] == "@dataclass"

    def test_python_no_decorator_no_edge(self):
        """Functions without decorators should not produce decorates edges."""
        src = """
def plain_function():
    pass
"""
        result = extract_full("test.py", src, "python")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 0

    def test_decorates_source_is_decorated_node(self, queries):
        """The source of a decorates edge should be the decorated function/class node."""
        import hashlib

        src = """
def my_decorator(func):
    return func

@my_decorator
def target_func():
    pass
"""
        result = extract_full("test.py", src, "python")
        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1

        # The source should be the decorated function's node ID
        raw = "test.py:test.py::target_func"
        target_func_id = hashlib.sha256(raw.encode()).hexdigest()[:32]
        assert dec_edges[0]["source"] == target_func_id

    def test_typescript_decorator(self):
        """TypeScript @Decorator should produce decorates edges."""
        src = """
function Log(target: any, key: string) {}

class MyService {
    @Log
    getData(): string {
        return "data";
    }
}
"""
        result = extract_full("test.ts", src, "typescript")

        dec_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(dec_edges) == 1, f"Expected 1 decorates edge, got {len(dec_edges)}"
        assert dec_edges[0]["target_text"] == "@Log"
