"""Tests for ParseExtractPass — tree-sitter parsing and symbol extraction."""

import os
import pytest
from pathlib import Path

from tws_graph.pipeline.pass_interface import Pass, PipelineContext


# ============================================================================
# Helpers
# ============================================================================


def _make_pass():
    """Create a ParseExtractPass instance (lazy import)."""
    from tws_graph.pipeline.passes.parse_extract import ParseExtractPass
    return ParseExtractPass()


def _create_file(root_dir: Path, rel_path: str, content: str) -> str:
    """Create a file under root_dir and return its relative path."""
    full_path = root_dir / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return rel_path


VALID_PYTHON_SRC = """
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

class Calculator:
    def compute(self, x, y):
        s = add(x, y)
        p = multiply(x, y)
        return s + p
"""

VALID_PYTHON_WITH_CALLS = """
def helper(data):
    return len(data)

def process(items):
    result = helper(items)
    return result
"""


# ============================================================================
# Basic behavior
# ============================================================================


class TestParseExtractBasic:
    """Basic ParseExtractPass behavior."""

    def test_parses_python_file(self, tmp_path: Path):
        """Parse a valid Python file and extract symbols."""
        _create_file(tmp_path, "test.py", VALID_PYTHON_SRC)

        ctx = PipelineContext(
            files=["test.py"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)

        assert "test.py" in result.parsed_results
        parsed = result.parsed_results["test.py"]
        assert "defs" in parsed
        assert "calls" in parsed
        assert "refs" in parsed
        assert len(parsed["defs"]) >= 3  # add, multiply, Calculator, compute

    def test_extracts_defs_correctly(self, tmp_path: Path):
        """Verify defs contain expected function and class definitions."""
        _create_file(tmp_path, "test.py", VALID_PYTHON_SRC)

        ctx = PipelineContext(
            files=["test.py"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)

        parsed = result.parsed_results["test.py"]
        defs = parsed["defs"]

        # Check for expected definitions
        names = [d["name"] for d in defs]
        assert "add" in names
        assert "multiply" in names
        assert "Calculator" in names
        assert "compute" in names

        # Verify def structure has required fields
        for d in defs:
            assert "id" in d
            assert "kind" in d
            assert "name" in d
            assert "qualified_name" in d
            assert "file_path" in d

    def test_extracts_calls_correctly(self, tmp_path: Path):
        """Verify calls are extracted with proper kind='calls'."""
        _create_file(tmp_path, "test.py", VALID_PYTHON_WITH_CALLS)

        ctx = PipelineContext(
            files=["test.py"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)

        parsed = result.parsed_results["test.py"]
        calls = parsed["calls"]

        # At least one call edge should exist (helper called from process)
        assert len(calls) >= 1
        call_kinds = {c["kind"] for c in calls}
        assert call_kinds == {"calls"} or "calls" in call_kinds

    def test_empty_files_noop(self, tmp_path: Path):
        """Empty files list should return empty results."""
        ctx = PipelineContext(
            files=[],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)
        assert result.parsed_results == {}


# ============================================================================
# Error handling
# ============================================================================


class TestParseExtractErrors:
    """Error handling in ParseExtractPass."""

    def test_unsupported_language_skipped(self, tmp_path: Path):
        """Files with unsupported language (unknown extension) are skipped."""
        _create_file(tmp_path, "config.xyz", "some unknown content")

        ctx = PipelineContext(
            files=["config.xyz"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)
        # Unsupported language should not crash; parsed_results may be empty
        # or contain an entry with empty defs/errors
        assert isinstance(result.parsed_results, dict)

    def test_file_read_error_does_not_crash(self, tmp_path: Path):
        """File that cannot be read should not crash the pass."""
        ctx = PipelineContext(
            files=["nonexistent.py"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        # Should not raise
        result = pas.run(ctx)
        assert isinstance(result.parsed_results, dict)

    def test_multiple_files_mixed_results(self, tmp_path: Path):
        """Mix of valid and missing files — valid ones still parsed."""
        _create_file(tmp_path, "good.py", VALID_PYTHON_SRC)

        ctx = PipelineContext(
            files=["good.py", "missing.py"],
            root_dir=str(tmp_path),
        )

        pas = _make_pass()
        result = pas.run(ctx)

        # good.py should be parsed
        assert "good.py" in result.parsed_results
        assert len(result.parsed_results["good.py"]["defs"]) >= 3


# ============================================================================
# Pass interface compliance
# ============================================================================


class TestParseExtractInterface:
    """Verify ParseExtractPass conforms to Pass ABC."""

    def test_is_pass_subclass(self):
        pas = _make_pass()
        assert isinstance(pas, Pass)

    def test_name_and_description(self):
        pas = _make_pass()
        assert pas.name == "parse-extract"
        assert len(pas.description) > 0

    def test_dependencies(self):
        pas = _make_pass()
        assert pas.dependencies == ["stat-filter"]

    def test_supports_incremental(self):
        pas = _make_pass()
        assert pas.supports_incremental is True

    def test_enabled_default(self):
        pas = _make_pass()
        assert pas.enabled(PipelineContext()) is True

    def test_run_returns_pipeline_context(self, tmp_path: Path):
        _create_file(tmp_path, "a.py", "x = 1")
        ctx = PipelineContext(
            files=["a.py"],
            root_dir=str(tmp_path),
        )
        pas = _make_pass()
        result = pas.run(ctx)
        assert isinstance(result, PipelineContext)
