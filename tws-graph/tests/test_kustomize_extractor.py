"""Tests for Kustomize overlay extractor."""

import os
import pytest
from tws_graph.indexer.extractors.kustomize_extractor import kustomize_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "kustomize")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("yaml")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="kustomization.yaml"):
    parser = get_parser("yaml")
    tree = parser.parse(source_str)
    return kustomize_extract(source_str.encode("utf-8"), tree, file_path)


class TestKustomizeExtractor:
    """Unit tests for Kustomize YAML AST extraction."""

    def test_bases_contains(self):
        """bases items should produce CONTAINS edges."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        base_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] in ("../../base", "../common")]
        assert len(base_edges) == 2

    def test_resources_contains(self):
        """resources items should produce CONTAINS edges."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        resource_edges = [e for e in edges if e["kind"] == "contains"
                         and e["target_text"] in ("deployment.yaml", "service.yaml",
                                                   "ingress.yaml")]
        assert len(resource_edges) == 3

    def test_namespace_contains(self):
        """namespace should produce CONTAINS edge for the value and for the section."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        ns_value = [e for e in edges if e["kind"] == "contains"
                   and e["target_text"] == "production"]
        assert len(ns_value) >= 1

        ns_section = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"] == "namespace"]
        assert len(ns_section) >= 1

    def test_patches_references(self):
        """patches should produce REFERENCES edges with patch paths."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        patch_edges = [e for e in edges if e["kind"] == "references"]
        assert len(patch_edges) == 3

        targets = {e["target_text"] for e in patch_edges}
        assert "patch-deployment.yaml" in targets
        assert "patch-configmap.yaml" in targets
        assert "patch-remove-annotation.yaml" in targets

    def test_images_imports(self):
        """images should produce IMPORTS edges for name, newName, newTag."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        import_targets = {e["target_text"] for e in import_edges}

        # Name fields
        assert "nginx" in import_targets
        assert "redis" in import_targets
        assert "myapp" in import_targets

        # newName fields
        assert "my-registry.io/production/nginx" in import_targets
        assert "my-registry.io/production/myapp" in import_targets

        # newTag fields
        assert "1.21.0" in import_targets
        assert "6.2-alpine" in import_targets

    def test_config_map_generator_contains(self):
        """configMapGenerator should produce CONTAINS edge."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        cm_edges = [e for e in edges if e["target_text"] == "configMapGenerator"
                   and e["kind"] == "contains"]
        assert len(cm_edges) >= 1

    def test_secret_generator_contains(self):
        """secretGenerator should produce CONTAINS edge."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        sg_edges = [e for e in edges if e["target_text"] == "secretGenerator"
                   and e["kind"] == "contains"]
        assert len(sg_edges) >= 1

    def test_common_labels_contains(self):
        """commonLabels should produce CONTAINS edge."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        cl_edges = [e for e in edges if e["target_text"] == "commonLabels"
                   and e["kind"] == "contains"]
        assert len(cl_edges) >= 1

    def test_common_annotations_contains(self):
        """commonAnnotations should produce CONTAINS edge."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        ca_edges = [e for e in edges if e["target_text"] == "commonAnnotations"
                   and e["kind"] == "contains"]
        assert len(ca_edges) >= 1

    def test_non_kustomization_returns_empty(self):
        """Non-kustomization YAML files should return no edges."""
        edges = _parse("apiVersion: v1\nkind: Service\n", "service.yaml")
        assert len(edges) == 0

    def test_non_kustomization_filename_returns_empty(self):
        """YAML files not named kustomization.yaml/yml should return no edges."""
        edges = _parse("bases:\n  - ../../base\n", "deployment.yaml")
        assert len(edges) == 0

    def test_kustomization_yml_works(self):
        """kustomization.yml should also be detected."""
        edges = _parse("namespace: test\n", "kustomization.yml")
        ns_edges = [e for e in edges if e["target_text"] == "namespace"
                   and e["kind"] == "contains"]
        assert len(ns_edges) >= 1

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        assert len(edges) > 0
        for edge in edges:
            assert "kustomization.yaml:" in edge["source_loc"]
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Edge source IDs should be 32-char hex strings."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        for edge in edges:
            assert len(edge["source"]) == 32

    def test_patches_no_duplicates(self):
        """Patch edge should not have duplicate full-text entries."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        patch_edges = [e for e in edges if e["kind"] == "references"]
        # Each unique patch target should appear only once
        seen = {}
        for e in patch_edges:
            key = (e["kind"], e["target_text"], e["source_loc"])
            assert key not in seen, f"Duplicate edge: {key}"
            seen[key] = True

    def test_total_edge_count(self):
        """Verify reasonable total edge count from the fixture."""
        source, tree = _parse_file("kustomization.yaml")
        edges = kustomize_extract(source, tree, "kustomization.yaml")

        # bases(2) + resources(3) + namespace(2) + patches(3) + images(7) +
        # configMapGenerator(1) + secretGenerator(1) + commonLabels(1) + commonAnnotations(1)
        assert len(edges) >= 18

    def test_empty_kustomization(self):
        """Empty kustomization YAML should produce no edges."""
        edges = _parse("# Just a comment\n", "kustomization.yaml")
        assert len(edges) == 0

    def test_simple_patch_string(self):
        """Simple string patches (not objects) should work."""
        edges = _parse("patches:\n  - my-patch.yaml\n", "kustomization.yaml")
        ref_edges = [e for e in edges if e["kind"] == "references"]
        assert len(ref_edges) == 1
        assert ref_edges[0]["target_text"] == "my-patch.yaml"

    def test_simple_image_string(self):
        """Simple string images (not objects) should work."""
        edges = _parse("images:\n  - nginx:1.19\n", "kustomization.yaml")
        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) >= 1
        assert any("nginx" in e["target_text"] for e in import_edges)
