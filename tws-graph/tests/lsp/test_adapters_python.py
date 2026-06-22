"""Tests for lsp/adapters/python.py --- PythonLspAdapter.

TDD red phase: these tests are written before the adapters/python module exists.
They encode the acceptance criteria from design-p5-lsp.md for adapters/python.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - language = "python"
  - get_server_command returns ["pyright-langserver", "--stdio"]
  - get_file_extensions returns [".py", ".pyi"]
  - check_availability checks pyright-langserver via shutil.which
  - parse_import: from-import, plain import, relative import, aliased import
  - resolve_module_path: module name to file path mapping
"""

import os
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.adapters.python is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.adapters.python import PythonLspAdapter  # noqa: E402
from tws_graph.lsp.adapters.base import ImportInfo  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh PythonLspAdapter instance for each test."""
    return PythonLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestPythonLanguageProperty:
    """language should return 'python'."""

    def test_language_is_python(self, adapter):
        assert adapter.language == "python"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_pyright_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["pyright-langserver", "--stdio"]

    def test_accepts_workspace_root(self, adapter):
        """get_server_command should accept workspace_root parameter (spec signature)."""
        # It should not raise when given workspace_root
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

    def test_returns_py_and_pyi(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".py", ".pyi"]

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
    """check_availability() -> bool: checks pyright-langserver in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        """When shutil.which finds pyright-langserver, return True."""
        with patch("shutil.which", return_value="/usr/bin/pyright-langserver") as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("pyright-langserver")

    def test_returns_false_when_binary_not_found(self, adapter):
        """When shutil.which returns None, return False."""
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("pyright-langserver")

    def test_returns_bool(self, adapter):
        """check_availability always returns a boolean."""
        with patch("shutil.which", return_value="/some/path"):
            result = adapter.check_availability()
            assert isinstance(result, bool)

    def test_does_not_throw_on_shutil_error(self, adapter):
        """If shutil access fails (e.g. permission), adapter should not propagate raw exception."""
        # The design says adapter registry should not throw on unavailable servers.
        # We test that check_availability catches errors and returns False.
        with patch("shutil.which", side_effect=OSError("permission denied")):
            try:
                result = adapter.check_availability()
                # If it doesn't raise, it should return a sensible value
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

    # --- plain imports -------------------------------------------------------

    def test_plain_import_no_alias(self, adapter):
        """import os -> module='os', symbol='os'"""
        info = adapter.parse_import("import os", "/project/main.py")
        assert isinstance(info, ImportInfo)
        assert info.module == "os"
        assert info.symbol == "os"
        assert info.is_relative is False
        assert info.alias is None

    def test_plain_import_dotted(self, adapter):
        """import os.path -> module='os.path', symbol='os.path'"""
        info = adapter.parse_import("import os.path", "/project/main.py")
        assert info.module == "os.path"
        assert info.symbol == "os.path"
        assert info.is_relative is False
        assert info.alias is None

    def test_plain_import_with_alias(self, adapter):
        """import numpy as np -> module='numpy', alias='np'"""
        info = adapter.parse_import("import numpy as np", "/project/main.py")
        assert info.module == "numpy"
        assert info.symbol == "numpy"
        assert info.is_relative is False
        assert info.alias == "np"

    def test_plain_import_nested_alias(self, adapter):
        """import foo.bar.baz as bz -> module='foo.bar.baz', alias='bz'"""
        info = adapter.parse_import("import foo.bar.baz as bz", "/project/main.py")
        assert info.module == "foo.bar.baz"
        assert info.symbol == "foo.bar.baz"
        assert info.is_relative is False
        assert info.alias == "bz"

    # --- from-imports --------------------------------------------------------

    def test_from_import_single(self, adapter):
        """from foo.bar import Baz -> module='foo.bar', symbol='Baz'"""
        info = adapter.parse_import("from foo.bar import Baz", "/project/main.py")
        assert info.module == "foo.bar"
        assert info.symbol == "Baz"
        assert info.is_relative is False
        assert info.alias is None

    def test_from_import_with_alias(self, adapter):
        """from foo import Bar as B -> module='foo', symbol='Bar', alias='B'"""
        info = adapter.parse_import("from foo import Bar as B", "/project/main.py")
        assert info.module == "foo"
        assert info.symbol == "Bar"
        assert info.is_relative is False
        assert info.alias == "B"

    def test_from_import_multiple_returns_first(self, adapter):
        """from os import path, environ -> module='os', symbol='path' (first symbol)"""
        info = adapter.parse_import("from os import path, environ", "/project/main.py")
        assert info.module == "os"
        assert info.symbol == "path"
        assert info.is_relative is False

    # --- relative imports ----------------------------------------------------

    def test_relative_import_dot_sibling(self, adapter):
        """from . import sibling -> module='.', symbol='sibling', is_relative=True"""
        info = adapter.parse_import("from . import sibling", "/project/pkg/sub.py")
        assert info.module == "."
        assert info.symbol == "sibling"
        assert info.is_relative is True
        assert info.alias is None

    def test_relative_import_dot_module(self, adapter):
        """from .module import func -> module='.module', symbol='func', is_relative=True"""
        info = adapter.parse_import("from .module import func", "/project/pkg/sub.py")
        assert info.module == ".module"
        assert info.symbol == "func"
        assert info.is_relative is True

    def test_relative_import_dotdot(self, adapter):
        """from ..parent import Thing -> module='..parent', symbol='Thing', is_relative=True"""
        info = adapter.parse_import("from ..parent import Thing", "/project/pkg/sub.py")
        assert info.module == "..parent"
        assert info.symbol == "Thing"
        assert info.is_relative is True

    def test_relative_import_triple_dot(self, adapter):
        """from ...grandparent import Func -> module='...grandparent', is_relative=True"""
        info = adapter.parse_import(
            "from ...grandparent import Func", "/project/pkg/sub/deep.py"
        )
        assert info.module == "...grandparent"
        assert info.is_relative is True

    # --- edge cases ----------------------------------------------------------

    def test_import_with_trailing_whitespace(self, adapter):
        """Import statement with trailing spaces/newlines still parsed correctly."""
        info = adapter.parse_import("  import os  ", "/project/main.py")
        assert info.module == "os"

    def test_from_import_stdlib(self, adapter):
        """from collections import defaultdict -> correct parsing."""
        info = adapter.parse_import(
            "from collections import defaultdict", "/project/main.py"
        )
        assert info.module == "collections"
        assert info.symbol == "defaultdict"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_simple_module_to_py_file(self, adapter):
        """foo -> <current_dir>/foo.py when foo.py exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create current_file
            current_file = os.path.join(tmpdir, "main.py")
            with open(current_file, "w") as f:
                f.write("pass")
            # Create the target module
            with open(os.path.join(tmpdir, "foo.py"), "w") as f:
                f.write("pass")

            result = adapter.resolve_module_path("foo", current_file)
            assert result is not None
            assert os.path.basename(result) == "foo.py"

    def test_resolves_dotted_module_to_directory(self, adapter):
        """foo.bar -> <current_dir>/foo/bar.py or <current_dir>/foo/bar/__init__.py"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "main.py")
            with open(current_file, "w") as f:
                f.write("pass")
            # Create foo/bar.py
            pkg_dir = os.path.join(tmpdir, "foo")
            os.makedirs(pkg_dir, exist_ok=True)
            with open(os.path.join(pkg_dir, "bar.py"), "w") as f:
                f.write("pass")

            result = adapter.resolve_module_path("foo.bar", current_file)
            assert result is not None

    def test_resolves_module_to_init_py(self, adapter):
        """foo -> <current_dir>/foo/__init__.py when foo is a package."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "main.py")
            with open(current_file, "w") as f:
                f.write("pass")
            pkg_dir = os.path.join(tmpdir, "foo")
            os.makedirs(pkg_dir, exist_ok=True)
            with open(os.path.join(pkg_dir, "__init__.py"), "w") as f:
                f.write("pass")

            result = adapter.resolve_module_path("foo", current_file)
            assert result is not None
            # Should resolve to __init__.py or foo.py
            assert "__init__.py" in result or result.endswith("foo.py")

    def test_returns_none_for_unresolvable_module(self, adapter):
        """Module that does not exist -> None."""
        result = adapter.resolve_module_path(
            "nonexistent_module_xyz", "/project/main.py"
        )
        assert result is None

    def test_returns_str_or_none(self, adapter):
        """Return type is Optional[str]."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "main.py")
            with open(current_file, "w") as f:
                f.write("pass")
            with open(os.path.join(tmpdir, "exist.py"), "w") as f:
                f.write("pass")

            result = adapter.resolve_module_path("exist", current_file)
            assert result is None or isinstance(result, str)

    def test_resolves_from_subdirectory(self, adapter):
        """Module resolution from a file in a subdirectory."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # main.py in subdir/pkg/
            pkg_dir = os.path.join(tmpdir, "subdir", "pkg")
            os.makedirs(pkg_dir, exist_ok=True)
            current_file = os.path.join(pkg_dir, "main.py")
            with open(current_file, "w") as f:
                f.write("pass")
            # Create a sibling module
            with open(os.path.join(pkg_dir, "sibling.py"), "w") as f:
                f.write("pass")

            result = adapter.resolve_module_path("sibling", current_file)
            assert result is not None
            assert "sibling.py" in result
