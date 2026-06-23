"""Tests for YAML extractor."""

import os
import pytest
from tws_graph.indexer.extractors.yaml_extractor import extract as yaml_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "yaml")


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
    return yaml_extract(source_str.encode("utf-8"), tree, file_path)


class TestYamlExtractor:
    """Unit tests for YAML AST extraction."""

    def test_top_level_keys(self):
        """Top-level mapping keys should produce CONTAINS edges."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        top_keys = {"apiVersion", "kind", "metadata", "spec", "env", "data"}
        contains_edges = {e["target_text"] for e in edges if e["kind"] == "contains"}
        for key in top_keys:
            assert key in contains_edges, f"Missing top-level key: {key}"

    def test_nested_keys(self):
        """Nested mapping keys should produce dot-separated CONTAINS edges."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        contains_edges = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "metadata.name" in contains_edges
        assert "metadata.namespace" in contains_edges
        assert "metadata.labels.app" in contains_edges
        assert "spec.selector.app" in contains_edges
        assert "spec.type" in contains_edges

    def test_sequence_items(self):
        """Sequence entries should produce CONTAINS edges with [index] notation."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        contains_edges = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "spec.ports[0]" in contains_edges
        assert "spec.ports[0].port" in contains_edges
        assert "spec.ports[0].targetPort" in contains_edges
        assert "spec.ports[1]" in contains_edges
        assert "spec.ports[1].port" in contains_edges

    def test_sequence_scalar_items(self):
        """Sequence of scalars should produce CONTAINS edges."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        contains_edges = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "spec.externalIPs[0]" in contains_edges

    def test_include_imports(self):
        """!!include / !include tags should produce IMPORTS edges."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) == 2
        import_targets = {e["target_text"] for e in import_edges}
        assert "config/base.yaml" in import_targets
        assert "secrets/env.yaml" in import_targets

    def test_env_var_references(self):
        """String values with ${VAR} should produce REFERENCES edges."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        ref_edges = [e for e in edges if e["kind"] == "references"]
        assert len(ref_edges) >= 2
        ref_targets = {e["target_text"] for e in ref_edges}
        assert "${DB_URL}" in ref_targets
        assert "${APP_PORT:-8080}" in ref_targets

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        for edge in edges:
            assert "sample.yaml:" in edge["source_loc"]

    def test_source_hash_consistency(self):
        """Hash IDs should be 32 hex characters."""
        source, tree = _parse_file("sample.yaml")
        edges = yaml_extract(source, tree, "sample.yaml")

        for edge in edges:
            assert len(edge["source"]) == 32
            assert all(c in "0123456789abcdef" for c in edge["source"])

    def test_empty_yaml(self):
        """Empty YAML should produce no edges."""
        edges = _parse("# Just a comment\n")
        assert len(edges) == 0

    def test_simple_key_value(self):
        """A single key-value pair should produce one CONTAINS edge."""
        edges = _parse("name: test-app\n")
        assert len(edges) == 1
        assert edges[0]["kind"] == "contains"
        assert edges[0]["target_text"] == "name"

    def test_inline_flow_mapping(self):
        """Flow mappings {key: value} should be extracted."""
        edges = _parse("labels: {app: myapp, tier: backend}\n")
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        # The inline mapping produces edges for labels, labels.app, labels.tier
        assert "labels" in contains_targets

    def test_multiple_documents(self):
        """YAML with multiple documents (---) should extract from all."""
        edges = _parse("---\nfirst: doc1\n---\nsecond: doc2\n")
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "first" in contains_targets
        assert "second" in contains_targets

    def test_no_env_refs_in_non_string(self):
        """Non-string values should not trigger env var references."""
        edges = _parse("port: 8080\nenabled: true\ncount: 3.14\n")
        ref_edges = [e for e in edges if e["kind"] == "references"]
        assert len(ref_edges) == 0

    def test_quoted_strings(self):
        """Both single and double quoted strings should be extracted."""
        edges = _parse('single: \'hello\'\ndouble: "world"\n')
        contains_targets = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "single" in contains_targets
        assert "double" in contains_targets
