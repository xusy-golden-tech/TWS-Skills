"""SQL extractor — standalone (does not inherit BaseExtractor, per P6 freeze).

Produces:
- CONTAINS edges for: CREATE TABLE, CREATE INDEX, CREATE VIEW
- REFERENCES edges for: SELECT ... FROM/JOIN, INSERT INTO, UPDATE, DELETE FROM, FOREIGN KEY refs

Usage:
    from tree_sitter_language_pack import get_parser
    parser = get_parser("sql")
    tree = parser.parse(source)
    edges = extract(source.encode(), tree, "schema.sql")
"""

from __future__ import annotations

from tws_graph.indexer.base import hash_id, BaseExtractor, make_structural_node


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte():node.end_byte()].decode("utf-8")


def _named_children(node):
    for i in range(node.named_child_count()):
        yield node.named_child(i)


def _find_named_child(node, kind: str):
    for child in _named_children(node):
        if child.kind() == kind:
            return child
    return None


def _find_all_named_children(node, kind: str) -> list:
    return [c for c in _named_children(node) if c.kind() == kind]


def _find_first_descendant(node, *kinds: str):
    """Depth-first search for a named child matching any of *kinds*."""
    for child in _named_children(node):
        if child.kind() in kinds:
            return child
    for child in _named_children(node):
        result = _find_first_descendant(child, *kinds)
        if result:
            return result
    return None


def _find_all_descendants_matching(node, kind: str) -> list:
    """Depth-first search for ALL named children matching *kind*."""
    results = []
    for child in _named_children(node):
        if child.kind() == kind:
            results.append(child)
    for child in _named_children(node):
        results.extend(_find_all_descendants_matching(child, kind))
    return results


def _make_edge(source_id: str, target_text: str, kind: str, file_path: str, line: int) -> dict:
    return {
        "source": source_id,
        "target": "",
        "kind": kind,
        "target_text": target_text,
        "source_loc": f"{file_path}:{line}",
        "provenance": "tree-sitter",
    }


def _get_identifier_text(obj_ref_node, source: bytes) -> str | None:
    """Get identifier text from an object_reference or relation node."""
    if obj_ref_node.kind() == "identifier":
        return _node_text(obj_ref_node, source)
    if obj_ref_node.kind() == "object_reference":
        ident = _find_named_child(obj_ref_node, "identifier")
        if ident:
            return _node_text(ident, source)
    if obj_ref_node.kind() == "relation":
        obj_ref = _find_named_child(obj_ref_node, "object_reference")
        if obj_ref:
            return _get_identifier_text(obj_ref, source)
    return None


def _extract_from_join_tables(statement_node, source: bytes) -> list[str]:
    """Extract all table names from FROM and JOIN clauses within a statement.

    In the SQL tree:
    - FROM is a sibling of SELECT/DELETE under statement
    - JOIN may be a child of FROM or a sibling at the statement level
    """
    tables = []

    def _extract_from_node(node):
        """Extract tables from a FROM or JOIN node (including nested JOINs)."""
        # Main table in FROM/JOIN
        relation = _find_named_child(node, "relation")
        if relation:
            name = _get_identifier_text(relation, source)
            if name:
                tables.append(name)
        # Also check for direct object_reference (some SQL dialects)
        obj_ref = _find_named_child(node, "object_reference")
        if obj_ref and not relation:
            name = _get_identifier_text(obj_ref, source)
            if name:
                tables.append(name)
        # Check for nested JOIN children inside FROM
        for child in _named_children(node):
            if child.kind() == "join":
                _extract_from_node(child)

    for child in _named_children(statement_node):
        if child.kind() == "from":
            _extract_from_node(child)
        elif child.kind() == "join":
            _extract_from_node(child)
    return tables


def extract(source: bytes, tree, file_path: str) -> tuple[list[dict], list[dict]]:
    """Extract CONTAINS and REFERENCES nodes and edges from a SQL CST."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def _add_node(source_id: str, name: str, kind: str, line: int):
        if source_id not in seen:
            seen.add(source_id)
            nodes.append(make_structural_node(source_id, name, kind, file_path, line, "sql"))

    root = tree.root_node()

    # Ensure the shared __query__ node exists (used by SELECT/INSERT/UPDATE/DELETE)
    _query_sid = hash_id(f"{file_path}::__query__", file_path)
    _add_node(_query_sid, "__query__", "sql_query", 1)

    def walk(node, parent_statement=None):
        for child in _named_children(node):
            kind = child.kind()
            line = child.start_position().row + 1

            # Handle statement-level constructs (where SELECT/DELETE + FROM/JOIN are siblings)
            if kind == "statement":
                _handle_statement(child)
                # Also recurse in case of nested queries
                walk(child, parent_statement=child)
                continue

            # CREATE TABLE: table_name → CONTAINS edge
            if kind == "create_table":
                obj_ref = _find_named_child(child, "object_reference")
                if obj_ref:
                    table_name = _get_identifier_text(obj_ref, source)
                    if table_name:
                        sid = hash_id(f"{file_path}::{table_name}", file_path)
                        _add_node(sid, table_name, "sql_table", line)
                        edges.append(_make_edge(sid, table_name, "contains", file_path, line))

                        # FOREIGN KEY references
                        constraints = _find_all_descendants_matching(child, "constraint")
                        for constraint in constraints:
                            ref_kw = _find_named_child(constraint, "keyword_references")
                            if ref_kw:
                                obj_refs = _find_all_named_children(constraint, "object_reference")
                                if obj_refs:
                                    ref_table = _get_identifier_text(obj_refs[-1], source)
                                    if ref_table:
                                        edges.append(_make_edge(
                                            sid, ref_table, "references",
                                            file_path, constraint.start_position().row + 1,
                                        ))

            # CREATE INDEX
            elif kind == "create_index":
                idx_name_node = _find_named_child(child, "identifier")
                idx_name = _node_text(idx_name_node, source) if idx_name_node else "index"
                sid = hash_id(f"{file_path}::{idx_name}", file_path)
                _add_node(sid, idx_name, "sql_index", line)
                edges.append(_make_edge(sid, idx_name, "contains", file_path, line))
                obj_ref = _find_named_child(child, "object_reference")
                if obj_ref:
                    tbl_name = _get_identifier_text(obj_ref, source)
                    if tbl_name:
                        edges.append(_make_edge(sid, tbl_name, "references", file_path, line))

            # CREATE VIEW
            elif kind == "create_view":
                obj_ref = _find_named_child(child, "object_reference")
                if obj_ref:
                    view_name = _get_identifier_text(obj_ref, source)
                    if view_name:
                        sid = hash_id(f"{file_path}::{view_name}", file_path)
                        _add_node(sid, view_name, "sql_view", line)
                        edges.append(_make_edge(sid, view_name, "contains", file_path, line))
                        select = _find_first_descendant(child, "select")
                        if select:
                            # For view, the FROM is a child of select inside the view
                            tables = _extract_from_join_tables(child, source)
                            if not tables:
                                tables = _extract_from_join_tables(
                                    _find_first_descendant(child, "statement") or child,
                                    source,
                                )
                            for tbl in tables:
                                edges.append(_make_edge(
                                    sid, tbl, "references",
                                    file_path, select.start_position().row + 1,
                                ))

            # Walk children
            walk(child)

    def _handle_statement(stmt_node):
        """Process a statement node where SELECT/DELETE and FROM/JOIN are siblings."""
        line = stmt_node.start_position().row + 1

        # Collect all children of the statement
        select_node = None
        insert_node = None
        update_node = None
        delete_node = None

        for child in _named_children(stmt_node):
            if child.kind() == "select":
                select_node = child
            elif child.kind() == "insert":
                insert_node = child
            elif child.kind() == "update":
                update_node = child
            elif child.kind() == "delete":
                delete_node = child

        # Handle SELECT
        if select_node:
            tables = _extract_from_join_tables(stmt_node, source)
            if tables:
                for tbl in tables:
                    edges.append(_make_edge(
                        _query_sid, tbl, "references",
                        file_path, select_node.start_position().row + 1,
                    ))

        # Handle INSERT
        if insert_node:
            obj_ref = _find_named_child(insert_node, "object_reference")
            if obj_ref:
                table_name = _get_identifier_text(obj_ref, source)
                if table_name:
                    edges.append(_make_edge(
                        _query_sid, table_name, "references",
                        file_path, insert_node.start_position().row + 1,
                    ))

        # Handle UPDATE
        if update_node:
            relation = _find_named_child(update_node, "relation")
            if relation:
                table_name = _get_identifier_text(relation, source)
                if table_name:
                    edges.append(_make_edge(
                        _query_sid, table_name, "references",
                        file_path, update_node.start_position().row + 1,
                    ))

        # Handle DELETE
        if delete_node:
            tables = _extract_from_join_tables(stmt_node, source)
            if tables:
                for tbl in tables:
                    edges.append(_make_edge(
                        _query_sid, tbl, "references",
                        file_path, delete_node.start_position().row + 1,
                    ))

    walk(root)
    return nodes, edges


class SqlExtractor(BaseExtractor):
    extensions = [".sql"]
    tree_sitter_languages = ["sql"]
    language_name = "sql"

    def extract(self, source, tree, ctx) -> None:
        result_nodes, result_edges = extract(source, tree, ctx.file_path)
        ctx.result.nodes.extend(result_nodes)
        ctx.result.edges.extend(result_edges)
