"""Lightweight gRPC call pattern matching — for inline use in extractors.

Unlike GrpcDetector (which does its own tree traversal), this module provides
O(1) pattern lookup functions that extractors call inside their existing walk
loops. No re-parsing needed.
"""

from __future__ import annotations

# ── Python gRPC patterns ────────────────────────────────────────────────
# Maps callee name → (direction_kind, service_hint)
_PYTHON_GRPC: dict[str, tuple[str, str]] = {
    # grpc library direct calls
    "grpc.server": ("grpc_server", "grpc"),
    "grpc.insecure_channel": ("grpc_client", "grpc"),
    "grpc.secure_channel": ("grpc_client", "grpc"),
    "grpc.aio.insecure_channel": ("grpc_client", "grpc"),
    "grpc.aio.secure_channel": ("grpc_client", "grpc"),
    # Generated stub patterns
    "grpc.experimental": ("grpc_client", ""),
}

# Suffix-based patterns — more general matching
_PYTHON_GRPC_SUFFIXES: list[tuple[str, str]] = [
    # (suffix, kind)
    ("Stub", "grpc_client"),           # e.g., GreeterStub(...)
    ("Servicer", "grpc_server"),       # e.g., GreeterServicer(...)
    ("Server", "grpc_server"),         # e.g., GreeterServer(...)
]

# Pattern-based detection for "add_XxxServicer_to_server"
_PYTHON_GRPC_CONTAINS: list[tuple[str, str]] = [
    ("add_", "grpc_server"),           # add_GreeterServicer_to_server
    ("Servicer_to_server", "grpc_server"),
]


def detect_python_grpc(callee_name: str) -> tuple[str, str] | None:
    """Detect if a Python call expression is a gRPC operation.

    Returns (edge_kind, service_name) or None.
    Edge kind: ``grpc_client`` or ``grpc_server``.
    """
    if not callee_name:
        return None

    # Exact match (O(1) dict lookup)
    result = _PYTHON_GRPC.get(callee_name)
    if result:
        return result

    # Suffix match
    for suffix, kind in _PYTHON_GRPC_SUFFIXES:
        if callee_name.endswith(suffix):
            # Extract service name: "my_pb2_grpc.GreeterStub" → "Greeter"
            parts = callee_name.split(".")
            svc = parts[-1].replace(suffix, "") if parts else ""
            return (kind, svc)

    # Contains match
    for substr, kind in _PYTHON_GRPC_CONTAINS:
        if substr in callee_name:
            # Extract service name from "add_GreeterServicer_to_server"
            if "add_" in callee_name and "Servicer" in callee_name:
                try:
                    svc = callee_name.split("add_")[1].split("Servicer")[0]
                    return (kind, svc)
                except (IndexError, ValueError):
                    pass
            return (kind, callee_name.split(".")[-1] if "." in callee_name else callee_name)

    return None


# ── TypeScript gRPC patterns ────────────────────────────────────────────

_TS_GRPC: dict[str, tuple[str, str]] = {
    "@grpc/grpc-js": ("grpc_client", "grpc-js"),
    "@grpc/proto-loader": ("grpc_service", "proto-loader"),
}

_TS_GRPC_SUFFIXES: list[tuple[str, str]] = [
    ("Client", "grpc_client"),          # e.g., GreeterClient, new GreeterClient()
    ("ServiceClient", "grpc_client"),
    ("Service", "grpc_server"),         # e.g., GreeterService
]


def detect_typescript_grpc(callee_name: str) -> tuple[str, str] | None:
    """Detect if a TS/JS call/new expression is a gRPC operation."""
    if not callee_name:
        return None

    # Exact match
    result = _TS_GRPC.get(callee_name)
    if result:
        return result

    # Suffix match
    for suffix, kind in _TS_GRPC_SUFFIXES:
        if callee_name.endswith(suffix):
            parts = callee_name.split(".")
            svc = parts[-1].replace(suffix, "") if parts else ""
            return (kind, svc)

    # Decorator pattern: @GrpcMethod, @GrpcService
    if callee_name in ("GrpcMethod", "GrpcService"):
        return ("grpc_service", callee_name)

    return None


# ── Java gRPC patterns ──────────────────────────────────────────────────

_JAVA_GRPC_SUFFIXES: list[tuple[str, str]] = [
    ("Grpc.newBlockingStub", "grpc_client"),
    ("Grpc.newStub", "grpc_client"),
    ("Grpc.newFutureStub", "grpc_client"),
    ("Builder.forPort", "grpc_server"),
    ("Builder.forAddress", "grpc_client"),
    (".addService", "grpc_server"),
    ("ServerBuilder", "grpc_server"),
    ("ManagedChannelBuilder", "grpc_client"),
    ("NettyChannelBuilder", "grpc_client"),
]


def detect_java_grpc(callee_name: str) -> tuple[str, str] | None:
    """Detect if a Java method invocation is a gRPC operation."""
    if not callee_name:
        return None
    for suffix, kind in _JAVA_GRPC_SUFFIXES:
        if callee_name.endswith(suffix):
            return (kind, callee_name.split(".")[-1] if "." in callee_name else callee_name)
    return None


# ── Go gRPC patterns ────────────────────────────────────────────────────

_GO_GRPC: dict[str, tuple[str, str]] = {
    "grpc.Dial": ("grpc_client", "grpc"),
    "grpc.DialContext": ("grpc_client", "grpc"),
    "grpc.NewServer": ("grpc_server", "grpc"),
}

_GO_GRPC_CONTAINS: list[tuple[str, str]] = [
    ("pb.New", "grpc_client"),
    ("pb.Register", "grpc_server"),
    (".Register", "grpc_server"),
    ("pb.Connect", "grpc_client"),
]


def detect_go_grpc(callee_name: str) -> tuple[str, str] | None:
    """Detect if a Go call expression is a gRPC operation."""
    if not callee_name:
        return None

    result = _GO_GRPC.get(callee_name)
    if result:
        return result

    for substr, kind in _GO_GRPC_CONTAINS:
        if substr in callee_name:
            return (kind, callee_name.split(".")[-1] if "." in callee_name else callee_name)

    return None
