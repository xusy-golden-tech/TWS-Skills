"""Tests for lsp/adapters/cpp.py --- CppLspAdapter."""

import os
from unittest.mock import patch

import pytest

from tws_graph.lsp.adapters.cpp import CppLspAdapter
from tws_graph.lsp.adapters.base import ImportInfo


@pytest.fixture
def adapter():
    """Create a fresh CppLspAdapter instance for each test."""
    return CppLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestCppLanguageProperty:
    """language should return 'cpp'."""

    def test_language_is_cpp(self, adapter):
        assert adapter.language == "cpp"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_clangd_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["clangd"]

    def test_accepts_workspace_root(self, adapter):
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

    def test_returns_cpp_extensions(self, adapter):
        exts = adapter.get_file_extensions()
        assert ".cpp" in exts
        assert ".hpp" in exts
        assert ".cc" in exts

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
    """check_availability() -> bool: checks clangd in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        with patch("shutil.which", return_value="/usr/bin/clangd") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("clangd")

    def test_returns_false_when_binary_not_found(self, adapter):
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("clangd")

    def test_returns_bool(self, adapter):
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)


# ============================================================================
# parse_import
# ============================================================================

class TestParseImport:
    """parse_import(import_statement, current_file) -> ImportInfo"""

    def test_system_include(self, adapter):
        """#include <iostream> -> module='iostream', is_relative=False"""
        info = adapter.parse_import("#include <iostream>", "/project/src/main.cpp")
        assert isinstance(info, ImportInfo)
        assert info.module == "iostream"
        assert info.symbol == "iostream"
        assert info.is_relative is False

    def test_local_include(self, adapter):
        """#include "mylib.hpp" -> module='mylib.hpp', is_relative=True"""
        info = adapter.parse_import('#include "mylib.hpp"', "/project/src/main.cpp")
        assert isinstance(info, ImportInfo)
        assert info.module == "mylib.hpp"
        assert info.symbol == "mylib.hpp"
        assert info.is_relative is True

    def test_system_include_vector(self, adapter):
        """#include <vector> -> module='vector', is_relative=False"""
        info = adapter.parse_import("#include <vector>", "/project/src/main.cpp")
        assert info.module == "vector"
        assert info.is_relative is False


# ============================================================================
# resolve_module_path
# ============================================================================

class TestResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_local_header_in_same_dir(self, adapter):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "main.cpp")
            with open(current_file, "w") as f:
                f.write("int main() { return 0; }")

            header_path = os.path.join(tmpdir, "mylib.hpp")
            with open(header_path, "w") as f:
                f.write("int add(int a, int b);")

            result = adapter.resolve_module_path("mylib.hpp", current_file)
            assert result is not None
            assert "mylib.hpp" in result

    def test_returns_none_for_unresolvable_header(self, adapter):
        result = adapter.resolve_module_path("nonexistent.hpp", "/project/src/main.cpp")
        assert result is None
