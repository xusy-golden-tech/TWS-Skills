"""Tests for lsp/adapters/csharp.py --- CSharpLspAdapter."""

import os
from unittest.mock import patch

import pytest

from tws_graph.lsp.adapters.csharp import CSharpLspAdapter
from tws_graph.lsp.adapters.base import ImportInfo


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh CSharpLspAdapter instance for each test."""
    return CSharpLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestCSharpLanguageProperty:
    """language should return 'csharp'."""

    def test_language_is_csharp(self, adapter):
        assert adapter.language == "csharp"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestCSharpGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_omnisharp_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["omnisharp", "--languageserver"]

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

class TestCSharpGetFileExtensions:
    """get_file_extensions() -> list[str]"""

    def test_returns_cs_extension(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".cs"]

    def test_all_start_with_dot(self, adapter):
        exts = adapter.get_file_extensions()
        for ext in exts:
            assert ext.startswith("."), f"Extension '{ext}' should start with dot"


# ============================================================================
# check_availability
# ============================================================================

class TestCSharpCheckAvailability:
    """check_availability() -> bool: checks omnisharp in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        with patch("shutil.which", return_value="/usr/local/bin/omnisharp") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("omnisharp")

    def test_returns_false_when_binary_not_found(self, adapter):
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("omnisharp")

    def test_returns_bool(self, adapter):
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)


# ============================================================================
# parse_import
# ============================================================================

class TestCSharpParseImport:
    """parse_import(import_statement, current_file) -> ImportInfo"""

    def test_simple_using_import(self, adapter):
        """using System; -> module='System', symbol='System'"""
        info = adapter.parse_import("using System;", "/project/src/App.cs")
        assert isinstance(info, ImportInfo)
        assert info.module == "System"
        assert info.symbol == "System"
        assert info.is_relative is False
        assert info.alias is None

    def test_qualified_using_import(self, adapter):
        """using System.Collections.Generic; -> module='System.Collections.Generic', symbol='Generic'"""
        info = adapter.parse_import(
            "using System.Collections.Generic;", "/project/src/App.cs"
        )
        assert info.module == "System.Collections.Generic"
        assert info.symbol == "Generic"

    def test_using_static_import(self, adapter):
        """using static System.Math; -> module='System.Math', symbol='Math'"""
        info = adapter.parse_import(
            "using static System.Math;", "/project/src/Calc.cs"
        )
        assert info.module == "System.Math"
        assert info.symbol == "Math"

    def test_using_without_semicolon(self, adapter):
        """using statement without trailing semicolon still parsed."""
        info = adapter.parse_import(
            "using System.Linq", "/project/src/App.cs"
        )
        assert info.module == "System.Linq"
        assert info.symbol == "Linq"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestCSharpResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_dotted_name_to_file(self, adapter):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "Main.cs")
            with open(current_file, "w") as f:
                f.write("class Main {}")

            target_dir = os.path.join(tmpdir, "Services")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "OrderService.cs"), "w") as f:
                f.write("namespace Services; class OrderService {}")

            result = adapter.resolve_module_path("Services.OrderService", current_file)
            assert result is not None
            assert "OrderService.cs" in result

    def test_returns_none_for_unresolvable_module(self, adapter):
        result = adapter.resolve_module_path(
            "Nonexistent.Module", "/project/src/Main.cs"
        )
        assert result is None
