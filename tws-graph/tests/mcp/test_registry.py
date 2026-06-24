"""Tests for ToolRegistry and ResourceRegistry."""

import json
import pytest

from tws_graph.mcp.registry import (
    ToolRegistry,
    ResourceRegistry,
    ToolDefinition,
    ResourceDefinition,
    ToolHandler,
    ResourceHandler,
)


class TestToolDefinition:
    """Test tool definition schema."""

    def test_tool_definition_minimal(self):
        td = ToolDefinition(name="test_tool", description="A test tool",
                            input_schema={"type": "object", "properties": {}})
        assert td.name == "test_tool"
        assert td.description == "A test tool"
        assert td.input_schema == {"type": "object", "properties": {}}

    def test_tool_definition_to_dict(self):
        td = ToolDefinition(
            name="search_symbols",
            description="Search for code symbols",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        )
        d = td.to_dict()
        assert d["name"] == "search_symbols"
        assert d["description"] == "Search for code symbols"
        assert "inputSchema" in d
        assert d["inputSchema"]["required"] == ["query"]


class TestResourceDefinition:
    """Test resource definition schema."""

    def test_resource_definition(self):
        rd = ResourceDefinition(uri="tws://stats", name="Statistics",
                                description="Graph statistics", mime_type="application/json")
        assert rd.uri == "tws://stats"
        assert rd.name == "Statistics"
        assert rd.mime_type == "application/json"

    def test_resource_definition_to_dict(self):
        rd = ResourceDefinition(uri="tws://health", name="Health Check",
                                description="Server health", mime_type="application/json")
        d = rd.to_dict()
        assert d["uri"] == "tws://health"
        assert d["name"] == "Health Check"
        assert d["mimeType"] == "application/json"


class TestToolRegistry:
    """Test ToolRegistry operations."""

    def test_register_and_list(self):
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="test1",
                description="Test tool 1",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            ),
            handler=lambda args: {"content": [{"type": "text", "text": str(args)}]},
        )
        tools = registry.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test1"

    def test_register_duplicate_raises(self):
        registry = ToolRegistry()
        td = ToolDefinition(name="dup", description="Dup",
                           input_schema={"type": "object"})
        registry.register(td, handler=lambda args: {})
        with pytest.raises(ValueError, match="already registered"):
            registry.register(td, handler=lambda args: {})

    def test_unregister(self):
        registry = ToolRegistry()
        td = ToolDefinition(name="temp", description="Temp",
                           input_schema={"type": "object"})
        registry.register(td, handler=lambda args: {})
        assert len(registry.list_tools()) == 1
        registry.unregister("temp")
        assert len(registry.list_tools()) == 0

    def test_unregister_missing(self):
        registry = ToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.unregister("nonexistent")

    def test_call_tool(self):
        registry = ToolRegistry()

        def my_handler(args):
            return {"content": [{"type": "text", "text": f"result: {args['q']}"}]}

        registry.register(
            ToolDefinition(name="echo", description="Echo",
                          input_schema={"type": "object"}),
            handler=my_handler,
        )
        result = registry.call("echo", {"q": "hello"})
        assert result["content"][0]["text"] == "result: hello"

    def test_call_nonexistent_tool(self):
        registry = ToolRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.call("nonexistent", {})

    def test_call_handler_exception(self):
        registry = ToolRegistry()
        td = ToolDefinition(name="fail", description="Fails",
                           input_schema={"type": "object"})

        def bad_handler(args):
            raise RuntimeError("BOOM")

        registry.register(td, handler=bad_handler)
        with pytest.raises(RuntimeError, match="BOOM"):
            registry.call("fail", {})

    def test_list_tools_empty(self):
        registry = ToolRegistry()
        assert registry.list_tools() == []

    def test_list_tools_schema_format(self):
        """Verify output conforms to tools/list response format."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="my_tool",
                description="Does something",
                input_schema={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
            handler=lambda args: {},
        )
        tools = registry.list_tools()
        tool = tools[0]
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"

    def test_call_tool_with_args_none(self):
        registry = ToolRegistry()

        def handler(args):
            assert args is None
            return {"content": []}

        registry.register(
            ToolDefinition(name="no_params", description="No params",
                          input_schema={"type": "object"}),
            handler=handler,
        )
        result = registry.call("no_params", None)
        assert result == {"content": []}


class TestResourceRegistry:
    """Test ResourceRegistry operations."""

    def test_register_and_list(self):
        registry = ResourceRegistry()

        def stats_handler(uri):
            return {"node_count": 100}

        registry.register(
            ResourceDefinition(uri="tws://stats", name="Stats",
                             description="Stats", mime_type="application/json"),
            handler=stats_handler,
        )
        resources = registry.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "tws://stats"

    def test_read_resource(self):
        registry = ResourceRegistry()

        def health_handler(uri):
            return {"status": "ok"}

        registry.register(
            ResourceDefinition(uri="tws://health", name="Health",
                             description="Health", mime_type="application/json"),
            handler=health_handler,
        )
        result = registry.read("tws://health")
        assert result["uri"] == "tws://health"
        assert result["mimeType"] == "application/json"
        assert json.loads(result["text"]) == {"status": "ok"}

    def test_read_nonexistent_resource(self):
        registry = ResourceRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.read("tws://nonexistent")

    def test_register_duplicate_raises(self):
        registry = ResourceRegistry()
        rd = ResourceDefinition(uri="tws://dup", name="Dup",
                              description="Dup", mime_type="application/json")
        registry.register(rd, handler=lambda uri: {})
        with pytest.raises(ValueError, match="already registered"):
            registry.register(rd, handler=lambda uri: {})

    def test_list_resources_empty(self):
        registry = ResourceRegistry()
        assert registry.list_resources() == []


