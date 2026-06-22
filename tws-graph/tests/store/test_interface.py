"""Tests for store/interface.py — Store abstract base class compliance.

Verifies:
    1. Store is an ABC subclass
    2. Count of abstract methods matches design spec (48)
    3. Instantiating a partially-implemented subclass raises TypeError
    4. load_from is NOT an abstract method
"""

import inspect
from abc import ABC, abstractmethod

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_all_abstract_methods(cls):
    """Return the set of abstract method names for a class.

    Walks MRO and collects all names that are abstract. This matches
    what Python itself uses to decide if a class is instantiable.
    """
    return cls.__abstractmethods__


def _get_all_methods(cls):
    """Return all method names (including inherited) defined on cls."""
    methods = set()
    for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
        methods.add(name)
    return methods


# ---------------------------------------------------------------------------
# Expected abstract methods by group (matches design-store-schema.md sec 5.2)
# ---------------------------------------------------------------------------

EXPECTED_METHODS = {
    # 1. Connection management
    "close",
    # 2. Transaction management
    "begin",
    "commit",
    "rollback",
    # 3. Node CRUD
    "insert_node",
    "insert_nodes",
    "get_node_by_id",
    "get_nodes_by_ids",
    "delete_nodes_by_file",
    "update_node_property",
    "iter_nodes_by_kind",
    "iter_all_nodes",
    "count_nodes",
    "iter_nodes_by_file",
    # 4. Edge CRUD
    "insert_edge",
    "insert_edges",
    "get_outgoing_edges",
    "get_incoming_edges",
    "get_edges_between",
    "update_edge_target",
    "update_edge_provenance",
    "delete_edges_by_source",
    "count_edges",
    "iter_all_edges",
    "get_dangling_edges",
    "iter_edges",
    "iter_edges_from",
    # 5. Graph traversal
    "get_neighbors",
    "get_neighbors_batch",
    "find_paths",
    # 6. File management
    "upsert_file",
    "get_file",
    "get_all_files",
    "get_file_stats",
    "delete_file",
    # 7. Search
    "fts_search",
    "search_by_def_index",
    "search_by_field_qualified",
    # 8. Unresolved references
    "insert_unresolved_ref",
    "insert_unresolved_refs",
    "get_unresolved_refs",
    "clear_unresolved_refs",
    # 9. Batch operations
    "flush",
    # 10. Maintenance / statistics
    "optimize",
    "clear",
    "rebuild_fts",
    "stats",
    "build_def_index",
}

EXPECTED_COUNT = 48

# Methods that must NOT be abstract in the ABC
EXCLUDED_METHODS = {"load_from"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStoreIsABC:
    """Verify Store inherits from ABC."""

    def test_store_class_exists(self):
        from tws_graph.store.interface import Store
        assert inspect.isclass(Store)

    def test_store_is_abc(self):
        from tws_graph.store.interface import Store
        assert issubclass(Store, ABC)

    def test_store_cannot_be_instantiated(self):
        """Direct instantiation of Store should fail (missing abstract methods)."""
        from tws_graph.store.interface import Store
        with pytest.raises(TypeError) as exc_info:
            Store()
        # The error message should mention abstract methods
        assert "abstract" in str(exc_info.value).lower()


class TestAbstractMethodCount:
    """Verify the exact count of abstract methods."""

    def test_abstract_method_count_matches_expected(self):
        from tws_graph.store.interface import Store
        actual = len(_get_all_abstract_methods(Store))
        assert actual == EXPECTED_COUNT, (
            f"Expected {EXPECTED_COUNT} abstract methods, got {actual}. "
            f"Diff: missing={EXPECTED_METHODS - _get_all_abstract_methods(Store)}, "
            f"extra={_get_all_abstract_methods(Store) - EXPECTED_METHODS}"
        )

    def test_all_expected_methods_are_abstract(self):
        from tws_graph.store.interface import Store
        actual_abstract = _get_all_abstract_methods(Store)
        missing = EXPECTED_METHODS - actual_abstract
        assert not missing, (
            f"Methods expected to be abstract but are not: {missing}"
        )

    def test_no_unexpected_abstract_methods(self):
        from tws_graph.store.interface import Store
        actual_abstract = _get_all_abstract_methods(Store)
        extra = actual_abstract - EXPECTED_METHODS
        assert not extra, (
            f"Methods are abstract but were not expected: {extra}"
        )


class TestExcludedMethodsNotAbstract:
    """Verify that excluded methods (e.g. load_from) are NOT abstract."""

    def test_load_from_is_not_abstract(self):
        from tws_graph.store.interface import Store
        abstract_set = _get_all_abstract_methods(Store)
        for method in EXCLUDED_METHODS:
            assert method not in abstract_set, (
                f"'{method}' should NOT be an abstract method on Store, "
                f"but it was found in __abstractmethods__"
            )


class TestMethodHasAbstractDecorator:
    """Verify every abstract method is decorated with @abstractmethod."""

    def test_every_abstract_method_has_decorator(self):
        from tws_graph.store.interface import Store
        for name in _get_all_abstract_methods(Store):
            method = getattr(Store, name)
            assert hasattr(method, "__isabstractmethod__"), (
                f"Method '{name}' is in __abstractmethods__ but "
                f"lacks __isabstractmethod__ attribute. "
                f"Has it been misconfigured?"
            )
            assert method.__isabstractmethod__ is True, (
                f"Method '{name}' has __isabstractmethod__ but it is not True"
            )


class TestPartiallyImplementedSubclassTypeError:
    """Verify that a subclass missing abstract method implementations
    cannot be instantiated and raises TypeError."""

    def test_empty_subclass_raises_typeerror(self):
        """A subclass that implements nothing should fail."""
        from tws_graph.store.interface import Store

        class EmptyStore(Store):
            pass

        with pytest.raises(TypeError) as exc_info:
            EmptyStore()
        assert "abstract" in str(exc_info.value).lower()

    def test_partial_subclass_raises_typeerror(self):
        """A subclass that implements only a few methods should fail."""
        from tws_graph.store.interface import Store

        class PartialStore(Store):
            def close(self):
                pass

        with pytest.raises(TypeError) as exc_info:
            PartialStore()
        assert "abstract" in str(exc_info.value).lower()

    def test_fully_implemented_subclass_can_instantiate(self):
        """A subclass that implements ALL abstract methods should succeed."""
        from tws_graph.store.interface import Store

        class FullStore(Store):
            def make_empty(self, hint):
                if hint == "node":
                    return {
                        "id": "", "kind": "", "name": "", "qualified_name": "",
                        "file_path": "", "language": "", "start_line": 0,
                        "end_line": 0, "signature": None, "docstring": None,
                        "visibility": None, "is_abstract": 0, "is_exported": 0,
                        "decorators": None, "framework": None, "properties": None,
                        "updated_at": 0,
                    }
                if hint == "edge":
                    return {
                        "id": 0, "source": "", "target": "", "target_text": None,
                        "kind": "", "source_loc": None, "provenance": "", "properties": None,
                    }
                return {}

            # 1. Connection management
            def close(self): pass
            # 2. Transaction management
            def begin(self): pass
            def commit(self): pass
            def rollback(self): pass
            # 3. Node CRUD
            def insert_node(self, node): pass
            def insert_nodes(self, nodes): pass
            def get_node_by_id(self, id): return None
            def get_nodes_by_ids(self, ids): return {}
            def delete_nodes_by_file(self, file_path, kind=None): pass
            def update_node_property(self, node_id, properties): pass
            def iter_nodes_by_kind(self, kind, batch_size=1000): return iter(())
            def iter_all_nodes(self, batch_size=1000): return iter(())
            def count_nodes(self): return 0
            def iter_nodes_by_file(self, file_path): return iter(())
            # 4. Edge CRUD
            def insert_edge(self, edge): pass
            def insert_edges(self, edges): pass
            def get_outgoing_edges(self, source_id, kinds=None): return []
            def get_incoming_edges(self, target_id, kinds=None): return []
            def get_edges_between(self, source_id, target_id): return []
            def update_edge_target(self, edge_id, new_target, provenance="resolved"): pass
            def update_edge_provenance(self, edge_id, provenance): pass
            def delete_edges_by_source(self, source_id): pass
            def count_edges(self): return 0
            def iter_all_edges(self, batch_size=1000): return iter(())
            def get_dangling_edges(self, kind=None): return []
            def iter_edges(self, kind=None, with_source_info=False): return iter(())
            def iter_edges_from(self, node_id, kinds=None, direction="both", batch_size=1000):
                return iter(())
            # 5. Graph traversal
            def get_neighbors(self, node_id, kinds=None, direction="both"): return []
            def get_neighbors_batch(self, node_ids, kinds=None, direction="both"): return {}
            def find_paths(self, from_id, to_id, kinds=None, max_depth=5): return None
            # 6. File management
            def upsert_file(self, path, content_hash, language, node_count=0, size=0, modified_at=0):
                pass
            def get_file(self, path): return None
            def get_all_files(self): return []
            def get_file_stats(self): return {}
            def delete_file(self, path): pass
            # 7. Search
            def fts_search(self, query, limit=20, kind_filter=None, language_filter=None, path_filter=None):
                return []
            def search_by_def_index(self, qualified_name): return None
            def search_by_field_qualified(self, query, limit=20): return []
            # 8. Unresolved references
            def insert_unresolved_ref(self, ref): pass
            def insert_unresolved_refs(self, refs): pass
            def get_unresolved_refs(self, file_path=None): return []
            def clear_unresolved_refs(self): pass
            # 9. Batch operations
            def flush(self): pass
            # 10. Maintenance / statistics
            def optimize(self): pass
            def clear(self): pass
            def rebuild_fts(self): pass
            def stats(self):
                return {"node_count": 0, "edge_count": 0, "file_count": 0, "unresolved_count": 0}
            def build_def_index(self): return {}

        instance = FullStore()
        assert isinstance(instance, Store)


class TestMethodDocstrings:
    """Verify every abstract method has a docstring."""

    def test_every_abstract_method_has_docstring(self):
        from tws_graph.store.interface import Store
        missing = []
        for name in sorted(_get_all_abstract_methods(Store)):
            method = getattr(Store, name)
            if not method.__doc__ or len(method.__doc__.strip()) < 10:
                missing.append(name)
        assert not missing, (
            f"Abstract methods missing adequate docstrings: {missing}"
        )


class TestMethodSignatures:
    """Verify method signatures match the design document.

    Checks parameter names (not types, since annotations are verified
    by the compiler). Any mismatch here means the interface does not
    match what callers expect per the design spec.
    """

    def test_all_method_params_match_spec(self):
        """Spot-check key methods have the right parameter names."""
        from tws_graph.store.interface import Store

        # Checks: (method_name, expected_param_names)
        checks = [
            # 1. Connection
            ("close", ["self"]),
            # 2. Transaction
            ("begin", ["self"]),
            ("commit", ["self"]),
            ("rollback", ["self"]),
            # 3. Node CRUD
            ("insert_node", ["self", "node"]),
            ("insert_nodes", ["self", "nodes"]),
            ("get_node_by_id", ["self", "id"]),
            ("get_nodes_by_ids", ["self", "ids"]),
            ("delete_nodes_by_file", ["self", "file_path", "kind"]),
            ("update_node_property", ["self", "node_id", "properties"]),
            ("iter_nodes_by_kind", ["self", "kind", "batch_size"]),
            ("iter_all_nodes", ["self", "batch_size"]),
            ("count_nodes", ["self"]),
            ("iter_nodes_by_file", ["self", "file_path"]),
            # 4. Edge CRUD
            ("insert_edge", ["self", "edge"]),
            ("insert_edges", ["self", "edges"]),
            ("get_outgoing_edges", ["self", "source_id", "kinds"]),
            ("get_incoming_edges", ["self", "target_id", "kinds"]),
            ("get_edges_between", ["self", "source_id", "target_id"]),
            ("update_edge_target", ["self", "edge_id", "new_target", "provenance"]),
            ("update_edge_provenance", ["self", "edge_id", "provenance"]),
            ("delete_edges_by_source", ["self", "source_id"]),
            ("count_edges", ["self"]),
            ("iter_all_edges", ["self", "batch_size"]),
            ("get_dangling_edges", ["self", "kind"]),
            ("iter_edges", ["self", "kind", "with_source_info"]),
            ("iter_edges_from", ["self", "node_id", "kinds", "direction", "batch_size"]),
            # 5. Traversal
            ("get_neighbors", ["self", "node_id", "kinds", "direction"]),
            ("get_neighbors_batch", ["self", "node_ids", "kinds", "direction"]),
            ("find_paths", ["self", "from_id", "to_id", "kinds", "max_depth"]),
            # 6. File
            ("upsert_file", ["self", "path", "content_hash", "language", "node_count", "size", "modified_at"]),
            ("get_file", ["self", "path"]),
            ("get_all_files", ["self"]),
            ("get_file_stats", ["self"]),
            ("delete_file", ["self", "path"]),
            # 7. Search
            ("fts_search", ["self", "query", "limit", "kind_filter", "language_filter", "path_filter"]),
            ("search_by_def_index", ["self", "qualified_name"]),
            ("search_by_field_qualified", ["self", "query", "limit"]),
            # 8. Unresolved refs
            ("insert_unresolved_ref", ["self", "ref"]),
            ("insert_unresolved_refs", ["self", "refs"]),
            ("get_unresolved_refs", ["self", "file_path"]),
            ("clear_unresolved_refs", ["self"]),
            # 9. Batch
            ("flush", ["self"]),
            # 10. Maintenance
            ("optimize", ["self"]),
            ("clear", ["self"]),
            ("rebuild_fts", ["self"]),
            ("stats", ["self"]),
            ("build_def_index", ["self"]),
        ]

        for method_name, expected_params in checks:
            method = getattr(Store, method_name)
            sig = inspect.signature(method)
            actual_params = list(sig.parameters.keys())
            assert actual_params == expected_params, (
                f"Method '{method_name}': expected params {expected_params}, "
                f"got {actual_params}"
            )
