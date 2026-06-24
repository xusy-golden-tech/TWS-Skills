"""Tests for StdioTransport — MCP stdio read/write."""

import io
import json
import threading
import pytest

from tws_graph.mcp.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcNotification,
    JsonRpcError,
    ErrorCode,
    parse_message,
    serialize_message,
)
from tws_graph.mcp.transport import StdioTransport


class TestStdioTransportRead:
    """Test reading JSON-RPC messages from stdin."""

    def test_read_single_message(self):
        msg = '{"jsonrpc":"2.0","method":"tools/list","id":1}\n'
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcRequest)
        assert parsed.method == "tools/list"

    def test_read_multiple_messages(self):
        msgs = (
            '{"jsonrpc":"2.0","method":"tools/list","id":1}\n'
            '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"x"},"id":2}\n'
        )
        stdin = io.StringIO(msgs)
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)

        m1 = transport.read_message()
        assert m1 is not None
        assert isinstance(m1, JsonRpcRequest)
        assert m1.id == 1

        m2 = transport.read_message()
        assert m2 is not None
        assert isinstance(m2, JsonRpcRequest)
        assert m2.id == 2

    def test_read_notification(self):
        msg = '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcNotification)
        assert parsed.is_notification()

    def test_read_response(self):
        msg = '{"jsonrpc":"2.0","result":{"ok":true},"id":1}\n'
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcResponse)
        assert parsed.result == {"ok": True}

    def test_read_error_response(self):
        msg = '{"jsonrpc":"2.0","error":{"code":-32601,"message":"Not found"},"id":1}\n'
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcError)
        assert parsed.error["code"] == -32601

    def test_read_eof_returns_none(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        assert transport.read_message() is None

    def test_read_invalid_json_returns_error(self):
        stdin = io.StringIO("not valid json\n")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcError)
        assert parsed.error["code"] == ErrorCode.PARSE_ERROR

    def test_read_empty_line_skips(self):
        stdin = io.StringIO('\n\n{"jsonrpc":"2.0","method":"test","id":1}\n')
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        parsed = transport.read_message()
        assert parsed is not None
        assert isinstance(parsed, JsonRpcRequest)
        assert parsed.method == "test"


class TestStdioTransportWrite:
    """Test writing JSON-RPC messages to stdout."""

    def test_write_request(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        req = JsonRpcRequest(method="tools/list", id=1)
        transport.write_message(req)
        output = stdout.getvalue()
        assert output.endswith("\n")
        parsed = json.loads(output.strip())
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "tools/list"

    def test_write_response(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        resp = JsonRpcResponse(result={"tools": []}, id=1)
        transport.write_message(resp)
        output = stdout.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["result"] == {"tools": []}

    def test_write_notification(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        noti = JsonRpcNotification(method="ping")
        transport.write_message(noti)
        output = stdout.getvalue().strip()
        parsed = json.loads(output)
        assert "id" not in parsed
        assert parsed["method"] == "ping"

    def test_write_error(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        err = JsonRpcError(code=ErrorCode.METHOD_NOT_FOUND, message="Not found", id=1)
        transport.write_message(err)
        output = stdout.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["error"]["code"] == -32601

    def test_write_multiple_messages(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        transport.write_message(JsonRpcRequest(method="a", id=1))
        transport.write_message(JsonRpcResponse(result={"b": 2}, id=2))
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 2


class TestStdioTransportClose:
    """Test StdioTransport close/shutdown."""

    def test_close(self):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        transport.close()
        # After close, reading should return None
        assert transport.read_message() is None
