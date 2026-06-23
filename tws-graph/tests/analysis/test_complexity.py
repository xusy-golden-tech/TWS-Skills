"""Tests for analysis/complexity.py — ComplexityAnalyzer."""
import math
import pytest
from tws_graph.analysis.complexity import ComplexityAnalyzer, ComplexityMetrics


def _make_node(
    node_id="test::func",
    qname="test.py::func",
    file_path="test.py",
    language="python",
    start_line=1,
    end_line=10,
    kind="function",
) -> dict:
    """Helper to create a minimal NodeRecord-like dict."""
    return {
        "id": node_id,
        "qualified_name": qname,
        "file_path": file_path,
        "language": language,
        "start_line": start_line,
        "end_line": end_line,
        "kind": kind,
        "name": qname.split("::")[-1] if "::" in qname else qname,
    }


class TestCyclomaticComplexity:
    """Cyclomatic complexity = branch_count + 1."""

    def test_empty_function(self):
        analyzer = ComplexityAnalyzer()
        node = _make_node()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(node, body)
        assert m.cyclomatic == 1

    def test_single_if(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    if x:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 if

    def test_if_else(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    if x:\n        pass\n    else:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 if (else is not a new branch)

    def test_if_elif_else(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "def foo(x):\n"
            "    if x == 0:\n        pass\n"
            "    elif x == 1:\n        pass\n"
            "    elif x == 2:\n        pass\n"
            "    else:\n        pass\n"
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 4  # 1 base + 3 decision points (if + 2 elifs)

    def test_for_loop(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(items):\n    for i in items:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 for

    def test_while_loop(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    while x > 0:\n        x -= 1\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 while

    def test_nested_branches(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x, y):\n    if x:\n        for i in y:\n            if i > 0:\n                pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 4  # 1 base + 1 if + 1 for + 1 inner if

    def test_and_operator(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(a, b):\n    return a and b\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 AND

    def test_or_operator(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(a, b):\n    return a or b\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 OR

    def test_combined_and_or(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(a, b, c):\n    return a and b or c\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 3  # 1 base + 2 logical operators

    def test_try_except(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    try:\n        pass\n    except ValueError:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 except

    def test_try_multiple_except(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "def foo():\n"
            "    try:\n        pass\n"
            "    except ValueError:\n        pass\n"
            "    except TypeError:\n        pass\n"
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 3  # 1 base + 2 excepts

    def test_python_ternary(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    return 1 if x else 0\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 2  # 1 base + 1 ternary

    def test_typescript_if(self):
        analyzer = ComplexityAnalyzer()
        body = "function foo(x: number): void {\n    if (x) { return; }\n}\n"
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cyclomatic == 2

    def test_typescript_switch(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "function foo(x: number): string {\n"
            "    switch (x) {\n"
            "        case 1: return 'a';\n"
            "        case 2: return 'b';\n"
            "        default: return 'c';\n"
            "    }\n"
            "}\n"
        )
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cyclomatic == 3  # 1 base + 2 cases (case 1, case 2; default is not a new branch)

    def test_typescript_ternary(self):
        analyzer = ComplexityAnalyzer()
        body = "function foo(x: number): number {\n    return x ? 1 : 0;\n}\n"
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cyclomatic == 2  # 1 base + 1 ternary


class TestCognitiveComplexity:
    """Cognitive complexity with nesting penalties (SonarSource simplified)."""

    def test_empty_function(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cognitive == 0

    def test_single_if(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    if x:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cognitive == 1  # base 1, nesting level 0

    def test_nested_if(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x, y):\n    if x:\n        if y:\n            pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Outer if: 1 + 0 = 1
        # Inner if: 1 + 1 = 2
        assert m.cognitive == 3

    def test_deeply_nested_if(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "def foo(a, b, c, d):\n"
            "    if a:\n"
            "        if b:\n"
            "            if c:\n"
            "                if d:\n"
            "                    pass\n"
        )
        m = analyzer.analyze_node(_make_node(), body)
        # Level 0: 1, Level 1: 2, Level 2: 3, Level 3: 4
        assert m.cognitive == 1 + 2 + 3 + 4  # = 10

    def test_sequential_if(self):
        """Sequential ifs at same level reset nesting."""
        analyzer = ComplexityAnalyzer()
        body = "def foo(a, b):\n    if a:\n        pass\n    if b:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        # First if: 1 + 0 = 1
        # Second if: 1 + 0 = 1
        assert m.cognitive == 2

    def test_nested_for_in_if(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x, items):\n    if x:\n        for i in items:\n            pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Outer if: 1 + 0 = 1
        # Inner for: 1 + 1 = 2
        assert m.cognitive == 3

    def test_for_loop(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(items):\n    for i in items:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cognitive == 1

    def test_while_loop(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo(x):\n    while x > 0:\n        x -= 1\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cognitive == 1

    def test_except_increment(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    try:\n        pass\n    except ValueError:\n        pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        # try/except counts as a control structure
        assert m.cognitive >= 1

    def test_logical_operators_cognitive(self):
        """and/or operators increment cognitive complexity."""
        analyzer = ComplexityAnalyzer()
        body = "def foo(a, b):\n    return a and b\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cognitive == 1  # 1 AND

    def test_recursive_call(self):
        """Recursive call adds +1 to cognitive complexity."""
        analyzer = ComplexityAnalyzer()
        body = "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Has if-structure (1) + recursive call (1) = 2
        assert m.cognitive >= 2

    def test_typescript_nested(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "function foo(a: number, b: number): void {\n"
            "    if (a) {\n"
            "        if (b) {\n"
            "            console.log('both');\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cognitive == 3  # 1 + 2

    def test_typescript_catch(self):
        analyzer = ComplexityAnalyzer()
        body = (
            "function foo(): void {\n"
            "    try { doSomething(); }\n"
            "    catch (e) { console.log(e); }\n"
            "}\n"
        )
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cognitive >= 1

    def test_typescript_switch_cognitive(self):
        """Switch with cases should count each case."""
        analyzer = ComplexityAnalyzer()
        body = (
            "function foo(x: number): string {\n"
            "    switch (x) {\n"
            "        case 1: return 'a';\n"
            "        case 2: return 'b';\n"
            "        default: return 'c';\n"
            "    }\n"
            "}\n"
        )
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cognitive >= 2  # At least 2 case labels


class TestHalsteadMetrics:
    """Halstead metrics: volume, difficulty, effort (heuristic approximation)."""

    def test_empty_function(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.halstead_volume >= 0
        assert m.halstead_difficulty >= 0
        # Empty function should have very low volume
        assert m.halstead_volume < 20

    def test_simple_function(self):
        analyzer = ComplexityAnalyzer()
        body = "def add(a, b):\n    return a + b\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Should have operators (def, return, +) and operands (a, b)
        assert m.halstead_volume > 0
        assert m.halstead_difficulty > 0
        assert m.halstead_effort > 0

    def test_effort_relation(self):
        """Effort = Volume * Difficulty."""
        analyzer = ComplexityAnalyzer()
        body = "def add(a, b):\n    return a + b\n"
        m = analyzer.analyze_node(_make_node(), body)
        expected_effort = m.halstead_volume * m.halstead_difficulty
        assert math.isclose(m.halstead_effort, expected_effort, rel_tol=1e-9)

    def test_complex_function_higher_volume(self):
        """A more complex function should have higher volume."""
        analyzer = ComplexityAnalyzer()
        simple = "def add(a, b):\n    return a + b\n"
        complex_body = (
            "def process(data, threshold):\n"
            "    result = []\n"
            "    for item in data:\n"
            "        if item > threshold:\n"
            "            result.append(item * 2 + 1)\n"
            "    return result\n"
        )
        m_simple = analyzer.analyze_node(_make_node(node_id="s", qname="s"), simple)
        m_complex = analyzer.analyze_node(_make_node(node_id="c", qname="c"), complex_body)
        assert m_complex.halstead_volume > m_simple.halstead_volume

    def test_typescript_function(self):
        analyzer = ComplexityAnalyzer()
        body = "function add(a: number, b: number): number {\n    return a + b;\n}\n"
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.halstead_volume > 0
        assert m.halstead_effort > 0


class TestRiskLevel:
    """Risk level classification based on cyclomatic and cognitive complexity."""

    def test_low_risk(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "low"

    def test_medium_risk_by_cyclomatic(self):
        analyzer = ComplexityAnalyzer()
        # cyclomatic = 7 (1 base + 6 ifs)
        body = (
            "def foo(x):\n"
            + "".join(f"    if x == {i}:\n        pass\n" for i in range(6))
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "medium"

    def test_high_risk_by_cyclomatic(self):
        analyzer = ComplexityAnalyzer()
        # cyclomatic = 12 (1 base + 11 ifs)
        body = (
            "def foo(x):\n"
            + "".join(f"    if x == {i}:\n        pass\n" for i in range(11))
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "high"

    def test_critical_risk_by_cyclomatic(self):
        analyzer = ComplexityAnalyzer()
        # cyclomatic = 22 (1 base + 21 ifs)
        body = (
            "def foo(x):\n"
            + "".join(f"    if x == {i}:\n        pass\n" for i in range(21))
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "critical"

    def test_medium_risk_by_cognitive(self):
        analyzer = ComplexityAnalyzer()
        # cognitive = 7 (6 sequential ifs + 1 for)
        body = "\n".join(
            [f"def foo(x):"]
            + [f"    if x == {i}:\n        pass" for i in range(6)]
            + [f"    for i in range(1):\n        pass"]
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "medium"

    def test_high_risk_by_cognitive(self):
        analyzer = ComplexityAnalyzer()
        # cognitive = 18 (15 sequential ifs + 1 for + 1 while + 1 except)
        body = "\n".join(
            [f"def foo(x):"]
            + [f"    if x == {i}:\n        pass" for i in range(15)]
            + [
                "    for i in range(1):\n        pass",
                "    while False:\n        pass",
                "    try:\n        pass\n    except:\n        pass",
            ]
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "high"

    def test_critical_risk_by_cognitive(self):
        analyzer = ComplexityAnalyzer()
        # cognitive ~= 35 (more than 31 sequential ifs + structural)
        body = "\n".join(
            [f"def foo(x):"]
            + [f"    if x == {i}:\n        pass" for i in range(32)]
            + [
                "    for i in range(1):\n        pass",
                "    while False:\n        pass",
                "    try:\n        pass\n    except:\n        pass",
            ]
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "critical"

    def test_risk_takes_higher_of_two(self):
        """Risk level should be the higher of cyclomatic and cognitive classifications."""
        analyzer = ComplexityAnalyzer()
        # cyclomatic = 3 (low) but cognitive = 7 (medium) → medium
        body = "\n".join(
            ["def foo(x):"]
            + [f"    if x == {i}:\n        pass" for i in range(2)]
            + [
                "    for i in range(1):\n        pass",
                "    while False:\n        pass",
                "    try:\n        pass\n    except:\n        pass",
                "    return True and False and True and False and True",
            ]
        )
        m = analyzer.analyze_node(_make_node(), body)
        assert m.risk_level == "medium"


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_function_body(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Should not crash
        assert m.cyclomatic == 1
        assert m.cognitive == 0
        assert m.lines_of_code == 2

    def test_syntax_error(self):
        """Unparseable code should return a sentinel with an error logged."""
        analyzer = ComplexityAnalyzer()
        body = "def foo(:\n    this is not valid python!!!\n"
        m = analyzer.analyze_node(_make_node(), body)
        # Should still return a result, not crash
        assert m.cyclomatic >= 0
        assert isinstance(m.risk_level, str)

    def test_only_pass_function(self):
        analyzer = ComplexityAnalyzer()
        body = "def nothing():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.cyclomatic == 1
        assert m.cognitive == 0
        assert m.risk_level == "low"

    def test_method_in_class(self):
        analyzer = ComplexityAnalyzer()
        body = "def process(self, data):\n    if data:\n        return True\n    return False\n"
        m = analyzer.analyze_node(_make_node(kind="method"), body)
        assert m.cyclomatic == 2

    def test_typescript_empty_function(self):
        analyzer = ComplexityAnalyzer()
        body = "function empty(): void {}\n"
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert m.cyclomatic == 1
        assert m.cognitive == 0

    def test_typescript_syntax_error(self):
        analyzer = ComplexityAnalyzer()
        body = "function broken( { this is not valid!!! }\n"
        m = analyzer.analyze_node(_make_node(language="typescript"), body)
        assert isinstance(m.risk_level, str)

    def test_metrics_has_all_fields(self):
        analyzer = ComplexityAnalyzer()
        m = analyzer.analyze_node(_make_node(), "def foo():\n    pass\n")
        assert m.node_id == "test::func"
        assert m.qualified_name == "test.py::func"
        assert m.file_path == "test.py"
        assert m.language == "python"
        assert isinstance(m.cyclomatic, int)
        assert isinstance(m.cognitive, int)
        assert isinstance(m.halstead_volume, float)
        assert isinstance(m.halstead_difficulty, float)
        assert isinstance(m.halstead_effort, float)
        assert isinstance(m.lines_of_code, int)
        assert m.risk_level in ("low", "medium", "high", "critical")

    def test_lines_of_code_count(self):
        analyzer = ComplexityAnalyzer()
        body = "def foo():\n    pass\n"
        m = analyzer.analyze_node(_make_node(), body)
        assert m.lines_of_code == 2

        body2 = "def bar():\n    x = 1\n    y = 2\n    return x + y\n"
        m2 = analyzer.analyze_node(_make_node(), body2)
        assert m2.lines_of_code == 4


class TestAnalyze:
    """Integration tests for the analyze() method using a Mock Store."""

    def test_analyze_with_empty_store(self):
        """Analyze with a store that has no files should return empty list."""
        analyzer = ComplexityAnalyzer()
        from unittest.mock import MagicMock
        mock_store = MagicMock()
        mock_store.get_all_files.return_value = []
        mock_store.iter_all_nodes.return_value = iter([])
        results = analyzer.analyze(mock_store)
        assert results == []

    def test_analyze_unsupported_language_skipped(self):
        """Files with unsupported languages should be skipped."""
        analyzer = ComplexityAnalyzer()
        from unittest.mock import MagicMock
        mock_store = MagicMock()
        mock_store.get_all_files.return_value = [
            {"path": "test.java", "language": "java", "content_hash": "abc"}
        ]
        mock_store.iter_all_nodes.return_value = iter([])
        results = analyzer.analyze(mock_store)
        assert results == []
        # Java is not supported

    def test_analyze_non_existent_file_skipped(self):
        """File path that doesn't exist on disk should be skipped."""
        analyzer = ComplexityAnalyzer()
        from unittest.mock import MagicMock
        mock_store = MagicMock()
        mock_store.get_all_files.return_value = [
            {"path": "nonexistent.py", "language": "python", "content_hash": "abc"}
        ]
        mock_store.iter_all_nodes.return_value = iter([])
        results = analyzer.analyze(mock_store)
        assert results == []
