"""Tests for Java, Go, and Rust extractors."""

import os
import pytest


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

class TestJavaExtractor:
    def test_simple_class(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = """
public class HelloWorld {
    public void greet() {
        System.out.println("Hello");
    }
}
"""
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/HelloWorld.java", code, tree)

        assert len(result.nodes) >= 2  # class + method
        kinds = {n["kind"] for n in result.nodes}
        assert "class" in kinds
        assert "method" in kinds
        assert not result.errors

    def test_class_with_fields(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = """
public class User {
    private String name;
    private int age;

    public User(String name, int age) {
        this.name = name;
        this.age = age;
    }

    public String getName() {
        return name;
    }
}
"""
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/User.java", code, tree)

        assert len(result.nodes) >= 5  # class + 2 fields + constructor + method
        assert any(n["kind"] == "property" for n in result.nodes)
        assert any(n["kind"] == "method" for n in result.nodes)
        assert any(n["name"] == "getName" for n in result.nodes)

    def test_interface(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = """
public interface Repository {
    void save(Object entity);
    Object findById(long id);
}
"""
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/Repository.java", code, tree)

        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        assert len(interfaces) == 1
        assert interfaces[0]["name"] == "Repository"

    def test_visibility(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = """
public class Service {
    private void internalWork() {}
    protected void helper() {}
    public void api() {}
    void packageDefault() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/Service.java", code, tree)

        vis_map = {n["name"]: n["visibility"] for n in result.nodes}
        assert vis_map.get("api") == "public"
        assert vis_map.get("internalWork") == "private"
        assert vis_map.get("helper") == "protected"

    def test_call_edges(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = """
public class App {
    public void init() {
        setup();
        process();
    }

    private void setup() {}
    private void process() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/App.java", code, tree)

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        assert len(call_edges) >= 2

    def test_empty_file(self):
        from tws_graph.indexer.java_extractor import visit_java
        import tree_sitter_language_pack

        code = "// Just a comment"
        parser = tree_sitter_language_pack.get_parser("java")
        tree = parser.parse(code)
        result = visit_java("src/Empty.java", code, tree)

        assert len(result.nodes) == 0
        assert not result.errors


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

class TestGoExtractor:
    def test_simple_function(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

func calculateTotal(items []float64) float64 {
    sum := 0.0
    for _, item := range items {
        sum += item
    }
    return sum
}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/main.go", code, tree)

        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "calculateTotal"

    def test_exported_vs_unexported(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

func ExportedFunc() {}

func unexportedFunc() {}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/main.go", code, tree)

        vis_map = {n["name"]: n["visibility"] for n in result.nodes}
        assert vis_map.get("ExportedFunc") == "public"
        assert vis_map.get("unexportedFunc") == "private"

    def test_struct(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

type User struct {
    Name  string
    Email string
    age   int
}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/user.go", code, tree)

        structs = [n for n in result.nodes if n["kind"] == "class"]
        assert len(structs) == 1
        assert structs[0]["name"] == "User"

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 2

    def test_interface(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

type Reader interface {
    Read(p []byte) (n int, err error)
}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/reader.go", code, tree)

        ifaces = [n for n in result.nodes if n["kind"] == "interface"]
        assert len(ifaces) == 1
        assert ifaces[0]["name"] == "Reader"

    def test_call_edges(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

func main() {
    result := process()
    save(result)
}

func process() int { return 42 }

func save(n int) {}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/main.go", code, tree)

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        assert len(call_edges) >= 2

    def test_method_on_struct(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        code = """
package main

type Counter struct {
    count int
}

func (c *Counter) Increment() {
    c.count++
}

func (c *Counter) Value() int {
    return c.count
}
"""
        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go("src/counter.go", code, tree)

        methods = [n for n in result.nodes if n["kind"] == "method"]
        assert len(methods) >= 2

    def test_import_edges(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "go", "sample.go")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go(fixture_path, code, tree)

        import_edges = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = [e.get("target_text", "") for e in import_edges]

        assert len(import_edges) >= 6, f"Expected >=6 import edges, got {len(import_edges)}: {target_texts}"
        assert any("context" in t for t in target_texts), f"'context' not in imports: {target_texts}"
        assert any("encoding/json" in t for t in target_texts), f"'encoding/json' not in imports: {target_texts}"
        assert any("fmt" in t for t in target_texts), f"'fmt' not in imports: {target_texts}"
        assert any("net/http" in t for t in target_texts), f"'net/http' not in imports: {target_texts}"
        assert any("time" in t for t in target_texts), f"'time' not in imports: {target_texts}"
        assert any("github.com/gorilla/mux" in t for t in target_texts), \
            f"'github.com/gorilla/mux' not in imports: {target_texts}"

    def test_interface_implements(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "go", "sample.go")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go(fixture_path, code, tree)

        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        assert len(impl_edges) >= 1, f"Expected >=1 implements edge, got {len(impl_edges)}"

        # MemoryStore should implement DataStore
        mem_store_nodes = [n for n in result.nodes if n["name"] == "MemoryStore"]
        data_store_nodes = [n for n in result.nodes if n["name"] == "DataStore"]

        assert len(mem_store_nodes) >= 1, "MemoryStore node not found"
        assert len(data_store_nodes) >= 1, "DataStore node not found"

        mem_id = mem_store_nodes[0]["id"]
        ds_id = data_store_nodes[0]["id"]
        found = any(e["source"] == mem_id and e["target"] == ds_id for e in impl_edges)
        assert found, f"MemoryStore should implement DataStore. impl_edges={impl_edges}"

    def test_struct_tags(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "go", "sample.go")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go(fixture_path, code, tree)

        decorates_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(decorates_edges) > 0, f"Expected decorates edges for struct tags, got {len(decorates_edges)}"

        target_texts = [e.get("target_text", "") for e in decorates_edges]
        assert any("json" in t for t in target_texts), f"'json' tag not found in: {target_texts}"
        assert any("validate" in t for t in target_texts), f"'validate' tag not found in: {target_texts}"

    def test_type_references(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "go", "sample.go")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go(fixture_path, code, tree)

        type_ref_edges = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_ref_edges) > 0, f"Expected type_ref edges, got 0"

        target_texts = [e.get("target_text", "") for e in type_ref_edges]
        # Functions reference DataStore, context.Context, CacheStats, time.Duration, etc.
        assert any("DataStore" in t for t in target_texts), \
            f"'DataStore' not found in type_refs: {target_texts}"

    def test_variable_reads_writes(self):
        from tws_graph.indexer.go_extractor import visit_go
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "go", "sample.go")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("go")
        tree = parser.parse(code)
        result = visit_go(fixture_path, code, tree)

        read_edges = [e for e in result.edges if e["kind"] == "reads"]
        write_edges = [e for e in result.edges if e["kind"] == "writes"]

        assert len(read_edges) > 0, f"Expected read edges, got 0"
        assert len(write_edges) > 0, f"Expected write edges, got 0"


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

class TestRustExtractor:
    def test_simple_function(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
fn calculate_total(items: &[f64]) -> f64 {
    items.iter().sum()
}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        funcs = [n for n in result.nodes if n["kind"] == "function"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "calculate_total"

    def test_visibility(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
pub fn public_api() {}

fn private_fn() {}

pub(crate) fn crate_fn() {}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        vis_map = {n["name"]: n["visibility"] for n in result.nodes}
        assert vis_map.get("public_api") == "public"
        assert vis_map.get("private_fn") == "private"
        assert vis_map.get("crate_fn") == "internal"

    def test_struct(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
pub struct User {
    pub name: String,
    pub email: String,
    age: u32,
}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        structs = [n for n in result.nodes if n["kind"] == "class"]
        assert len(structs) == 1
        assert structs[0]["name"] == "User"

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 2

    def test_impl_methods(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
pub struct Calculator { value: i32 }

impl Calculator {
    pub fn new() -> Self {
        Calculator { value: 0 }
    }

    pub fn add(&mut self, x: i32) {
        self.value += x;
    }
}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        methods = [n for n in result.nodes if n["kind"] == "function"]
        assert len(methods) >= 2  # new + add
        names = {n["name"] for n in methods}
        assert "new" in names
        assert "add" in names

    def test_trait(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
pub trait Summary {
    fn summarize(&self) -> String;
}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        traits = [n for n in result.nodes if n["kind"] == "interface"]
        assert len(traits) == 1
        assert traits[0]["name"] == "Summary"

    def test_enum(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
pub enum Status {
    Active,
    Inactive,
    Pending,
}
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/lib.rs", code, tree)

        enums = [n for n in result.nodes if n["kind"] == "enum"]
        assert len(enums) == 1
        assert enums[0]["name"] == "Status"

    def test_call_edges(self):
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        code = """
fn main() {
    let x = compute();
    output(x);
}

fn compute() -> i32 { 42 }
fn output(v: i32) { println!("{}", v); }
"""
        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        result = visit_rust("src/main.rs", code, tree)

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        assert len(call_edges) >= 2
