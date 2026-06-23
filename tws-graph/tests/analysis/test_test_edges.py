"""Tests for analysis/test_edges.py — TestEdgeAnalyzer.

Covers:
    1. Naming convention matching: test_foo.py ↔ foo.py, test_calculate ↔ calculate
    2. CALLS edge association: test function calls source function
    3. IMPORTS edge association: test file imports source module
    4. Multi-confidence deduplication: keep highest confidence
    5. Edge cases: empty store, no test files, no source matches
"""

import pytest

from tws_graph.edges.kind import EdgeKind
from tws_graph.store.memory_store import MemoryStore
from tws_graph.analysis.test_edges import TestEdge, TestEdgeAnalyzer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(node_id, file_path="src/util.py", kind="function", name="calculate",
               **overrides):
    """Create a minimal valid NodeRecord for testing."""
    node = {
        "id": node_id,
        "kind": kind,
        "name": name,
        "qualified_name": f"{file_path}::{kind}.{name}",
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 10,
        "signature": None,
        "docstring": None,
        "visibility": None,
        "is_abstract": 0,
        "is_exported": 0,
        "decorators": None,
        "framework": None,
        "properties": None,
        "updated_at": 0,
    }
    node.update(overrides)
    return node


def _make_edge(source, target, kind="calls", **overrides):
    """Create a minimal valid EdgeRecord for testing."""
    edge = {
        "source": source,
        "target": target,
        "target_text": None,
        "kind": kind,
        "source_loc": None,
        "provenance": "tree-sitter",
        "properties": None,
    }
    edge.update(overrides)
    return edge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_store():
    """Return a fresh empty MemoryStore."""
    return MemoryStore()


@pytest.fixture
def basic_store():
    """Store with test functions and source functions for naming convention matching.

    test_foo.py::test_calculate → matches foo.py::calculate (naming convention)
    """
    s = MemoryStore()

    # Test file node
    s.insert_node(_make_node(
        "test_calc", name="test_calculate",
        file_path="tests/test_foo.py",
        qualified_name="tests/test_foo.py::function.test_calculate",
    ))

    # Source file node
    s.insert_node(_make_node(
        "calc", name="calculate",
        file_path="src/foo.py",
        qualified_name="src/foo.py::function.calculate",
    ))

    return s


@pytest.fixture
def calls_store():
    """Store where a test function has a CALLS edge to a source function.

    Test name and file path deliberately do NOT match naming convention
    so that the CALLS edge is the only way to derive the association.
    """
    s = MemoryStore()

    s.insert_node(_make_node(
        "test_func", name="test_check_validation",
        file_path="tests/test_misc.py",
        qualified_name="tests/test_misc.py::function.test_check_validation",
    ))

    s.insert_node(_make_node(
        "validate", name="validate_input",
        file_path="src/core_utils.py",
        qualified_name="src/core_utils.py::function.validate_input",
    ))

    s.insert_edge(_make_edge("test_func", "validate", kind="calls"))

    return s


@pytest.fixture
def imports_store():
    """Store where a test function has an IMPORTS edge to a source module.

    Test name and file path deliberately do NOT match naming convention
    so that the IMPORTS edge is the only way to derive the association.
    """
    s = MemoryStore()

    s.insert_node(_make_node(
        "test_parse", name="test_check_parsing",
        file_path="tests/test_misc.py",
        qualified_name="tests/test_misc.py::function.test_check_parsing",
    ))

    s.insert_node(_make_node(
        "parse", name="parse_data",
        file_path="src/transform.py",
        qualified_name="src/transform.py::function.parse_data",
    ))

    s.insert_edge(_make_edge("test_parse", "parse", kind="imports"))

    return s


@pytest.fixture
def multi_confidence_store():
    """Store where same (test, source) pair appears via multiple derivation methods.

    test_calculate → calculate found via:
      - naming convention (0.9)
      - CALLS edge (0.8)
    Deduplication should keep the highest (0.9).
    """
    s = MemoryStore()

    s.insert_node(_make_node(
        "test_calc", name="test_calculate",
        file_path="tests/test_foo.py",
        qualified_name="tests/test_foo.py::function.test_calculate",
    ))

    s.insert_node(_make_node(
        "calc", name="calculate",
        file_path="src/foo.py",
        qualified_name="src/foo.py::function.calculate",
    ))

    # Also add a CALLS edge for same pair
    s.insert_edge(_make_edge("test_calc", "calc", kind="calls"))

    return s


@pytest.fixture
def class_test_store():
    """Store with class-based test naming conventions."""
    s = MemoryStore()

    # Test class FooTest matching source class Foo
    s.insert_node(_make_node(
        "test_class", name="FooTest", kind="class",
        file_path="tests/test_models.py",
        qualified_name="tests/test_models.py::class.FooTest",
    ))

    s.insert_node(_make_node(
        "source_class", name="Foo", kind="class",
        file_path="src/models.py",
        qualified_name="src/models.py::class.Foo",
    ))

    return s


@pytest.fixture
def full_store():
    """A comprehensive store with multiple test-to-source relationships."""
    s = MemoryStore()

    # --- test_foo.py functions ---
    s.insert_node(_make_node(
        "t_add", name="test_add",
        file_path="tests/test_foo.py",
        qualified_name="tests/test_foo.py::function.test_add",
    ))
    s.insert_node(_make_node(
        "t_sub", name="test_subtract",
        file_path="tests/test_foo.py",
        qualified_name="tests/test_foo.py::function.test_subtract",
    ))

    # --- foo.py source functions ---
    s.insert_node(_make_node(
        "s_add", name="add",
        file_path="src/foo.py",
        qualified_name="src/foo.py::function.add",
    ))
    s.insert_node(_make_node(
        "s_sub", name="subtract",
        file_path="src/foo.py",
        qualified_name="src/foo.py::function.subtract",
    ))

    # --- test with CALLS edge (naming deliberately non-matching) ---
    s.insert_node(_make_node(
        "t_mul", name="test_verify_multiplication",
        file_path="tests/test_math_check.py",
        qualified_name="tests/test_math_check.py::function.test_verify_multiplication",
    ))
    s.insert_node(_make_node(
        "s_mul", name="multiply",
        file_path="src/math_extra.py",
        qualified_name="src/math_extra.py::function.multiply",
    ))
    s.insert_edge(_make_edge("t_mul", "s_mul", kind="calls"))

    # --- test with IMPORTS edge (naming deliberately non-matching) ---
    s.insert_node(_make_node(
        "t_div", name="test_check_division",
        file_path="tests/test_math_check.py",
        qualified_name="tests/test_math_check.py::function.test_check_division",
    ))
    s.insert_node(_make_node(
        "s_div", name="divide",
        file_path="src/math_extra.py",
        qualified_name="src/math_extra.py::function.divide",
    ))
    s.insert_edge(_make_edge("t_div", "s_div", kind="imports"))

    # --- class-based: FooTests → Foo ---
    s.insert_node(_make_node(
        "class_test", name="BarTests", kind="class",
        file_path="tests/test_bar.py",
        qualified_name="tests/test_bar.py::class.BarTests",
    ))
    s.insert_node(_make_node(
        "class_src", name="Bar", kind="class",
        file_path="src/bar.py",
        qualified_name="src/bar.py::class.Bar",
    ))

    # --- spec-style: FooSpec → Foo ---
    s.insert_node(_make_node(
        "spec_test", name="BazSpec", kind="class",
        file_path="tests/test_baz.py",
        qualified_name="tests/test_baz.py::class.BazSpec",
    ))
    s.insert_node(_make_node(
        "spec_src", name="Baz", kind="class",
        file_path="src/baz.py",
        qualified_name="src/baz.py::class.Baz",
    ))

    return s


# =============================================================================
# Tests — TestEdge dataclass
# =============================================================================

class TestTestEdgeDataclass:
    """Verify the TestEdge dataclass structure."""

    def test_create_test_edge(self):
        """TestEdge should be creatable with all fields."""
        edge = TestEdge(
            test_node_id="t1",
            test_name="test_add",
            test_file_path="tests/test_foo.py",
            source_node_id="s1",
            source_name="add",
            source_file_path="src/foo.py",
            confidence=0.9,
            derivation="naming_convention",
        )
        assert edge.test_node_id == "t1"
        assert edge.test_name == "test_add"
        assert edge.test_file_path == "tests/test_foo.py"
        assert edge.source_node_id == "s1"
        assert edge.source_name == "add"
        assert edge.source_file_path == "src/foo.py"
        assert edge.confidence == 0.9
        assert edge.derivation == "naming_convention"

    def test_default_confidence(self):
        """confidence should have a default of 0.0."""
        edge = TestEdge(
            test_node_id="t1",
            test_name="t",
            test_file_path="f",
            source_node_id="s1",
            source_name="s",
            source_file_path="f2",
            derivation="naming_convention",
        )
        assert edge.confidence == 0.0

    def test_edge_equality(self):
        """Two TestEdges with the same test_node_id and source_node_id should be equal."""
        e1 = TestEdge("t1", "tn", "tf", "s1", "sn", "sf", 0.9, "naming_convention")
        e2 = TestEdge("t1", "tn", "tf2", "s1", "sn2", "sf2", 0.5, "call_graph")
        assert e1 == e2

    def test_edge_not_equal_different_ids(self):
        """TestEdges with different test or source should not be equal."""
        e1 = TestEdge("t1", "tn", "tf", "s1", "sn", "sf", 0.9, "naming_convention")
        e2 = TestEdge("t2", "tn", "tf", "s1", "sn", "sf", 0.9, "naming_convention")
        e3 = TestEdge("t1", "tn", "tf", "s2", "sn", "sf", 0.9, "naming_convention")
        assert e1 != e2
        assert e1 != e3

    def test_edge_hashable(self):
        """TestEdge should be hashable for deduplication via set/dict."""
        e1 = TestEdge("t1", "tn", "tf", "s1", "sn", "sf", 0.9, "naming_convention")
        e2 = TestEdge("t1", "tn2", "tf2", "s1", "sn2", "sf2", 0.5, "call_graph")
        s = {e1, e2}
        assert len(s) == 1


# =============================================================================
# Tests — Naming Convention Matching (confidence=0.9)
# =============================================================================

class TestNamingConventionMatching:
    """Step 1: Derive test↔source via naming conventions."""

    def test_test_file_to_source_file(self, basic_store):
        """test_foo.py → foo.py: match test functions to source functions."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(basic_store)
        assert len(edges) >= 1

        # Find the naming convention edge
        naming_edges = [e for e in edges if e.derivation == "naming_convention"]
        assert len(naming_edges) >= 1

    def test_test_function_prefix_match(self, basic_store):
        """test_calculate() → calculate: strip test_ prefix to match."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(basic_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_calc" and e.source_node_id == "calc"]
        assert len(matching) >= 1
        edge = matching[0]
        assert edge.derivation == "naming_convention"
        assert edge.confidence == 0.9

    def test_naming_convention_confidence(self, basic_store):
        """Naming convention matches should have confidence 0.9."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(basic_store)
        naming_edges = [e for e in edges if e.derivation == "naming_convention"]
        for e in naming_edges:
            assert e.confidence == 0.9

    def test_class_test_suffix_match(self, class_test_store):
        """FooTest class → Foo class: strip Test/Tests suffix."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(class_test_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_class" and e.source_node_id == "source_class"]
        assert len(matching) >= 1
        assert matching[0].derivation == "naming_convention"
        assert matching[0].confidence == 0.9

    def test_class_tests_suffix_match(self):
        """FooTests class → Foo class."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "tc", name="FooTests", kind="class",
            file_path="tests/test_foo.py",
            qualified_name="tests/test_foo.py::class.FooTests",
        ))
        s.insert_node(_make_node(
            "sc", name="Foo", kind="class",
            file_path="src/foo.py",
            qualified_name="src/foo.py::class.Foo",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        matching = [e for e in edges if e.test_node_id == "tc" and e.source_node_id == "sc"]
        assert len(matching) >= 1

    def test_spec_suffix_match(self):
        """FooSpec → Foo."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "ts", name="FooSpec", kind="class",
            file_path="tests/test_foo.py",
            qualified_name="tests/test_foo.py::class.FooSpec",
        ))
        s.insert_node(_make_node(
            "ss", name="Foo", kind="class",
            file_path="src/foo.py",
            qualified_name="src/foo.py::class.Foo",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        matching = [e for e in edges if e.test_node_id == "ts" and e.source_node_id == "ss"]
        assert len(matching) >= 1


# =============================================================================
# Tests — CALLS Edge Association (confidence=0.8)
# =============================================================================

class TestCallsEdgeMatching:
    """Step 2: Derive test↔source via CALLS edges."""

    def test_calls_edge_creates_match(self, calls_store):
        """A CALLS edge from test function to source function → test edge."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(calls_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_func" and e.source_node_id == "validate"]
        assert len(matching) >= 1

    def test_calls_edge_confidence(self, calls_store):
        """CALLS-derived edges should have confidence 0.8."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(calls_store)
        calls_edges = [e for e in edges if e.derivation == "call_graph"]
        for e in calls_edges:
            assert e.confidence == 0.8

    def test_calls_edge_derivation_label(self, calls_store):
        """CALLS edges should be labeled 'call_graph'."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(calls_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_func" and e.source_node_id == "validate"]
        assert matching[0].derivation == "call_graph"

    def test_calls_edge_ignores_non_test_source(self):
        """Only include CALLS edges where test node calls source node."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "t1", name="test_func",
            file_path="tests/test_x.py",
            qualified_name="tests/test_x.py::function.test_func",
        ))
        s.insert_node(_make_node(
            "s1", name="source_func",
            file_path="src/x.py",
            qualified_name="src/x.py::function.source_func",
        ))
        s.insert_node(_make_node(
            "other_test", name="test_other",
            file_path="tests/test_other.py",
            qualified_name="tests/test_other.py::function.test_other",
        ))
        # t1 calls s1 (valid) and also calls another test function (should not create edge)
        s.insert_edge(_make_edge("t1", "s1", kind="calls"))
        s.insert_edge(_make_edge("t1", "other_test", kind="calls"))

        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        # Should have edge for t1 → s1 but NOT t1 → other_test
        valid = [e for e in edges if e.source_node_id == "s1"]
        invalid = [e for e in edges if e.source_node_id == "other_test"]
        assert len(valid) >= 1
        assert len(invalid) == 0


# =============================================================================
# Tests — IMPORTS Edge Association (confidence=0.5)
# =============================================================================

class TestImportsEdgeMatching:
    """Step 3: Derive test↔source via IMPORTS edges."""

    def test_imports_edge_creates_match(self, imports_store):
        """IMPORTS edge from test node to source module → test edge."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(imports_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_parse" and e.source_node_id == "parse"]
        assert len(matching) >= 1

    def test_imports_edge_confidence(self, imports_store):
        """IMPORTS-derived edges should have confidence 0.5."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(imports_store)
        imports_edges = [e for e in edges if e.derivation == "import_reference"]
        for e in imports_edges:
            assert e.confidence == 0.5

    def test_imports_edge_derivation_label(self, imports_store):
        """IMPORTS edges should be labeled 'import_reference'."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(imports_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_parse" and e.source_node_id == "parse"]
        assert matching[0].derivation == "import_reference"


# =============================================================================
# Tests — Deduplication
# =============================================================================

class TestDeduplication:
    """Deduplication: same (test, source) pair keeps highest confidence."""

    def test_keeps_highest_confidence(self, multi_confidence_store):
        """When same pair found via naming (0.9) and calls (0.8), keep 0.9."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(multi_confidence_store)
        matching = [e for e in edges
                     if e.test_node_id == "test_calc" and e.source_node_id == "calc"]
        assert len(matching) == 1
        assert matching[0].confidence == 0.9
        assert matching[0].derivation == "naming_convention"

    def test_no_duplicate_pairs(self, full_store):
        """Each unique (test, source) pair should appear at most once."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(full_store)
        pairs = [(e.test_node_id, e.source_node_id) for e in edges]
        assert len(pairs) == len(set(pairs))


# =============================================================================
# Tests — Edge cases
# =============================================================================

class TestEdgeCases:
    """Boundary and error conditions."""

    def test_empty_store_returns_empty(self, empty_store):
        """An empty store should yield no test edges."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(empty_store)
        assert edges == []

    def test_no_test_files_returns_empty(self):
        """Store with no test functions/files should return empty."""
        s = MemoryStore()
        s.insert_node(_make_node("n1", name="helper", file_path="src/util.py"))
        s.insert_node(_make_node("n2", name="process", file_path="src/main.py"))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        assert edges == []

    def test_test_function_with_no_matching_source(self):
        """Test function with no naming match, no calls, no imports → no edge."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "t1", name="test_unknown",
            file_path="tests/test_mystery.py",
            qualified_name="tests/test_mystery.py::function.test_unknown",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        assert edges == []

    def test_only_source_nodes_no_tests(self):
        """Store with only source functions, no test nodes."""
        s = MemoryStore()
        s.insert_node(_make_node("s1", name="foo", file_path="src/foo.py"))
        s.insert_node(_make_node("s2", name="bar", file_path="src/bar.py"))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        assert edges == []

    def test_edge_fields_are_populated(self, basic_store):
        """All TestEdge fields should be populated correctly."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(basic_store)
        for e in edges:
            assert e.test_node_id
            assert e.test_name
            assert e.test_file_path
            assert e.source_node_id
            assert e.source_name
            assert e.source_file_path
            assert isinstance(e.confidence, float)
            assert 0.0 <= e.confidence <= 1.0
            assert e.derivation in (
                "naming_convention", "call_graph", "import_reference"
            )

    def test_camel_case_test_name(self):
        """testCalculate should match calculate."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "t1", name="testCalculate",
            file_path="tests/test_math.py",
            qualified_name="tests/test_math.py::function.testCalculate",
        ))
        s.insert_node(_make_node(
            "s1", name="calculate",
            file_path="src/math.py",
            qualified_name="src/math.py::function.calculate",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        matching = [e for e in edges if e.test_node_id == "t1" and e.source_node_id == "s1"]
        assert len(matching) >= 1

    def test_test_name_with_underscore_then_camel(self):
        """test_calculate_total should match calculate_total."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "t1", name="test_calculate_total",
            file_path="tests/test_math.py",
            qualified_name="tests/test_math.py::function.test_calculate_total",
        ))
        s.insert_node(_make_node(
            "s1", name="calculate_total",
            file_path="src/math.py",
            qualified_name="src/math.py::function.calculate_total",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        matching = [e for e in edges if e.test_node_id == "t1" and e.source_node_id == "s1"]
        assert len(matching) >= 1

    def test_non_python_test_file(self):
        """TypeScript test file (Foo.spec.ts) with FooSpec → Foo naming match."""
        s = MemoryStore()
        s.insert_node(_make_node(
            "t1", name="FooSpec", kind="class",
            file_path="src/__tests__/foo.spec.ts",
            qualified_name="src/__tests__/foo.spec.ts::class.FooSpec",
            language="typescript",
        ))
        s.insert_node(_make_node(
            "s1", name="Foo", kind="class",
            file_path="src/foo.ts",
            qualified_name="src/foo.ts::class.Foo",
            language="typescript",
        ))
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(s)
        assert len(edges) >= 1


# =============================================================================
# Tests — Full integration
# =============================================================================

class TestFullIntegration:
    """End-to-end integration tests."""

    def test_all_derivations_in_full_store(self, full_store):
        """Full store should produce edges via all three derivation methods."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(full_store)
        derivations = {e.derivation for e in edges}
        assert "naming_convention" in derivations
        assert "call_graph" in derivations
        assert "import_reference" in derivations

    def test_naming_convention_edges_in_full_store(self, full_store):
        """Naming convention should match test_add → add."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(full_store)
        add_edge = [e for e in edges
                     if e.test_node_id == "t_add" and e.source_node_id == "s_add"]
        assert len(add_edge) == 1
        assert add_edge[0].confidence == 0.9

    def test_subtract_naming_match(self, full_store):
        """test_subtract → subtract via naming convention."""
        analyzer = TestEdgeAnalyzer()
        edges = analyzer.analyze(full_store)
        sub_edge = [e for e in edges
                     if e.test_node_id == "t_sub" and e.source_node_id == "s_sub"]
        assert len(sub_edge) == 1
