"""Proto extractor — parse .proto files for service/rpc/message definitions.

Produces:
- service nodes (with kind="service")
- method nodes (rpc methods with kind="method")
- struct nodes (message definitions with kind="struct")
- enum nodes (enum definitions with kind="enum")
- grpc_service edges (service → rpc method)
"""

from __future__ import annotations

from ..base import BaseExtractor, ExtractionContext, node_text, children


def _find_identifier(parent_node, source: bytes) -> str | None:
    """Find the identifier child of a name node (service_name/rpc_name/etc).

    The proto grammar wraps names in a named node (e.g. service_name)
    containing a single identifier node. Walk children to find it.
    """
    for child in children(parent_node):
        if child.kind() in ("service_name", "rpc_name", "message_name",
                            "enum_name", "identifier"):
            for gc in children(child):
                if gc.kind() == "identifier":
                    return node_text(gc, source)
            # Direct identifier child
            if child.kind() == "identifier":
                return node_text(child, source)
    return None


class ProtoExtractor(BaseExtractor):
    extensions = [".proto"]
    tree_sitter_languages = ["proto"]
    language_name = "proto"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        def walk(node, parent_service_id: str | None = None):
            kind = node.kind()

            if kind == "service":
                svc_name = _find_identifier(node, source)
                if svc_name:
                    svc_id = ctx.add_node("service", svc_name, node)
                    for child in children(node):
                        walk(child, parent_service_id=svc_id)
                    return

            elif kind == "rpc" and parent_service_id:
                rpc_name = _find_identifier(node, source)
                if rpc_name:
                    rpc_id = ctx.add_node("method", rpc_name, node)
                    ctx.add_edge(parent_service_id, rpc_id, "contains",
                                 node.start_position().row + 1)
                    ctx.add_edge(parent_service_id, rpc_id, "grpc_service",
                                 node.start_position().row + 1,
                                 target_text=rpc_name)

            elif kind == "message":
                msg_name = _find_identifier(node, source)
                if msg_name:
                    ctx.add_node("struct", msg_name, node)

            elif kind == "enum":
                enum_name = _find_identifier(node, source)
                if enum_name:
                    ctx.add_node("enum", enum_name, node)

            for child in children(node):
                walk(child, parent_service_id=parent_service_id)

        walk(tree.root_node())
