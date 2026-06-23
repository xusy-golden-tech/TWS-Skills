"""Tests for Kubernetes manifest detector (extractor)."""

import os
import pytest
from tws_graph.indexer.extractors.kubernetes_extractor import extract as k8s_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kubernetes")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("yaml")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.yaml"):
    parser = get_parser("yaml")
    tree = parser.parse(source_str)
    return k8s_extract(source_str.encode("utf-8"), tree, file_path)


class TestKubernetesExtractor:
    """Unit tests for Kubernetes YAML resource extraction."""

    # -- Deployment tests --

    def test_deployment_resource_identity(self):
        """Deployment should have apiVersion and kind edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "apiVersion=apps/v1" in contains
        assert "kind=Deployment" in contains

    def test_deployment_metadata(self):
        """Deployment should have name and namespace edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "name=nginx-deployment" in contains
        assert "namespace=production" in contains

    def test_deployment_labels(self):
        """Deployment should extract metadata labels."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "label:app=nginx" in contains
        assert "label:tier=frontend" in contains

    def test_deployment_selector(self):
        """Deployment spec.selector should produce REFERENCES edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        refs = {e["target_text"] for e in edges if e["kind"] == "references"}
        assert "selector:app=nginx" in refs

    def test_deployment_containers(self):
        """Deployment should extract container info."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "container:nginx" in contains
        assert "container:sidecar" in contains

    def test_deployment_container_images(self):
        """Container images should produce IMPORTS edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        imports = {e["target_text"] for e in edges if e["kind"] == "imports"}
        assert "nginx:1.25-alpine" in imports
        assert "alpine:3.18" in imports

    def test_deployment_container_env(self):
        """Container env vars should produce ENV_ACCESSES edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        env_edges = {e["target_text"] for e in edges if e["kind"] == "env_accesses"}
        assert "env:NGINX_HOST=localhost" in env_edges
        assert "env:NGINX_PORT=80" in env_edges
        assert "env:DATABASE_URL=valueFrom" in env_edges

    def test_deployment_volumes(self):
        """Volumes should produce CONTAINS edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "volume:nginx-config" in contains
        assert "volume:app-secret" in contains

    def test_deployment_volume_references(self):
        """Volume configMap/secret refs should produce REFERENCES edges."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        refs = {e["target_text"] for e in edges if e["kind"] == "references"}
        assert "volume:nginx-config/configMap=nginx-config" in refs
        assert "volume:app-secret/secret=app-secret" in refs

    # -- Service tests --

    def test_service_resource(self):
        """Service should have correct resource identity."""
        source, tree = _parse_file("service.yaml")
        nodes, edges = k8s_extract(source, tree, "service.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "apiVersion=v1" in contains
        assert "kind=Service" in contains
        assert "name=nginx-service" in contains
        assert "namespace=production" in contains

    def test_service_selector(self):
        """Service selector should produce REFERENCES edges."""
        source, tree = _parse_file("service.yaml")
        nodes, edges = k8s_extract(source, tree, "service.yaml")

        refs = {e["target_text"] for e in edges if e["kind"] == "references"}
        assert "selector:app=nginx" in refs

    # -- ConfigMap tests --

    def test_configmap_resource(self):
        """ConfigMap should have correct resource identity."""
        source, tree = _parse_file("configmap.yaml")
        nodes, edges = k8s_extract(source, tree, "configmap.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "apiVersion=v1" in contains
        assert "kind=ConfigMap" in contains
        assert "name=app-config" in contains
        assert "namespace=production" in contains

    def test_configmap_data_keys(self):
        """ConfigMap data keys should produce CONTAINS edges."""
        source, tree = _parse_file("configmap.yaml")
        nodes, edges = k8s_extract(source, tree, "configmap.yaml")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "data:app.properties" in contains
        assert "data:log_level" in contains
        assert "data:max_connections" in contains
        assert "data:feature_flags" in contains

    # -- General edge format tests --

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter", f"Bad provenance: {edge}"
            assert edge["target"] == "", f"Target should be empty: {edge}"

    def test_source_hash_format(self):
        """Source IDs should be 32 hex characters."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        for edge in edges:
            assert len(edge["source"]) == 32
            assert all(c in "0123456789abcdef" for c in edge["source"])

    def test_source_loc_format(self):
        """source_loc should include the file path."""
        source, tree = _parse_file("deployment.yaml")
        nodes, edges = k8s_extract(source, tree, "deployment.yaml")

        for edge in edges:
            assert "deployment.yaml:" in edge["source_loc"]

    def test_non_k8s_yaml(self):
        """Non-Kubernetes YAML should produce no edges."""
        nodes, edges = _parse("name: my-app\nversion: 1.0.0\n")
        assert len(edges) == 0

    def test_only_k8s_with_api_version(self):
        """Only YAML with apiVersion AND recognized kind produces edges."""
        nodes, edges = _parse("apiVersion: v1\nkind: UnknownKind\nmetadata:\n  name: test\n")
        assert len(edges) == 0

    def test_non_yaml_content(self):
        """YAML without documents should produce no edges."""
        nodes, edges = _parse("# Just comments\n")
        assert len(edges) == 0
