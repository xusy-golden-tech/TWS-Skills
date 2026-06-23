"""Tests for SQL extractor."""

import os
import pytest
from tws_graph.indexer.extractors.sql_extractor import extract as sql_extract
from tree_sitter_language_pack import get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sql")


def _parse_file(filename):
    path = os.path.join(FIXTURES, filename)
    with open(path, "r", encoding="utf-8") as f:
        source_str = f.read()
    parser = get_parser("sql")
    tree = parser.parse(source_str)
    return source_str.encode("utf-8"), tree


def _parse(source_str, file_path="test.sql"):
    parser = get_parser("sql")
    tree = parser.parse(source_str)
    return sql_extract(source_str.encode("utf-8"), tree, file_path)


class TestSqlExtractor:
    """Unit tests for SQL AST extraction."""

    def test_create_table_contains(self):
        """CREATE TABLE should produce a CONTAINS edge."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        table_edges = [e for e in edges if e["target_text"] == "users"
                       and e["kind"] == "contains"]
        assert len(table_edges) >= 1

    def test_create_table_multiple(self):
        """Multiple CREATE TABLE should produce multiple CONTAINS edges."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        contains = {e["target_text"] for e in edges if e["kind"] == "contains"}
        assert "users" in contains
        assert "orders" in contains

    def test_foreign_key_references(self):
        """FOREIGN KEY ... REFERENCES should produce a REFERENCES edge."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        ref_edges = [e for e in edges if e["kind"] == "references"]
        ref_targets = {e["target_text"] for e in ref_edges}
        assert "users" in ref_targets

    def test_create_index_contains(self):
        """CREATE INDEX should produce a CONTAINS edge for the index name."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        index_edges = [e for e in edges if e["target_text"] == "idx_orders_user"
                       and e["kind"] == "contains"]
        assert len(index_edges) >= 1

    def test_create_view_contains(self):
        """CREATE VIEW should produce a CONTAINS edge for the view name."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        view_edges = [e for e in edges if e["target_text"] == "active_users"
                      and e["kind"] == "contains"]
        assert len(view_edges) >= 1

    def test_select_references(self):
        """SELECT ... FROM table should produce a REFERENCES edge."""
        edges = _parse("SELECT * FROM users;\n")

        ref_edges = [e for e in edges if e["kind"] == "references"
                     and e["target_text"] == "users"]
        assert len(ref_edges) >= 1

    def test_insert_references(self):
        """INSERT INTO table should produce a REFERENCES edge."""
        edges = _parse("INSERT INTO users (name) VALUES ('Alice');\n")

        ref_edges = [e for e in edges if e["kind"] == "references"
                     and e["target_text"] == "users"]
        assert len(ref_edges) >= 1

    def test_update_references(self):
        """UPDATE table should produce a REFERENCES edge."""
        edges = _parse("UPDATE users SET name = 'Bob' WHERE id = 1;\n")

        ref_edges = [e for e in edges if e["kind"] == "references"
                     and e["target_text"] == "users"]
        assert len(ref_edges) >= 1

    def test_delete_references(self):
        """DELETE FROM table should produce a REFERENCES edge."""
        edges = _parse("DELETE FROM users WHERE id = 1;\n")

        ref_edges = [e for e in edges if e["kind"] == "references"
                     and e["target_text"] == "users"]
        assert len(ref_edges) >= 1

    def test_select_multiple_tables(self):
        """SELECT with JOIN should reference multiple tables."""
        edges = _parse(
            "SELECT * FROM users JOIN orders ON users.id = orders.user_id;\n"
        )

        ref_targets = {e["target_text"] for e in edges if e["kind"] == "references"}
        assert "users" in ref_targets
        assert "orders" in ref_targets

    def test_edge_provenance(self):
        """All edges should have provenance='tree-sitter'."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        assert len(edges) > 0
        for edge in edges:
            assert edge["provenance"] == "tree-sitter"
            assert edge["target"] == ""

    def test_source_loc_format(self):
        """Each edge should have a properly formatted source_loc."""
        source, tree = _parse_file("sample.sql")
        edges = sql_extract(source, tree, "sample.sql")

        for edge in edges:
            assert "sample.sql:" in edge["source_loc"]
