"""Tests for MCP server lifecycle: initialize → tools/list → tools/call → shutdown."""

import io
import json
import pytest

from tws_graph.mcp.server import MCPServer, MCP_VERSION
from tws_graph.mcp.transport import StdioTransport
from tws_graph.mcp.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    parse_message,
    serialize_message,
)
from tws_graph.store.memory_store import MemoryStore


@pytest.fixture
def server():
    """Create an MCPServer with test transport (StringIO)."""
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
    return MCPServer(lambda: store)


def send_and_receive(server: MCPServer, request: dict) -> dict:
    """Simulate sending a JSON-RPC request to a server and receiving the response.

    Uses StringIO as stdin/stdout transport to test the server's dispatch logic
    without actually running the event loop.
    """
    # Build a transport that feeds the request and captures the response
    stdin = io.StringIO(json.dumps(request) + "\n")
    stdout = io.StringIO()
    transport = StdioTransport(stdin=stdin, stdout=stdout)

    msg = transport.read_message()
    assert msg is not None, f"Failed to parse message: {request}"

    response = server._dispatch(msg)
    if response is not None:
        transport.write_message(response)

    output = stdout.getvalue().strip()
    if output:
        return json.loads(output)
    return {}


class TestInitialize:
    """Test the initialize lifecycle step."""

    def test_initialize(self, server):
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
            "id": 1,
        })
        assert result["id"] == 1
        assert "result" in result
        assert result["result"]["protocolVersion"] == MCP_VERSION
        assert "capabilities" in result["result"]
        assert result["result"]["capabilities"]["tools"] == {}
        assert result["result"]["capabilities"]["resources"] == {}
        assert result["result"]["serverInfo"]["name"] == "tws-graph-mcp"

    def test_request_before_initialize_fails(self, server):
        # Server is not yet initialized; tools/list should fail
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        assert "error" in result
        assert result["error"]["code"] == -32002  # SERVER_NOT_INITIALIZED

    def test_tools_list_after_initialize(self, server):
        # Initialize first
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        # Then list tools
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2,
        })
        assert "result" in result
        tools = result["result"]["tools"]
        assert len(tools) == 21
        tool_names = {t["name"] for t in tools}
        expected_names = {
            "search_symbols", "semantic_search",
            "get_code", "get_dependencies", "get_impact", "trace_path",
            "get_complexity", "find_dead_code", "get_test_coverage", "get_entry_points",
            "find_clones", "get_git_diff_impact", "get_config_links",
            "query_cypher", "detect_cross_service", "get_edge_distribution",
            "review_changes", "safe_refactor", "api_compat_check", "find_pattern",
            "security_scan",
        }
        assert tool_names == expected_names

    def test_resources_list_after_initialize(self, server):
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "resources/list",
            "id": 2,
        })
        assert "result" in result
        resources = result["result"]["resources"]
        assert len(resources) == 3
        uris = {r["uri"] for r in resources}
        assert uris == {"tws://stats", "tws://languages", "tws://health"}

    def test_tools_call_after_initialize(self, server):
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        # Call find_dead_code (requires no params)
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "find_dead_code", "arguments": {}},
            "id": 3,
        })
        assert "result" in result
        assert "content" in result["result"]

    def test_shutdown(self, server):
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "shutdown",
            "id": 2,
        })
        assert "result" in result
        assert result["result"] == {}

    def test_ping(self, server):
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "ping",
            "id": 2,
        })
        assert "result" in result
        assert result["result"] == {}

    def test_resources_read_after_initialize(self, server):
        send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            "id": 1,
        })
        result = send_and_receive(server, {
            "jsonrpc": "2.0",
            "method": "resources/read",
            "params": {"uri": "tws://health"},
            "id": 2,
        })
        assert "result" in result
        assert "contents" in result["result"]


class TestNotificationHandling:
    """Test notification handling (fire-and-forget, no response)."""

    def test_initialized_notification(self, server):
        # Use StringIO transport to send notification
        stdin = io.StringIO('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        msg = transport.read_message()
        response = server._dispatch(msg)
        # Notifications should not produce a response
        assert response is None

    def test_cancelled_notification(self, server):
        stdin = io.StringIO('{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":5}}\n')
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        msg = transport.read_message()
        response = server._dispatch(msg)
        assert response is None

    def test_exit_notification_raises_system_exit(self, server):
        stdin = io.StringIO('{"jsonrpc":"2.0","method":"notifications/exit"}\n')
        stdout = io.StringIO()
        transport = StdioTransport(stdin=stdin, stdout=stdout)
        msg = transport.read_message()
        with pytest.raises(SystemExit):
            server._dispatch(msg)
