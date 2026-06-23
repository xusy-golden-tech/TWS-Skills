"""Tests for Dockerfile extractor."""

import os
import pytest
from tws_graph.indexer.extractors.dockerfile_extractor import extract as df_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "dockerfile")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("dockerfile")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.df"):
    parser = get_parser("dockerfile")
    tree = parser.parse(source_str)
    return df_extract(source_str.encode("utf-8"), tree, file_path)


class TestDockerfileExtractor:
    """Unit tests for Dockerfile AST extraction."""

    def test_from_imports(self):
        """FROM image:tag should produce IMPORTS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) >= 2  # python:3.11-slim and nginx:alpine

        import_targets = {e["target_text"] for e in import_edges}
        assert "python:3.11-slim" in import_targets
        assert "nginx:alpine" in import_targets

    def test_from_without_tag(self):
        """FROM image without tag should produce IMPORTS with image name only."""
        edges = _parse("FROM ubuntu\n")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) == 1
        assert import_edges[0]["target_text"] == "ubuntu"

    def test_copy_imports(self):
        """COPY should produce IMPORTS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        copy_edges = [e for e in edges if e["kind"] == "imports"
                      and e["target_text"] == "requirements.txt"]
        assert len(copy_edges) >= 1

    def test_add_imports(self):
        """ADD should produce IMPORTS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        add_edges = [e for e in edges if e["kind"] == "imports"
                     and e["target_text"] == "extra.tar.gz"]
        assert len(add_edges) >= 1

    def test_run_calls(self):
        """RUN should produce CALLS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        call_edges = [e for e in edges if e["kind"] == "calls"]
        assert len(call_edges) >= 1
        # Check the pip install command is captured
        call_texts = {e["target_text"] for e in call_edges}
        assert any("pip" in c for c in call_texts)

    def test_env_accesses(self):
        """ENV should produce ENV_ACCESSES edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        env_edges = [e for e in edges if e["kind"] == "env_accesses"]
        assert len(env_edges) >= 3  # APP_HOME, MODE, DEBUG

        env_texts = {e["target_text"] for e in env_edges}
        assert "APP_HOME=/app" in env_texts
        assert "MODE=production" in env_texts
        assert "DEBUG=false" in env_texts

    def test_expose_contains(self):
        """EXPOSE should produce CONTAINS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        expose_edges = [e for e in edges if e["kind"] == "contains"
                        and e["target_text"] in ("8080", "3000", "80")]
        assert len(expose_edges) >= 3

    def test_volume_contains(self):
        """VOLUME should produce CONTAINS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        volume_edges = [e for e in edges if e["kind"] == "contains"
                        and e["target_text"] in ("/data", "/var/log", "/var/tmp")]
        assert len(volume_edges) >= 3

    def test_cmd_contains(self):
        """CMD should produce CONTAINS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        cmd_edges = [e for e in edges if e["kind"] == "contains"]
        cmd_texts = {e["target_text"] for e in cmd_edges}
        # The CMD is ["python", "-m", "app"] → "python;-m;app"
        assert any("python" in t for t in cmd_texts)

    def test_entrypoint_contains(self):
        """ENTRYPOINT should produce CONTAINS edges."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        ep_edges = [e for e in edges if e["kind"] == "contains"]
        ep_texts = {e["target_text"] for e in ep_edges}
        assert any("/bin/sh" in t for t in ep_texts)

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("Dockerfile")
        edges = df_extract(source, tree, "Dockerfile")

        for edge in edges:
            assert "Dockerfile:" in edge["source_loc"]

    def test_multi_stage_build(self):
        """Multi-stage build with multiple FROM should produce multiple IMPORTS."""
        edges = _parse(
            "FROM golang:1.21 AS builder\n"
            "COPY . /src\n"
            "FROM alpine:3.18\n"
            "COPY --from=builder /src/app /app\n",
            "multi.df",
        )

        import_edges = [e for e in edges if e["kind"] == "imports"
                        and ":" in e["target_text"]]
        assert len(import_edges) >= 2
        targets = {e["target_text"] for e in import_edges if ":" in e["target_text"]}
        assert "golang:1.21" in targets
        assert "alpine:3.18" in targets

    def test_json_form_cmd(self):
        """CMD with JSON form should be extracted correctly."""
        edges = _parse('CMD ["python", "app.py"]\n')

        cmd_edges = [e for e in edges if e["kind"] == "contains"
                     and "python" in e["target_text"]]
        assert len(cmd_edges) >= 1
