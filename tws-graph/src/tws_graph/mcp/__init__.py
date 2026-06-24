"""MCP (Model Context Protocol) server for tws-graph.

Provides a zero-dependency, pure-Python JSON-RPC 2.0 MCP server that exposes
tws-graph's code symbol graph as 15 tools and 3 resources over stdio transport.

Usage:
    from tws_graph.mcp.server import create_server, run_server

    # Create and run an MCP server
    run_server("/path/to/index.db")

    # Or create manually for testing
    server = create_server("/path/to/index.db")
    server.run()

Modules:
    protocol    — JSON-RPC 2.0 message types (request, response, notification, error)
    transport   — StdioTransport (read line → parse → dispatch → write line)
    registry    — ToolRegistry + ResourceRegistry
    server      — MCPServer main class (lifecycle, dispatch, stdio loop)
    resources   — 3 resource handlers (stats, languages, health)
    tools       — 15 tool handler modules (search, code, analysis, advanced, query)
"""

from .protocol import (
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

from .registry import (
    ToolRegistry,
    ResourceRegistry,
    ToolDefinition,
    ResourceDefinition,
    ToolHandler,
    ResourceHandler,
)

from .transport import StdioTransport
from .server import MCPServer, ServerNotInitializedError, create_server, run_server

__all__ = [
    # Protocol
    "JsonRpcMessage",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcNotification",
    "JsonRpcError",
    "ErrorCode",
    "parse_message",
    "serialize_message",
    "make_error_response",
    # Registry
    "ToolRegistry",
    "ResourceRegistry",
    "ToolDefinition",
    "ResourceDefinition",
    "ToolHandler",
    "ResourceHandler",
    # Transport
    "StdioTransport",
    # Server
    "MCPServer",
    "ServerNotInitializedError",
    "create_server",
    "run_server",
]
