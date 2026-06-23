"""Tests for lsp/adapters/php.py --- PhpLspAdapter."""

import os
from unittest.mock import patch

import pytest

from tws_graph.lsp.adapters.php import PhpLspAdapter
from tws_graph.lsp.adapters.base import ImportInfo


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh PhpLspAdapter instance for each test."""
    return PhpLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestPhpLanguageProperty:
    """language should return 'php'."""

    def test_language_is_php(self, adapter):
        assert adapter.language == "php"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestPhpGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_intelephense_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["intelephense", "--stdio"]

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

class TestPhpGetFileExtensions:
    """get_file_extensions() -> list[str]"""

    def test_returns_php_extension(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".php"]

    def test_all_start_with_dot(self, adapter):
        exts = adapter.get_file_extensions()
        for ext in exts:
            assert ext.startswith("."), f"Extension '{ext}' should start with dot"


# ============================================================================
# check_availability
# ============================================================================

class TestPhpCheckAvailability:
    """check_availability() -> bool: checks intelephense in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        with patch("shutil.which", return_value="/usr/local/bin/intelephense") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("intelephense")

    def test_returns_false_when_binary_not_found(self, adapter):
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("intelephense")

    def test_returns_bool(self, adapter):
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)


# ============================================================================
# parse_import
# ============================================================================

class TestPhpParseImport:
    """parse_import(import_statement, current_file) -> ImportInfo"""

    def test_use_import(self, adapter):
        """use App\\Services\\OrderService; -> module='App\\Services\\OrderService', symbol='OrderService'"""
        info = adapter.parse_import(
            "use App\\Services\\OrderService;", "/project/src/Main.php"
        )
        assert isinstance(info, ImportInfo)
        assert info.module == "App\\Services\\OrderService"
        assert info.symbol == "OrderService"
        assert info.is_relative is False
        assert info.alias is None

    def test_use_import_with_alias(self, adapter):
        """use App\\Services\\OrderService as OS; -> module='App\\Services\\OrderService', alias='OS'"""
        info = adapter.parse_import(
            "use App\\Services\\OrderService as OS;", "/project/src/Main.php"
        )
        assert info.module == "App\\Services\\OrderService"
        assert info.symbol == "OrderService"
        assert info.alias == "OS"

    def test_require_import(self, adapter):
        """require 'config.php'; -> module='config.php', symbol='config'"""
        info = adapter.parse_import(
            "require 'config.php';", "/project/src/Main.php"
        )
        assert info.module == "config.php"
        assert info.symbol == "config"
        assert info.is_relative is False

    def test_require_once_import(self, adapter):
        """require_once 'vendor/autoload.php'; -> module='vendor/autoload.php'"""
        info = adapter.parse_import(
            "require_once 'vendor/autoload.php';", "/project/src/Main.php"
        )
        assert info.module == "vendor/autoload.php"
        assert info.symbol == "autoload"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestPhpResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_namespace_to_file(self, adapter):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "Main.php")
            with open(current_file, "w") as f:
                f.write("<?php class Main {}")

            target_dir = os.path.join(tmpdir, "App", "Services")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "OrderService.php"), "w") as f:
                f.write("<?php namespace App\\Services; class OrderService {}")

            result = adapter.resolve_module_path(
                "App\\Services\\OrderService", current_file
            )
            assert result is not None
            assert "OrderService.php" in result

    def test_returns_none_for_unresolvable_module(self, adapter):
        result = adapter.resolve_module_path(
            "Nonexistent\\Module", "/project/src/Main.php"
        )
        assert result is None
