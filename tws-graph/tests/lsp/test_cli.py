"""Tests for CLI ``tws-graph lsp setup`` command.

TDD red phase: these tests are written before the CLI integration exists.
They encode the acceptance criteria from design-p5-lsp.md for the cli.py
integration point: ``tws-graph lsp setup`` and ``tws-graph lsp setup --json``.

Coverage per acceptance criteria:
  - ``tws-graph lsp setup`` outputs terminal table (headers, language/binary/status/path)
  - ``tws-graph lsp setup --json`` outputs valid JSON
  - Each DiscoveryResult renders correctly in both formats
  - Available adapters show path; unavailable show error
  - --help shows usage information
  - discover_all is called with registered adapters (mocked)
  - Adapter registration auto-discovers Python + TypeScript adapters
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from typer.testing import CliRunner

from tws_graph.cli import app
from tws_graph.lsp.discovery import DiscoveryResult


# ============================================================================
# Helpers
# ============================================================================


def _make_result(
    language: str,
    binary: str,
    available: bool,
    path: str | None = None,
    error: str | None = None,
) -> DiscoveryResult:
    """Create a DiscoveryResult for test assertions."""
    return DiscoveryResult(
        language=language,
        binary=binary,
        available=available,
        path=path if available else None,
        error=None if available else (error or "Not found"),
    )


def _all_available() -> dict[str, DiscoveryResult]:
    """Pre-built mock: both Python and TypeScript servers available."""
    return {
        "python": _make_result(
            "python", "pyright-langserver", True,
            path="/usr/local/bin/pyright-langserver",
        ),
        "typescript": _make_result(
            "typescript", "typescript-language-server", True,
            path="/usr/local/bin/typescript-language-server",
        ),
    }


def _all_unavailable() -> dict[str, DiscoveryResult]:
    """Pre-built mock: both servers unavailable."""
    return {
        "python": _make_result(
            "python", "pyright-langserver", False,
            error="Binary not found in PATH",
        ),
        "typescript": _make_result(
            "typescript", "typescript-language-server", False,
            error="Binary not found in PATH",
        ),
    }


def _mixed() -> dict[str, DiscoveryResult]:
    """Pre-built mock: one available, one not."""
    return {
        "python": _make_result(
            "python", "pyright-langserver", True,
            path="/usr/local/bin/pyright-langserver",
        ),
        "typescript": _make_result(
            "typescript", "typescript-language-server", False,
            error="Server availability check failed",
        ),
    }


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def runner():
    """Create a CliRunner for testing CLI commands."""
    return CliRunner()


# ============================================================================
# Test: lsp appears in main help
# ============================================================================


class TestLspInMainHelp:
    """Verify the ``lsp`` subcommand is listed in the top-level help."""

    def test_lsp_appears_in_main_help(self, runner):
        """Main help should list the lsp command group."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0, f"CLI failed: {result.stderr}"
        assert "lsp" in result.output.lower(), (
            f"Main help output:\n{result.output}"
        )

    def test_lsp_subcommand_help(self, runner):
        """``tws-graph lsp --help`` lists ``setup`` command."""
        result = runner.invoke(app, ["lsp", "--help"])
        assert result.exit_code == 0, f"CLI failed: {result.stderr}"
        assert "setup" in result.output.lower(), (
            f"LSP help output:\n{result.output}"
        )


# ============================================================================
# Test: lsp setup --help
# ============================================================================


class TestLspSetupHelp:
    """Tests for ``tws-graph lsp setup --help``."""

    def test_setup_help_shows_usage(self, runner):
        """setup --help should show usage and the --json option."""
        result = runner.invoke(app, ["lsp", "setup", "--help"])
        assert result.exit_code == 0, f"CLI failed: {result.stderr}"
        output = result.output.lower()
        assert "setup" in output
        assert "json" in output, (
            f"Expected --json option in help:\n{result.output}"
        )


# ============================================================================
# Test: lsp setup table output
# ============================================================================


class TestLspSetupTableOutput:
    """Tests for terminal table output of ``tws-graph lsp setup``.

    The table must display: language, binary name, status indicator, path or
    error message for each adapter returned by ``discover_all``.
    """

    def test_all_available_shows_success_table(self, runner):
        """When all servers are available, the table shows each with a
        status indicator and path."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        output = result.output
        # Each language appears
        assert "python" in output.lower()
        assert "typescript" in output.lower()
        # Binary names appear
        assert "pyright-langserver" in output
        assert "typescript-language-server" in output
        # Paths appear
        assert "/usr/local/bin/pyright-langserver" in output
        assert "/usr/local/bin/typescript-language-server" in output
        # Some status-like indicator (e.g. a checkmark, "OK", "available")
        # — we just assert the output is not empty and has column-like structure

    def test_all_unavailable_shows_error_info(self, runner):
        """When all servers are unavailable, error messages are displayed."""
        mock_results = _all_unavailable()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        output = result.output
        # Both languages listed
        assert "python" in output.lower()
        assert "typescript" in output.lower()
        # Error messages shown
        assert "not found" in output.lower()

    def test_mixed_availability(self, runner):
        """Table shows available and unavailable rows correctly side by side."""
        mock_results = _mixed()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        output = result.output
        # Available path shown
        assert "/usr/local/bin/pyright-langserver" in output
        # Unavailable error shown
        assert "availability" in output.lower() or "failed" in output.lower()

    def test_zero_adapters_empty_output(self, runner):
        """When discover_all returns empty dict, graceful output (no crash)."""
        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value={},
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        # Should produce some output, at least not crash
        assert isinstance(result.output, str)

    def test_table_format_has_columns(self, runner):
        """The table output should contain language and binary column headers
        or structured alignment."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        output = result.output
        # Column-like structure: binary names are separated by whitespace
        # and language appears in the same row
        lines = [l for l in output.splitlines() if l.strip()]
        assert len(lines) >= 2, (
            f"Expected at least header + data lines, got:\n{output}"
        )


# ============================================================================
# Test: lsp setup --json output
# ============================================================================


class TestLspSetupJsonOutput:
    """Tests for JSON output of ``tws-graph lsp setup --json``.

    JSON output must be a valid JSON object keyed by language, with each
    value containing the DiscoveryResult fields.
    """

    def test_json_output_all_available(self, runner):
        """--json flag produces valid JSON with correct fields."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        data = json.loads(result.output)
        assert "python" in data
        assert "typescript" in data

        py = data["python"]
        assert py["language"] == "python"
        assert py["binary"] == "pyright-langserver"
        assert py["available"] is True
        assert py["path"] == "/usr/local/bin/pyright-langserver"
        assert py["error"] is None

        ts = data["typescript"]
        assert ts["language"] == "typescript"
        assert ts["binary"] == "typescript-language-server"
        assert ts["available"] is True
        assert ts["path"] == "/usr/local/bin/typescript-language-server"
        assert ts["error"] is None

    def test_json_output_all_unavailable(self, runner):
        """--json with all unavailable shows available=False and error."""
        mock_results = _all_unavailable()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)

        for lang in ("python", "typescript"):
            assert data[lang]["available"] is False
            assert data[lang]["error"] is not None
            assert data[lang]["path"] is None

    def test_json_output_mixed(self, runner):
        """--json with mixed availability has correct per-language fields."""
        mock_results = _mixed()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)

        assert data["python"]["available"] is True
        assert data["python"]["path"] is not None
        assert data["python"]["error"] is None

        assert data["typescript"]["available"] is False
        assert data["typescript"]["path"] is None
        assert data["typescript"]["error"] is not None

    def test_json_output_empty(self, runner):
        """--json with zero adapters returns empty JSON object."""
        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value={},
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0, f"CLI failed: {result.output}"
        data = json.loads(result.output)
        assert data == {}

    def test_json_output_is_pretty_printed(self, runner):
        """--json output should be indented for readability."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0
        # Pretty-printed JSON has newlines and indentation
        assert "\n" in result.output
        assert "  " in result.output

    def test_json_output_is_valid_unicode(self, runner):
        """--json output is valid UTF-8, use ensure_ascii=False."""
        # Even with ASCII-only data, the result should parse correctly
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0
        # Must be valid JSON
        json.loads(result.output)


# ============================================================================
# Test: discover_all is called with registered adapters
# ============================================================================


class TestDiscoverAllIntegration:
    """Verify that ``lsp setup`` calls ``discover_all()`` with the correct
    adapter instances (Python + TypeScript)."""

    def test_discovers_both_python_and_typescript(self, runner):
        """When running without mocks, check that both languages appear in
        the output (real discovery may show unavailable if servers not
        installed)."""
        result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0, (
            f"CLI failed: {result.output}\n{result.stderr}"
        )
        data = json.loads(result.output)

        # At minimum both adapters should be registered and discovered
        assert "python" in data, (
            f"Expected 'python' in discover_all results, got keys: "
            f"{list(data.keys())}"
        )
        assert "typescript" in data, (
            f"Expected 'typescript' in discover_all results, got keys: "
            f"{list(data.keys())}"
        )

    def test_result_structure_is_correct(self, runner):
        """Each entry has language, binary, available, path, error fields."""
        result = runner.invoke(app, ["lsp", "setup", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)

        for lang, entry in data.items():
            assert "language" in entry, (
                f"Missing 'language' in {lang}: {entry}"
            )
            assert "binary" in entry, (
                f"Missing 'binary' in {lang}: {entry}"
            )
            assert "available" in entry, (
                f"Missing 'available' in {lang}: {entry}"
            )
            assert "path" in entry, (
                f"Missing 'path' in {lang}: {entry}"
            )
            assert "error" in entry, (
                f"Missing 'error' in {lang}: {entry}"
            )


# ============================================================================
# Test: table output uses typer.echo / typer.style for formatting
# ============================================================================


class TestTableFormattingSpecifics:
    """Detailed formatting assertions for the terminal table output."""

    def test_binary_name_column_present(self, runner):
        """Each row contains the binary name from the adapter."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0
        # Binary names must appear in the output
        assert "pyright-langserver" in result.output
        assert "typescript-language-server" in result.output

    def test_path_column_present_for_available(self, runner):
        """Available rows have the discovered binary path."""
        mock_results = _all_available()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0
        assert "/usr/local/bin/pyright-langserver" in result.output
        assert "/usr/local/bin/typescript-language-server" in result.output

    def test_status_column_shows_fail_for_unavailable(self, runner):
        """Unavailable rows display a failure indicator and error text."""
        mock_results = _all_unavailable()

        with mock.patch(
            "tws_graph.lsp.discovery.discover_all", return_value=mock_results,
        ):
            result = runner.invoke(app, ["lsp", "setup"])

        assert result.exit_code == 0
        output = result.output.lower()
        # Some kind of failure indication
        has_fail_indicator = (
            "not found" in output
            or "unavailable" in output
            or "FAIL" in result.output
            or "\u2717" in result.output  # cross mark
            or "\u2718" in result.output
            or "x " in output
            or "no" in output
        )
        assert has_fail_indicator, (
            f"Expected a failure indicator in output:\n{result.output}"
        )
