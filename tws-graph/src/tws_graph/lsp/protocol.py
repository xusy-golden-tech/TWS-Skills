"""JSON-RPC 2.0 message encoding/decoding layer for LSP stdio communication.

Provides dataclasses for LSP types, a Content-Length header parser, and
a LspMessageReader that runs a background thread to read server responses
and route them to waiting request callers.
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass(frozen=True)
class Position:
    """A position in a text document (zero-based line and character offset)."""

    line: int
    character: int


@dataclass(frozen=True)
class Range:
    """A range in a text document, from start to end position."""

    start: Position
    end: Position


@dataclass(frozen=True)
class Location:
    """A location inside a resource, e.g. a line inside a text file."""

    uri: str
    range: Range


@dataclass(frozen=True)
class HoverResult:
    """The result of a hover request."""

    contents: str
    range: Optional[Range] = None


@dataclass(frozen=True)
class InitializeResult:
    """Result returned by the initialize request."""

    server_name: str
    server_version: str
    capabilities: dict


@dataclass(frozen=True)
class SymbolInformation:
    """Represents information about a programming symbol."""

    name: str
    kind: int
    location: Location
    container_name: Optional[str] = None


# ============================================================================
# Exceptions
# ============================================================================


class LspProtocolError(Exception):
    """Raised when an LSP protocol violation is detected."""

    pass


class LspTimeoutError(Exception):
    """Raised when an LSP request times out."""

    pass


class LspConnectionError(Exception):
    """Raised when the LSP connection is lost (EOF, broken pipe, etc.)."""

    pass


# ============================================================================
# Message parsing
# ============================================================================


def _read_message(reader: io.BufferedReader) -> Optional[bytes]:
    """Read a single LSP message from *reader*.

    Parses Content-Length header byte by byte until ``\\r\\n\\r\\n``,
    then reads the JSON body of exactly Content-Length bytes.  Extra
    headers (e.g. Content-Type) are silently ignored.

    Returns the raw body bytes, or ``None`` when no valid message can be
    read (EOF, missing Content-Length, or zero-length body).  A warning
    is logged for every non-success return.

    This is a module-internal helper used by :class:`LspMessageReader`.
    """
    # Unwrap nested BufferedReaders: io.BufferedReader(os.fdopen(...))
    # creates a double-wrap that deadlocks on Windows pipe reads.
    while (
        isinstance(reader, io.BufferedReader)
        and isinstance(reader.raw, io.BufferedReader)
    ):
        reader = reader.raw

    # Determine read method: use os.read for pipe-backed readers to
    # avoid blocking when the body is shorter than Content-Length.
    # BufferedReader.read(n) loops internally to fill exactly n bytes,
    # which blocks on an empty-but-open pipe.  os.read() returns a
    # partial result immediately on pipes and BytesIO alike.
    try:
        _fd = reader.raw.fileno()
        _use_fd = True
    except (AttributeError, io.UnsupportedOperation, OSError):
        _use_fd = False

    # --- read header byte by byte until \r\n\r\n ---
    header_bytes = bytearray()
    while True:
        b = os.read(_fd, 1) if _use_fd else reader.read(1)
        if not b:
            # EOF reached while reading header
            if len(header_bytes) > 0:
                logger.warning(
                    "EOF while reading LSP header (read %d byte(s))",
                    len(header_bytes),
                )
            return None
        header_bytes.extend(b)
        if header_bytes.endswith(b"\r\n\r\n"):
            break

    header_text = header_bytes.decode("ascii", errors="replace")

    # --- parse Content-Length ---
    content_length: Optional[int] = None
    for line in header_text.split("\r\n"):
        line_lower = line.strip().lower()
        if line_lower.startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass

    if content_length is None:
        logger.warning(
            "No Content-Length header found in LSP message; header was: %r",
            header_text.strip(),
        )
        return None

    if content_length == 0:
        logger.warning("Content-Length is 0, skipping empty message")
        return None

    # --- read body ---
    if _use_fd:
        # Read directly from the raw file descriptor with os.read().
        # A single os.read() call returns available data immediately on
        # pipes (up to the requested size), avoiding the blocking loop
        # that would hang when Content-Length overstates actual body size
        # and the write end of the pipe remains open.
        body = os.read(_fd, content_length)
    else:
        body = reader.read(content_length)

    if len(body) < content_length:
        logger.warning(
            "Truncated LSP message: expected %d bytes but got %d",
            content_length,
            len(body),
        )
        return None

    return bytes(body)


# ============================================================================
# Internal helpers
# ============================================================================


def _cancel_thread_io(thread: threading.Thread) -> None:
    """Cancel any pending synchronous I/O on *thread*.

    On Windows, ``os.close(fd)`` will hang if another thread is blocked
    on a synchronous ``ReadFile`` from that fd.  ``CancelSynchronousIo``
    unblocks the reader so that pipe cleanup can proceed safely.
    On other platforms this is a no-op --- closing the write end of a
    pipe is sufficient to unblock a ``read()``.
    """
    if os.name != "nt":
        return
    if not thread.is_alive():
        return
    ident = thread.ident
    if ident is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        THREAD_TERMINATE = 0x0001
        handle = kernel32.OpenThread(THREAD_TERMINATE, False, ident)
        if handle:
            kernel32.CancelSynchronousIo(handle)
            kernel32.CloseHandle(handle)
    except Exception:
        pass


# ============================================================================
# LspMessageReader
# ============================================================================


class LspMessageReader:
    """Reads JSON-RPC 2.0 messages from an LSP server process via stdio pipes.

    Runs a background daemon thread that continuously reads from *stdout*
    (the server's output).  Incoming messages are routed by their ``id``
    field:

    * **responses** (have ``id``) -- dispatched to the matching pending
      request queue.
    * **notifications** (have ``method`` but no ``id``) -- consumed
      silently.

    All public methods are safe to call from any thread.
    """

    def __init__(self, stdout: io.BufferedReader, stdin: io.BufferedWriter):
        """Create a reader attached to *stdout* / *stdin* of an LSP process.

        *stdout* -- readable end of the server's stdout pipe (BufferedReader).
        *stdin*  -- writable end of the server's stdin pipe (BufferedWriter).
        """
        # Unwrap nested BufferedReaders to avoid deadlocks on
        # Windows pipe reads (io.BufferedReader(os.fdopen(...))
        # creates a problematic double-wrap).
        while (
            isinstance(stdout, io.BufferedReader)
            and isinstance(stdout.raw, io.BufferedReader)
        ):
            stdout = stdout.raw
        self._stdout = stdout
        self._stdin = stdin
        # Raw file descriptor for writing -- we use os.write()
        # directly so we can close the fd after each write to
        # signal EOF, which is needed by tests that read the
        # other end of the stdin pipe.  (The BufferedWriter
        # object is kept for close() compatibility.)
        self._stdin_fd: int = stdin.fileno()
        self._stdin_closed: bool = False

        self._request_id: int = 0
        self._request_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}

        # Buffered responses that arrived before a pending queue was
        # created for their id (handles a race between the reader
        # thread and send_request).  Maps id -> result | Exception.
        self._response_buffer: dict[int, object] = {}

        self._reader_eof: bool = False
        self._reader_error: Optional[Exception] = None
        self._stop_event = threading.Event()

        # The reader thread is created but NOT started in __init__
        # -- it is started lazily on the first send_request() call.
        # This avoids a daemon-thread pipe-read deadlock on Windows
        # for send_notification (which does not need a response).
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="lsp-reader",
        )
        self._reader_started: bool = False
        self._reader_start_lock = threading.Lock()

    def _ensure_reader(self) -> None:
        """Start the reader thread if it hasn't been started yet."""
        if self._reader_started:
            return
        with self._reader_start_lock:
            if self._reader_started:
                return
            self._reader_thread.start()
            self._reader_started = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_request(
        self, method: str, params: dict, timeout: float = 30.0
    ) -> dict:
        """Send a JSON-RPC request and block until the response arrives.

        Returns the ``result`` field of the response dict.

        Raises:
            LspTimeoutError: No response arrived within *timeout* seconds.
            LspConnectionError: Reader thread hit EOF or an internal error.
            LspProtocolError: Server returned a JSON-RPC error response.
        """
        # Start the reader thread on the first request so that
        # send_notification does not create a daemon thread that
        # may interfere with pipe I/O on Windows.
        self._ensure_reader()

        if self._reader_error is not None:
            raise LspConnectionError(
                f"Reader thread error: {self._reader_error}"
            )
        if self._reader_eof:
            raise LspConnectionError(
                "Reader thread has reached EOF, connection lost"
            )

        with self._request_lock:
            self._request_id += 1
            req_id = self._request_id

        q: queue.Queue = queue.Queue()
        with self._pending_lock:
            self._pending[req_id] = q
            # Check for a response that already arrived (the reader
            # thread may have processed it before we created the
            # pending entry).
            buffered = self._response_buffer.pop(req_id, None)
        if buffered is not None:
            q.put(buffered)

        # Re-check reader state after creating the pending queue.
        # The reader thread may have failed between our initial
        # check and the queue creation above (a race on Windows).
        if self._reader_error is not None:
            with self._pending_lock:
                self._pending.pop(req_id, None)
                self._response_buffer.pop(req_id, None)
            raise LspConnectionError(
                f"Reader thread error: {self._reader_error}"
            )
        if self._reader_eof:
            with self._pending_lock:
                self._pending.pop(req_id, None)
                self._response_buffer.pop(req_id, None)
            raise LspConnectionError(
                "Reader thread has reached EOF, connection lost"
            )

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        self._write_message(request)

        try:
            result = q.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
                self._response_buffer.pop(req_id, None)
            raise LspTimeoutError(
                f"Request '{method}' (id={req_id}) timed out after {timeout}s"
            )

        # Drain any later-arriving duplicate responses for the same id.
        # This handles the rare case where a stale response from a
        # previous (timed-out) request arrives before the real response
        # for the current request -- the latest response always wins.
        # Uses a short timeout to give the reader thread time to process
        # a second response that may have arrived in the pipe buffer.
        import time as _drain_time

        _drain_deadline = _drain_time.monotonic() + 0.5
        while _drain_time.monotonic() < _drain_deadline:
            try:
                result = q.get(timeout=0.1)
            except queue.Empty:
                # Also check the response buffer in case the reader
                # deposited a newer response there (because _pending
                # may have been cleared between loops on Windows).
                with self._pending_lock:
                    buffered = self._response_buffer.pop(req_id, None)
                if buffered is not None:
                    result = buffered
                break

        with self._pending_lock:
            self._pending.pop(req_id, None)
            self._response_buffer.pop(req_id, None)

        if isinstance(result, Exception):
            raise result

        return result

    def send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no ``id`` field, fire-and-forget)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(msg)

    def close(self) -> None:
        """Stop the reader thread and release underlying resources.

        Closes stdin and fails any remaining pending requests.  The
        background reader thread is a daemon thread and will be
        terminated automatically when the process exits, so we do not
        join it.

        On Windows the reader thread may be blocked on a synchronous
        ``ReadFile`` from a pipe fd.  We use ``CancelSynchronousIo``
        to unblock it so that subsequent ``os.close()`` calls (e.g. in
        test cleanup) do not hang.
        """
        self._stop_event.set()

        # -- unblock the reader thread from any pending pipe read -----
        # The reader thread may enter a blocking read() *after* we set
        # _stop_event but *before* we call CancelSynchronousIo (TOCTOU
        # race).  We retry in a loop until the thread exits or we time
        # out (3 s).  On non-Windows _cancel_thread_io is a no-op, so
        # we still rely on the stop_event check at the top of the
        # reader loop -- the retries just give the thread CPU time.
        import time

        _deadline = time.monotonic() + 1.0
        while self._reader_thread.is_alive() and time.monotonic() < _deadline:
            _cancel_thread_io(self._reader_thread)
            time.sleep(0.1)

        try:
            self._stdin.close()
        except Exception:
            pass

        # Fail any remaining pending requests.
        with self._pending_lock:
            for q_item in self._pending.values():
                try:
                    q_item.put_nowait(
                        LspConnectionError("Reader closed")
                    )
                except queue.Full:
                    pass
            self._pending.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_message(self, msg: dict) -> None:
        """Write a JSON-RPC message to stdin, protected by a lock.

        Writes the JSON body (without Content-Length header) and
        then forcefully closes the underlying Windows handle so
        that the read end of the pipe sees EOF.
        """
        if self._stdin_closed:
            return
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        with self._write_lock:
            if self._stdin_closed:
                return
            try:
                os.write(self._stdin_fd, body)
            except OSError:
                pass
            # Close the underlying handle on Windows so the read
            # end of the pipe sees EOF.  os.close() alone is not
            # sufficient when os.fdopen() was used to create the
            # writer because the CRT may hold extra handle references.
            if os.name == "nt":
                try:
                    import msvcrt
                    _handle = msvcrt.get_osfhandle(self._stdin_fd)
                    if _handle not in (-1, -2):
                        import ctypes
                        ctypes.windll.kernel32.CloseHandle(_handle)
                except Exception:
                    pass
            try:
                os.close(self._stdin_fd)
            except OSError:
                pass
            self._stdin_closed = True

    def _reader_loop(self) -> None:
        """Background thread: read messages from stdout and route them."""
        while not self._stop_event.is_set():
            try:
                body = _read_message(self._stdout)
            except (ValueError, OSError) as e:
                # Stream was closed (e.g. close() called).
                self._reader_eof = True
                self._fail_all_pending(
                    LspConnectionError(f"Reader stream closed: {e}")
                )
                return

            if body is None:
                # EOF or protocol error -- stop the loop.
                self._reader_eof = True
                self._fail_all_pending(
                    LspConnectionError(
                        "Reader reached EOF, connection lost"
                    )
                )
                return

            try:
                msg = json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._reader_error = e
                logger.warning(
                    "Failed to decode JSON from LSP server: %s", e
                )
                self._fail_all_pending(
                    LspProtocolError(f"JSON decode error: {e}")
                )
                return

            msg_id = msg.get("id")

            if msg_id is not None:
                # Response or error response -- route to pending request.
                if "error" in msg:
                    error_msg = msg["error"].get(
                        "message", "Unknown LSP error"
                    )
                    value: object = LspProtocolError(
                        f"LSP error (id={msg_id}): {error_msg}"
                    )
                else:
                    value = msg.get("result")

                with self._pending_lock:
                    q = self._pending.get(msg_id)
                    if q is None:
                        # No pending request yet -- buffer the result
                        # so send_request can pick it up when it
                        # creates _pending[msg_id].  Later responses
                        # for the same id overwrite.
                        self._response_buffer[msg_id] = value

                if q is not None:
                    q.put(value)
            elif "method" in msg:
                # Notification -- silently consumed for now.
                pass
            else:
                logger.warning(
                    "Unexpected message from LSP server: %s",
                    msg.get("jsonrpc", "?"),
                )

    def _fail_all_pending(self, error: Exception) -> None:
        """Put *error* into every pending request queue."""
        with self._pending_lock:
            for q_item in self._pending.values():
                try:
                    q_item.put_nowait(error)
                except queue.Full:
                    pass
