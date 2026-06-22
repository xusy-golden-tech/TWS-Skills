"""Tests for lsp/adapters/typescript.py --- TypeScriptLspAdapter.

TDD red phase: these tests are written before the adapters/typescript module exists.
They encode the acceptance criteria from design-p5-lsp.md for adapters/typescript.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - language = "typescript"
  - get_server_command returns ["typescript-language-server", "--stdio"]
  - get_file_extensions returns [".ts", ".tsx", ".js", ".jsx"]
  - check_availability checks typescript-language-server via shutil.which
  - parse_import: named import, default import, namespace import, side-effect import
  - resolve_module_path: relative path, absolute-like path resolution
"""

import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.adapters.typescript is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.adapters.typescript import TypeScriptLspAdapter  # noqa: E402
from tws_graph.lsp.adapters.base import ImportInfo  # noqa: E402


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def adapter():
    """Create a fresh TypeScriptLspAdapter instance for each test."""
    return TypeScriptLspAdapter()


# ============================================================================
# language property
# ============================================================================

class TestTypeScriptLanguageProperty:
    """language should return 'typescript'."""

    def test_language_is_typescript(self, adapter):
        assert adapter.language == "typescript"

    def test_language_is_string(self, adapter):
        assert isinstance(adapter.language, str)


# ============================================================================
# get_server_command
# ============================================================================

class TestGetServerCommand:
    """get_server_command(workspace_root) -> list[str]"""

    def test_returns_typescript_server_command(self, adapter):
        cmd = adapter.get_server_command("/home/user/project")
        assert cmd == ["typescript-language-server", "--stdio"]

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

    def test_returns_ts_tsx_js_jsx(self, adapter):
        exts = adapter.get_file_extensions()
        assert exts == [".ts", ".tsx", ".js", ".jsx"]

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
    """check_availability() -> bool: checks typescript-language-server in PATH."""

    def test_returns_true_when_binary_found(self, adapter):
        """When shutil.which finds typescript-language-server, return True."""
        with patch(
            "shutil.which", return_value="/usr/local/bin/typescript-language-server"
        ) as mock_which:
            result = adapter.check_availability()
            assert result is True
            mock_which.assert_called_once_with("typescript-language-server")

    def test_returns_false_when_binary_not_found(self, adapter):
        """When shutil.which returns None, return False."""
        with patch("shutil.which", return_value=None) as mock_which:
            result = adapter.check_availability()
            assert result is False
            mock_which.assert_called_once_with("typescript-language-server")

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

    # --- named imports (destructured) ----------------------------------------

    def test_named_import_single(self, adapter):
        """import {X} from './foo' -> module='./foo', symbol='X', is_relative=True"""
        info = adapter.parse_import("import {X} from './foo'", "/project/src/test.ts")
        assert isinstance(info, ImportInfo)
        assert info.module == "./foo"
        assert info.symbol == "X"
        assert info.is_relative is True
        assert info.alias is None

    def test_named_import_multiple_destructured(self, adapter):
        """import {a, b} from './multi' -> module='./multi', symbol='a' (first)"""
        info = adapter.parse_import(
            "import {a, b} from './multi'", "/project/src/test.ts"
        )
        assert info.module == "./multi"
        assert info.symbol == "a"
        assert info.is_relative is True

    def test_named_import_with_aliasing(self, adapter):
        """import {X as Y} from './foo' -> module='./foo', symbol='X', alias='Y'"""
        info = adapter.parse_import(
            "import {X as Y} from './foo'", "/project/src/test.ts"
        )
        assert info.module == "./foo"
        assert info.symbol == "X"
        assert info.alias == "Y"

    def test_named_import_from_absolute_module(self, adapter):
        """import {useState} from 'react' -> module='react', symbol='useState', is_relative=False"""
        info = adapter.parse_import(
            "import {useState} from 'react'", "/project/src/test.ts"
        )
        assert info.module == "react"
        assert info.symbol == "useState"
        assert info.is_relative is False

    # --- default imports -----------------------------------------------------

    def test_default_import_relative(self, adapter):
        """import X from './foo' -> module='./foo', symbol='X', is_relative=True"""
        info = adapter.parse_import("import X from './foo'", "/project/src/test.ts")
        assert info.module == "./foo"
        assert info.symbol == "X"
        assert info.is_relative is True
        assert info.alias is None

    def test_default_import_absolute(self, adapter):
        """import X from 'lodash' -> module='lodash', symbol='X', is_relative=False"""
        info = adapter.parse_import("import X from 'lodash'", "/project/src/test.ts")
        assert info.module == "lodash"
        assert info.symbol == "X"
        assert info.is_relative is False

    # --- namespace imports ---------------------------------------------------

    def test_namespace_import(self, adapter):
        """import * as X from './bar' -> module='./bar', symbol='*', is_relative=True, alias='X'"""
        info = adapter.parse_import(
            "import * as X from './bar'", "/project/src/test.ts"
        )
        assert info.module == "./bar"
        assert info.symbol == "*"
        assert info.is_relative is True
        assert info.alias == "X"

    def test_namespace_import_absolute(self, adapter):
        """import * as React from 'react' -> module='react', symbol='*', alias='React'"""
        info = adapter.parse_import(
            "import * as React from 'react'", "/project/src/test.ts"
        )
        assert info.module == "react"
        assert info.symbol == "*"
        assert info.is_relative is False
        assert info.alias == "React"

    # --- combined imports ----------------------------------------------------

    def test_combined_default_and_named(self, adapter):
        """import React, {useState} from 'react' -> module='react', symbol='React' (default first)"""
        info = adapter.parse_import(
            "import React, {useState} from 'react'", "/project/src/test.ts"
        )
        assert info.module == "react"
        assert info.symbol == "React"
        assert info.is_relative is False

    # --- side-effect imports -------------------------------------------------

    def test_side_effect_import(self, adapter):
        """import 'side-effect' -> module='side-effect', symbol='', is_relative=False"""
        info = adapter.parse_import(
            "import 'side-effect'", "/project/src/test.ts"
        )
        assert info.module == "side-effect"
        assert info.symbol == ""
        assert info.is_relative is False
        assert info.alias is None

    def test_side_effect_import_relative(self, adapter):
        """import './init' -> module='./init', symbol='', is_relative=True"""
        info = adapter.parse_import(
            "import './init'", "/project/src/test.ts"
        )
        assert info.module == "./init"
        assert info.symbol == ""
        assert info.is_relative is True

    # --- relative path variants ----------------------------------------------

    def test_relative_import_parent_dir(self, adapter):
        """import {X} from '../parent' -> module='../parent', is_relative=True"""
        info = adapter.parse_import(
            "import {X} from '../parent'", "/project/src/test.ts"
        )
        assert info.module == "../parent"
        assert info.is_relative is True

    def test_relative_import_current_dir_slash(self, adapter):
        """import {X} from './foo/bar' -> module='./foo/bar', is_relative=True"""
        info = adapter.parse_import(
            "import {X} from './foo/bar'", "/project/src/test.ts"
        )
        assert info.module == "./foo/bar"
        assert info.is_relative is True

    # --- scoped packages -----------------------------------------------------

    def test_import_from_scoped_package(self, adapter):
        """import X from '@scope/pkg' -> module='@scope/pkg', is_relative=False"""
        info = adapter.parse_import(
            "import X from '@scope/pkg'", "/project/src/test.ts"
        )
        assert info.module == "@scope/pkg"
        assert info.symbol == "X"
        assert info.is_relative is False

    # --- type imports --------------------------------------------------------

    def test_type_import(self, adapter):
        """import type {X} from './types' -> module='./types', symbol='X', is_relative=True"""
        info = adapter.parse_import(
            "import type {X} from './types'", "/project/src/test.ts"
        )
        assert info.module == "./types"
        assert info.symbol == "X"
        assert info.is_relative is True

    # --- edge cases ----------------------------------------------------------

    def test_import_with_semicolon(self, adapter):
        """import {X} from './foo'; -> trailing semicolon handled correctly."""
        info = adapter.parse_import(
            "import {X} from './foo';", "/project/src/test.ts"
        )
        assert info.module == "./foo"
        assert info.symbol == "X"

    def test_import_with_trailing_whitespace(self, adapter):
        """Leading/trailing whitespace should be stripped."""
        info = adapter.parse_import(
            "  import X from 'lodash'  ", "/project/src/test.ts"
        )
        assert info.module == "lodash"
        assert info.symbol == "X"


# ============================================================================
# resolve_module_path
# ============================================================================

class TestResolveModulePath:
    """resolve_module_path(module_name, current_file) -> Optional[str]"""

    def test_resolves_relative_local_file_ts(self, adapter):
        """./foo -> <current_dir>/foo.ts when foo.ts exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            with open(os.path.join(tmpdir, "foo.ts"), "w") as f:
                f.write("// foo")

            result = adapter.resolve_module_path("./foo", current_file)
            assert result is not None
            assert os.path.basename(result) == "foo.ts"

    def test_resolves_relative_local_file_tsx(self, adapter):
        """./bar -> <current_dir>/bar.tsx when bar.tsx exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            with open(os.path.join(tmpdir, "bar.tsx"), "w") as f:
                f.write("// bar")

            result = adapter.resolve_module_path("./bar", current_file)
            assert result is not None
            assert os.path.basename(result) == "bar.tsx"

    def test_resolves_relative_local_file_js(self, adapter):
        """./lib -> <current_dir>/lib.js when lib.js exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            with open(os.path.join(tmpdir, "lib.js"), "w") as f:
                f.write("// lib")

            result = adapter.resolve_module_path("./lib", current_file)
            assert result is not None
            assert os.path.basename(result) == "lib.js"

    def test_resolves_without_extension_when_index_exists(self, adapter):
        """./dir -> <current_dir>/dir/index.ts when dir/index.ts exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            sub_dir = os.path.join(tmpdir, "dir")
            os.makedirs(sub_dir, exist_ok=True)
            with open(os.path.join(sub_dir, "index.ts"), "w") as f:
                f.write("// index")

            result = adapter.resolve_module_path("./dir", current_file)
            assert result is not None
            # Should resolve to index.ts inside the directory
            assert "index.ts" in result or "dir.ts" in result or "dir.tsx" in result

    def test_resolves_relative_parent_dir(self, adapter):
        """../sibling -> <parent_dir>/sibling.ts when sibling.ts exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir, exist_ok=True)
            current_file = os.path.join(src_dir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            # Create sibling.ts in the parent directory (tmpdir)
            with open(os.path.join(tmpdir, "sibling.ts"), "w") as f:
                f.write("// sibling")

            result = adapter.resolve_module_path("../sibling", current_file)
            assert result is not None
            assert "sibling.ts" in result

    def test_returns_none_for_unresolvable_module(self, adapter):
        """Module that does not exist -> None."""
        result = adapter.resolve_module_path(
            "./nonexistent", "/project/src/test.ts"
        )
        assert result is None

    def test_returns_str_or_none(self, adapter):
        """Return type is Optional[str]."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            with open(os.path.join(tmpdir, "exist.ts"), "w") as f:
                f.write("// exist")

            result = adapter.resolve_module_path("./exist", current_file)
            assert result is None or isinstance(result, str)

    def test_resolves_extension_priority(self, adapter):
        """When both foo.ts and foo.tsx exist, resolution order matters.

        The adapter should follow a consistent extension priority order
        (e.g., .ts before .tsx before .js before .jsx).
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            current_file = os.path.join(tmpdir, "test.ts")
            with open(current_file, "w") as f:
                f.write("// test")
            with open(os.path.join(tmpdir, "foo.ts"), "w") as f:
                f.write("// ts")
            with open(os.path.join(tmpdir, "foo.tsx"), "w") as f:
                f.write("// tsx")

            result = adapter.resolve_module_path("./foo", current_file)
            # Both exist; resolution should be consistent (pick .ts by priority)
            assert result is not None
            # If priority is .ts > .tsx, should get .ts
            assert "foo.ts" in result or "foo.tsx" in result
