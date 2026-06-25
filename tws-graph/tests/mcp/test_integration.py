"""End-to-end integration tests for the MCP server.

Tests the complete MCP lifecycle: initialize → initialized → tools/list →
tools/call → resources/list → resources/read → shutdown.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

from tws_graph.mcp.server import MCPServer, MCP_VERSION
from tws_graph.mcp.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcError,
    ErrorCode,
    parse_message,
)
from tws_graph.mcp.transport import StdioTransport
from tws_graph.store.memory_store import MemoryStore

import io


@pytest.fixture
def server_with_data():
    """Create a fully initialized MCPServer with test data."""
    store = MemoryStore()
    store.begin()
    store.upsert_file(
        path="src/main.py",
        content_hash="abc",
        language="python",
        node_count=2,
        size=100,
        modified_at=1000,
    )
    nodes = [
        {"id": "n1", "kind": "function", "name": "main", "qualified_name": "src/main.py::main",
         "file_path": "src/main.py", "language": "python", "start_line": 1, "end_line": 10},
        {"id": "n2", "kind": "function", "name": "foo", "qualified_name": "src/main.py::foo",
         "file_path": "src/main.py", "language": "python", "start_line": 12, "end_line": 20},
    ]
    for node in nodes:
        store.insert_node(node)
    store.insert_edge({
        "source": "n1",
        "target": "n2",
        "kind": "calls",
        "provenance": "tree-sitter",
    })
    store.commit()
    return MCPServer(lambda: store)


class TestEndToEndLifecycle:
    """End-to-end MCP lifecycle using in-memory server."""

    def test_full_lifecycle(self, server_with_data):
        """Complete MCP lifecycle: initialize → tools/list → tools/call → shutdown."""
        server = server_with_data

        # Step 1: Initialize
        init_result = _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        assert init_result["protocolVersion"] == MCP_VERSION
        assert "tools" in init_result["capabilities"]

        # Step 2: Send initialized notification
        _send_notification(server, "notifications/initialized")

        # Step 3: List tools
        tools_result = _send_request(server, "tools/list", {})
        assert len(tools_result["tools"]) == 20
        tool_names = {t["name"] for t in tools_result["tools"]}
        assert "search_symbols" in tool_names
        assert "get_code" in tool_names
        assert "query_cypher" in tool_names
        assert "review_changes" in tool_names

        # Step 4: List resources
        resources_result = _send_request(server, "resources/list", {})
        resources = resources_result["resources"]
        assert len(resources) == 3

        # Step 5: Read a resource
        health_result = _send_request(server, "resources/read", {"uri": "tws://health"})
        assert "contents" in health_result
        health_data = json.loads(health_result["contents"][0]["text"])
        assert "status" in health_data

        # Step 6: Call a tool
        code_result = _send_request(server, "tools/call", {
            "name": "get_code",
            "arguments": {"symbol_id": "n1"},
        })
        code_content = json.loads(code_result["content"][0]["text"])
        assert code_content["name"] == "main"

        # Step 7: Call another tool
        dead_code_result = _send_request(server, "tools/call", {
            "name": "find_dead_code",
            "arguments": {},
        })
        dead_data = json.loads(dead_code_result["content"][0]["text"])
        assert "results" in dead_data or "error" in dead_data

        # Step 8: Shutdown
        shutdown_result = _send_request(server, "shutdown", {})
        assert shutdown_result == {}

    def test_tool_call_with_args(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        _send_notification(server, "notifications/initialized")

        # Call search_symbols with specific query
        result = _send_request(server, "tools/call", {
            "name": "search_symbols",
            "arguments": {"query": "main"},
        })
        assert "content" in result

    def test_tool_call_missing_args(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        _send_notification(server, "notifications/initialized")

        # Call search_symbols without query
        result = _send_request(server, "tools/call", {
            "name": "search_symbols",
            "arguments": {},
        })
        content_data = json.loads(result["content"][0]["text"])
        assert content_data["count"] == 0

    def test_error_on_unknown_tool(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        _send_notification(server, "notifications/initialized")

        result = _send_request_raw(server, "tools/call",
                                   {"name": "ghost_tool", "arguments": {}}, expect_error=True)
        assert result.error["code"] == ErrorCode.METHOD_NOT_FOUND

    def test_ping(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        result = _send_request(server, "ping", {})
        assert result == {}

    def test_all_20_tools_registered(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })

        tools = _send_request(server, "tools/list", {})
        tool_names = {t["name"] for t in tools["tools"]}
        expected = {
            "search_symbols", "semantic_search",
            "get_code", "get_dependencies", "get_impact", "trace_path",
            "get_complexity", "find_dead_code", "get_test_coverage", "get_entry_points",
            "find_clones", "get_git_diff_impact", "get_config_links",
            "query_cypher", "detect_cross_service", "get_edge_distribution",
            "review_changes", "safe_refactor", "api_compat_check", "find_pattern",
        }
        assert tool_names == expected

    def test_all_3_resources_registered(self, server_with_data):
        server = server_with_data
        _send_request(server, "initialize", {
            "protocolVersion": MCP_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })

        resources = _send_request(server, "resources/list", {})
        uris = {r["uri"] for r in resources["resources"]}
        assert uris == {"tws://stats", "tws://languages", "tws://health"}


# ============================================================================
# Helpers
# ============================================================================


def _send_request(server: MCPServer, method: str, params: dict) -> dict:
    """Send a JSON-RPC request and return the result dict."""
    response = _send_request_raw(server, method, params, expect_error=False)
    if isinstance(response, JsonRpcError):
        raise AssertionError(
            f"Expected success but got error: {response.error}"
        )
    return response.result


def _send_request_raw(
    server: MCPServer,
    method: str,
    params: dict,
    expect_error: bool = False,
):
    """Send a request and return the raw response message."""
    import threading
    req_id = _next_id()
    req = JsonRpcRequest(method=method, params=params, id=req_id)
    request_json = json.dumps(req.to_dict(), ensure_ascii=False)

    stdin = io.StringIO(request_json + "\n")
    stdout = io.StringIO()
    transport = StdioTransport(stdin=stdin, stdout=stdout)

    msg = transport.read_message()
    assert msg is not None

    response = server._dispatch(msg)
    if response is not None:
        transport.write_message(response)

    output = stdout.getvalue().strip()
    if output:
        parsed = parse_message(output)
        return parsed
    return None


def _send_notification(server: MCPServer, method: str, params: dict = None):
    """Send a notification (no response expected)."""
    noti_json = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }, ensure_ascii=False)

    stdin = io.StringIO(noti_json + "\n")
    stdout = io.StringIO()
    transport = StdioTransport(stdin=stdin, stdout=stdout)

    msg = transport.read_message()
    assert msg is not None
    response = server._dispatch(msg)
    assert response is None  # Notifications produce no response


_id_counter = 0
_id_lock = __import__('threading').Lock()


def _next_id():
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter
