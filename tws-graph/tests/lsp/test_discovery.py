"""Tests for lsp/discovery.py --- DiscoveryResult + discover / discover_all.

TDD red phase: these tests are written before the discovery module exists.
They encode the acceptance criteria from design-p5-lsp.md for discovery.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - DiscoveryResult frozen dataclass: language, binary, available, path, error
  - discover(adapter) -> DiscoveryResult
  - discover_all(adapters) -> dict[str, DiscoveryResult]
  - available=True when binary is found in PATH (shutil.which)
  - available=False when binary not found or check_availability() returns False
  - available=False when error contains reason
  - L2 manual path override via TWS_LSP_{LANG}_BINARY env var
  - adapter.check_availability() returns False -> marked unavailable
  - Empty adapter list returns empty dict
"""

from __future__ import annotations

import os
import shutil
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.discovery is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.discovery import (  # noqa: E402
    DiscoveryResult,
    discover,
    discover_all,
)
from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


# ============================================================================
# Helper: minimal concrete adapter factory
# ============================================================================

def _make_mock_adapter(
    *,
    language: str = "test-lang",
    binary: str = "test-server",
    available: bool = True,
) -> LspLanguageAdapter:
    """Create a minimal fully-concrete LspLanguageAdapter subclass for testing.

    All five abstract methods are implemented so the instance can be created.
    The ``available`` flag controls ``check_availability()``.
    """
    _language = language

    class MockAdapter(LspLanguageAdapter):
        language = _language

        def get_server_command(self, workspace_root: str) -> list[str]:
            return [binary, "--stdio"]

        def parse_import(self, import_statement: str, current_file: str) -> ImportInfo:
            return ImportInfo(
                module="mock", symbol="mock", is_relative=False, alias=None,
            )

        def resolve_module_path(self, module_name: str, current_file: str) -> str:
            return "/fake/path"

        def get_file_extensions(self) -> list[str]:
            return [".mock"]

        def check_availability(self) -> bool:
            return available

    return MockAdapter()


# ============================================================================
# DiscoveryResult dataclass tests
# ============================================================================

class TestDiscoveryResultDataclass:
    """DiscoveryResult(language, binary, available, path=None, error=None)

    Acceptance criteria:
      - Frozen dataclass with five fields
      - path and error default to None
    """

    def test_create_available_with_path(self):
        """DiscoveryResult: create an available result with a concrete path."""
        result = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
            path="/usr/local/bin/pyright-langserver",
        )
        assert result.language == "python"
        assert result.binary == "pyright-langserver"
        assert result.available is True
        assert result.path == "/usr/local/bin/pyright-langserver"
        assert result.error is None

    def test_create_unavailable_with_error(self):
        """DiscoveryResult: create an unavailable result with an error message."""
        result = DiscoveryResult(
            language="typescript",
            binary="typescript-language-server",
            available=False,
            error="Binary not found in PATH",
        )
        assert result.language == "typescript"
        assert result.binary == "typescript-language-server"
        assert result.available is False
        assert result.path is None
        assert result.error == "Binary not found in PATH"

    def test_defaults_path_and_error_are_none(self):
        """DiscoveryResult: path and error default to None when omitted."""
        result = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
        )
        assert result.path is None
        assert result.error is None

    def test_frozen_cannot_mutate(self):
        """DiscoveryResult: frozen dataclass — setting any attribute raises."""
        result = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
            path="/usr/bin/pyright-langserver",
        )
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError or similar
            result.language = "ruby"  # type: ignore[misc]

        with pytest.raises(Exception):
            result.available = False  # type: ignore[misc]

        with pytest.raises(Exception):
            result.path = "/other"  # type: ignore[misc]

        with pytest.raises(Exception):
            result.error = "changed"  # type: ignore[misc]

    def test_equality_by_value(self):
        """DiscoveryResult: value equality (same fields -> equal, different -> not)."""
        a = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
            path="/usr/bin/pyright-langserver",
        )
        b = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
            path="/usr/bin/pyright-langserver",
        )
        c = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=False,
            error="not found",
        )
        assert a == b
        assert a != c
        assert b != c

    def test_hashable(self):
        """DiscoveryResult: frozen dataclass is hashable (can be used in sets/dicts)."""
        result = DiscoveryResult(
            language="python",
            binary="pyright-langserver",
            available=True,
            path="/usr/bin/pyright-langserver",
        )
        # Should not raise
        _ = hash(result)
        _ = {result}


# ============================================================================
# discover() function tests
# ============================================================================

class TestDiscover:
    """discover(adapter: LspLanguageAdapter) -> DiscoveryResult

    Acceptance criteria:
      - Returns DiscoveryResult with language from adapter
      - binary field populated from adapter's server command
      - available=True + path when shutil.which finds binary
      - available=False + error when binary not found
      - L2 env var TWS_LSP_{LANG}_BINARY overrides PATH lookup
      - adapter.check_availability()=False forces unavailable
    """

    # -- helpers --

    @staticmethod
    def _mock_which_returns(path: str | None):
        """Create a shutil.which side-effect that returns *path*."""
        def _which(cmd, path_env=None):
            return path
        return _which

    # -- basic success / failure --

    def test_binary_found_returns_available(self):
        """discover: when shutil.which finds binary -> available=True + path."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        found_path = "/usr/local/bin/pyright-langserver"

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(found_path)):
            result = discover(adapter)

        assert result.language == "python"
        assert result.binary == "pyright-langserver"
        assert result.available is True
        assert result.path == found_path
        assert result.error is None

    def test_binary_not_found_returns_unavailable(self):
        """discover: when shutil.which returns None -> available=False + error."""
        adapter = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(None)):
            result = discover(adapter)

        assert result.language == "typescript"
        assert result.binary == "typescript-language-server"
        assert result.available is False
        assert result.path is None
        assert result.error is not None
        assert "not found" in result.error.lower() or "unavailable" in result.error.lower()

    def test_check_availability_false_forces_unavailable(self):
        """discover: even if binary is in PATH, check_availability()=False -> unavailable."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=False,
        )
        found_path = "/usr/local/bin/pyright-langserver"

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(found_path)):
            result = discover(adapter)

        assert result.available is False
        assert result.path is None
        assert result.error is not None

    def test_both_not_found_and_not_available(self):
        """discover: binary not in PATH and check_availability()=False -> unavailable."""
        adapter = _make_mock_adapter(
            language="go", binary="gopls", available=False,
        )

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(None)):
            result = discover(adapter)

        assert result.available is False
        assert result.path is None
        assert result.error is not None

    # -- L2 env var override --

    def test_l2_env_override_python(self, monkeypatch):
        """discover: TWS_LSP_PYTHON_BINARY overrides PATH lookup result."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        custom_path = "/custom/path/to/pyright-langserver"
        monkeypatch.setenv("TWS_LSP_PYTHON_BINARY", custom_path)

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns("/usr/bin/pyright-langserver")):
            result = discover(adapter)

        assert result.available is True
        assert result.path == custom_path
        assert result.binary == "pyright-langserver"

    def test_l2_env_override_typescript(self, monkeypatch):
        """discover: TWS_LSP_TYPESCRIPT_BINARY overrides PATH lookup result."""
        adapter = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )
        custom_path = "/opt/node/bin/typescript-language-server"
        monkeypatch.setenv("TWS_LSP_TYPESCRIPT_BINARY", custom_path)

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns("/usr/bin/typescript-language-server")):
            result = discover(adapter)

        assert result.available is True
        assert result.path == custom_path

    def test_l2_env_override_still_checks_availability(self, monkeypatch):
        """discover: even with L2 override, check_availability()=False -> unavailable."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=False,
        )
        monkeypatch.setenv("TWS_LSP_PYTHON_BINARY", "/custom/pyright-langserver")

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(None)):
            result = discover(adapter)

        assert result.available is False
        assert result.error is not None

    def test_l2_env_var_not_set_falls_back_to_path(self):
        """discover: when env var is not set, falls back to shutil.which."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )

        # Ensure env var is NOT set
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns("/usr/bin/pyright-langserver")):
                result = discover(adapter)

        assert result.available is True
        assert result.path == "/usr/bin/pyright-langserver"

    # -- edge cases --

    def test_oserror_in_shutil_which_handled_gracefully(self):
        """discover: OSError from shutil.which is caught -> unavailable."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )

        def _raise_oserror(cmd, path_env=None):
            raise OSError("Permission denied")

        with mock.patch.object(shutil, "which", side_effect=_raise_oserror):
            result = discover(adapter)

        assert result.available is False
        assert result.error is not None
        assert "oserror" in result.error.lower() or "permission" in result.error.lower()

    def test_adapter_with_multiple_words_in_binary(self):
        """discover: binary name extracted from adapter's get_server_command()[0]."""
        adapter = _make_mock_adapter(
            language="java", binary="java-lsp-server", available=True,
        )
        found_path = "/opt/jdtls/bin/java-lsp-server"

        with mock.patch.object(shutil, "which", side_effect=self._mock_which_returns(found_path)):
            result = discover(adapter)

        assert result.binary == "java-lsp-server"
        assert result.language == "java"
        assert result.path == found_path


# ============================================================================
# discover_all() function tests
# ============================================================================

class TestDiscoverAll:
    """discover_all(adapters: list[LspLanguageAdapter]) -> dict[str, DiscoveryResult]

    Acceptance criteria:
      - Returns dict keyed by language name
      - Each adapter gets a DiscoveryResult
      - Available + path when found; unavailable + error when not
      - Empty list returns empty dict
      - L2 env var per-language override works
    """

    def test_empty_list_returns_empty_dict(self):
        """discover_all: [] -> {}."""
        assert discover_all([]) == {}

    def test_single_adapter_available(self):
        """discover_all: single available adapter -> dict with one available entry."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        found_path = "/usr/bin/pyright-langserver"

        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: found_path):
            results = discover_all([adapter])

        assert isinstance(results, dict)
        assert len(results) == 1
        assert "python" in results
        r = results["python"]
        assert r.language == "python"
        assert r.binary == "pyright-langserver"
        assert r.available is True
        assert r.path == found_path

    def test_single_adapter_unavailable(self):
        """discover_all: single unavailable adapter -> dict with one unavailable entry."""
        adapter = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=False,
        )

        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: None):
            results = discover_all([adapter])

        assert len(results) == 1
        assert "typescript" in results
        r = results["typescript"]
        assert r.available is False
        assert r.error is not None

    def test_multiple_adapters_all_available(self):
        """discover_all: multiple available adapters -> all marked available."""
        python = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        typescript = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )
        py_path = "/usr/bin/pyright-langserver"
        ts_path = "/usr/bin/typescript-language-server"

        def _which(cmd, path_env=None):
            return py_path if "pyright" in cmd else ts_path

        with mock.patch.object(shutil, "which", side_effect=_which):
            results = discover_all([python, typescript])

        assert len(results) == 2
        assert results["python"].available is True
        assert results["python"].path == py_path
        assert results["typescript"].available is True
        assert results["typescript"].path == ts_path

    def test_multiple_adapters_mixed_availability(self):
        """discover_all: mixed available/unavailable -> each correctly reported."""
        python = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        typescript = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=False,
        )
        go = _make_mock_adapter(
            language="go", binary="gopls", available=True,
        )

        def _which(cmd, path_env=None):
            if "pyright" in cmd:
                return "/usr/bin/pyright-langserver"
            if "gopls" in cmd:
                return "/usr/bin/gopls"
            return None

        with mock.patch.object(shutil, "which", side_effect=_which):
            results = discover_all([python, typescript, go])

        assert len(results) == 3

        assert results["python"].available is True
        assert results["python"].path == "/usr/bin/pyright-langserver"

        assert results["typescript"].available is False
        assert results["typescript"].error is not None

        assert results["go"].available is True
        assert results["go"].path == "/usr/bin/gopls"

    def test_dict_keys_are_language_names(self):
        """discover_all: result dict keys are adapter.language values, not indices."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )

        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: "/usr/bin/pyright-langserver"):
            results = discover_all([adapter])

        assert list(results.keys()) == ["python"]
        assert all(isinstance(k, str) for k in results)

    def test_l2_env_override_per_language(self, monkeypatch):
        """discover_all: per-language L2 env var overrides work independently."""
        python_adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        ts_adapter = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )

        py_custom = "/custom/pyright"
        ts_custom = "/custom/tsserver"
        monkeypatch.setenv("TWS_LSP_PYTHON_BINARY", py_custom)
        monkeypatch.setenv("TWS_LSP_TYPESCRIPT_BINARY", ts_custom)

        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: "/usr/bin/default"):
            results = discover_all([python_adapter, ts_adapter])

        assert results["python"].path == py_custom
        assert results["typescript"].path == ts_custom

    def test_l2_env_var_only_for_one_language(self, monkeypatch):
        """discover_all: env var for one language, the other falls back to PATH."""
        python_adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        ts_adapter = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )

        py_custom = "/custom/pyright"
        monkeypatch.setenv("TWS_LSP_PYTHON_BINARY", py_custom)
        # TWS_LSP_TYPESCRIPT_BINARY is NOT set

        ts_found = "/usr/bin/typescript-language-server"

        def _which(cmd, path_env=None):
            if "pyright" in cmd:
                return "/usr/bin/pyright-langserver"  # should be overridden
            return ts_found

        with mock.patch.object(shutil, "which", side_effect=_which):
            results = discover_all([python_adapter, ts_adapter])

        assert results["python"].path == py_custom
        assert results["typescript"].path == ts_found

    def test_adapter_order_insensitive(self):
        """discover_all: result keys are language names regardless of input order."""
        python = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        typescript = _make_mock_adapter(
            language="typescript", binary="typescript-language-server", available=True,
        )
        go = _make_mock_adapter(
            language="go", binary="gopls", available=True,
        )

        def _which(cmd, path_env=None):
            return f"/usr/bin/{cmd}"

        with mock.patch.object(shutil, "which", side_effect=_which):
            results_abc = discover_all([go, python, typescript])
            results_cba = discover_all([typescript, python, go])

        # Same keys regardless of input order
        assert set(results_abc.keys()) == set(results_cba.keys()) == {"python", "typescript", "go"}
        # Values are the same per-language
        for lang in ("python", "typescript", "go"):
            assert results_abc[lang] == results_cba[lang]

    def test_results_are_discovery_result_instances(self):
        """discover_all: every value in result dict is a DiscoveryResult."""
        adapter = _make_mock_adapter(
            language="python", binary="pyright-langserver", available=True,
        )
        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: "/usr/bin/pyright-langserver"):
            results = discover_all([adapter])

        assert all(isinstance(v, DiscoveryResult) for v in results.values())


# ============================================================================
# Integration-style tests with real adapters
# ============================================================================

class TestDiscoverWithRealAdapters:
    """Smoke tests using the actual PythonLspAdapter and TypeScriptLspAdapter.

    These verify that discover() / discover_all() integrate correctly with the
    real adapter classes (binary names, language strings, etc.).
    """

    @pytest.fixture(autouse=True)
    def _clear_lsp_env(self, monkeypatch):
        """Ensure no L2 env vars leak from developer environment."""
        for key in list(os.environ):
            if key.startswith("TWS_LSP_"):
                monkeypatch.delenv(key, raising=False)

    def test_python_adapter_binary_name(self):
        """discover: PythonLspAdapter yields binary='pyright-langserver'."""
        from tws_graph.lsp.adapters.python import PythonLspAdapter

        adapter = PythonLspAdapter()
        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: "/usr/bin/pyright-langserver"):
            result = discover(adapter)

        assert result.language == "python"
        assert result.binary == "pyright-langserver"

    def test_typescript_adapter_binary_name(self):
        """discover: TypeScriptLspAdapter yields binary='typescript-language-server'."""
        from tws_graph.lsp.adapters.typescript import TypeScriptLspAdapter

        adapter = TypeScriptLspAdapter()
        with mock.patch.object(shutil, "which", side_effect=lambda cmd, path_env=None: "/usr/bin/typescript-language-server"):
            result = discover(adapter)

        assert result.language == "typescript"
        assert result.binary == "typescript-language-server"

    def test_discover_all_with_real_adapters(self):
        """discover_all: with both real adapters, returns dict keyed by
        'python' and 'typescript'."""
        from tws_graph.lsp.adapters.python import PythonLspAdapter
        from tws_graph.lsp.adapters.typescript import TypeScriptLspAdapter

        def _which(cmd, path_env=None):
            return f"/usr/bin/{cmd}"

        with mock.patch.object(shutil, "which", side_effect=_which):
            results = discover_all([PythonLspAdapter(), TypeScriptLspAdapter()])

        assert set(results.keys()) == {"python", "typescript"}
        assert results["python"].binary == "pyright-langserver"
        assert results["typescript"].binary == "typescript-language-server"

    def test_real_adapter_not_available_in_test_env(self):
        """discover: in test env without real servers, result is unavailable (red)."""
        from tws_graph.lsp.adapters.python import PythonLspAdapter

        adapter = PythonLspAdapter()
        result = discover(adapter)

        # In CI / dev environment, pyright-langserver may or may not be installed.
        # The test only checks structural correctness, not a specific bool value.
        assert isinstance(result, DiscoveryResult)
        assert result.language == "python"
        assert result.binary == "pyright-langserver"
        # available reflects the real environment — both True and False are valid
        assert isinstance(result.available, bool)
        if result.available:
            assert result.path is not None
        else:
            assert result.error is not None
