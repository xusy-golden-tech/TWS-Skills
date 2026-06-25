"""MCP (Model Context Protocol) server — stdio-based JSON-RPC 2.0 server.

Provides the MCPServer class that:
1. Accepts connections via stdin/stdout (stdio transport)
2. Implements the full MCP lifecycle: initialize → initialized → request/response loop
3. Dispatches tools/list, tools/call, resources/list, resources/read
4. Handles JSON-RPC errors: -32700 (parse), -32601 (method not found), -32602 (invalid params)
5. Runs the main event loop (read → dispatch → write)

Design: zero external dependencies, works with a local tws-graph index database.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

from .protocol import (
    ErrorCode,
    JsonRpcMessage,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcNotification,
    JsonRpcError,
    parse_message,
    make_error_response,
)
from .transport import StdioTransport
from .registry import ToolRegistry, ResourceRegistry
from .resources import register_resources, reset_start_time
from .tools import (
    register_search_tools,
    register_code_tools,
    register_analysis_tools,
    register_advanced_tools,
    register_query_tools,
    register_dev_assist_tools,
)
from tws_graph.store.interface import Store

logger = logging.getLogger(__name__)

# MCP protocol version
MCP_VERSION = "2024-11-05"

# Server info
SERVER_NAME = "tws-graph-mcp"
SERVER_VERSION = "0.2.0"

# Type alias for store factory
StoreFactory = Callable[[], Store]


class MCPServer:
    """MCP stdio server for tws-graph code symbol graph queries.

    Usage:
        store_factory = lambda: SqliteStore.open(db_path)
        server = MCPServer(store_factory)
        server.run()
    """

    def __init__(
        self,
        store_factory: StoreFactory,
        transport: Optional[StdioTransport] = None,
    ) -> None:
        """Initialize the MCP server.

        Args:
            store_factory: A callable that returns a Store instance on each invocation.
                This allows the server to create fresh Store connections per request.
            transport: Optional custom transport (defaults to sys.stdin/sys.stdout).
        """
        self._store_factory = store_factory
        self._transport = transport or StdioTransport()

        # Tool and resource registries
        self._tool_registry = ToolRegistry()
        self._resource_registry = ResourceRegistry()

        # Server state
        self._initialized = False
        self._server_name = SERVER_NAME
        self._server_version = SERVER_VERSION

        # Register all tools and resources
        self._register_tools()
        self._register_resources()

    # =========================================================================
    # Tool & Resource Registration
    # =========================================================================

    def _register_tools(self) -> None:
        """Register all 21 MCP tools (16 core + 5 dev-assist P44+P46b)."""
        # Each tool module's register_tools adds tools to the registry
        register_search_tools(self._tool_registry, self._store_factory)
        register_code_tools(self._tool_registry, self._store_factory)
        register_analysis_tools(self._tool_registry, self._store_factory)
        register_advanced_tools(self._tool_registry, self._store_factory)
        register_query_tools(self._tool_registry, self._store_factory)
        register_dev_assist_tools(self._tool_registry, self._store_factory)

    def _register_resources(self) -> None:
        """Register all 3 MCP resources (stats, languages, health)."""
        register_resources(self._resource_registry, self._store_factory)

    # =========================================================================
    # Main Event Loop
    # =========================================================================

    def run(self) -> None:
        """Run the MCP server main loop.

        Reads messages from stdin, dispatches them, writes responses to stdout.
        Blocks until stdin EOF or an unrecoverable error.
        """
        logger.info(
            "MCP server starting (name=%s, version=%s)",
            self._server_name,
            self._server_version,
        )

        while True:
            # Read one message from stdin
            message = self._transport.read_message()

            if message is None:
                # EOF — exit gracefully
                logger.info("MCP server: stdin EOF, shutting down")
                break

            # Dispatch based on message type
            response = self._dispatch(message)

            # Write response if there is one (notifications have no response)
            if response is not None:
                self._transport.write_message(response)

        self._transport.close()

    def _dispatch(self, message: JsonRpcMessage) -> Optional[JsonRpcMessage]:
        """Dispatch a parsed JSON-RPC message to the appropriate handler.

        Args:
            message: A parsed JSON-RPC message.

        Returns:
            A response message, or None for notifications (fire-and-forget).
        """
        if isinstance(message, JsonRpcNotification):
            # Handle notification
            return self._handle_notification(message)

        if isinstance(message, JsonRpcRequest):
            # Handle request
            return self._handle_request(message)

        if isinstance(message, JsonRpcError):
            # Error from transport
            if message.id is not None:
                # Has a request id — return the error to the client
                return message
            # Parse error with no id — cannot respond, log and discard
            logger.warning("Parse error from transport (no id): %s", message.error)
            return None

        # Should not reach here for responses (we don't send requests as server)
        logger.warning("Unexpected message type: %s", type(message).__name__)
        return None

    # =========================================================================
    # Notification Handlers
    # =========================================================================

    def _handle_notification(
        self, notification: JsonRpcNotification
    ) -> Optional[JsonRpcMessage]:
        """Handle a JSON-RPC notification (no response expected).

        MCP notifications:
        - notifications/initialized — client confirms initialization complete
        - notifications/cancelled — client cancels an in-progress request
        - notifications/exit — client requests server shutdown
        """
        method = notification.method

        if method == "notifications/initialized":
            logger.info("MCP initialization complete (client sent initialized)")
            return None

        if method == "notifications/cancelled":
            logger.info(
                "Request cancelled: %s",
                notification.params.get("requestId") if notification.params else "unknown",
            )
            return None

        if method == "notifications/exit":
            logger.info("Client requested exit")
            raise SystemExit(0)

        # Unknown notification — silently ignore per protocol spec
        return None

    # =========================================================================
    # Request Handler
    # =========================================================================

    def _handle_request(
        self, request: JsonRpcRequest
    ) -> Optional[JsonRpcMessage]:
        """Handle a JSON-RPC request and return a response.

        Args:
            request: The incoming JSON-RPC request.

        Returns:
            A JSON-RPC response or error, never None for valid requests.
        """
        method = request.method
        req_id = request.id

        try:
            # Route to the appropriate handler
            if method == "initialize":
                result = self._handle_initialize(request.params or {})
                return JsonRpcResponse(result=result, id=req_id)

            elif method == "shutdown":
                result = self._handle_shutdown()
                return JsonRpcResponse(result=result, id=req_id)

            elif method == "tools/list":
                # Check if initialized
                self._check_initialized()
                tools = self._tool_registry.list_tools()
                return JsonRpcResponse(result={"tools": tools}, id=req_id)

            elif method == "tools/call":
                self._check_initialized()
                params = request.params or {}
                tool_name = params.get("name", "")
                tool_args = params.get("arguments")
                try:
                    result = self._tool_registry.call(tool_name, tool_args)
                    return JsonRpcResponse(result=result, id=req_id)
                except KeyError:
                    return make_error_response(
                        ErrorCode.METHOD_NOT_FOUND,
                        f"Tool not found: {tool_name}",
                        id=req_id,
                    )
                except Exception as e:
                    return make_error_response(
                        ErrorCode.INTERNAL_ERROR,
                        f"Tool execution error: {e}",
                        id=req_id,
                        data={"tool": tool_name},
                    )

            elif method == "resources/list":
                self._check_initialized()
                resources = self._resource_registry.list_resources()
                return JsonRpcResponse(
                    result={"resources": resources}, id=req_id
                )

            elif method == "resources/read":
                self._check_initialized()
                params = request.params or {}
                uri = params.get("uri", "")
                try:
                    result = self._resource_registry.read(uri)
                    return JsonRpcResponse(
                        result={"contents": [result]}, id=req_id
                    )
                except KeyError:
                    return make_error_response(
                        ErrorCode.METHOD_NOT_FOUND,
                        f"Resource not found: {uri}",
                        id=req_id,
                    )
                except Exception as e:
                    return make_error_response(
                        ErrorCode.INTERNAL_ERROR,
                        f"Resource read error: {e}",
                        id=req_id,
                    )

            elif method == "ping":
                return JsonRpcResponse(result={}, id=req_id)

            else:
                # Unknown method
                return make_error_response(
                    ErrorCode.METHOD_NOT_FOUND,
                    f"Method not found: {method}",
                    id=req_id,
                )

        except ServerNotInitializedError as e:
            return make_error_response(
                ErrorCode.SERVER_NOT_INITIALIZED,
                str(e),
                id=req_id,
            )
        except SystemExit:
            raise
        except Exception as e:
            logger.exception("Unhandled error processing request %s", method)
            return make_error_response(
                ErrorCode.INTERNAL_ERROR,
                f"Internal error: {e}",
                id=req_id,
            )

    # =========================================================================
    # Lifecycle Handlers
    # =========================================================================

    def _handle_initialize(self, params: dict) -> dict:
        """Handle the initialize request.

        This is the first request a client sends. The server returns its
        capabilities and metadata.

        Args:
            params: The initialize params from the client.

        Returns:
            The initialize result with server capabilities.
        """
        self._initialized = True
        reset_start_time()

        return {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": self._server_name,
                "version": self._server_version,
            },
        }

    def _handle_shutdown(self) -> dict:
        """Handle the shutdown request."""
        self._initialized = False
        return {}

    def _check_initialized(self) -> None:
        """Raise ServerNotInitializedError if the server has not been initialized."""
        if not self._initialized:
            raise ServerNotInitializedError(
                "Server not initialized. Send 'initialize' request first."
            )


class ServerNotInitializedError(Exception):
    """Raised when a request is received before initialize."""
    pass


# =============================================================================
# Entry point helpers
# =============================================================================


def create_server(db_path: str) -> MCPServer:
    """Create an MCP server connected to the given database.

    Validates that the database file exists and is readable.

    Args:
        db_path: Path to the tws-graph SQLite index database.

    Returns:
        A configured MCPServer instance.

    Raises:
        FileNotFoundError: If the database does not exist.
    """
    import os

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Index database not found: {db_path}. "
            "Run 'tws-graph index' first to build the code graph."
        )

    from tws_graph.store.sqlite_store import SqliteStore

    def store_factory() -> Store:
        return SqliteStore.open(db_path)

    return MCPServer(store_factory)


def run_server(db_path: str) -> None:
    """Create and run an MCP server connected to the given database.

    This is the main entry point for the 'tws-graph serve' CLI command.

    Args:
        db_path: Path to the tws-graph SQLite index database.

    Raises:
        FileNotFoundError: If the database does not exist.
        SystemExit: On exit notification from client.
    """
    server = create_server(db_path)
    try:
        server.run()
    except SystemExit:
        pass
