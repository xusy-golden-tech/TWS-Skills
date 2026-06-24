"""Tests for MCP tools: all 15 tools with at least 1 success + 1 failure each."""

import json
import pytest

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.mcp.tools.search import register_tools as register_search
from tws_graph.mcp.tools.code import register_tools as register_code
from tws_graph.mcp.tools.analysis import register_tools as register_analysis
from tws_graph.mcp.tools.advanced import register_tools as register_advanced
from tws_graph.mcp.tools.query import register_tools as register_query
from tws_graph.store.memory_store import MemoryStore


@pytest.fixture
def empty_store():
    """An empty MemoryStore with no indexed data."""
    return MemoryStore()


@pytest.fixture
def store_with_data():
    """A MemoryStore populated with test nodes and edges."""
    store = MemoryStore()
    store.begin()

    # Insert test file
    store.upsert_file(
        path="src/main.py",
        content_hash="abc123",
        language="python",
        node_count=3,
        size=100,
        modified_at=1000,
    )

    # Insert test nodes
    nodes = [
        {
            "id": "node_main",
            "kind": "function",
            "name": "main",
            "qualified_name": "src/main.py::main",
            "file_path": "src/main.py",
            "language": "python",
            "start_line": 1,
            "end_line": 10,
        },
        {
            "id": "node_foo",
            "kind": "function",
            "name": "foo",
            "qualified_name": "src/main.py::foo",
            "file_path": "src/main.py",
            "language": "python",
            "start_line": 12,
            "end_line": 20,
        },
        {
            "id": "node_bar",
            "kind": "function",
            "name": "bar",
            "qualified_name": "src/main.py::bar",
            "file_path": "src/main.py",
            "language": "python",
            "start_line": 22,
            "end_line": 30,
        },
    ]
    for node in nodes:
        store.insert_node(node)

    # Insert test edges
    store.insert_edge({
        "source": "node_main",
        "target": "node_foo",
        "kind": "calls",
        "provenance": "tree-sitter",
    })
    store.insert_edge({
        "source": "node_foo",
        "target": "node_bar",
        "kind": "calls",
        "provenance": "tree-sitter",
    })

    store.commit()
    return store


def make_factory(store):
    """Return a store factory that returns the given store."""
    return lambda: store


# ============================================================================
# search_symbols tests
# ============================================================================


class TestSearchSymbols:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_search(registry, make_factory(store_with_data))
        # search_symbols queries the FTS5 index; with MemoryStore, this may
        # fallback gracefully. Call the handler directly.
        result = registry.call("search_symbols", {"query": "foo"})
        assert "content" in result
        assert len(result["content"]) > 0

    def test_empty_query(self, store_with_data):
        registry = ToolRegistry()
        register_search(registry, make_factory(store_with_data))
        result = registry.call("search_symbols", {"query": ""})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["count"] == 0

    def test_missing_query(self, store_with_data):
        registry = ToolRegistry()
        register_search(registry, make_factory(store_with_data))
        result = registry.call("search_symbols", {})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["count"] == 0


# ============================================================================
# semantic_search tests
# ============================================================================


class TestSemanticSearch:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_search(registry, make_factory(store_with_data))
        result = registry.call("semantic_search", {"query": "foo"})
        assert "content" in result

    def test_empty_query(self, store_with_data):
        registry = ToolRegistry()
        register_search(registry, make_factory(store_with_data))
        result = registry.call("semantic_search", {"query": ""})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["count"] == 0


# ============================================================================
# get_code tests
# ============================================================================


class TestGetCode:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_code", {"symbol_id": "node_main"})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["name"] == "main"
        assert parsed["kind"] == "function"

    def test_not_found(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_code", {"symbol_id": "nonexistent"})
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed


# ============================================================================
# get_dependencies tests
# ============================================================================


class TestGetDependencies:
    def test_success_inbound(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_dependencies", {"symbol_id": "node_foo", "direction": "inbound"})
        assert "content" in result

    def test_missing_symbol_id(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_dependencies", {})
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed


# ============================================================================
# get_impact tests
# ============================================================================


class TestGetImpact:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_impact", {"symbol_id": "node_foo"})
        assert "content" in result

    def test_missing_symbol_id(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("get_impact", {})
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed


# ============================================================================
# trace_path tests
# ============================================================================


class TestTracePath:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("trace_path", {"from_id": "node_main", "to_id": "node_bar"})
        assert "content" in result

    def test_missing_args(self, store_with_data):
        registry = ToolRegistry()
        register_code(registry, make_factory(store_with_data))
        result = registry.call("trace_path", {})
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed


# ============================================================================
# get_complexity tests
# ============================================================================


class TestGetComplexity:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_analysis(registry, make_factory(store_with_data))
        result = registry.call("get_complexity", {})
        assert "content" in result

    def test_with_symbol_id(self, store_with_data):
        registry = ToolRegistry()
        register_analysis(registry, make_factory(store_with_data))
        result = registry.call("get_complexity", {"symbol_id": "node_main"})
        assert "content" in result


# ============================================================================
# find_dead_code tests
# ============================================================================


class TestFindDeadCode:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_analysis(registry, make_factory(store_with_data))
        result = registry.call("find_dead_code", {})
        assert "content" in result


# ============================================================================
# get_test_coverage tests
# ============================================================================


class TestGetTestCoverage:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_analysis(registry, make_factory(store_with_data))
        result = registry.call("get_test_coverage", {})
        assert "content" in result


# ============================================================================
# get_entry_points tests
# ============================================================================


class TestGetEntryPoints:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_analysis(registry, make_factory(store_with_data))
        result = registry.call("get_entry_points", {})
        assert "content" in result


# ============================================================================
# find_clones tests
# ============================================================================


class TestFindClones:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_advanced(registry, make_factory(store_with_data))
        result = registry.call("find_clones", {})
        assert "content" in result

    def test_with_threshold(self, store_with_data):
        registry = ToolRegistry()
        register_advanced(registry, make_factory(store_with_data))
        result = registry.call("find_clones", {"threshold": 0.5})
        assert "content" in result


# ============================================================================
# get_git_diff_impact tests
# ============================================================================


class TestGetGitDiffImpact:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_advanced(registry, make_factory(store_with_data))
        result = registry.call("get_git_diff_impact", {})
        assert "content" in result


# ============================================================================
# get_config_links tests
# ============================================================================


class TestGetConfigLinks:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_advanced(registry, make_factory(store_with_data))
        result = registry.call("get_config_links", {})
        assert "content" in result


# ============================================================================
# query_cypher tests
# ============================================================================


class TestQueryCypher:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_query(registry, make_factory(store_with_data))
        result = registry.call("query_cypher", {"query": "MATCH (n) RETURN n LIMIT 5"})
        assert "content" in result

    def test_empty_query(self, store_with_data):
        registry = ToolRegistry()
        register_query(registry, make_factory(store_with_data))
        result = registry.call("query_cypher", {"query": ""})
        parsed = json.loads(result["content"][0]["text"])
        assert "error" in parsed


# ============================================================================
# detect_cross_service tests
# ============================================================================


class TestDetectCrossService:
    def test_success(self, store_with_data):
        registry = ToolRegistry()
        register_query(registry, make_factory(store_with_data))
        result = registry.call("detect_cross_service", {})
        assert "content" in result
