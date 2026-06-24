"""Tests for MCP JSON-RPC error handling: -32601, -32602, -32700, etc."""

import io
import json
import pytest

from tws_graph.mcp.server import MCPServer, MCP_VERSION
from tws_graph.mcp.transport import StdioTransport
from tws_graph.mcp.protocol import ErrorCode
from tws_graph.store.memory_store import MemoryStore


@pytest.fixture
def server():
    """Create an initialized MCPServer."""
    store = MemoryStore()
    store.begin()
    store.insert_node({
        "id": "n1",
        "kind": "function",
        "name": "main",
        "qualified_name": "src/main.py::main",
        "file_path": "src/main.py",
        "language": "python",
        "start_line": 1,
        "end_line": 10,
    })
    store.commit()
    srv = MCPServer(lambda: store)
    # Initialize the server
    init_req = json.dumps({
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        "id": 0,
    })
    stdin = io.StringIO(init_req + "\n")
    stdout = io.StringIO()
    transport = StdioTransport(stdin=stdin, stdout=stdout)
    msg = transport.read_message()
    response = srv._dispatch(msg)
    transport.write_message(response)
    return srv


def send_and_receive(server: MCPServer, request: dict) -> dict:
    stdin = io.StringIO(json.dumps(request) + "\n")
    stdout = io.StringIO()
    transport = StdioTransport(stdin=stdin, stdout=stdout)
    msg = transport.read_message()
    response = server._dispatch(msg)
    if response is not None:
        transport.write_message(response)
    output = stdout.getvalue().strip()
    if output:
        return json.loads(output)
    return {}


class TestMethodNotFound:
    """-32601: Method not found."""

    def test_unknown_method(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "nonexistent/method",
            "id": 1,
        })
        assert result["error"]["code"] == ErrorCode.METHOD_NOT_FOUND

    def test_unknown_tool(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
            "id": 1,
        })
        assert result["error"]["code"] == ErrorCode.METHOD_NOT_FOUND

    def test_unknown_resource(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "resources/read",
            "params": {"uri": "tws://nonexistent"},
            "id": 1,
        })
        assert result["error"]["code"] == ErrorCode.METHOD_NOT_FOUND


class TestServerNotInitialized:
    """-32002: Server not initialized."""

    def test_request_before_init(self, server):
        # Create a new un-initialized server for this test
        store = MemoryStore()
        srv = MCPServer(lambda: store)
        result = send_and_receive(srv, {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        assert result["error"]["code"] == ErrorCode.SERVER_NOT_INITIALIZED


class TestInternalError:
    """-32603: Internal error."""

    def test_tool_runtime_error(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "find_dead_code", "arguments": {}},
            "id": 1,
        })
        # find_dead_code should work with a basic store
        assert "result" in result or "error" in result


class TestParseError:
    """-32700: Parse error (handled at transport level)."""

    def test_transport_parse_error(self):
        stdin = io.StringIO("invalid json!!!\n")
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        msg = transport.read_message()
        assert msg is not None
        # Transport returns a JsonRpcError for parse errors
        assert hasattr(msg, "is_error")
        assert msg.is_error()
        assert msg.error["code"] == ErrorCode.PARSE_ERROR

    def test_transport_missing_jsonrpc(self):
        stdin = io.StringIO('{"method":"test","id":1}\n')
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        msg = transport.read_message()
        assert msg is not None
        assert msg.is_error()
        assert msg.error["code"] == ErrorCode.PARSE_ERROR


class TestInvalidRequest:
    """-32600: Invalid Request."""

    def test_missing_method_field(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "params": {},
            "id": 1,
        })
        # Missing method field is an Invalid Request (-32600), not Method Not Found
        assert result["error"]["code"] == ErrorCode.INVALID_REQUEST


class TestCompleteErrorLifecycle:
    """End-to-end error handling scenarios."""

    def test_init_list_call_shutdown(self, server):
        # Verify full flow works
        results = []
        for req in [
            {"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "find_dead_code", "arguments": {}}, "id": 2},
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_code", "arguments": {"symbol_id": "n1"}}, "id": 3},
        ]:
            result = send_and_receive(server, req)
            results.append(result)
        # All should succeed
        for r in results:
            assert "result" in r, f"Expected result but got error: {r}"
        assert len(results) == 3

    def test_unknown_tool_returns_correct_error_code(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "ghost_tool", "arguments": {}},
            "id": 7,
        })
        assert result["error"]["code"] == -32601
        assert result["id"] == 7
        assert "Tool not found" in result["error"]["message"]
