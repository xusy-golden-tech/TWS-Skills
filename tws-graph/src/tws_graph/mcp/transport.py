"""Stdio transport layer for MCP (Model Context Protocol).

Provides line-delimited JSON-RPC 2.0 message I/O over stdin/stdout.

Protocol: each JSON message is written as a single line terminated by \\n.
This is a simplified transport compared to LSP's Content-Length header approach,
chosen because MCP does not mandate Content-Length for stdio transport.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Optional

from .protocol import (
    JsonRpcMessage,
    ErrorCode,
    InvalidRequestError,
    parse_message,
    serialize_message,
    make_error_response,
)


class StdioTransport:
    """Reads/writes JSON-RPC 2.0 messages on stdin/stdout.

    Each message is a single JSON line terminated by \\n. Empty lines are
    skipped during reading. Invalid JSON lines produce a parse error response
    (but read_message() returns it rather than writing to stdout, allowing
    the caller to decide whether to respond).

    Thread safety: write_message() is protected by a lock so that concurrent
    handler threads don't interleave output. read_message() is NOT thread-safe;
    only one consumer should read from stdin at a time.
    """

    def __init__(
        self,
        stdin: Optional[io.TextIOWrapper] = None,
        stdout: Optional[io.TextIOWrapper] = None,
    ) -> None:
        """Create a stdio transport.

        Args:
            stdin: Input stream (defaults to sys.stdin).
            stdout: Output stream (defaults to sys.stdout).
        """
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._closed = False

    def read_message(self) -> Optional[JsonRpcMessage]:
        """Read and parse a single JSON-RPC 2.0 message from stdin.

        Blocks until a non-empty line is available or EOF is reached.
        Empty lines (including whitespace-only lines) are skipped.

        Returns:
            A JsonRpcMessage subclass, or None on EOF.
            Returns a JsonRpcError for parse errors (caller can write it back).
        """
        if self._closed:
            return None

        while True:
            try:
                line = self._stdin.readline()
            except (ValueError, OSError):
                return None

            if not line:
                # EOF
                self._closed = True
                return None

            line = line.strip()
            if not line:
                # Skip empty lines
                continue

            try:
                return parse_message(line)
            except InvalidRequestError as e:
                # The message has an id, so return a proper error response
                return make_error_response(
                    code=ErrorCode.INVALID_REQUEST,
                    message=str(e),
                    id=e.id,
                )
            except ValueError as e:
                # Return a parse error -- caller decides whether to write it
                return make_error_response(
                    code=ErrorCode.PARSE_ERROR,
                    message=str(e),
                    id=None,
                )

    def write_message(self, msg: JsonRpcMessage) -> None:
        """Serialize and write a single JSON-RPC 2.0 message to stdout.

        The message is written as a single line followed by \\n.
        Flushes immediately.

        Args:
            msg: Any JsonRpcMessage to send.
        """
        if self._closed:
            return
        try:
            data = serialize_message(msg)
            self._stdout.write(data + "\n")
            self._stdout.flush()
        except (ValueError, OSError):
            self._closed = True

    def close(self) -> None:
        """Mark the transport as closed.

        Does not close the underlying streams (stdin/stdout are typically
        owned by the process). After calling close(), read_message()
        returns None and write_message() is a no-op.
        """
        self._closed = True
