"""P38 v5.5.0 — Graph Query Language (GQL).

Human-readable graph queries. No SQL required.

Usage::

    from tws_graph.gql import parse_gql, execute_gql
    result = execute_gql(queries, "FIND function WHERE name MATCHES 'auth'")

CLI::

    tws-graph query "FIND class WHERE file_path MATCHES 'src/auth/'"
"""

from __future__ import annotations
import re


# ---------------------------------------------------------------------------
# P38a: Parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""(?P<string>'[^']*')|(?P<word>[A-Za-z0-9_.*+/-]+)|(?P<op>[=~])|(?P<other>\S)"""
)


def _tokenize(query: str) -> list[dict]:
    """Tokenize a GQL query string into tokens."""
    tokens = []
    for m in _TOKEN_RE.finditer(query):
        if m.group("string"):
            val = m.group("string")
            tokens.append({"type": "string", "value": val[1:-1]})
        elif m.group("word"):
            tokens.append({"type": "word", "value": m.group("word")})
        elif m.group("op"):
            tokens.append({"type": "op", "value": m.group("op")})
        elif m.group("other"):
            tokens.append({"type": "other", "value": m.group("other")})
    return tokens


def parse_gql(query: str) -> dict:
    """Parse a GQL query string into an AST dict.

    Returns:
        {
            "type": "find" | "impact",
            "kind": str | "*",
            "conditions": [{"field": str, "op": str, "value": str}] | None,
            "return_fields": [str] | None,
            "limit": int | None,
            "symbol": str (impact only),
        }
    """
    tokens = _tokenize(query)
    if not tokens:
        raise ValueError("Empty query")

    # Normalize keywords to uppercase for matching
    words = [t["value"].upper() if t["type"] == "word" else t["value"] for t in tokens]
    raw_words = [t["value"] for t in tokens]

    ast: dict = {
        "type": None,
        "kind": None,
        "conditions": None,
        "return_fields": None,
        "limit": None,
        "symbol": None,
    }

    idx = 0
    length = len(tokens)

    if idx >= length:
        raise ValueError("Empty query")

    # Parse command type
    cmd = words[idx].upper()
    if cmd == "FIND":
        ast["type"] = "find"
        idx += 1
        if idx >= length:
            raise ValueError("FIND requires a kind (e.g., FIND function)")
        kind = words[idx].upper() if tokens[idx]["type"] == "word" and words[idx] == "*" else raw_words[idx]
        ast["kind"] = kind if kind == "*" else kind.lower()
        idx += 1
    elif cmd == "IMPACT":
        ast["type"] = "impact"
        idx += 1
        if idx + 1 >= length or words[idx] != "OF":
            raise ValueError("IMPACT requires OF (e.g., IMPACT OF my_func)")
        idx += 1  # skip OF
        ast["symbol"] = raw_words[idx]
        idx += 1
        return ast  # IMPACT has no further clauses currently
    else:
        raise ValueError(f"Invalid query type: {cmd}")

    # Parse optional clauses: WHERE, RETURN, LIMIT
    while idx < length:
        w = words[idx]
        if w == "WHERE":
            idx += 1
            conditions = []
            while idx < length:
                field = raw_words[idx]
                idx += 1
                if idx >= length:
                    raise ValueError(f"Expected operator after {field}")
                op_token = tokens[idx]
                if op_token["type"] == "op":
                    op_text = op_token["value"]
                    if op_text == "~":
                        op_text = "MATCHES"
                    elif op_text == "=":
                        op_text = "="
                    idx += 1
                elif op_token["type"] == "word" and op_token["value"].upper() == "MATCHES":
                    op_text = "MATCHES"
                    idx += 1
                else:
                    raise ValueError(f"Expected operator after {field}, got {op_token}")
                if idx >= length:
                    raise ValueError(f"Expected value after {op_text}")
                value_token = tokens[idx]
                if value_token["type"] == "string":
                    value = value_token["value"]
                elif value_token["type"] == "word":
                    value = value_token["value"]
                else:
                    value = value_token["value"]
                idx += 1
                conditions.append({"field": field, "op": op_text, "value": value})
                # Check for AND
                if idx < length and words[idx] == "AND":
                    idx += 1
                    continue
                else:
                    break
            ast["conditions"] = conditions
        elif w == "RETURN":
            idx += 1
            fields = []
            while idx < length:
                fields.append(raw_words[idx])
                idx += 1
                if idx < length and tokens[idx]["value"] == ",":
                    idx += 1
                    continue
                else:
                    break
            ast["return_fields"] = fields
        elif w == "LIMIT":
            idx += 1
            if idx >= length:
                raise ValueError("LIMIT requires a number")
            try:
                ast["limit"] = int(raw_words[idx])
            except ValueError:
                raise ValueError(f"Invalid LIMIT value: {raw_words[idx]}")
            idx += 1
        else:
            raise ValueError(f"Unexpected token: {raw_words[idx]}")

    return ast


# ---------------------------------------------------------------------------
# P38b: Executor
# ---------------------------------------------------------------------------

def _build_sql(ast: dict) -> tuple[str, list]:
    """Build SQL query from GQL AST. Returns (sql, params)."""
    if ast["type"] == "find":
        select_cols = "id, name, qualified_name, file_path, kind, language, start_line, end_line"
        if ast.get("return_fields"):
            select_cols = ", ".join(ast["return_fields"])

        sql = f"SELECT {select_cols} FROM nodes WHERE 1=1"
        params: list = []

        kind = ast.get("kind")
        if kind and kind != "*":
            sql += " AND kind = ?"
            params.append(kind)

        conditions = ast.get("conditions") or []
        for cond in conditions:
            field = cond["field"]
            op = cond["op"]
            value = cond["value"]
            if op in ("MATCHES", "~"):
                sql += f" AND {field} LIKE ?"
                params.append(f"%{value}%")
            elif op == "=":
                sql += f" AND {field} = ?"
                params.append(value)

        if ast.get("limit"):
            sql += f" LIMIT {ast['limit']}"

        return sql, params

    elif ast["type"] == "impact":
        symbol = ast["symbol"]
        sql = """
            SELECT DISTINCT ns.id, ns.name, ns.qualified_name, ns.file_path, ns.kind
            FROM edges e
            JOIN nodes ns ON e.source = ns.id
            JOIN nodes nt ON e.target = nt.id
            WHERE e.kind = 'calls'
              AND nt.qualified_name LIKE ?
        """
        params = [f"%{symbol}%"]
        return sql, params

    raise ValueError(f"Unknown query type: {ast['type']}")


def execute_gql(queries, query: str) -> list[dict]:
    """Parse and execute a GQL query against the graph.

    Args:
        queries: QueryBuilder instance.
        query: GQL query string.

    Returns:
        List of result dicts.
    """
    ast = parse_gql(query)
    sql, params = _build_sql(ast)
    rows = queries._exec(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows] if rows else []
