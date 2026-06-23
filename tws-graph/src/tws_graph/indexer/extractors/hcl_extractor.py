"""HCL/Terraform extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: resource, data, module, provider, variable, output,
  terraform backend, locals, provisioner blocks
- IMPORTS edges for: required_providers
- REFERENCES edges for: resource references (var.name, module.name.output, etc.)

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("hcl")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "main.tf")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor, ExtractionContext, make_structural_node


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")


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


def _extract_string_lit(node, source: bytes) -> str:
    """Extract the template_literal text from a string_lit node.

    HCL string_lit nodes contain quoted_template_start, template_literal,
    and quoted_template_end children. We extract only the literal content.
    """
    for child in _named_children(node):
        if child.kind() == "template_literal":
            return _node_text(child, source)
    # Fallback: return full text (for heredoc or complex templates)
    return _node_text(node, source)


def _resolve_variable_expr(node, source: bytes) -> str:
    """Resolve a variable_expr with get_attr chain to a dotted reference string.

    e.g., aws_s3_bucket.my_bucket.bucket → "aws_s3_bucket.my_bucket.bucket"
          var.region → "var.region"
    """
    parts = []
    for child in _named_children(node):
        if child.kind() == "identifier":
            parts.append(_node_text(child, source))
    # get_attr siblings contain the dot-access parts
    # In tree-sitter, get_attr may be children of the parent expression, not of variable_expr
    return ".".join(parts) if parts else _node_text(node, source)


def _extract_reference_text(expr_node, source: bytes) -> str | None:
    """Extract a reference string from an expression node.

    Handles variable_expr with get_attr chain like:
      variable_expr("aws_s3_bucket") → get_attr(".my_bucket") → get_attr(".bucket")
    which becomes "aws_s3_bucket.my_bucket.bucket"
    """
    # Look for variable_expr as the base
    var_expr = None
    for child in _named_children(expr_node):
        if child.kind() == "variable_expr":
            var_expr = child
            break
    if var_expr is None:
        return None

    parts = []
    for child in _named_children(var_expr):
        if child.kind() == "identifier":
            parts.append(_node_text(child, source))

    # get_attr nodes are siblings of variable_expr within the expression
    for child in _named_children(expr_node):
        if child.kind() == "get_attr":
            for gc in _named_children(child):
                if gc.kind() == "identifier":
                    parts.append(_node_text(child, source).lstrip("."))

    # Rebuild: the get_attr children are whole ".name" text, so extract properly
    # Actually let's do this more carefully - get_attr text is like ".my_bucket"
    if var_expr:
        result = _resolve_variable_expr(var_expr, source)
        for child in _named_children(expr_node):
            if child.kind() == "get_attr":
                attr_text = _node_text(child, source).lstrip(".")
                if attr_text:
                    result += "." + attr_text
        return result

    return None


def _extract_template_literal_from_string_lit(string_lit_node, source: bytes) -> str:
    """Extract template_literal from a string_lit node."""
    for child in _named_children(string_lit_node):
        if child.kind() == "template_literal":
            return _node_text(child, source)
    return _node_text(string_lit_node, source)


def _block_identifier_text(block_node, source: bytes) -> str:
    """Get the identifier text (e.g., 'resource', 'variable') from a block."""
    for child in _named_children(block_node):
        if child.kind() == "identifier":
            return _node_text(child, source)
    return ""


def _block_string_lits(block_node, source: bytes) -> list[str]:
    """Get all string_lit template_literal texts from a block."""
    result = []
    for child in _named_children(block_node):
        if child.kind() == "string_lit":
            result.append(_extract_template_literal_from_string_lit(child, source))
    return result


def hcl_extract(source: bytes, tree, file_path: str) -> tuple[list[dict], list[dict]]:
    """Extract CONTAINS, IMPORTS, and REFERENCES nodes and edges from an HCL/Terraform CST.

    Args:
        source: Raw file bytes.
        tree: tree-sitter parse result.
        file_path: Logical file path (used in source IDs and locs).

    Returns:
        Tuple of (node dicts, edge dicts).
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def _add_node(source_id: str, name: str, kind: str, line: int):
        if source_id not in seen:
            seen.add(source_id)
            nodes.append(make_structural_node(source_id, name, kind, file_path, line, "hcl"))

    def _process_block(block_node, parent_id: str | None = None):
        """Process a single HCL block and recurse into its body."""
        ident = _block_identifier_text(block_node, source)
        string_lits = _block_string_lits(block_node, source)
        line = block_node.start_position().row + 1

        # --- Top-level blocks ---
        if ident == "resource" and len(string_lits) >= 2:
            target_text = f"resource:{string_lits[0]}/{string_lits[1]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_resource", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "data" and len(string_lits) >= 2:
            target_text = f"data:{string_lits[0]}/{string_lits[1]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_data", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "module" and len(string_lits) >= 1:
            target_text = f"module:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_module", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "provider" and len(string_lits) >= 1:
            target_text = f"provider:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_provider", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "variable" and len(string_lits) >= 1:
            target_text = f"variable:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_variable", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "output" and len(string_lits) >= 1:
            target_text = f"output:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_output", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        elif ident == "terraform":
            source_id = hash_id(f"{file_path}::terraform", file_path)
            _add_node(source_id, "terraform", "hcl_terraform", line)
            edges.append(_make_edge(source_id, "terraform", "contains", file_path, line))
            # Process body for backend and required_providers
            body = _find_body(block_node)
            if body:
                _process_terraform_body(body, source_id)

        elif ident == "locals":
            source_id = hash_id(f"{file_path}::locals", file_path)
            _add_node(source_id, "locals", "hcl_locals", line)
            edges.append(_make_edge(source_id, "locals", "contains", file_path, line))
            _process_body(block_node, source_id)

        # --- Nested blocks ---
        elif ident == "backend" and len(string_lits) >= 1:
            target_text = f"backend:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_backend", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))

        elif ident == "required_providers":
            source_id = hash_id(f"{file_path}::required_providers", file_path)
            _add_node(source_id, "required_providers", "hcl_required_providers", line)
            edges.append(_make_edge(source_id, "required_providers", "contains", file_path, line))
            # Extract provider imports from body attributes
            _process_required_providers_body(block_node, source_id)

        elif ident == "provisioner" and len(string_lits) >= 1:
            target_text = f"provisioner:{string_lits[0]}"
            source_id = hash_id(f"{file_path}::{target_text}", file_path)
            _add_node(source_id, target_text, "hcl_provisioner", line)
            edges.append(_make_edge(source_id, target_text, "contains", file_path, line))
            _process_body(block_node, source_id)

        else:
            # Unknown block type — process body anyway
            _process_body(block_node, parent_id)

    def _find_body(block_node):
        """Find the body node within a block."""
        for child in _named_children(block_node):
            if child.kind() == "body":
                return child
        return None

    def _process_body(block_node, source_id: str | None):
        """Process body contents: nested blocks and attributes with references."""
        body = _find_body(block_node)
        if body is None:
            return
        for child in _named_children(body):
            if child.kind() == "block":
                _process_block(child, source_id)
            elif child.kind() == "attribute":
                _process_attribute(child, source_id)

    def _process_terraform_body(body_node, terraform_id: str):
        """Process terraform body — handles backend and required_providers."""
        for child in _named_children(body_node):
            if child.kind() == "block":
                _process_block(child, terraform_id)

    def _process_required_providers_body(block_node, source_id: str):
        """Extract provider names as IMPORTS from required_providers."""
        body = _find_body(block_node)
        if body is None:
            return
        for child in _named_children(body):
            if child.kind() == "attribute":
                # attribute name is the provider name (e.g., "aws")
                attr_ident = None
                for ac in _named_children(child):
                    if ac.kind() == "identifier":
                        attr_ident = _node_text(ac, source)
                        break
                if attr_ident:
                    edges.append(_make_edge(
                        source_id, attr_ident, "imports",
                        file_path, child.start_position().row + 1,
                    ))

    def _process_attribute(attr_node, source_id: str | None):
        """Process an attribute node — extract references from expressions."""
        if source_id is None:
            return
        line = attr_node.start_position().row + 1

        # Extract key name for locals
        key_ident = None
        for child in _named_children(attr_node):
            if child.kind() == "identifier":
                key_ident = _node_text(child, source)
                break

        # Check for variable references in expression
        for child in _named_children(attr_node):
            if child.kind() == "expression":
                ref_text = _extract_reference_text(child, source)
                if ref_text:
                    edges.append(_make_edge(
                        source_id, ref_text, "references",
                        file_path, line,
                    ))

    # --- Main extraction ---
    root = tree.root_node()
    body = _find_body(root)
    if body:
        for child in _named_children(body):
            if child.kind() == "block":
                _process_block(child)

    return nodes, edges


class HclExtractor(BaseExtractor):
    """BaseExtractor wrapper for the standalone hcl_extract function."""

    extensions = [".hcl", ".tf", ".tfvars"]
    tree_sitter_languages = ["hcl"]
    language_name = "hcl"

    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        result_nodes, result_edges = hcl_extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result_nodes)
        ctx.result.edges.extend(result_edges)
