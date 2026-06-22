"""AST node definitions for the Cypher query engine.

All nodes are frozen dataclasses inheriting from the Node(ABC) base class.
The Expression union type covers all 10 expression variants supported in P2.

P2 scope: MATCH / WHERE / RETURN / ORDER BY / SKIP / LIMIT
Out of scope for P2: UNION / CASE / aggregations / subqueries
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Span:
    """Source code location."""
    start_line: int
    start_col: int
    end_line: int
    end_col: int


class Node(ABC):
    """AST base class. All nodes are immutable (frozen dataclass)."""
    span: Span


class Direction(Enum):
    """Relationship direction."""
    LEFT = "left"    # <-
    RIGHT = "right"  # ->
    BOTH = "both"    # -


# ---------------------------------------------------------------------------
# Top-level structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Statement(Node):
    """Top-level statement. UNION queries chain multiple Statements."""
    query: Query
    union: Optional[UnionClause] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class UnionClause(Node):
    """UNION ALL | UNION"""
    all: bool               # True = UNION ALL, False = UNION (dedup)
    right: Statement        # right-hand query
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class Query(Node):
    """Single query: MATCH ... WHERE ... RETURN ..."""
    return_clause: ReturnClause
    match: Optional[MatchClause] = None
    optional_matches: list[MatchClause] = field(default_factory=list)
    where: Optional[WhereClause] = None
    order_by: Optional[OrderByClause] = None
    skip: Optional[int] = None
    limit: Optional[int] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# MATCH clause
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchClause(Node):
    """MATCH or OPTIONAL MATCH clause."""
    pattern: PatternPart
    optional: bool = False
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class PatternPart(Node):
    """A pattern part: node entry point + chain of PatternElements."""
    node: NodePattern
    chain: list[PatternElement] = field(default_factory=list)
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class PatternElement(Node):
    """One link in a pattern chain: (rel?)->(node)."""
    node: NodePattern
    rel: Optional[RelPattern] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class NodePattern(Node):
    """Node pattern: (name:Label1:Label2 {propKey: propVal})"""
    name: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    properties: Optional[dict] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class RelPattern(Node):
    """Relationship pattern: -[name:TYPE1|TYPE2 *min..max {props}]->"""
    name: Optional[str] = None
    types: list[str] = field(default_factory=list)
    direction: Direction = Direction.RIGHT
    properties: Optional[dict] = None
    var_length: Optional[tuple[int, Optional[int]]] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# WHERE clause & expressions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WhereClause(Node):
    """WHERE clause."""
    expression: Expression
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class BinaryOp(Node):
    """Binary operation: a = b, x AND y, a + b"""
    op: str   # AND, OR, =, <>, <, >, <=, >=, +, -, *, /, %, ^, =~
    left: Expression
    right: Expression
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class UnaryOp(Node):
    """Unary operation: NOT x, -x, x IS NULL, x IS NOT NULL"""
    op: str   # NOT, -, IS NULL, IS NOT NULL
    operand: Expression
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class PropertyAccess(Node):
    """Property access: n.name, n.file_path"""
    obj: Expression
    key: str
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class Identifier(Node):
    """Variable identifier: RETURN n 中的 n"""
    name: str
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class Literal(Node):
    """Literal value: string, integer, float, boolean, null."""
    value: Union[int, float, str, bool, None]
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class ListLiteral(Node):
    """List literal: [1, 2, 3]"""
    elements: list[Expression] = field(default_factory=list)
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class FunctionCall(Node):
    """Built-in function call: toUpper(n.name), coalesce(x, 0)"""
    name: str
    args: list[Expression] = field(default_factory=list)
    distinct: bool = False
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class Parameter(Node):
    """Parameter placeholder: $paramName"""
    name: str
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class InExpression(Node):
    """IN expression: x IN [1, 2, 3] or x IN list_var"""
    expr: Expression
    list: Expression
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# RETURN clause
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StarExpression(Node):
    """RETURN *"""
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class ReturnItem(Node):
    """Single item in RETURN clause."""
    expression: Expression
    alias: Optional[str] = None
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class ReturnClause(Node):
    """RETURN clause."""
    items: list[ReturnItem] = field(default_factory=list)
    distinct: bool = False
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# ORDER BY clause
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderByItem(Node):
    """Single item in ORDER BY clause."""
    expression: Expression
    direction: str = "ASC"  # "ASC" or "DESC"
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


@dataclass(frozen=True)
class OrderByClause(Node):
    """ORDER BY clause."""
    items: list[OrderByItem] = field(default_factory=list)
    span: Span = field(default_factory=lambda: Span(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# Expression union type
# ---------------------------------------------------------------------------

Expression = Union[
    Identifier, Literal, ListLiteral, FunctionCall, Parameter,
    BinaryOp, UnaryOp, PropertyAccess, InExpression,
    StarExpression,
]
