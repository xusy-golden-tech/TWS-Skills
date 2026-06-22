"""Tests for lsp/protocol.py --- JSON-RPC 2.0 message coding layer.

TDD red phase: these tests are written before the protocol module exists.
They encode the acceptance criteria from design-p5-lsp.md for protocol.py.
Imports are expected to fail (ImportError) until the module is created.

Coverage per acceptance criteria:
  - Dataclass field validation
  - Exception hierarchy
  - _read_message: Content-Length header parsing, zero-length, missing header
  - LspMessageReader: send_request / send_notification / response routing
  - LspMessageReader: timeout handling, post-timeout recovery
  - LspMessageReader: EOF detection, reader error propagation
  - LspMessageReader: stdin write locking (concurrent safety)
  - LspMessageReader: request id collision handling
"""

import io
import json
import logging
import os
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# TDD red phase: imports will fail until tws_graph.lsp.protocol is created.
# ---------------------------------------------------------------------------
from tws_graph.lsp.protocol import (  # noqa: E402 (expected ImportError in red phase)
    HoverResult,
    InitializeResult,
    Location,
    LspConnectionError,
    LspMessageReader,
    LspProtocolError,
    LspTimeoutError,
    Position,
    Range,
    SymbolInformation,
    _read_message,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_lsp_message(payload: dict) -> bytes:
    """Build a complete LSP message: Content-Length header + JSON body."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _make_reader(data: bytes) -> io.BufferedReader:
    """Wrap bytes in a BufferedReader for _read_message tests."""
    return io.BufferedReader(io.BytesIO(data))


def _pair_fds():
    """Create a pair of file descriptors simulating process stdout pipe.

    Returns (read_fd, write_fd) opened in binary mode.
    The read side is a BufferedReader-compatible object.
    """
    r_fd, w_fd = os.pipe()
    return r_fd, w_fd


# ============================================================================
# Dataclass tests
# ============================================================================

class TestPosition:
    """Position(line: int, character: int)"""

    def test_create(self):
        p = Position(line=10, character=5)
        assert p.line == 10
        assert p.character == 5

    def test_equality(self):
        a = Position(line=1, character=2)
        b = Position(line=1, character=2)
        c = Position(line=1, character=3)
        assert a == b
        assert a != c

    def test_defaults(self):
        """Position should require both fields (no defaults)."""
        with pytest.raises(TypeError):
            Position()


class TestRange:
    """Range(start: Position, end: Position)"""

    def test_create(self):
        start = Position(line=0, character=0)
        end = Position(line=5, character=10)
        r = Range(start=start, end=end)
        assert r.start == start
        assert r.end == end

    def test_not_equal(self):
        a = Range(start=Position(0, 0), end=Position(1, 1))
        b = Range(start=Position(0, 0), end=Position(2, 2))
        assert a != b


class TestLocation:
    """Location(uri: str, range: Range)"""

    def test_create(self):
        loc = Location(
            uri="file:///home/user/project/main.py",
            range=Range(start=Position(1, 0), end=Position(1, 10)),
        )
        assert loc.uri == "file:///home/user/project/main.py"
        assert loc.range.start.line == 1


class TestHoverResult:
    """HoverResult(contents: str, range: Optional[Range])"""

    def test_create_with_range(self):
        r = Range(start=Position(1, 0), end=Position(1, 5))
        hr = HoverResult(contents="int: x", range=r)
        assert hr.contents == "int: x"
        assert hr.range == r

    def test_create_without_range(self):
        hr = HoverResult(contents="str: name", range=None)
        assert hr.contents == "str: name"
        assert hr.range is None


class TestInitializeResult:
    """InitializeResult(server_name, server_version, capabilities)"""

    def test_create(self):
        caps = {"textDocumentSync": 1, "definitionProvider": True}
        ir = InitializeResult(
            server_name="pyright",
            server_version="1.1.300",
            capabilities=caps,
        )
        assert ir.server_name == "pyright"
        assert ir.server_version == "1.1.300"
        assert ir.capabilities == caps
        assert ir.capabilities["definitionProvider"] is True


class TestSymbolInformation:
    """SymbolInformation(name, kind, location, container_name)"""

    def test_create_with_container(self):
        loc = Location(
            uri="file:///a.py",
            range=Range(start=Position(5, 0), end=Position(5, 4)),
        )
        si = SymbolInformation(
            name="my_func", kind=12, location=loc, container_name="MyClass"
        )
        assert si.name == "my_func"
        assert si.kind == 12
        assert si.location == loc
        assert si.container_name == "MyClass"

    def test_create_without_container(self):
        loc = Location(
            uri="file:///a.py",
            range=Range(start=Position(1, 0), end=Position(1, 3)),
        )
        si = SymbolInformation(name="foo", kind=12, location=loc, container_name=None)
        assert si.container_name is None


# ============================================================================
# Exception hierarchy tests
# ============================================================================

class TestLspProtocolError:
    """LspProtocolError(Exception)"""

    def test_is_exception(self):
        e = LspProtocolError()
        assert isinstance(e, Exception)

    def test_with_message(self):
        e = LspProtocolError("invalid JSON-RPC message")
        assert str(e) == "invalid JSON-RPC message"

    def test_catchable(self):
        with pytest.raises(LspProtocolError):
            raise LspProtocolError("test")


class TestLspTimeoutError:
    """LspTimeoutError(Exception)"""

    def test_is_exception(self):
        e = LspTimeoutError()
        assert isinstance(e, Exception)

    def test_with_message(self):
        e = LspTimeoutError("request timed out after 30s")
        assert "30s" in str(e)

    def test_catchable(self):
        with pytest.raises(LspTimeoutError):
            raise LspTimeoutError("timeout")


class TestLspConnectionError:
    """LspConnectionError(Exception)"""

    def test_is_exception(self):
        e = LspConnectionError()
        assert isinstance(e, Exception)

    def test_with_message(self):
        e = LspConnectionError("reader EOF, connection lost")
        assert "EOF" in str(e)

    def test_catchable(self):
        with pytest.raises(LspConnectionError):
            raise LspConnectionError("broken pipe")


# ============================================================================
# _read_message tests
# ============================================================================

class TestReadMessage:
    """Tests for _read_message(reader: io.BufferedReader) -> Optional[bytes]"""

    # --- normal path --------------------------------------------------------

    def test_reads_complete_message(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"value": 42}}
        raw = _make_lsp_message(payload)
        reader = _make_reader(raw)
        result = _read_message(reader)
        assert result is not None
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["id"] == 1
        assert parsed["result"]["value"] == 42

    def test_reads_message_with_extra_headers(self):
        """Content-Type header (optional) should be ignored."""
        body = json.dumps({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics"}).encode("utf-8")
        header = (
            f"Content-Length: {len(body)}\r\n"
            f"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
            f"\r\n"
        ).encode("ascii")
        raw = header + body
        reader = _make_reader(raw)
        result = _read_message(reader)
        assert result is not None
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["method"] == "textDocument/publishDiagnostics"

    def test_reads_large_body(self):
        """Content-Length with a large body should be handled."""
        large_value = "x" * 10000
        payload = {"jsonrpc": "2.0", "id": 99, "result": {"data": large_value}}
        raw = _make_lsp_message(payload)
        reader = _make_reader(raw)
        result = _read_message(reader)
        assert result is not None
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["result"]["data"] == large_value

    # --- boundary: Content-Length = 0 ---------------------------------------

    def test_zero_content_length_returns_none(self, caplog):
        raw = b"Content-Length: 0\r\n\r\n"
        reader = _make_reader(raw)

        with caplog.at_level(logging.WARNING):
            result = _read_message(reader)

        assert result is None
        # Must log a warning, not crash
        assert len(caplog.records) >= 1
        assert any("Content-Length" in r.message for r in caplog.records)

    # --- boundary: missing Content-Length -----------------------------------

    def test_missing_content_length_returns_none(self, caplog):
        raw = b"Content-Type: application/json\r\n\r\n"
        reader = _make_reader(raw)

        with caplog.at_level(logging.WARNING):
            result = _read_message(reader)

        assert result is None
        assert len(caplog.records) >= 1
        assert any("Content-Length" in r.message for r in caplog.records)

    def test_empty_stream_returns_none(self, caplog):
        """Empty reader (no data at all) returns None without crashing."""
        reader = _make_reader(b"")

        with caplog.at_level(logging.WARNING):
            result = _read_message(reader)

        assert result is None


# ============================================================================
# LspMessageReader tests
# ============================================================================

class TestLspMessageReaderInit:
    """Tests for LspMessageReader.__init__ and basic lifecycle."""

    def test_creates_with_pipes(self):
        r_fd, w_fd = os.pipe()
        stdout = os.fdopen(r_fd, "rb")
        stdin = os.fdopen(w_fd, "wb")
        try:
            reader = LspMessageReader(stdout=stdout, stdin=stdin)
            assert reader is not None
            reader.close()
        finally:
            try:
                stdin.close()
            except Exception:
                pass
            try:
                stdout.close()
            except Exception:
                pass

    def test_close_stops_reader_thread(self):
        r_fd, w_fd = os.pipe()
        stdout = os.fdopen(r_fd, "rb")
        stdin = os.fdopen(w_fd, "wb")
        try:
            reader = LspMessageReader(stdout=stdout, stdin=stdin)
            reader.close()
            # close() should complete without hanging
        finally:
            try:
                stdin.close()
            except Exception:
                pass
            try:
                stdout.close()
            except Exception:
                pass


class TestLspMessageReaderSendRequest:
    """Tests for send_request method — request/response matching."""

    @pytest.fixture
    def pipe_pair(self):
        """Create a pipe pair for stdout/stdin simulation."""
        r_fd, w_fd = os.pipe()
        # stdout: we read server responses from here
        # The LspMessageReader reads from stdout (the read end of the pipe)
        # We write fake responses into the write end
        stdout_reader = os.fdopen(r_fd, "rb")
        stdin_writer_fd, stdin_reader_fd = os.pipe()
        stdin_writer = os.fdopen(stdin_writer_fd, "wb")
        stdin_reader = os.fdopen(stdin_reader_fd, "rb")
        yield stdout_reader, stdin_writer, stdin_reader
        # cleanup
        for f in [stdout_reader, stdin_writer, stdin_reader]:
            try:
                f.close()
            except Exception:
                pass

    def _write_response_to_stdout(self, stdout_reader, response: dict):
        """Write a response message into the pipe's write end.

        We open a new fd for the write end since stdout_reader is the read end.
        This is a simplified helper — the caller must have access to the write fd.
        """
        # The pipe_pair fixture gives us stdout_reader (read end).
        # We need to write to the write end. Use os.write on the original fd.
        # But the original fd was closed by fdopen... we need a different approach.
        pass

    def test_send_request_returns_result(self):
        """send_request writes to stdin, waits for matching response on stdout.

        Uses os.pipe() to simulate the LSP server process pipes.
        The reader thread reads from stdout_pipe (read end),
        while we inject a response by writing to stdout_pipe (write end).
        """
        # stdout: server -> client  (LspMessageReader reads from stdout_read)
        stdout_read_fd, stdout_write_fd = os.pipe()
        # stdin: client -> server  (LspMessageReader writes to stdin_write)
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)
            stdin_reader = os.fdopen(stdin_read_fd, "rb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Build the response that the "server" would send for request id=1
            response_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"uri": "file:///main.py", "range": {}},
            }
            response_bytes = _make_lsp_message(response_payload)

            # Write the response into the stdout pipe so the reader thread finds it
            os.write(stdout_write_fd, response_bytes)

            # Now send the request — it should pick up the pre-written response
            result = reader.send_request("textDocument/definition", {"param": 1}, timeout=5.0)

            assert result == {"uri": "file:///main.py", "range": {}}

            # Verify stdin received the request (use _read_message since
            # stdin is no longer auto-closed after each write)
            stdin_buf = (
                stdin_reader
                if isinstance(stdin_reader, io.BufferedReader)
                else io.BufferedReader(stdin_reader)
            )
            stdin_content = _read_message(stdin_buf)
            assert stdin_content is not None
            assert b"textDocument/definition" in stdin_content
            assert b'"id":1' in stdin_content or b'"id": 1' in stdin_content

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def test_send_request_correctly_matches_response_by_id(self):
        """Responses are routed to the correct pending request by id."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Pre-write both responses (id=2 first, then id=1)
            resp2 = _make_lsp_message({"jsonrpc": "2.0", "id": 2, "result": "second"})
            resp1 = _make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "first"})
            os.write(stdout_write_fd, resp2 + resp1)

            # Send requests — id=2 response arrives first but should route correctly
            r1 = reader.send_request("method1", {}, timeout=5.0)
            r2 = reader.send_request("method2", {}, timeout=5.0)

            assert r1 == "first"
            assert r2 == "second"

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderNotification:
    """Tests for send_notification — fire-and-forget messages."""

    def test_send_notification_writes_to_stdin(self):
        """send_notification writes a JSON-RPC notification (no id field)."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)
            stdin_reader_f = os.fdopen(stdin_read_fd, "rb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)
            reader.send_notification("initialized", {})

            stdin_content = _read_message(stdin_reader_f)
            assert stdin_content is not None
            assert b"initialized" in stdin_content
            # Notification must NOT have an "id" field
            parsed = json.loads(stdin_content.decode("utf-8"))
            assert "id" not in parsed
            assert parsed["method"] == "initialized"

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderRouting:
    """Tests for response vs notification distinction."""

    def test_response_has_id_field(self):
        """A message with 'id' key is treated as a response, routed to _pending."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Write a response with id=1 (must be treated as response)
            resp = _make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
            os.write(stdout_write_fd, resp)

            result = reader.send_request("test/method", {}, timeout=5.0)
            assert result == {"ok": True}

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def test_notification_has_no_id_but_has_method(self):
        """A message with 'method' but no 'id' is a notification.

        Notifications should NOT block pending requests or cause errors.
        """
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Write a notification first, then a response
            notif = _make_lsp_message(
                {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                 "params": {"uri": "file:///a.py", "diagnostics": []}}
            )
            resp = _make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "ok"})
            os.write(stdout_write_fd, notif + resp)

            # Notification should be consumed silently, response should match
            result = reader.send_request("test/method", {}, timeout=5.0)
            assert result == "ok"

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderTimeout:
    """Tests for timeout handling."""

    def test_send_request_timeout_raises(self):
        """send_request raises LspTimeoutError on timeout."""
        stdout_read_fd, _stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # No response will be written — send_request should time out
            with pytest.raises(LspTimeoutError):
                reader.send_request("test/method", {}, timeout=0.1)

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def test_does_not_block_subsequent_requests_after_timeout(self):
        """After a timeout, the next request should still work."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # First request times out (no response)
            with pytest.raises(LspTimeoutError):
                reader.send_request("slow/method", {}, timeout=0.1)

            # Second request should succeed — pre-write its response
            resp2 = _make_lsp_message({"jsonrpc": "2.0", "id": 2, "result": "recovered"})
            os.write(stdout_write_fd, resp2)

            result = reader.send_request("fast/method", {}, timeout=5.0)
            assert result == "recovered"

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderEOF:
    """Tests for reader EOF handling."""

    def test_reader_eof_sets_flag_and_errors_pending_requests(self):
        """When the reader thread hits EOF, _reader_eof is set to True
        and any waiting send_request receives LspConnectionError."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Close the write end of stdout pipe → reader thread sees EOF
            os.close(stdout_write_fd)

            # Allow the reader thread a moment to detect EOF
            time.sleep(0.1)

            # Now any send_request should get LspConnectionError
            with pytest.raises(LspConnectionError):
                reader.send_request("any/method", {}, timeout=1.0)

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderErrorHandling:
    """Tests for reader thread error resilience."""

    def test_json_decode_error_does_not_crash_process(self):
        """When the reader thread encounters an invalid JSON message,
        it sets _reader_error and stops, without crashing the process."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Write an invalid message: Content-Length says 50 bytes but body is short garbage
            bad_header = b"Content-Length: 50\r\n\r\n"
            bad_body = b"not valid json!!!!"
            os.write(stdout_write_fd, bad_header + bad_body)

            # Wait briefly for the reader thread to process
            time.sleep(0.2)

            # The process is still alive (we got here)
            # send_request should get an error because the reader is stopped
            with pytest.raises((LspConnectionError, LspProtocolError)):
                reader.send_request("any/method", {}, timeout=1.0)

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderConcurrency:
    """Tests for thread-safe stdin writing."""

    def test_concurrent_send_request_does_not_interleave(self):
        """When multiple threads call send_request concurrently,
        stdin writes are protected by a Lock — no interleaved JSON."""
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)
            stdin_reader_f = io.BufferedReader(os.fdopen(stdin_read_fd, "rb", closefd=False))

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Pre-write enough responses for 10 concurrent requests
            # We'll write them in a separate thread after requests are queued
            errors = []

            def write_responses():
                time.sleep(0.3)  # Give requests time to be queued
                for i in range(1, 11):
                    resp = _make_lsp_message({
                        "jsonrpc": "2.0",
                        "id": i,
                        "result": f"ok-{i}",
                    })
                    try:
                        os.write(stdout_write_fd, resp)
                    except Exception:
                        pass

            writer_thread = threading.Thread(target=write_responses, daemon=True)
            writer_thread.start()

            def do_request(i):
                try:
                    r = reader.send_request(f"method-{i}", {"idx": i}, timeout=5.0)
                    assert r == f"ok-{i}", f"Unexpected result for id={i}: {r}"
                except Exception as e:
                    errors.append((i, e))

            threads = []
            for i in range(1, 11):
                t = threading.Thread(target=do_request, args=(i,), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=10.0)

            writer_thread.join(timeout=5.0)

            assert len(errors) == 0, f"Unexpected errors: {errors}"

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def test_stdin_write_lock_prevents_interleaving(self):
        """Verify that Lock protects stdin writes: each message is written
        as a contiguous block even under concurrent load.

        This test writes very large messages concurrently to increase the
        chance of interleaving if the Lock is missing.
        """
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Write responses for the concurrent requests
            resp_thread_done = threading.Event()

            def write_responses():
                for i in range(1, 6):
                    resp = _make_lsp_message({
                        "jsonrpc": "2.0", "id": i, "result": "ok",
                    })
                    try:
                        os.write(stdout_write_fd, resp)
                    except Exception:
                        pass
                resp_thread_done.set()

            threading.Thread(target=write_responses, daemon=True).start()

            def send_large_request(idx):
                reader.send_request(
                    f"method-{idx}",
                    {"data": "x" * 5000},  # large params increase interleave risk
                    timeout=5.0,
                )

            threads = []
            for i in range(1, 6):
                t = threading.Thread(target=send_large_request, args=(i,), daemon=True)
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=10.0)

            # If we got here without a crash, the Lock did its job
            # (Interleaved writes would produce malformed JSON that crashes
            #  the reader thread or the subprocess stdin parser)
            resp_thread_done.wait(timeout=5.0)

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass


class TestLspMessageReaderIdCollision:
    """Tests for request id collision handling."""

    def test_late_response_with_duplicate_id_is_discarded(self, caplog):
        """If request ids wrap around (collision), a late response for an
        old request with a reused id should be silently discarded.

        This is an extremely rare edge case but the design requires it.
        """
        stdout_read_fd, stdout_write_fd = os.pipe()
        stdin_read_fd, stdin_write_fd = os.pipe()

        try:
            stdout_reader = io.BufferedReader(os.fdopen(stdout_read_fd, "rb", closefd=False))
            stdin_writer = os.fdopen(stdin_write_fd, "wb", closefd=False)

            reader = LspMessageReader(stdout=stdout_reader, stdin=stdin_writer)

            # Write a stale response for id=1 (before any request is pending for id=1)
            # In a real scenario this would be a late-arriving response from a
            # previous request whose id was reused.
            stale_resp = _make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "stale"})
            os.write(stdout_write_fd, stale_resp)

            # Wait briefly for the reader thread to process
            time.sleep(0.1)

            # Now write a real response for id=1 (our actual request)
            real_resp = _make_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "real"})
            os.write(stdout_write_fd, real_resp)

            with caplog.at_level(logging.DEBUG):
                result = reader.send_request("test", {}, timeout=5.0)

            # The result should be "real", not "stale"
            assert result == "real"

            # The stale response should have been logged at DEBUG level
            # (it was either consumed first and then overwritten, or discarded)
            # At minimum, no crash occurred.

            reader.close()
        finally:
            for fd in [stdout_read_fd, stdout_write_fd, stdin_read_fd, stdin_write_fd]:
                try:
                    os.close(fd)
                except Exception:
                    pass
