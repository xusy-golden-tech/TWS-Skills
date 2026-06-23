"""TDD tests for VariableUsageExtractor (P7 data flow analysis).

Tests are written BEFORE the implementation. They will fail until
VariableUsageExtractor is implemented in extractors/usage.py.

Test strategy:
- Use tree-sitter to parse Python/TypeScript source snippets
- Build func_node_ids dicts matching the store format {qualified_name: node_id}
- Call VariableUsageExtractor.extract() and verify edge lists returned
- Verify edge structure, categorization, dedup, and target_text format

Design reference: D:\\TWS-Skills\\.tws\\sessions\\design-p7-dataflow.md
"""

import hashlib
import pytest
from tree_sitter_language_pack import get_parser
from tws_graph.edges.kind import EdgeKind


def _hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID matching tws_graph.indexer.base.hash_id."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ============================================================================
# Python tests
# ============================================================================


class TestVariableUsageExtractorPython:
    """Variable usage extraction tests for Python source code."""

    @pytest.fixture
    def parser(self):
        return get_parser("python")

    @pytest.fixture
    def file_path(self):
        return "test.py"

    def _extract(self, code, parser, file_path, func_node_ids):
        from tws_graph.indexer.extractors.usage import VariableUsageExtractor

        extractor = VariableUsageExtractor()
        tree = parser.parse(code)
        source = code.encode("utf-8")
        return extractor.extract(source, tree, func_node_ids, file_path, "python")

    # ------------------------------------------------------------------
    # write detection
    # ------------------------------------------------------------------

    def test_assignment_produces_write_edge(self, parser, file_path):
        """x = 1 in a function produces a WRITES edge for var:x."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert len(writes) == 1, f"Expected 1 write edge, got {len(writes)}"
        assert writes[0]["source"] == fid
        assert writes[0]["target"] == ""
        assert writes[0]["target_text"] == "var:x"
        assert writes[0]["provenance"] == "tree-sitter"
        assert file_path in writes[0]["source_loc"]

    def test_multiple_writes_to_different_variables(self, parser, file_path):
        """Multiple assignments to different variables produce distinct edges."""
        code = "def foo():\n    x = 1\n    y = 2\n    z = 3\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets
        assert "var:y" in write_targets
        assert "var:z" in write_targets
        assert len(write_targets) == 3

    def test_augmented_assignment_produces_write_and_read(self, parser, file_path):
        """x += 1 is both a read and a write of x."""
        code = "def foo():\n    x += 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value
                  and e["target_text"] == "var:x"]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(writes) == 1, "x += 1 should produce a write to x"
        assert len(reads) == 1, "x += 1 should produce a read of x"

    # ------------------------------------------------------------------
    # read detection
    # ------------------------------------------------------------------

    def test_read_identifier_in_expression(self, parser, file_path):
        """Reading x in print(x) produces a READS edge for var:x."""
        code = "def foo():\n    print(x)\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(reads) == 1, f"Expected 1 read of x, got {len(reads)}"

    def test_attribute_read(self, parser, file_path):
        """Accessing obj.attr produces a READS edge for the attribute."""
        code = "def foo():\n    return obj.x\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        # obj.x as attribute read; the extractor records attribute text
        assert any("obj.x" in t for t in read_targets | {"var:obj.x"}), \
            f"Attribute read should include obj.x, got {read_targets}"

    # ------------------------------------------------------------------
    # throw detection
    # ------------------------------------------------------------------

    def test_raise_produces_throw_edge(self, parser, file_path):
        """raise ValueError(...) produces a THROWS edge."""
        code = 'def foo():\n    raise ValueError("bad")\n'
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        throws = [e for e in edges if e["kind"] == EdgeKind.THROWS.value]
        assert len(throws) >= 1, f"Expected at least 1 throw edge, got {len(throws)}"
        assert any("ValueError" in e.get("target_text", "") for e in throws)

    # ------------------------------------------------------------------
    # parameters in write_set
    # ------------------------------------------------------------------

    def test_function_parameters_in_write_set(self, parser, file_path):
        """Function parameters count as implicit writes (entry-point writes)."""
        code = "def foo(a, b, c):\n    return a + b + c\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:a" in write_targets, f"Parameter a not in write_set: {write_targets}"
        assert "var:b" in write_targets, f"Parameter b not in write_set: {write_targets}"
        assert "var:c" in write_targets, f"Parameter c not in write_set: {write_targets}"

    # ------------------------------------------------------------------
    # for loop variable
    # ------------------------------------------------------------------

    def test_for_loop_variable_in_write_set(self, parser, file_path):
        """for_stmt left-hand side is a write."""
        code = "def foo():\n    for x in items:\n        print(x)\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets, f"For-loop variable x should be in write_set: {write_targets}"

    # ------------------------------------------------------------------
    # with as clause
    # ------------------------------------------------------------------

    def test_with_as_produces_write(self, parser, file_path):
        """with open(...) as fh: fh is a write."""
        code = "def foo():\n    with open('f') as fh:\n        pass\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:fh" in write_targets, f"with-as variable fh should be in write_set: {write_targets}"

    # ------------------------------------------------------------------
    # dedup
    # ------------------------------------------------------------------

    def test_dedup_same_variable_multiple_uses(self, parser, file_path):
        """Multiple reads/writes of the same variable produce only one edge each."""
        code = "def foo():\n    x = 1\n    print(x)\n    print(x)\n    x = 2\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value
                  and e["target_text"] == "var:x"]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(writes) == 1, f"Multiple writes to x should be deduped to 1 edge, got {len(writes)}"
        assert len(reads) == 1, f"Multiple reads of x should be deduped to 1 edge, got {len(reads)}"

    def test_dedup_read_write_are_separate(self, parser, file_path):
        """A variable that is both read and written gets one read edge + one write edge."""
        code = "def foo():\n    x = 1\n    y = x + 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        assert len(writes) >= 2  # x, y
        assert len(reads) >= 1   # x read in y = x + 1

    # ------------------------------------------------------------------
    # empty function
    # ------------------------------------------------------------------

    def test_empty_function_produces_no_edges(self, parser, file_path):
        """A function with only pass produces zero edges."""
        code = "def foo():\n    pass\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        assert len(edges) == 0, f"Empty function should produce 0 edges, got {len(edges)}"

    def test_empty_function_no_func_node_ids(self, parser, file_path):
        """A module with no functions and empty func_node_ids produces zero edges."""
        code = "\n"
        edges = self._extract(code, parser, file_path, {})
        assert len(edges) == 0

    # ------------------------------------------------------------------
    # nested function independence
    # ------------------------------------------------------------------

    def test_nested_function_independence(self, parser, file_path):
        """Inner function's reads/writes must NOT leak into the outer function."""
        code = (
            "def outer():\n    x = 1\n"
            "    def inner():\n        y = 2\n        print(y)\n"
            "    return x\n"
        )
        outer_id = _hash_id("test.py::outer", file_path)
        inner_id = _hash_id("test.py::outer::inner", file_path)
        func_node_ids = {
            "test.py::outer": outer_id,
            "test.py::outer::inner": inner_id,
        }
        edges = self._extract(code, parser, file_path, func_node_ids)

        # Outer edges
        outer_writes = [e for e in edges
                        if e["source"] == outer_id and e["kind"] == EdgeKind.WRITES.value]
        outer_write_tgts = {e["target_text"] for e in outer_writes}
        assert "var:x" in outer_write_tgts, f"Outer should write x, got {outer_write_tgts}"
        assert "var:y" not in outer_write_tgts, \
            f"Outer should NOT write y (inner's variable), got {outer_write_tgts}"

        # Inner edges
        inner_writes = [e for e in edges
                        if e["source"] == inner_id and e["kind"] == EdgeKind.WRITES.value]
        inner_write_tgts = {e["target_text"] for e in inner_writes}
        assert "var:y" in inner_write_tgts, f"Inner should write y, got {inner_write_tgts}"
        assert "var:x" not in inner_write_tgts, \
            f"Inner should NOT write x (outer's variable), got {inner_write_tgts}"

    # ------------------------------------------------------------------
    # class method
    # ------------------------------------------------------------------

    def test_class_method(self, parser, file_path):
        """Methods in Python classes are extracted as write/read sources."""
        code = "class C:\n    def m(self):\n        x = 1\n        return x\n"
        method_id = _hash_id("test.py::C::m", file_path)
        func_node_ids = {"test.py::C::m": method_id}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        assert len(writes) >= 1, f"Method should have writes, got {len(writes)}"
        assert len(reads) >= 1, f"Method should have reads, got {len(reads)}"

    # ------------------------------------------------------------------
    # structure checks
    # ------------------------------------------------------------------

    def test_edge_has_required_fields(self, parser, file_path):
        """Every edge dict contains all required fields."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        required = {"source", "target", "kind", "target_text", "source_loc", "provenance"}
        for e in edges:
            missing = required - set(e.keys())
            assert not missing, f"Edge missing required fields: {missing}"

    def test_target_is_empty_string(self, parser, file_path):
        """READS/WRITES edges have target='' (variable is not a graph node)."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        for e in edges:
            assert e["target"] == "", \
                f"Expected target='' for READS/WRITES edge, got {e['target']!r}"

    def test_target_text_var_prefix(self, parser, file_path):
        """READS/WRITES edges have target_text starting with 'var:'."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        rw_edges = [e for e in edges
                    if e["kind"] in (EdgeKind.READS.value, EdgeKind.WRITES.value)]
        for e in rw_edges:
            assert e["target_text"].startswith("var:"), \
                f"Expected target_text 'var:<name>', got {e['target_text']!r}"

    def test_edge_provenance_is_tree_sitter(self, parser, file_path):
        """All edges have provenance='tree-sitter'."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        for e in edges:
            assert e.get("provenance") == "tree-sitter", \
                f"Expected provenance='tree-sitter', got {e.get('provenance')!r}"

    def test_file_path_in_source_loc(self, parser, file_path):
        """source_loc field contains the file_path."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        for e in edges:
            assert file_path in e.get("source_loc", ""), \
                f"source_loc should contain file_path, got {e.get('source_loc')!r}"

    def test_kind_uses_edgekind_values(self, parser, file_path):
        """Edge kind strings are exactly EdgeKind.READS.value etc."""
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        valid_kinds = {EdgeKind.READS.value, EdgeKind.WRITES.value, EdgeKind.THROWS.value}
        for e in edges:
            assert e["kind"] in valid_kinds, \
                f"Unexpected edge kind {e['kind']!r}, allowed: {valid_kinds}"

    # ------------------------------------------------------------------
    # nonlocal / global detection
    # ------------------------------------------------------------------

    def test_nonlocal_variable(self, parser, file_path):
        """nonlocal x: the variable is detected as a write, annotated as nonlocal."""
        code = "def outer():\n    x = 1\n    def inner():\n        nonlocal x\n        x = 2\n"
        inner_id = _hash_id("test.py::outer::inner", file_path)
        func_node_ids = {"test.py::outer::inner": inner_id}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        # Design spec: nonlocal variables annotated as var:<name>:nonlocal
        assert "var:x:nonlocal" in write_targets, \
            f"Nonlocal x should be annotated var:x:nonlocal, got {write_targets}"

    def test_global_variable(self, parser, file_path):
        """global x: the variable is detected, annotated as global."""
        code = "def foo():\n    global x\n    x = 1\n"
        fid = _hash_id("test.py::foo", file_path)
        func_node_ids = {"test.py::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x:global" in write_targets, \
            f"Global x should be annotated var:x:global, got {write_targets}"


# ============================================================================
# TypeScript tests
# ============================================================================


class TestVariableUsageExtractorTypeScript:
    """Variable usage extraction tests for TypeScript source code."""

    @pytest.fixture
    def parser(self):
        return get_parser("typescript")

    @pytest.fixture
    def file_path(self):
        return "test.ts"

    def _extract(self, code, parser, file_path, func_node_ids):
        from tws_graph.indexer.extractors.usage import VariableUsageExtractor

        extractor = VariableUsageExtractor()
        tree = parser.parse(code)
        source = code.encode("utf-8")
        return extractor.extract(source, tree, func_node_ids, file_path, "typescript")

    # ------------------------------------------------------------------
    # write detection: variable_declarator
    # ------------------------------------------------------------------

    def test_let_declaration_produces_write(self, parser, file_path):
        """let x = 1 produces a WRITES edge for var:x."""
        code = "function foo() {\n  let x = 1;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert any(e["target_text"] == "var:x" for e in writes), \
            f"Expected WRITES edge for var:x, got {[e['target_text'] for e in writes]}"

    def test_const_declaration_produces_write(self, parser, file_path):
        """const y = 2 produces a WRITES edge for var:y."""
        code = "function foo() {\n  const y = 2;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert any(e["target_text"] == "var:y" for e in writes), \
            f"Expected WRITES edge for var:y, got {[e['target_text'] for e in writes]}"

    # ------------------------------------------------------------------
    # assignment_expression (write + read)
    # ------------------------------------------------------------------

    def test_assignment_expression_produces_write_and_read(self, parser, file_path):
        """x = y: x is a write, y is a read."""
        code = "function foo() {\n  x = y;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        assert any(e["target_text"] == "var:x" for e in writes), \
            f"x should be written, got writes: {[e['target_text'] for e in writes]}"
        assert any(e["target_text"] == "var:y" for e in reads), \
            f"y should be read, got reads: {[e['target_text'] for e in reads]}"

    def test_augmented_assignment_ts(self, parser, file_path):
        """x += 1 in TS is both a read and write of x."""
        code = "function foo() {\n  x += 1;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value
                  and e["target_text"] == "var:x"]
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(writes) == 1
        assert len(reads) == 1

    # ------------------------------------------------------------------
    # throw statement
    # ------------------------------------------------------------------

    def test_throw_statement_produces_throw_edge(self, parser, file_path):
        """throw new Error("bad") produces a THROWS edge with Error in target_text."""
        code = 'function foo() {\n  throw new Error("bad");\n}\n'
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        throws = [e for e in edges if e["kind"] == EdgeKind.THROWS.value]
        assert len(throws) >= 1, f"Expected at least 1 throw edge, got {len(throws)}"
        assert any("Error" in e.get("target_text", "") for e in throws), \
            f"Expected Error in throw target_text, got {[e.get('target_text') for e in throws]}"

    # ------------------------------------------------------------------
    # read in call argument / member expression
    # ------------------------------------------------------------------

    def test_read_in_call_argument(self, parser, file_path):
        """console.log(x): x is read."""
        code = "function foo() {\n  console.log(x);\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        assert "var:x" in read_targets, \
            f"x should be read, got read targets: {read_targets}"

    # ------------------------------------------------------------------
    # function parameters in write_set
    # ------------------------------------------------------------------

    def test_function_parameters_in_write_set(self, parser, file_path):
        """TS function parameters are implicit writes."""
        code = "function foo(a: number, b: string): void {\n  return a;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:a" in write_targets, f"Parameter a not in write_set: {write_targets}"
        assert "var:b" in write_targets, f"Parameter b not in write_set: {write_targets}"

    # ------------------------------------------------------------------
    # empty function
    # ------------------------------------------------------------------

    def test_empty_function_produces_no_edges(self, parser, file_path):
        """An empty TS function body produces zero edges."""
        code = "function foo() {\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        assert len(edges) == 0, f"Empty function should produce 0 edges, got {len(edges)}"

    # ------------------------------------------------------------------
    # nested function independence (TS)
    # ------------------------------------------------------------------

    def test_nested_function_independence(self, parser, file_path):
        """Inner TS function variable usage does not leak to outer."""
        code = (
            "function outer() {\n  let x = 1;\n"
            "  function inner() {\n    let y = 2;\n    return y;\n  }\n"
            "  return x;\n}\n"
        )
        outer_id = _hash_id("test.ts::outer", file_path)
        inner_id = _hash_id("test.ts::outer::inner", file_path)
        func_node_ids = {
            "test.ts::outer": outer_id,
            "test.ts::outer::inner": inner_id,
        }
        edges = self._extract(code, parser, file_path, func_node_ids)

        outer_writes = [e for e in edges
                        if e["source"] == outer_id and e["kind"] == EdgeKind.WRITES.value]
        outer_write_tgts = {e["target_text"] for e in outer_writes}
        assert "var:x" in outer_write_tgts
        assert "var:y" not in outer_write_tgts

        inner_writes = [e for e in edges
                        if e["source"] == inner_id and e["kind"] == EdgeKind.WRITES.value]
        inner_write_tgts = {e["target_text"] for e in inner_writes}
        assert "var:y" in inner_write_tgts

    # ------------------------------------------------------------------
    # method definition in class
    # ------------------------------------------------------------------

    def test_method_definition(self, parser, file_path):
        """TS class methods produce edges keyed by the method's node_id."""
        code = "class C {\n  m() {\n    let x = 1;\n  }\n}\n"
        method_id = _hash_id("test.ts::C::m", file_path)
        func_node_ids = {"test.ts::C::m": method_id}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert any(e["target_text"] == "var:x" and e["source"] == method_id
                   for e in writes), \
            f"Method should have write for var:x with source={method_id}"

    # ------------------------------------------------------------------
    # arrow function
    # ------------------------------------------------------------------

    def test_arrow_function(self, parser, file_path):
        """Arrow functions (assigned to variables) are supported."""
        code = "const foo = () => {\n  let x = 1;\n};\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert any(e["target_text"] == "var:x" for e in writes), \
            f"Arrow function should capture write to x, got {[e['target_text'] for e in writes]}"

    # ------------------------------------------------------------------
    # dedup (TS)
    # ------------------------------------------------------------------

    def test_dedup_same_variable(self, parser, file_path):
        """Multiple reads/writes of same variable produce one edge each."""
        code = (
            "function foo() {\n  let x = 1;\n  x = 2;\n"
            "  console.log(x);\n  console.log(x);\n}\n"
        )
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges
                  if e["kind"] == EdgeKind.WRITES.value and e["target_text"] == "var:x"]
        reads = [e for e in edges
                 if e["kind"] == EdgeKind.READS.value and e["target_text"] == "var:x"]
        assert len(writes) == 1, f"x writes should be deduped to 1, got {len(writes)}"
        assert len(reads) == 1, f"x reads should be deduped to 1, got {len(reads)}"

    # ------------------------------------------------------------------
    # structure checks (TS)
    # ------------------------------------------------------------------

    def test_edge_provenance(self, parser, file_path):
        """All TS edges have provenance='tree-sitter'."""
        code = "function foo() {\n  let x = 1;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        for e in edges:
            assert e.get("provenance") == "tree-sitter", \
                f"Expected provenance='tree-sitter', got {e.get('provenance')!r}"

    def test_target_text_var_prefix_ts(self, parser, file_path):
        """READS/WRITES edges in TS have target_text starting with 'var:'."""
        code = "function foo() {\n  let x = 1;\n}\n"
        fid = _hash_id("test.ts::foo", file_path)
        func_node_ids = {"test.ts::foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        rw_edges = [e for e in edges
                    if e["kind"] in (EdgeKind.READS.value, EdgeKind.WRITES.value)]
        for e in rw_edges:
            assert e["target_text"].startswith("var:"), \
                f"Expected target_text 'var:<name>', got {e['target_text']!r}"


# ============================================================================
# Edge structure contract tests (language-agnostic)
# ============================================================================


class TestVariableUsageEdgeStructure:
    """Validate edge dict contract across languages."""

    def _extract(self, code, language, func_node_ids, file_path="test.py"):
        from tws_graph.indexer.extractors.usage import VariableUsageExtractor

        parser = get_parser(language)
        tree = parser.parse(code)
        source = code.encode("utf-8")
        extractor = VariableUsageExtractor()
        return extractor.extract(source, tree, func_node_ids, file_path, language)

    def test_all_edges_have_required_fields_python(self):
        code = "def foo():\n    x = 1\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        required = {"source", "target", "kind", "target_text", "source_loc", "provenance"}
        for e in edges:
            missing = required - set(e.keys())
            assert not missing, f"Edge missing required fields: {missing}"

    def test_all_edges_have_required_fields_typescript(self):
        code = "function foo() {\n  let x = 1;\n}\n"
        fid = _hash_id("test.ts::foo", "test.ts")
        edges = self._extract(code, "typescript", {"test.ts::foo": fid}, "test.ts")
        required = {"source", "target", "kind", "target_text", "source_loc", "provenance"}
        for e in edges:
            missing = required - set(e.keys())
            assert not missing, f"Edge missing required fields: {missing}"

    def test_target_is_empty_string(self):
        """All READS/WRITES/THROWS edges must have target='' (not a graph node)."""
        code = "def foo():\n    x = 1\n    raise ValueError()\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        for e in edges:
            assert e["target"] == "", \
                f"Expected target='', got {e['target']!r} for kind={e['kind']}"


# ============================================================================
# Edge-case and robustness tests
# ============================================================================


class TestVariableUsageEdgeCases:
    """Edge cases for VariableUsageExtractor."""

    def _extract(self, code, language, func_node_ids, file_path="test.py"):
        from tws_graph.indexer.extractors.usage import VariableUsageExtractor

        parser = get_parser(language)
        tree = parser.parse(code)
        source = code.encode("utf-8")
        extractor = VariableUsageExtractor()
        return extractor.extract(source, tree, func_node_ids, file_path, language)

    def test_module_level_code_no_functions(self):
        """Module-level code without any function defs produces 0 edges."""
        code = "x = 1\ny = x + 2\n"
        edges = self._extract(code, "python", {})
        assert len(edges) == 0

    def test_func_not_in_func_node_ids_skipped(self):
        """A function in the source but NOT in func_node_ids is skipped."""
        code = "def foo():\n    x = 1\n"
        # foo NOT in func_node_ids
        edges = self._extract(code, "python", {})
        assert len(edges) == 0

    def test_partial_func_node_ids(self):
        """Only functions listed in func_node_ids are analyzed."""
        code = "def foo():\n    x = 1\ndef bar():\n    y = 2\n"
        foo_id = _hash_id("test.py::foo", "test.py")
        # Only request foo, not bar
        edges = self._extract(code, "python", {"test.py::foo": foo_id})
        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets
        assert "var:y" not in write_targets, "bar should not be analyzed"

    def test_return_statement_with_identifier(self):
        """return x: x is a read."""
        code = "def foo():\n    return x\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(reads) == 1, f"return x should produce a read of x, got {len(reads)} reads"

    def test_if_condition_reads(self):
        """if x: x is a read."""
        code = "def foo():\n    if x:\n        pass\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(reads) == 1, f"if x should produce a read of x"

    def test_while_condition_reads(self):
        """while x: x is a read."""
        code = "def foo():\n    while x:\n        pass\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value
                 and e["target_text"] == "var:x"]
        assert len(reads) == 1, f"while x should produce a read of x"

    def test_binary_expression_reads(self):
        """a + b: both a and b are reads."""
        code = "def foo():\n    return a + b\n"
        fid = _hash_id("test.py::foo", "test.py")
        edges = self._extract(code, "python", {"test.py::foo": fid})
        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        assert "var:a" in read_targets, f"a should be read, got {read_targets}"
        assert "var:b" in read_targets, f"b should be read, got {read_targets}"


# ============================================================================
# Java tests
# ============================================================================


class TestVariableUsageExtractorJava:
    """Variable usage extraction tests for Java source code."""

    @pytest.fixture
    def parser(self):
        return get_parser("java")

    @pytest.fixture
    def file_path(self):
        return "Test.java"

    def _extract(self, code, parser, file_path, func_node_ids):
        from tws_graph.indexer.extractors.usage import VariableUsageExtractor

        extractor = VariableUsageExtractor()
        tree = parser.parse(code)
        source = code.encode("utf-8")
        return extractor.extract(source, tree, func_node_ids, file_path, "java")

    # ------------------------------------------------------------------
    # write detection: variable_declarator
    # ------------------------------------------------------------------

    def test_variable_declaration_produces_write(self, parser, file_path):
        """int x = 1; produces a WRITES edge for var:x."""
        code = "class Foo { void bar() { int x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets, \
            f"Expected WRITES edge for var:x, got {write_targets}"

    def test_assignment_expression_produces_write(self, parser, file_path):
        """x = 1; produces a WRITES edge for var:x."""
        code = "class Foo { void bar() { int x; x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets, \
            f"Expected WRITES edge for var:x, got {write_targets}"

    def test_for_loop_variable_produces_write(self, parser, file_path):
        """for (int i = 0; ...) : i is a write."""
        code = "class Foo { void bar() { for (int i = 0; i < 10; i++) { int y = i; } } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:i" in write_targets, \
            f"For-loop variable i should be in write_set, got {write_targets}"

    def test_enhanced_for_loop_variable_produces_write(self, parser, file_path):
        """for (int x : arr) : x is a write."""
        code = "class Foo { void bar(int[] arr) { for (int x : arr) { int y = x; } } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets, \
            f"Enhanced-for variable x should be in write_set, got {write_targets}"

    # ------------------------------------------------------------------
    # read detection
    # ------------------------------------------------------------------

    def test_identifier_read_in_expression(self, parser, file_path):
        """Reading x in expression y = x + 1 produces READS edge."""
        code = "class Foo { void bar() { int y = x + 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        assert "var:x" in read_targets, \
            f"x should be read, got read targets: {read_targets}"

    def test_field_access_produces_read(self, parser, file_path):
        """obj.field produces a READS edge."""
        code = "class Foo { void bar(Foo obj) { int y = obj.field; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        assert any("obj.field" in t for t in read_targets), \
            f"Field access should produce a read with obj.field, got {read_targets}"

    def test_method_call_argument_read(self, parser, file_path):
        """foo(x): x in argument position is a read."""
        code = "class Foo { void bar() { foo(x); } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        reads = [e for e in edges if e["kind"] == EdgeKind.READS.value]
        read_targets = {e["target_text"] for e in reads}
        assert "var:x" in read_targets, \
            f"x should be read as argument, got {read_targets}"

    # ------------------------------------------------------------------
    # throw detection
    # ------------------------------------------------------------------

    def test_throw_statement_produces_throw_edge(self, parser, file_path):
        """throw new Exception() produces a THROWS edge."""
        code = "class Foo { void bar() { throw new RuntimeException(); } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        throws = [e for e in edges if e["kind"] == EdgeKind.THROWS.value]
        assert len(throws) >= 1, f"Expected at least 1 throw edge, got {len(throws)}"
        assert any("RuntimeException" in e.get("target_text", "") for e in throws), \
            f"Expected RuntimeException in throw target_text, got {[e.get('target_text') for e in throws]}"

    def test_try_catch_produces_throw_edge(self, parser, file_path):
        """try/catch block: caught exception type is recorded as throw."""
        code = "class Foo { void bar() { try { x(); } catch (Exception e) { } } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        throws = [e for e in edges if e["kind"] == EdgeKind.THROWS.value]
        assert any("Exception" in e.get("target_text", "") for e in throws), \
            f"Expected Exception in throw target_text from catch, got {[e.get('target_text') for e in throws]}"

    # ------------------------------------------------------------------
    # function parameters in write_set
    # ------------------------------------------------------------------

    def test_method_parameters_in_write_set(self, parser, file_path):
        """Method parameters count as implicit writes."""
        code = "class Foo { void bar(int a, String b) { int c = a; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:a" in write_targets, f"Parameter a not in write_set: {write_targets}"
        assert "var:b" in write_targets, f"Parameter b not in write_set: {write_targets}"

    def test_constructor_parameters_in_write_set(self, parser, file_path):
        """Constructor parameters are also implicit writes."""
        code = "class Foo { Foo(int x) { int y = x; } }"
        fid = _hash_id("Test.java::Foo::Foo", file_path)
        func_node_ids = {"Test.java::Foo::Foo": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        write_targets = {e["target_text"] for e in writes}
        assert "var:x" in write_targets, f"Constructor param x not in write_set: {write_targets}"

    # ------------------------------------------------------------------
    # class method (method inside class)
    # ------------------------------------------------------------------

    def test_class_method_qualified_name(self, parser, file_path):
        """Method inside class has qualified_name file::Class::method."""
        code = "class Foo { void bar() { int x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        writes = [e for e in edges if e["kind"] == EdgeKind.WRITES.value]
        assert any(e["source"] == fid and e["target_text"] == "var:x" for e in writes), \
            f"Expected write edge from {fid} for var:x"

    # ------------------------------------------------------------------
    # edges structure checks
    # ------------------------------------------------------------------

    def test_edge_has_required_fields(self, parser, file_path):
        """Every edge dict contains all required fields."""
        code = "class Foo { void bar() { int x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        required = {"source", "target", "kind", "target_text", "source_loc", "provenance"}
        for e in edges:
            missing = required - set(e.keys())
            assert not missing, f"Edge missing required fields: {missing}"

    def test_target_text_var_prefix(self, parser, file_path):
        """READS/WRITES edges have target_text starting with 'var:'."""
        code = "class Foo { void bar() { int x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)

        rw_edges = [e for e in edges
                    if e["kind"] in (EdgeKind.READS.value, EdgeKind.WRITES.value)]
        for e in rw_edges:
            assert e["target_text"].startswith("var:"), \
                f"Expected target_text 'var:<name>', got {e['target_text']!r}"

    def test_provenance_is_tree_sitter(self, parser, file_path):
        """All edges have provenance='tree-sitter'."""
        code = "class Foo { void bar() { int x = 1; } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        for e in edges:
            assert e.get("provenance") == "tree-sitter", \
                f"Expected provenance='tree-sitter', got {e.get('provenance')!r}"

    def test_empty_method_produces_no_edges(self, parser, file_path):
        """An empty Java method body produces zero edges."""
        code = "class Foo { void bar() { } }"
        fid = _hash_id("Test.java::Foo::bar", file_path)
        func_node_ids = {"Test.java::Foo::bar": fid}
        edges = self._extract(code, parser, file_path, func_node_ids)
        assert len(edges) == 0, f"Empty method should produce 0 edges, got {len(edges)}"

    def test_method_not_in_func_node_ids_skipped(self, parser, file_path):
        """Methods not in func_node_ids are skipped."""
        code = "class Foo { void bar() { int x = 1; } }"
        edges = self._extract(code, parser, file_path, {})
        assert len(edges) == 0
