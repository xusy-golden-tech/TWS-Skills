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

    # -------------------------------------------------------------------
    # P51 Rust deep-upgrade tests — uses sample.rs fixture
    # -------------------------------------------------------------------

    def _load_rust_fixture(self):
        """Helper: parse sample.rs and return visit_rust result."""
        from tws_graph.indexer.rust_extractor import visit_rust
        import tree_sitter_language_pack

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "rust", "sample.rs")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("rust")
        tree = parser.parse(code)
        return visit_rust(fixture_path, code, tree)

    def test_use_imports(self):
        """Verify use declarations produce imports edges."""
        result = self._load_rust_fixture()

        import_edges = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = [e.get("target_text", "") for e in import_edges]

        assert len(import_edges) >= 7, \
            f"Expected >=7 import edges from use declarations, got {len(import_edges)}: {target_texts}"
        assert any("std::collections" in t for t in target_texts), \
            f"'std::collections' not in imports: {target_texts}"
        assert any("HashMap" in t for t in target_texts), \
            f"'HashMap' not in imports: {target_texts}"
        assert any("std::fmt" in t or "fmt" in t for t in target_texts), \
            f"'std::fmt' not in imports: {target_texts}"
        assert any("PathBuf" in t for t in target_texts), \
            f"'PathBuf' not in imports: {target_texts}"

    def test_mod_imports(self):
        """Verify mod declarations produce imports edges."""
        result = self._load_rust_fixture()

        import_edges = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = [e.get("target_text", "") for e in import_edges]

        assert any("database" in t for t in target_texts), \
            f"'database' mod not in imports: {target_texts}"
        assert any("handlers" in t for t in target_texts), \
            f"'handlers' mod not in imports: {target_texts}"
        assert any("models" in t for t in target_texts), \
            f"'models' mod not in imports: {target_texts}"

    def test_trait_implements(self):
        """Verify impl Trait for Type produces implements edges."""
        result = self._load_rust_fixture()

        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        assert len(impl_edges) >= 2, \
            f"Expected >=2 implements edges, got {len(impl_edges)}"

        # MemoryStore should implement DataStore
        struct_nodes = [n for n in result.nodes if n["name"] == "MemoryStore"]
        trait_nodes = [n for n in result.nodes if n["name"] == "DataStore"]

        assert len(struct_nodes) >= 1, "MemoryStore node not found"
        assert len(trait_nodes) >= 1, "DataStore node not found"

        struct_id = struct_nodes[0]["id"]
        trait_id = trait_nodes[0]["id"]
        found = any(
            e["source"] == struct_id and e["target"] == trait_id
            for e in impl_edges
        )
        assert found, f"MemoryStore should implement DataStore. impl_edges={impl_edges}"

    def test_derive_attributes(self):
        """Verify #[derive(Debug, Clone, ...)] produces decorates edges."""
        result = self._load_rust_fixture()

        decorates_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(decorates_edges) > 0, \
            f"Expected decorates edges for derive attributes, got {len(decorates_edges)}"

        target_texts = [e.get("target_text", "") for e in decorates_edges]
        assert any("Debug" in t for t in target_texts), \
            f"'Debug' derive not found in: {target_texts}"
        assert any("Clone" in t for t in target_texts), \
            f"'Clone' derive not found in: {target_texts}"
        assert any("Default" in t for t in target_texts), \
            f"'Default' derive not found in: {target_texts}"

    def test_generics_and_lifetimes(self):
        """Verify generic parameters and lifetimes produce type_ref edges."""
        result = self._load_rust_fixture()

        type_ref_edges = [e for e in result.edges if e["kind"] == "type_ref"]
        assert len(type_ref_edges) > 0, \
            f"Expected type_ref edges for generics/lifetimes, got 0"

        target_texts = [e.get("target_text", "") for e in type_ref_edges]
        # load_config has R: Read → type_ref to Read
        assert any("Read" in t for t in target_texts), \
            f"'Read' not found in type_refs: {target_texts}"
        # save_record has W: Write → type_ref to Write
        assert any("Write" in t for t in target_texts), \
            f"'Write' not found in type_refs: {target_texts}"

    def test_macro_calls(self):
        """Verify macro invocations (println!, write!, format!, vec![]) produce calls edges."""
        result = self._load_rust_fixture()

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        target_texts = [e.get("target_text", "") for e in call_edges]

        # At minimum we should find some macro calls
        assert len(call_edges) > 0, f"Expected calls edges including macro calls, got 0"

        # Try to find specific macro invocations
        assert any("println" in t for t in target_texts), \
            f"'println' macro call not found in calls: {target_texts}"
        assert any("format" in t for t in target_texts), \
            f"'format' macro call not found in calls: {target_texts}"

    def test_variable_reads_writes(self):
        """Verify let declarations and assignments produce reads/writes edges."""
        result = self._load_rust_fixture()

        read_edges = [e for e in result.edges if e["kind"] == "reads"]
        write_edges = [e for e in result.edges if e["kind"] == "writes"]

        assert len(read_edges) > 0, \
            f"Expected read edges in function bodies, got 0"
        assert len(write_edges) > 0, \
            f"Expected write edges for let/assignment, got 0"

        # Specific variable checks
        read_targets = [e.get("target_text", "") for e in read_edges]
        write_targets = [e.get("target_text", "") for e in write_edges]

        assert any("result" in t for t in read_targets), \
            f"'result' variable read not found: {read_targets}"
        assert any("buffer" in t for t in write_targets), \
            f"'buffer' variable write not found: {write_targets}"


# ---------------------------------------------------------------------------
# Dart
# ---------------------------------------------------------------------------

class TestDartExtractor:
    """P52: Dart language extractor tests — classes, mixins, enums, annotations."""

    # -- helper --
    @staticmethod
    def _load_fixture():
        import tree_sitter_language_pack
        from tws_graph.indexer.dart_extractor import visit_dart

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "dart", "sample.dart")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        return visit_dart(fixture_path, code, tree)

    # -- inline code tests (1-5) --

    def test_simple_class(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
class Counter {
  int _value = 0;

  void increment() {
    _value++;
  }

  int get value => _value;
}
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/counter.dart", code, tree)

        classes = [n for n in result.nodes if n["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "Counter"

        methods = [n for n in result.nodes if n["kind"] == "method"]
        assert len(methods) >= 2  # increment + value getter

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 1  # _value

    def test_abstract_class(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
abstract class Shape {
  double area();
  double perimeter();
}

class Circle extends Shape {
  double radius;
  Circle(this.radius);

  @override
  double area() => 3.14 * radius * radius;

  @override
  double perimeter() => 2 * 3.14 * radius;
}
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/shape.dart", code, tree)

        classes = [n for n in result.nodes if n["kind"] == "class"]
        assert len(classes) >= 2  # Shape + Circle

        # Shape is abstract
        shape = [n for n in result.nodes if n["name"] == "Shape"]
        assert len(shape) == 1
        assert shape[0]["is_abstract"] == 1

        # Circle is not abstract
        circle = [n for n in result.nodes if n["name"] == "Circle"]
        assert len(circle) == 1
        assert circle[0]["is_abstract"] == 0

    def test_mixin(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
mixin Logger {
  bool _enabled = true;
  void log(String msg) => print('[LOG] \$msg');
}
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/logger.dart", code, tree)

        classes = [n for n in result.nodes if n["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "Logger"

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 1  # _enabled

        methods = [n for n in result.nodes if n["kind"] == "method"]
        assert len(methods) >= 1  # log

    def test_enum(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
enum Color { red, green, blue }
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/color.dart", code, tree)

        enums = [n for n in result.nodes if n["kind"] == "enum"]
        assert len(enums) == 1
        assert enums[0]["name"] == "Color"

        consts = [n for n in result.nodes if n["kind"] == "enum_constant"]
        assert len(consts) >= 3

    def test_call_edges(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
class Worker {
  void start() {
    init();
    process();
    cleanup();
  }

  void init() {}
  void process() {}
  void cleanup() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/worker.dart", code, tree)

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        assert len(call_edges) >= 3, f"Expected >=3 call edges, got {len(call_edges)}"

    # -- fixture tests (6-12) --

    def test_import_edges(self):
        result = self._load_fixture()

        import_edges = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = [e.get("target_text", "") for e in import_edges]

        assert len(import_edges) >= 6, \
            f"Expected >=6 import edges, got {len(import_edges)}: {target_texts}"
        assert any("dart:core" in t for t in target_texts), \
            f"'dart:core' not in imports: {target_texts}"
        assert any("dart:async" in t for t in target_texts), \
            f"'dart:async' not in imports: {target_texts}"
        assert any("package:http/http.dart" in t for t in target_texts), \
            f"'package:http/http.dart' not in imports: {target_texts}"
        assert any("package:meta/meta.dart" in t for t in target_texts), \
            f"'package:meta/meta.dart' not in imports: {target_texts}"
        assert any("package:json_annotation/json_annotation.dart" in t for t in target_texts), \
            f"'package:json_annotation/json_annotation.dart' not in imports: {target_texts}"
        assert any("dart:convert" in t for t in target_texts), \
            f"'dart:convert' not in imports: {target_texts}"

    def test_class_inheritance(self):
        result = self._load_fixture()

        extends_edges = [e for e in result.edges if e["kind"] == "extends"]
        assert len(extends_edges) >= 1, f"Expected >=1 extends edges, got {len(extends_edges)}"

        target_texts = [e.get("target_text", "") for e in extends_edges]
        assert any("User" in t for t in target_texts), \
            f"'User' not in extends targets: {target_texts}"

    def test_interface_implementation(self):
        result = self._load_fixture()

        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        assert len(impl_edges) >= 1, f"Expected >=1 implements edges, got {len(impl_edges)}"

        target_texts = [e.get("target_text", "") for e in impl_edges]
        assert any("Comparable" in t for t in target_texts), \
            f"'Comparable' not in implements targets: {target_texts}"

    def test_mixin_application(self):
        result = self._load_fixture()

        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        target_texts = [e.get("target_text", "") for e in impl_edges]

        # AdminUser uses Logger mixin via "with Logger" clause
        assert any("Logger" in t for t in target_texts), \
            f"'Logger' mixin not found in implements edges: {target_texts}"

    def test_annotations(self):
        result = self._load_fixture()

        decorates_edges = [e for e in result.edges if e["kind"] == "decorates"]
        assert len(decorates_edges) > 0, f"Expected decorates edges, got {len(decorates_edges)}"

        target_texts = [e.get("target_text", "") for e in decorates_edges]
        assert any("override" in t for t in target_texts), \
            f"'override' annotation not found: {target_texts}"
        assert any("deprecated" in t for t in target_texts), \
            f"'deprecated' annotation not found: {target_texts}"
        assert any("JsonSerializable" in t for t in target_texts), \
            f"'JsonSerializable' annotation not found: {target_texts}"

    def test_visibility(self):
        from tws_graph.indexer.dart_extractor import visit_dart
        import tree_sitter_language_pack

        code = """
class Service {
  String publicField = '';
  String _privateField = '';

  void publicMethod() {}
  void _privateMethod() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("dart")
        tree = parser.parse(code)
        result = visit_dart("src/service.dart", code, tree)

        vis_map = {n["name"]: n["visibility"] for n in result.nodes}
        assert vis_map.get("publicField") == "public"
        assert vis_map.get("_privateField") == "private"
        assert vis_map.get("publicMethod") == "public"
        assert vis_map.get("_privateMethod") == "private"

    def test_variable_reads_writes(self):
        result = self._load_fixture()

        read_edges = [e for e in result.edges if e["kind"] == "reads"]
        write_edges = [e for e in result.edges if e["kind"] == "writes"]

        assert len(read_edges) > 0, f"Expected read edges, got 0"
        assert len(write_edges) > 0, f"Expected write edges, got 0"

        read_targets = [e.get("target_text", "") for e in read_edges]
        write_targets = [e.get("target_text", "") for e in write_edges]

        # Check that some variables from the fixture are being read/written
        assert any("repo" in t for t in read_targets) or any("admin" in t for t in read_targets), \
            f"Expected variable reads, got: {read_targets[:20]}"
        assert any("repo" in t for t in write_targets) or any("admin" in t for t in write_targets), \
            f"Expected variable writes, got: {write_targets[:20]}"


# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------

class TestSwiftExtractor:
    """P52: Swift language extractor tests — classes, protocols, structs, enums, extensions."""

    # -- helper --
    @staticmethod
    def _load_fixture():
        import tree_sitter_language_pack
        from tws_graph.indexer.swift_extractor import visit_swift

        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "swift", "sample.swift")
        with open(fixture_path, "r", encoding="utf-8") as f:
            code = f.read()

        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        return visit_swift(fixture_path, code, tree)

    # -- inline code tests (1-5) --

    def test_simple_class(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
class Calculator {
    var value: Int = 0

    func add(_ x: Int) {
        value += x
    }

    func getValue() -> Int {
        return value
    }
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Calculator.swift", code, tree)

        classes = [n for n in result.nodes if n["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "Calculator"

        methods = [n for n in result.nodes if n["kind"] == "method"]
        assert len(methods) >= 2  # add + getValue

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 1  # value

    def test_protocol(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
protocol Identifiable {
    var id: String { get }
    func identify() -> String
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Identifiable.swift", code, tree)

        interfaces = [n for n in result.nodes if n["kind"] == "interface"]
        assert len(interfaces) == 1
        assert interfaces[0]["name"] == "Identifiable"

    def test_struct(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
struct Point {
    var x: Double
    var y: Double
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Point.swift", code, tree)

        classes = [n for n in result.nodes if n["kind"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "Point"

        props = [n for n in result.nodes if n["kind"] == "property"]
        assert len(props) >= 2

    def test_enum(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
enum Direction {
    case north
    case south
    case east
    case west
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Direction.swift", code, tree)

        enums = [n for n in result.nodes if n["kind"] == "enum"]
        assert len(enums) == 1
        assert enums[0]["name"] == "Direction"

        consts = [n for n in result.nodes if n["kind"] == "enum_constant"]
        assert len(consts) >= 4

    def test_call_edges(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
class Worker {
    func start() {
        init()
        process()
        cleanup()
    }

    func init() {}
    func process() {}
    func cleanup() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Worker.swift", code, tree)

        call_edges = [e for e in result.edges if e["kind"] == "calls"]
        assert len(call_edges) >= 3, f"Expected >=3 call edges, got {len(call_edges)}"

    # -- fixture tests (6-10) --

    def test_import_edges(self):
        result = self._load_fixture()

        import_edges = [e for e in result.edges if e["kind"] == "imports"]
        target_texts = [e.get("target_text", "") for e in import_edges]

        assert len(import_edges) >= 2, \
            f"Expected >=2 import edges, got {len(import_edges)}: {target_texts}"
        assert any("Foundation" in t for t in target_texts), \
            f"'Foundation' not in imports: {target_texts}"
        assert any("UIKit" in t for t in target_texts), \
            f"'UIKit' not in imports: {target_texts}"

    def test_class_inheritance(self):
        result = self._load_fixture()

        extends_edges = [e for e in result.edges if e["kind"] == "extends"]
        assert len(extends_edges) >= 1, \
            f"Expected >=1 extends edges, got {len(extends_edges)}"

        # UserRecord extends BaseRecord
        user_nodes = [n for n in result.nodes if n["name"] == "UserRecord"]
        base_nodes = [n for n in result.nodes if n["name"] == "BaseRecord"]
        assert len(user_nodes) >= 1, "UserRecord node not found"
        assert len(base_nodes) >= 1, "BaseRecord node not found"

        user_id = user_nodes[0]["id"]
        base_id = base_nodes[0]["id"]
        found = any(
            e["source"] == user_id and e["target"] == base_id
            for e in extends_edges
        )
        assert found, f"UserRecord should extend BaseRecord. extends_edges={extends_edges}"

    def test_protocol_conformance(self):
        result = self._load_fixture()

        impl_edges = [e for e in result.edges if e["kind"] == "implements"]
        assert len(impl_edges) >= 1, \
            f"Expected >=1 implements edges, got {len(impl_edges)}"

        # MemoryStore should implement DataStore
        mem_nodes = [n for n in result.nodes if n["name"] == "MemoryStore"]
        ds_nodes = [n for n in result.nodes if n["name"] == "DataStore"]
        assert len(mem_nodes) >= 1, "MemoryStore node not found"
        assert len(ds_nodes) >= 1, "DataStore node not found"

        mem_id = mem_nodes[0]["id"]
        ds_id = ds_nodes[0]["id"]
        found = any(
            e["source"] == mem_id and e["target"] == ds_id
            for e in impl_edges
        )
        assert found, f"MemoryStore should implement DataStore. impl_edges={impl_edges}"

    def test_visibility(self):
        from tws_graph.indexer.swift_extractor import visit_swift
        import tree_sitter_language_pack

        code = """
public class Service {
    public func api() {}
    private func helper() {}
    internal func work() {}
}
"""
        parser = tree_sitter_language_pack.get_parser("swift")
        tree = parser.parse(code)
        result = visit_swift("src/Service.swift", code, tree)

        methods = [n for n in result.nodes if n["kind"] == "method"]
        assert len(methods) >= 3, f"Expected >=3 methods, got {len(methods)}"

        vis_map = {n["name"]: n["visibility"] for n in result.nodes}
        assert vis_map.get("api") == "public", f"vis_map={vis_map}"
        assert vis_map.get("helper") == "private", f"vis_map={vis_map}"
        assert vis_map.get("work") == "internal", f"vis_map={vis_map}"

    def test_variable_reads_writes(self):
        result = self._load_fixture()

        read_edges = [e for e in result.edges if e["kind"] == "reads"]
        write_edges = [e for e in result.edges if e["kind"] == "writes"]

        assert len(read_edges) > 0, f"Expected read edges, got 0"
        assert len(write_edges) > 0, f"Expected write edges, got 0"

        read_targets = [e.get("target_text", "") for e in read_edges]
        write_targets = [e.get("target_text", "") for e in write_edges]

        # Check that variables from the fixture are read/written
        assert any("name" in t for t in read_targets) or any("storage" in t for t in read_targets), \
            f"Expected variable reads, got: {read_targets[:20]}"
        assert any("storage" in t for t in write_targets) or any("stats" in t for t in write_targets), \
            f"Expected variable writes, got: {write_targets[:20]}"
