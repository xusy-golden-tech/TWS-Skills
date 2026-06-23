"""Kubernetes manifest detector — standalone extractor for K8s YAML resources.

Based on tree-sitter YAML parser. Identifies K8s resources by apiVersion/kind
and extracts structured edges:

- apiVersion/kind → resource type identification (CONTAINS)
- metadata.name → CONTAINS
- metadata.namespace → environment marker
- spec.containers[].image → IMPORTS
- spec.containers[].ports → CONTAINS
- spec.containers[].env → ENV_ACCESSES
- spec.volumes → CONTAINS
- ConfigMap/Secret data keys → CONTAINS
- label selector → REFERENCES
- provenance = "tree-sitter"

Edge format:
    {
        "source": hash_id(f"{file_path}::{resource_kind}/{resource_name}", file_path),
        "target": "",
        "kind": str,  # EdgeKind value
        "target_text": str,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("yaml")
    tree = parser.parse(content_str)
    edges = extract(content_str.encode("utf-8"), tree, "deployment.yaml")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id

# Well-known K8s resource kinds
_K8S_RESOURCE_KINDS = {
    "Deployment", "Service", "Pod", "ConfigMap", "Secret",
    "StatefulSet", "DaemonSet", "Job", "CronJob", "Ingress",
    "PersistentVolumeClaim", "PersistentVolume", "ServiceAccount",
    "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding",
    "Namespace", "HorizontalPodAutoscaler", "NetworkPolicy",
    "ReplicaSet", "ReplicationController",
}


def _node_text(node, source: bytes) -> str:
    """Decode source bytes spanned by a tree-sitter node."""
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    """Generator over named children only."""
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    """Create an edge dict in the standard format."""
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _find_scalar_text(node, source: bytes) -> str | None:
    """Recursively find and extract scalar text from a node."""
    kind = node.kind()
    if kind == "string_scalar":
        return _node_text(node, source)
    # Handle quoted scalars and non-string scalars
    if kind in ("double_quote_scalar", "single_quote_scalar"):
        text = _node_text(node, source)
        if text and len(text) >= 2:
            if (text[0] == '"' and text[-1] == '"') or \
               (text[0] == "'" and text[-1] == "'"):
                text = text[1:-1]
        return text
    if kind in ("integer_scalar", "float_scalar", "boolean_scalar", "null_scalar"):
        return _node_text(node, source)
    for child in _named_children(node):
        result = _find_scalar_text(child, source)
        if result is not None:
            return result
    return None


def _get_pair_key(pair_node, source: bytes) -> str | None:
    """Extract the key from a block_mapping_pair."""
    named = list(_named_children(pair_node))
    if not named:
        return None
    # First named child is the key (flow_node)
    return _find_scalar_text(named[0], source)


def _get_pair_value_node(pair_node):
    """Get the value node from a block_mapping_pair (second named child)."""
    named = list(_named_children(pair_node))
    if len(named) >= 2:
        return named[1]
    return None


def _get_scalar_value(pair_node, source: bytes) -> str | None:
    """Get scalar text value from a mapping pair's value."""
    value_node = _get_pair_value_node(pair_node)
    if value_node is None:
        return None
    if value_node.kind() == "flow_node":
        # Find scalar inside flow_node
        for child in _named_children(value_node):
            if child.kind() in ("plain_scalar", "double_quote_scalar", "single_quote_scalar",
                                 "integer_scalar", "float_scalar", "boolean_scalar", "null_scalar"):
                return _find_scalar_text(child, source)
    return None


def _get_pairs_with_key(mapping_node, key: str, source: bytes) -> list:
    """Find all block_mapping_pairs with a given key name within a mapping node."""
    results = []
    if mapping_node is None:
        return results
    for pair in _named_children(mapping_node):
        if pair.kind() != "block_mapping_pair":
            continue
        k = _get_pair_key(pair, source)
        if k == key:
            results.append(pair)
    return results


def _get_pair_by_key(mapping_node, key: str, source: bytes):
    """Find the first block_mapping_pair with a given key name."""
    pairs = _get_pairs_with_key(mapping_node, key, source)
    return pairs[0] if pairs else None


def _find_mapping(node):
    """Find the inner block_mapping from a block_node or similar."""
    if node is None:
        return None
    if node.kind() == "block_mapping":
        return node
    for child in _named_children(node):
        if child.kind() == "block_mapping":
            return child
    return None


def _find_sequence(node):
    """Find the inner block_sequence from a block_node."""
    if node is None:
        return None
    if node.kind() in ("block_sequence", "flow_sequence"):
        return node
    for child in _named_children(node):
        if child.kind() in ("block_sequence", "flow_sequence"):
            return child
    return None


def extract(source: bytes, tree, file_path: str) -> list[dict]:
    """Extract K8s resource edges from a YAML CST.

    Args:
        source: Raw file bytes (UTF-8 encoded).
        tree: tree-sitter parse result.
        file_path: Logical file path.

    Returns:
        List of edge dicts.
    """
    edges: list[dict] = []
    root = tree.root_node()
    file_id = hash_id(f"{file_path}::__kubernetes__", file_path)

    for doc_node in _named_children(root):
        if doc_node.kind() != "document":
            continue

        # Find the top-level block_mapping
        top_mapping = None
        for bn in _named_children(doc_node):
            if bn.kind() == "block_node":
                top_mapping = _find_mapping(bn)
                break

        if top_mapping is None:
            continue

        # Check apiVersion and kind to see if this is a K8s resource
        api_pair = _get_pair_by_key(top_mapping, "apiVersion", source)
        kind_pair = _get_pair_by_key(top_mapping, "kind", source)

        if api_pair is None or kind_pair is None:
            continue

        api_version = _get_scalar_value(api_pair, source)
        kind_value = _get_scalar_value(kind_pair, source)

        if not api_version or not kind_value:
            continue

        # Only process well-known K8s resource kinds
        if kind_value not in _K8S_RESOURCE_KINDS:
            continue

        # Get resource name from metadata.name
        metadata_pair = _get_pair_by_key(top_mapping, "metadata", source)
        resource_name = "unknown"
        namespace = ""
        if metadata_pair is not None:
            meta_value = _get_pair_value_node(metadata_pair)
            if meta_value is not None:
                meta_mapping = _find_mapping(meta_value)
                if meta_mapping is not None:
                    name_pair = _get_pair_by_key(meta_mapping, "name", source)
                    if name_pair is not None:
                        resource_name = _get_scalar_value(name_pair, source) or "unknown"
                    ns_pair = _get_pair_by_key(meta_mapping, "namespace", source)
                    if ns_pair is not None:
                        namespace = _get_scalar_value(ns_pair, source) or ""

        # Source ID for this resource
        resource_id = hash_id(f"{file_path}::{kind_value}/{resource_name}", file_path)

        # -- apiVersion / kind → CONTAINS edges --
        if api_pair:
            edges.append(_make_edge(
                resource_id, f"apiVersion={api_version}", "contains",
                file_path, api_pair.start_position().row + 1,
            ))
        if kind_pair:
            edges.append(_make_edge(
                resource_id, f"kind={kind_value}", "contains",
                file_path, kind_pair.start_position().row + 1,
            ))

        # -- metadata.name → CONTAINS edge --
        if resource_name != "unknown":
            edges.append(_make_edge(
                resource_id, f"name={resource_name}", "contains",
                file_path, metadata_pair.start_position().row + 1 if metadata_pair else 1,
            ))

        # -- metadata.namespace → environment marker (CONTAINS edge) --
        if namespace:
            edges.append(_make_edge(
                resource_id, f"namespace={namespace}", "contains",
                file_path, ns_pair.start_position().row + 1,
            ))

        # -- metadata.labels → CONTAINS edges (label selectors can become REFERENCES) --
        if meta_mapping is not None:
            labels_pair = _get_pair_by_key(meta_mapping, "labels", source)
            if labels_pair is not None:
                labels_val = _get_pair_value_node(labels_pair)
                if labels_val is not None:
                    labels_map = _find_mapping(labels_val)
                    if labels_map is not None:
                        for label_pair in _named_children(labels_map):
                            if label_pair.kind() != "block_mapping_pair":
                                continue
                            label_key = _get_pair_key(label_pair, source)
                            label_value = _get_scalar_value(label_pair, source)
                            if label_key and label_value:
                                edges.append(_make_edge(
                                    resource_id, f"label:{label_key}={label_value}",
                                    "contains", file_path,
                                    label_pair.start_position().row + 1,
                                ))

        # -- spec.selector → REFERENCES edges --
        spec_pair = _get_pair_by_key(top_mapping, "spec", source)
        if spec_pair is not None:
            spec_val = _get_pair_value_node(spec_pair)
            if spec_val is not None:
                spec_map = _find_mapping(spec_val)
                if spec_map is not None:
                    selector_pair = _get_pair_by_key(spec_map, "selector", source)
                    if selector_pair is not None:
                        selector_val = _get_pair_value_node(selector_pair)
                        if selector_val is not None:
                            sel_map = _find_mapping(selector_val)
                            if sel_map is not None:
                                # matchLabels style
                                ml_pair = _get_pair_by_key(sel_map, "matchLabels", source)
                                if ml_pair is not None:
                                    ml_val = _get_pair_value_node(ml_pair)
                                    if ml_val is not None:
                                        ml_map = _find_mapping(ml_val)
                                        if ml_map is not None:
                                            for sel_pair in _named_children(ml_map):
                                                if sel_pair.kind() != "block_mapping_pair":
                                                    continue
                                                sk = _get_pair_key(sel_pair, source)
                                                sv = _get_scalar_value(sel_pair, source)
                                                if sk and sv:
                                                    edges.append(_make_edge(
                                                        resource_id, f"selector:{sk}={sv}",
                                                        "references", file_path,
                                                        sel_pair.start_position().row + 1,
                                                    ))
                                # Direct selector labels
                                for sel_pair in _named_children(sel_map):
                                    if sel_pair.kind() != "block_mapping_pair":
                                        continue
                                    sk = _get_pair_key(sel_pair, source)
                                    sv = _get_scalar_value(sel_pair, source)
                                    if sk and sv:
                                        edges.append(_make_edge(
                                            resource_id, f"selector:{sk}={sv}",
                                            "references", file_path,
                                            sel_pair.start_position().row + 1,
                                        ))

                    # Determine the effective spec for containers/volumes
                    # In Deployments/StatefulSets/DaemonSets, containers are under
                    # spec.template.spec; in Pods, they are under spec directly.
                    effective_spec = spec_map

                    # Check for spec.template.spec nesting
                    template_pair = _get_pair_by_key(spec_map, "template", source)
                    if template_pair is not None:
                        template_val = _get_pair_value_node(template_pair)
                        if template_val is not None:
                            template_map = _find_mapping(template_val)
                            if template_map is not None:
                                template_spec_pair = _get_pair_by_key(template_map, "spec", source)
                                if template_spec_pair is not None:
                                    ts_val = _get_pair_value_node(template_spec_pair)
                                    if ts_val is not None:
                                        ts_map = _find_mapping(ts_val)
                                        if ts_map is not None:
                                            effective_spec = ts_map

                    # -- containers[].* --
                    containers_pair = _get_pair_by_key(effective_spec, "containers", source)
                    if containers_pair is not None:
                        cont_val = _get_pair_value_node(containers_pair)
                        if cont_val is not None:
                            cont_seq = _find_sequence(cont_val)
                            if cont_seq is not None:
                                _extract_containers(cont_seq, resource_id, file_path, source, edges)

                    # -- volumes (check both template spec and top-level spec) --
                    volumes_pair = _get_pair_by_key(effective_spec, "volumes", source)
                    if volumes_pair is None:
                        volumes_pair = _get_pair_by_key(spec_map, "volumes", source)
                    if volumes_pair is not None:
                        vol_val = _get_pair_value_node(volumes_pair)
                        if vol_val is not None:
                            vol_seq = _find_sequence(vol_val)
                            if vol_seq is not None:
                                _extract_volumes(vol_seq, resource_id, file_path, source, edges)

        # -- ConfigMap / Secret data keys --
        if kind_value in ("ConfigMap", "Secret"):
            data_pair = _get_pair_by_key(top_mapping, "data", source)
            if data_pair is not None:
                data_val = _get_pair_value_node(data_pair)
                if data_val is not None:
                    data_map = _find_mapping(data_val)
                    if data_map is not None:
                        for d_pair in _named_children(data_map):
                            if d_pair.kind() != "block_mapping_pair":
                                continue
                            dk = _get_pair_key(d_pair, source)
                            if dk:
                                edges.append(_make_edge(
                                    resource_id, f"data:{dk}", "contains",
                                    file_path, d_pair.start_position().row + 1,
                                ))

    return edges


def _extract_containers(containers_seq, resource_id: str, file_path: str, source: bytes, edges: list):
    """Extract edges from spec.containers."""
    for item in _named_children(containers_seq):
        if item.kind() != "block_sequence_item":
            continue

        named = list(_named_children(item))
        if not named:
            continue

        first_val = named[0]
        container_map = None
        if first_val.kind() == "flow_node":
            for child in _named_children(first_val):
                if child.kind() == "block_mapping":
                    container_map = child
                    break
        elif first_val.kind() == "block_node":
            container_map = _find_mapping(first_val)

        if container_map is None:
            continue

        # Container name
        cont_name = None
        name_pair = _get_pair_by_key(container_map, "name", source)
        if name_pair is not None:
            cont_name = _get_scalar_value(name_pair, source)

        cont_id = resource_id
        if cont_name:
            edges.append(_make_edge(
                cont_id, f"container:{cont_name}", "contains",
                file_path, name_pair.start_position().row + 1,
            ))

        # Container image → IMPORTS edge
        image_pair = _get_pair_by_key(container_map, "image", source)
        if image_pair is not None:
            image_text = _get_scalar_value(image_pair, source)
            if image_text:
                edges.append(_make_edge(
                    cont_id, image_text, "imports",
                    file_path, image_pair.start_position().row + 1,
                ))

        # Container ports → CONTAINS edges
        ports_pair = _get_pair_by_key(container_map, "ports", source)
        if ports_pair is not None:
            ports_val = _get_pair_value_node(ports_pair)
            if ports_val is not None:
                ports_seq = _find_sequence(ports_val)
                if ports_seq is not None:
                    for port_item in _named_children(ports_seq):
                        if port_item.kind() != "block_sequence_item":
                            continue
                        _extract_port_item(port_item, cont_id, file_path, source, edges)

        # Container env → ENV_ACCESSES edges
        env_pair = _get_pair_by_key(container_map, "env", source)
        if env_pair is not None:
            env_val = _get_pair_value_node(env_pair)
            if env_val is not None:
                env_seq = _find_sequence(env_val)
                if env_seq is not None:
                    for env_item in _named_children(env_seq):
                        if env_item.kind() != "block_sequence_item":
                            continue
                        _extract_env_item(env_item, cont_id, file_path, source, edges)


def _extract_port_item(port_item, cont_id: str, file_path: str, source: bytes, edges: list):
    """Extract edges from a container port definition."""
    named = list(_named_children(port_item))
    if not named:
        return
    first_val = named[0]
    port_map = None
    if first_val.kind() == "flow_node":
        for child in _named_children(first_val):
            if child.kind() == "block_mapping":
                port_map = child
                break
    elif first_val.kind() == "block_node":
        port_map = _find_mapping(first_val)

    if port_map is None:
        return

    line = port_item.start_position().row + 1

    # containerPort
    cp = _get_pair_by_key(port_map, "containerPort", source)
    if cp is not None:
        cp_val = _get_scalar_value(cp, source)
        if cp_val:
            edges.append(_make_edge(cont_id, f"port:{cp_val}", "contains", file_path, line))

    # protocol
    proto = _get_pair_by_key(port_map, "protocol", source)
    if proto is not None:
        proto_val = _get_scalar_value(proto, source)
        if proto_val:
            edges.append(_make_edge(cont_id, f"port:{cp_val}/{proto_val}" if cp_val else f"protocol:{proto_val}",
                                    "contains", file_path, line))


def _extract_env_item(env_item, cont_id: str, file_path: str, source: bytes, edges: list):
    """Extract edges from a container env definition."""
    named = list(_named_children(env_item))
    if not named:
        return
    first_val = named[0]
    env_map = None
    if first_val.kind() == "flow_node":
        for child in _named_children(first_val):
            if child.kind() == "block_mapping":
                env_map = child
                break
    elif first_val.kind() == "block_node":
        env_map = _find_mapping(first_val)

    if env_map is None:
        return

    line = env_item.start_position().row + 1

    name_pair = _get_pair_by_key(env_map, "name", source)
    if name_pair is None:
        return
    env_name = _get_scalar_value(name_pair, source)
    if not env_name:
        return

    # Check for value (direct value)
    value_pair = _get_pair_by_key(env_map, "value", source)
    if value_pair is not None:
        env_value = _get_scalar_value(value_pair, source) or ""
        edges.append(_make_edge(
            cont_id, f"env:{env_name}={env_value}", "env_accesses",
            file_path, line,
        ))
    else:
        # Check for valueFrom (reference to ConfigMap/Secret)
        vf_pair = _get_pair_by_key(env_map, "valueFrom", source)
        if vf_pair is not None:
            edges.append(_make_edge(
                cont_id, f"env:{env_name}=valueFrom", "env_accesses",
                file_path, line,
            ))


def _extract_volumes(volumes_seq, resource_id: str, file_path: str, source: bytes, edges: list):
    """Extract edges from spec.volumes."""
    for item in _named_children(volumes_seq):
        if item.kind() != "block_sequence_item":
            continue

        named = list(_named_children(item))
        if not named:
            continue

        first_val = named[0]
        vol_map = None
        if first_val.kind() == "flow_node":
            for child in _named_children(first_val):
                if child.kind() == "block_mapping":
                    vol_map = child
                    break
        elif first_val.kind() == "block_node":
            vol_map = _find_mapping(first_val)

        if vol_map is None:
            continue

        line = item.start_position().row + 1

        # Volume name
        name_pair = _get_pair_by_key(vol_map, "name", source)
        if name_pair is not None:
            vol_name = _get_scalar_value(name_pair, source)
            if vol_name:
                edges.append(_make_edge(
                    resource_id, f"volume:{vol_name}", "contains",
                    file_path, line,
                ))

                # Check for configMap, secret, etc. references
                for kind_key in ("configMap", "secret", "persistentVolumeClaim"):
                    ref_pair = _get_pair_by_key(vol_map, kind_key, source)
                    if ref_pair is not None:
                        ref_val = _get_pair_value_node(ref_pair)
                        if ref_val is not None:
                            ref_map = _find_mapping(ref_val)
                            if ref_map is not None:
                                # Volume refs can have "name" (configMap/pvc) or "secretName" (secret)
                                ref_name_pair = _get_pair_by_key(ref_map, "name", source)
                                if ref_name_pair is None:
                                    ref_name_pair = _get_pair_by_key(ref_map, "secretName", source)
                                if ref_name_pair is not None:
                                    ref_name = _get_scalar_value(ref_name_pair, source)
                                    if ref_name:
                                        edges.append(_make_edge(
                                            resource_id,
                                            f"volume:{vol_name}/{kind_key}={ref_name}",
                                            "references", file_path,
                                            ref_name_pair.start_position().row + 1,
                                        ))
