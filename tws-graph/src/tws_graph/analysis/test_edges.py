"""Test edge analysis -- derive test<->source associations.

Derives which source code each test function/method/class exercises
via three heuristic strategies, from highest to lowest confidence:

1. **Naming convention** (confidence=0.9):
   ``test_foo.py`` -> ``foo.py``, ``test_calculate()`` -> ``calculate()``,
   ``FooTest`` -> ``Foo``, ``FooSpec`` -> ``Foo``.

2. **Call graph** (confidence=0.8):
   CALLS edges from test node to source node.

3. **Import reference** (confidence=0.5):
   IMPORTS edges from test node to source node.

Deduplication: when the same (test, source) pair is found via multiple
strategies, the highest-confidence entry is retained.

Design: P9 Analysis Suite -- Test Edge Analyzer.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

from tws_graph.edges.kind import EdgeKind
from tws_graph.store.interface import Store


@dataclass
class TestEdge:
    """A derived association between a test symbol and a source symbol.

    ``__test__ = False`` prevents pytest from treating this as a test class.
    ``__hash__`` and ``__eq__`` are based on ``(test_node_id, source_node_id)``
    so that the same pair from multiple strategies deduplicates to one entry.

    Attributes:
        test_node_id: Unique node identifier of the test symbol.
        test_name: Simple name of the test function/method/class.
        test_file_path: Project-relative path of the test file.
        source_node_id: Unique node identifier of the source symbol.
        source_name: Simple name of the source function/method/class.
        source_file_path: Project-relative path of the source file.
        confidence: Probability-like score between 0.0 and 1.0.
        derivation: Strategy that produced this edge
            (``naming_convention``, ``call_graph``, or ``import_reference``).
    """

    __test__ = False  # not a pytest test class

    test_node_id: str
    test_name: str
    test_file_path: str
    source_node_id: str
    source_name: str
    source_file_path: str
    confidence: float = 0.0
    derivation: str = ""

    def _key(self) -> tuple[str, str]:
        return (self.test_node_id, self.source_node_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TestEdge):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


class TestEdgeAnalyzer:
    """Derive test<->source associations from a code graph Store.

    Usage::

        store = MemoryStore()
        # ... populate store with indexed nodes and edges ...
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(store)
        for e in edges:
            print(f"{e.test_name} -> {e.source_name} ({e.confidence})")
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, store: Store) -> list[TestEdge]:
        """Run full test-edge derivation over *store*.

        Returns a list of ``TestEdge`` objects with no duplicates -- each
        (test, source) pair appears once at its highest confidence.
        """
        nodes = list(store.iter_all_nodes())

        test_nodes = [n for n in nodes if self._is_test_node(n)]
        source_nodes = [n for n in nodes if not self._is_test_node(n)]

        # Fast lookup indices
        source_by_name: defaultdict[str, list[dict]] = defaultdict(list)
        for n in source_nodes:
            source_by_name[n["name"]].append(n)

        source_id_set: set[str] = {n["id"] for n in source_nodes}

        # Accumulate edges keyed by (test_node_id, source_node_id) for dedup
        edges_by_key: dict[tuple[str, str], TestEdge] = {}

        # Step 1 -- naming convention (0.9)
        for test_node in test_nodes:
            for edge in self._match_naming_convention(test_node, source_by_name):
                self._upsert_edge(edges_by_key, edge)

        # Step 2 -- CALLS edges (0.8)
        for test_node in test_nodes:
            for edge in self._match_calls(test_node, store, source_id_set):
                self._upsert_edge(edges_by_key, edge)

        # Step 3 -- IMPORTS edges (0.5)
        for test_node in test_nodes:
            for edge in self._match_imports(test_node, store, source_id_set):
                self._upsert_edge(edges_by_key, edge)

        return list(edges_by_key.values())

    # ------------------------------------------------------------------
    # Test node identification
    # ------------------------------------------------------------------

    @staticmethod
    def _is_test_node(node: dict) -> bool:
        """Return True if *node* represents a test symbol."""
        fp: str = node.get("file_path", "")
        name: str = node.get("name", "")
        kind: str = node.get("kind", "")

        # File path contains "test" or "__tests__"
        if "test" in fp.lower():
            return True

        # Function/method name starts with "test_" or camelCase "testXxx"
        if name.startswith("test_"):
            return True
        if name.startswith("test") and len(name) > 4 and name[4].isupper():
            return True

        # Class name ends with well-known test-class suffixes
        if kind in ("class",):
            for suffix in ("Tests", "Test", "Spec"):
                if name.endswith(suffix) and name != suffix:
                    return True

        return False

    # ------------------------------------------------------------------
    # Source name / file path derivation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_source_names(test_name: str) -> list[str]:
        """Derive candidate source names from a test name.

        ``test_calculate`` -> ``calculate``,
        ``testCalculate`` -> ``calculate``,
        ``FooTest`` -> ``Foo``,
        ``BazSpec`` -> ``Baz``.
        """
        candidates: list[str] = []

        # test_calculate -> calculate
        if test_name.startswith("test_"):
            candidates.append(test_name[5:])

        # testCalculate -> calculate
        if test_name.startswith("test") and len(test_name) > 4 and test_name[4].isupper():
            candidates.append(test_name[4].lower() + test_name[5:])

        # FooTest / FooTests / FooSpec -> Foo
        for suffix in ("Tests", "Test", "Spec"):
            if test_name.endswith(suffix) and len(test_name) > len(suffix):
                candidates.append(test_name[:-len(suffix)])

        return candidates

    @staticmethod
    def _derive_source_file_names(test_file_path: str) -> list[str]:
        """Derive candidate source file names from a test file path.

        ``tests/test_foo.py`` -> ``foo.py``,
        ``Foo.spec.ts`` -> ``Foo.ts``.
        """
        candidates: list[str] = []
        basename = os.path.basename(test_file_path)
        name_no_ext, ext = os.path.splitext(basename)

        # test_foo -> foo
        if name_no_ext.startswith("test_"):
            candidates.append(name_no_ext[5:] + ext)

        # foo_test -> foo
        if name_no_ext.endswith("_test"):
            candidates.append(name_no_ext[:-5] + ext)

        # foo.spec -> foo
        if ".spec" in basename:
            candidates.append(basename.replace(".spec", ""))

        # foo_spec -> foo
        if name_no_ext.endswith("_spec"):
            candidates.append(name_no_ext[:-5] + ext)

        return candidates

    # ------------------------------------------------------------------
    # Step 1 -- naming convention (confidence=0.9)
    # ------------------------------------------------------------------

    def _match_naming_convention(
        self,
        test_node: dict,
        source_by_name: dict[str, list[dict]],
    ) -> list[TestEdge]:
        """Match test node to source nodes via naming conventions."""
        edges: list[TestEdge] = []
        test_name: str = test_node["name"]
        test_fp: str = test_node["file_path"]

        source_name_candidates = self._derive_source_names(test_name)
        source_file_candidates = self._derive_source_file_names(test_fp)

        if not source_name_candidates:
            return edges

        for src_name in source_name_candidates:
            if src_name not in source_by_name:
                continue

            for src_node in source_by_name[src_name]:
                src_fp = src_node["file_path"]
                src_basename = os.path.basename(src_fp)

                if self._file_matches_candidates(src_fp, src_basename, source_file_candidates):
                    edges.append(TestEdge(
                        test_node_id=test_node["id"],
                        test_name=test_name,
                        test_file_path=test_fp,
                        source_node_id=src_node["id"],
                        source_name=src_node["name"],
                        source_file_path=src_fp,
                        confidence=0.9,
                        derivation="naming_convention",
                    ))

        return edges

    @staticmethod
    def _file_matches_candidates(
        src_full_path: str,
        src_basename: str,
        candidates: list[str],
    ) -> bool:
        """Return True if *src_full_path* matches any candidate source file name."""
        if not candidates:
            return True
        for candidate in candidates:
            if src_full_path.endswith(candidate) or src_basename == candidate:
                return True
        return False

    # ------------------------------------------------------------------
    # Step 2 -- CALLS edges (confidence=0.8)
    # ------------------------------------------------------------------

    @staticmethod
    def _match_calls(
        test_node: dict,
        store: Store,
        source_id_set: set[str],
    ) -> list[TestEdge]:
        """Match via CALLS edges from test node to source nodes."""
        edges: list[TestEdge] = []

        try:
            call_edges = store.get_outgoing_edges(
                test_node["id"], [EdgeKind.CALLS.value]
            )
        except Exception:
            return edges

        for edge in call_edges:
            target_id = edge.get("target", "")
            if not target_id or target_id not in source_id_set:
                continue

            target_node = store.get_node_by_id(target_id)
            if target_node is None:
                continue

            edges.append(TestEdge(
                test_node_id=test_node["id"],
                test_name=test_node["name"],
                test_file_path=test_node["file_path"],
                source_node_id=target_id,
                source_name=target_node["name"],
                source_file_path=target_node["file_path"],
                confidence=0.8,
                derivation="call_graph",
            ))

        return edges

    # ------------------------------------------------------------------
    # Step 3 -- IMPORTS edges (confidence=0.5)
    # ------------------------------------------------------------------

    @staticmethod
    def _match_imports(
        test_node: dict,
        store: Store,
        source_id_set: set[str],
    ) -> list[TestEdge]:
        """Match via IMPORTS edges from test node to source nodes."""
        edges: list[TestEdge] = []

        try:
            import_edges = store.get_outgoing_edges(
                test_node["id"], [EdgeKind.IMPORTS.value]
            )
        except Exception:
            return edges

        for edge in import_edges:
            target_id = edge.get("target", "")
            if not target_id or target_id not in source_id_set:
                continue

            target_node = store.get_node_by_id(target_id)
            if target_node is None:
                continue

            edges.append(TestEdge(
                test_node_id=test_node["id"],
                test_name=test_node["name"],
                test_file_path=test_node["file_path"],
                source_node_id=target_id,
                source_name=target_node["name"],
                source_file_path=target_node["file_path"],
                confidence=0.5,
                derivation="import_reference",
            ))

        return edges

    # ------------------------------------------------------------------
    # Deduplication helper
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert_edge(
        edges_by_key: dict[tuple[str, str], TestEdge],
        edge: TestEdge,
    ) -> None:
        """Insert *edge* into *edges_by_key*, keeping the higher-confidence one."""
        key = (edge.test_node_id, edge.source_node_id)
        if key not in edges_by_key or edge.confidence > edges_by_key[key].confidence:
            edges_by_key[key] = edge
