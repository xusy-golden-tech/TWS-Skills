"""Tests for DataFlowExtractor.

TDD test suite for the DataFlowExtractor class defined in:
    tws_graph/indexer/extractors/dataflow.py

Verifies that extract() correctly maps call-site positional/keyword arguments
to callee parameter names, producing DATA_FLOWS edges per the P7 design spec.

Design doc: .tws/sessions/design-p7-dataflow.md
"""

import pytest
import tree_sitter_language_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_python(code: str):
    """Parse Python source, return (tree, source_bytes)."""
    parser = tree_sitter_language_pack.get_parser("python")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_typescript(code: str):
    """Parse TypeScript source, return (tree, source_bytes)."""
    parser = tree_sitter_language_pack.get_parser("typescript")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _extract(tree, source: bytes, func_node_ids: dict[str, str],
             file_path: str, language: str) -> list[dict]:
    """Call DataFlowExtractor.extract() and return edge list."""
    from tws_graph.indexer.extractors.dataflow import DataFlowExtractor
    return DataFlowExtractor().extract(
        source, tree, func_node_ids, file_path, language,
    )


def _by_target_text(edges: list[dict]) -> dict[str, dict]:
    """Index edges by target_text."""
    return {e["target_text"]: e for e in edges}


# ===================================================================
# Python positive tests
# ===================================================================

class TestDataFlowExtractorPython:
    """Positive tests for DataFlowExtractor with Python source."""

    # -- position-based mapping ----------------------------------------

    def test_basic_positional_two_args(self):
        """foo(a, b) calling def foo(x, y) -> 2 DATA_FLOWS edges."""
        code = "def foo(x, y):\n    pass\n\ndef bar(a, b):\n    foo(a, b)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2, f"Expected 2 edges, got {len(edges)}: {edges}"
        by_tt = _by_target_text(edges)
        assert "arg:0->param:x" in by_tt
        assert "arg:1->param:y" in by_tt
        for e in edges:
            assert e["source"] == "node_bar"
            assert e["target"] == "node_foo"
            assert e["kind"] == "data_flows"
            assert e["provenance"] == "tree-sitter"
            assert e["source_loc"].startswith("test.py:")

    def test_single_positional_arg(self):
        """foo(a) calling def foo(x) -> 1 DATA_FLOWS edge."""
        code = "def foo(x):\n    pass\n\ndef bar(a):\n    foo(a)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "node_bar"
        assert e["target"] == "node_foo"
        assert e["target_text"] == "arg:0->param:x"

    def test_three_positional_args(self):
        """foo(a, b, c) calling def foo(x, y, z) -> 3 DATA_FLOWS edges."""
        code = ("def foo(x, y, z):\n    pass\n\n"
                "def bar(a, b, c):\n    foo(a, b, c)\n")
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 3
        target_texts = {e["target_text"] for e in edges}
        assert target_texts == {"arg:0->param:x",
                                "arg:1->param:y",
                                "arg:2->param:z"}

    # -- method calls (self.) -----------------------------------------

    def test_method_call_self_single_arg(self):
        """self.method(42) def method(self, x) -> arg:0->param:x.

        self parameter is skipped when mapping positional args for
        instance-method calls.
        """
        code = ("class MyClass:\n"
                "    def method(self, x):\n        pass\n"
                "    def caller(self):\n        self.method(42)\n")
        tree, source = _parse_python(code)
        func_node_ids = {
            "test.py::MyClass::method": "node_method",
            "test.py::MyClass::caller": "node_caller",
        }

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 1, f"Got {len(edges)} edges: {edges}"
        e = edges[0]
        assert e["source"] == "node_caller"
        assert e["target"] == "node_method"
        assert e["target_text"] == "arg:0->param:x"

    def test_method_call_self_multi_arg(self):
        """self.method(a, b) def method(self, x, y) -> 2 mapped edges."""
        code = ("class MyClass:\n"
                "    def method(self, x, y):\n        pass\n"
                "    def caller(self, a, b):\n        self.method(a, b)\n")
        tree, source = _parse_python(code)
        func_node_ids = {
            "test.py::MyClass::method": "node_method",
            "test.py::MyClass::caller": "node_caller",
        }

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts
        for e in edges:
            assert e["source"] == "node_caller"
            assert e["target"] == "node_method"

    def test_method_call_another_method(self):
        """One method calls another method in the same class via self."""
        code = ("class MyClass:\n"
                "    def helper(self, x):\n        pass\n"
                "    def main(self, a):\n        self.helper(a)\n")
        tree, source = _parse_python(code)
        func_node_ids = {
            "test.py::MyClass::helper": "node_helper",
            "test.py::MyClass::main": "node_main",
        }

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "node_main"
        assert e["target"] == "node_helper"
        assert e["target_text"] == "arg:0->param:x"

    # -- keyword-argument matching ------------------------------------

    def test_keyword_args_by_name(self):
        """foo(y=2, x=1) def foo(x, y) -> matched by param name."""
        code = "def foo(x, y):\n    pass\n\ndef bar():\n    foo(y=2, x=1)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        # Keyword args matched by name: arg:x->param:x, arg:y->param:y
        assert "arg:x->param:x" in target_texts
        assert "arg:y->param:y" in target_texts

    def test_mixed_positional_and_keyword(self):
        """foo(a, y=2) def foo(x, y) -> arg:0->param:x, arg:y->param:y."""
        code = "def foo(x, y):\n    pass\n\ndef bar(a):\n    foo(a, y=2)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:y->param:y" in target_texts

    # -- recursion ----------------------------------------------------

    def test_recursive_call_source_equals_target(self):
        """def recurse(n): recurse(n-1) -> source == target."""
        code = "def recurse(n):\n    if n > 0:\n        recurse(n - 1)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::recurse": "node_recurse"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) >= 1
        for e in edges:
            assert e["source"] == "node_recurse"
            assert e["target"] == "node_recurse"

    # -- forward reference (callee defined after caller) ---------------

    def test_out_of_order_definitions(self):
        """Caller defined before callee: forward reference still works."""
        code = "def bar(a, b):\n    foo(a, b)\n\ndef foo(x, y):\n    pass\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    # -- default-parameter functions ----------------------------------

    def test_default_parameter_func(self):
        """Call arg positions map correctly when callee has defaults."""
        code = "def foo(x, y=10):\n    pass\n\ndef bar(a, b):\n    foo(a, b)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    # -- nested functions ---------------------------------------------

    def test_nested_function_call(self):
        """Inner function calling outer function of the same file."""
        code = ("def outer(x):\n    pass\n\n"
                "def bar():\n"
                "    def inner(a):\n        outer(a)\n"
                "    inner(1)\n")
        tree, source = _parse_python(code)
        func_node_ids = {
            "test.py::outer": "node_outer",
            "test.py::bar": "node_bar",
            "test.py::bar::inner": "node_inner",
        }

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        inner_to_outer = [e for e in edges
                          if e["source"] == "node_inner"
                          and e["target"] == "node_outer"]
        assert len(inner_to_outer) >= 1
        assert inner_to_outer[0]["target_text"] == "arg:0->param:x"


# ===================================================================
# TypeScript positive tests
# ===================================================================

class TestDataFlowExtractorTypeScript:
    """Positive tests for DataFlowExtractor with TypeScript source."""

    def test_basic_positional_call(self):
        """foo(a, b) calling function foo(x, y) -> 2 DATA_FLOWS edges."""
        code = ("function foo(x: number, y: string): void {}\n"
                "function bar(a: number, b: string): void { foo(a, b); }\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts
        for e in edges:
            assert e["source"] == "node_bar"
            assert e["target"] == "node_foo"
            assert e["kind"] == "data_flows"
            assert e["provenance"] == "tree-sitter"

    def test_method_call_this(self):
        """this.method(42) calling method(x) -> arg:0->param:x."""
        code = ("class MyClass {\n"
                "    method(x: number): void {}\n"
                "    caller(): void { this.method(42); }\n"
                "}\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {
            "test.ts::MyClass::method": "node_method",
            "test.ts::MyClass::caller": "node_caller",
        }

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 1, f"Got {len(edges)} edges: {edges}"
        e = edges[0]
        assert e["source"] == "node_caller"
        assert e["target"] == "node_method"
        assert e["target_text"] == "arg:0->param:x"

    def test_method_call_this_multi_arg(self):
        """this.method(a, b) calling method(x, y) -> 2 edges."""
        code = ("class MyClass {\n"
                "    method(x: number, y: string): void {}\n"
                "    caller(a: number, b: string): void { this.method(a, b); }\n"
                "}\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {
            "test.ts::MyClass::method": "node_method",
            "test.ts::MyClass::caller": "node_caller",
        }

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_multiple_args(self):
        """foo(a, b, c) calling function foo(x, y, z) -> 3 edges."""
        code = ("function foo(x: number, y: number, z: number): void {}\n"
                "function bar(a: number, b: number, c: number): void {\n"
                "    foo(a, b, c);\n}\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 3
        target_texts = {e["target_text"] for e in edges}
        assert target_texts == {"arg:0->param:x",
                                "arg:1->param:y",
                                "arg:2->param:z"}

    def test_recursive_call(self):
        """function recurse(n) calling itself -> source == target."""
        code = ("function recurse(n: number): void {\n"
                "    if (n > 0) { recurse(n - 1); }\n"
                "}\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::recurse": "node_recurse"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) >= 1
        for e in edges:
            assert e["source"] == "node_recurse"
            assert e["target"] == "node_recurse"

    def test_forward_reference(self):
        """Callex defined after caller: forward reference still works."""
        code = ("function bar(a: number, b: string): void { foo(a, b); }\n"
                "function foo(x: number, y: string): void {}\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_optional_parameter(self):
        """Call with args maps correctly even when callee has optional param."""
        code = ("function foo(x: number, y?: string): void {}\n"
                "function bar(a: number): void { foo(a); }\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 1
        e = edges[0]
        assert e["target_text"] == "arg:0->param:x"


# ===================================================================
# Negative / no-edge tests
# ===================================================================

class TestDataFlowExtractorNoEdges:
    """Scenarios where no DATA_FLOWS edges should be produced."""

    def test_external_call_no_edge_python(self):
        """Call to builtin/external function -> 0 DATA_FLOWS edges."""
        code = "def bar():\n    len([1, 2, 3])\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0

    def test_external_call_no_edge_typescript(self):
        """Call to external function -> 0 DATA_FLOWS edges."""
        code = ("function bar(): void { console.log(\"hi\"); }\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 0

    def test_empty_function_no_edges_python(self):
        """Empty function body -> 0 DATA_FLOWS edges."""
        code = "def foo(x, y):\n    pass\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0

    def test_empty_function_no_edges_typescript(self):
        """Empty function body TS -> 0 DATA_FLOWS edges."""
        code = "function foo(x: number): void {}\n"
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 0

    def test_no_arg_call_no_edges_python(self):
        """Call with 0 arguments -> 0 DATA_FLOWS edges."""
        code = "def foo():\n    pass\n\ndef bar():\n    foo()\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0

    def test_no_arg_call_no_edges_typescript(self):
        """TS call with 0 arguments -> 0 DATA_FLOWS edges."""
        code = ("function foo(): void {}\n"
                "function bar(): void { foo(); }\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) == 0

    def test_only_class_no_function_no_edges(self):
        """File with only class def, no functions -> 0 edges."""
        code = "class Foo:\n    x = 1\n"
        tree, source = _parse_python(code)
        func_node_ids = {}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0

    def test_empty_file_no_edges(self):
        """Empty file -> 0 edges, no error."""
        tree, source = _parse_python("")
        func_node_ids = {}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0

    def test_comment_only_file_no_edges(self):
        """Comment-only file -> 0 edges."""
        code = "# Just a comment\n# Another line\n"
        tree, source = _parse_python(code)
        func_node_ids = {}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 0


# ===================================================================
# Edge-field contract tests
# ===================================================================

class TestDataFlowExtractorEdgeFields:
    """Verify every returned edge obeys the interface contract."""

    def test_edge_has_required_fields(self):
        """Each edge has source, target, kind, target_text, source_loc, provenance."""
        code = "def foo(x):\n    pass\n\ndef bar(a):\n    foo(a)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) >= 1, "Expected at least 1 edge"
        for e in edges:
            assert isinstance(e["source"], str)
            assert len(e["source"]) > 0, "source must not be empty"
            assert isinstance(e["target"], str)
            assert e["kind"] == "data_flows"
            assert isinstance(e["target_text"], str)
            assert e["target_text"].startswith("arg:")
            assert e["provenance"] == "tree-sitter"
            assert isinstance(e["source_loc"], str)
            assert ":" in e["source_loc"]

    def test_provenance_tree_sitter_python(self):
        """All Python DATA_FLOWS edges have provenance='tree-sitter'."""
        code = "def foo(x, y):\n    pass\n\ndef bar(a, b):\n    foo(a, b)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) > 0
        for e in edges:
            assert e["provenance"] == "tree-sitter"

    def test_provenance_tree_sitter_typescript(self):
        """All TS DATA_FLOWS edges have provenance='tree-sitter'."""
        code = ("function foo(x: number, y: string): void {}\n"
                "function bar(a: number, b: string): void { foo(a, b); }\n")
        tree, source = _parse_typescript(code)
        func_node_ids = {"test.ts::foo": "node_foo", "test.ts::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.ts", "typescript")

        assert len(edges) > 0
        for e in edges:
            assert e["provenance"] == "tree-sitter"

    def test_source_loc_format(self):
        """source_loc is 'file_path:line_number'."""
        code = "def foo(x):\n    pass\n\ndef bar(a):\n    foo(a)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        for e in edges:
            assert "test.py:" in e["source_loc"], \
                f"source_loc should contain file_path: {e['source_loc']}"
            parts = e["source_loc"].rsplit(":", 1)
            assert len(parts) == 2
            assert parts[1].isdigit(), \
                f"Line number should be numeric: {e['source_loc']}"

    def test_kind_matches_edgekind_enum_value(self):
        """Edge kind == EdgeKind.DATA_FLOWS.value."""
        from tws_graph.edges.kind import EdgeKind

        code = "def foo(x):\n    pass\n\ndef bar(a):\n    foo(a)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        for e in edges:
            assert e["kind"] == EdgeKind.DATA_FLOWS.value
            assert EdgeKind(e["kind"]) is EdgeKind.DATA_FLOWS


# ===================================================================
# Boundary / edge-case tests
# ===================================================================

class TestDataFlowExtractorBoundary:
    """Boundary and edge-case tests for DataFlowExtractor."""

    def test_source_is_caller_not_callee(self):
        """source field is the calling function's node_id."""
        code = "def foo(x):\n    pass\n\ndef bar(a):\n    foo(a)\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        for e in edges:
            assert e["source"] == "node_bar", \
                f"source should be caller (bar), got {e['source']}"

    def test_call_with_literal_and_constant_args(self):
        """Call with literal/constant values mapped by position."""
        code = "def foo(x, y):\n    pass\n\ndef bar():\n    foo(1, \"hello\")\n"
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_call_with_expression_args(self):
        """Call with expression args (a+1) mapped by position."""
        code = ("def foo(x, y):\n    pass\n\n"
                "def bar(a, b):\n    foo(a + 1, b * 2)\n")
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_multiple_calls_to_same_function(self):
        """Two call sites to same function both produce edges."""
        code = ("def foo(x):\n    pass\n\n"
                "def bar(a, b):\n    foo(a)\n    foo(b)\n")
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) >= 1
        for e in edges:
            assert e["source"] == "node_bar"
            assert e["target"] == "node_foo"

    def test_return_type_does_not_affect_mapping(self):
        """Function return type annotation does not affect arg->param mapping."""
        code = ("def foo(x, y):\n    return x + y\n\n"
                "def bar(a, b):\n    foo(a, b)\n")
        tree, source = _parse_python(code)
        func_node_ids = {"test.py::foo": "node_foo", "test.py::bar": "node_bar"}

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_decorated_function_call(self):
        """Calls to decorated functions still mapped correctly."""
        code = ("def deco(f):\n    return f\n\n"
                "@deco\n"
                "def foo(x):\n    pass\n\n"
                "def bar(a):\n    foo(a)\n")
        tree, source = _parse_python(code)
        func_node_ids = {
            "test.py::deco": "node_deco",
            "test.py::foo": "node_foo",
            "test.py::bar": "node_bar",
        }

        edges = _extract(tree, source, func_node_ids, "test.py", "python")

        # bar calls foo -> 1 DATA_FLOWS edge
        bar_to_foo = [e for e in edges
                      if e["source"] == "node_bar" and e["target"] == "node_foo"]
        assert len(bar_to_foo) == 1
        assert bar_to_foo[0]["target_text"] == "arg:0->param:x"


# ===================================================================
# Java positive tests
# ===================================================================


def _parse_java(code: str):
    """Parse Java source, return (tree, source_bytes)."""
    parser = tree_sitter_language_pack.get_parser("java")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


class TestDataFlowExtractorJava:
    """Positive tests for DataFlowExtractor with Java source."""

    def test_basic_positional_call(self):
        """foo(a, b) calling void foo(int x, int y) -> 2 DATA_FLOWS edges."""
        code = (
            "class Foo {\n"
            "    void foo(int x, int y) { }\n"
            "    void bar(int a, int b) { foo(a, b); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 2, f"Expected 2 edges, got {len(edges)}"
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts
        for e in edges:
            assert e["source"] == "node_bar"
            assert e["target"] == "node_foo"
            assert e["kind"] == "data_flows"
            assert e["provenance"] == "tree-sitter"

    def test_this_method_call(self):
        """this.method(42) calling void method(int x) -> arg:0->param:x."""
        code = (
            "class Foo {\n"
            "    void helper(int x) { }\n"
            "    void caller(int a) { this.helper(a); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::helper": "node_helper",
            "Test.java::Foo::caller": "node_caller",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 1, f"Got {len(edges)} edges"
        e = edges[0]
        assert e["source"] == "node_caller"
        assert e["target"] == "node_helper"
        assert e["target_text"] == "arg:0->param:x"

    def test_this_method_multi_arg(self):
        """this.method(a, b) calling void method(int x, int y) -> 2 edges."""
        code = (
            "class Foo {\n"
            "    void method(int x, int y) { }\n"
            "    void caller(int a, int b) { this.method(a, b); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::method": "node_method",
            "Test.java::Foo::caller": "node_caller",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_recursive_call(self):
        """void recurse(int n) calling recurse(n-1) -> source == target."""
        code = (
            "class Foo {\n"
            "    void recurse(int n) {\n"
            "        if (n > 0) { recurse(n - 1); }\n"
            "    }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {"Test.java::Foo::recurse": "node_recurse"}
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) >= 1
        for e in edges:
            assert e["source"] == "node_recurse"
            assert e["target"] == "node_recurse"

    def test_constructor_call(self):
        """Constructor calling method -> dataflow mapping works."""
        code = (
            "class Foo {\n"
            "    void helper(int x) { }\n"
            "    Foo(int a) { helper(a); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::helper": "node_helper",
            "Test.java::Foo::Foo": "node_ctor",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == "node_ctor"
        assert e["target"] == "node_helper"
        assert e["target_text"] == "arg:0->param:x"

    def test_three_positional_args(self):
        """foo(a, b, c) -> 3 mapped edges."""
        code = (
            "class Foo {\n"
            "    void foo(int x, int y, int z) { }\n"
            "    void bar(int a, int b, int c) { foo(a, b, c); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 3
        target_texts = {e["target_text"] for e in edges}
        assert target_texts == {"arg:0->param:x", "arg:1->param:y", "arg:2->param:z"}

    def test_forward_reference(self):
        """Caller defined before callee still works."""
        code = (
            "class Foo {\n"
            "    void bar(int a, int b) { foo(a, b); }\n"
            "    void foo(int x, int y) { }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts

    def test_external_call_no_edges(self):
        """Call to external method -> 0 DATA_FLOWS edges."""
        code = "class Foo { void bar() { System.out.println(\"hi\"); } }"
        tree, source = _parse_java(code)
        func_node_ids = {"Test.java::Foo::bar": "node_bar"}
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 0

    def test_empty_method_no_edges(self):
        """Empty method -> 0 edges."""
        code = "class Foo { void bar(int x) { } }"
        tree, source = _parse_java(code)
        func_node_ids = {"Test.java::Foo::bar": "node_bar"}
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 0

    def test_no_arg_call_no_edges(self):
        """Call with 0 arguments -> 0 edges."""
        code = (
            "class Foo {\n"
            "    void foo() { }\n"
            "    void bar() { foo(); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 0

    def test_edge_contract(self):
        """Each edge has all required fields."""
        code = (
            "class Foo {\n"
            "    void foo(int x) { }\n"
            "    void bar(int a) { foo(a); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) >= 1
        for e in edges:
            assert isinstance(e["source"], str)
            assert len(e["source"]) > 0
            assert isinstance(e["target"], str)
            assert e["kind"] == "data_flows"
            assert isinstance(e["target_text"], str)
            assert e["target_text"].startswith("arg:")
            assert e["provenance"] == "tree-sitter"
            assert isinstance(e["source_loc"], str)
            assert ":" in e["source_loc"]

    def test_call_with_literal_args(self):
        """Call with literal values mapped by position."""
        code = (
            "class Foo {\n"
            "    void foo(int x, String y) { }\n"
            "    void bar() { foo(1, \"hi\"); }\n"
            "}"
        )
        tree, source = _parse_java(code)
        func_node_ids = {
            "Test.java::Foo::foo": "node_foo",
            "Test.java::Foo::bar": "node_bar",
        }
        edges = _extract(tree, source, func_node_ids, "Test.java", "java")
        assert len(edges) == 2
        target_texts = {e["target_text"] for e in edges}
        assert "arg:0->param:x" in target_texts
        assert "arg:1->param:y" in target_texts
