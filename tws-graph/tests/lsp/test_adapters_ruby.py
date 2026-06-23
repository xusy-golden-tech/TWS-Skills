"""Tests for lsp/adapters/ruby.py --- RubyLspAdapter."""

import os
from unittest.mock import patch

import pytest

from tws_graph.lsp.adapters.ruby import RubyLspAdapter
from tws_graph.lsp.adapters.base import ImportInfo


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh RubyLspAdapter instance for each test."""
    return RubyLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestRubyLanguageProperty:
    """language should return 'ruby'."""

    def test_language_is_ruby(self, adapter):
        assert adapter.language == "ruby"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestRubyGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_solargraph_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["solargraph", "stdio"]

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

class TestRubyGetFileExtensions:
    """get_file_extensions() -> list[str]"""

    def test_returns_rb_extension(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".rb"]

    def test_all_start_with_dot(self, adapter):
        exts = adapter.get_file_extensions()
        for ext in exts:
            assert ext.startswith("."), f"Extension '{ext}' should start with dot"


# ============================================================================
# check_availability
# ============================================================================

class TestRubyCheckAvailability:
    """check_availability() -> bool: checks solargraph in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        with patch("shutil.which", return_value="/usr/local/bin/solargraph") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("solargraph")

    def test_returns_false_when_binary_not_found(self, adapter):
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("solargraph")

    def test_returns_bool(self, adapter):
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)


# ============================================================================
# parse_import
# ============================================================================

class TestRubyParseImport:
    """parse_import(import_statement, current_file) -> ImportInfo"""

    def test_require_import(self, adapter):
        """require 'json' -> module='json', symbol='json'"""
        info = adapter.parse_import(
            "require 'json'", "/project/src/main.rb"
        )
        assert isinstance(info, ImportInfo)
        assert info.module == "json"
        assert info.symbol == "json"
        assert info.is_relative is False
        assert info.alias is None

    def test_require_relative_import(self, adapter):
        """require_relative 'helpers' -> module='helpers', symbol='helpers', is_relative=True"""
        info = adapter.parse_import(
            "require_relative 'helpers'", "/project/src/main.rb"
        )
        assert info.module == "helpers"
        assert info.symbol == "helpers"
        assert info.is_relative is True
        assert info.alias is None

    def test_load_import(self, adapter):
        """load 'config.rb' -> module='config.rb', symbol='config'"""
        info = adapter.parse_import(
            "load 'config.rb'", "/project/src/main.rb"
        )
        assert info.module == "config.rb"
        assert info.symbol == "config"
        assert info.is_relative is False

    def test_require_with_double_quotes(self, adapter):
        """require \"json\" -> module='json'"""
        info = adapter.parse_import(
            'require "json"', "/project/src/main.rb"
        )
        assert info.module == "json"
        assert info.symbol == "json"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestRubyResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_module_to_file(self, adapter):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "main.rb")
            with open(current_file, "w") as f:
                f.write("class Main; end")

            with open(os.path.join(tmpdir, "helpers.rb"), "w") as f:
                f.write("module Helpers; end")

            result = adapter.resolve_module_path("helpers", current_file)
            assert result is not None
            assert "helpers.rb" in result

    def test_returns_none_for_unresolvable_module(self, adapter):
        result = adapter.resolve_module_path(
            "nonexistent", "/project/src/main.rb"
        )
        assert result is None
