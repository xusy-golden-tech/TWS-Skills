"""Tests for JSON extractor."""

import os
import pytest
from tws_graph.indexer.extractors.json_extractor import extract as json_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "json")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("json")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.json"):
    parser = get_parser("json")
    tree = parser.parse(source_str)
    return json_extract(source_str.encode("utf-8"), tree, file_path)


class TestJsonExtractor:
    """Unit tests for JSON AST extraction."""

    def test_top_level_keys_contains(self):
        """Top-level keys should produce CONTAINS edges."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        top_keys = {"name", "version", "description", "main", "scripts",
                    "dependencies", "devDependencies", "peerDependencies", "config"}
        for key in top_keys:
            assert any(e["target_text"] == key and e["kind"] == "contains"
                      for e in edges), f"Missing contains edge for '{key}'"

    def test_nested_keys_contains(self):
        """Nested keys should produce CONTAINS edges with dotted paths."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        nested_keys = {"config.port", "config.host", "config.ssl", "config.ssl.enabled",
                       "config.ssl.cert"}
        for key in nested_keys:
            assert any(e["target_text"] == key and e["kind"] == "contains"
                      for e in edges), f"Missing contains edge for '{key}'"

    def test_dependencies_imports(self):
        """dependencies should produce IMPORTS edges for package names."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        import_targets = {e["target_text"] for e in import_edges}

        assert "express" in import_targets
        assert "lodash" in import_targets
        assert "axios" in import_targets

    def test_dev_dependencies_imports(self):
        """devDependencies should produce IMPORTS edges."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        import_targets = {e["target_text"] for e in import_edges}

        assert "jest" in import_targets
        assert "typescript" in import_targets
        assert "eslint" in import_targets
        assert "webpack" in import_targets

    def test_peer_dependencies_imports(self):
        """peerDependencies should produce IMPORTS edges."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        import_targets = {e["target_text"] for e in import_edges}

        assert "react" in import_targets
        assert "react-dom" in import_targets

    def test_scripts_contains(self):
        """Scripts should produce CONTAINS edges for each script."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        script_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"].startswith("scripts.")]

        script_targets = {e["target_text"] for e in script_edges}
        assert "scripts.start" in script_targets
        assert "scripts.test" in script_targets
        assert "scripts.build" in script_targets
        assert "scripts.lint" in script_targets

    def test_no_imports_for_non_package_json(self):
        """Non-package.json files should not have IMPORTS edges."""
        src = '{"dependencies": {"foo": "1.0"}}'
        edges = _parse(src, "config.json")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) == 0

    def test_nested_keys_for_non_package_json(self):
        """Nested keys should still produce CONTAINS for non-package.json files."""
        src = '{"dependencies": {"foo": "1.0"}}'
        edges = _parse(src, "config.json")

        contains_edges = [e for e in edges if e["kind"] == "contains"]
        targets = {e["target_text"] for e in contains_edges}
        assert "dependencies" in targets
        assert "dependencies.foo" in targets

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        assert len(edges) > 0
        for edge in edges:
            assert "package.json:" in edge["source_loc"]
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Edge source IDs should be 32-char hex strings."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        for edge in edges:
            assert len(edge["source"]) == 32

    def test_empty_json(self):
        """Empty JSON object should produce no edges."""
        edges = _parse("{}\n", "empty.json")
        assert len(edges) == 0

    def test_simple_key_value(self):
        """A simple key-value pair should produce one CONTAINS edge."""
        edges = _parse('{"key": "value"}', "simple.json")
        assert len(edges) == 1
        assert edges[0]["target_text"] == "key"
        assert edges[0]["kind"] == "contains"

    def test_array_of_objects(self):
        """An array of objects should produce nested CONTAINS edges."""
        src = '[{"name": "foo", "version": "1.0"}, {"name": "bar", "version": "2.0"}]'
        edges = _parse(src, "array.json")

        contains_edges = [e for e in edges if e["kind"] == "contains"]
        assert len(contains_edges) == 4  # name, version for each object

    def test_deeply_nested_object(self):
        """Deeply nested objects should produce proper dotted paths."""
        src = '{"a": {"b": {"c": {"d": "deep"}}}}'
        edges = _parse(src, "deep.json")

        paths = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "a" in paths
        assert "a.b" in paths
        assert "a.b.c" in paths
        assert "a.b.c.d" in paths

    def test_total_edge_count(self):
        """Verify reasonable total edge count from the fixture."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        # Should have: top-level keys CONTAINS, nested config.* CONTAINS,
        # scripts.* CONTAINS, dependencies/devDeps/peerDeps IMPORTS
        assert len(edges) >= 20

    def test_scripts_not_duplicated(self):
        """Script names should not appear as both generic CONTAINS and script CONTAINS."""
        source, tree = _parse_file("package.json")
        edges = json_extract(source, tree, "package.json")

        # scripts.start should appear exactly once
        start_edges = [e for e in edges if e["target_text"] == "scripts.start"]
        assert len(start_edges) == 1
