"""Tests for store.exceptions — Store-level exception classes."""

import pytest


class TestStoreError:
    """Tests for the base StoreError class."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import StoreError
        e = StoreError()
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import StoreError
        e = StoreError("database connection lost")
        assert str(e) == "database connection lost"

    def test_is_catchable_as_exception(self):
        from tws_graph.store.exceptions import StoreError
        with pytest.raises(StoreError):
            raise StoreError("test")


class TestStoreClosedError:
    """Tests for StoreClosedError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import StoreClosedError, StoreError
        e = StoreClosedError()
        assert isinstance(e, StoreClosedError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import StoreClosedError
        e = StoreClosedError("store has been closed")
        assert str(e) == "store has been closed"


class TestTransactionError:
    """Tests for TransactionError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import TransactionError, StoreError
        e = TransactionError()
        assert isinstance(e, TransactionError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import TransactionError
        e = TransactionError("nested transaction not allowed")
        assert str(e) == "nested transaction not allowed"


class TestNodeNotFoundError:
    """Tests for NodeNotFoundError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import NodeNotFoundError, StoreError
        e = NodeNotFoundError()
        assert isinstance(e, NodeNotFoundError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import NodeNotFoundError
        e = NodeNotFoundError("node 'abc123' not found")
        assert str(e) == "node 'abc123' not found"


class TestEdgeNotFoundError:
    """Tests for EdgeNotFoundError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import EdgeNotFoundError, StoreError
        e = EdgeNotFoundError()
        assert isinstance(e, EdgeNotFoundError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import EdgeNotFoundError
        e = EdgeNotFoundError("edge id=42 not found")
        assert str(e) == "edge id=42 not found"


class TestSchemaVersionError:
    """Tests for SchemaVersionError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import SchemaVersionError, StoreError
        e = SchemaVersionError()
        assert isinstance(e, SchemaVersionError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import SchemaVersionError
        e = SchemaVersionError("expected schema v3, got v2")
        assert str(e) == "expected schema v3, got v2"


class TestMigrationError:
    """Tests for MigrationError."""

    def test_can_instantiate(self):
        from tws_graph.store.exceptions import MigrationError, StoreError
        e = MigrationError()
        assert isinstance(e, MigrationError)
        assert isinstance(e, StoreError)
        assert isinstance(e, Exception)

    def test_can_instantiate_with_message(self):
        from tws_graph.store.exceptions import MigrationError
        e = MigrationError("migration v003 failed: column already exists")
        assert str(e) == "migration v003 failed: column already exists"


class TestInheritanceHierarchy:
    """Tests for the full inheritance chain and catch-all behavior."""

    ALL_EXCEPTIONS = [
        "StoreClosedError",
        "TransactionError",
        "NodeNotFoundError",
        "EdgeNotFoundError",
        "SchemaVersionError",
        "MigrationError",
    ]

    def test_all_subclasses_inherit_from_store_error(self):
        from tws_graph.store import exceptions as ex
        for name in self.ALL_EXCEPTIONS:
            cls = getattr(ex, name)
            assert issubclass(cls, ex.StoreError), f"{name} should be subclass of StoreError"

    def test_catch_store_error_catches_all_subclasses(self):
        """except StoreError should catch every subclass."""
        from tws_graph.store.exceptions import (
            StoreError,
            StoreClosedError,
            TransactionError,
            NodeNotFoundError,
            EdgeNotFoundError,
            SchemaVersionError,
            MigrationError,
        )
        subclasses = [
            StoreClosedError, TransactionError,
            NodeNotFoundError, EdgeNotFoundError,
            SchemaVersionError, MigrationError,
        ]
        caught = []
        for cls in subclasses:
            try:
                raise cls("test message")
            except StoreError:
                caught.append(cls.__name__)
        expected = [c.__name__ for c in subclasses]
        assert caught == expected

    def test_catch_exception_catches_store_error(self):
        """except Exception should catch StoreError too."""
        from tws_graph.store.exceptions import StoreError
        try:
            raise StoreError("test")
        except Exception as e:
            assert isinstance(e, StoreError)
            assert str(e) == "test"
        else:
            pytest.fail("Exception did not catch StoreError")
