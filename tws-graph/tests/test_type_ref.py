"""TDD tests for type_ref edge detection (P26d v5.2.0).

Tests that type annotation references produce type_ref edges.
"""

import pytest

from tws_graph.indexer.parser import extract_full


class TestTypeRefDetection:
    """Test that type_ref edges are correctly detected during extraction."""

    def test_python_param_type_annotation(self):
        """Function parameter type annotation should produce type_ref edge."""
        src = """
class OrderService:
    def process(self, amount: float) -> bool:
        return amount > 0
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        # 'float' and 'bool' are built-in types — should be filtered
        assert len(type_refs) == 0, f"Built-in types should be filtered, got {len(type_refs)}"

    def test_python_custom_type_annotation(self):
        """References to custom types should produce type_ref edges."""
        src = """
class OrderService:
    def create_order(self, items: list, user_id: int) -> dict:
        pass
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        # list, int, dict are built-in → should be filtered
        assert len(type_refs) == 0

    def test_python_imported_type(self):
        """Type annotation referencing an imported class should produce type_ref."""
        src = """
from models import User

class UserService:
    def get_user(self, user_id: int) -> User:
        pass
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        # 'int' filtered, 'User' should produce type_ref
        assert len(type_refs) >= 1, f"Expected at least 1 type_ref for User, got {len(type_refs)}"
        user_refs = [e for e in type_refs if e["target_text"] == "User"]
        assert len(user_refs) == 1

    def test_python_builtin_filtered(self):
        """Common built-in types should be filtered from type_ref edges."""
        src = """
def func(a: int, b: str, c: float, d: bool, e: list, f: dict, g: tuple,
         h: set, i: bytes, j: complex) -> None:
    pass
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_refs) == 0, f"All built-in types should be filtered, got {len(type_refs)}"

    def test_python_return_type(self):
        """Return type annotation referencing custom type should produce type_ref."""
        src = """
class Order:
    pass

class OrderService:
    def create(self) -> Order:
        return Order()
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        order_refs = [e for e in type_refs if e["target_text"] == "Order"]
        assert len(order_refs) >= 1, f"Expected type_ref for Order return type, got {len(order_refs)}"

    def test_type_ref_provenance(self):
        """Type ref edges should have provenance='tree-sitter'."""
        src = """
class MyService:
    pass

class Controller:
    def handle(self) -> MyService:
        return MyService()
"""
        result = extract_full("test.py", src, "python")

        type_refs = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_refs) >= 1
        for e in type_refs:
            assert e["provenance"] == "tree-sitter"
