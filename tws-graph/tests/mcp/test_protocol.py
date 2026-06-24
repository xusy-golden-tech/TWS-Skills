"""Tests for JSON-RPC 2.0 message encoding/decoding in MCP protocol."""

import json
import pytest

from tws_graph.mcp.protocol import (
    JsonRpcMessage,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcNotification,
    JsonRpcError,
    ErrorCode,
    parse_message,
    serialize_message,
    make_error_response,
)


class TestJsonRpcRequest:
    """Test JSON-RPC 2.0 request message creation and serialization."""

    def test_create_request(self):
        req = JsonRpcRequest(method="tools/list", params={}, id=1)
        assert req.jsonrpc == "2.0"
        assert req.method == "tools/list"
        assert req.params == {}
        assert req.id == 1

    def test_serialize_request(self):
        req = JsonRpcRequest(method="tools/call", params={"name": "search"}, id=2)
        msg = req.to_dict()
        assert msg == {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "search"},
            "id": 2,
        }

    def test_request_without_params(self):
        req = JsonRpcRequest(method="exit", id=5)
        assert req.params is None

    def test_request_str_roundtrip(self):
        req = JsonRpcRequest(method="test", params={"x": 1}, id=42)
        data = json.dumps(req.to_dict())
        parsed = parse_message(data)
        assert isinstance(parsed, JsonRpcRequest)
        assert parsed.method == "test"
        assert parsed.params == {"x": 1}
        assert parsed.id == 42


class TestJsonRpcResponse:
    """Test JSON-RPC 2.0 response (success) message."""

    def test_create_response(self):
        resp = JsonRpcResponse(result={"tools": []}, id=1)
        assert resp.jsonrpc == "2.0"
        assert resp.result == {"tools": []}
        assert resp.id == 1
        assert not resp.is_error()

    def test_serialize_response(self):
        resp = JsonRpcResponse(result={"key": "val"}, id=3)
        msg = resp.to_dict()
        assert msg == {"jsonrpc": "2.0", "result": {"key": "val"}, "id": 3}

    def test_response_str_roundtrip(self):
        resp = JsonRpcResponse(result={"data": [1, 2, 3]}, id=100)
        data = json.dumps(resp.to_dict())
        parsed = parse_message(data)
        assert isinstance(parsed, JsonRpcResponse)
        assert parsed.result == {"data": [1, 2, 3]}
        assert parsed.id == 100


class TestJsonRpcNotification:
    """Test JSON-RPC 2.0 notification (no id field)."""

    def test_create_notification(self):
        noti = JsonRpcNotification(method="notifications/initialized", params={})
        assert noti.jsonrpc == "2.0"
        assert noti.method == "notifications/initialized"
        assert noti.id is None
        assert noti.is_notification()

    def test_serialize_notification(self):
        noti = JsonRpcNotification(method="notifications/cancelled", params={"requestId": 7})
        msg = noti.to_dict()
        assert msg == {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 7},
        }
        assert "id" not in msg

    def test_notification_roundtrip(self):
        noti = JsonRpcNotification(method="ping", params={})
        data = json.dumps(noti.to_dict())
        parsed = parse_message(data)
        assert isinstance(parsed, JsonRpcNotification)
        assert parsed.method == "ping"


class TestJsonRpcError:
    """Test JSON-RPC 2.0 error response."""

    def test_create_error(self):
        err = JsonRpcError(code=ErrorCode.METHOD_NOT_FOUND, message="Method not found", id=1)
        assert err.jsonrpc == "2.0"
        assert err.error["code"] == -32601
        assert err.error["message"] == "Method not found"
        assert err.id == 1
        assert err.is_error()

    def test_create_error_with_data(self):
        err = JsonRpcError(code=-32000, message="Custom error", data={"detail": "bad"}, id=9)
        assert err.error["code"] == -32000
        assert err.error["data"] == {"detail": "bad"}

    def test_serialize_error(self):
        err = JsonRpcError(code=ErrorCode.INVALID_PARAMS, message="Bad params", id=2)
        msg = err.to_dict()
        assert msg["error"]["code"] == -32602
        assert msg["id"] == 2

    def test_make_error_response(self):
        resp = make_error_response(ErrorCode.PARSE_ERROR, "Parse error", id=None)
        assert resp.id is None
        assert resp.error["code"] == -32700


class TestParseMessage:
    """Test parse_message function for all message types."""

    def test_parse_request(self):
        data = '{"jsonrpc":"2.0","method":"tools/list","id":1}'
        msg = parse_message(data)
        assert isinstance(msg, JsonRpcRequest)
        assert msg.method == "tools/list"
        assert msg.id == 1

    def test_parse_response_success(self):
        data = '{"jsonrpc":"2.0","result":{"tools":[]},"id":1}'
        msg = parse_message(data)
        assert isinstance(msg, JsonRpcResponse)
        assert msg.result == {"tools": []}

    def test_parse_response_error(self):
        data = '{"jsonrpc":"2.0","error":{"code":-32601,"message":"Not found"},"id":1}'
        msg = parse_message(data)
        assert isinstance(msg, JsonRpcError)
        assert msg.error["code"] == -32601

    def test_parse_notification(self):
        data = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
        msg = parse_message(data)
        assert isinstance(msg, JsonRpcNotification)
        assert msg.method == "notifications/initialized"

    def test_parse_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_message("not json")

    def test_parse_missing_jsonrpc(self):
        with pytest.raises(ValueError, match="jsonrpc"):
            parse_message('{"method":"test","id":1}')

    def test_parse_wrong_jsonrpc(self):
        with pytest.raises(ValueError, match="jsonrpc"):
            parse_message('{"jsonrpc":"1.0","method":"test","id":1}')

    def test_parse_missing_method_and_result_and_error(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_message('{"jsonrpc":"2.0","id":1}')


class TestErrorCodes:
    """Test predefined JSON-RPC error codes."""

    def test_standard_error_codes(self):
        assert ErrorCode.PARSE_ERROR == -32700
        assert ErrorCode.INVALID_REQUEST == -32600
        assert ErrorCode.METHOD_NOT_FOUND == -32601
        assert ErrorCode.INVALID_PARAMS == -32602
        assert ErrorCode.INTERNAL_ERROR == -32603

    def test_server_error_range(self):
        """Server errors are in range [-32099, -32000]."""
        assert -32099 <= ErrorCode.SERVER_NOT_INITIALIZED <= -32000
        assert -32099 <= ErrorCode.SERVER_SHUTTING_DOWN <= -32000


class TestSerializeMessage:
    """Test serialize_message function."""

    def test_serialize_request(self):
        req = JsonRpcRequest(method="test", params={"key": "val"}, id=5)
        data = serialize_message(req)
        parsed = json.loads(data)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "test"
        assert parsed["id"] == 5

    def test_serialize_response(self):
        resp = JsonRpcResponse(result={"ok": True}, id=5)
        data = serialize_message(resp)
        parsed = json.loads(data)
        assert parsed["result"] == {"ok": True}

    def test_serialize_notification(self):
        noti = JsonRpcNotification(method="ping", params=None)
        data = serialize_message(noti)
        parsed = json.loads(data)
        assert "id" not in parsed
        assert parsed["method"] == "ping"


class TestJsonRpcMessageBaseClass:
    """Test the abstract base class."""

    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            JsonRpcMessage(jsonrpc="2.0")  # type: ignore

    def test_is_notification_on_request(self):
        req = JsonRpcRequest(method="test", id=1)
        assert not req.is_notification()

    def test_is_notification_on_noti(self):
        noti = JsonRpcNotification(method="test")
        assert noti.is_notification()

    def test_is_error_on_response(self):
        resp = JsonRpcResponse(result={}, id=1)
        assert not resp.is_error()

    def test_is_error_on_error(self):
        err = JsonRpcError(code=-1, message="x", id=1)
        assert err.is_error()
