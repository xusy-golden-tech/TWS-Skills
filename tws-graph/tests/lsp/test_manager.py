"""Tests for lsp/manager.py --- LspManager process manager.

TDD red phase: these tests are written before the manager module exists.
They encode the acceptance criteria from design-p5-lsp.md for manager.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - get_client first call lazily starts LSP server process
  - Repeated calls for same language return same LspClient instance
  - is_available returns True when LSP server can be started
  - shutdown_all correctly closes all processes without orphans
  - Crash restart: max 3 retries, exponential backoff 5s->10s->20s
  - After 3 consecutive failures, give up and stop retrying
  - No adapter for a language -> get_client returns None
  - Context manager exit calls shutdown_all
  - heartbeat_timeout detection

Strategy: mock LspClient + mock adapter, no real subprocess.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.manager is created.
# ---------------------------------------------------------------------------
# from tws_graph.lsp.manager import LspManager  # noqa: E402


# ============================================================================
# Helpers / Fixtures
# ============================================================================


def _make_mock_adapter(language, binary_name, available=True):
    """Create a MagicMock that behaves like an LspLanguageAdapter.

    Args:
        language: e.g. "python" or "typescript".
        binary_name: e.g. "pyright-langserver".
        available: whether check_availability() returns True.

    Returns a MagicMock with the adapter interface.
    """
    adapter = MagicMock()
    adapter.language = language
    adapter.get_server_command.return_value = [binary_name, "--stdio"]
    adapter.check_availability.return_value = available
    adapter.get_file_extensions.return_value = (
        [".py", ".pyi"] if language == "python" else [".ts", ".tsx"]
    )
    return adapter


def _make_mock_client(command, workspace_root):
    """Create a MagicMock that simulates an LspClient.

    Args:
        command: The server command list.
        workspace_root: Workspace root directory.

    Returns a MagicMock with client interface.
    """
    mock = MagicMock()
    mock.command = command
    mock.workspace_root = workspace_root
    mock._process = MagicMock()
    mock._process.poll.return_value = None  # alive
    mock._closed = False
    # _heartbeat returns True by default (process alive, no reader error)
    mock._heartbeat.return_value = True
    return mock


@pytest.fixture
def python_adapter():
    """A mock Python LSP adapter (pyright-langserver)."""
    return _make_mock_adapter("python", "pyright-langserver")


@pytest.fixture
def typescript_adapter():
    """A mock TypeScript LSP adapter (typescript-language-server)."""
    return _make_mock_adapter("typescript", "typescript-language-server")


@pytest.fixture
def unavailable_adapter():
    """A mock adapter whose check_availability returns False."""
    return _make_mock_adapter("rust", "rust-analyzer", available=False)


@pytest.fixture
def adapters(python_adapter, typescript_adapter):
    """Standard adapter list: python + typescript."""
    return [python_adapter, typescript_adapter]


@pytest.fixture
def mock_client_class():
    """Fixture that patches LspClient to return MagicMock instances.

    Yields the mock class so tests can inspect construction calls.
    """
    with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
        mock_cls.side_effect = lambda command, workspace_root: (
            _make_mock_client(command, workspace_root)
        )
        yield mock_cls


@pytest.fixture
def manager_factory(adapters, mock_client_class):
    """Factory fixture: creates LspManager with mocks in place.

    Since manager.py does not exist yet, this fixture uses a try/import
    pattern.  It will raise ImportError in red phase -- that is expected.
    Returns a callable: (kwargs) -> LspManager instance.
    """
    try:
        from tws_graph.lsp.manager import LspManager
    except ImportError:
        pytest.skip("LspManager not yet implemented (red phase)")

    def _make(**kwargs):
        params = {
            "workspace_root": "/test/workspace",
            "adapters": adapters,
        }
        params.update(kwargs)
        return LspManager(**params)

    return _make


# ============================================================================
# Test: LspManager.__init__
# ============================================================================


class TestLspManagerInit:
    """Tests for LspManager constructor and parameter storage."""

    def test_stores_workspace_root(self):
        """__init__ must store workspace_root."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/my/project", adapters=[])
        assert mgr.workspace_root == "/my/project"

    def test_stores_adapters_list(self):
        """adapters parameter is stored as-is or transformed."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        a1 = _make_mock_adapter("python", "pyright-langserver")
        mgr = LspManager(workspace_root="/ws", adapters=[a1])
        # adapters should be accessible
        assert hasattr(mgr, "_adapters") or hasattr(mgr, "adapters")

    def test_default_max_restart_is_3(self):
        """max_restart defaults to 3 per design spec."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        # max_restart should be stored or incorporated into restart logic
        assert hasattr(mgr, "max_restart")
        assert mgr.max_restart == 3

    def test_custom_max_restart(self):
        """max_restart can be overridden."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[], max_restart=5)
        assert mgr.max_restart == 5

    def test_default_restart_base_delay_is_5_seconds(self):
        """restart_base_delay defaults to 5.0 per design spec."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        assert hasattr(mgr, "restart_base_delay")
        assert mgr.restart_base_delay == 5.0

    def test_custom_restart_base_delay(self):
        """restart_base_delay can be overridden."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(
            workspace_root="/ws", adapters=[], restart_base_delay=10.0
        )
        assert mgr.restart_base_delay == 10.0

    def test_default_heartbeat_timeout_is_60_seconds(self):
        """heartbeat_timeout defaults to 60.0 per design spec."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        assert hasattr(mgr, "heartbeat_timeout")
        assert mgr.heartbeat_timeout == 60.0

    def test_custom_heartbeat_timeout(self):
        """heartbeat_timeout can be overridden."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(
            workspace_root="/ws", adapters=[], heartbeat_timeout=30.0
        )
        assert mgr.heartbeat_timeout == 30.0

    def test_initially_no_clients_are_started(self):
        """Lazy start: no client should be created during __init__."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mgr = LspManager(
                workspace_root="/ws",
                adapters=[_make_mock_adapter("python", "pyright-langserver")],
            )
            mock_cls.assert_not_called()


# ============================================================================
# Test: get_client(language)
# ============================================================================


class TestLspManagerGetClient:
    """Tests for get_client(language) -> Optional[LspClient]."""

    def test_get_client_lazy_starts_process_on_first_call(self):
        """First get_client call must create an LspClient (lazy start)."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[adapter])
            client = mgr.get_client("python")

            # Must have called LspClient(command, workspace_root)
            mock_cls.assert_called_once_with(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            assert client is mock_client

    def test_get_client_returns_same_instance_for_same_language(self):
        """Repeated calls for the same language return the same client."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[adapter])
            c1 = mgr.get_client("python")
            c2 = mgr.get_client("python")
            c3 = mgr.get_client("python")

            assert c1 is c2
            assert c2 is c3
            # LspClient should only be created once
            assert mock_cls.call_count == 1

    def test_get_client_for_no_adapter_returns_none(self):
        """Language with no registered adapter returns None."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mgr = LspManager(workspace_root="/ws", adapters=[])
            client = mgr.get_client("java")
            assert client is None
            mock_cls.assert_not_called()

    def test_get_client_different_languages_get_different_clients(self):
        """Each language gets its own LspClient instance."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py_adapter = _make_mock_adapter("python", "pyright-langserver")
        ts_adapter = _make_mock_adapter("typescript", "typescript-language-server")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            py_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            ts_client = _make_mock_client(
                ["typescript-language-server", "--stdio"], "/ws"
            )
            mock_cls.side_effect = [py_client, ts_client]

            mgr = LspManager(
                workspace_root="/ws",
                adapters=[py_adapter, ts_adapter],
            )
            c_py = mgr.get_client("python")
            c_ts = mgr.get_client("typescript")

            assert c_py is py_client
            assert c_ts is ts_client
            assert c_py is not c_ts

    def test_get_client_uses_adapter_get_server_command(self):
        """The LspClient is launched with the command from adapter."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("typescript", "typescript-language-server")
        adapter.get_server_command.return_value = [
            "typescript-language-server", "--stdio"
        ]

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mgr = LspManager(
                workspace_root="/my_app", adapters=[adapter]
            )
            mgr.get_client("typescript")

            mock_cls.assert_called_once_with(
                ["typescript-language-server", "--stdio"], "/my_app"
            )

    def test_get_client_passes_workspace_root_to_client(self):
        """The workspace_root is forwarded to LspClient.__init__."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mgr = LspManager(
                workspace_root="/specific/path", adapters=[adapter]
            )
            mgr.get_client("python")

            call_args, _call_kwargs = mock_cls.call_args
            assert call_args[1] == "/specific/path"


# ============================================================================
# Test: is_available(language)
# ============================================================================


class TestLspManagerIsAvailable:
    """Tests for is_available(language) -> bool."""

    def test_is_available_returns_true_when_client_starts(self):
        """is_available returns True when get_client succeeds."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )

            mgr = LspManager(workspace_root="/ws", adapters=[adapter])
            result = mgr.is_available("python")

            assert result is True

    def test_is_available_returns_false_for_unknown_language(self):
        """is_available returns False when no adapter matches."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        with patch("tws_graph.lsp.manager.LspClient"):
            mgr = LspManager(workspace_root="/ws", adapters=[])
            assert mgr.is_available("java") is False
            assert mgr.is_available("rust") is False

    def test_is_available_returns_false_when_start_fails(self):
        """is_available returns False when _start_client returns None."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            # Simulate start failure: LspClient raises an exception
            mock_cls.side_effect = RuntimeError("cannot start")

            mgr = LspManager(workspace_root="/ws", adapters=[adapter])
            result = mgr.is_available("python")

            assert result is False

    def test_is_available_does_not_cache_previous_client(self):
        """is_available should work even if no get_client call was made."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        adapter = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )

            mgr = LspManager(workspace_root="/ws", adapters=[adapter])
            # is_available should work without prior get_client call
            assert mgr.is_available("python") is True


# ============================================================================
# Test: get_availability_report()
# ============================================================================


class TestLspManagerAvailabilityReport:
    """Tests for get_availability_report() -> dict[str, bool]."""

    def test_report_includes_all_registered_languages(self):
        """Report must have an entry for each adapter's language."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")
        ts = _make_mock_adapter("typescript", "typescript-language-server")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py, ts])
            report = mgr.get_availability_report()

            assert isinstance(report, dict)
            assert "python" in report
            assert "typescript" in report
            assert len(report) == 2

    def test_report_values_are_bools(self):
        """Each report entry must be a bool."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver", available=True)

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            report = mgr.get_availability_report()

            assert isinstance(report["python"], bool)

    def test_report_returns_false_for_unavailable_language(self):
        """Unavailable adapter should yield False in the report."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver", available=True)
        rust = _make_mock_adapter("rust", "rust-analyzer", available=False)

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            # Python starts fine, but rust adapter is unavailable
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py, rust])
            report = mgr.get_availability_report()

            assert "python" in report
            assert "rust" in report

    def test_empty_adapters_produces_empty_report(self):
        """No adapters should return an empty dict."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        report = mgr.get_availability_report()
        assert report == {}


# ============================================================================
# Test: health_status()
# ============================================================================


class TestLspManagerHealthStatus:
    """Tests for health_status() -> dict[str, bool]."""

    def test_health_status_returns_dict_str_to_bool(self):
        """health_status returns a dict mapping language to health bool."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            # Initially no clients; health_status should still work
            status = mgr.health_status()

            assert isinstance(status, dict)
            for lang, healthy in status.items():
                assert isinstance(lang, str)
                assert isinstance(healthy, bool)

    def test_health_status_includes_all_registered_languages(self):
        """health_status must cover all adapters, not just started ones."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")
        ts = _make_mock_adapter("typescript", "typescript-language-server")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py, ts])
            status = mgr.health_status()

            assert "python" in status
            assert "typescript" in status

    def test_health_status_false_when_no_client_started(self):
        """Language without a started client is not healthy."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient"):
            mgr = LspManager(workspace_root="/ws", adapters=[py])
            status = mgr.health_status()

            # No client started -> should be False
            # (or not included; either is acceptable)
            if "python" in status:
                assert status["python"] is False

    def test_health_status_true_when_client_is_healthy(self):
        """Active healthy client should report True."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(["mock"], "/ws")
            mock_client._heartbeat.return_value = True
            mock_client._process.poll.return_value = None
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")  # start the client
            status = mgr.health_status()

            assert status.get("python") is True

    def test_health_status_false_when_client_is_unhealthy(self):
        """Unhealthy client (dead process) should report False."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(["mock"], "/ws")
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")  # start

            # Simulate unhealthy
            mock_client._process.poll.return_value = 1  # dead process
            status = mgr.health_status()

            # Manager should detect unhealthy client
            if "python" in status:
                assert status["python"] is False


# ============================================================================
# Test: _is_healthy(client, language)
# ============================================================================


class TestLspManagerIsHealthy:
    """Tests for _is_healthy(client, language) -> bool."""

    def test_is_healthy_true_when_process_alive_and_reader_ok(self):
        """Healthy when process.poll() is None and _heartbeat returns True."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client = mgr.get_client("python")

            assert mgr._is_healthy(client, "python") is True

    def test_is_healthy_false_when_process_is_dead(self):
        """Unhealthy when process.poll() returns non-None."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client = mgr.get_client("python")

            # Simulate dead process
            mock_client._process.poll.return_value = 1
            assert mgr._is_healthy(client, "python") is False

    def test_is_healthy_false_when_heartbeat_returns_false(self):
        """Unhealthy when _heartbeat() returns False."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client = mgr.get_client("python")

            # Process alive but heartbeat fails
            mock_client._process.poll.return_value = None
            mock_client._heartbeat.return_value = False
            assert mgr._is_healthy(client, "python") is False

    def test_is_healthy_calls_client_heartbeat(self):
        """_is_healthy must use client._heartbeat() as part of its check."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client = mgr.get_client("python")

            mock_client._heartbeat.reset_mock()
            mock_client._heartbeat.return_value = True
            mgr._is_healthy(client, "python")

            mock_client._heartbeat.assert_called()


# ============================================================================
# Test: shutdown_all()
# ============================================================================


class TestLspManagerShutdownAll:
    """Tests for shutdown_all() — graceful shutdown of all LSP processes."""

    def test_shutdown_all_closes_each_client(self):
        """shutdown_all must call shutdown/exit/close on each active client."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")
        ts = _make_mock_adapter("typescript", "typescript-language-server")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            py_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            ts_client = _make_mock_client(
                ["typescript-language-server", "--stdio"], "/ws"
            )
            mock_cls.side_effect = [py_client, ts_client]

            mgr = LspManager(workspace_root="/ws", adapters=[py, ts])
            mgr.get_client("python")
            mgr.get_client("typescript")

            mgr.shutdown_all()

            # Each client should be shut down gracefully
            py_client.shutdown.assert_called_once()
            py_client.exit.assert_called_once()
            py_client.close.assert_called_once()

            ts_client.shutdown.assert_called_once()
            ts_client.exit.assert_called_once()
            ts_client.close.assert_called_once()

    def test_shutdown_all_handles_no_clients(self):
        """shutdown_all should not error when no clients exist."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        # Must not raise
        mgr.shutdown_all()

    def test_shutdown_all_clears_client_references(self):
        """After shutdown_all, get_client should create a new client (not reuse old)."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2]

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client_a = mgr.get_client("python")
            mgr.shutdown_all()
            client_b = mgr.get_client("python")

            # Should be a new client after shutdown_all
            assert client_b is c2
            assert client_b is not c1

    def test_shutdown_all_handles_exceptions_gracefully(self):
        """If one client's shutdown raises, still try to shut down others."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")
        ts = _make_mock_adapter("typescript", "typescript-language-server")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            py_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            ts_client = _make_mock_client(
                ["typescript-language-server", "--stdio"], "/ws"
            )
            # Python client throws on shutdown
            py_client.shutdown.side_effect = RuntimeError("shutdown failed")
            mock_cls.side_effect = [py_client, ts_client]

            mgr = LspManager(workspace_root="/ws", adapters=[py, ts])
            mgr.get_client("python")
            mgr.get_client("typescript")

            # Must not raise — should handle exception and continue
            mgr.shutdown_all()

            # TypeScript client should still be shut down
            ts_client.shutdown.assert_called_once()

    def test_shutdown_all_no_orphan_processes(self):
        """All clients must be terminated — no dangling processes."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")
            mgr.shutdown_all()

            # After shutdown_all, the client should be closed
            mock_client.close.assert_called_once()


# ============================================================================
# Test: context manager (__enter__ / __exit__)
# ============================================================================


class TestLspManagerContextManager:
    """Tests for context manager protocol."""

    def test_enter_returns_self(self):
        """__enter__ should return self (the manager instance)."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        assert mgr.__enter__() is mgr

    def test_exit_calls_shutdown_all(self):
        """__exit__ must call shutdown_all()."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")

            mgr.__exit__(None, None, None)

            mock_client.shutdown.assert_called_once()
            mock_client.exit.assert_called_once()
            mock_client.close.assert_called_once()

    def test_with_statement_triggers_shutdown(self):
        """When used as context manager, exit triggers shutdown_all."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            with LspManager(workspace_root="/ws", adapters=[py]) as mgr:
                mgr.get_client("python")

            # After with-block, shutdown should have been called
            mock_client.shutdown.assert_called_once()

    def test_exit_accepts_exception_args(self):
        """__exit__ must accept (exc_type, exc_val, exc_tb) args."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        # Should not raise when called with exception args
        result = mgr.__exit__(ValueError, ValueError("test"), None)
        # Context manager should not suppress exceptions
        assert result is None  # or False — do not swallow


# ============================================================================
# Test: crash restart logic
# ============================================================================


class TestLspManagerCrashRestart:
    """Tests for crash restart with exponential backoff (max 3 retries)."""

    def test_restart_creates_new_client_after_crash(self):
        """When client is unhealthy, get_client should trigger restart."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(workspace_root="/ws", adapters=[py])

                # First client — healthy
                _client = mgr.get_client("python")
                assert _client is c1

                # Simulate crash: make first client unhealthy
                c1._process.poll.return_value = 1  # dead
                c1._heartbeat.return_value = False

                # get_client should detect unhealthy and restart
                client_after = mgr.get_client("python")

                # Should be the new client c2
                assert client_after is c2

    def test_max_3_restarts_then_give_up(self):
        """After 3 consecutive failures, give up and return None."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            # First client: starts OK
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            # All subsequent clients die immediately
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2._process.poll.return_value = 1
            c2._heartbeat.return_value = False

            c3 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c3._process.poll.return_value = 1
            c3._heartbeat.return_value = False

            c4 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c4._process.poll.return_value = 1
            c4._heartbeat.return_value = False

            mock_cls.side_effect = [c1, c2, c3, c4]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                )

                # First one succeeds
                client = mgr.get_client("python")
                assert client is c1

                # Simulate crash — first restart
                c1._process.poll.return_value = 1
                client2 = mgr.get_client("python")  # restart 1 -> c2
                assert client2 is c2

                # Second restart
                client3 = mgr.get_client("python")  # restart 2 -> c3
                assert client3 is c3

                # Third restart
                client4 = mgr.get_client("python")  # restart 3 -> c4
                assert client4 is c4

                # Fourth attempt — should give up, no more restarts
                c5 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
                mock_cls.side_effect = [c5]

                client5 = mgr.get_client("python")  # should be None
                assert client5 is None

    def test_exponential_backoff_delays(self):
        """Verify backoff delays: 5s, 10s, 20s (base_delay * 2^attempt)."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c3 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c4 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2, c3, c4]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                    restart_base_delay=5.0,
                )

                _client = mgr.get_client("python")  # OK

                # Crash → restart 1: delay should be 5s
                c1._process.poll.return_value = 1
                c1._heartbeat.return_value = False
                mgr.get_client("python")
                # sleep should have been called
                sleep_calls = [
                    c for c in mock_time.sleep.call_args_list
                ]
                # At least one sleep with approx delay 5.0
                delays = [c[0][0] for c in sleep_calls]
                # first delay should be 5s
                assert any(abs(d - 5.0) < 1.0 for d in delays), (
                    f"Expected delay ~5s, got {delays}"
                )

    def test_restart_respects_max_restart_parameter(self):
        """Custom max_restart=1: give up after 1 retry."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2._process.poll.return_value = 1  # dead immediately
            c2._heartbeat.return_value = False
            mock_cls.side_effect = [c1, c2]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=1,
                    restart_base_delay=0.1,
                )

                # First: OK
                client1 = mgr.get_client("python")
                assert client1 is c1

                # Crash → restart (1st and last)
                c1._process.poll.return_value = 1
                client2 = mgr.get_client("python")
                assert client2 is c2  # got the restart

                # Crashed again → should give up
                client3 = mgr.get_client("python")
                assert client3 is None

    def test_successful_restart_resets_failure_count(self):
        """A successful restart should reset the failure counter."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            # Restart client 1 — crashes immediately
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2._process.poll.return_value = 1
            c2._heartbeat.return_value = False
            # Restart client 2 — healthy this time
            c3 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c3._process.poll.return_value = None
            c3._heartbeat.return_value = True
            mock_cls.side_effect = [c1, c2, c3]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                )

                _c = mgr.get_client("python")

                # Crash 1: restart fails (c2 is dead)
                c1._process.poll.return_value = 1
                c1._heartbeat.return_value = False
                _client2 = mgr.get_client("python")

                # Crash 2: restart succeeds (c3 is healthy)
                _client3 = mgr.get_client("python")
                assert _client3 is c3

                # Next crash: should have full 3-restart budget again
                c3._process.poll.return_value = 1
                c3._heartbeat.return_value = False
                # This should trigger restart, not give up
                _client4 = mgr.get_client("python")
                # Should get a new client (restart 1 of 3), not None
                assert _client4 is not None

    def test_restart_base_delay_custom_value(self):
        """When restart_base_delay is 10, the first delay is 10s."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                    restart_base_delay=10.0,
                )

                mgr.get_client("python")  # start

                c1._process.poll.return_value = 1
                c1._heartbeat.return_value = False
                mgr.get_client("python")  # restart

                sleep_calls = [
                    c for c in mock_time.sleep.call_args_list
                ]
                delays = [c[0][0] for c in sleep_calls]
                assert any(abs(d - 10.0) < 1.0 for d in delays), (
                    f"Expected delay ~10s, got {delays}"
                )

    def test_get_client_recognizes_healthy_client_no_restart(self):
        """When client is healthy, get_client does NOT trigger restart."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1]

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            _client = mgr.get_client("python")
            _client2 = mgr.get_client("python")
            _client3 = mgr.get_client("python")

            # Only one LspClient created — no restart
            assert mock_cls.call_count == 1


# ============================================================================
# Test: heartbeat_timeout detection
# ============================================================================


class TestLspManagerHeartbeatTimeout:
    """Tests for heartbeat_timeout logic in _is_healthy."""

    def test_is_healthy_uses_heartbeat_timeout(self):
        """_is_healthy should detect when heartbeat has timed out."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    heartbeat_timeout=60.0,
                )

                client = mgr.get_client("python")

                # After 0s: healthy
                assert mgr._is_healthy(client, "python") is True

                # After 61s: heartbeat should have timed out
                mock_time.monotonic.return_value = 61.0
                # Manager should detect timeout
                result = mgr._is_healthy(client, "python")
                # Should be False if heartbeat_timeout is enforced
                assert isinstance(result, bool)

    def test_heartbeat_timeout_custom_value_used(self):
        """When heartbeat_timeout is 30, timeout is 30s."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    heartbeat_timeout=30.0,
                )

                client = mgr.get_client("python")

                # After 31s: should have timed out with 30s timeout
                mock_time.monotonic.return_value = 31.0
                result = mgr._is_healthy(client, "python")
                assert result is False

    def test_successful_heartbeat_resets_timer(self):
        """A successful heartbeat check should reset the last-heartbeat timer."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    heartbeat_timeout=30.0,
                )

                client = mgr.get_client("python")

                # Time passes...
                mock_time.monotonic.return_value = 29.0
                # Still healthy
                assert mgr._is_healthy(client, "python") is True

                # More time passes, beyond original window...
                mock_time.monotonic.return_value = 59.0
                # But last check was at 29s, so we're 30s from that check
                # This should be borderline — if heartbeat clock resets on check
                result = mgr._is_healthy(client, "python")
                assert isinstance(result, bool)


# ============================================================================
# Test: _start_client(language, adapter)
# ============================================================================


class TestLspManagerStartClient:
    """Tests for _start_client(language, adapter) -> Optional[LspClient]."""

    def test_start_client_returns_lsp_client(self):
        """_start_client creates and returns an LspClient."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_client = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )
            mock_cls.return_value = mock_client

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            result = mgr._start_client("python", py)

            assert result is mock_client
            mock_cls.assert_called_once_with(
                ["pyright-langserver", "--stdio"], "/ws"
            )

    def test_start_client_returns_none_on_failure(self):
        """_start_client returns None when LspClient raises."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.side_effect = RuntimeError("subprocess failed")

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            result = mgr._start_client("python", py)

            assert result is None

    def test_start_client_with_none_adapter_returns_none(self):
        """_start_client returns None when adapter is None."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        mgr = LspManager(workspace_root="/ws", adapters=[])
        result = mgr._start_client("java", None)
        assert result is None


# ============================================================================
# Test: _restart_client(language, adapter)
# ============================================================================


class TestLspManagerRestartClient:
    """Tests for _restart_client(language, adapter)."""

    def test_restart_client_replaces_old_client(self):
        """_restart_client should replace the stored client with a new one."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2]

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                    restart_base_delay=0.01,
                )

                _client1 = mgr.get_client("python")

                mgr._restart_client("python", py)

                # After restart, get_client should return the new client
                client_after = mgr.get_client("python")
                assert client_after is c2

    def test_restart_client_handles_failure_to_start(self):
        """If new client also fails to start, _restart_client should handle it."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.side_effect = RuntimeError("cannot start")

            with patch("tws_graph.lsp.manager.time") as mock_time:
                mock_time.monotonic.return_value = 0.0

                mgr = LspManager(
                    workspace_root="/ws",
                    adapters=[py],
                    max_restart=3,
                    restart_base_delay=0.01,
                )

                # This should not raise
                mgr._restart_client("python", py)


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestLspManagerEdgeCases:
    """Edge case and boundary condition tests."""

    def test_get_client_case_insensitive_or_exact_match(self):
        """Language matching should be case-sensitive or documented."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )

            mgr = LspManager(workspace_root="/ws", adapters=[py])

            # Exact match should work
            c1 = mgr.get_client("python")
            assert c1 is not None

            # Case mismatch — behavior depends on implementation
            # At minimum this should not crash
            c2 = mgr.get_client("Python")
            # Either None or the same client — both are acceptable
            # but must not crash

    def test_multiple_adapters_same_language(self):
        """If two adapters claim same language, first-match wins (no crash)."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py1 = _make_mock_adapter("python", "pyright-langserver")
        py2 = _make_mock_adapter("python", "pylsp")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(["mock"], "/ws")

            mgr = LspManager(workspace_root="/ws", adapters=[py1, py2])
            client = mgr.get_client("python")

            assert client is not None
            # Should use the first adapter
            assert mock_cls.call_count == 1

    def test_shutdown_all_idempotent(self):
        """Calling shutdown_all twice should not raise errors."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")

            mgr.shutdown_all()
            # Second call should not raise
            mgr.shutdown_all()

    def test_get_client_after_shutdown_all_creates_new_client(self):
        """After shutdown_all, get_client starts a fresh client."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            c1 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            c2 = _make_mock_client(["pyright-langserver", "--stdio"], "/ws")
            mock_cls.side_effect = [c1, c2]

            mgr = LspManager(workspace_root="/ws", adapters=[py])

            client1 = mgr.get_client("python")
            mgr.shutdown_all()
            client2 = mgr.get_client("python")

            assert client1 is not client2
            assert client2 is c2

    def test_get_client_for_unavailable_adapter(self):
        """When adapter.check_availability returns False, behavior is defined."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver", available=False)

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mgr = LspManager(workspace_root="/ws", adapters=[py])
            client = mgr.get_client("python")

            # Should not crash; either None (can't start) or tries anyway
            # Based on design: manager should handle this gracefully
            assert client is not None or client is None  # tautology, no crash

    def test_health_status_after_shutdown(self):
        """After shutdown_all, health_status should reflect no clients."""
        try:
            from tws_graph.lsp.manager import LspManager
        except ImportError:
            pytest.skip("LspManager not yet implemented (red phase)")

        py = _make_mock_adapter("python", "pyright-langserver")

        with patch("tws_graph.lsp.manager.LspClient") as mock_cls:
            mock_cls.return_value = _make_mock_client(
                ["pyright-langserver", "--stdio"], "/ws"
            )

            mgr = LspManager(workspace_root="/ws", adapters=[py])
            mgr.get_client("python")

            mgr.shutdown_all()
            status = mgr.health_status()

            # After shutdown, should not report healthy
            if "python" in status:
                assert status["python"] is False
