"""GrpcAnalysisPass — detect gRPC service/client/server edges during indexing.

Runs the P10 GrpcDetector after the core indexing pipeline has populated
symbol nodes.  Converts gRPC detection results to edge dicts with kinds
``grpc_service``, ``grpc_client``, and ``grpc_server``.

grpc edges use empty target (like http_calls / env_accesses) with service
name and method name stored as target_text and in edge properties.

Design: This pass runs automatically as part of the index pipeline.
No manual ``tws-graph analyze`` needed.
"""

from __future__ import annotations

import json
import logging

from tws_graph.pipeline.pass_interface import Pass, PipelineContext
from tws_graph.edges.kind import EdgeKind

logger = logging.getLogger(__name__)

# Languages that have gRPC patterns defined in patterns.yaml
_GRPC_LANGUAGES = frozenset({"python", "typescript", "tsx", "java", "go"})
_GRPC_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".java", ".go", ".proto",
})


class GrpcAnalysisPass(Pass):
    """Detect gRPC service definitions and client/server usage.

    Runs GrpcDetector on files in languages that have gRPC pattern support.
    Inserts grpc_service, grpc_client, and grpc_server edges.

    Depends on ``node-insert`` because function/method nodes must already
    exist in the Store for edge source resolution.

    Edges produced:
    * ``grpc_service`` — proto service definition or service registration
    * ``grpc_client`` — gRPC client stub usage
    * ``grpc_server`` — gRPC server implementation / registration
    """

    name: str = "grpc-analysis"
    description: str = (
        "Detect gRPC service/client/server usage from proto files and "
        "language-specific stub patterns (grpc_service/grpc_client/grpc_server)"
    )
    dependencies: list[str] = ["node-insert"]
    supports_incremental: bool = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        store = ctx.store
        if store is None:
            logger.warning("GrpcAnalysisPass: ctx.store is None, skipping")
            ctx.metadata["grpc_edge_count"] = 0
            return ctx

        root_dir = ctx.root_dir
        if not root_dir:
            logger.warning("GrpcAnalysisPass: ctx.root_dir is empty, skipping")
            ctx.metadata["grpc_edge_count"] = 0
            return ctx

        try:
            from tws_graph.services.grpc_detector import GrpcDetector
            from tree_sitter_language_pack import get_parser
        except ImportError as exc:
            logger.warning("GrpcAnalysisPass: import failed: %s", exc)
            ctx.errors.append({
                "pass": self.name,
                "error": str(exc),
                "severity": "warning",
            })
            ctx.metadata["grpc_edge_count"] = 0
            return ctx

        import os

        detector = GrpcDetector()

        # Delete old gRPC edges (full rebuild each run)
        for kind in (EdgeKind.GRPC_SERVICE, EdgeKind.GRPC_CLIENT, EdgeKind.GRPC_SERVER):
            try:
                store.delete_edges_by_kind(kind.value)
            except AttributeError:
                pass

        all_edges: list[dict] = []

        for file_record in store.get_all_files():
            file_path = file_record.get("path", "")
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in _GRPC_EXTENSIONS:
                continue

            language = file_record.get("language", "")
            if not language and ext == ".proto":
                language = "proto"
            if language not in _GRPC_LANGUAGES and language != "proto":
                continue

            # Collect function/method node IDs for this file (for source resolution)
            func_node_ids: dict[str, str] = {}
            try:
                for node in store.iter_nodes_by_file(file_path):
                    if node.get("kind") in ("function", "method"):
                        qname = node.get("qualified_name", "")
                        nid = node.get("id", "")
                        if qname and nid:
                            func_node_ids[qname] = nid
            except Exception:
                continue

            # Read and parse the file
            full_path = os.path.join(root_dir, file_path)
            try:
                with open(full_path, "rb") as fh:
                    source_bytes = fh.read()
            except OSError:
                continue

            try:
                if language == "proto" or ext == ".proto":
                    parser = get_parser("python")  # proto uses tree-sitter-proto, fallback
                    tree = parser.parse(source_bytes)
                    language = "proto"
                else:
                    parser = get_parser(language)
                    tree = parser.parse(source_bytes)
            except Exception:
                continue

            # Detect gRPC edges
            try:
                grpc_edges = detector.detect(source_bytes, tree, file_path, language)
            except Exception:
                continue

            if not grpc_edges:
                continue

            # Resolve source node IDs: GrpcDetector uses hash_id which may differ
            # from the node IDs in the store. Map via qualified name.
            for edge in grpc_edges:
                source_nid = edge["source"]
                # Try to find the source node by checking if the ID exists in store
                # If not, we need to resolve by qualified name
                resolved_source = _resolve_source(edge, func_node_ids, file_path, source_bytes, parser, language)
                if resolved_source:
                    all_edges.append({
                        "source": resolved_source,
                        "target": edge.get("target", ""),
                        "kind": edge["kind"],
                        "target_text": edge.get("target_text", ""),
                        "source_loc": edge.get("source_loc", ""),
                        "provenance": "tree-sitter",
                        "properties": json.dumps({
                            "grpc_method": edge.get("grpc_method", ""),
                        }, ensure_ascii=False),
                    })

        if all_edges:
            try:
                store.insert_edges(all_edges)
            except Exception as exc:
                logger.warning("GrpcAnalysisPass: insert_edges failed: %s", exc)
                ctx.errors.append({
                    "pass": self.name,
                    "error": str(exc),
                    "severity": "warning",
                })

        ctx.metadata["grpc_edge_count"] = len(all_edges)
        logger.info("GrpcAnalysisPass: %d gRPC edges inserted", len(all_edges))
        return ctx


def _resolve_source(edge: dict, func_node_ids: dict[str, str],
                    file_path: str, source_bytes: bytes, parser,
                    language: str) -> str | None:
    """Resolve the source node ID for a gRPC edge.

    The GrpcDetector computes its own hash IDs. We need to map back to
    the actual node IDs in the store.
    """
    source_nid = edge.get("source", "")
    source_loc = edge.get("source_loc", "")

    # Strategy 1: Direct ID match (unlikely if hash computation differs)
    if source_nid and source_nid in func_node_ids.values():
        return source_nid

    # Strategy 2: Parse the source_loc (file:line) and find the enclosing function
    if source_loc and ":" in source_loc:
        line = int(source_loc.rsplit(":", 1)[1])
        try:
            tree = parser.parse(source_bytes)
        except Exception:
            return None
        func_name = _find_enclosing_function(tree.root_node(), line, source_bytes)
        if func_name:
            # Build qualified name: file_path::func_name (or file_path::ClassName::method_name)
            for qname, nid in func_node_ids.items():
                if qname.endswith(f"::{func_name}"):
                    return nid
        # Fallback: use __global__ sentinel
        for qname, nid in func_node_ids.items():
            if qname.endswith("::__global__"):
                return nid

    return None


def _find_enclosing_function(root, target_line: int, source: bytes) -> str | None:
    """Find the function/method that contains the given line number."""
    func_kinds = frozenset({
        "function_definition", "function_declaration", "method_definition",
        "method_declaration", "function_declaration",
    })

    def walk(node) -> str | None:
        if node.kind() in func_kinds:
            start = node.start_position().row + 1
            end = node.end_position().row + 1
            if start <= target_line <= end:
                name_node = node.child_by_field_name("name")
                if name_node:
                    return source[name_node.start_byte():name_node.end_byte()].decode("utf-8")
        for i in range(node.child_count()):
            result = walk(node.child(i))
            if result:
                return result
        return None

    return walk(root)
