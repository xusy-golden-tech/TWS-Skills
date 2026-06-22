"""Tests for cypher AST node definitions."""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from tws_graph.cypher.ast import (
    Span,
    Node,
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
    Expression,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _span():
    return Span(start_line=1, start_col=1, end_line=1, end_col=5)


# ============================================================================
# Span
# ============================================================================

class TestSpan:
    def test_construct(self):
        s = Span(1, 2, 3, 4)
        assert s.start_line == 1
        assert s.start_col == 2
        assert s.end_line == 3
        assert s.end_col == 4

    def test_equality(self):
        s1 = Span(1, 2, 3, 4)
        s2 = Span(1, 2, 3, 4)
        s3 = Span(5, 6, 7, 8)
        assert s1 == s2
        assert s1 != s3

    def test_hashable(self):
        s = Span(1, 2, 3, 4)
        assert hash(s) is not None
        d = {s: "test"}
        assert d[s] == "test"

    def test_frozen(self):
        s = Span(1, 2, 3, 4)
        with pytest.raises(FrozenInstanceError):
            s.start_line = 99


# ============================================================================
# Direction
# ============================================================================

class TestDirection:
    def test_values(self):
        assert Direction.LEFT.value == "left"
        assert Direction.RIGHT.value == "right"
        assert Direction.BOTH.value == "both"

    def test_members(self):
        members = set(Direction)
        assert members == {Direction.LEFT, Direction.RIGHT, Direction.BOTH}

    def test_default_usage(self):
        assert Direction.RIGHT == Direction.RIGHT


# ============================================================================
# Node (ABC)
# ============================================================================

class TestNodeABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Node(span=_span())  # type: ignore[abstract]


# ============================================================================
# Statement
# ============================================================================

class TestStatement:
    def test_construct_minimal(self):
        q = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        stmt = Statement(query=q, span=_span())
        assert stmt.query is q
        assert stmt.union is None

    def test_construct_with_union(self):
        q1 = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        q2 = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        union = UnionClause(all=True, right=Statement(query=q2, span=_span()), span=_span())
        stmt = Statement(query=q1, union=union, span=_span())
        assert stmt.union.all is True

    def test_frozen(self):
        q = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        stmt = Statement(query=q, span=_span())
        with pytest.raises(FrozenInstanceError):
            stmt.union = None  # type: ignore[misc]


# ============================================================================
# UnionClause
# ============================================================================

class TestUnionClause:
    def test_construct_union_all(self):
        q = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        right = Statement(query=q, span=_span())
        uc = UnionClause(all=True, right=right, span=_span())
        assert uc.all is True
        assert uc.right is right

    def test_construct_union_distinct(self):
        q = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        right = Statement(query=q, span=_span())
        uc = UnionClause(all=False, right=right, span=_span())
        assert uc.all is False

    def test_frozen(self):
        q = Query(return_clause=ReturnClause(items=[], span=_span()), span=_span())
        right = Statement(query=q, span=_span())
        uc = UnionClause(all=True, right=right, span=_span())
        with pytest.raises(FrozenInstanceError):
            uc.all = False  # type: ignore[misc]


# ============================================================================
# Query
# ============================================================================

class TestQuery:
    def test_construct_minimal(self):
        rc = ReturnClause(items=[], span=_span())
        q = Query(return_clause=rc, span=_span())
        assert q.match is None
        assert q.optional_matches == []
        assert q.where is None
        assert q.return_clause is rc
        assert q.order_by is None
        assert q.skip is None
        assert q.limit is None

    def test_construct_full(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, chain=[], span=_span())
        mc = MatchClause(pattern=pp, span=_span())
        wc = WhereClause(
            expression=Identifier(name="x", span=_span()),
            span=_span(),
        )
        rc = ReturnClause(items=[ReturnItem(expression=Identifier(name="x", span=_span()), span=_span())], span=_span())
        ob = OrderByClause(
            items=[OrderByItem(expression=Identifier(name="x", span=_span()), span=_span())],
            span=_span(),
        )
        q = Query(
            match=mc,
            optional_matches=[],
            where=wc,
            return_clause=rc,
            order_by=ob,
            skip=10,
            limit=20,
            span=_span(),
        )
        assert q.match is mc
        assert q.where is wc
        assert q.return_clause is rc
        assert q.order_by is ob
        assert q.skip == 10
        assert q.limit == 20

    def test_frozen(self):
        rc = ReturnClause(items=[], span=_span())
        q = Query(return_clause=rc, span=_span())
        with pytest.raises(FrozenInstanceError):
            q.skip = 5  # type: ignore[misc]

    def test_with_clause_default(self):
        rc = ReturnClause(items=[], span=_span())
        q = Query(return_clause=rc, span=_span())
        assert q.with_clause is None

    def test_unwind_default(self):
        rc = ReturnClause(items=[], span=_span())
        q = Query(return_clause=rc, span=_span())
        assert q.unwind is None

    def test_with_clause_and_unwind_set(self):
        rc = ReturnClause(items=[], span=_span())
        wc = WithClause(
            items=[ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())],
            span=_span(),
        )
        uw = UnwindClause(
            expression=ListLiteral(elements=[], span=_span()),
            variable="x",
            span=_span(),
        )
        q = Query(return_clause=rc, with_clause=wc, unwind=uw, span=_span())
        assert q.with_clause is wc
        assert q.unwind is uw


# ============================================================================
# MatchClause
# ============================================================================

class TestMatchClause:
    def test_construct_required(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, chain=[], span=_span())
        mc = MatchClause(pattern=pp, span=_span())
        assert mc.pattern is pp
        assert mc.optional is False

    def test_construct_optional(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, chain=[], span=_span())
        mc = MatchClause(pattern=pp, optional=True, span=_span())
        assert mc.optional is True

    def test_frozen(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, chain=[], span=_span())
        mc = MatchClause(pattern=pp, span=_span())
        with pytest.raises(FrozenInstanceError):
            mc.optional = True  # type: ignore[misc]


# ============================================================================
# PatternPart
# ============================================================================

class TestPatternPart:
    def test_construct(self):
        node = NodePattern(span=_span())
        pe = PatternElement(rel=None, node=node, span=_span())
        pp = PatternPart(node=node, chain=[pe], span=_span())
        assert pp.node is node
        assert pp.chain == [pe]

    def test_default_chain(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, span=_span())
        assert pp.chain == []

    def test_frozen(self):
        node = NodePattern(span=_span())
        pp = PatternPart(node=node, span=_span())
        with pytest.raises(FrozenInstanceError):
            pp.chain = []  # type: ignore[misc]


# ============================================================================
# PatternElement
# ============================================================================

class TestPatternElement:
    def test_construct_node_only(self):
        node = NodePattern(name="n", span=_span())
        pe = PatternElement(rel=None, node=node, span=_span())
        assert pe.rel is None
        assert pe.node is node

    def test_construct_with_rel(self):
        node = NodePattern(name="m", span=_span())
        rel = RelPattern(direction=Direction.RIGHT, span=_span())
        pe = PatternElement(rel=rel, node=node, span=_span())
        assert pe.rel is rel
        assert pe.node is node

    def test_frozen(self):
        node = NodePattern(span=_span())
        pe = PatternElement(rel=None, node=node, span=_span())
        with pytest.raises(FrozenInstanceError):
            pe.rel = None  # type: ignore[misc]


# ============================================================================
# NodePattern
# ============================================================================

class TestNodePattern:
    def test_construct_minimal(self):
        np = NodePattern(span=_span())
        assert np.name is None
        assert np.labels == []
        assert np.properties is None

    def test_construct_full(self):
        np = NodePattern(
            name="n",
            labels=["Function", "Python"],
            properties={"kind": "function"},
            span=_span(),
        )
        assert np.name == "n"
        assert np.labels == ["Function", "Python"]
        assert np.properties == {"kind": "function"}

    def test_frozen(self):
        np = NodePattern(name="x", span=_span())
        with pytest.raises(FrozenInstanceError):
            np.name = "y"  # type: ignore[misc]


# ============================================================================
# RelPattern
# ============================================================================

class TestRelPattern:
    def test_construct_minimal(self):
        rp = RelPattern(span=_span())
        assert rp.name is None
        assert rp.types == []
        assert rp.direction == Direction.RIGHT
        assert rp.properties is None
        assert rp.var_length is None

    def test_construct_full(self):
        rp = RelPattern(
            name="r",
            types=["calls", "references"],
            direction=Direction.LEFT,
            properties={"count": 5},
            var_length=(1, 3),
            span=_span(),
        )
        assert rp.name == "r"
        assert rp.types == ["calls", "references"]
        assert rp.direction == Direction.LEFT
        assert rp.properties == {"count": 5}
        assert rp.var_length == (1, 3)

    def test_var_length_unbounded(self):
        rp = RelPattern(var_length=(2, None), span=_span())
        assert rp.var_length == (2, None)

    def test_frozen(self):
        rp = RelPattern(span=_span())
        with pytest.raises(FrozenInstanceError):
            rp.direction = Direction.LEFT  # type: ignore[misc]


# ============================================================================
# WhereClause
# ============================================================================

class TestWhereClause:
    def test_construct(self):
        expr = BinaryOp(
            op="=",
            left=Identifier(name="x", span=_span()),
            right=Literal(value=42, span=_span()),
            span=_span(),
        )
        wc = WhereClause(expression=expr, span=_span())
        assert isinstance(wc.expression, BinaryOp)
        assert wc.expression.op == "="

    def test_frozen(self):
        expr = Identifier(name="x", span=_span())
        wc = WhereClause(expression=expr, span=_span())
        with pytest.raises(FrozenInstanceError):
            wc.expression = Identifier(name="y", span=_span())  # type: ignore[misc]


# ============================================================================
# Expression node types
# ============================================================================

class TestIdentifier:
    def test_construct(self):
        ident = Identifier(name="myVar", span=_span())
        assert ident.name == "myVar"

    def test_frozen(self):
        ident = Identifier(name="x", span=_span())
        with pytest.raises(FrozenInstanceError):
            ident.name = "y"  # type: ignore[misc]


class TestLiteral:
    @pytest.mark.parametrize("value", [42, 3.14, "hello", True, False, None])
    def test_construct_variants(self, value):
        lit = Literal(value=value, span=_span())
        assert lit.value == value

    def test_frozen(self):
        lit = Literal(value=1, span=_span())
        with pytest.raises(FrozenInstanceError):
            lit.value = 2  # type: ignore[misc]


class TestListLiteral:
    def test_construct_empty(self):
        ll = ListLiteral(elements=[], span=_span())
        assert ll.elements == []

    def test_construct_with_elements(self):
        e1 = Literal(value=1, span=_span())
        e2 = Literal(value=2, span=_span())
        ll = ListLiteral(elements=[e1, e2], span=_span())
        assert len(ll.elements) == 2

    def test_frozen(self):
        ll = ListLiteral(elements=[], span=_span())
        with pytest.raises(FrozenInstanceError):
            ll.elements = []  # type: ignore[misc]


class TestFunctionCall:
    def test_construct_minimal(self):
        fc = FunctionCall(name="toUpper", args=[], span=_span())
        assert fc.name == "toUpper"
        assert fc.args == []
        assert fc.distinct is False

    def test_construct_with_args(self):
        arg = Identifier(name="x", span=_span())
        fc = FunctionCall(name="coalesce", args=[arg, Literal(value=0, span=_span())], span=_span())
        assert fc.name == "coalesce"
        assert len(fc.args) == 2

    def test_construct_distinct(self):
        fc = FunctionCall(name="count", args=[Identifier(name="x", span=_span())], distinct=True, span=_span())
        assert fc.distinct is True

    def test_frozen(self):
        fc = FunctionCall(name="f", args=[], span=_span())
        with pytest.raises(FrozenInstanceError):
            fc.name = "g"  # type: ignore[misc]


class TestParameter:
    def test_construct(self):
        p = Parameter(name="myParam", span=_span())
        assert p.name == "myParam"

    def test_frozen(self):
        p = Parameter(name="p", span=_span())
        with pytest.raises(FrozenInstanceError):
            p.name = "q"  # type: ignore[misc]


class TestBinaryOp:
    @pytest.mark.parametrize("op", ["AND", "OR", "=", "<>", "<", ">", "<=", ">=", "+", "-", "*", "/", "%", "^", "=~"])
    def test_construct_all_operators(self, op):
        left = Identifier(name="a", span=_span())
        right = Literal(value=1, span=_span())
        bo = BinaryOp(op=op, left=left, right=right, span=_span())
        assert bo.op == op
        assert bo.left is left
        assert bo.right is right

    def test_frozen(self):
        left = Identifier(name="a", span=_span())
        right = Identifier(name="b", span=_span())
        bo = BinaryOp(op="=", left=left, right=right, span=_span())
        with pytest.raises(FrozenInstanceError):
            bo.op = "<>"  # type: ignore[misc]


class TestUnaryOp:
    @pytest.mark.parametrize("op", ["NOT", "-", "IS NULL", "IS NOT NULL"])
    def test_construct_all_operators(self, op):
        operand = Identifier(name="x", span=_span())
        uo = UnaryOp(op=op, operand=operand, span=_span())
        assert uo.op == op
        assert uo.operand is operand

    def test_frozen(self):
        uo = UnaryOp(op="NOT", operand=Identifier(name="x", span=_span()), span=_span())
        with pytest.raises(FrozenInstanceError):
            uo.op = "-"  # type: ignore[misc]


class TestPropertyAccess:
    def test_construct(self):
        obj = Identifier(name="n", span=_span())
        pa = PropertyAccess(obj=obj, key="name", span=_span())
        assert pa.obj is obj
        assert pa.key == "name"

    def test_nested_property_access(self):
        obj = PropertyAccess(obj=Identifier(name="n", span=_span()), key="addr", span=_span())
        pa = PropertyAccess(obj=obj, key="city", span=_span())
        assert pa.key == "city"
        assert pa.obj.key == "addr"

    def test_frozen(self):
        pa = PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span())
        with pytest.raises(FrozenInstanceError):
            pa.key = "other"  # type: ignore[misc]


class TestInExpression:
    def test_construct(self):
        expr = Identifier(name="x", span=_span())
        lst = ListLiteral(elements=[Literal(value=1, span=_span()), Literal(value=2, span=_span())], span=_span())
        ie = InExpression(expr=expr, list=lst, span=_span())
        assert ie.expr is expr
        assert ie.list is lst

    def test_frozen(self):
        expr = Identifier(name="x", span=_span())
        lst = ListLiteral(elements=[], span=_span())
        ie = InExpression(expr=expr, list=lst, span=_span())
        with pytest.raises(FrozenInstanceError):
            ie.expr = Identifier(name="y", span=_span())  # type: ignore[misc]


# ============================================================================
# RETURN clause nodes
# ============================================================================

class TestStarExpression:
    def test_construct(self):
        se = StarExpression(span=_span())
        assert se.span is not None

    def test_frozen(self):
        se = StarExpression(span=_span())
        with pytest.raises(FrozenInstanceError):
            se.span = _span()  # type: ignore[misc]


class TestReturnItem:
    def test_construct_no_alias(self):
        expr = Identifier(name="n", span=_span())
        ri = ReturnItem(expression=expr, span=_span())
        assert ri.expression is expr
        assert ri.alias is None

    def test_construct_with_alias(self):
        expr = PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span())
        ri = ReturnItem(expression=expr, alias="funcName", span=_span())
        assert ri.alias == "funcName"

    def test_frozen(self):
        ri = ReturnItem(expression=StarExpression(span=_span()), span=_span())
        with pytest.raises(FrozenInstanceError):
            ri.alias = "x"  # type: ignore[misc]


class TestReturnClause:
    def test_construct_minimal(self):
        rc = ReturnClause(items=[], span=_span())
        assert rc.items == []
        assert rc.distinct is False

    def test_construct_with_items(self):
        item = ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())
        rc = ReturnClause(items=[item], distinct=True, span=_span())
        assert len(rc.items) == 1
        assert rc.distinct is True

    def test_frozen(self):
        rc = ReturnClause(items=[], span=_span())
        with pytest.raises(FrozenInstanceError):
            rc.distinct = True  # type: ignore[misc]


# ============================================================================
# ORDER BY clause nodes
# ============================================================================

class TestOrderByItem:
    def test_construct_default_asc(self):
        expr = Identifier(name="n", span=_span())
        obi = OrderByItem(expression=expr, span=_span())
        assert obi.expression is expr
        assert obi.direction == "ASC"

    def test_construct_desc(self):
        expr = Identifier(name="n", span=_span())
        obi = OrderByItem(expression=expr, direction="DESC", span=_span())
        assert obi.direction == "DESC"

    def test_frozen(self):
        obi = OrderByItem(expression=Identifier(name="n", span=_span()), span=_span())
        with pytest.raises(FrozenInstanceError):
            obi.direction = "DESC"  # type: ignore[misc]


class TestOrderByClause:
    def test_construct(self):
        items = [
            OrderByItem(expression=Identifier(name="a", span=_span()), span=_span()),
            OrderByItem(expression=Identifier(name="b", span=_span()), direction="DESC", span=_span()),
        ]
        obc = OrderByClause(items=items, span=_span())
        assert len(obc.items) == 2
        assert obc.items[0].direction == "ASC"
        assert obc.items[1].direction == "DESC"

    def test_frozen(self):
        obc = OrderByClause(items=[], span=_span())
        with pytest.raises(FrozenInstanceError):
            obc.items = []  # type: ignore[misc]


# ============================================================================
# Expression union type — verify all variants are valid
# ============================================================================

class TestExpressionUnion:
    """Verify that all 12 expression node types are valid members of Expression."""

    def test_identifier_is_expression(self):
        node = Identifier(name="x", span=_span())
        # Accept the node where Expression is expected
        self._accept_expression(node)

    def test_literal_is_expression(self):
        node = Literal(value=42, span=_span())
        self._accept_expression(node)

    def test_list_literal_is_expression(self):
        node = ListLiteral(elements=[], span=_span())
        self._accept_expression(node)

    def test_function_call_is_expression(self):
        node = FunctionCall(name="f", args=[], span=_span())
        self._accept_expression(node)

    def test_parameter_is_expression(self):
        node = Parameter(name="$p", span=_span())
        self._accept_expression(node)

    def test_binary_op_is_expression(self):
        node = BinaryOp(
            op="=",
            left=Identifier(name="a", span=_span()),
            right=Identifier(name="b", span=_span()),
            span=_span(),
        )
        self._accept_expression(node)

    def test_unary_op_is_expression(self):
        node = UnaryOp(op="NOT", operand=Identifier(name="x", span=_span()), span=_span())
        self._accept_expression(node)

    def test_property_access_is_expression(self):
        node = PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span())
        self._accept_expression(node)

    def test_in_expression_is_expression(self):
        node = InExpression(
            expr=Identifier(name="x", span=_span()),
            list=ListLiteral(elements=[], span=_span()),
            span=_span(),
        )
        self._accept_expression(node)

    def test_star_expression_is_expression(self):
        node = StarExpression(span=_span())
        self._accept_expression(node)

    def test_case_expression_is_expression(self):
        node = CaseExpression(
            cases=[(Identifier(name="x", span=_span()), Literal(value=1, span=_span()))],
            span=_span(),
        )
        self._accept_expression(node)

    def test_subquery_expression_is_expression(self):
        node = SubqueryExpression(
            query=Query(return_clause=ReturnClause(items=[], span=_span()), span=_span()),
            span=_span(),
        )
        self._accept_expression(node)

    @staticmethod
    def _accept_expression(expr: Expression) -> None:
        """Helper to type-check that expr is a valid Expression."""
        assert isinstance(expr, Node)


# ============================================================================
# Nested structure — full Query construction
# ============================================================================

class TestNestedStructure:
    """Verify complex nested AST construction works end-to-end."""

    def test_full_query_nested(self):
        """MATCH (n:Function)-[:calls]->(m:Function) WHERE n.name = 'main' RETURN n, m ORDER BY n.name SKIP 0 LIMIT 10"""

        # node patterns
        n = NodePattern(name="n", labels=["Function"], span=_span())
        m = NodePattern(name="m", labels=["Function"], span=_span())

        # relationship
        rel = RelPattern(types=["calls"], direction=Direction.RIGHT, span=_span())

        # pattern chain: (n)-[:calls]->(m)
        pe = PatternElement(rel=rel, node=m, span=_span())

        # pattern part
        pp = PatternPart(node=n, chain=[pe], span=_span())

        # match clause
        mc = MatchClause(pattern=pp, span=_span())

        # WHERE n.name = 'main'
        where_expr = BinaryOp(
            op="=",
            left=PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span()),
            right=Literal(value="main", span=_span()),
            span=_span(),
        )
        wc = WhereClause(expression=where_expr, span=_span())

        # RETURN n, m
        ri_n = ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())
        ri_m = ReturnItem(expression=Identifier(name="m", span=_span()), span=_span())
        rc = ReturnClause(items=[ri_n, ri_m], span=_span())

        # ORDER BY n.name
        obi = OrderByItem(
            expression=PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span()),
            span=_span(),
        )
        obc = OrderByClause(items=[obi], span=_span())

        # full query
        query = Query(
            match=mc,
            where=wc,
            return_clause=rc,
            order_by=obc,
            skip=0,
            limit=10,
            span=_span(),
        )

        # assert
        assert query.match is mc
        assert query.match.pattern.node.name == "n"
        assert query.match.pattern.chain[0].rel.types == ["calls"]
        assert query.match.pattern.chain[0].node.name == "m"
        assert query.where is wc
        assert isinstance(query.where.expression, BinaryOp)
        assert query.where.expression.op == "="
        assert query.return_clause.items[0].alias is None
        assert query.order_by.items[0].direction == "ASC"
        assert query.skip == 0
        assert query.limit == 10

    def test_simple_match_return(self):
        """MATCH (n) RETURN n"""
        n = NodePattern(name="n", span=_span())
        pp = PatternPart(node=n, chain=[], span=_span())
        mc = MatchClause(pattern=pp, span=_span())
        rc = ReturnClause(
            items=[ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())],
            span=_span(),
        )
        q = Query(match=mc, return_clause=rc, span=_span())
        assert q.match.pattern.node.name == "n"
        assert q.return_clause.items[0].expression.name == "n"

    def test_variable_length_relationship(self):
        """MATCH (a)-[:calls*2..5]->(b) RETURN a, b"""
        a = NodePattern(name="a", span=_span())
        b = NodePattern(name="b", span=_span())
        rel = RelPattern(types=["calls"], direction=Direction.RIGHT, var_length=(2, 5), span=_span())
        pe = PatternElement(rel=rel, node=b, span=_span())
        pp = PatternPart(node=a, chain=[pe], span=_span())
        mc = MatchClause(pattern=pp, span=_span())
        rc = ReturnClause(
            items=[
                ReturnItem(expression=Identifier(name="a", span=_span()), span=_span()),
                ReturnItem(expression=Identifier(name="b", span=_span()), span=_span()),
            ],
            span=_span(),
        )
        q = Query(match=mc, return_clause=rc, span=_span())
        assert q.match.pattern.chain[0].rel.var_length == (2, 5)

    def test_optional_match(self):
        """OPTIONAL MATCH (n)-[:imports]->(m)"""
        n = NodePattern(name="n", span=_span())
        m = NodePattern(name="m", span=_span())
        rel = RelPattern(types=["imports"], span=_span())
        pe = PatternElement(rel=rel, node=m, span=_span())
        pp = PatternPart(node=n, chain=[pe], span=_span())
        mc = MatchClause(pattern=pp, optional=True, span=_span())
        rc = ReturnClause(
            items=[ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())],
            span=_span(),
        )
        q = Query(match=MatchClause(
            pattern=PatternPart(node=NodePattern(name="x", span=_span()), span=_span()),
            span=_span(),
        ), optional_matches=[mc], return_clause=rc, span=_span())
        assert q.optional_matches[0].optional is True
        assert q.optional_matches[0].pattern.node.name == "n"

    def test_where_with_not(self):
        """WHERE NOT n.is_test"""
        where_expr = UnaryOp(
            op="NOT",
            operand=PropertyAccess(obj=Identifier(name="n", span=_span()), key="is_test", span=_span()),
            span=_span(),
        )
        wc = WhereClause(expression=where_expr, span=_span())
        assert isinstance(wc.expression, UnaryOp)
        assert wc.expression.op == "NOT"

    def test_where_with_is_null(self):
        """WHERE n.parent IS NULL"""
        where_expr = UnaryOp(
            op="IS NULL",
            operand=PropertyAccess(obj=Identifier(name="n", span=_span()), key="parent", span=_span()),
            span=_span(),
        )
        wc = WhereClause(expression=where_expr, span=_span())
        assert wc.expression.op == "IS NULL"

    def test_where_with_in(self):
        """WHERE n.kind IN ['function', 'method']"""
        where_expr = InExpression(
            expr=PropertyAccess(obj=Identifier(name="n", span=_span()), key="kind", span=_span()),
            list=ListLiteral(
                elements=[Literal(value="function", span=_span()), Literal(value="method", span=_span())],
                span=_span(),
            ),
            span=_span(),
        )
        wc = WhereClause(expression=where_expr, span=_span())
        assert isinstance(wc.expression, InExpression)

    def test_return_star(self):
        """RETURN *"""
        rc = ReturnClause(items=[ReturnItem(expression=StarExpression(span=_span()), span=_span())], span=_span())
        q = Query(return_clause=rc, span=_span())
        assert isinstance(q.return_clause.items[0].expression, StarExpression)

    def test_return_distinct(self):
        """RETURN DISTINCT n.name"""
        rc = ReturnClause(
            distinct=True,
            items=[ReturnItem(
                expression=PropertyAccess(obj=Identifier(name="n", span=_span()), key="name", span=_span()),
                alias="funcName",
                span=_span(),
            )],
            span=_span(),
        )
        assert rc.distinct is True
        assert rc.items[0].alias == "funcName"


# ============================================================================
# CaseExpression
# ============================================================================

class TestCaseExpression:
    """CaseExpression: CASE WHEN ... THEN ... ELSE ... END"""

    def test_construct_search_case(self):
        """CASE WHEN n.val > 0 THEN 'pos' ELSE 'neg' END — expression=None search case"""
        when_cond = BinaryOp(
            op=">",
            left=PropertyAccess(obj=Identifier(name="n", span=_span()), key="val", span=_span()),
            right=Literal(value=0, span=_span()),
            span=_span(),
        )
        then_result = Literal(value="pos", span=_span())
        default = Literal(value="neg", span=_span())
        ce = CaseExpression(
            expression=None,
            cases=[(when_cond, then_result)],
            default=default,
            span=_span(),
        )
        assert ce.expression is None
        assert len(ce.cases) == 1
        assert ce.cases[0][0] is when_cond
        assert ce.cases[0][1] is then_result
        assert ce.default is default

    def test_construct_simple_case(self):
        """CASE n.val WHEN 1 THEN 'one' WHEN 2 THEN 'two' END — expression-based case"""
        expr = PropertyAccess(obj=Identifier(name="n", span=_span()), key="val", span=_span())
        when1 = Literal(value=1, span=_span())
        then1 = Literal(value="one", span=_span())
        when2 = Literal(value=2, span=_span())
        then2 = Literal(value="two", span=_span())
        ce = CaseExpression(
            expression=expr,
            cases=[(when1, then1), (when2, then2)],
            default=None,
            span=_span(),
        )
        assert ce.expression is expr
        assert len(ce.cases) == 2
        assert ce.cases[0][0] is when1
        assert ce.cases[0][1] is then1
        assert ce.cases[1][0] is when2
        assert ce.cases[1][1] is then2
        assert ce.default is None

    def test_construct_without_default(self):
        """CASE WHEN x THEN y END — no ELSE clause"""
        when = Literal(value=True, span=_span())
        then = Identifier(name="y", span=_span())
        ce = CaseExpression(
            expression=None,
            cases=[(when, then)],
            span=_span(),
        )
        assert ce.default is None

    def test_default_fields(self):
        """Default values for optional fields."""
        ce = CaseExpression(
            cases=[(Literal(value=True, span=_span()), Identifier(name="x", span=_span()))],
            span=_span(),
        )
        assert ce.expression is None
        assert ce.default is None

    def test_frozen(self):
        ce = CaseExpression(
            cases=[(Identifier(name="x", span=_span()), Identifier(name="y", span=_span()))],
            span=_span(),
        )
        with pytest.raises(FrozenInstanceError):
            ce.cases = []  # type: ignore[misc]


# ============================================================================
# UnwindClause
# ============================================================================

class TestUnwindClause:
    """UnwindClause: UNWIND list AS var"""

    def test_construct(self):
        lst = ListLiteral(
            elements=[Literal(value=1, span=_span()), Literal(value=2, span=_span()), Literal(value=3, span=_span())],
            span=_span(),
        )
        uw = UnwindClause(expression=lst, variable="x", span=_span())
        assert uw.expression is lst
        assert uw.variable == "x"

    def test_construct_with_identifier(self):
        """UNWIND $list AS item"""
        param = Parameter(name="$list", span=_span())
        uw = UnwindClause(expression=param, variable="item", span=_span())
        assert uw.expression is param
        assert uw.variable == "item"

    def test_frozen(self):
        lst = ListLiteral(elements=[], span=_span())
        uw = UnwindClause(expression=lst, variable="x", span=_span())
        with pytest.raises(FrozenInstanceError):
            uw.variable = "y"  # type: ignore[misc]


# ============================================================================
# SubqueryExpression
# ============================================================================

class TestSubqueryExpression:
    """SubqueryExpression: EXISTS { MATCH ... }"""

    def test_construct_exists(self):
        """EXISTS { MATCH (n) RETURN n }"""
        inner_q = Query(
            match=MatchClause(
                pattern=PatternPart(node=NodePattern(name="n", span=_span()), span=_span()),
                span=_span(),
            ),
            return_clause=ReturnClause(
                items=[ReturnItem(expression=Identifier(name="n", span=_span()), span=_span())],
                span=_span(),
            ),
            span=_span(),
        )
        sqe = SubqueryExpression(query=inner_q, exists=True, span=_span())
        assert sqe.query is inner_q
        assert sqe.exists is True

    def test_construct_bare_subquery(self):
        """{ MATCH (n) RETURN n } — bare subquery without EXISTS"""
        inner_q = Query(
            match=MatchClause(
                pattern=PatternPart(node=NodePattern(span=_span()), span=_span()),
                span=_span(),
            ),
            return_clause=ReturnClause(items=[], span=_span()),
            span=_span(),
        )
        sqe = SubqueryExpression(query=inner_q, exists=False, span=_span())
        assert sqe.exists is False
        assert sqe.query is inner_q

    def test_default_exists(self):
        """Default exists is True."""
        inner_q = Query(
            return_clause=ReturnClause(items=[], span=_span()),
            span=_span(),
        )
        sqe = SubqueryExpression(query=inner_q, span=_span())
        assert sqe.exists is True

    def test_frozen(self):
        inner_q = Query(
            return_clause=ReturnClause(items=[], span=_span()),
            span=_span(),
        )
        sqe = SubqueryExpression(query=inner_q, span=_span())
        with pytest.raises(FrozenInstanceError):
            sqe.exists = False  # type: ignore[misc]


# ============================================================================
# WithClause
# ============================================================================

class TestWithClause:
    """WithClause: WITH ... AS ... [WHERE ...]"""

    def test_construct_minimal(self):
        items = [
            ReturnItem(expression=Identifier(name="n", span=_span()), alias="name", span=_span()),
        ]
        wc = WithClause(items=items, span=_span())
        assert wc.items == items
        assert wc.where is None

    def test_construct_with_where(self):
        items = [
            ReturnItem(expression=Identifier(name="n", span=_span()), span=_span()),
        ]
        where_expr = BinaryOp(
            op="=",
            left=Identifier(name="n", span=_span()),
            right=Literal(value=42, span=_span()),
            span=_span(),
        )
        where_clause = WhereClause(expression=where_expr, span=_span())
        wc = WithClause(items=items, where=where_clause, span=_span())
        assert wc.where is where_clause

    def test_default_items_and_where(self):
        """Default values: items=[], where=None."""
        wc = WithClause(span=_span())
        assert wc.items == []
        assert wc.where is None

    def test_frozen(self):
        wc = WithClause(span=_span())
        with pytest.raises(FrozenInstanceError):
            wc.items = []  # type: ignore[misc]
