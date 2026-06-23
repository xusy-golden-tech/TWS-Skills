"""Tests for HCL/Terraform extractor."""

import os
import pytest
from tws_graph.indexer.extractors.hcl_extractor import hcl_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "hcl")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("hcl")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.tf"):
    parser = get_parser("hcl")
    tree = parser.parse(source_str)
    return hcl_extract(source_str.encode("utf-8"), tree, file_path)


class TestHclExtractor:
    """Unit tests for HCL/Terraform AST extraction."""

    def test_resource_block_contains(self):
        """resource blocks should produce CONTAINS edges with 'resource:type/name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        resource_edges = [e for e in edges if e["kind"] == "contains"
                         and e["target_text"].startswith("resource:")]
        assert len(resource_edges) >= 3

        targets = {e["target_text"] for e in resource_edges}
        assert "resource:aws_s3_bucket/my_bucket" in targets
        assert "resource:aws_instance/web" in targets
        assert "resource:aws_instance/db" in targets

    def test_variable_block_contains(self):
        """variable blocks should produce CONTAINS edges with 'variable:name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        var_edges = [e for e in edges if e["kind"] == "contains"
                    and e["target_text"].startswith("variable:")]
        assert len(var_edges) >= 2

        targets = {e["target_text"] for e in var_edges}
        assert "variable:region" in targets
        assert "variable:instance_type" in targets

    def test_output_block_contains(self):
        """output blocks should produce CONTAINS edges with 'output:name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        output_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"].startswith("output:")]
        assert len(output_edges) >= 2

        targets = {e["target_text"] for e in output_edges}
        assert "output:bucket_name" in targets
        assert "output:bucket_arn" in targets

    def test_module_block_contains(self):
        """module blocks should produce CONTAINS edges with 'module:name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        module_edges = [e for e in edges if e["kind"] == "contains"
                       and e["target_text"].startswith("module:")]
        assert len(module_edges) >= 1
        assert any(e["target_text"] == "module:vpc" for e in module_edges)

    def test_provider_block_contains(self):
        """provider blocks should produce CONTAINS edges with 'provider:name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        provider_edges = [e for e in edges if e["kind"] == "contains"
                         and e["target_text"].startswith("provider:")]
        assert len(provider_edges) >= 1
        assert any(e["target_text"] == "provider:aws" for e in provider_edges)

    def test_data_block_contains(self):
        """data blocks should produce CONTAINS edges with 'data:type/name'."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        data_edges = [e for e in edges if e["kind"] == "contains"
                     and e["target_text"].startswith("data:")]
        assert len(data_edges) >= 1
        assert any(e["target_text"] == "data:aws_ami/ubuntu" for e in data_edges)

    def test_terraform_backend_contains(self):
        """terraform { backend 's3' {} } should produce CONTAINS edge."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        backend_edges = [e for e in edges if e["kind"] == "contains"
                        and e["target_text"].startswith("backend:")]
        assert len(backend_edges) >= 1
        assert any(e["target_text"] == "backend:s3" for e in backend_edges)

    def test_terraform_block_contains(self):
        """terraform {} block should produce a CONTAINS edge."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        tf_edges = [e for e in edges if e["target_text"] == "terraform"
                   and e["kind"] == "contains"]
        assert len(tf_edges) >= 1

    def test_required_providers_imports(self):
        """required_providers should produce IMPORTS edges for provider names."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        import_edges = [e for e in edges if e["kind"] == "imports"]
        assert len(import_edges) >= 2

        import_targets = {e["target_text"] for e in import_edges}
        assert "aws" in import_targets
        assert "kubernetes" in import_targets

    def test_locals_block_contains(self):
        """locals block should produce CONTAINS edge."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        locals_edges = [e for e in edges if e["target_text"] == "locals"
                       and e["kind"] == "contains"]
        assert len(locals_edges) >= 1

    def test_provisioner_block_contains(self):
        """provisioner blocks should produce CONTAINS edges."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        provisioner_edges = [e for e in edges if e["kind"] == "contains"
                            and e["target_text"].startswith("provisioner:")]
        assert len(provisioner_edges) >= 2

        targets = {e["target_text"] for e in provisioner_edges}
        assert "provisioner:local-exec" in targets
        assert "provisioner:remote-exec" in targets

    def test_resource_references(self):
        """Resource attribute references should produce REFERENCES edges."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        ref_edges = [e for e in edges if e["kind"] == "references"]
        # var.region, aws_s3_bucket.my_bucket.bucket, aws_s3_bucket.my_bucket.arn,
        # var.vpc_cidr, data.aws_ami.ubuntu.id, var.instance_type, etc.
        assert len(ref_edges) >= 5

        ref_targets = {e["target_text"] for e in ref_edges}
        # Check for multi-segment references
        assert "aws_s3_bucket.my_bucket.bucket" in ref_targets
        assert "aws_s3_bucket.my_bucket.arn" in ref_targets
        assert "var.region" in ref_targets or any("region" in t for t in ref_targets)
        assert "data.aws_ami.ubuntu.id" in ref_targets or any("ubuntu" in t for t in ref_targets)

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        assert len(edges) > 0
        for edge in edges:
            assert "main.tf:" in edge["source_loc"]
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_hash_consistency(self):
        """Edge source IDs should be 32-char hex strings."""
        source, tree = _parse_file("main.tf")
        edges = hcl_extract(source, tree, "main.tf")

        for edge in edges:
            assert len(edge["source"]) == 32

    def test_empty_hcl(self):
        """Empty HCL file should produce no edges."""
        edges = _parse("# Just a comment\n", "empty.tf")
        assert len(edges) == 0

    def test_minimal_resource(self):
        """A minimal resource block should produce one CONTAINS edge."""
        edges = _parse(
            'resource "null_resource" "test" {\n}\n',
            "test.tf",
        )
        resource_edges = [e for e in edges if e["kind"] == "contains"
                         and e["target_text"] == "resource:null_resource/test"]
        assert len(resource_edges) == 1

    def test_minimal_output_with_reference(self):
        """An output with a reference should produce CONTAINS + REFERENCES edges."""
        edges = _parse(
            'output "test" {\n  value = module.vpc.id\n}\n',
            "test.tf",
        )
        output_edges = [e for e in edges if e["target_text"] == "output:test"
                       and e["kind"] == "contains"]
        assert len(output_edges) == 1

        ref_edges = [e for e in edges if e["kind"] == "references"]
        assert len(ref_edges) >= 1
        assert any("module.vpc.id" in e["target_text"] or "vpc" in e["target_text"]
                  for e in ref_edges)

    def test_variable_with_type_and_default(self):
        """A variable with type and default should produce CONTAINS edge."""
        edges = _parse(
            'variable "count" {\n  type    = number\n  default = 3\n}\n',
            "test.tf",
        )
        var_edges = [e for e in edges if e["target_text"] == "variable:count"
                    and e["kind"] == "contains"]
        assert len(var_edges) == 1
