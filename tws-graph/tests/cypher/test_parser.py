"""Tests for cypher/parser.py — recursive descent Parser."""

from __future__ import annotations

import pytest

from tws_graph.cypher.lexer import Lexer, TokenType
from tws_graph.cypher.parser import Parser
from tws_graph.cypher.ast import (
    Span,
    Direction,
    Statement,
    UnionClause,
    Query,
    MatchClause,
    PatternPart,
    PatternElement,
    NodePattern,
    RelPattern,
    WhereClause,
    BinaryOp,
    UnaryOp,
    PropertyAccess,
    Identifier,
    Literal,
    ListLiteral,
    FunctionCall,
    Parameter,
    InExpression,
    ReturnClause,
    ReturnItem,
    StarExpression,
    OrderByClause,
    OrderByItem,
    CaseExpression,
    UnwindClause,
    SubqueryExpression,
    WithClause,
)
from tws_graph.cypher.errors import CypherSyntaxError


# ============================================================================
# Helpers
# ============================================================================

def _parse(source: str) -> Statement:
    """Shorthand to parse Cypher source and return the Statement."""
    lexer = Lexer(source)
    parser = Parser(lexer)
    return parser.parse()


# ============================================================================
# Basic MATCH-RETURN
# ============================================================================

class TestSimpleMatchReturn:
    """MATCH (n) RETURN n — minimal queries."""

    def test_simple_match_return(self):
        stmt = _parse("MATCH (n) RETURN n")
        q = stmt.query
        assert q.match is not None
        assert q.match.optional is False
        pp = q.match.pattern
        assert pp.node.name == "n"
        assert pp.chain == []
        assert len(q.return_clause.items) == 1
        assert isinstance(q.return_clause.items[0].expression, Identifier)
        assert q.return_clause.items[0].expression.name == "n"
        assert q.return_clause.distinct is False
        assert q.where is None
        assert q.order_by is None
        assert q.skip is None
        assert q.limit is None

    def test_match_with_label(self):
        stmt = _parse("MATCH (n:Class) RETURN n")
        q = stmt.query
        assert q.match is not None
        assert q.match.pattern.node.name == "n"
        assert q.match.pattern.node.labels == ["Class"]

    def test_match_with_multiple_labels(self):
        stmt = _parse("MATCH (n:Class:Abstract) RETURN n")
        q = stmt.query
        assert q.match.pattern.node.labels == ["Class", "Abstract"]

    def test_match_anonymous_node(self):
        stmt = _parse("MATCH () RETURN 1")
        q = stmt.query
        assert q.match.pattern.node.name is None
        assert q.match.pattern.node.labels == []

    def test_match_anonymous_node_with_label(self):
        stmt = _parse("MATCH (:Function) RETURN 1")
        q = stmt.query
        assert q.match.pattern.node.name is None
        assert q.match.pattern.node.labels == ["Function"]

    def test_return_multiple_items(self):
        stmt = _parse("MATCH (n) RETURN n, n.name, n.file_path")
        q = stmt.query
        items = q.return_clause.items
        assert len(items) == 3
        assert isinstance(items[0].expression, Identifier)
        assert items[0].expression.name == "n"
        assert isinstance(items[1].expression, PropertyAccess)
        assert isinstance(items[2].expression, PropertyAccess)

    def test_return_with_alias(self):
        stmt = _parse("MATCH (n) RETURN n.name AS func_name")
        q = stmt.query
        item = q.return_clause.items[0]
        assert item.alias == "func_name"
        assert isinstance(item.expression, PropertyAccess)

    def test_match_case_insensitive(self):
        stmt = _parse("match (n) return n")
        q = stmt.query
        assert q.match is not None
        assert q.return_clause is not None

    def test_return_case_insensitive(self):
        stmt = _parse("MATCH (n) RETURN n")
        q = stmt.query
        assert q.return_clause is not None

    def test_statement_span(self):
        stmt = _parse("MATCH (n) RETURN n")
        assert stmt.span is not None
        assert stmt.query.span is not None


# ============================================================================
# WHERE clause
# ============================================================================

class TestWhereClause:
    """WHERE filtering expressions."""

    def test_simple_where_equals(self):
        stmt = _parse("MATCH (n) WHERE n.name = 'hello' RETURN n")
        q = stmt.query
        assert q.where is not None
        expr = q.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "="
        assert isinstance(expr.left, PropertyAccess)
        assert expr.left.key == "name"
        assert isinstance(expr.right, Literal)
        assert expr.right.value == "hello"

    def test_where_not_equals(self):
        stmt = _parse("MATCH (n) WHERE n.name <> 'hello' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "<>"

    def test_where_greater_than(self):
        stmt = _parse("MATCH (n) WHERE n.age > 18 RETURN n")
        expr = stmt.query.where.expression
        assert expr.op == ">"
        assert isinstance(expr.right, Literal)
        assert expr.right.value == 18

    def test_where_less_than(self):
        stmt = _parse("MATCH (n) WHERE n.age < 65 RETURN n")
        expr = stmt.query.where.expression
        assert expr.op == "<"

    def test_where_gte(self):
        stmt = _parse("MATCH (n) WHERE n.age >= 18 RETURN n")
        expr = stmt.query.where.expression
        assert expr.op == ">="

    def test_where_lte(self):
        stmt = _parse("MATCH (n) WHERE n.age <= 65 RETURN n")
        expr = stmt.query.where.expression
        assert expr.op == "<="

    def test_where_contains(self):
        stmt = _parse("MATCH (n) WHERE n.name =~ 'test' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "=~"

    def test_where_not(self):
        stmt = _parse("MATCH (n) WHERE NOT n.is_test RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, UnaryOp)
        assert expr.op == "NOT"
        assert isinstance(expr.operand, PropertyAccess)
        assert expr.operand.key == "is_test"

    def test_where_and(self):
        stmt = _parse("MATCH (n) WHERE n.age > 18 AND n.name = 'x' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "AND"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == ">"

    def test_where_or(self):
        stmt = _parse("MATCH (n) WHERE n.name = 'a' OR n.name = 'b' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "OR"

    def test_where_and_or_precedence(self):
        """AND binds tighter than OR: a OR b AND c → OR(a, AND(b,c))"""
        stmt = _parse("MATCH (n) WHERE n.x = 1 OR n.y = 2 AND n.z = 3 RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "OR"
        # Right side should be AND (AND binds tighter)
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.op == "AND"

    def test_where_is_null(self):
        stmt = _parse("MATCH (n) WHERE n.parent IS NULL RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, UnaryOp)
        assert expr.op == "IS NULL"

    def test_where_is_not_null(self):
        stmt = _parse("MATCH (n) WHERE n.parent IS NOT NULL RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, UnaryOp)
        assert expr.op == "IS NOT NULL"

    def test_where_starts_with(self):
        stmt = _parse("MATCH (n) WHERE n.name STARTS WITH 'test' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "STARTS WITH"

    def test_where_ends_with(self):
        stmt = _parse("MATCH (n) WHERE n.name ENDS WITH '.py' RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "ENDS WITH"

    def test_where_in(self):
        stmt = _parse("MATCH (n) WHERE n.kind IN ['function', 'method'] RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, InExpression)
        assert isinstance(expr.expr, PropertyAccess)
        assert expr.expr.key == "kind"
        assert isinstance(expr.list, ListLiteral)
        assert len(expr.list.elements) == 2
        assert isinstance(expr.list.elements[0], Literal)
        assert expr.list.elements[0].value == "function"
        assert isinstance(expr.list.elements[1], Literal)
        assert expr.list.elements[1].value == "method"

    def test_where_in_identifier(self):
        stmt = _parse("MATCH (n) WHERE n.kind IN $types RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, InExpression)
        assert isinstance(expr.list, Parameter)

    def test_where_complex_nesting(self):
        stmt = _parse(
            "MATCH (n) WHERE (n.a = 1 OR n.b = 2) AND NOT n.c IS NULL RETURN n"
        )
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "AND"

    def test_where_xor(self):
        stmt = _parse("MATCH (n) WHERE n.a XOR n.b RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "XOR"


# ============================================================================
# ORDER BY / SKIP / LIMIT
# ============================================================================

class TestOrderBySkipLimit:
    """ORDER BY, SKIP, LIMIT clauses."""

    def test_order_by_asc_default(self):
        stmt = _parse("MATCH (n) RETURN n ORDER BY n.name")
        q = stmt.query
        assert q.order_by is not None
        assert len(q.order_by.items) == 1
        item = q.order_by.items[0]
        assert item.direction == "ASC"
        assert isinstance(item.expression, PropertyAccess)
        assert item.expression.key == "name"

    def test_order_by_asc_explicit(self):
        stmt = _parse("MATCH (n) RETURN n ORDER BY n.name ASC")
        item = stmt.query.order_by.items[0]
        assert item.direction == "ASC"

    def test_order_by_desc(self):
        stmt = _parse("MATCH (n) RETURN n ORDER BY n.name DESC")
        item = stmt.query.order_by.items[0]
        assert item.direction == "DESC"

    def test_order_by_multiple(self):
        stmt = _parse("MATCH (n) RETURN n ORDER BY n.name ASC, n.age DESC")
        ob = stmt.query.order_by
        assert len(ob.items) == 2
        assert ob.items[0].direction == "ASC"
        assert ob.items[1].direction == "DESC"

    def test_skip(self):
        stmt = _parse("MATCH (n) RETURN n SKIP 10")
        assert stmt.query.skip == 10

    def test_limit(self):
        stmt = _parse("MATCH (n) RETURN n LIMIT 5")
        assert stmt.query.limit == 5

    def test_skip_limit(self):
        stmt = _parse("MATCH (n) RETURN n SKIP 10 LIMIT 5")
        assert stmt.query.skip == 10
        assert stmt.query.limit == 5

    def test_full_combo(self):
        stmt = _parse(
            "MATCH (n) RETURN n ORDER BY n.name ASC SKIP 10 LIMIT 5"
        )
        q = stmt.query
        assert q.order_by is not None
        assert q.skip == 10
        assert q.limit == 5


# ============================================================================
# Relationship patterns
# ============================================================================

class TestRelationships:
    """MATCH with relationship patterns."""

    def test_rel_right(self):
        stmt = _parse("MATCH (a)-[r]->(b) RETURN a, b")
        pp = stmt.query.match.pattern
        assert len(pp.chain) == 1
        pe = pp.chain[0]
        assert pe.rel is not None
        assert pe.rel.direction == Direction.RIGHT
        assert pe.rel.name == "r"
        assert pe.node.name == "b"

    def test_rel_left(self):
        stmt = _parse("MATCH (a)<-[r]-(b) RETURN a, b")
        pp = stmt.query.match.pattern
        pe = pp.chain[0]
        assert pe.rel.direction == Direction.LEFT
        assert pe.rel.name == "r"
        assert pe.node.name == "b"

    def test_rel_both(self):
        stmt = _parse("MATCH (a)-[r]-(b) RETURN a, b")
        pp = stmt.query.match.pattern
        pe = pp.chain[0]
        assert pe.rel.direction == Direction.BOTH
        assert pe.rel.name == "r"
        assert pe.node.name == "b"

    def test_rel_with_type(self):
        stmt = _parse("MATCH (a)-[r:CALLS]->(b) RETURN a, b")
        pe = stmt.query.match.pattern.chain[0]
        assert pe.rel.types == ["CALLS"]

    def test_rel_with_multiple_types(self):
        stmt = _parse("MATCH (a)-[r:CALLS|IMPORTS]->(b) RETURN a, b")
        pe = stmt.query.match.pattern.chain[0]
        assert pe.rel.types == ["CALLS", "IMPORTS"]

    def test_rel_shorthand_right(self):
        stmt = _parse("MATCH (a)-->(b) RETURN a, b")
        pp = stmt.query.match.pattern
        pe = pp.chain[0]
        assert pe.rel is not None
        assert pe.rel.direction == Direction.RIGHT
        assert pe.rel.name is None
        assert pe.rel.types == []
        assert pe.node.name == "b"

    def test_rel_shorthand_left(self):
        stmt = _parse("MATCH (a)<--(b) RETURN a, b")
        pp = stmt.query.match.pattern
        pe = pp.chain[0]
        assert pe.rel.direction == Direction.LEFT
        assert pe.rel.name is None
        assert pe.rel.types == []
        assert pe.node.name == "b"

    def test_rel_shorthand_both(self):
        """Test -- as bidirectional shorthand (no brackets)."""
        stmt = _parse("MATCH (a)--(b) RETURN a, b")
        pp = stmt.query.match.pattern
        assert len(pp.chain) == 1
        pe = pp.chain[0]
        assert pe.rel.direction == Direction.BOTH
        assert pe.rel.name is None

    def test_rel_anonymous(self):
        stmt = _parse("MATCH (a)-[:CALLS]->(b) RETURN a, b")
        pe = stmt.query.match.pattern.chain[0]
        assert pe.rel.name is None
        assert pe.rel.types == ["CALLS"]

    def test_rel_chain(self):
        stmt = _parse("MATCH (a)-[r1]->(b)-[r2]->(c) RETURN a, c")
        pp = stmt.query.match.pattern
        assert len(pp.chain) == 2
        assert pp.chain[0].rel.name == "r1"
        assert pp.chain[0].node.name == "b"
        assert pp.chain[1].rel.name == "r2"
        assert pp.chain[1].node.name == "c"

    def test_rel_right_with_properties(self):
        stmt = _parse("MATCH (a)-[r {count: 5}]->(b) RETURN a, b")
        pe = stmt.query.match.pattern.chain[0]
        assert pe.rel.properties == {"count": "5"}

    def test_rel_with_type_and_properties(self):
        stmt = _parse("MATCH (a)-[r:CALLS {count: 3, flag: true}]->(b) RETURN r")
        pe = stmt.query.match.pattern.chain[0]
        assert pe.rel.types == ["CALLS"]
        assert pe.rel.properties == {"count": "3", "flag": "true"}


# ============================================================================
# RETURN special forms
# ============================================================================

class TestReturnStar:
    """RETURN *"""

    def test_return_star(self):
        stmt = _parse("MATCH (n) RETURN *")
        items = stmt.query.return_clause.items
        assert len(items) == 1
        assert isinstance(items[0].expression, StarExpression)

    def test_return_distinct(self):
        stmt = _parse("MATCH (n) RETURN DISTINCT n")
        assert stmt.query.return_clause.distinct is True
        assert len(stmt.query.return_clause.items) == 1

    def test_return_distinct_star(self):
        stmt = _parse("MATCH (n) RETURN DISTINCT *")
        rc = stmt.query.return_clause
        assert rc.distinct is True
        assert isinstance(rc.items[0].expression, StarExpression)

    def test_return_distinct_multi(self):
        stmt = _parse("MATCH (n) RETURN DISTINCT n.name, n.age")
        rc = stmt.query.return_clause
        assert rc.distinct is True
        assert len(rc.items) == 2


# ============================================================================
# Expressions
# ============================================================================

class TestExpressions:
    """Expression parsing: literals, identifiers, arithmetic, precedence."""

    def test_identifier(self):
        stmt = _parse("MATCH (n) RETURN myVar")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, Identifier)
        assert expr.name == "myVar"

    def test_string_literal(self):
        stmt = _parse("MATCH (n) WHERE n.x = 'hello' RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value == "hello"

    def test_integer_literal(self):
        stmt = _parse("MATCH (n) WHERE n.x = 42 RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value == 42

    def test_float_literal(self):
        stmt = _parse("MATCH (n) WHERE n.x = 3.14 RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value == 3.14

    def test_boolean_true(self):
        stmt = _parse("MATCH (n) WHERE n.flag = true RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value is True

    def test_boolean_false(self):
        stmt = _parse("MATCH (n) WHERE n.flag = false RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value is False

    def test_null_literal(self):
        stmt = _parse("MATCH (n) WHERE n.x = null RETURN n")
        lit = stmt.query.where.expression.right
        assert isinstance(lit, Literal)
        assert lit.value is None

    def test_parameter(self):
        stmt = _parse("MATCH (n) WHERE n.name = $paramName RETURN n")
        right = stmt.query.where.expression.right
        assert isinstance(right, Parameter)
        assert right.name == "$paramName"

    def test_property_access_single(self):
        stmt = _parse("MATCH (n) RETURN n.name")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, PropertyAccess)
        assert isinstance(expr.obj, Identifier)
        assert expr.obj.name == "n"
        assert expr.key == "name"

    def test_property_access_chain(self):
        stmt = _parse("MATCH (n) RETURN n.addr.city")
        expr = stmt.query.return_clause.items[0].expression
        # n.addr.city → PropertyAccess(PropertyAccess(n, addr), city)
        assert isinstance(expr, PropertyAccess)
        assert expr.key == "city"
        assert isinstance(expr.obj, PropertyAccess)
        assert expr.obj.key == "addr"
        assert isinstance(expr.obj.obj, Identifier)
        assert expr.obj.obj.name == "n"

    def test_property_access_triple_chain(self):
        stmt = _parse("MATCH (n) RETURN a.b.c")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, PropertyAccess)
        assert expr.key == "c"
        assert isinstance(expr.obj, PropertyAccess)
        assert expr.obj.key == "b"
        assert isinstance(expr.obj.obj, Identifier)
        assert expr.obj.obj.name == "a"

    def test_function_call_no_args(self):
        stmt = _parse("MATCH (n) RETURN toUpper()")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, FunctionCall)
        assert expr.name == "toUpper"
        assert expr.args == []
        assert expr.distinct is False

    def test_function_call_one_arg(self):
        stmt = _parse("MATCH (n) RETURN toUpper(n.name)")
        fc = stmt.query.return_clause.items[0].expression
        assert isinstance(fc, FunctionCall)
        assert fc.name == "toUpper"
        assert len(fc.args) == 1
        assert isinstance(fc.args[0], PropertyAccess)

    def test_function_call_multiple_args(self):
        stmt = _parse("MATCH (n) RETURN coalesce(n.name, 'default')")
        fc = stmt.query.return_clause.items[0].expression
        assert fc.name == "coalesce"
        assert len(fc.args) == 2

    def test_function_call_distinct(self):
        stmt = _parse("MATCH (n) RETURN count(DISTINCT n.name)")
        fc = stmt.query.return_clause.items[0].expression
        assert fc.name == "count"
        assert fc.distinct is True
        assert len(fc.args) == 1

    def test_arithmetic_addition(self):
        stmt = _parse("MATCH (n) RETURN 1 + 2")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"

    def test_arithmetic_subtraction(self):
        stmt = _parse("MATCH (n) RETURN 5 - 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "-"

    def test_arithmetic_multiplication(self):
        stmt = _parse("MATCH (n) RETURN 2 * 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "*"

    def test_arithmetic_division(self):
        stmt = _parse("MATCH (n) RETURN 10 / 2")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "/"

    def test_arithmetic_modulo(self):
        stmt = _parse("MATCH (n) RETURN 10 % 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "%"

    def test_arithmetic_power(self):
        stmt = _parse("MATCH (n) RETURN 2 ^ 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "^"

    def test_arithmetic_precedence(self):
        """* before +: 1 + 2 * 3 → +(1, *(2, 3))"""
        stmt = _parse("MATCH (n) RETURN 1 + 2 * 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.op == "*"

    def test_unary_minus(self):
        stmt = _parse("MATCH (n) RETURN -42")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, UnaryOp)
        assert expr.op == "-"
        assert isinstance(expr.operand, Literal)
        assert expr.operand.value == 42

    def test_parenthesized_expression(self):
        stmt = _parse("MATCH (n) RETURN (1 + 2) * 3")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "*"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"

    def test_and_precedence_over_or(self):
        """AND binds tighter than OR."""
        stmt = _parse("MATCH (n) WHERE n.a = 1 AND n.b = 2 OR n.c = 3 RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, BinaryOp)
        assert expr.op == "OR"

    def test_not_precedence_over_comparison(self):
        """NOT binds tighter than comparison."""
        stmt = _parse("MATCH (n) WHERE NOT n.a = 1 RETURN n")
        expr = stmt.query.where.expression
        # NOT (n.a = 1) — NOT binds tighter
        assert isinstance(expr, BinaryOp)
        assert expr.op == "="
        assert isinstance(expr.left, UnaryOp)
        assert expr.left.op == "NOT"


# ============================================================================
# OPTIONAL MATCH
# ============================================================================

class TestOptionalMatch:
    """OPTIONAL MATCH clause."""

    def test_optional_match_standalone(self):
        stmt = _parse("OPTIONAL MATCH (n) RETURN n")
        q = stmt.query
        assert q.match is not None
        assert q.match.optional is True

    def test_optional_match_after_main_match(self):
        stmt = _parse("MATCH (a) OPTIONAL MATCH (a)-[r]->(b) RETURN a, b")
        q = stmt.query
        assert q.match is not None
        assert q.match.optional is False
        assert len(q.optional_matches) == 1
        assert q.optional_matches[0].optional is True
        assert q.optional_matches[0].pattern.node.name == "a"

    def test_multiple_optional_matches(self):
        stmt = _parse(
            "MATCH (a) OPTIONAL MATCH (a)-[r1]->(b) OPTIONAL MATCH (b)-[r2]->(c) RETURN a, b, c"
        )
        q = stmt.query
        assert len(q.optional_matches) == 2
        assert q.optional_matches[0].pattern.node.name == "a"
        assert q.optional_matches[1].pattern.node.name == "b"


# ============================================================================
# Error handling
# ============================================================================

class TestParserErrors:
    """CypherSyntaxError for malformed input."""

    def test_eof_after_match(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH")

    def test_missing_return(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n)")

    def test_missing_return_after_with(self):
        """WITH without RETURN is an error."""
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n) WITH n")

    def test_unclosed_parenthesis(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n RETURN n")

    def test_unclosed_bracket(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (a)-[r:TY RETURN a")

    def test_unknown_keyword_in_expression(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n) WHERE n.x FOO 1 RETURN n")

    def test_syntax_error_has_expected_actual(self):
        try:
            _parse("MATCH")
        except CypherSyntaxError as e:
            assert e.expected
            assert e.actual
            assert e.line
            assert e.col
        else:
            pytest.fail("Expected CypherSyntaxError")

    def test_syntax_error_message_contains_details(self):
        try:
            _parse("MATCH")
        except CypherSyntaxError as e:
            msg = str(e)
            assert "Syntax error" in msg or "syntax error" in msg

    def test_missing_where_after_match(self):
        """WHERE is optional; if present need an expression."""
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n) WHERE RETURN n")

    def test_missing_return_expression(self):
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n) RETURN")

    def test_trailing_comma_in_return(self):
        # Trailing comma is a syntax error (no expression after comma)
        with pytest.raises(CypherSyntaxError):
            _parse("MATCH (n) RETURN n,")


# ============================================================================
# Whitespace and case insensitivity
# ============================================================================

class TestWhitespaceAndCase:
    """Whitespace tolerance and case insensitivity."""

    def test_extra_whitespace(self):
        stmt = _parse("  MATCH   (  n  )   RETURN   n  ")
        assert stmt.query.match.pattern.node.name == "n"

    def test_newlines(self):
        query = "MATCH (n)\nWHERE n.x = 1\nRETURN n\nORDER BY n.x\nSKIP 10\nLIMIT 5"
        stmt = _parse(query)
        q = stmt.query
        assert q.where is not None
        assert q.order_by is not None
        assert q.skip == 10
        assert q.limit == 5

    def test_keywords_all_caps(self):
        stmt = _parse("MATCH (n) WHERE n.x = 1 RETURN n ORDER BY n.x SKIP 0 LIMIT 1")
        assert stmt.query is not None

    def test_keywords_lowercase(self):
        stmt = _parse("match (n) where n.x = 1 return n order by n.x skip 0 limit 1")
        assert stmt.query is not None

    def test_keywords_mixed_case(self):
        stmt = _parse("Match (n) Where n.x = 1 Return n Order By n.x Skip 0 Limit 1")
        assert stmt.query is not None


# ============================================================================
# Complex query integration
# ============================================================================

class TestComplexQueries:
    """Full pipeline integration tests — realistic Cypher queries."""

    def test_match_with_where_order_skip_limit(self):
        query = (
            "MATCH (n:Function)\n"
            "WHERE n.name STARTS WITH 'test_'\n"
            "  AND n.file_path =~ 'test'\n"
            "  OR n.is_exported = true\n"
            "RETURN n.name AS func_name, n.file_path AS path\n"
            "ORDER BY func_name ASC\n"
            "SKIP 0\n"
            "LIMIT 20"
        )
        stmt = _parse(query)
        q = stmt.query

        assert q.match.pattern.node.labels == ["Function"]

        # WHERE exists with complex expression
        assert q.where is not None
        assert isinstance(q.where.expression, BinaryOp)

        # RETURN with aliases
        assert len(q.return_clause.items) == 2
        assert q.return_clause.items[0].alias == "func_name"
        assert q.return_clause.items[1].alias == "path"

        # ORDER BY
        assert q.order_by is not None
        assert q.order_by.items[0].direction == "ASC"

        # SKIP/LIMIT
        assert q.skip == 0
        assert q.limit == 20

    def test_two_node_pattern_with_where(self):
        query = (
            "MATCH (caller:Function)-[:calls]->(callee:Function)\n"
            "WHERE caller.name = 'main'\n"
            "RETURN caller, callee"
        )
        stmt = _parse(query)
        q = stmt.query
        pp = q.match.pattern

        assert pp.node.name == "caller"
        assert pp.node.labels == ["Function"]
        assert len(pp.chain) == 1
        pe = pp.chain[0]
        assert pe.rel.types == ["calls"]
        assert pe.rel.direction == Direction.RIGHT
        assert pe.node.name == "callee"
        assert pe.node.labels == ["Function"]

        assert q.where is not None
        assert isinstance(q.where.expression, BinaryOp)

    def test_three_node_chain(self):
        query = (
            "MATCH (a:File)-[:contains]->(b:Class)-[:inherits]->(c:Class)\n"
            "RETURN a, b, c"
        )
        stmt = _parse(query)
        pp = stmt.query.match.pattern

        assert pp.node.name == "a"
        assert len(pp.chain) == 2
        assert pp.chain[0].rel.types == ["contains"]
        assert pp.chain[0].node.name == "b"
        assert pp.chain[1].rel.types == ["inherits"]
        assert pp.chain[1].node.name == "c"

    def test_optional_match_with_where(self):
        query = (
            "MATCH (a:Function)\n"
            "OPTIONAL MATCH (a)-[r:calls]->(b:Function)\n"
            "WHERE b.language = 'python'\n"
            "RETURN a.name, b.name"
        )
        stmt = _parse(query)
        q = stmt.query

        assert q.match.optional is False
        assert len(q.optional_matches) == 1
        assert q.optional_matches[0].optional is True
        assert q.where is not None

    def test_empty_input(self):
        with pytest.raises(CypherSyntaxError):
            _parse("")

    def test_whitespace_only(self):
        with pytest.raises(CypherSyntaxError):
            _parse("   \n\t  ")

    def test_line_comment_in_query(self):
        query = "MATCH (n) // find all\nRETURN n"
        stmt = _parse(query)
        assert stmt.query.match is not None
        assert stmt.query.return_clause is not None

    def test_block_comment_in_query(self):
        query = "MATCH (n) /* block comment */ RETURN n"
        stmt = _parse(query)
        assert stmt.query.match is not None
        assert stmt.query.return_clause is not None


# ============================================================================
# UNION
# ============================================================================

class TestUnion:
    """UNION and UNION ALL parsing."""

    def test_simple_union(self):
        stmt = _parse("MATCH (n) RETURN n UNION MATCH (m) RETURN m")
        assert stmt.union is not None
        assert stmt.union.all is False
        right = stmt.union.right
        assert right.query.match is not None
        assert right.query.match.pattern.node.name == "m"
        assert right.query.return_clause.items[0].expression.name == "m"

    def test_union_all(self):
        stmt = _parse("MATCH (n) RETURN n UNION ALL MATCH (m) RETURN m")
        assert stmt.union is not None
        assert stmt.union.all is True
        right = stmt.union.right
        assert right.query.match.pattern.node.name == "m"

    def test_union_chain(self):
        stmt = _parse("MATCH (a) RETURN a UNION MATCH (b) RETURN b UNION MATCH (c) RETURN c")
        # First level: a UNION (b UNION c)
        assert stmt.union is not None
        assert stmt.union.right.query.match.pattern.node.name == "b"
        # Second level: b UNION c
        right = stmt.union.right
        assert right.union is not None
        assert right.union.right.query.match.pattern.node.name == "c"
        assert right.union.all is False


# ============================================================================
# CASE WHEN expression
# ============================================================================

class TestCaseExpression:
    """CASE WHEN ... THEN ... ELSE ... END parsing."""

    def test_search_case(self):
        """CASE WHEN n.val > 0 THEN 'pos' ELSE 'neg' END"""
        stmt = _parse("RETURN CASE WHEN n.val > 0 THEN 'pos' ELSE 'neg' END AS result")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, CaseExpression)
        assert expr.expression is None
        assert len(expr.cases) == 1
        # Condition: n.val > 0
        cond = expr.cases[0][0]
        assert isinstance(cond, BinaryOp)
        assert cond.op == ">"
        # Then: 'pos'
        then_expr = expr.cases[0][1]
        assert isinstance(then_expr, Literal)
        assert then_expr.value == "pos"
        # Default: 'neg'
        assert isinstance(expr.default, Literal)
        assert expr.default.value == "neg"

    def test_simple_case(self):
        """CASE n.val WHEN 1 THEN 'one' WHEN 2 THEN 'two' END"""
        stmt = _parse("RETURN CASE n.val WHEN 1 THEN 'one' WHEN 2 THEN 'two' END")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, CaseExpression)
        # expression = n.val
        assert isinstance(expr.expression, PropertyAccess)
        assert expr.expression.key == "val"
        # 2 cases
        assert len(expr.cases) == 2
        # Case 1: WHEN 1 THEN 'one'
        assert isinstance(expr.cases[0][0], Literal)
        assert expr.cases[0][0].value == 1
        assert isinstance(expr.cases[0][1], Literal)
        assert expr.cases[0][1].value == "one"
        # Case 2: WHEN 2 THEN 'two'
        assert isinstance(expr.cases[1][0], Literal)
        assert expr.cases[1][0].value == 2
        assert isinstance(expr.cases[1][1], Literal)
        assert expr.cases[1][1].value == "two"
        # No ELSE clause
        assert expr.default is None

    def test_simple_case_with_default(self):
        """CASE n.val WHEN 1 THEN 'one' ELSE 'other' END"""
        stmt = _parse("RETURN CASE n.val WHEN 1 THEN 'one' ELSE 'other' END")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, CaseExpression)
        assert isinstance(expr.expression, PropertyAccess)
        assert len(expr.cases) == 1
        assert isinstance(expr.default, Literal)
        assert expr.default.value == "other"

    def test_case_with_single_when_then_no_else(self):
        """CASE WHEN n.active THEN 'yes' END"""
        stmt = _parse("RETURN CASE WHEN n.active THEN 'yes' END")
        expr = stmt.query.return_clause.items[0].expression
        assert isinstance(expr, CaseExpression)
        assert expr.expression is None
        assert len(expr.cases) == 1
        assert isinstance(expr.cases[0][0], PropertyAccess)
        assert isinstance(expr.cases[0][1], Literal)
        assert expr.cases[0][1].value == "yes"
        assert expr.default is None

    def test_case_in_where(self):
        """WHERE CASE WHEN ... THEN ... END = 'value'"""
        stmt = _parse("MATCH (n) WHERE CASE WHEN n.a = 1 THEN 'yes' ELSE 'no' END = 'yes' RETURN n")
        expr = stmt.query.where.expression
        # Should be BinaryOp with = operator
        assert isinstance(expr, BinaryOp)
        assert expr.op == "="
        assert isinstance(expr.left, CaseExpression)
        assert isinstance(expr.right, Literal)

    def test_case_error_no_end(self):
        """CASE WHEN ... THEN ... — missing END"""
        with pytest.raises(CypherSyntaxError):
            _parse("RETURN CASE WHEN n.val > 0 THEN 'pos'")

    def test_case_error_no_when(self):
        """CASE x THEN 'pos' END — missing WHEN"""
        with pytest.raises(CypherSyntaxError):
            _parse("RETURN CASE n.val THEN 'oops' END")


# ============================================================================
# UNWIND clause
# ============================================================================

class TestUnwind:
    """UNWIND clause parsing."""

    def test_unwind_list(self):
        """UNWIND [1, 2, 3] AS x RETURN x"""
        stmt = _parse("UNWIND [1, 2, 3] AS x RETURN x")
        q = stmt.query
        assert q.unwind is not None
        assert q.unwind.variable == "x"
        uw_expr = q.unwind.expression
        assert isinstance(uw_expr, ListLiteral)
        assert len(uw_expr.elements) == 3
        assert uw_expr.elements[0].value == 1

    def test_unwind_with_match(self):
        """MATCH (n) UNWIND n.labels AS label RETURN n, label"""
        stmt = _parse("MATCH (n) UNWIND n.labels AS label RETURN n, label")
        q = stmt.query
        assert q.match is not None
        assert q.unwind is not None
        assert q.unwind.variable == "label"
        assert isinstance(q.unwind.expression, PropertyAccess)
        assert q.unwind.expression.key == "labels"

    def test_unwind_error_no_as(self):
        """UNWIND [1,2,3] x — missing AS keyword"""
        with pytest.raises(CypherSyntaxError):
            _parse("UNWIND [1,2,3] x RETURN x")


# ============================================================================
# EXISTS subquery
# ============================================================================

class TestSubquery:
    """EXISTS { MATCH ... } subquery parsing."""

    def test_exists_subquery_in_where(self):
        stmt = _parse("MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) } RETURN n")
        q = stmt.query
        assert q.where is not None
        expr = q.where.expression
        assert isinstance(expr, SubqueryExpression)
        assert expr.exists is True
        # Inner query
        inner = expr.query
        assert inner.match is not None
        pp = inner.match.pattern
        assert pp.node.name == "n"
        assert len(pp.chain) == 1
        assert pp.chain[0].rel.types == ["calls"]
        assert pp.chain[0].node.name == "m"

    def test_exists_subquery_with_where_inside(self):
        stmt = _parse("MATCH (n) WHERE EXISTS { MATCH (n)-[:calls]->(m) WHERE m.lang = 'python' } RETURN n")
        expr = stmt.query.where.expression
        assert isinstance(expr, SubqueryExpression)
        assert expr.query.where is not None


# ============================================================================
# WITH clause
# ============================================================================

class TestWithClause:
    """WITH clause parsing."""

    def test_with_simple(self):
        stmt = _parse("MATCH (n) WITH n.name AS name RETURN name")
        q = stmt.query
        assert q.with_clause is not None
        wc = q.with_clause
        assert len(wc.items) == 1
        assert wc.items[0].alias == "name"
        assert isinstance(wc.items[0].expression, PropertyAccess)
        assert wc.items[0].expression.key == "name"
        assert wc.where is None

    def test_with_where(self):
        stmt = _parse("MATCH (n) WITH n.name AS name, n.age AS age WHERE name STARTS WITH 'test' RETURN name, age")
        q = stmt.query
        assert q.with_clause is not None
        wc = q.with_clause
        assert len(wc.items) == 2
        assert wc.items[0].alias == "name"
        assert wc.items[1].alias == "age"
        assert wc.where is not None
        assert isinstance(wc.where.expression, BinaryOp)
        assert wc.where.expression.op == "STARTS WITH"

    def test_unwind_with(self):
        """UNWIND [1,2] AS x WITH x AS num RETURN num"""
        stmt = _parse("UNWIND [1, 2] AS x WITH x AS num RETURN num")
        q = stmt.query
        assert q.unwind is not None
        assert q.unwind.variable == "x"
        assert q.with_clause is not None
        assert q.with_clause.items[0].alias == "num"

    def test_with_multiple_items(self):
        stmt = _parse("MATCH (n) WITH n AS node, n.name AS name RETURN node, name")
        q = stmt.query
        assert q.with_clause is not None
        assert len(q.with_clause.items) == 2
        assert q.with_clause.items[0].alias == "node"
        assert q.with_clause.items[1].alias == "name"
