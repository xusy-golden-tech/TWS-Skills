"""Dockerfile extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- FROM image:tag → IMPORTS edges
- COPY/ADD src dst → IMPORTS edges
- RUN command → CALLS edges
- ENV key=value → ENV_ACCESSES edges
- EXPOSE port → CONTAINS edges
- VOLUME path → CONTAINS edges
- CMD/ENTRYPOINT → CONTAINS edges

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("dockerfile")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "Dockerfile")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _resolve_image_spec(image_spec_node, source: bytes) -> str:
    """Resolve image_spec to 'name:tag' (or just 'name' if no tag)."""
    name_part = ""
    tag_part = ""
    for child in _named_children(image_spec_node):
        if child.kind() == "image_name":
            name_part = _node_text(child, source)
        elif child.kind() == "image_tag":
            tag_part = _node_text(child, source)
    if name_part:
        return f"{name_part}{tag_part}"
    # Fallback: use full text
    return _node_text(image_spec_node, source)


def _resolve_command_text(node, source: bytes) -> str:
    """Resolve shell_command or json_string_array to command text."""
    # shell_command contains shell_fragment children
    fragments = _find_all_named_children(node, "shell_fragment")
    if fragments:
        return " ".join(_node_text(f, source) for f in fragments)
    # json_string_array
    json_strings = _find_all_named_children(node, "json_string")
    if json_strings:
        parts = []
        for s in json_strings:
            text = _node_text(s, source)
            # strip quotes
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            parts.append(text)
        return ";".join(parts)
    return _node_text(node, source).strip()


def _resolve_env_text(env_pair_node, source: bytes) -> str:
    """Resolve env_pair to 'key=value' text."""
    parts = []
    for child in _named_children(env_pair_node):
        if child.kind() == "unquoted_string":
            parts.append(_node_text(child, source))
    if len(parts) >= 2:
        return f"{parts[0]}={parts[1]}"
    return _node_text(env_pair_node, source)


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract edges from a Dockerfile CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()

    # source ID for the whole Dockerfile
    df_id = hash_id(f"{file_path}::__dockerfile__", file_path)

    def walk(node):
        for child in _named_children(node):
            kind = child.kind()
            line = child.start_position().row + 1

            if kind == "from_instruction":
                image_spec = None
                image_alias = None
                for c in _named_children(child):
                    if c.kind() == "image_spec":
                        image_spec = c
                    elif c.kind() == "image_alias":
                        image_alias = c
                if image_spec:
                    image_text = _resolve_image_spec(image_spec, source)
                    stage_id = hash_id(f"{file_path}::{image_text}", file_path)
                    edges.append(_make_edge(
                        stage_id, image_text, "imports",
                        file_path, line,
                    ))

            elif kind == "copy_instruction":
                paths = _find_all_named_children(child, "path")
                if paths:
                    src_path = _node_text(paths[0], source)
                    edges.append(_make_edge(
                        df_id, src_path, "imports",
                        file_path, line,
                    ))

            elif kind == "add_instruction":
                paths = _find_all_named_children(child, "path")
                if paths:
                    src_path = _node_text(paths[0], source)
                    edges.append(_make_edge(
                        df_id, src_path, "imports",
                        file_path, line,
                    ))

            elif kind == "run_instruction":
                # Extract command text — look for shell_command or json_string_array
                run_cmd = None
                for c in _named_children(child):
                    if c.kind() in ("shell_command", "json_string_array"):
                        run_cmd = _resolve_command_text(c, source)
                        break
                if not run_cmd:
                    # Fallback: get full text minus the RUN keyword
                    run_cmd = _node_text(child, source).replace("RUN ", "", 1).strip()
                edges.append(_make_edge(
                    df_id, run_cmd, "calls",
                    file_path, line,
                ))

            elif kind == "env_instruction":
                env_pairs = _find_all_named_children(child, "env_pair")
                for ep in env_pairs:
                    env_text = _resolve_env_text(ep, source)
                    edges.append(_make_edge(
                        df_id, env_text, "env_accesses",
                        file_path, line,
                    ))

            elif kind == "expose_instruction":
                ports = _find_all_named_children(child, "expose_port")
                for port in ports:
                    port_text = _node_text(port, source)
                    edges.append(_make_edge(
                        df_id, port_text, "contains",
                        file_path, line,
                    ))

            elif kind == "volume_instruction":
                paths = _find_all_named_children(child, "path")
                for p in paths:
                    path_text = _node_text(p, source)
                    edges.append(_make_edge(
                        df_id, path_text, "contains",
                        file_path, line,
                    ))

            elif kind == "cmd_instruction":
                cmd_text = None
                for c in _named_children(child):
                    if c.kind() in ("shell_command", "json_string_array"):
                        cmd_text = _resolve_command_text(c, source)
                        break
                if not cmd_text:
                    cmd_text = _node_text(child, source).replace("CMD ", "", 1).strip()
                edges.append(_make_edge(
                    df_id, cmd_text, "contains",
                    file_path, line,
                ))

            elif kind == "entrypoint_instruction":
                ep_text = None
                for c in _named_children(child):
                    if c.kind() in ("shell_command", "json_string_array"):
                        ep_text = _resolve_command_text(c, source)
                        break
                if not ep_text:
                    ep_text = _node_text(child, source).replace("ENTRYPOINT ", "", 1).strip()
                edges.append(_make_edge(
                    df_id, ep_text, "contains",
                    file_path, line,
                ))

            # Recurse
            walk(child)

    walk(root)
    return edges


class DockerfileExtractor(BaseExtractor):
    extensions = [".dockerfile", "Dockerfile"]
    tree_sitter_languages = ["dockerfile"]

    def extract(self, source, tree, ctx) -> None:
        result_edges = extract(source, tree, ctx.file_path)
        ctx.result.edges.extend(result_edges)
