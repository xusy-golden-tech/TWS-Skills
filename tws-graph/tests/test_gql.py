"""TDD tests for P38 v5.5.0 — Graph Query Language (GQL)."""

import hashlib
import pytest
from tws_graph.db.connection import DatabaseConnection
from tws_graph.db.queries import QueryBuilder


def _make_id(qualified_name: str, file_path: str) -> str:
    return hashlib.sha256(f"{file_path}:{qualified_name}".encode()).hexdigest()[:32]


@pytest.fixture
def queries(tmp_path):
    db_path = str(tmp_path / "test_gql.db")
    db = DatabaseConnection.initialize(db_path)
    qb = QueryBuilder(db.conn)
    yield qb
    db.close()


@pytest.fixture
def populated_queries(queries):
    """Populate with functions and classes across multiple files."""
    nodes = [
        ("auth_login", "function", "src/auth/login.py::auth_login", "src/auth/login.py"),
        ("auth_logout", "function", "src/auth/login.py::auth_logout", "src/auth/login.py"),
        ("auth_service", "class", "src/auth/service.py::auth_service", "src/auth/service.py"),
        ("user_model", "class", "src/models/user.py::user_model", "src/models/user.py"),
        ("create_user", "function", "src/models/user.py::create_user", "src/models/user.py"),
        ("api_handler", "function", "src/api/handler.py::api_handler", "src/api/handler.py"),
        ("main", "function", "src/main.py::main", "src/main.py"),
    ]

    for name, kind, qname, fpath in nodes:
        nid = _make_id(qname, fpath)
        queries._exec(
            "INSERT OR IGNORE INTO nodes (id, kind, name, qualified_name, file_path, "
            "language, start_line, end_line, updated_at) "
            "VALUES (?,?,?,?,?,'python',1,10,1000)",
            (nid, kind, name, qname, fpath))

    # Add edges
    auth_login_id = _make_id("src/auth/login.py::auth_login", "src/auth/login.py")
    auth_svc_id = _make_id("src/auth/service.py::auth_service", "src/auth/service.py")
    main_id = _make_id("src/main.py::main", "src/main.py")
    api_id = _make_id("src/api/handler.py::api_handler", "src/api/handler.py")
    user_id = _make_id("src/models/user.py::user_model", "src/models/user.py")
    create_id = _make_id("src/models/user.py::create_user", "src/models/user.py")

    for src, tgt in [
        (main_id, auth_login_id),
        (main_id, api_id),
        (api_id, auth_svc_id),
        (api_id, create_id),
        (auth_login_id, auth_svc_id),
    ]:
        queries._exec(
            "INSERT OR IGNORE INTO edges (source, target, kind, source_loc, provenance) "
            "VALUES (?,?,'calls','test.py:1','tree-sitter')", (src, tgt))

    queries.conn.commit()
    return queries


# ---------------------------------------------------------------------------
# P38a: GQL Parser
# ---------------------------------------------------------------------------

class TestGQLParser:
    """Test the GQL parser produces correct AST."""

    def test_parse_find_function(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function")
        assert ast["type"] == "find"
        assert ast["kind"] == "function"
        assert ast.get("conditions") is None

    def test_parse_find_class(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND class")
        assert ast["type"] == "find"
        assert ast["kind"] == "class"

    def test_parse_find_with_name_match(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function WHERE name MATCHES 'auth'")
        assert ast["type"] == "find"
        assert ast["conditions"] == [{"field": "name", "op": "MATCHES", "value": "auth"}]

    def test_parse_find_with_file_path(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND class WHERE file_path MATCHES 'src/auth/'")
        assert ast["conditions"] == [
            {"field": "file_path", "op": "MATCHES", "value": "src/auth/"}
        ]

    def test_parse_find_with_lang_filter(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function WHERE lang = 'python'")
        assert ast["conditions"] == [{"field": "lang", "op": "=", "value": "python"}]

    def test_parse_find_with_multiple_conditions(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function WHERE name MATCHES 'auth' AND lang = 'python'")
        assert len(ast["conditions"]) == 2

    def test_parse_find_with_limit(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function LIMIT 10")
        assert ast["limit"] == 10

    def test_parse_find_with_return(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND function RETURN name, file_path")
        assert ast["return_fields"] == ["name", "file_path"]

    def test_parse_impact(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("IMPACT OF MyClass.my_method")
        assert ast["type"] == "impact"
        assert ast["symbol"] == "MyClass.my_method"

    def test_parse_find_all(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("FIND *")
        assert ast["type"] == "find"
        assert ast["kind"] == "*"

    def test_parse_case_insensitive_keywords(self):
        from tws_graph.gql import parse_gql
        ast = parse_gql("find FUNCTION where name matches 'test' limit 5")
        assert ast["type"] == "find"
        assert ast["kind"] == "function"
        assert ast["limit"] == 5

    def test_parse_invalid_syntax(self):
        from tws_graph.gql import parse_gql
        with pytest.raises(ValueError, match="Invalid"):
            parse_gql("INVALID COMMAND")


# ---------------------------------------------------------------------------
# P38b: GQL Executor
# ---------------------------------------------------------------------------

class TestGQLExecutor:
    """Test the GQL executor runs queries and returns results."""

    def test_execute_find_function(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND function")
        names = {r["name"] for r in result}
        assert "auth_login" in names
        assert "create_user" in names
        assert "main" in names
        # Should not include classes
        assert "auth_service" not in names
        assert "user_model" not in names

    def test_execute_find_class(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND class")
        names = {r["name"] for r in result}
        assert "auth_service" in names
        assert "user_model" in names
        assert "auth_login" not in names

    def test_execute_find_all(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND *")
        assert len(result) == 7

    def test_execute_name_match(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND function WHERE name MATCHES 'auth'")
        names = {r["name"] for r in result}
        assert "auth_login" in names
        assert "auth_logout" in names
        assert "main" not in names

    def test_execute_file_path_match(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND * WHERE file_path MATCHES 'src/auth/'")
        names = {r["name"] for r in result}
        assert "auth_login" in names
        assert "auth_service" in names
        assert "main" not in names

    def test_execute_limit(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND function LIMIT 2")
        assert len(result) == 2

    def test_execute_return_fields(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(
            populated_queries, "FIND function WHERE name MATCHES 'main' RETURN name, file_path"
        )
        assert len(result) == 1
        r = result[0]
        assert "name" in r
        assert "file_path" in r
        # Should not include all default fields
        assert r["name"] == "main"

    def test_execute_empty_result(self, populated_queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(populated_queries, "FIND function WHERE name MATCHES 'nonexistent'")
        assert result == []

    def test_execute_empty_graph(self, queries):
        from tws_graph.gql import execute_gql
        result = execute_gql(queries, "FIND function")
        assert result == []


# ---------------------------------------------------------------------------
# P38c: GQL CLI
# ---------------------------------------------------------------------------

class TestGQLCLI:
    """Test CLI integration for GQL."""

    def test_query_command_help(self):
        import subprocess, sys
        r = subprocess.run(
            [sys.executable, "-m", "tws_graph", "query", "--help"],
            capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0
        assert "query" in (r.stdout + r.stderr).lower()

    def test_query_command_invalid_syntax(self):
        import subprocess, sys
        r = subprocess.run(
            [sys.executable, "-m", "tws_graph", "query", "INVALID"],
            capture_output=True, text=True, timeout=30
        )
        assert r.returncode != 0
