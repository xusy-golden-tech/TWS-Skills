"""Tests for MCP resources: tws://stats, tws://languages, tws://health."""

import json
import pytest

from tws_graph.mcp.registry import ResourceRegistry
from tws_graph.mcp.resources import register_resources, reset_start_time
from tws_graph.store.memory_store import MemoryStore


@pytest.fixture
def store_with_data():
    """A MemoryStore with test data for resource queries."""
    store = MemoryStore()
    store.begin()

    store.upsert_file(
        path="src/main.py",
        content_hash="abc123",
        language="python",
        node_count=3,
        size=100,
        modified_at=1000,
    )
    store.upsert_file(
        path="src/utils.py",
        content_hash="def456",
        language="python",
        node_count=2,
        size=200,
        modified_at=1000,
    )

    nodes = [
        {"id": "n1", "kind": "function", "name": "main", "qualified_name": "src/main.py::main",
         "file_path": "src/main.py", "language": "python", "start_line": 1, "end_line": 10},
        {"id": "n2", "kind": "function", "name": "foo", "qualified_name": "src/main.py::foo",
         "file_path": "src/main.py", "language": "python", "start_line": 12, "end_line": 20},
        {"id": "n3", "kind": "class", "name": "App", "qualified_name": "src/main.py::App",
         "file_path": "src/main.py", "language": "python", "start_line": 22, "end_line": 30},
        {"id": "n4", "kind": "function", "name": "helper", "qualified_name": "src/utils.py::helper",
         "file_path": "src/utils.py", "language": "python", "start_line": 1, "end_line": 8},
        {"id": "n5", "kind": "function", "name": "util", "qualified_name": "src/utils.py::util",
         "file_path": "src/utils.py", "language": "typescript", "start_line": 10, "end_line": 18},
    ]
    for node in nodes:
        store.insert_node(node)

    store.commit()
    return store


def make_factory(store):
    return lambda: store


class TestResourceRegistry:
    """Test that resources can be registered and read."""

    def test_list_resources(self, store_with_data):
        registry = ResourceRegistry()
        register_resources(registry, make_factory(store_with_data))
        resources = registry.list_resources()
        assert len(resources) == 3
        uris = {r["uri"] for r in resources}
        assert uris == {"tws://stats", "tws://languages", "tws://health"}

    def test_read_stats(self, store_with_data):
        registry = ResourceRegistry()
        register_resources(registry, make_factory(store_with_data))
        result = registry.read("tws://stats")
        assert result["uri"] == "tws://stats"
        assert result["mimeType"] == "application/json"
        data = json.loads(result["text"])
        assert data["node_count"] == 5
        assert data["file_count"] >= 1

    def test_read_languages(self, store_with_data):
        registry = ResourceRegistry()
        register_resources(registry, make_factory(store_with_data))
        result = registry.read("tws://languages")
        assert result["uri"] == "tws://languages"
        data = json.loads(result["text"])
        lang_names = {l["name"] for l in data["languages"]}
        assert "python" in lang_names

    def test_read_health(self, store_with_data):
        reset_start_time()
        registry = ResourceRegistry()
        register_resources(registry, make_factory(store_with_data))
        result = registry.read("tws://health")
        assert result["uri"] == "tws://health"
        data = json.loads(result["text"])
        assert data["status"] in ("ok", "degraded")
        assert "index_ready" in data
        assert "uptime_seconds" in data

    def test_read_nonexistent_resource(self, store_with_data):
        registry = ResourceRegistry()
        register_resources(registry, make_factory(store_with_data))
        with pytest.raises(KeyError):
            registry.read("tws://nonexistent")

    def test_health_with_empty_store(self, empty_store):
        reset_start_time()
        registry = ResourceRegistry()
        register_resources(registry, make_factory(empty_store))
        result = registry.read("tws://health")
        data = json.loads(result["text"])
        assert data["status"] == "degraded"
        assert data["index_ready"] is False

    def test_stats_with_empty_store(self, empty_store):
        registry = ResourceRegistry()
        register_resources(registry, make_factory(empty_store))
        result = registry.read("tws://stats")
        data = json.loads(result["text"])
        assert data["node_count"] == 0
        assert data["edge_count"] == 0


@pytest.fixture
def empty_store():
    return MemoryStore()
