"""Tests for analysis/entry_point.py — EntryPointDetector.

Covers:
    1. main function detection (__main__ or name=="main")
    2. test function/method detection (test_ prefix or test in path)
    3. CLI entry detection (typer/click/argparse/commander decorators)
    4. Route handler detection (route/get/post/put/delete decorators)
    5. Init/constructor detection (__init__ or constructor)
    6. Lifecycle hook detection (ngOnInit/useEffect/componentDidMount)
    7. Edge cases: empty store, no matches, deduplication
    8. Multi-language support (Python, TypeScript, Java, Go, Kotlin, Rust)
"""

import pytest

from tws_graph.analysis.entry_point import EntryPointDetector, EntryPointResult
from tws_graph.store.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(id, file_path="src/app.py", kind="function", name="my_func",
               qualified_name=None, decorators=None, framework=None,
               language="python", **overrides):
    """Create a minimal valid NodeRecord for testing."""
    if qualified_name is None:
        qualified_name = f"{file_path}::{kind}.{name}"
    node = {
        "id": id,
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "file_path": file_path,
        "language": language,
        "start_line": 1,
        "end_line": 10,
        "signature": None,
        "docstring": None,
        "visibility": None,
        "is_abstract": 0,
        "is_exported": 0,
        "decorators": decorators,
        "framework": framework,
        "properties": None,
        "updated_at": 0,
    }
    node.update(overrides)
    return node


@pytest.fixture
def store():
    """Return a fresh empty MemoryStore."""
    return MemoryStore()


@pytest.fixture
def detector():
    """Return a fresh EntryPointDetector."""
    return EntryPointDetector()


# ---------------------------------------------------------------------------
# 1. Main function detection
# ---------------------------------------------------------------------------

class TestMainFunctionDetection:
    """Detect main entry points via qualified_name __main__ or name=="main"."""

    def test_detect_python_main_block(self, store, detector):
        """qualified_name contains __main__ → entry_type=main."""
        store.insert_node(_make_node(
            "n1", name="<module>",
            qualified_name="src/main.py::function.__main__",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "main"
        assert r.confidence == 0.9
        assert "qualified_name" in r.evidence

    def test_detect_function_named_main(self, store, detector):
        """name == 'main' → entry_type=main."""
        store.insert_node(_make_node(
            "n1", name="main",
            qualified_name="src/cli.py::function.main",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "main"
        assert r.confidence == 0.9
        assert "name" in r.evidence

    def test_method_named_main(self, store, detector):
        """method with name 'main' also detected."""
        store.insert_node(_make_node(
            "n2", kind="method", name="main",
            qualified_name="src/app.py::MyClass.main",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "main"

    def test_main_in_go(self, store, detector):
        """Go main function in package main."""
        store.insert_node(_make_node(
            "n3", name="main", language="go",
            qualified_name="cmd/server.go::function.main",
            file_path="cmd/server.go",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "main"

    def test_not_main_when_name_is_main_like(self, store, detector):
        """name 'main_loop' should NOT be detected as main."""
        store.insert_node(_make_node(
            "n4", name="main_loop",
            qualified_name="src/app.py::function.main_loop",
        ))
        results = detector.detect(store)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# 2. Test function/method detection
# ---------------------------------------------------------------------------

class TestTestFunctionDetection:
    """Detect test entry points."""

    def test_detect_test_prefix_function(self, store, detector):
        """name starts with 'test_' → entry_type=test."""
        store.insert_node(_make_node(
            "n1", name="test_login",
            qualified_name="tests/test_auth.py::function.test_login",
            file_path="tests/test_auth.py",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "test"
        assert r.confidence == 0.9
        assert "test_ prefix" in r.evidence.lower()

    def test_detect_test_in_file_path(self, store, detector):
        """file_path contains 'test' → entry_type=test."""
        store.insert_node(_make_node(
            "n2", name="login_scenario",
            qualified_name="tests/test_auth.py::function.login_scenario",
            file_path="tests/test_auth.py",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "test"
        assert r.confidence == 0.7
        assert "path" in r.evidence.lower()

    def test_detect_java_test_class(self, store, detector):
        """Java test method with @Test annotation/decorator."""
        store.insert_node(_make_node(
            "n3", kind="method", name="shouldLoginSuccessfully",
            qualified_name="src/test/.../AuthTest.shouldLoginSuccessfully",
            file_path="src/test/java/com/example/AuthTest.java",
            language="java",
            decorators='["Test"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "test"

    def test_detect_spec_test(self, store, detector):
        """TypeScript/Jest spec file."""
        store.insert_node(_make_node(
            "n4", name="it should render",
            qualified_name="src/__tests__/App.spec.tsx::function.it should render",
            file_path="src/__tests__/App.spec.tsx",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "test"


# ---------------------------------------------------------------------------
# 3. CLI entry detection
# ---------------------------------------------------------------------------

class TestCliEntryDetection:
    """Detect CLI entry points via decorators."""

    def test_detect_typer_command(self, store, detector):
        """@app.command() typer decorator → entry_type=cli."""
        store.insert_node(_make_node(
            "n1", name="deploy",
            qualified_name="src/cli.py::function.deploy",
            decorators='["typer_app.command()"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "cli"
        assert r.confidence == 0.8
        assert "decorator" in r.evidence.lower()

    def test_detect_click_command(self, store, detector):
        """@click.command() decorator → entry_type=cli."""
        store.insert_node(_make_node(
            "n2", name="sync",
            qualified_name="src/cli.py::function.sync",
            decorators='["click.command()"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "cli"

    def test_detect_argparse_function(self, store, detector):
        """name containing argparse → entry_type=cli (heuristic)."""
        store.insert_node(_make_node(
            "n3", name="add_arguments",
            qualified_name="src/cli.py::function.parse_args",
            decorators='["argparse.ArgumentParser"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "cli"

    def test_detect_commander_decorator(self, store, detector):
        """@commander decorator (TypeScript) → entry_type=cli."""
        store.insert_node(_make_node(
            "n4", name="greet",
            qualified_name="src/cli.ts::function.greet",
            language="typescript",
            decorators='["commander.command()"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "cli"

    def test_no_false_positive_on_regular_function(self, store, detector):
        """Regular function without CLI decorators should not match."""
        store.insert_node(_make_node(
            "n5", name="helper",
            qualified_name="src/utils.py::function.helper",
            decorators='["staticmethod"]',
        ))
        results = detector.detect(store)
        cli_results = [r for r in results if r.entry_type == "cli"]
        assert len(cli_results) == 0


# ---------------------------------------------------------------------------
# 4. Route handler detection
# ---------------------------------------------------------------------------

class TestRouteHandlerDetection:
    """Detect HTTP route handlers."""

    def test_detect_flask_route(self, store, detector):
        """@app.route('/') → entry_type=route_handler."""
        store.insert_node(_make_node(
            "n1", name="index",
            qualified_name="src/app.py::function.index",
            decorators='["app.route(\'/\')"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "route_handler"
        assert r.confidence == 0.8
        assert "decorator" in r.evidence.lower()

    def test_detect_fastapi_get(self, store, detector):
        """@app.get('/users') → entry_type=route_handler."""
        store.insert_node(_make_node(
            "n2", name="get_users",
            qualified_name="src/api.py::function.get_users",
            decorators='["app.get(\'/users\')"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "route_handler"

    def test_detect_fastapi_post(self, store, detector):
        """@app.post('/users') → entry_type=route_handler."""
        store.insert_node(_make_node(
            "n3", kind="method", name="create_user",
            qualified_name="src/api.py::UserController.create_user",
            decorators='["router.post(\'/users\')"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "route_handler"

    def test_detect_fastapi_put_delete(self, store, detector):
        """@app.put() and @app.delete() → entry_type=route_handler."""
        store.insert_node(_make_node(
            "n4", name="update_user",
            qualified_name="src/api.py::function.update_user",
            file_path="src/users_api.py",
            decorators='["app.put(\'/users/{id}\')"]',
        ))
        store.insert_node(_make_node(
            "n5", name="delete_user",
            qualified_name="src/items_api.py::function.delete_user",
            file_path="src/items_api.py",
            decorators='["app.delete(\'/users/{id}\')"]',
        ))
        results = detector.detect(store)
        assert len(results) == 2
        assert all(r.entry_type == "route_handler" for r in results)

    def test_detect_express_route(self, store, detector):
        """Express.js router.get('/') → entry_type=route_handler."""
        store.insert_node(_make_node(
            "n6", name="getUsers",
            qualified_name="src/routes/users.ts::function.getUsers",
            language="typescript",
            decorators='["router.get(\'/users\')"]',
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "route_handler"

    def test_no_false_route_on_getter_method(self, store, detector):
        """Name 'get' without route decorator should NOT match as route_handler."""
        store.insert_node(_make_node(
            "n7", kind="method", name="get_name",
            qualified_name="src/model.py::User.get_name",
        ))
        results = detector.detect(store)
        route_results = [r for r in results if r.entry_type == "route_handler"]
        assert len(route_results) == 0


# ---------------------------------------------------------------------------
# 5. Init / constructor detection
# ---------------------------------------------------------------------------

class TestInitDetection:
    """Detect __init__ methods and constructors."""

    def test_detect_python_init(self, store, detector):
        """__init__ method → entry_type=init."""
        store.insert_node(_make_node(
            "n1", kind="method", name="__init__",
            qualified_name="src/model.py::User.__init__",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "init"
        assert r.confidence == 0.9
        assert "__init__" in r.evidence

    def test_detect_java_constructor(self, store, detector):
        """Constructor named same as class → entry_type=init."""
        store.insert_node(_make_node(
            "n2", kind="method", name="constructor",
            qualified_name="src/model.ts::User.constructor",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "init"

    def test_detect_kotlin_init(self, store, detector):
        """Kotlin init block."""
        store.insert_node(_make_node(
            "n3", kind="method", name="init",
            qualified_name="src/User.kt::User.init",
            language="kotlin",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "init"


# ---------------------------------------------------------------------------
# 6. Lifecycle hook detection
# ---------------------------------------------------------------------------

class TestLifecycleHookDetection:
    """Detect lifecycle hooks: useEffect, componentDidMount, ngOnInit, etc."""

    def test_detect_react_useeffect(self, store, detector):
        """useEffect → entry_type=hook."""
        store.insert_node(_make_node(
            "n1", name="useEffect",
            qualified_name="src/App.tsx::function.useEffect",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        r = results[0]
        assert r.entry_type == "hook"
        assert r.confidence == 0.9

    def test_detect_react_component_did_mount(self, store, detector):
        """componentDidMount → entry_type=hook."""
        store.insert_node(_make_node(
            "n2", kind="method", name="componentDidMount",
            qualified_name="src/App.tsx::MyComponent.componentDidMount",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "hook"

    def test_detect_angular_ng_on_init(self, store, detector):
        """ngOnInit → entry_type=hook."""
        store.insert_node(_make_node(
            "n3", kind="method", name="ngOnInit",
            qualified_name="src/app.component.ts::AppComponent.ngOnInit",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "hook"

    def test_detect_multiple_hooks(self, store, detector):
        """Multiple hooks in same store all detected."""
        store.insert_node(_make_node(
            "n4", kind="method", name="ngOnDestroy",
            qualified_name="src/comp.ts::Comp.ngOnDestroy",
            file_path="src/comp.ts",
            language="typescript",
        ))
        store.insert_node(_make_node(
            "n5", name="useState",
            qualified_name="src/App.tsx::function.useState",
            file_path="src/App.tsx",
            language="typescript",
        ))
        results = detector.detect(store)
        assert len(results) == 2
        assert all(r.entry_type == "hook" for r in results)


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary and edge case testing."""

    def test_empty_store_returns_empty_list(self, store, detector):
        """Empty store → empty results."""
        results = detector.detect(store)
        assert results == []

    def test_no_matching_nodes(self, store, detector):
        """Store with regular functions only → no entry points detected."""
        store.insert_node(_make_node("n1", name="helper"))
        store.insert_node(_make_node("n2", name="calculate"))
        results = detector.detect(store)
        assert results == []

    def test_deduplication_same_file_and_type(self, store, detector):
        """Multiple entry points of same type in same file → deduplicated."""
        store.insert_node(_make_node(
            "n1", name="test_one",
            qualified_name="tests/test_app.py::function.test_one",
            file_path="tests/test_app.py",
        ))
        store.insert_node(_make_node(
            "n2", name="test_two",
            qualified_name="tests/test_app.py::function.test_two",
            file_path="tests/test_app.py",
        ))
        results = detector.detect(store)
        # Both in same file, same entry_type → deduplicated to 1
        assert len(results) == 1

    def test_multiple_entry_types_across_files(self, store, detector):
        """Multiple entry types across different files → all preserved."""
        store.insert_node(_make_node(
            "n1", name="main",
            qualified_name="src/cli.py::function.main",
        ))
        store.insert_node(_make_node(
            "n2", name="test_login",
            qualified_name="tests/test_auth.py::function.test_login",
            file_path="tests/test_auth.py",
        ))
        store.insert_node(_make_node(
            "n3", name="index",
            qualified_name="src/api.py::function.index",
            decorators='["app.get(\'/\')"]',
        ))
        results = detector.detect(store)
        entry_types = {r.entry_type for r in results}
        assert "main" in entry_types
        assert "test" in entry_types
        assert "route_handler" in entry_types

    def test_same_entry_type_different_files(self, store, detector):
        """Same entry_type in different files → kept separate."""
        store.insert_node(_make_node(
            "n1", name="main",
            qualified_name="src/cli.py::function.main",
            file_path="src/cli.py",
        ))
        store.insert_node(_make_node(
            "n2", name="main",
            qualified_name="src/server.py::function.main",
            file_path="src/server.py",
        ))
        results = detector.detect(store)
        # Two main functions in different files → both kept
        assert len(results) == 2

    def test_node_with_no_decorators_field(self, store, detector):
        """Node without decorators field should not crash."""
        node = _make_node(
            "n1", name="regular_func",
            qualified_name="src/app.py::function.regular_func",
        )
        # Remove decorators key entirely
        node.pop("decorators", None)
        store.insert_node(node)
        results = detector.detect(store)
        # Not an entry point, just verify no crash
        assert results == []

    def test_is_entry_point_returns_none_for_non_entry(self, detector):
        """is_entry_point returns None for non-entry node."""
        node = _make_node("n1", name="helper")
        result = detector.is_entry_point(node)
        assert result is None

    def test_is_entry_point_returns_result_for_entry(self, detector):
        """is_entry_point returns EntryPointResult for entry node."""
        node = _make_node("n1", name="main",
                          qualified_name="src/main.py::function.main")
        result = detector.is_entry_point(node)
        assert result is not None
        assert isinstance(result, EntryPointResult)
        assert result.entry_type == "main"


# ---------------------------------------------------------------------------
# 8. Multi-language support
# ---------------------------------------------------------------------------

class TestMultiLanguageSupport:
    """Test detection across all 6 supported languages."""

    def test_python_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="python",
            name="main", qualified_name="src/main.py::function.main",
        ))
        store.insert_node(_make_node(
            "n2", language="python", kind="method", name="__init__",
            qualified_name="src/model.py::User.__init__",
        ))
        results = detector.detect(store)
        assert len(results) == 2

    def test_typescript_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="typescript",
            name="ngOnInit", kind="method",
            qualified_name="src/app.ts::AppComponent.ngOnInit",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "hook"

    def test_java_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="java", kind="method",
            name="main",
            qualified_name="src/Main.java::Main.main",
            file_path="src/Main.java",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "main"

    def test_go_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="go",
            name="main",
            qualified_name="cmd/server.go::function.main",
        ))
        results = detector.detect(store)
        assert len(results) == 1

    def test_kotlin_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="kotlin", kind="method",
            name="init",
            qualified_name="src/App.kt::App.init",
        ))
        results = detector.detect(store)
        assert len(results) == 1
        assert results[0].entry_type == "init"

    def test_rust_entry_points(self, store, detector):
        store.insert_node(_make_node(
            "n1", language="rust",
            name="main",
            qualified_name="src/main.rs::function.main",
        ))
        results = detector.detect(store)
        assert len(results) == 1

    def test_mixed_language_store(self, store, detector):
        """Store with nodes from multiple languages → all detected."""
        store.insert_node(_make_node(
            "n1", language="python", name="main",
            qualified_name="src/cli.py::function.main",
        ))
        store.insert_node(_make_node(
            "n2", language="typescript", kind="method",
            name="ngOnInit",
            qualified_name="src/app.ts::App.ngOnInit",
        ))
        store.insert_node(_make_node(
            "n3", language="go", name="main",
            qualified_name="cmd/main.go::function.main",
            file_path="cmd/main.go",
        ))
        results = detector.detect(store)
        # Go and Python both have main → 2 main + 1 hook = 3
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 9. EntryPointResult dataclass
# ---------------------------------------------------------------------------

class TestEntryPointResult:
    """Test the EntryPointResult dataclass directly."""

    def test_create_result(self):
        r = EntryPointResult(
            node_id="n1",
            qualified_name="src/main.py::function.main",
            kind="function",
            file_path="src/main.py",
            entry_type="main",
            confidence=0.9,
            evidence="name matches 'main'",
        )
        assert r.node_id == "n1"
        assert r.entry_type == "main"
        assert r.confidence == 0.9

    def test_create_result_with_different_types(self):
        """All entry_types are valid."""
        for et in ("main", "test", "cli", "route_handler", "init", "hook"):
            r = EntryPointResult(
                node_id="x", qualified_name="a", kind="function",
                file_path="f.py", entry_type=et,
                confidence=0.8, evidence="test",
            )
            assert r.entry_type == et
