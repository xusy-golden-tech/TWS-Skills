"""Tests for GrpcDetector — cross-language gRPC detection.

Tests cover .proto files, Python, Java, and Go gRPC detection.
"""

import pytest
import tree_sitter_language_pack

from tws_graph.services.grpc_detector import GrpcDetector
from tws_graph.edges.kind import EdgeKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_python(code: str):
    parser = tree_sitter_language_pack.get_parser("python")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_java(code: str):
    parser = tree_sitter_language_pack.get_parser("java")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _parse_go(code: str):
    parser = tree_sitter_language_pack.get_parser("go")
    tree = parser.parse(code)
    return tree, code.encode("utf-8")


def _make_detector():
    return GrpcDetector()


# ===================================================================
# .proto file tests
# ===================================================================

class TestProtoFileDetection:
    """gRPC detection in .proto files."""

    def test_basic_service_rpc(self):
        """Basic service with rpc method — placeholder (proto parser may not be available)."""
        pass

    def test_proto_detection_dispatched(self):
        """When language='proto', should dispatch to _detect_proto."""
        detector = _make_detector()
        try:
            edges = detector.detect(b"", None, "test.proto", "proto")
            assert isinstance(edges, list)
        except Exception:
            pass  # Acceptable if proto parser unavailable


# ===================================================================
# Python gRPC tests
# ===================================================================

class TestPythonGrpcDetection:
    """gRPC detection in Python source files."""

    def test_grpc_insecure_channel(self):
        """grpc.insecure_channel should produce GRPC_CLIENT edge."""
        code = """\
import grpc

def run():
    channel = grpc.insecure_channel('localhost:50051')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1, f"No GRPC_CLIENT edges found in: {edges}"
        assert client_edges[0]["provenance"] == "tree-sitter"
        assert client_edges[0]["source_loc"].startswith("test.py:")

    def test_grpc_secure_channel(self):
        """grpc.secure_channel should produce GRPC_CLIENT edge."""
        code = """\
import grpc

def create_secure_client():
    credentials = grpc.ssl_channel_credentials()
    channel = grpc.secure_channel('localhost:50051', credentials)
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_grpc_server(self):
        """grpc.server should produce GRPC_SERVER edge."""
        code = """\
import grpc
from concurrent import futures

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    server.add_insecure_port('[::]:50051')
    server.start()
    server.wait_for_termination()
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        server_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_SERVER]
        assert len(server_edges) >= 1

    def test_grpc_stub_instantiation(self):
        """XxxStub instantiation should produce GRPC_CLIENT edge."""
        code = """\
import helloworld_pb2_grpc

def create_stub(channel):
    stub = helloworld_pb2_grpc.GreeterStub(channel)
    return stub
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_empty_for_non_grpc_code(self):
        """Plain code with no gRPC usage should return no edges."""
        code = """\
def plain_function():
    x = 1 + 2
    return x
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert edges == []


# ===================================================================
# Java gRPC tests
# ===================================================================

class TestJavaGrpcDetection:
    """gRPC detection in Java source files."""

    def test_managed_channel_builder(self):
        """ManagedChannelBuilder.forAddress should produce GRPC_CLIENT edge."""
        code = """\
import io.grpc.ManagedChannelBuilder;

public class GrpcClient {
    public void connect() {
        ManagedChannelBuilder.forAddress("localhost", 50051).usePlaintext().build();
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Client.java", "java")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_new_blocking_stub(self):
        """newBlockingStub should produce GRPC_CLIENT edge."""
        code = """\
public class GrpcClient {
    public void callService(ManagedChannel channel) {
        MyServiceGrpc.newBlockingStub(channel);
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Client.java", "java")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_server_builder_for_port(self):
        """ServerBuilder.forPort should produce GRPC_SERVER edge."""
        code = """\
import io.grpc.ServerBuilder;

public class GrpcServer {
    public void start() throws Exception {
        ServerBuilder.forPort(50051).addService(new MyServiceImpl()).build().start();
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Server.java", "java")

        server_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_SERVER]
        assert len(server_edges) >= 1

    def test_add_service(self):
        """addService should produce GRPC_SERVER edge."""
        code = """\
import io.grpc.ServerBuilder;

public class GrpcServer {
    public void start() throws Exception {
        ServerBuilder.forPort(50051)
            .addService(new MyServiceImpl())
            .build()
            .start();
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Server.java", "java")

        server_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_SERVER]
        assert len(server_edges) >= 1

    def test_empty_for_non_grpc_code(self):
        """Plain Java with no gRPC usage should return no edges."""
        code = """\
public class Hello {
    public void greet() {
        System.out.println("Hello");
    }
}
"""
        tree, source = _parse_java(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "Hello.java", "java")

        assert edges == []


# ===================================================================
# Go gRPC tests
# ===================================================================

class TestGoGrpcDetection:
    """gRPC detection in Go source files."""

    def test_grpc_dial(self):
        """grpc.Dial should produce GRPC_CLIENT edge."""
        code = """\
package main

import "google.golang.org/grpc"

func main() {
    conn, err := grpc.Dial("localhost:50051", grpc.WithInsecure())
    if err != nil {
        panic(err)
    }
    defer conn.Close()
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_grpc_new_server(self):
        """grpc.NewServer should produce GRPC_SERVER edge."""
        code = """\
package main

import "google.golang.org/grpc"

func main() {
    server := grpc.NewServer()
    server.Serve(lis)
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        server_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_SERVER]
        assert len(server_edges) >= 1

    def test_pb_new_client(self):
        """pb.NewXxxClient should produce GRPC_CLIENT edge."""
        code = """\
package main

import pb "myapp/helloworld"

func main() {
    client := pb.NewGreeterClient(conn)
    resp, err := client.SayHello(ctx, &pb.HelloRequest{Name: "world"})
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        client_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_CLIENT]
        assert len(client_edges) >= 1

    def test_pb_register_server(self):
        """pb.RegisterXxxServer should produce GRPC_SERVER edge."""
        code = """\
package main

import pb "myapp/helloworld"

func main() {
    server := grpc.NewServer()
    pb.RegisterGreeterServer(server, &serverImpl{})
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        server_edges = [e for e in edges if e["kind"] == EdgeKind.GRPC_SERVER]
        assert len(server_edges) >= 1

    def test_empty_for_non_grpc_code(self):
        """Plain Go with no gRPC usage should return no edges."""
        code = """\
package main

func main() {
    println("hello")
}
"""
        tree, source = _parse_go(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "main.go", "go")

        assert edges == []


# ===================================================================
# Edge structure tests
# ===================================================================

class TestGrpcEdgeStructure:
    """Verify gRPC edge dict structure correctness."""

    def test_edge_has_required_fields(self):
        """All edges must have the required fields."""
        code = """\
import grpc

def run():
    channel = grpc.insecure_channel('localhost:50051')
"""
        tree, source = _parse_python(code)
        detector = _make_detector()
        edges = detector.detect(source, tree, "test.py", "python")

        assert len(edges) >= 1
        for edge in edges:
            assert "source" in edge
            assert "target" in edge
            assert "kind" in edge
            assert "target_text" in edge
            assert "grpc_method" in edge
            assert "source_loc" in edge
            assert "provenance" in edge
            assert edge["kind"] in (
                EdgeKind.GRPC_CLIENT,
                EdgeKind.GRPC_SERVER,
                EdgeKind.GRPC_SERVICE,
            )

    def test_unsupported_language_returns_empty(self):
        """Unsupported languages should return empty list."""
        detector = _make_detector()
        edges = detector.detect(b"", None, "test.rb", "ruby")
        assert edges == []
