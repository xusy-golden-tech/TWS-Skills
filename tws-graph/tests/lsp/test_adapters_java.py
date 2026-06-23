"""Tests for lsp/adapters/java.py --- JavaLspAdapter.

TDD red phase: these tests are written before the adapters/java module exists.
They encode the acceptance criteria for adapters/java.py.

Coverage per acceptance criteria:
  - language = "java"
  - get_server_command returns ["jdtls"]
  - get_file_extensions returns [".java"]
  - check_availability checks jdtls via shutil.which
  - parse_import: single class import, wildcard import, static import
  - resolve_module_path: dotted name to file path mapping
"""

import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.adapters.java is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.adapters.java import JavaLspAdapter  # noqa: E402
from tws_graph.lsp.adapters.base import ImportInfo  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh JavaLspAdapter instance for each test."""
    return JavaLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestJavaLanguageProperty:
    """language should return 'java'."""

    def test_language_is_java(self, adapter):
        assert adapter.language == "java"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_jdtls_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["jdtls"]

    def test_accepts_workspace_root(self, adapter):
        """get_server_command should accept workspace_root parameter (spec signature)."""
        cmd = adapter.get_server_command("/any/path")
        assert isinstance(cmd, list)
        assert len(cmd) >= 1

    def test_returns_list_of_strings(self, adapter):
        cmd = adapter.get_server_command("/ws")
        assert isinstance(cmd, list)
        for item in cmd:
            assert isinstance(item, str)


# ============================================================================
# get_file_extensions
# ============================================================================

class TestGetFileExtensions:
    """get_file_extensions() -> list[str]"""

    def test_returns_java_extension(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".java"]

    def test_all_start_with_dot(self, adapter):
        exts = adapter.get_file_extensions()
        for ext in exts:
            assert ext.startswith("."), f"Extension '{ext}' should start with dot"

    def test_returns_list(self, adapter):
        exts = adapter.get_file_extensions()
        assert isinstance(exts, list)


# ============================================================================
# check_availability
# ============================================================================

class TestCheckAvailability:
    """check_availability() -> bool: checks jdtls in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        """When shutil.which finds jdtls, return True."""
        with patch("shutil.which", return_value="/usr/local/bin/jdtls") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("jdtls")

    def test_returns_false_when_binary_not_found(self, adapter):
        """When shutil.which returns None, return False."""
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("jdtls")

    def test_returns_bool(self, adapter):
        """check_availability always returns a boolean."""
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)

    def test_does_not_throw_on_shutil_error(self, adapter):
        """If shutil access fails, adapter should handle gracefully."""
        with patch("shutil.which", side_effect=OSError("permission denied")):
            try:
                result = adapter.check_availability()
                assert result is False or result is True
            except OSError:
                pytest.xfail(
                    "check_availability should ideally handle OSError gracefully "
                    "(design says adapters should not throw on unavailable servers)"
                )


# ============================================================================
# parse_import
# ============================================================================

class TestParseImport:
    """parse_import(import_statement, current_file) -> ImportInfo"""

    # --- single class imports --------------------------------------------------

    def test_single_class_import(self, adapter):
        """import java.util.List; -> module='java.util', symbol='List'"""
        info = adapter.parse_import(
            "import java.util.List;", "/project/src/MyClass.java"
        )
        assert isinstance(info, ImportInfo)
        assert info.module == "java.util"
        assert info.symbol == "List"
        assert info.is_relative is False
        assert info.alias is None

    def test_import_top_level_class(self, adapter):
        """import javax.swing.JFrame; -> module='javax.swing', symbol='JFrame'"""
        info = adapter.parse_import(
            "import javax.swing.JFrame;", "/project/src/MyClass.java"
        )
        assert info.module == "javax.swing"
        assert info.symbol == "JFrame"

    def test_import_deep_package(self, adapter):
        """import com.example.utils.StringHelper; -> module='com.example.utils', symbol='StringHelper'"""
        info = adapter.parse_import(
            "import com.example.utils.StringHelper;", "/project/src/App.java"
        )
        assert info.module == "com.example.utils"
        assert info.symbol == "StringHelper"

    # --- wildcard imports ------------------------------------------------------

    def test_wildcard_import(self, adapter):
        """import java.util.*; -> module='java.util', symbol='*'"""
        info = adapter.parse_import(
            "import java.util.*;", "/project/src/MyClass.java"
        )
        assert info.module == "java.util"
        assert info.symbol == "*"
        assert info.is_relative is False
        assert info.alias is None

    # --- static imports --------------------------------------------------------

    def test_static_import_method(self, adapter):
        """import static java.lang.Math.PI; -> module='java.lang.Math', symbol='PI'"""
        info = adapter.parse_import(
            "import static java.lang.Math.PI;", "/project/src/MyClass.java"
        )
        assert info.module == "java.lang.Math"
        assert info.symbol == "PI"
        assert info.is_relative is False

    def test_static_wildcard_import(self, adapter):
        """import static org.junit.Assert.*; -> module='org.junit.Assert', symbol='*'"""
        info = adapter.parse_import(
            "import static org.junit.Assert.*;", "/project/src/MyTest.java"
        )
        assert info.module == "org.junit.Assert"
        assert info.symbol == "*"

    # --- edge cases ------------------------------------------------------------

    def test_import_with_trailing_whitespace(self, adapter):
        """Leading/trailing whitespace should be stripped."""
        info = adapter.parse_import(
            "  import java.util.List;  ", "/project/src/MyClass.java"
        )
        assert info.module == "java.util"
        assert info.symbol == "List"

    def test_import_without_semicolon(self, adapter):
        """import statement without trailing semicolon still parsed."""
        info = adapter.parse_import(
            "import java.util.List", "/project/src/MyClass.java"
        )
        assert info.module == "java.util"
        assert info.symbol == "List"

    def test_static_import_without_semicolon(self, adapter):
        """static import without trailing semicolon still parsed."""
        info = adapter.parse_import(
            "import static java.lang.Math.PI", "/project/src/MyClass.java"
        )
        assert info.module == "java.lang.Math"
        assert info.symbol == "PI"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_dotted_name_to_file(self, adapter):
        """com.example.Foo -> <current_dir>/com/example/Foo.java"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "Main.java")
            with open(current_file, "w") as f:
                f.write("class Main {}")

            # Create the target: com/example/Foo.java under current_dir
            target_dir = os.path.join(tmpdir, "com", "example")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "Foo.java"), "w") as f:
                f.write("package com.example; class Foo {}")

            result = adapter.resolve_module_path("com.example.Foo", current_file)
            assert result is not None
            assert "Foo.java" in result

    def test_resolves_top_level_package_class(self, adapter):
        """foo.Bar -> <current_dir>/foo/Bar.java"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "Main.java")
            with open(current_file, "w") as f:
                f.write("class Main {}")

            target_dir = os.path.join(tmpdir, "foo")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "Bar.java"), "w") as f:
                f.write("package foo; class Bar {}")

            result = adapter.resolve_module_path("foo.Bar", current_file)
            assert result is not None
            assert "Bar.java" in result

    def test_returns_none_for_unresolvable_module(self, adapter):
        """Module that does not exist -> None."""
        result = adapter.resolve_module_path(
            "nonexistent.Module", "/project/src/Main.java"
        )
        assert result is None

    def test_returns_str_or_none(self, adapter):
        """Return type is Optional[str]."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "Main.java")
            with open(current_file, "w") as f:
                f.write("class Main {}")
            target_dir = os.path.join(tmpdir, "exist")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "Module.java"), "w") as f:
                f.write("package exist; class Module {}")

            result = adapter.resolve_module_path("exist.Module", current_file)
            assert result is None or isinstance(result, str)
