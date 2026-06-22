"""Tests for cypher/lexer.py — TokenType, Token, Lexer."""

import pytest
from tws_graph.cypher.lexer import TokenType, Token, Lexer
from tws_graph.cypher.errors import CypherLexerError


# ============================================================================
# TokenType
# ============================================================================

class TestTokenType:
    """TokenType enum completeness and basic properties."""

    def test_has_keyword(self):
        assert hasattr(TokenType, "KEYWORD")

    def test_has_identifier(self):
        assert hasattr(TokenType, "IDENTIFIER")

    def test_has_literals(self):
        for name in ["STRING", "INTEGER", "FLOAT", "PARAMETER"]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_comparison_operators(self):
        for name in ["OP_EQ", "OP_NEQ", "OP_LT", "OP_GT", "OP_LTE", "OP_GTE"]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_arithmetic_operators(self):
        for name in ["OP_PLUS", "OP_MINUS", "OP_MUL", "OP_DIV", "OP_MOD", "OP_POW"]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_special_operators(self):
        for name in ["OP_CONTAINS", "OP_STARTS", "OP_ENDS"]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_punctuation(self):
        for name in [
            "DOT", "COMMA", "LPAREN", "RPAREN",
            "LBRACKET", "RBRACKET", "LBRACE", "RBRACE",
            "COLON", "SEMICOLON", "PIPE",
        ]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_arrows_and_dash(self):
        for name in ["ARROW_LEFT", "ARROW_RIGHT", "DASH"]:
            assert hasattr(TokenType, name), f"Missing {name}"

    def test_has_eof(self):
        assert hasattr(TokenType, "EOF")

    def test_count_members(self):
        count = len(TokenType)
        assert 30 <= count <= 40, f"Expected ~35 token types, got {count}"


# ============================================================================
# Token
# ============================================================================

class TestToken:
    """Token dataclass — frozen, keyword property."""

    def test_construct_keyword_token(self):
        t = Token(TokenType.KEYWORD, "MATCH", 0, 1, 1)
        assert t.type == TokenType.KEYWORD
        assert t.value == "MATCH"
        assert t.pos == 0
        assert t.line == 1
        assert t.col == 1

    def test_keyword_property_uppercase(self):
        t = Token(TokenType.KEYWORD, "match", 0, 1, 1)
        assert t.keyword == "MATCH"

    def test_keyword_property_already_upper(self):
        t = Token(TokenType.KEYWORD, "MATCH", 0, 1, 1)
        assert t.keyword == "MATCH"

    def test_keyword_property_mixed_case(self):
        t = Token(TokenType.KEYWORD, "MaTcH", 0, 1, 1)
        assert t.keyword == "MATCH"

    def test_frozen_cannot_modify(self):
        t = Token(TokenType.KEYWORD, "MATCH", 0, 1, 1)
        with pytest.raises(Exception):
            t.value = "WHERE"  # type: ignore

    def test_eof_token(self):
        t = Token(TokenType.EOF, "", 100, 5, 1)
        assert t.type == TokenType.EOF
        assert t.value == ""

    def test_identifier_token(self):
        t = Token(TokenType.IDENTIFIER, "myVar", 10, 2, 3)
        assert t.type == TokenType.IDENTIFIER
        assert t.value == "myVar"
        assert t.pos == 10
        assert t.line == 2
        assert t.col == 3


# ============================================================================
# Lexer — empty / whitespace / EOF
# ============================================================================

class TestLexerEmpty:
    """Empty input and whitespace-only."""

    def test_empty_string_returns_eof(self):
        lexer = Lexer("")
        tok = lexer.next()
        assert tok.type == TokenType.EOF
        assert tok.pos == 0

    def test_eof_repeats(self):
        lexer = Lexer("")
        assert lexer.next().type == TokenType.EOF
        assert lexer.next().type == TokenType.EOF
        assert lexer.next().type == TokenType.EOF

    def test_whitespace_only(self):
        lexer = Lexer("   \t\n\r\n  ")
        tok = lexer.next()
        assert tok.type == TokenType.EOF


# ============================================================================
# Lexer — keywords
# ============================================================================

class TestLexerKeywords:
    """Keyword recognition and case insensitivity."""

    KEYWORDS = [
        "MATCH", "WHERE", "RETURN", "ORDER", "BY",
        "SKIP", "LIMIT", "AS", "DISTINCT",
        "AND", "OR", "NOT", "IS", "NULL", "TRUE", "FALSE",
        "ASC", "DESC", "CONTAINS", "STARTS", "ENDS", "IN",
    ]

    def test_keywords_recognized(self):
        for kw in self.KEYWORDS:
            lexer = Lexer(kw)
            tok = lexer.next()
            assert tok.type == TokenType.KEYWORD, f"Failed for {kw}"
            assert tok.keyword == kw, f"Keyword value mismatch for {kw}"

    def test_keywords_case_insensitive(self):
        variants = [
            ("match", "MATCH"), ("Match", "MATCH"), ("MATCH", "MATCH"),
            ("where", "WHERE"), ("WHERE", "WHERE"),
            ("return", "RETURN"), ("Return", "RETURN"),
            ("true", "TRUE"), ("True", "TRUE"),
            ("false", "FALSE"), ("null", "NULL"), ("Null", "NULL"),
        ]
        for input_str, expected_upper in variants:
            lexer = Lexer(input_str)
            tok = lexer.next()
            assert tok.type == TokenType.KEYWORD
            assert tok.keyword == expected_upper
            assert tok.value == input_str


# ============================================================================
# Lexer — identifiers
# ============================================================================

class TestLexerIdentifiers:
    """Identifier recognition."""

    def test_simple_identifier(self):
        lexer = Lexer("myVar")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "myVar"

    def test_underscore_prefix(self):
        lexer = Lexer("_private")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "_private"

    def test_snake_case(self):
        lexer = Lexer("snake_case_var")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "snake_case_var"

    def test_camel_case(self):
        lexer = Lexer("camelCaseVar")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "camelCaseVar"

    def test_identifier_with_digits(self):
        lexer = Lexer("var123")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "var123"

    def test_identifier_starts_with_underscore_then_digit(self):
        lexer = Lexer("_123abc")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "_123abc"


# ============================================================================
# Lexer — strings
# ============================================================================

class TestLexerStrings:
    """String literal recognition and escaping."""

    def test_single_quoted_string(self):
        lexer = Lexer("'hello'")
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == "hello"

    def test_double_quoted_string(self):
        lexer = Lexer('"world"')
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == "world"

    def test_empty_single_quoted(self):
        lexer = Lexer("''")
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == ""

    def test_empty_double_quoted(self):
        lexer = Lexer('""')
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == ""

    def test_escaped_single_quote(self):
        lexer = Lexer("'it\\'s'")
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == "it's"

    def test_escaped_double_quote(self):
        lexer = Lexer('"a\\"b"')
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert tok.value == 'a"b'

    def test_escaped_backslash(self):
        lexer = Lexer("'line\\\\nbreak'")
        tok = lexer.next()
        assert tok.type == TokenType.STRING
        assert "\\" in tok.value

    def test_unclosed_single_quote(self):
        lexer = Lexer("'unclosed")
        with pytest.raises(CypherLexerError):
            lexer.next()

    def test_unclosed_double_quote(self):
        lexer = Lexer('"unclosed')
        with pytest.raises(CypherLexerError):
            lexer.next()

    def test_string_position(self):
        lexer = Lexer("x = 'hello'")
        lexer.next()  # x
        lexer.next()  # =
        tok = lexer.next()  # 'hello'
        assert tok.type == TokenType.STRING
        assert tok.line == 1
        assert tok.col >= 5


# ============================================================================
# Lexer — numbers
# ============================================================================

class TestLexerNumbers:
    """Integer and float recognition."""

    def test_integer_zero(self):
        lexer = Lexer("0")
        tok = lexer.next()
        assert tok.type == TokenType.INTEGER
        assert tok.value == "0"

    def test_integer_positive(self):
        lexer = Lexer("123")
        tok = lexer.next()
        assert tok.type == TokenType.INTEGER
        assert tok.value == "123"

    def test_integer_large(self):
        lexer = Lexer("42")
        tok = lexer.next()
        assert tok.type == TokenType.INTEGER
        assert tok.value == "42"

    def test_float_with_decimal(self):
        lexer = Lexer("3.14")
        tok = lexer.next()
        assert tok.type == TokenType.FLOAT
        assert tok.value == "3.14"

    def test_float_zero_point(self):
        lexer = Lexer("0.5")
        tok = lexer.next()
        assert tok.type == TokenType.FLOAT
        assert tok.value == "0.5"

    def test_float_leading_dot(self):
        lexer = Lexer(".5")
        tok = lexer.next()
        assert tok.type == TokenType.FLOAT
        assert tok.value == ".5"

    def test_integer_followed_by_keyword(self):
        lexer = Lexer("10LIMIT")
        tok = lexer.next()
        assert tok.type == TokenType.INTEGER
        assert tok.value == "10"
        tok2 = lexer.next()
        assert tok2.type == TokenType.KEYWORD
        assert tok2.keyword == "LIMIT"


# ============================================================================
# Lexer — parameters
# ============================================================================

class TestLexerParameters:
    """Parameter placeholder recognition."""

    def test_simple_parameter(self):
        lexer = Lexer("$param")
        tok = lexer.next()
        assert tok.type == TokenType.PARAMETER
        assert tok.value == "$param"

    def test_parameter_camel_case(self):
        lexer = Lexer("$myParam")
        tok = lexer.next()
        assert tok.type == TokenType.PARAMETER
        assert tok.value == "$myParam"


# ============================================================================
# Lexer — operators
# ============================================================================

class TestLexerOperators:
    """All operator tokens."""

    def test_eq(self):
        lexer = Lexer("=")
        tok = lexer.next()
        assert tok.type == TokenType.OP_EQ
        assert tok.value == "="

    def test_neq(self):
        lexer = Lexer("<>")
        tok = lexer.next()
        assert tok.type == TokenType.OP_NEQ
        assert tok.value == "<>"

    def test_lt(self):
        lexer = Lexer("<")
        tok = lexer.next()
        assert tok.type == TokenType.OP_LT
        assert tok.value == "<"

    def test_gt(self):
        lexer = Lexer(">")
        tok = lexer.next()
        assert tok.type == TokenType.OP_GT
        assert tok.value == ">"

    def test_lte(self):
        lexer = Lexer("<=")
        tok = lexer.next()
        assert tok.type == TokenType.OP_LTE
        assert tok.value == "<="

    def test_gte(self):
        lexer = Lexer(">=")
        tok = lexer.next()
        assert tok.type == TokenType.OP_GTE
        assert tok.value == ">="

    def test_plus(self):
        lexer = Lexer("+")
        tok = lexer.next()
        assert tok.type == TokenType.OP_PLUS

    def test_minus(self):
        lexer = Lexer("-")
        tok = lexer.next()
        assert tok.type == TokenType.OP_MINUS

    def test_mul(self):
        lexer = Lexer("*")
        tok = lexer.next()
        assert tok.type == TokenType.OP_MUL

    def test_div(self):
        lexer = Lexer("/")
        tok = lexer.next()
        assert tok.type == TokenType.OP_DIV

    def test_mod(self):
        lexer = Lexer("%")
        tok = lexer.next()
        assert tok.type == TokenType.OP_MOD

    def test_pow(self):
        lexer = Lexer("^")
        tok = lexer.next()
        assert tok.type == TokenType.OP_POW

    def test_contains(self):
        lexer = Lexer("=~")
        tok = lexer.next()
        assert tok.type == TokenType.OP_CONTAINS
        assert tok.value == "=~"

    def test_operators_no_whitespace(self):
        lexer = Lexer("=<>")
        tok1 = lexer.next()
        tok2 = lexer.next()
        assert tok1.type in (TokenType.OP_EQ, TokenType.OP_NEQ)
        assert tok2.type in (TokenType.OP_NEQ, TokenType.OP_GT)


# ============================================================================
# Lexer — punctuation
# ============================================================================

class TestLexerPunctuation:
    """Punctuation token recognition."""

    def test_dot(self):
        lexer = Lexer(".")
        tok = lexer.next()
        assert tok.type == TokenType.DOT

    def test_comma(self):
        lexer = Lexer(",")
        tok = lexer.next()
        assert tok.type == TokenType.COMMA

    def test_lparen(self):
        lexer = Lexer("(")
        tok = lexer.next()
        assert tok.type == TokenType.LPAREN

    def test_rparen(self):
        lexer = Lexer(")")
        tok = lexer.next()
        assert tok.type == TokenType.RPAREN

    def test_lbracket(self):
        lexer = Lexer("[")
        tok = lexer.next()
        assert tok.type == TokenType.LBRACKET

    def test_rbracket(self):
        lexer = Lexer("]")
        tok = lexer.next()
        assert tok.type == TokenType.RBRACKET

    def test_lbrace(self):
        lexer = Lexer("{")
        tok = lexer.next()
        assert tok.type == TokenType.LBRACE

    def test_rbrace(self):
        lexer = Lexer("}")
        tok = lexer.next()
        assert tok.type == TokenType.RBRACE

    def test_colon(self):
        lexer = Lexer(":")
        tok = lexer.next()
        assert tok.type == TokenType.COLON

    def test_semicolon(self):
        lexer = Lexer(";")
        tok = lexer.next()
        assert tok.type == TokenType.SEMICOLON

    def test_pipe(self):
        lexer = Lexer("|")
        tok = lexer.next()
        assert tok.type == TokenType.PIPE


# ============================================================================
# Lexer — arrows
# ============================================================================

class TestLexerArrows:
    """Arrow and dash recognition."""

    def test_arrow_left(self):
        lexer = Lexer("<-")
        tok = lexer.next()
        assert tok.type == TokenType.ARROW_LEFT
        assert tok.value == "<-"

    def test_arrow_right(self):
        lexer = Lexer("->")
        tok = lexer.next()
        assert tok.type == TokenType.ARROW_RIGHT
        assert tok.value == "->"

    def test_dash_alone(self):
        lexer = Lexer("-")
        tok = lexer.next()
        assert tok.type == TokenType.OP_MINUS
        assert tok.value == "-"

    def test_dash_followed_by_greater(self):
        lexer = Lexer("->")
        tok = lexer.next()
        assert tok.type == TokenType.ARROW_RIGHT

    def test_less_followed_by_dash(self):
        lexer = Lexer("<-")
        tok = lexer.next()
        assert tok.type == TokenType.ARROW_LEFT


# ============================================================================
# Lexer — comments
# ============================================================================

class TestLexerComments:
    """Comment skipping (line and block)."""

    def test_line_comment_skipped(self):
        lexer = Lexer("// this is a comment\nMATCH")
        tok = lexer.next()
        assert tok.type == TokenType.KEYWORD
        assert tok.keyword == "MATCH"

    def test_line_comment_eof(self):
        lexer = Lexer("// comment at end of file")
        tok = lexer.next()
        assert tok.type == TokenType.EOF

    def test_line_comment_before_code(self):
        lexer = Lexer("// header\n// more header\nRETURN x")
        tok = lexer.next()
        assert tok.type == TokenType.KEYWORD
        assert tok.keyword == "RETURN"

    def test_block_comment_simple(self):
        lexer = Lexer("/* block */ MATCH")
        tok = lexer.next()
        assert tok.type == TokenType.KEYWORD
        assert tok.keyword == "MATCH"

    def test_block_comment_multiline(self):
        lexer = Lexer("/* line 1\nline 2\nline 3 */\nMATCH")
        tok = lexer.next()
        assert tok.type == TokenType.KEYWORD
        assert tok.keyword == "MATCH"

    def test_block_comment_empty(self):
        lexer = Lexer("/**/MATCH")
        tok = lexer.next()
        assert tok.type == TokenType.KEYWORD
        assert tok.keyword == "MATCH"

    def test_block_comment_unclosed(self):
        lexer = Lexer("/* unclosed")
        with pytest.raises(CypherLexerError):
            lexer.next()

    def test_line_comment_then_newline_then_code(self):
        lexer = Lexer("// comment\nx")
        tok = lexer.next()
        assert tok.type == TokenType.IDENTIFIER
        assert tok.value == "x"


# ============================================================================
# Lexer — peek / next / iteration
# ============================================================================

class TestLexerPeekNext:
    """Peek and next semantics."""

    def test_peek_does_not_consume(self):
        lexer = Lexer("MATCH")
        tok1 = lexer.peek()
        tok2 = lexer.peek()
        assert tok1.type == TokenType.KEYWORD
        assert tok1 is tok2  # same cached token

    def test_peek_then_next_consumes(self):
        lexer = Lexer("MATCH WHERE")
        tok1 = lexer.peek()
        assert tok1.keyword == "MATCH"
        tok2 = lexer.next()
        assert tok2.keyword == "MATCH"
        tok3 = lexer.next()
        assert tok3.keyword == "WHERE"

    def test_iteration_protocol(self):
        lexer = Lexer("a b c")
        tokens = list(lexer)
        assert len(tokens) == 3
        assert all(t.type == TokenType.IDENTIFIER for t in tokens)
        assert [t.value for t in tokens] == ["a", "b", "c"]

    def test_iteration_includes_eof(self):
        lexer = Lexer("x")
        tokens = list(lexer)
        assert len(tokens) == 1
        # After iteration, next() returns EOF
        assert lexer.next().type == TokenType.EOF

    def test_peek_at_eof(self):
        lexer = Lexer("x")
        lexer.next()  # consume 'x'
        tok = lexer.peek()
        assert tok.type == TokenType.EOF
        tok2 = lexer.peek()
        assert tok is tok2  # cached

    def test_next_at_eof(self):
        lexer = Lexer("x")
        lexer.next()
        assert lexer.next().type == TokenType.EOF
        assert lexer.next().type == TokenType.EOF


# ============================================================================
# Lexer — position tracking
# ============================================================================

class TestLexerPositions:
    """Verify line/col/pos accuracy."""

    def test_start_position(self):
        lexer = Lexer("MATCH")
        tok = lexer.next()
        assert tok.line == 1
        assert tok.col == 1
        assert tok.pos == 0

    def test_position_after_whitespace(self):
        lexer = Lexer("  MATCH")
        tok = lexer.next()
        assert tok.line == 1
        assert tok.col == 3
        assert tok.pos == 2

    def test_multiline_position(self):
        lexer = Lexer("x\ny\nz")
        tok1 = lexer.next()
        assert tok1.line == 1
        assert tok1.col == 1
        tok2 = lexer.next()
        assert tok2.line == 2
        assert tok2.col == 1
        tok3 = lexer.next()
        assert tok3.line == 3
        assert tok3.col == 1

    def test_pos_starts_at_zero(self):
        lexer = Lexer("abc")
        tok = lexer.next()
        assert tok.pos == 0

    def test_pos_increments(self):
        lexer = Lexer("abc def")
        tok1 = lexer.next()
        assert tok1.pos == 0
        tok2 = lexer.next()
        assert tok2.pos == 4  # "abc def" — pos 0:'a',4:'d'

    def test_col_resets_per_line(self):
        lexer = Lexer("x\n y")
        tok1 = lexer.next()
        assert tok1.line == 1
        assert tok1.col == 1
        tok2 = lexer.next()
        assert tok2.line == 2
        assert tok2.col == 2  # space then 'y'

    def test_line_increment_tracks_newlines(self):
        lexer = Lexer("a\nb\n\nc")
        toks = []
        while True:
            t = lexer.next()
            if t.type == TokenType.EOF:
                break
            toks.append(t)
        assert toks[0].line == 1
        assert toks[1].line == 2
        assert toks[2].line == 4  # skipped blank line 3


# ============================================================================
# Lexer — error conditions
# ============================================================================

class TestLexerErrors:
    """Error handling — illegal characters, unterminated delimiters."""

    def test_illegal_character_at_sign(self):
        lexer = Lexer("@")
        with pytest.raises(CypherLexerError) as exc_info:
            lexer.next()
        e = exc_info.value
        assert e.line == 1
        assert e.col == 1
        assert e.pos == 0

    def test_illegal_character_with_message(self):
        lexer = Lexer("MATCH @n")
        lexer.next()  # consume MATCH
        with pytest.raises(CypherLexerError) as exc_info:
            lexer.next()
        e = exc_info.value
        assert "@" in str(e).lower() or "unexpected" in str(e).lower()

    def test_unclosed_block_comment_position(self):
        lexer = Lexer("/* start of block\nthat never ends")
        with pytest.raises(CypherLexerError) as exc_info:
            lexer.next()
        e = exc_info.value
        assert e.line >= 1

    def test_identifier_cannot_start_with_digit(self):
        lexer = Lexer("123abc")
        tok = lexer.next()
        if tok.type == TokenType.INTEGER:
            assert tok.value == "123"
            tok2 = lexer.next()
            assert tok2.type == TokenType.IDENTIFIER
            assert tok2.value == "abc"

    def test_error_is_cypher_error_subclass(self):
        lexer = Lexer("@")
        with pytest.raises(CypherLexerError):
            lexer.next()


# ============================================================================
# Lexer — integration scenarios
# ============================================================================

class TestLexerIntegration:
    """Realistic Cypher queries."""

    def test_simple_match_return(self):
        query = "MATCH (n:Function) RETURN n.name, n.file_path"
        lexer = Lexer(query)
        tokens = []
        while True:
            tok = lexer.next()
            if tok.type == TokenType.EOF:
                break
            tokens.append(tok)

        types = [t.type for t in tokens]
        assert types[0] == TokenType.KEYWORD      # MATCH
        assert types[1] == TokenType.LPAREN         # (
        assert types[2] == TokenType.IDENTIFIER     # n
        assert types[3] == TokenType.COLON          # :
        assert types[4] == TokenType.IDENTIFIER     # Function
        assert types[5] == TokenType.RPAREN         # )
        assert types[6] == TokenType.KEYWORD        # RETURN
        assert types[7] == TokenType.IDENTIFIER     # n
        assert types[8] == TokenType.DOT            # .
        assert types[9] == TokenType.IDENTIFIER     # name
        assert types[10] == TokenType.COMMA         # ,
        assert types[11] == TokenType.IDENTIFIER    # n
        assert types[12] == TokenType.DOT           # .
        assert types[13] == TokenType.IDENTIFIER    # file_path

    def test_where_order_limit(self):
        query = "WHERE f.language = 'python' ORDER BY f.name ASC LIMIT 10"
        lexer = Lexer(query)
        tokens = []
        while True:
            tok = lexer.next()
            if tok.type == TokenType.EOF:
                break
            tokens.append(tok)

        types = [t.type for t in tokens]
        assert TokenType.KEYWORD in types       # WHERE, ORDER, BY, ASC, LIMIT
        assert TokenType.STRING in types         # 'python'
        assert TokenType.INTEGER in types        # 10

    def test_multiline_query_positions(self):
        query = (
            "MATCH (n)\n"
            "WHERE n.name IS NOT NULL\n"
            "RETURN n\n"
            "ORDER BY n.name\n"
            "SKIP 5\n"
            "LIMIT 10"
        )
        lexer = Lexer(query)
        tokens = []
        while True:
            tok = lexer.next()
            if tok.type == TokenType.EOF:
                break
            tokens.append(tok)

        lines_seen = set()
        for tok in tokens:
            lines_seen.add(tok.line)
        assert 1 in lines_seen
        assert 6 in lines_seen
        assert len(lines_seen) >= 3

    def test_complete_query(self):
        query = (
            "MATCH (f:Function:Python)\n"
            "// find only test functions\n"
            "WHERE f.name STARTS WITH 'test_'\n"
            "  AND f.file_path ENDS WITH '.py'\n"
            "RETURN f.name AS name,\n"
            "       f.file_path AS path\n"
            "ORDER BY name ASC\n"
            "SKIP 0\n"
            "LIMIT 20"
        )
        lexer = Lexer(query)
        tokens = []
        while True:
            tok = lexer.next()
            if tok.type == TokenType.EOF:
                break
            tokens.append(tok)

        type_names = [t.type for t in tokens]
        assert TokenType.COLON in type_names
        assert TokenType.STRING in type_names    # 'test_', '.py'
        assert TokenType.COMMA in type_names
        assert TokenType.DOT in type_names
        assert TokenType.INTEGER in type_names   # 0, 20
