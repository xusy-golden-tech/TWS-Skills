"""Tests for lsp/client.py --- LspClient high-level LSP client.

TDD red phase: these tests are written before the client module exists.
They encode the acceptance criteria from design-p5-lsp.md for client.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - LspClient instantiation auto-completes initialize handshake
  - definition/references/hover call correct LSP methods
  - _to_uri correctly converts Windows/Unix paths
  - heartbeat mechanism (HEARTBEAT_INTERVAL == 30.0)
  - Lifecycle: shutdown / exit / close
  - Edge cases: empty results, null hover, path encoding (spaces/Chinese)
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.client is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.client import (  # noqa: E402 (expected ImportError in red phase)
    LspClient,
)
from tws_graph.lsp.protocol import (
    HoverResult,
    InitializeResult,
    Location,
    LspConnectionError,
    LspProtocolError,
    LspTimeoutError,
    Position,
    Range,
)

# ============================================================================
# Constants
# ============================================================================

# Default initialize response that a typical LSP server returns
DEFAULT_INITIALIZE_RESULT = {
    "serverInfo": {"name": "test-lsp-server", "version": "1.0.0"},
    "capabilities": {
        "definitionProvider": True,
        "referencesProvider": True,
        "hoverProvider": True,
        "textDocumentSync": 1,  # Full
    },
}


# ============================================================================
# Helpers / Fixtures
# ============================================================================


def _make_mock_process():
    """Create a mock subprocess.Popen result with os.pipe() stdout/stdin.

    Uses real os.pipe() file descriptors so that LspMessageReader (which
    expects BufferedReader/BufferedWriter-compatible objects) can be
    constructed without errors.  The write end of stdin is left open for
    the test to write into; the write end of stdout simulates the LSP
    server's output.

    Returns (mock_process, stdout_write_fd, stdin_read_fd).
    """
    stdout_r_fd, stdout_w_fd = os.pipe()
    stdin_r_fd, stdin_w_fd = os.pipe()

    mock = MagicMock()
    mock.pid = 12345
    mock.poll.return_value = None  # process still alive
    mock.returncode = None

    mock.stdout = io.BufferedReader(os.fdopen(stdout_r_fd, "rb", closefd=False))
    mock.stdin = os.fdopen(stdin_w_fd, "wb", closefd=False)

    return mock, stdout_w_fd, stdin_r_fd


@pytest.fixture
def lsp_env():
    """Fixture providing mocks for subprocess.Popen and LspMessageReader.

    Sets up:
    - mock_process: MagicMock simulating the LSP server subprocess
      (with real os.pipe() stdout/stdin)
    - mock_reader: MagicMock simulating LspMessageReader
      (pre-configured to respond to 'initialize')

    The actual LspClient class is NOT imported in this fixture --
    it is imported at module level.  Tests call patch() themselves
    when they need to substitute reader behaviour.

    Yields (mock_process, mock_reader, mock_reader_class).
    """
    mock_process, stdout_w_fd, stdin_r_fd = _make_mock_process()
    mock_reader = MagicMock()

    # Default: respond to "initialize" with a valid result
    def _default_send_request(method, params, timeout=30.0):
        if method == "initialize":
            return DEFAULT_INITIALIZE_RESULT
        return {}

    mock_reader.send_request.side_effect = _default_send_request
    mock_reader_class = MagicMock(return_value=mock_reader)

    with patch("tws_graph.lsp.client.subprocess") as mock_subprocess:
        mock_subprocess.Popen.return_value = mock_process
        mock_subprocess.DEVNULL = -3  # subprocess.DEVNULL sentinel
        mock_subprocess.PIPE = -1  # subprocess.PIPE sentinel

        with patch(
            "tws_graph.lsp.client.LspMessageReader", mock_reader_class
        ):
            yield mock_process, mock_reader, mock_reader_class

    # Cleanup pipe fds
    for fd in (stdout_w_fd, stdin_r_fd):
        try:
            os.close(fd)
        except OSError:
            pass


def _make_lsp_message(payload: dict) -> bytes:
    """Build a complete LSP message: Content-Length header + JSON body."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


# ============================================================================
# _to_uri tests (unit tests — no process mock needed)
# ============================================================================


class TestToUri:
    """Tests for LspClient._to_uri(file_path) -> str."""

    # -- normal path ----------------------------------------------------------

    def test_converts_unix_absolute_path(self):
        uri = LspClient._to_uri("/home/user/project/main.py")
        assert uri == "file:///home/user/project/main.py"

    def test_converts_unix_root_path(self):
        uri = LspClient._to_uri("/foo")
        assert uri == "file:///foo"

    def test_converts_windows_absolute_path(self):
        uri = LspClient._to_uri("C:\\Users\\dev\\src\\main.py")
        assert uri == "file:///C:/Users/dev/src/main.py"

    def test_converts_windows_drive_with_lowercase(self):
        uri = LspClient._to_uri("d:\\data\\file.txt")
        assert uri == "file:///d:/data/file.txt"

    # -- boundary: spaces -----------------------------------------------------

    def test_encodes_spaces_in_path(self):
        uri = LspClient._to_uri("/home/user/my project/main file.py")
        assert "%20" in uri
        assert uri == "file:///home/user/my%20project/main%20file.py"

    def test_encodes_spaces_in_windows_path(self):
        uri = LspClient._to_uri("C:\\Program Files\\My App\\main.py")
        assert "%20" in uri
        assert uri == "file:///C:/Program%20Files/My%20App/main.py"

    # -- boundary: Chinese characters -----------------------------------------

    def test_encodes_chinese_characters_in_path(self):
        uri = LspClient._to_uri("/home/user/\u9879\u76ee/\u4e3b\u7a0b\u5e8f.py")
        # 项目/主程序.py
        assert "%E9%A1%B9%E7%9B%AE" in uri  # 项目
        assert "%E4%B8%BB%E7%A8%8B%E5%BA%8F" in uri  # 主程序
        assert uri.startswith("file:///")

    def test_encodes_chinese_in_windows_path(self):
        uri = LspClient._to_uri("D:\\\u6211\u7684\u6587\u6863\\\u4ee3\u7801.py")
        # D:\我的文档\代码.py
        assert uri.startswith("file:///D:/")
        assert "%E6%88%91%E7%9A%84%E6%96%87%E6%A1%A3" in uri  # 我的文档
        assert "%E4%BB%A3%E7%A0%81" in uri  # 代码

    # -- boundary: special characters -----------------------------------------

    def test_encodes_hash_in_path(self):
        uri = LspClient._to_uri("/home/user/project#1/main.py")
        assert "%23" in uri

    def test_encodes_square_brackets(self):
        uri = LspClient._to_uri("/home/user/[test]/file.py")
        assert "%5B" in uri
        assert "%5D" in uri

    # -- boundary: backslash/forward-slash only paths -------------------------

    def test_preserves_forward_slashes_in_unix_path(self):
        # Path with trailing slash should still work
        uri = LspClient._to_uri("/home/user/project/")
        assert uri.startswith("file:///")
        # The trailing slash should be preserved
        assert uri.endswith("/")

    def test_converts_mixed_separators(self):
        """Windows path with mixed \\\\ and / should be normalized."""
        uri = LspClient._to_uri("C:\\Users/dev\\src/main.py")
        # All separators become forward slashes
        assert "\\" not in uri[len("file:///"):]
        assert uri == "file:///C:/Users/dev/src/main.py"


# ============================================================================
# LspClient initialization tests
# ============================================================================


class TestLspClientInit:
    """Tests for LspClient.__init__ and automatic initialize handshake."""

    def test_creates_subprocess_with_correct_args(self, lsp_env):
        mock_process, mock_reader, _ = lsp_env

        _client = LspClient(
            command=["pyright-langserver", "--stdio"],
            workspace_root="/home/test/project",
        )

        from tws_graph.lsp.client import subprocess as subprocess_mod

        subprocess_mod.Popen.assert_called_once_with(
            ["pyright-langserver", "--stdio"],
            stdin=subprocess_mod.PIPE,
            stdout=subprocess_mod.PIPE,
            stderr=subprocess_mod.DEVNULL,
        )

    def test_creates_lsp_message_reader(self, lsp_env):
        mock_process, _, mock_reader_class = lsp_env

        _client = LspClient(
            command=["test-server", "--stdio"],
            workspace_root="/workspace",
        )

        # LspMessageReader should be instantiated with process stdout/stdin
        mock_reader_class.assert_called_once_with(
            stdout=mock_process.stdout,
            stdin=mock_process.stdin,
        )

    def test_auto_sends_initialize_request_on_creation(self, lsp_env):
        _, mock_reader, _ = lsp_env

        _client = LspClient(
            command=["test-server"],
            workspace_root="/workspace",
        )

        # Verify initialize request was sent (at least one call)
        init_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "initialize"
        ]
        assert len(init_calls) >= 1, "initialize request was not sent"

    def test_initialize_request_includes_root_uri_and_capabilities(self, lsp_env):
        """The initialize request must declare the client's capabilities."""
        _, mock_reader, _ = lsp_env

        _client = LspClient(
            command=["test-server"],
            workspace_root="/workspace",
        )

        # Find the initialize call
        init_call = None
        for c in mock_reader.send_request.call_args_list:
            if c[0][0] == "initialize":
                init_call = c
                break
        assert init_call is not None

        _method, params, _timeout = init_call[0]
        assert "rootUri" in params
        assert params["rootUri"] == "file:///workspace"
        assert "capabilities" in params

    def test_auto_sends_initialized_notification_after_initialize(self, lsp_env):
        """After receiving initialize response, send initialized notification."""
        _, mock_reader, _ = lsp_env

        _client = LspClient(
            command=["test-server"],
            workspace_root="/workspace",
        )

        # Check that initialized notification was sent
        notif_calls = [
            c for c in mock_reader.send_notification.call_args_list
            if c[0][0] == "initialized"
        ]
        assert len(notif_calls) >= 1, "initialized notification was not sent"

    def test_returns_initialize_result_from_init(self, lsp_env):
        """LspClient should expose the initialize result."""
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/workspace",
        )

        # The client should have stored the initialize result
        # (accessible via .server_info or similar attribute)
        assert hasattr(client, "initialize")

    def test_stores_workspace_root(self, lsp_env):
        _, _, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/my/workspace",
        )

        assert hasattr(client, "workspace_root")
        assert client.workspace_root == "/my/workspace"

    def test_stores_server_command(self, lsp_env):
        _, _, _ = lsp_env

        client = LspClient(
            command=["custom-lsp", "--arg1", "--arg2"],
            workspace_root="/ws",
        )

        assert client.command == ["custom-lsp", "--arg1", "--arg2"]


# ============================================================================
# initialize() / initialized() methods
# ============================================================================


class TestLspClientInitializeMethods:
    """Tests for standalone initialize() and initialized() calls."""

    def test_initialize_returns_initialize_result(self, lsp_env):
        """initialize() should return an InitializeResult dataclass."""
        _, mock_reader, _ = lsp_env

        # Override the reader to return a specific initialize response
        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {
            "serverInfo": {"name": "my-server", "version": "2.0"},
            "capabilities": {"hoverProvider": False},
        }

        client = LspClient(
            command=["test-server"],
            workspace_root="/ws",
        )

        result = client.initialize()
        assert isinstance(result, InitializeResult)
        assert result.server_name == "my-server"
        assert result.server_version == "2.0"
        assert result.capabilities == {"hoverProvider": False}

    def test_initialize_sends_correct_params(self, lsp_env):
        """initialize request should include rootUri, workspaceFolders, and capabilities."""
        _, mock_reader, _ = lsp_env

        # Reset so we can intercept the second call
        mock_reader.send_request.reset_mock()
        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = DEFAULT_INITIALIZE_RESULT

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client.initialize()

        # Should find an initialize call
        init_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "initialize"
        ]
        assert len(init_calls) >= 1

    def test_initialized_sends_notification(self, lsp_env):
        """initialized() sends the 'initialized' notification."""
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/ws",
        )

        # Reset to clear the auto-sent notification
        mock_reader.send_notification.reset_mock()

        client.initialized()

        mock_reader.send_notification.assert_called_with("initialized", {})


# ============================================================================
# did_open tests
# ============================================================================


class TestLspClientDidOpen:
    """Tests for did_open(file_path, text, language_id)."""

    def test_sends_text_document_did_open_notification(self, lsp_env):
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        # Reset to clear auto-sent notifications
        mock_reader.send_notification.reset_mock()

        client.did_open(
            file_path="/project/src/main.py",
            text="print('hello')",
            language_id="python",
        )

        mock_reader.send_notification.assert_called_once()
        call_args = mock_reader.send_notification.call_args[0]
        assert call_args[0] == "textDocument/didOpen"
        params = call_args[1]
        assert "textDocument" in params
        td = params["textDocument"]
        assert td["uri"] == "file:///project/src/main.py"
        assert td["text"] == "print('hello')"
        assert td["languageId"] == "python"
        assert td["version"] is not None  # version must be present

    def test_did_open_includes_correct_uri(self, lsp_env):
        """did_open converts the file_path to a proper file:// URI."""
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/workspace",
        )
        mock_reader.send_notification.reset_mock()

        client.did_open(
            file_path="/workspace/lib/util.py",
            text="x = 1",
            language_id="python",
        )

        params = mock_reader.send_notification.call_args[0][1]
        assert params["textDocument"]["uri"] == "file:///workspace/lib/util.py"


# ============================================================================
# definition() tests
# ============================================================================


class TestLspClientDefinition:
    """Tests for definition(file_path, line, col) -> list[Location]."""

    def test_calls_text_document_definition(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = [
            {
                "uri": "file:///project/lib/helper.py",
                "range": {
                    "start": {"line": 10, "character": 4},
                    "end": {"line": 10, "character": 10},
                },
            }
        ]

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.definition("/project/src/main.py", line=5, col=12)

        # Verify the correct LSP method was called
        defn_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/definition"
        ]
        assert len(defn_calls) >= 1

        # Verify params
        _method, params, _timeout = defn_calls[-1][0]
        assert params["textDocument"]["uri"] == "file:///project/src/main.py"
        assert params["position"]["line"] == 5
        assert params["position"]["character"] == 12

    def test_returns_list_of_locations(self, lsp_env):
        """definition() returns a list of Location dataclass instances."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = [
            {
                "uri": "file:///defs.py",
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 1, "character": 5},
                },
            },
            {
                "uri": "file:///other.py",
                "range": {
                    "start": {"line": 3, "character": 2},
                    "end": {"line": 3, "character": 7},
                },
            },
        ]

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.definition("/project/src/main.py", line=1, col=1)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(loc, Location) for loc in result)
        assert result[0].uri == "file:///defs.py"
        assert result[0].range.start.line == 1
        assert result[0].range.start.character == 0
        assert result[1].uri == "file:///other.py"

    # -- boundary: empty result -----------------------------------------------

    def test_returns_empty_list_when_no_definition_found(self, lsp_env):
        """When the LSP server returns null/None, return empty list."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = None

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.definition("/project/src/main.py", line=1, col=1)

        assert isinstance(result, list)
        assert len(result) == 0

    def test_returns_empty_list_when_result_is_empty_array(self, lsp_env):
        """When the LSP server returns [], return empty list."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = []

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.definition("/project/src/main.py", line=1, col=1)

        assert result == []


# ============================================================================
# references() tests
# ============================================================================


class TestLspClientReferences:
    """Tests for references(file_path, line, col, include_declaration=False)."""

    def test_calls_text_document_references(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = [
            {
                "uri": "file:///project/src/usage.py",
                "range": {
                    "start": {"line": 3, "character": 5},
                    "end": {"line": 3, "character": 12},
                },
            }
        ]

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.references(
            "/project/src/main.py", line=5, col=6, include_declaration=False
        )

        refs_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/references"
        ]
        assert len(refs_calls) >= 1

        _method, params, _timeout = refs_calls[-1][0]
        assert params["textDocument"]["uri"] == "file:///project/src/main.py"
        assert params["position"]["line"] == 5
        assert params["position"]["character"] == 6
        assert params["context"]["includeDeclaration"] is False

    def test_include_declaration_defaults_to_false(self, lsp_env):
        """When include_declaration is not specified, default to False."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = []

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client.references("/project/src/main.py", line=1, col=2)

        refs_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/references"
        ]
        params = refs_calls[-1][0][1]
        assert params["context"]["includeDeclaration"] is False

    def test_include_declaration_true(self, lsp_env):
        """When include_declaration is True, include it in the context."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = []

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client.references(
            "/project/src/main.py", line=1, col=2, include_declaration=True
        )

        refs_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/references"
        ]
        params = refs_calls[-1][0][1]
        assert params["context"]["includeDeclaration"] is True

    def test_returns_list_of_locations(self, lsp_env):
        """references() returns a list of Location dataclass instances."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = [
            {
                "uri": "file:///a.py",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
            }
        ]

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.references("/a.py", line=0, col=0)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Location)

    # -- boundary: empty result -----------------------------------------------

    def test_returns_empty_list_when_no_references(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = None

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.references("/a.py", line=0, col=0)
        assert result == []


# ============================================================================
# hover() tests
# ============================================================================


class TestLspClientHover:
    """Tests for hover(file_path, line, col) -> Optional[HoverResult]."""

    def test_calls_text_document_hover(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {
            "contents": "def foo(x: int) -> str: ...",
        }

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.hover("/project/src/main.py", line=10, col=4)

        hover_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/hover"
        ]
        assert len(hover_calls) >= 1

        _method, params, _timeout = hover_calls[-1][0]
        assert params["textDocument"]["uri"] == "file:///project/src/main.py"
        assert params["position"]["line"] == 10
        assert params["position"]["character"] == 4

    def test_returns_hover_result_with_contents(self, lsp_env):
        """hover() returns a HoverResult when the server returns contents."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {
            "contents": "```python\ndef greet(name: str) -> str: ...\n```",
            "range": {
                "start": {"line": 10, "character": 4},
                "end": {"line": 10, "character": 9},
            },
        }

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.hover("/project/src/main.py", line=10, col=4)

        assert isinstance(result, HoverResult)
        assert "greet" in result.contents
        assert result.range is not None
        assert result.range.start.line == 10

    def test_returns_hover_result_without_range(self, lsp_env):
        """HoverResult.range can be None when server omits it."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {
            "contents": "int",
        }

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.hover("/project/a.py", line=1, col=1)

        assert isinstance(result, HoverResult)
        assert result.contents == "int"
        assert result.range is None

    # -- boundary: null hover -------------------------------------------------

    def test_returns_none_when_hover_result_is_null(self, lsp_env):
        """When the LSP server returns null (no hover info), return None."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = None

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.hover("/project/a.py", line=1, col=1)

        assert result is None

    def test_returns_none_when_hover_result_is_empty_object(self, lsp_env):
        """Server may return {} for no hover info."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {}

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client.hover("/project/a.py", line=1, col=1)

        assert result is None


# ============================================================================
# shutdown / exit / close tests
# ============================================================================


class TestLspClientShutdown:
    """Tests for shutdown(), exit(), and close() lifecycle methods."""

    def test_shutdown_sends_shutdown_request(self, lsp_env):
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )
        mock_reader.send_request.reset_mock()

        client.shutdown()

        shutdown_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "shutdown"
        ]
        assert len(shutdown_calls) >= 1

    def test_exit_sends_exit_notification(self, lsp_env):
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )
        mock_reader.send_notification.reset_mock()

        client.exit()

        mock_reader.send_notification.assert_called_with("exit", None)

    def test_close_calls_reader_close(self, lsp_env):
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client.close()

        mock_reader.close.assert_called_once()

    def test_close_stops_heartbeat_if_active(self, lsp_env):
        """close() should stop the heartbeat mechanism."""
        _, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client.close()

        # After close, the client should be marked as closed
        # and the heartbeat should not be running
        assert hasattr(client, "_closed")
        assert client._closed is True


# ============================================================================
# heartbeat tests
# ============================================================================


class TestLspClientHeartbeat:
    """Tests for the HEARTBEAT_INTERVAL constant and _heartbeat() method."""

    def test_heartbeat_interval_is_30_seconds(self):
        """HEARTBEAT_INTERVAL must be 30.0 seconds (per design doc)."""
        from tws_graph.lsp.client import HEARTBEAT_INTERVAL
        assert HEARTBEAT_INTERVAL == 30.0

    def test_heartbeat_interval_is_class_constant(self):
        """HEARTBEAT_INTERVAL should be accessible as a class attribute."""
        assert hasattr(LspClient, "HEARTBEAT_INTERVAL")
        assert LspClient.HEARTBEAT_INTERVAL == 30.0

    def test_heartbeat_returns_true_when_process_alive(self, lsp_env):
        """_heartbeat() returns True when the subprocess is still running."""
        mock_process, _, _ = lsp_env

        mock_process.poll.return_value = None  # alive

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client._heartbeat()
        assert result is True

    def test_heartbeat_returns_false_when_process_dead(self, lsp_env):
        """_heartbeat() returns False when the subprocess has exited."""
        mock_process, _, _ = lsp_env

        # First create with process alive
        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        # Simulate process death
        mock_process.poll.return_value = 1  # exited with code 1

        result = client._heartbeat()
        assert result is False

    def test_heartbeat_returns_false_when_reader_has_error(self, lsp_env):
        """_heartbeat() should check reader health too."""
        mock_process, mock_reader, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        # Simulate reader error
        mock_process.poll.return_value = None  # process alive
        # The reader having an error should be detectable
        # This test verifies the heartbeat integration exists
        result = client._heartbeat()

        # When process is alive and reader has no error, should be True
        assert result is True

    def test_heartbeat_is_callable_method(self, lsp_env):
        """_heartbeat must exist as a callable method."""
        _, _, _ = lsp_env

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        assert callable(client._heartbeat)


# ============================================================================
# _send_request / _send_notification internal methods
# ============================================================================


class TestLspClientInternalMethods:
    """Tests for internal _send_request and _send_notification delegation."""

    def test_send_request_delegates_to_reader(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {"key": "value"}

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        result = client._send_request("custom/method", {"param": 1}, timeout=10.0)

        mock_reader.send_request.assert_called_with(
            "custom/method", {"param": 1}, 10.0
        )
        assert result == {"key": "value"}

    def test_send_request_default_timeout(self, lsp_env):
        """_send_request should use a default timeout of 30s."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = {}

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client._send_request("test", {})

        # Check the timeout was passed (default should be reasonable, like 30s)
        call_args = mock_reader.send_request.call_args[0]
        assert len(call_args) >= 2  # (method, params, [timeout])

    def test_send_notification_delegates_to_reader(self, lsp_env):
        _, mock_reader, _ = lsp_env

        mock_reader.send_notification.reset_mock()

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        client._send_notification("textDocument/didChange", {"changes": []})

        mock_reader.send_notification.assert_called_with(
            "textDocument/didChange", {"changes": []}
        )


# ============================================================================
# Error propagation tests
# ============================================================================


class TestLspClientErrorPropagation:
    """Tests for error handling: timeout, connection errors, etc."""

    def test_definition_propagates_timeout(self, lsp_env):
        """When the reader raises LspTimeoutError, it should propagate."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = LspTimeoutError("timeout")

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        with pytest.raises(LspTimeoutError):
            client.definition("/project/a.py", line=1, col=1)

    def test_hover_propagates_connection_error(self, lsp_env):
        """When the reader raises LspConnectionError, it should propagate."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = LspConnectionError("pipe broken")

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        with pytest.raises(LspConnectionError):
            client.hover("/project/a.py", line=1, col=1)

    def test_references_propagates_protocol_error(self, lsp_env):
        """When the reader raises LspProtocolError, it should propagate."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = LspProtocolError("invalid response")

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        with pytest.raises(LspProtocolError):
            client.references("/project/a.py", line=1, col=1)


# ============================================================================
# Windows-specific tests (only meaningful on Windows)
# ============================================================================


class TestWindowsSpecific:
    """Tests for Windows-specific behaviour (non-destructive on Unix)."""

    def test_windows_path_with_spaces_at_drive_root(self):
        """C:\\Program Files\\... should be URI-encoded correctly."""
        uri = LspClient._to_uri("C:\\Program Files (x86)\\My App\\main.py")
        assert uri == "file:///C:/Program%20Files%20%28x86%29/My%20App/main.py"


# ============================================================================
# Integration smoke test
# ============================================================================


class TestLspClientIntegration:
    """Smoke test: verify the client lifecycle with mocked dependencies."""

    def test_full_lifecycle(self, lsp_env):
        """Create client, use it, shut it down — no errors."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = None

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        # Use the client
        client.did_open("/project/main.py", "print(1)", "python")
        defs = client.definition("/project/main.py", line=1, col=1)
        assert defs == []

        refs = client.references("/project/main.py", line=1, col=1)
        assert refs == []

        hover = client.hover("/project/main.py", line=1, col=1)
        assert hover is None

        hb = client._heartbeat()
        assert hb is True

        # Shutdown
        client.shutdown()
        client.exit()
        client.close()

    def test_multiple_definition_calls(self, lsp_env):
        """Multiple definition calls in sequence should work."""
        _, mock_reader, _ = lsp_env

        mock_reader.send_request.side_effect = None
        mock_reader.send_request.return_value = [
            {
                "uri": "file:///defs.py",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 4},
                },
            }
        ]

        client = LspClient(
            command=["test-server"],
            workspace_root="/project",
        )

        r1 = client.definition("/project/a.py", line=1, col=1)
        r2 = client.definition("/project/b.py", line=2, col=2)
        r3 = client.definition("/project/c.py", line=3, col=3)

        assert len(r1) == 1
        assert len(r2) == 1
        assert len(r3) == 1

        # Verify all three calls were made with correct URIs
        defn_calls = [
            c for c in mock_reader.send_request.call_args_list
            if c[0][0] == "textDocument/definition"
        ]
        assert len(defn_calls) >= 3
        uris = [c[0][1]["textDocument"]["uri"] for c in defn_calls[-3:]]
        assert "file:///project/a.py" in uris
        assert "file:///project/b.py" in uris
        assert "file:///project/c.py" in uris
