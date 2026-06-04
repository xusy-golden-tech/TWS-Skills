"""Tests for FTS5 full-text search and helpers."""

import pytest


class TestEditDistance:
    def test_identical(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("abc", "abc") == 0

    def test_substitution(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("abc", "abd") == 1

    def test_deletion(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("abc", "ab") == 1

    def test_insertion(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("ab", "abc") == 1

    def test_multiple_edits(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("calculate", "calculatr") == 1
        assert _edit_distance("calculate", "calc") == 5

    def test_empty(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("", "") == 0
        assert _edit_distance("abc", "") == 3

    def test_case_sensitive(self):
        from tws_graph.db.queries import _edit_distance
        assert _edit_distance("abc", "ABC") == 3


class TestFieldQualifiers:
    def test_single_qualifier(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("kind:function api")
        assert r["text"] == ["api"]
        assert r["filters"] == {"kind": "function"}

    def test_multiple_qualifiers(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("lang:python kind:class controller")
        assert r["text"] == ["controller"]
        assert r["filters"] == {"kind": "class", "lang": "python"}

    def test_no_qualifiers(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("simple search query")
        assert r["text"] == ["simple", "search", "query"]
        assert r["filters"] == {}

    def test_path_qualifier(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("path:src/auth handleRequest")
        assert r["filters"] == {"path": "src/auth"}

    def test_visibility_qualifier(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("visibility:private helper")
        assert r["filters"] == {"visibility": "private"}

    def test_framework_qualifier(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("framework:fastapi route")
        assert r["filters"] == {"framework": "fastapi"}

    def test_unknown_field_treated_as_text(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("unknown:value something")
        assert r["text"] == ["unknown:value", "something"]
        assert r["filters"] == {}

    def test_language_alias(self):
        from tws_graph.db.queries import _parse_field_qualifiers
        r = _parse_field_qualifiers("language:python search")
        assert r["filters"] == {"lang": "python"}


class TestFTSBuildQuery:
    def test_simple_term(self):
        from tws_graph.db.queries import QueryBuilder
        q = QueryBuilder._build_fts_query("calculate")
        assert "calculate" in q or '"calculate"' in q

    def test_multi_term(self):
        from tws_graph.db.queries import QueryBuilder
        q = QueryBuilder._build_fts_query("hello world")
        assert "AND" in q


class TestSearchNodes:
    def test_like_search(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('test1', 'function', 'calculateTotal', 'src/app.py::calculateTotal',
                    'src/app.py', 'python', 1, 5, 1000)
        """)
        results = qb.search_nodes("calculate")
        assert len(results) > 0
        assert results[0]["name"] == "calculateTotal"

    def test_field_qualified_search(self, queries):
        qb = queries
        for i, (name, kind, lang) in enumerate([
            ("calculateTotal", "function", "python"),
            ("UserService", "class", "python"),
            ("handleClick", "function", "typescript"),
            ("UserController", "class", "kotlin"),
        ]):
            qb._exec("""
                INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                                   start_line, end_line, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (f"test{i}", kind, name, f"src/test{i}.py::{name}",
                  f"src/test{i}.py", lang, 1, 5, 1000))

        results = qb.search_nodes_field_qualified("kind:class", limit=10)
        kinds = {r["name"] for r in results}
        assert "UserService" in kinds
        assert "calculateTotal" not in kinds

        results = qb.search_nodes_field_qualified("lang:python", limit=10)
        langs = {r["name"] for r in results}
        assert "calculateTotal" in langs
        assert "UserService" in langs
        assert "handleClick" not in langs

    def test_search_no_results(self, queries):
        qb = queries
        results = qb.search_nodes("nonexistent_xyz_123")
        assert len(results) == 0

    def test_fuzzy_fallback(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('fuzz1', 'function', 'calculateTotal', 'src/app.py::calculateTotal',
                    'src/app.py', 'python', 1, 5, 1000)
        """)
        results = qb.search_nodes("calculatte")
        assert isinstance(results, list)


class TestUnresolvedRefs:
    def test_insert_and_query(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node1', 'function', 'foo', 'src/x.py::foo', 'src/x.py', 'python', 1, 5, 1000)
        """)

        qb.insert_unresolved_ref({
            "from_node_id": "node1",
            "reference_name": "bar",
            "reference_kind": "call",
            "line": 10,
            "file_path": "src/x.py",
            "language": "python",
        })

        refs = qb.get_unresolved_refs_by_file("src/x.py")
        assert len(refs) == 1
        assert refs[0]["reference_name"] == "bar"
        assert refs[0]["from_node_id"] == "node1"

    def test_cascade_delete(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node2', 'function', 'foo', 'src/y.py::foo', 'src/y.py', 'python', 1, 5, 1000)
        """)

        qb.insert_unresolved_ref({
            "from_node_id": "node2",
            "reference_name": "external_api",
            "file_path": "src/y.py",
            "language": "python",
        })

        qb._exec("DELETE FROM nodes WHERE id = ?", ("node2",))
        refs = qb.get_unresolved_refs_by_file("src/y.py")
        assert len(refs) == 0

    def test_batch_insert(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node3', 'function', 'a', 'src/z.py::a', 'src/z.py', 'python', 1, 5, 1000)
        """)

        refs = [
            {"from_node_id": "node3", "reference_name": "ext1", "file_path": "src/z.py", "language": "python"},
            {"from_node_id": "node3", "reference_name": "ext2", "file_path": "src/z.py", "language": "python"},
        ]
        qb.insert_unresolved_refs(refs)
        all_refs = qb.get_unresolved_refs_by_node("node3")
        assert len(all_refs) == 2

    def test_get_all_unresolved_refs(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node4', 'function', 'a', 'src/a.py::a', 'src/a.py', 'python', 1, 5, 1000)
        """)
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node5', 'function', 'b', 'src/b.py::b', 'src/b.py', 'python', 1, 5, 1000)
        """)

        qb.insert_unresolved_ref({"from_node_id": "node4", "reference_name": "x", "file_path": "src/a.py", "language": "python"})
        qb.insert_unresolved_ref({"from_node_id": "node5", "reference_name": "y", "file_path": "src/b.py", "language": "python"})

        all_refs = qb.get_all_unresolved_refs()
        assert len(all_refs) == 2

    def test_update_candidates(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node6', 'function', 'f', 'src/f.py::f', 'src/f.py', 'python', 1, 5, 1000)
        """)

        qb.insert_unresolved_ref({"from_node_id": "node6", "reference_name": "g", "file_path": "src/f.py", "language": "python"})
        refs = qb.get_unresolved_refs_by_node("node6")
        ref_id = refs[0]["id"]

        qb.update_unresolved_candidates(ref_id, ["cand1", "cand2"])
        updated = qb.get_unresolved_refs_by_node("node6")
        import json
        candidates = json.loads(updated[0]["candidates"] or "[]")
        assert candidates == ["cand1", "cand2"]

    def test_clear_unresolved_refs(self, queries):
        qb = queries
        qb._exec("""
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                               start_line, end_line, updated_at)
            VALUES ('node7', 'function', 'h', 'src/h.py::h', 'src/h.py', 'python', 1, 5, 1000)
        """)

        qb.insert_unresolved_ref({"from_node_id": "node7", "reference_name": "i", "file_path": "src/h.py", "language": "python"})
        qb.clear_unresolved_refs()
        assert qb.get_unresolved_ref_count() == 0
