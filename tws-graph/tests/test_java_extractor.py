"""Tests for Java extractor deep upgrade (P45).

P45a: Package & import system
P45b: Annotation handling
P45c: Generic type handling
P45d: Method call depth upgrade
P45e: Modern Java features
P45f: Variable-level read/write tracking
"""

import pytest
from tws_graph.indexer.parser import extract_from_source


# ============================================================================
# Sample Java source snippets
# ============================================================================

JAVA_WITH_PACKAGE_IMPORTS = """\
package com.example.service;

import java.util.List;
import java.util.Map;
import java.util.ArrayList;
import static org.junit.Assert.assertEquals;
import static java.lang.Math.*;

public class UserService {
    private List<String> users;

    public UserService() {
        this.users = new ArrayList<>();
    }

    public void addUser(String name) {
        users.add(name);
    }
}
"""

JAVA_WITH_ANNOTATIONS = """\
package com.example.model;

import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.Column;

@Entity
@Table(name = "users")
public class User {
    @Id
    private Long id;

    @Column(name = "username", nullable = false)
    private String username;

    @Override
    public String toString() {
        return "User{" + id + ", " + username + "}";
    }

    @Deprecated
    public void oldMethod() {
    }
}
"""

JAVA_WITH_GENERICS = """\
package com.example.generics;

import java.util.List;
import java.util.Map;
import java.util.Optional;

public class Repository<T extends Comparable<T>> {
    private List<T> items;

    public Optional<T> findById(Long id) {
        return items.stream().filter(i -> i.compareTo(null) > 0).findFirst();
    }

    public Map<String, List<T>> groupByCategory() {
        return null;
    }
}
"""

JAVA_WITH_METHOD_CALLS = """\
package com.example.app;

import java.util.ArrayList;

public class OrderProcessor {
    private final Calculator calc = new Calculator();

    public void process() {
        // Chained call
        this.calc.init().compute().finalize();

        // Static call
        java.lang.Math.abs(-5);

        // Constructor call (instantiates)
        ArrayList<String> items = new ArrayList<>();

        // Method call with arguments
        calc.add(1, 2);

        // super call
        super.toString();
    }
}

class Calculator {
    public Calculator init() { return this; }
    public Calculator compute() { return this; }
    public Calculator finalize() { return this; }
    public int add(int a, int b) { return a + b; }
}
"""

JAVA_WITH_MODERN_FEATURES = """\
package com.example.modern;

import java.util.List;
import java.util.function.Function;

// Record (Java 14+)
public record Point(int x, int y) {
    public double distance() {
        return Math.sqrt(x * x + y * y);
    }
}

// Enum
enum Color {
    RED("#FF0000"),
    GREEN("#00FF00"),
    BLUE("#0000FF");

    private final String hex;
    Color(String hex) { this.hex = hex; }
    public String getHex() { return hex; }
}

// Interface with default and static methods
interface Calculator {
    default int add(int a, int b) {
        return a + b;
    }

    static Calculator create() {
        return new Calculator() {};
    }
}

// Lambda usage
class LambdaDemo {
    public void demo() {
        Function<Integer, Integer> doubler = (x) -> x * 2;
        List<Integer> nums = List.of(1, 2, 3);
        nums.stream().map(n -> n + 1).toList();
    }
}
"""

JAVA_WITH_READS_WRITES = """\
package com.example.data;

public class Counter {
    private int count = 0;

    public void increment() {
        count = count + 1;  // read count, write count
    }

    public int getValue() {
        return this.count;  // read count
    }

    public void reset(int initialValue) {
        int temp = initialValue;  // write temp, read initialValue
        this.count = temp;  // write count, read temp
    }

    public void update(Config cfg) {
        String name = cfg.getName();  // write name, calls getName
        int val = cfg.getValue();     // write val, calls getValue
        process(val);                 // read val, calls process
    }

    private void process(int x) {
        System.out.println(x);  // read x
    }
}

class Config {
    public String getName() { return "default"; }
    public int getValue() { return 42; }
}
"""


# ============================================================================
# P45a: Package & Import System
# ============================================================================

@pytest.fixture(scope="module")
def pkg_import_result():
    return extract_from_source("UserService.java", JAVA_WITH_PACKAGE_IMPORTS, "java")


class TestPackageAndImports:
    """P45a: Package declaration and import tracking."""

    def test_package_declaration_parsed(self, pkg_import_result):
        """Package declaration produces a module-level node."""
        pkgs = [n for n in pkg_import_result.nodes if n["kind"] == "package"]
        assert len(pkgs) == 1
        assert pkgs[0]["name"] == "com.example.service"

    def test_import_edges_produced(self, pkg_import_result):
        """Each import statement produces an imports edge."""
        import_edges = [e for e in pkg_import_result.edges if e["kind"] == "imports"]
        # java.util.List, java.util.Map, java.util.ArrayList, static org.junit.Assert.assertEquals, static java.lang.Math.*
        assert len(import_edges) >= 5

    def test_import_target_text_correct(self, pkg_import_result):
        """Import edge target_text contains the fully qualified import name."""
        import_edges = [e for e in pkg_import_result.edges if e["kind"] == "imports"]
        imports = [e.get("target_text", "") for e in import_edges]
        assert any("java.util.List" in t for t in imports)
        assert any("java.util.Map" in t for t in imports)
        assert any("java.util.ArrayList" in t for t in imports)

    def test_static_import_detected(self, pkg_import_result):
        """Static imports are tracked."""
        import_edges = [e for e in pkg_import_result.edges if e["kind"] == "imports"]
        imports = [e.get("target_text", "") for e in import_edges]
        assert any("org.junit.Assert.assertEquals" in t for t in imports)

    def test_wildcard_import_detected(self, pkg_import_result):
        """Wildcard imports (import static X.*) are tracked."""
        import_edges = [e for e in pkg_import_result.edges if e["kind"] == "imports"]
        imports = [e.get("target_text", "") for e in import_edges]
        assert any("java.lang.Math.*" in t for t in imports)

    def test_qualified_name_format_upgraded(self, pkg_import_result):
        """qualified_name uses Java-standard format: package.Class::member (not file_path::name)."""
        classes = [n for n in pkg_import_result.nodes if n["kind"] == "class"]
        assert len(classes) >= 1
        for c in classes:
            # Should NOT contain file path directly
            assert not c["qualified_name"].startswith("UserService.java::")
            # Should contain the package prefix
            assert "com.example.service" in c["qualified_name"]

    def test_method_qualified_name_includes_package(self, pkg_import_result):
        """Method qualified_name includes package and class."""
        methods = [n for n in pkg_import_result.nodes if n["kind"] == "method"]
        assert len(methods) >= 1
        for m in methods:
            assert "com.example.service" in m["qualified_name"]


# ============================================================================
# P45b: Annotation Handling
# ============================================================================

@pytest.fixture(scope="module")
def annot_result():
    return extract_from_source("User.java", JAVA_WITH_ANNOTATIONS, "java")


class TestAnnotations:
    """P45b: Annotation handling — decorates edges."""

    def test_class_annotation_produces_decorates(self, annot_result):
        """Class-level @Entity annotation produces decorates edge."""
        decorates = [e for e in annot_result.edges if e["kind"] == "decorates"]
        # @Entity and @Table on class
        assert len(decorates) >= 2

    def test_field_annotation_produces_decorates(self, annot_result):
        """Field-level @Id @Column annotations produce decorates edges."""
        decorates = [e for e in annot_result.edges if e["kind"] == "decorates"]
        # @Id and @Column on fields
        target_texts = [e.get("target_text", "") for e in decorates]
        assert any("@Id" in t for t in target_texts)
        assert any("@Column" in t for t in target_texts)

    def test_method_annotation_produces_decorates(self, annot_result):
        """Method-level @Override @Deprecated annotations produce decorates edges."""
        decorates = [e for e in annot_result.edges if e["kind"] == "decorates"]
        target_texts = [e.get("target_text", "") for e in decorates]
        assert any("@Override" in t for t in target_texts)
        assert any("@Deprecated" in t for t in target_texts)


# ============================================================================
# P45c: Generic Type Handling
# ============================================================================

@pytest.fixture(scope="module")
def generic_result():
    return extract_from_source("Repository.java", JAVA_WITH_GENERICS, "java")


class TestGenerics:
    """P45c: Generic type handling — type_ref edges."""

    def test_type_parameter_produces_type_ref(self, generic_result):
        """Generic type parameter <T> is tracked."""
        type_refs = [e for e in generic_result.edges if e["kind"] == "type_ref"]
        # Should have type_ref edges from generic usages
        assert len(type_refs) > 0

    def test_list_type_argument_produces_type_ref(self, generic_result):
        """List<T> produces type_ref to T."""
        type_refs = [e for e in generic_result.edges if e["kind"] == "type_ref"]
        target_texts = [e.get("target_text", "") for e in type_refs]
        assert any("T" in t for t in target_texts)

    def test_nested_generic_produces_type_ref(self, generic_result):
        """Map<String, List<T>> produces type_ref to String, List, T."""
        type_refs = [e for e in generic_result.edges if e["kind"] == "type_ref"]
        target_texts = [e.get("target_text", "") for e in type_refs]
        assert any("String" in t for t in target_texts)


# ============================================================================
# P45d: Method Call Depth Upgrade
# ============================================================================

@pytest.fixture(scope="module")
def method_call_result():
    return extract_from_source("OrderProcessor.java", JAVA_WITH_METHOD_CALLS, "java")


class TestMethodCalls:
    """P45d: Method call depth upgrade — chain calls, static, instantiates."""

    def test_chained_call_produces_multiple_edges(self, method_call_result):
        """Chained call calc.init().compute().finalize() produces 3 calls edges."""
        calls = [e for e in method_call_result.edges if e["kind"] == "calls"]
        target_texts = [e.get("target_text", "") for e in calls]
        # Each step in chain: init, compute, finalize
        assert any("init" in t for t in target_texts)
        assert any("compute" in t for t in target_texts)
        assert any("finalize" in t for t in target_texts)

    def test_instantiates_edge_produced(self, method_call_result):
        """new ArrayList<>() produces instantiates edge."""
        instantiates = [e for e in method_call_result.edges if e["kind"] == "instantiates"]
        assert len(instantiates) >= 1
        target_texts = [e.get("target_text", "") for e in instantiates]
        assert any("ArrayList" in t for t in target_texts)

    def test_static_call_produces_calls(self, method_call_result):
        """Static call Math.abs() produces calls edge."""
        calls = [e for e in method_call_result.edges if e["kind"] == "calls"]
        target_texts = [e.get("target_text", "") for e in calls]
        assert any("abs" in t for t in target_texts)

    def test_super_call_produces_calls(self, method_call_result):
        """super.toString() produces calls edge to toString."""
        calls = [e for e in method_call_result.edges if e["kind"] == "calls"]
        target_texts = [e.get("target_text", "") for e in calls]
        assert any("toString" in t for t in target_texts)


# ============================================================================
# P45e: Modern Java Features
# ============================================================================

@pytest.fixture(scope="module")
def modern_result():
    return extract_from_source("Point.java", JAVA_WITH_MODERN_FEATURES, "java")


class TestModernJava:
    """P45e: Modern Java features — record, enum, interface default, lambda."""

    def test_record_produces_class_node(self, modern_result):
        """Record type produces a class node."""
        records = [n for n in modern_result.nodes
                   if n["kind"] == "class" and n["name"] == "Point"]
        assert len(records) == 1

    def test_enum_produces_class_node(self, modern_result):
        """Enum type produces a class node."""
        enums = [n for n in modern_result.nodes
                 if n["kind"] == "class" and n["name"] == "Color"]
        assert len(enums) == 1

    def test_enum_constants_produced(self, modern_result):
        """Enum constants (RED, GREEN, BLUE) are extracted."""
        enum_constants = [n for n in modern_result.nodes if n["kind"] == "enum_constant"]
        assert len(enum_constants) >= 3
        names = {n["name"] for n in enum_constants}
        assert "RED" in names
        assert "GREEN" in names
        assert "BLUE" in names

    def test_interface_default_method_produced(self, modern_result):
        """Interface default method is extracted."""
        methods = [n for n in modern_result.nodes
                   if n["kind"] == "method" and n["name"] == "add"]
        assert len(methods) >= 1

    def test_interface_static_method_produced(self, modern_result):
        """Interface static method is extracted."""
        methods = [n for n in modern_result.nodes
                   if n["kind"] == "method" and n["name"] == "create"]
        assert len(methods) >= 1

    def test_lambda_produces_function_node(self, modern_result):
        """Lambda expressions produce anonymous function nodes."""
        lambdas = [n for n in modern_result.nodes if n["kind"] == "function"]
        # At least one lambda (the doubler or the map lambda)
        assert len(lambdas) >= 1


# ============================================================================
# P45f: Variable-Level Read/Write Tracking
# ============================================================================

@pytest.fixture(scope="module")
def rw_result():
    return extract_from_source("Counter.java", JAVA_WITH_READS_WRITES, "java")


class TestReadsWrites:
    """P45f: Variable-level read/write tracking."""

    def test_reads_edges_produced(self, rw_result):
        """Reads edges track variable access."""
        reads = [e for e in rw_result.edges if e["kind"] == "reads"]
        assert len(reads) > 0

    def test_writes_edges_produced(self, rw_result):
        """Writes edges track variable assignment."""
        writes = [e for e in rw_result.edges if e["kind"] == "writes"]
        assert len(writes) > 0

    def test_field_read_produces_reads(self, rw_result):
        """Reading this.count produces reads edge."""
        reads = [e for e in rw_result.edges if e["kind"] == "reads"]
        target_texts = [e.get("target_text", "") for e in reads]
        assert any("count" in t for t in target_texts)

    def test_field_write_produces_writes(self, rw_result):
        """Writing this.count produces writes edge."""
        writes = [e for e in rw_result.edges if e["kind"] == "writes"]
        target_texts = [e.get("target_text", "") for e in writes]
        assert any("count" in t for t in target_texts)

    def test_local_variable_write_tracked(self, rw_result):
        """Local variable declaration produces writes edge."""
        writes = [e for e in rw_result.edges if e["kind"] == "writes"]
        target_texts = [e.get("target_text", "") for e in writes]
        assert any("temp" in t for t in target_texts)

    def test_parameter_read_tracked(self, rw_result):
        """Method parameter reading produces reads edge."""
        reads = [e for e in rw_result.edges if e["kind"] == "reads"]
        target_texts = [e.get("target_text", "") for e in reads]
        assert any("x" in t for t in target_texts)

    def test_data_flows_edge_produced(self, rw_result):
        """Argument → parameter produces data_flows edge."""
        data_flows = [e for e in rw_result.edges if e["kind"] == "data_flows"]
        # process(val) call should produce data_flow from val to process's x param
        assert len(data_flows) > 0
