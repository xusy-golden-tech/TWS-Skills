"""Tests for Bash/Shell extractor (P43)."""

import pytest
from tws_graph.indexer.parser import extract_from_source


SAMPLE_BASH_SRC = """\
#!/bin/bash
# Sample bash script for testing

MY_VAR="hello"
export PATH="/usr/bin:/usr/local/bin"

function greet() {
    local name="$1"
    echo "Hello, $name!"
    source /etc/profile
}

function _internal_helper() {
    return 0
}

# Call the functions
greet "World"
_internal_helper

# Another variable
readonly CONFIG_FILE="/etc/app.conf"
"""


@pytest.fixture(scope="module")
def result():
    return extract_from_source("sample.sh", SAMPLE_BASH_SRC, "bash")


class TestBashExtractorFunctions:
    """Tests for function extraction from bash scripts."""

    def test_extracts_functions(self, result):
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        names = {n["name"] for n in funcs}
        assert "greet" in names
        assert "_internal_helper" in names

    def test_function_names(self, result):
        funcs = {n["name"] for n in result.nodes if n["kind"] == "function"}
        assert len(funcs) == 2

    def test_function_file_path(self, result):
        for n in result.nodes:
            if n["kind"] == "function":
                assert n["file_path"] == "sample.sh"

    def test_function_has_line_numbers(self, result):
        for n in result.nodes:
            if n["kind"] == "function":
                assert n["start_line"] > 0
                assert n["end_line"] >= n["start_line"]

    def test_function_qualified_name(self, result):
        func = next(n for n in result.nodes if n["kind"] == "function" and n["name"] == "greet")
        assert "sample.sh" in func["qualified_name"]
        assert "greet" in func["qualified_name"]

    def test_function_signature(self, result):
        func = next(n for n in result.nodes if n["kind"] == "function" and n["name"] == "greet")
        assert "function greet()" in func["signature"]

    def test_function_language_is_bash(self, result):
        for n in result.nodes:
            if n["kind"] == "function":
                assert n["language"] == "bash"


class TestBashExtractorEdges:
    """Tests for edge extraction from bash scripts."""

    def test_extracts_writes_edges(self, result):
        writes = [e for e in result.edges if e["kind"] == "writes"]
        targets = {e["target_text"] for e in writes}
        assert "MY_VAR" in targets

    def test_extracts_calls_edges(self, result):
        calls = [e for e in result.edges if e["kind"] == "calls"]
        targets = {e["target_text"] for e in calls}
        assert "echo" in targets
        assert "greet" in targets or "_internal_helper" in targets

    def test_extracts_imports_edges(self, result):
        imports = [e for e in result.edges if e["kind"] == "imports"]
        targets = {e["target_text"] for e in imports}
        assert "/etc/profile" in targets

    def test_all_edges_have_source_loc(self, result):
        for e in result.edges:
            assert "source_loc" in e
            assert "sample.sh" in e["source_loc"]

    def test_all_edges_have_provenance(self, result):
        for e in result.edges:
            assert e.get("provenance") == "tree-sitter"

    def test_edges_have_file_source(self, result):
        for e in result.edges:
            assert e["source"].startswith("file:")


class TestBashExtractorEmptyInput:
    """Tests for edge cases with empty/minimal input."""

    def test_empty_script(self):
        result = extract_from_source("empty.sh", "", "bash")
        assert len(result.nodes) == 0
        # Empty script may still have the file node but no symbols

    def test_comment_only_script(self):
        result = extract_from_source("comments.sh", "# Just a comment\n# Another line\n", "bash")
        # Comments should not create any nodes or edges
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 0

    def test_shebang_only(self):
        result = extract_from_source("shebang.sh", "#!/bin/bash\n", "bash")
        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 0
