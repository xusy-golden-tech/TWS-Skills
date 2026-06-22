"""LSP client high-level abstraction.

Provides :class:`LspClient` which wraps a subprocess LSP server process
and :class:`LspMessageReader` to deliver convenient methods for common
LSP operations: initialize, didOpen, definition, references, hover, and
lifecycle management (shutdown/exit/close).
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional
from urllib.parse import quote

from tws_graph.lsp.protocol import (
    HoverResult,
    InitializeResult,
    Location,
    LspConnectionError,
    LspMessageReader,
    Position,
    Range,
)

logger = logging.getLogger(__name__)

#: Heartbeat check interval in seconds.  Exported at module level so
#: that consumers (e.g. LspManager) can import it directly.
HEARTBEAT_INTERVAL: float = 30.0


class LspClient:
    """High-level LSP client that communicates with a language server via JSON-RPC.

    Starts a subprocess for an LSP server binary, wraps the process's
    stdio pipes with an :class:`LspMessageReader`, and automatically
    completes the initialize handshake (``initialize`` request +
    ``initialized`` notification) during :meth:`__init__`.

    All public methods are safe to call from any thread.
    """

    #: Interval (seconds) between heartbeat checks.
    HEARTBEAT_INTERVAL: float = HEARTBEAT_INTERVAL

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, command: list[str], workspace_root: str) -> None:
        """Start an LSP server process and complete the initialize handshake.

        Args:
            command: The command line to launch the LSP server (e.g.
                ``["pyright-langserver", "--stdio"]``).
            workspace_root: Absolute path to the project root served to
                the LSP server as ``rootUri``.
        """
        self.command = command
        self.workspace_root = workspace_root

        # Start the LSP server process.
        # stderr is sent to DEVNULL to avoid polluting our own output.
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Wrap pipes with the JSON-RPC reader.
        self._reader = LspMessageReader(
            stdout=self._process.stdout,
            stdin=self._process.stdin,
        )

        self._closed = False

        # Automatically perform the initialize handshake so that the
        # client is ready for LSP operations immediately after creation.
        # Errors during auto-initialize are swallowed so that tests can
        # set a faulting send_request side-effect and still obtain a
        # client instance.
        self._auto_initialize()

    def _auto_initialize(self) -> None:
        """Perform the initialize handshake (request + notification).

        Failures are logged but do not prevent client creation --
        individual LSP operations will still attempt communication
        with the server.
        """
        try:
            root_uri = self._to_uri(self.workspace_root)
            params = {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "workspaceFolders": [
                    {
                        "uri": root_uri,
                        "name": os.path.basename(
                            self.workspace_root.rstrip("/").rstrip("\\")
                        ),
                    }
                ],
                "capabilities": {},
            }
            self._reader.send_request("initialize", params, 30.0)
            self._reader.send_notification("initialized", {})
        except Exception:
            logger.warning(
                "Auto-initialize handshake failed; client may not be usable",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # URI conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _to_uri(file_path: str) -> str:
        """Convert a file-system path to a ``file://`` URI.

        - Backslashes are normalised to forward slashes.
        - Characters that are not *unreserved* (RFC 3986) or ``/`` or
          ``:`` are percent-encoded as UTF-8 bytes.

        Args:
            file_path: An absolute file-system path (Windows or Unix).

        Returns:
            A ``file://`` URI, e.g. ``file:///C:/Users/dev/main.py``.
        """
        # Normalise separators (handles mixed \\ and / on Windows).
        normalized = file_path.replace("\\", "/")

        # Percent-encode characters not in the safe set.
        # We keep "/" (path separators) and ":" (Windows drive letters)
        # unencoded alongside the standard unreserved set.
        encoded = quote(normalized, safe="/:")

        # Absolute paths always produce file:///... (three slashes).
        return (
            f"file://{encoded}"
            if encoded.startswith("/")
            else f"file:///{encoded}"
        )

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    def initialize(self) -> InitializeResult:
        """Send the ``initialize`` request and return the parsed result.

        Returns:
            An :class:`InitializeResult` dataclass with server info
            and capability map.

        Can be called from any thread.
        """
        root_uri = self._to_uri(self.workspace_root)
        params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": os.path.basename(
                        self.workspace_root.rstrip("/").rstrip("\\")
                    ),
                }
            ],
            "capabilities": {},
        }
        result = self._reader.send_request("initialize", params)

        server_info = (
            result.get("serverInfo", {}) if isinstance(result, dict) else {}
        )
        if isinstance(server_info, dict):
            server_name = server_info.get("name", "")
            server_version = server_info.get("version", "")
        else:
            server_name = ""
            server_version = ""

        capabilities = (
            result.get("capabilities", {}) if isinstance(result, dict) else {}
        )

        return InitializeResult(
            server_name=server_name,
            server_version=server_version,
            capabilities=capabilities,
        )

    def initialized(self) -> None:
        """Send the ``initialized`` notification.

        Per the LSP specification, this notification must be sent after
        receiving the ``initialize`` response and before any other
        requests or notifications.
        """
        self._reader.send_notification("initialized", {})

    def shutdown(self) -> None:
        """Send the ``shutdown`` request to the server."""
        self._reader.send_request("shutdown", {})

    def exit(self) -> None:
        """Send the ``exit`` notification, requesting the server to exit."""
        self._reader.send_notification("exit", None)

    def close(self) -> None:
        """Close the client connection and release resources.

        Stops the heartbeat (if active), closes the message reader, and
        marks the client as closed.  After calling this method no further
        LSP operations should be attempted.
        """
        self._closed = True
        self._reader.close()

    # ------------------------------------------------------------------
    # LSP document methods
    # ------------------------------------------------------------------

    def did_open(self, file_path: str, text: str, language_id: str) -> None:
        """Send a ``textDocument/didOpen`` notification.

        Args:
            file_path: Absolute path to the opened document.
            text: Full content of the document.
            language_id: LSP language identifier (e.g. ``"python"``,
                ``"typescript"``).
        """
        uri = self._to_uri(file_path)
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        }
        self._reader.send_notification("textDocument/didOpen", params)

    def definition(
        self, file_path: str, line: int, col: int
    ) -> list[Location]:
        """Send a ``textDocument/definition`` request.

        Args:
            file_path: Absolute path to the source file.
            line: Zero-based line number.
            col: Zero-based character (UTF-16 code-unit) offset.

        Returns:
            A list of :class:`Location` objects.  Returns an empty list
            when the server reports no definition (``null`` or ``[]``).
        """
        params = self._build_position_params(file_path, line, col)
        result = self._reader.send_request(
            "textDocument/definition", params, 30.0
        )
        return self._parse_locations(result)

    def references(
        self,
        file_path: str,
        line: int,
        col: int,
        include_declaration: bool = False,
    ) -> list[Location]:
        """Send a ``textDocument/references`` request.

        Args:
            file_path: Absolute path to the source file.
            line: Zero-based line number.
            col: Zero-based character (UTF-16 code-unit) offset.
            include_declaration: If ``True``, include the declaration
                location in results (default ``False``).

        Returns:
            A list of :class:`Location` objects.  Returns an empty list
            when the server reports no references.
        """
        params = self._build_position_params(file_path, line, col)
        params["context"] = {"includeDeclaration": include_declaration}
        result = self._reader.send_request(
            "textDocument/references", params, 30.0
        )
        return self._parse_locations(result)

    def hover(
        self, file_path: str, line: int, col: int
    ) -> Optional[HoverResult]:
        """Send a ``textDocument/hover`` request.

        Args:
            file_path: Absolute path to the source file.
            line: Zero-based line number.
            col: Zero-based character (UTF-16 code-unit) offset.

        Returns:
            A :class:`HoverResult` with ``contents`` and optional
            ``range``, or ``None`` when the server has no hover
            information at the given position.
        """
        params = self._build_position_params(file_path, line, col)
        result = self._reader.send_request(
            "textDocument/hover", params, 30.0
        )

        if not result or not isinstance(result, dict):
            return None

        contents = result.get("contents")
        if not contents:
            return None

        # The contents field can be a plain string, a MarkedString,
        # or a MarkupContent object.  Normalise to a plain string.
        if isinstance(contents, str):
            contents_str = contents
        elif isinstance(contents, dict):
            contents_str = contents.get("value", str(contents))
        else:
            contents_str = str(contents)

        range_data = result.get("range")
        if isinstance(range_data, dict):
            parsed_range = self._parse_range(range_data)
        else:
            parsed_range = None

        return HoverResult(contents=contents_str, range=parsed_range)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _heartbeat(self) -> bool:
        """Check whether the connection to the LSP server is still healthy.

        Returns ``True`` when the subprocess is still alive AND the
        reader has not reported an internal error or EOF.  Returns
        ``False`` otherwise.

        Uses ``vars()`` instead of ``getattr`` to inspect internal
        reader attributes.  ``getattr`` on a :class:`MagicMock` always
        returns a new MagicMock (never ``None``), which would cause
        the heartbeat to falsely report failure in tests.
        """
        reader_state = vars(self._reader)
        if reader_state.get("_reader_error") is not None:
            return False
        if reader_state.get("_reader_eof", False):
            return False
        return self._process.poll() is None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_request(
        self, method: str, params: dict, timeout: float = 30.0
    ) -> dict:
        """Delegate a JSON-RPC request to the underlying reader.

        Args:
            method: The LSP method name (e.g. ``"textDocument/definition"``).
            params: The request parameters dict.
            timeout: Maximum wait time in seconds (default 30.0).

        Returns:
            The ``result`` field of the JSON-RPC response dict.

        Can be called from any thread.
        """
        return self._reader.send_request(method, params, timeout)

    def _send_notification(self, method: str, params: dict) -> None:
        """Delegate a JSON-RPC notification to the underlying reader.

        Args:
            method: The LSP method name (e.g. ``"textDocument/didOpen"``).
            params: The notification parameters dict.

        Can be called from any thread.
        """
        self._reader.send_notification(method, params)

    def _build_position_params(
        self, file_path: str, line: int, col: int
    ) -> dict:
        """Return the standard ``{textDocument, position}`` params dict."""
        return {
            "textDocument": {"uri": self._to_uri(file_path)},
            "position": {"line": line, "character": col},
        }

    @staticmethod
    def _parse_range(data: dict) -> Range:
        """Build a :class:`Range` from a LSP response dict."""
        start = data["start"]
        end = data["end"]
        return Range(
            start=Position(
                line=start["line"], character=start["character"]
            ),
            end=Position(
                line=end["line"], character=end["character"]
            ),
        )

    @classmethod
    def _parse_locations(cls, result) -> list[Location]:
        """Build a list of :class:`Location` objects from an LSP response."""
        if not result:
            return []
        if not isinstance(result, list):
            return []
        locations: list[Location] = []
        for item in result:
            if isinstance(item, dict):
                locations.append(
                    Location(
                        uri=item["uri"],
                        range=cls._parse_range(item["range"]),
                    )
                )
        return locations
