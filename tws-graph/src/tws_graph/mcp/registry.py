"""Tool and resource registries for MCP server.

Provides:
- ToolDefinition / ResourceDefinition: schema descriptors
- ToolRegistry: register, list, call tools
- ResourceRegistry: register, list, read resources

The registry pattern allows tools and resources to be registered independently
and later composed into a complete MCP server.

Handler signatures:
    ToolHandler: Callable[[dict | None], dict]
        args: tool arguments from tools/call params, or None if no arguments
        returns: {"content": [{"type": "text", "text": "..."}]}

    ResourceHandler: Callable[[str], Any]
        uri: the resource URI being read
        returns: a JSON-serializable object
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ============================================================================
# Type Aliases
# ============================================================================

ToolHandler = Callable[[Optional[dict]], dict]
"""A tool handler function: receives arguments dict, returns MCP result dict."""

ResourceHandler = Callable[[str], Any]
"""A resource handler function: receives URI string, returns JSON-serializable data."""


# ============================================================================
# Definition Schemas
# ============================================================================


@dataclass
class ToolDefinition:
    """Schema descriptor for a tool registered with the MCP server.

    This corresponds to the ``tools/list`` response format, which includes
    ``name``, ``description``, and ``inputSchema`` (a JSON Schema object).
    """

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to the tools/list response format."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class ResourceDefinition:
    """Schema descriptor for a resource registered with the MCP server.

    This corresponds to the ``resources/list`` response format.
    """

    uri: str
    name: str
    description: str = ""
    mime_type: str = "application/json"

    def to_dict(self) -> dict:
        """Serialize to the resources/list response format."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


# ============================================================================
# Tool Registry
# ============================================================================


class ToolRegistry:
    """Registers and manages tool definitions and their handlers.

    Provides:
    - register(def, handler): add a tool
    - unregister(name): remove a tool
    - list_tools(): return all registered tools in MCP tools/list format
    - call(name, args): invoke a tool handler with arguments
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, ToolHandler]] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        """Register a tool definition with its handler.

        Args:
            definition: The tool's metadata and input schema.
            handler: Callable that receives arguments dict and returns a result dict.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if definition.name in self._tools:
            raise ValueError(
                f"Tool '{definition.name}' is already registered"
            )
        self._tools[definition.name] = (definition, handler)

    def unregister(self, name: str) -> None:
        """Remove a registered tool.

        Args:
            name: The tool name to remove.

        Raises:
            KeyError: If the tool is not found.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found")
        del self._tools[name]

    def list_tools(self) -> list[dict]:
        """List all registered tools in MCP tools/list response format.

        Returns:
            A list of tool dicts, each with name, description, and inputSchema.
        """
        return [definition.to_dict() for definition, _ in self._tools.values()]

    def call(self, name: str, args: Optional[dict]) -> dict:
        """Call a registered tool handler.

        Args:
            name: The tool name.
            args: The arguments dict from the tools/call request params.

        Returns:
            The dict returned by the handler (should be in MCP format).

        Raises:
            KeyError: If the tool is not found.
            Any exception raised by the handler itself.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found")
        _, handler = self._tools[name]
        return handler(args)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ============================================================================
# Resource Registry
# ============================================================================


class ResourceRegistry:
    """Registers and manages resource definitions and their read handlers.

    Provides:
    - register(def, handler): add a resource
    - list_resources(): return all registered resources in MCP resources/list format
    - read(uri): read a resource, returning MCP resources/read response format
    """

    def __init__(self) -> None:
        self._resources: dict[str, tuple[ResourceDefinition, ResourceHandler]] = {}

    def register(
        self, definition: ResourceDefinition, handler: ResourceHandler
    ) -> None:
        """Register a resource definition with its read handler.

        Args:
            definition: The resource's metadata.
            handler: Callable that receives the URI and returns JSON-serializable data.

        Raises:
            ValueError: If a resource with the same URI is already registered.
        """
        if definition.uri in self._resources:
            raise ValueError(
                f"Resource '{definition.uri}' is already registered"
            )
        self._resources[definition.uri] = (definition, handler)

    def list_resources(self) -> list[dict]:
        """List all registered resources in MCP resources/list response format.

        Returns:
            A list of resource dicts with uri, name, description, mimeType.
        """
        return [
            definition.to_dict()
            for definition, _ in self._resources.values()
        ]

    def read(self, uri: str) -> dict:
        """Read a resource by URI.

        Returns a dict in the MCP resources/read response format:
        {
            "uri": "...",
            "mimeType": "...",
            "text": "<JSON string of the data>"
        }

        Args:
            uri: The resource URI to read.

        Returns:
            Dict with uri, mimeType, and text (JSON-encoded data).

        Raises:
            KeyError: If the resource URI is not found.
        """
        if uri not in self._resources:
            raise KeyError(f"Resource '{uri}' not found")
        definition, handler = self._resources[uri]
        data = handler(uri)
        return {
            "uri": definition.uri,
            "mimeType": definition.mime_type,
            "text": json.dumps(data, ensure_ascii=False),
        }

    def __len__(self) -> int:
        return len(self._resources)

    def __contains__(self, uri: str) -> bool:
        return uri in self._resources
