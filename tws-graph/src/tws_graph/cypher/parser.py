"""Cypher parser — recursive descent Parser converting Token stream to AST.

Per design doc: impl-p2-cypher-core.md Task 4.
"""

from __future__ import annotations

from typing import Optional

from .lexer import Lexer, Token, TokenType
from .ast import (
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
    Expression,
)
from .errors import CypherSyntaxError


class Parser:
    """Recursive-descent parser for the Cypher query language.

    Converts a Token stream (from Lexer) into an AST (Statement/Query).

    Usage::

        lexer = Lexer("MATCH (n) RETURN n")
        parser = Parser(lexer)
        stmt = parser.parse()  # Statement
    """

    def __init__(self, lexer: Lexer) -> None:
        self._lexer = lexer
        self._eof_token: Optional[Token] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> Statement:
        """Parse a complete Cypher query and return the Statement AST."""
        # Parse all statements separated by UNION
        statements: list[Statement] = [self._parse_statement()]
        union_flags: list[bool] = []

        while self._check_kw("UNION"):
            self._lexer.next()  # consume UNION
            all_union = self._match_kw("ALL")
            union_flags.append(all_union)
            statements.append(self._parse_statement())

        # Build nested UNION structure (left-associative)
        if union_flags:
            result = statements[-1]
            for i in range(len(statements) - 2, -1, -1):
                result = Statement(
                    query=statements[i].query,
                    union=UnionClause(
                        all=union_flags[i],
                        right=result,
                        span=result.span,
                    ),
                    span=self._span(statements[i].span, result.span),
                )
            stmt = result
        else:
            stmt = statements[0]

        # Check that we consumed all input
        if not self._check(TokenType.EOF):
            tok = self._lexer.peek()
            raise CypherSyntaxError(
                f"Unexpected token after end of statement: '{tok.value}'",
                expected="end of input",
                actual=tok.value,
                line=tok.line,
                col=tok.col,
            )
        return stmt

    # ------------------------------------------------------------------
    # Top-level parsing
    # ------------------------------------------------------------------

    def _parse_statement(self) -> Statement:
        """Parse Statement → Query."""
        query = self._parse_query()
        # The span covers from the query's span to its end
        return Statement(query=query, span=query.span)

    def _parse_query(self) -> Query:
        """Parse Query → [MATCH Pattern] [WHERE Expression] [UNWIND ...] [WITH ...] RETURN ReturnBody [ORDER BY OrderBy] [SKIP Integer] [LIMIT Integer].

        MATCH is optional. When omitted, UNWIND or WITH may start the query.
        """
        start_tok = self._lexer.peek()

        # Parse MATCH or OPTIONAL MATCH (optional)
        match = None
        optional_matches: list[MatchClause] = []

        if self._check_kw("MATCH") or self._check_kw("OPTIONAL"):
            match = self._parse_match()
            # Parse additional OPTIONAL MATCH clauses
            while self._check_kw("OPTIONAL"):
                optional_matches.append(self._parse_match())

        # Parse WHERE (optional)
        where = None
        if self._check_kw("WHERE"):
            where = self._parse_where()

        # Parse UNWIND (optional)
        unwind = None
        if self._check_kw("UNWIND"):
            unwind = self._parse_unwind()

        # Parse WITH (optional)
        with_clause = None
        if self._check_kw("WITH"):
            with_clause = self._parse_with()

        # Parse RETURN (required)
        return_clause = self._parse_return()

        # Parse ORDER BY (optional)
        order_by = None
        if self._check_kw("ORDER"):
            order_by = self._parse_order_by()

        # Parse SKIP (optional)
        skip = None
        if self._check_kw("SKIP"):
            self._lexer.next()  # consume SKIP
            int_tok = self._consume(TokenType.INTEGER, "expected integer after SKIP")
            skip = int(int_tok.value)

        # Parse LIMIT (optional)
        limit = None
        if self._check_kw("LIMIT"):
            self._lexer.next()  # consume LIMIT
            int_tok = self._consume(TokenType.INTEGER, "expected integer after LIMIT")
            limit = int(int_tok.value)

        return Query(
            match=match,
            optional_matches=optional_matches,
            where=where,
            with_clause=with_clause,
            unwind=unwind,
            return_clause=return_clause,
            order_by=order_by,
            skip=skip,
            limit=limit,
            span=self._span(start_tok, self._lexer.peek()),
        )

    # ------------------------------------------------------------------
    # MATCH clause
    # ------------------------------------------------------------------

    def _parse_match(self) -> MatchClause:
        """Parse MATCH [OPTIONAL MATCH] Pattern."""
        start = self._lexer.peek()
        optional = False

        if self._check_kw("OPTIONAL"):
            self._lexer.next()  # consume OPTIONAL
            optional = True

        self._consume_kw("MATCH", "expected MATCH")
        pattern = self._parse_pattern()
        return MatchClause(
            pattern=pattern,
            optional=optional,
            span=self._span(start, self._lexer.peek()),
        )

    # ------------------------------------------------------------------
    # WHERE clause
    # ------------------------------------------------------------------

    def _parse_where(self) -> WhereClause:
        """Parse WHERE Expression."""
        start = self._consume_kw("WHERE", "expected WHERE")
        expr = self._parse_expression()
        return WhereClause(
            expression=expr,
            span=self._span(start, self._lexer.peek()),
        )

    # ------------------------------------------------------------------
    # RETURN clause
    # ------------------------------------------------------------------

    def _parse_return(self) -> ReturnClause:
        """Parse RETURN [DISTINCT] ReturnBody."""
        start = self._consume_kw("RETURN", "expected RETURN")
        distinct = bool(self._match_kw("DISTINCT"))

        items: list[ReturnItem] = []

        # RETURN *
        if self._check(TokenType.OP_MUL):
            star_tok = self._lexer.next()
            items.append(ReturnItem(
                expression=StarExpression(span=self._span(star_tok, star_tok)),
                span=self._span(star_tok, star_tok),
            ))
            return ReturnClause(
                items=items,
                distinct=distinct,
                span=self._span(start, star_tok),
            )

        # RETURN expr [AS alias], ...
        items.append(self._parse_return_item())
        while self._check(TokenType.COMMA):
            self._lexer.next()
            items.append(self._parse_return_item())

        end = self._lexer.peek()
        return ReturnClause(
            items=items,
            distinct=distinct,
            span=self._span(start, end),
        )

    def _parse_return_item(self) -> ReturnItem:
        """Parse ReturnItem → Expression [AS Identifier]."""
        expr = self._parse_expression()
        alias = None
        if self._check_kw("AS"):
            self._lexer.next()  # consume AS
            tok = self._consume(TokenType.IDENTIFIER, "expected alias name after AS")
            alias = tok.value
        return ReturnItem(expression=expr, alias=alias, span=expr.span)

    # ------------------------------------------------------------------
    # ORDER BY clause
    # ------------------------------------------------------------------

    def _parse_order_by(self) -> OrderByClause:
        """Parse ORDER BY OrderByItem [, OrderByItem]*."""
        start = self._consume_kw("ORDER", "expected ORDER")
        self._consume_kw("BY", "expected BY after ORDER")

        items: list[OrderByItem] = []
        items.append(self._parse_order_by_item())
        while self._check(TokenType.COMMA):
            self._lexer.next()
            items.append(self._parse_order_by_item())

        end = self._lexer.peek()
        return OrderByClause(items=items, span=self._span(start, end))

    def _parse_order_by_item(self) -> OrderByItem:
        """Parse OrderByItem → Expression [ASC|DESC]."""
        expr = self._parse_expression()
        direction = "ASC"
        if self._check_kw("ASC"):
            self._lexer.next()
            direction = "ASC"
        elif self._check_kw("DESC"):
            self._lexer.next()
            direction = "DESC"
        return OrderByItem(expression=expr, direction=direction, span=expr.span)

    # ------------------------------------------------------------------
    # Pattern parsing
    # ------------------------------------------------------------------

    def _parse_pattern(self) -> PatternPart:
        """Parse Pattern → NodePattern {RelPattern NodePattern}*.

        Returns a PatternPart: first node + chain of PatternElements.
        """
        first_node = self._parse_node_pattern()
        chain: list[PatternElement] = []

        # Parse chain of (RelPattern NodePattern)*
        while self._check(TokenType.OP_MINUS) or self._check(TokenType.ARROW_LEFT):
            rel = self._parse_rel_pattern()
            node = self._parse_node_pattern()
            rs = rel.span
            ns = node.span
            chain.append(PatternElement(
                rel=rel,
                node=node,
                span=Span(rs.start_line, rs.start_col, ns.end_line, ns.end_col),
            ))

        end_elem_span = chain[-1].node.span if chain else first_node.span
        fs = first_node.span
        return PatternPart(
            node=first_node,
            chain=chain,
            span=Span(fs.start_line, fs.start_col, end_elem_span.end_line, end_elem_span.end_col),
        )

    def _parse_node_pattern(self) -> NodePattern:
        """Parse NodePattern → ( [Variable] [: Label*] [{Properties}] )."""
        start = self._consume(TokenType.LPAREN, "expected '(' for node pattern")
        name: Optional[str] = None
        labels: list[str] = []
        properties: Optional[dict] = None

        # Optional variable name
        if self._check(TokenType.IDENTIFIER):
            name = self._lexer.next().value

        # Optional labels: :Label1, :Label2, ...
        while self._check(TokenType.COLON):
            self._lexer.next()  # consume :
            label_tok = self._consume_name("expected label name after ':'")
            labels.append(label_tok.value)

        # Optional properties map: { ... }
        if self._check(TokenType.LBRACE):
            properties = self._parse_properties()

        end = self._consume(TokenType.RPAREN, "expected ')' to close node pattern")
        return NodePattern(
            name=name,
            labels=labels,
            properties=properties,
            span=self._span(start, end),
        )

    def _parse_rel_pattern(self) -> RelPattern:
        """Parse RelPattern.

        Full forms:
          -[...]->  → Direction.RIGHT
          <-[...]-  → Direction.LEFT
          -[...]-   → Direction.BOTH

        Shorthand forms:
          -->   → Direction.RIGHT
          <--   → Direction.LEFT
          --    → Direction.BOTH
        """
        if self._check(TokenType.ARROW_LEFT):
            # Left-directed: <-[...]- or <-- (shorthand)
            start = self._lexer.next()  # consume <-
            name: Optional[str] = None
            types: list[str] = []
            properties: Optional[dict] = None

            if self._check(TokenType.OP_MINUS):
                # Shorthand: <-- (consume the -)
                end = self._lexer.next()
                return RelPattern(
                    name=None,
                    types=[],
                    direction=Direction.LEFT,
                    properties=None,
                    span=self._span(start, end),
                )

            # Full form: <-[...]-
            self._consume(TokenType.LBRACKET, "expected '[' for relationship details")
            name, types, properties = self._parse_rel_details()
            self._consume(TokenType.RBRACKET, "expected ']' after relationship details")
            end = self._consume(TokenType.OP_MINUS, "expected '-' after relationship")

            return RelPattern(
                name=name,
                types=types,
                direction=Direction.LEFT,
                properties=properties,
                span=self._span(start, end),
            )

        elif self._check(TokenType.OP_MINUS):
            # Right or both: -[...]-> or -[...]- or --> or --
            start = self._lexer.next()  # consume OP_MINUS

            # Shorthand: -->
            if self._check(TokenType.ARROW_RIGHT):
                end = self._lexer.next()
                return RelPattern(
                    name=None,
                    types=[],
                    direction=Direction.RIGHT,
                    properties=None,
                    span=self._span(start, end),
                )

            # Shorthand: --
            if self._check(TokenType.OP_MINUS):
                end = self._lexer.next()
                return RelPattern(
                    name=None,
                    types=[],
                    direction=Direction.BOTH,
                    properties=None,
                    span=self._span(start, end),
                )

            # Full form: -[...]->
            self._consume(TokenType.LBRACKET, "expected '[' for relationship details or shorthand arrow")
            name, types, properties = self._parse_rel_details()
            self._consume(TokenType.RBRACKET, "expected ']' after relationship details")

            if self._check(TokenType.ARROW_RIGHT):
                end = self._lexer.next()
                direction = Direction.RIGHT
            else:
                end = self._consume(TokenType.OP_MINUS, "expected '-' or '->' after relationship")
                direction = Direction.BOTH

            return RelPattern(
                name=name,
                types=types,
                direction=direction,
                properties=properties,
                span=self._span(start, end),
            )

        else:
            raise self._error("expected relationship pattern starting with '-' or '<-'", "relationship")

    def _parse_rel_details(self) -> tuple[Optional[str], list[str], Optional[dict]]:
        """Parse the interior of a relationship pattern: [Variable] [: Type*] [{Properties}].

        Assumes the opening '[' has already been consumed.
        """
        name: Optional[str] = None
        types: list[str] = []
        properties: Optional[dict] = None

        # Optional variable name (if next token is an identifier not followed by colon,
        # but we'll handle it generically)
        if self._check(TokenType.IDENTIFIER):
            # Look ahead to check if this is a variable or something else
            name = self._lexer.next().value

        # Optional type list: :Type1|Type2|...
        if self._check(TokenType.COLON):
            self._lexer.next()  # consume :
            # At least one type name
            type_tok = self._consume_name("expected type name after ':'")
            types.append(type_tok.value)
            while self._check(TokenType.PIPE):
                self._lexer.next()  # consume |
                type_tok = self._consume_name("expected type name after '|'")
                types.append(type_tok.value)

        # Optional properties
        if self._check(TokenType.LBRACE):
            properties = self._parse_properties()

        return name, types, properties

    def _parse_properties(self) -> dict:
        """Parse a property map: { key: value [, key: value]* }."""
        self._consume(TokenType.LBRACE, "expected '{'")
        props: dict = {}

        if not self._check(TokenType.RBRACE):
            props.update(self._parse_property_pair())
            while self._check(TokenType.COMMA):
                self._lexer.next()
                props.update(self._parse_property_pair())

        self._consume(TokenType.RBRACE, "expected '}'")
        return props

    def _parse_property_pair(self) -> dict:
        """Parse a single key: value pair, returns as {key: value_str}."""
        key_tok = self._consume(TokenType.IDENTIFIER, "expected property key")
        self._consume(TokenType.COLON, "expected ':' after property key")

        # Property values in our simplified AST are stored as raw token value strings
        if self._check(TokenType.STRING):
            val = self._lexer.next().value
        elif self._check(TokenType.INTEGER):
            val = self._lexer.next().value
        elif self._check(TokenType.FLOAT):
            val = self._lexer.next().value
        elif self._check_kw("TRUE"):
            self._lexer.next()
            val = "true"
        elif self._check_kw("FALSE"):
            self._lexer.next()
            val = "false"
        elif self._check_kw("NULL"):
            self._lexer.next()
            val = "null"
        else:
            raise self._error("expected property value", "literal value")

        return {key_tok.value: val}

    # ------------------------------------------------------------------
    # Expression parsing — recursive descent by precedence
    # ------------------------------------------------------------------

    def _parse_expression(self) -> Expression:
        """Entry point: parse any expression."""
        return self._parse_or()

    def _parse_or(self) -> Expression:
        """OrExpr → XorExpr {OR XorExpr}*."""
        left = self._parse_xor()
        start = self._lexer.peek()

        while self._check_kw("OR"):
            or_tok = self._lexer.next()
            right = self._parse_xor()
            left = BinaryOp(
                op="OR",
                left=left,
                right=right,
                span=self._compose_span(left.span, right.span),
            )

        return left

    def _parse_xor(self) -> Expression:
        """XorExpr → AndExpr {XOR AndExpr}*."""
        left = self._parse_and()

        while self._check_kw("XOR"):
            xor_tok = self._lexer.next()
            right = self._parse_and()
            left = BinaryOp(
                op="XOR",
                left=left,
                right=right,
                span=self._compose_span(left.span, right.span),
            )

        return left

    def _parse_and(self) -> Expression:
        """AndExpr → NotExpr {AND NotExpr}*."""
        left = self._parse_not()

        while self._check_kw("AND"):
            and_tok = self._lexer.next()
            right = self._parse_not()
            left = BinaryOp(
                op="AND",
                left=left,
                right=right,
                span=self._compose_span(left.span, right.span),
            )

        return left

    def _parse_not(self) -> Expression:
        """NotExpr → NOT NotExpr | ComparisonExpr.

        NOT is parsed at the unary level (_parse_unary) so it binds tighter
        than comparison operators. This method delegates directly to
        _parse_comparison.
        """
        return self._parse_comparison()

    def _parse_comparison(self) -> Expression:
        """ComparisonExpr → AddExpr [(op) AddExpr]*.

        Comparison operators: =, <>, <, >, <=, >=, =~, STARTS WITH, ENDS WITH,
        IN, IS NULL, IS NOT NULL.
        """
        left = self._parse_add()

        while True:
            # Standard binary comparisons
            if self._check(TokenType.OP_EQ):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op="=", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_NEQ):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op="<>", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_LT):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op="<", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_GT):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op=">", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_LTE):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op="<=", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_GTE):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op=">=", left=left, right=right, span=self._compose_span(left.span, right.span))
            elif self._check(TokenType.OP_CONTAINS):
                op = self._lexer.next()
                right = self._parse_add()
                left = BinaryOp(op="=~", left=left, right=right, span=self._compose_span(left.span, right.span))
            # STARTS WITH
            elif self._check_kw("STARTS"):
                starts_tok = self._lexer.next()
                self._consume_kw("WITH", "expected WITH after STARTS")
                right = self._parse_add()
                left = BinaryOp(op="STARTS WITH", left=left, right=right, span=self._compose_span(left.span, right.span))
            # ENDS WITH
            elif self._check_kw("ENDS"):
                ends_tok = self._lexer.next()
                self._consume_kw("WITH", "expected WITH after ENDS")
                right = self._parse_add()
                left = BinaryOp(op="ENDS WITH", left=left, right=right, span=self._compose_span(left.span, right.span))
            # IS NULL / IS NOT NULL
            elif self._check_kw("IS"):
                is_tok = self._lexer.next()
                if self._check_kw("NOT"):
                    self._lexer.next()  # consume NOT
                    self._consume_kw("NULL", "expected NULL after IS NOT")
                    left = UnaryOp(op="IS NOT NULL", operand=left, span=self._span(left.span, is_tok))
                else:
                    self._consume_kw("NULL", "expected NULL or NOT after IS")
                    left = UnaryOp(op="IS NULL", operand=left, span=self._span(left.span, is_tok))
            # IN
            elif self._check_kw("IN"):
                in_tok = self._lexer.next()
                right = self._parse_add()
                left = InExpression(expr=left, list=right, span=self._compose_span(left.span, right.span))
            else:
                break

        return left

    def _parse_add(self) -> Expression:
        """AddExpr → MulExpr {(+|-) MulExpr}*."""
        left = self._parse_mul()

        while self._check(TokenType.OP_PLUS) or self._check(TokenType.OP_MINUS):
            op = self._lexer.next()
            right = self._parse_mul()
            op_str = op.value
            left = BinaryOp(op=op_str, left=left, right=right, span=self._compose_span(left.span, right.span))

        return left

    def _parse_mul(self) -> Expression:
        """MulExpr → UnaryExpr {(*|/|%|^) UnaryExpr}*."""
        left = self._parse_unary()

        while self._check(TokenType.OP_MUL) or self._check(TokenType.OP_DIV) or \
              self._check(TokenType.OP_MOD) or self._check(TokenType.OP_POW):
            op = self._lexer.next()
            right = self._parse_unary()
            op_str = op.value
            left = BinaryOp(op=op_str, left=left, right=right, span=self._compose_span(left.span, right.span))

        return left

    def _parse_unary(self) -> Expression:
        """UnaryExpr → [-] AtomExpr | NOT AtomExpr.

        NOT is parsed here (unary level) so it binds tighter than
        comparison operators AND binary logical operators.
        """
        if self._check(TokenType.OP_MINUS):
            min_tok = self._lexer.next()
            operand = self._parse_unary()
            return UnaryOp(op="-", operand=operand, span=self._span(min_tok, operand.span))
        if self._check_kw("NOT"):
            not_tok = self._lexer.next()
            operand = self._parse_unary()
            return UnaryOp(op="NOT", operand=operand, span=self._span(not_tok, operand.span))
        return self._parse_atom()

    def _parse_atom(self) -> Expression:
        """AtomExpr → Literal | Identifier | FunctionCall | (Expression) | Parameter | ListLiteral | CASE | EXISTS.

        Also handles postfix property access (.identifier) chaining.
        """
        # String literal
        if self._check(TokenType.STRING):
            tok = self._lexer.next()
            return Literal(value=tok.value, span=self._span(tok, tok))

        # Integer literal
        if self._check(TokenType.INTEGER):
            tok = self._lexer.next()
            return Literal(value=tok.value, span=self._span(tok, tok))

        # Float literal
        if self._check(TokenType.FLOAT):
            tok = self._lexer.next()
            return Literal(value=tok.value, span=self._span(tok, tok))

        # Boolean TRUE
        if self._check_kw("TRUE"):
            tok = self._lexer.next()
            return Literal(value=True, span=self._span(tok, tok))

        # Boolean FALSE
        if self._check_kw("FALSE"):
            tok = self._lexer.next()
            return Literal(value=False, span=self._span(tok, tok))

        # NULL
        if self._check_kw("NULL"):
            tok = self._lexer.next()
            return Literal(value=None, span=self._span(tok, tok))

        # CASE expression
        if self._check_kw("CASE"):
            return self._parse_case_expression()

        # EXISTS subquery: EXISTS { MATCH ... }
        if self._check_kw("EXISTS"):
            exists_tok = self._lexer.next()  # consume EXISTS
            self._consume(TokenType.LBRACE, "expected '{' after EXISTS")
            inner_query = self._parse_subquery()
            end = self._consume(TokenType.RBRACE, "expected '}' after subquery")
            return SubqueryExpression(
                query=inner_query,
                exists=True,
                span=self._span(exists_tok, end),
            )

        # Parameter: $name
        if self._check(TokenType.PARAMETER):
            tok = self._lexer.next()
            return Parameter(name=tok.value, span=self._span(tok, tok))

        # Identifier or function call
        if self._check(TokenType.IDENTIFIER):
            return self._parse_identifier_expr()

        # Parenthesized expression
        if self._check(TokenType.LPAREN):
            start = self._lexer.next()  # consume (
            expr = self._parse_expression()
            self._consume(TokenType.RPAREN, "expected ')' after expression")
            # Return the expression directly (parenthesized is just grouping)
            return expr

        # List literal
        if self._check(TokenType.LBRACKET):
            return self._parse_list_literal()

        tok = self._lexer.peek()
        raise CypherSyntaxError(
            f"Unexpected token: '{tok.value}'",
            expected="expression",
            actual=tok.value,
            line=tok.line,
            col=tok.col,
        )

    def _parse_identifier_expr(self) -> Expression:
        """Parse identifier or chained expression: name | name(...) | name.prop.prop."""
        tok = self._consume(TokenType.IDENTIFIER, "expected identifier")
        expr: Expression = Identifier(name=tok.value, span=self._span(tok, tok))

        # Function call: identifier ( [DISTINCT] args... )
        if self._check(TokenType.LPAREN):
            expr = self._parse_function_call(expr)

        # Property access chain: .name
        while self._check(TokenType.DOT):
            self._lexer.next()  # consume .
            key_tok = self._consume(TokenType.IDENTIFIER, "expected property name after '.'")
            expr = PropertyAccess(
                obj=expr,
                key=key_tok.value,
                span=self._span(expr.span, key_tok),
            )

        return expr

    def _parse_function_call(self, func_expr: Identifier) -> FunctionCall:
        """Parse function call arguments: ( [DISTINCT] expr [, expr]* ).

        Assumes the opening '(' is already peeked as the next token.
        """
        name = func_expr.name
        start_func = func_expr.span

        self._consume(TokenType.LPAREN, "expected '(' for function call")
        distinct = bool(self._match_kw("DISTINCT"))

        args: list[Expression] = []
        if not self._check(TokenType.RPAREN):
            args.append(self._parse_expression())
            while self._check(TokenType.COMMA):
                self._lexer.next()
                args.append(self._parse_expression())

        end = self._consume(TokenType.RPAREN, "expected ')' after function arguments")
        return FunctionCall(
            name=name,
            args=args,
            distinct=distinct,
            span=self._span(start_func, end),
        )

    def _parse_list_literal(self) -> ListLiteral:
        """Parse list literal: [ expr [, expr]* ]."""
        start = self._consume(TokenType.LBRACKET, "expected '['")
        elements: list[Expression] = []

        if not self._check(TokenType.RBRACKET):
            elements.append(self._parse_expression())
            while self._check(TokenType.COMMA):
                self._lexer.next()
                elements.append(self._parse_expression())

        end = self._consume(TokenType.RBRACKET, "expected ']'")
        return ListLiteral(elements=elements, span=self._span(start, end))

    # ------------------------------------------------------------------
    # CASE expression
    # ------------------------------------------------------------------

    def _parse_case_expression(self) -> CaseExpression:
        """Parse CASE [expr] WHEN cond THEN result [...] [ELSE default] END."""
        start = self._consume_kw("CASE", "expected CASE")

        # Optional expression: if the next token is WHEN, it's a search CASE
        expression = None
        if not self._check_kw("WHEN"):
            expression = self._parse_expression()

        # WHEN ... THEN ... (at least one pair)
        if not self._check_kw("WHEN"):
            raise self._error("expected WHEN in CASE expression", "WHEN")

        cases: list[tuple[Expression, Expression]] = []
        while self._check_kw("WHEN"):
            self._lexer.next()  # consume WHEN
            when_expr = self._parse_expression()
            self._consume_kw("THEN", "expected THEN after WHEN")
            then_expr = self._parse_expression()
            cases.append((when_expr, then_expr))

        # Optional ELSE
        default = None
        if self._check_kw("ELSE"):
            self._lexer.next()  # consume ELSE
            default = self._parse_expression()

        end = self._consume_kw("END", "expected END")
        return CaseExpression(
            expression=expression,
            cases=cases,
            default=default,
            span=self._span(start, end),
        )

    # ------------------------------------------------------------------
    # Subquery
    # ------------------------------------------------------------------

    def _parse_subquery(self) -> Query:
        """Parse a subquery: MATCH ... [WHERE ...] [RETURN ...]

        Used inside EXISTS { ... } or bare { ... } expressions.
        RETURN is optional inside a subquery.
        """
        start_tok = self._lexer.peek()

        # Parse MATCH or OPTIONAL MATCH
        match = self._parse_match()

        # Parse additional OPTIONAL MATCH clauses
        optional_matches: list[MatchClause] = []
        while self._check_kw("OPTIONAL"):
            optional_matches.append(self._parse_match())

        # Parse WHERE (optional)
        where = None
        if self._check_kw("WHERE"):
            where = self._parse_where()

        # RETURN is optional in subquery
        return_clause: ReturnClause
        if self._check_kw("RETURN"):
            return_clause = self._parse_return()
        else:
            return_clause = ReturnClause(items=[], span=Span(0, 0, 0, 0))

        return Query(
            match=match,
            optional_matches=optional_matches,
            where=where,
            return_clause=return_clause,
            span=self._span(start_tok, self._lexer.peek()),
        )

    # ------------------------------------------------------------------
    # UNWIND clause
    # ------------------------------------------------------------------

    def _parse_unwind(self) -> UnwindClause:
        """Parse UNWIND expression AS variable."""
        start = self._consume_kw("UNWIND", "expected UNWIND")
        expression = self._parse_expression()
        self._consume_kw("AS", "expected AS after UNWIND expression")
        var_tok = self._consume(TokenType.IDENTIFIER, "expected variable name after AS")
        end = self._lexer.peek()
        return UnwindClause(
            expression=expression,
            variable=var_tok.value,
            span=self._span(start, var_tok),
        )

    # ------------------------------------------------------------------
    # WITH clause
    # ------------------------------------------------------------------

    def _parse_with(self) -> WithClause:
        """Parse WITH ReturnItem [, ReturnItem]* [WHERE Expression]."""
        start = self._consume_kw("WITH", "expected WITH")

        items: list[ReturnItem] = []
        items.append(self._parse_return_item())
        while self._check(TokenType.COMMA):
            self._lexer.next()
            items.append(self._parse_return_item())

        where = None
        if self._check_kw("WHERE"):
            where = self._parse_where()

        end = self._lexer.peek()
        return WithClause(
            items=items,
            where=where,
            span=self._span(start, end),
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _span(self, start: Token, end: Token) -> Span:
        """Create a Span covering from *start* to *end*.

        Accepts either Token objects (with .line/.col) or Span objects
        (with .start_line/.start_col or .end_line/.end_col).
        """
        if hasattr(start, 'start_line'):
            start_line, start_col = start.start_line, start.start_col  # type: ignore[union-attr]
        else:
            start_line, start_col = start.line, start.col  # type: ignore[union-attr]
        if hasattr(end, 'start_line'):
            end_line, end_col = end.end_line, end.end_col  # type: ignore[union-attr]
        else:
            end_line = end.line  # type: ignore[union-attr]
            end_col = end.col + len(end.value)  # type: ignore[union-attr]
        return Span(
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
        )

    @staticmethod
    def _compose_span(left: Span, right: Span) -> Span:
        """Combine two Spans into one covering from left start to right end."""
        return Span(
            start_line=left.start_line,
            start_col=left.start_col,
            end_line=right.end_line,
            end_col=right.end_col,
        )

    def _error(self, message: str, expected: str) -> CypherSyntaxError:
        """Raise a CypherSyntaxError for the current peek token."""
        tok = self._lexer.peek()
        actual = tok.value if tok.value else "end of input"
        return CypherSyntaxError(
            message=message,
            expected=expected,
            actual=actual,
            line=tok.line,
            col=tok.col,
        )

    def _consume_name(self, message: str) -> Token:
        """Consume an IDENTIFIER or KEYWORD token (used for label/type names)."""
        tok = self._lexer.peek()
        if tok.type == TokenType.IDENTIFIER or tok.type == TokenType.KEYWORD:
            return self._lexer.next()
        raise self._error(message, "identifier or keyword")

    def _check(self, tt: TokenType) -> bool:
        """Return True if the next token has type *tt* (without consuming)."""
        return self._lexer.peek().type == tt

    def _check_kw(self, kw: str) -> bool:
        """Return True if the next token is a KEYWORD with value *kw*."""
        tok = self._lexer.peek()
        return tok.type == TokenType.KEYWORD and tok.keyword == kw

    def _consume(self, tt: TokenType, message: str) -> Token:
        """Consume and return the next token. Raise on type mismatch."""
        if self._check(tt):
            return self._lexer.next()
        raise self._error(message, tt.name)

    def _consume_kw(self, kw: str, message: str) -> Token:
        """Consume and return the next token, asserting it is keyword *kw*."""
        if self._check_kw(kw):
            return self._lexer.next()
        raise self._error(message, kw)

    def _match(self, tt: TokenType) -> bool:
        """If the next token has type *tt*, consume and return True."""
        if self._check(tt):
            self._lexer.next()
            return True
        return False

    def _match_kw(self, kw: str) -> bool:
        """If the next token is keyword *kw*, consume and return True."""
        if self._check_kw(kw):
            self._lexer.next()
            return True
        return False
