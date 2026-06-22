"""Cypher lexer — tokeniser for the Cypher query language.

Per design-cypher-engine.md sections 3.1-3.4.

Design: state-machine driven, no regex engine. This keeps error-recovery
simple and source-position tracking accurate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from .errors import CypherLexerError


# ============================================================================
# Keywords set
# ============================================================================

_KEYWORDS: set[str] = {
    "MATCH", "OPTIONAL", "WHERE", "RETURN", "ORDER", "BY",
    "SKIP", "LIMIT", "AS", "DISTINCT",
    "AND", "OR", "XOR", "NOT", "IS", "NULL", "TRUE", "FALSE",
    "ASC", "DESC", "CONTAINS", "STARTS", "ENDS", "WITH", "IN",
    "UNION", "ALL", "CASE", "WHEN", "THEN", "ELSE", "END",
    "UNWIND", "EXISTS",
}


# ============================================================================
# TokenType
# ============================================================================

class TokenType(Enum):
    """Every token kind recognised by the Cypher lexer."""

    # Keywords (concrete keyword stored in Token.value)
    KEYWORD = auto()

    # Identifiers and literals
    IDENTIFIER = auto()
    STRING = auto()
    INTEGER = auto()
    FLOAT = auto()
    PARAMETER = auto()

    # Comparison operators
    OP_EQ = auto()          # =
    OP_NEQ = auto()         # <>
    OP_LT = auto()          # <
    OP_GT = auto()          # >
    OP_LTE = auto()         # <=
    OP_GTE = auto()         # >=

    # Arithmetic operators
    OP_PLUS = auto()        # +
    OP_MINUS = auto()       # -
    OP_MUL = auto()         # *
    OP_DIV = auto()         # /
    OP_MOD = auto()         # %
    OP_POW = auto()         # ^

    # Special Cypher operators
    OP_CONTAINS = auto()    # =~
    OP_STARTS = auto()      # STARTS WITH (keywords lexed separately)
    OP_ENDS = auto()        # ENDS WITH   (keywords lexed separately)

    # Punctuation
    DOT = auto()            # .
    COMMA = auto()          # ,
    LPAREN = auto()         # (
    RPAREN = auto()         # )
    LBRACKET = auto()       # [
    RBRACKET = auto()       # ]
    LBRACE = auto()         # {
    RBRACE = auto()         # }
    COLON = auto()          # :
    SEMICOLON = auto()      # ;
    PIPE = auto()           # |

    # Arrows and dash
    ARROW_LEFT = auto()     # <-
    ARROW_RIGHT = auto()    # ->
    DASH = auto()           # -  (alone, not part of arrow)

    # Special
    EOF = auto()            # end of input


# ============================================================================
# Token
# ============================================================================

@dataclass(frozen=True)
class Token:
    """A single lexical token with source-position metadata.

    Immutable (frozen=True) so that peek() can safely return the same
    object on repeated calls.
    """

    type: TokenType
    value: str
    pos: int
    line: int
    col: int

    @property
    def keyword(self) -> str:
        """Return the uppercase keyword string (only meaningful for KEYWORD)."""
        return self.value.upper()


# ============================================================================
# Lexer
# ============================================================================

class Lexer:
    """State-machine driven tokeniser for Cypher query strings.

    Usage::

        lexer = Lexer(source)
        while True:
            tok = lexer.next()
            if tok.type == TokenType.EOF:
                break
            process(tok)

    or::

        for tok in Lexer(source):
            process(tok)
    """

    def __init__(self, source: str) -> None:
        self._source: str = source
        self._pos: int = 0
        self._line: int = 1
        self._col: int = 1
        self._peeked: Optional[Token] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def peek(self) -> Token:
        """Return the next token without consuming it.

        Repeated calls return the same cached token.
        """
        if self._peeked is None:
            self._peeked = self._next_impl()
        return self._peeked

    def next(self) -> Token:
        """Consume and return the next token."""
        if self._peeked is not None:
            tok = self._peeked
            self._peeked = None
            return tok
        return self._next_impl()

    def __iter__(self):
        """Iterator protocol — yields tokens until EOF (exclusive)."""
        return self

    def __next__(self) -> Token:
        tok = self.next()
        if tok.type == TokenType.EOF:
            raise StopIteration
        return tok

    # ------------------------------------------------------------------
    # Internal — token dispatch
    # ------------------------------------------------------------------

    def _next_impl(self) -> Token:
        self._skip_whitespace()

        if self._pos >= len(self._source):
            return Token(TokenType.EOF, "", self._pos, self._line, self._col)

        ch = self._source[self._pos]

        # String literal
        if ch in ("'", '"'):
            return self._lex_string(ch)

        # Float starting with dot: .5, .25
        if ch == "." and self._pos + 1 < len(self._source):
            nxt = self._source[self._pos + 1]
            if nxt.isdigit():
                return self._lex_number()

        # Number
        if ch.isdigit():
            return self._lex_number()

        # Parameter
        if ch == "$":
            return self._lex_parameter()

        # Identifier or keyword
        if ch.isalpha() or ch == "_":
            return self._lex_identifier_or_keyword()

        # Operator or punctuation
        return self._lex_operator_or_punctuation()

    # ------------------------------------------------------------------
    # Whitespace and comments
    # ------------------------------------------------------------------

    def _skip_whitespace(self) -> None:
        """Advance past whitespace, line comments, and block comments."""
        while self._pos < len(self._source):
            ch = self._source[self._pos]

            # Whitespace (not newline)
            if ch in (" ", "\t", "\r"):
                self._advance()
                continue

            # Newline
            if ch == "\n":
                self._advance()
                self._line += 1
                self._col = 1
                continue

            # Line comment  //
            if ch == "/" and self._pos + 1 < len(self._source):
                nxt = self._source[self._pos + 1]
                if nxt == "/":
                    self._advance()  # first /
                    self._advance()  # second /
                    # Skip until end of line
                    while self._pos < len(self._source) and self._source[self._pos] != "\n":
                        self._advance()
                    continue

                # Block comment  /*
                if nxt == "*":
                    self._advance()  # /
                    self._advance()  # *
                    self._skip_block_comment()
                    continue

            break

    def _skip_block_comment(self) -> None:
        """Skip a block comment. Called after consuming the opening /*."""
        start_line = self._line
        start_col = self._col - 2  # position of opening /*
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch == "\n":
                self._line += 1
                self._col = 1
                self._pos += 1
                continue
            if ch == "*" and self._pos + 1 < len(self._source):
                if self._source[self._pos + 1] == "/":
                    self._pos += 2
                    self._col += 2
                    return
            self._pos += 1
            self._col += 1

        # Unterminated block comment
        raise CypherLexerError(
            "Unterminated block comment",
            pos=self._pos,
            line=start_line,
            col=start_col,
        )

    # ------------------------------------------------------------------
    # Identifiers and keywords
    # ------------------------------------------------------------------

    def _lex_identifier_or_keyword(self) -> Token:
        """Lex an identifier or keyword: [a-zA-Z_][a-zA-Z0-9_]*."""
        start_pos = self._pos
        start_col = self._col
        start_line = self._line

        chars: list[str] = []
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch.isalnum() or ch == "_":
                chars.append(ch)
                self._advance()
            else:
                break

        value = "".join(chars)
        if value.upper() in _KEYWORDS:
            return Token(TokenType.KEYWORD, value, start_pos, start_line, start_col)
        return Token(TokenType.IDENTIFIER, value, start_pos, start_line, start_col)

    # ------------------------------------------------------------------
    # String literals
    # ------------------------------------------------------------------

    def _lex_string(self, quote: str) -> Token:
        """Lex a string literal delimited by *quote* (' or \").

        Supports backslash escapes for the quote character and backslash itself.
        """
        start_pos = self._pos
        start_col = self._col
        start_line = self._line
        self._advance()  # opening quote

        chars: list[str] = []
        while self._pos < len(self._source):
            ch = self._source[self._pos]

            if ch == "\n":
                raise CypherLexerError(
                    "Unterminated string literal",
                    pos=start_pos,
                    line=start_line,
                    col=start_col,
                )

            if ch == "\\":
                self._advance()  # backslash
                if self._pos < len(self._source):
                    nxt = self._source[self._pos]
                    if nxt == quote:
                        chars.append(quote)
                        self._advance()
                    elif nxt == "\\":
                        chars.append("\\")
                        self._advance()
                    else:
                        # Keep the backslash for other escape sequences
                        chars.append("\\")
                        chars.append(nxt)
                        self._advance()
                else:
                    raise CypherLexerError(
                        "Unterminated string literal",
                        pos=start_pos,
                        line=start_line,
                        col=start_col,
                    )
                continue

            if ch == quote:
                self._advance()  # closing quote
                return Token(
                    TokenType.STRING,
                    "".join(chars),
                    start_pos,
                    start_line,
                    start_col,
                )

            chars.append(ch)
            self._advance()

        raise CypherLexerError(
            "Unterminated string literal",
            pos=start_pos,
            line=start_line,
            col=start_col,
        )

    # ------------------------------------------------------------------
    # Numbers
    # ------------------------------------------------------------------

    def _lex_number(self) -> Token:
        """Lex an integer or float literal."""
        start_pos = self._pos
        start_col = self._col
        start_line = self._line

        chars: list[str] = []
        has_dot = False

        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch.isdigit():
                chars.append(ch)
                self._advance()
            elif ch == "." and not has_dot:
                chars.append(ch)
                has_dot = True
                self._advance()
            else:
                break

        value = "".join(chars)
        if has_dot:
            return Token(TokenType.FLOAT, value, start_pos, start_line, start_col)
        return Token(TokenType.INTEGER, value, start_pos, start_line, start_col)

    # ------------------------------------------------------------------
    # Parameter
    # ------------------------------------------------------------------

    def _lex_parameter(self) -> Token:
        """Lex a parameter placeholder: $identifier."""
        start_pos = self._pos
        start_col = self._col
        start_line = self._line
        self._advance()  # $

        chars: list[str] = []
        while self._pos < len(self._source):
            ch = self._source[self._pos]
            if ch.isalnum() or ch == "_":
                chars.append(ch)
                self._advance()
            else:
                break

        param_name = "".join(chars)
        if not param_name:
            raise CypherLexerError(
                "Expected parameter name after $",
                pos=start_pos,
                line=start_line,
                col=start_col,
            )

        return Token(
            TokenType.PARAMETER,
            "$" + param_name,
            start_pos,
            start_line,
            start_col,
        )

    # ------------------------------------------------------------------
    # Operators and punctuation
    # ------------------------------------------------------------------

    def _lex_operator_or_punctuation(self) -> Token:
        """Lex a single operator or punctuation token."""
        start_pos = self._pos
        start_col = self._col
        start_line = self._line
        ch = self._source[self._pos]

        # Two-character lookahead
        def peek_next() -> str:
            if self._pos + 1 < len(self._source):
                return self._source[self._pos + 1]
            return ""

        nxt = peek_next()

        # Multi-character tokens (longest match first)

        # <=
        if ch == "<" and nxt == "=":
            self._advance()
            self._advance()
            return Token(TokenType.OP_LTE, "<=", start_pos, start_line, start_col)

        # <-
        if ch == "<" and nxt == "-":
            self._advance()
            self._advance()
            return Token(TokenType.ARROW_LEFT, "<-", start_pos, start_line, start_col)

        # <>
        if ch == "<" and nxt == ">":
            self._advance()
            self._advance()
            return Token(TokenType.OP_NEQ, "<>", start_pos, start_line, start_col)

        # >=
        if ch == ">" and nxt == "=":
            self._advance()
            self._advance()
            return Token(TokenType.OP_GTE, ">=", start_pos, start_line, start_col)

        # ->
        if ch == "-" and nxt == ">":
            self._advance()
            self._advance()
            return Token(TokenType.ARROW_RIGHT, "->", start_pos, start_line, start_col)

        # =~
        if ch == "=" and nxt == "~":
            self._advance()
            self._advance()
            return Token(TokenType.OP_CONTAINS, "=~", start_pos, start_line, start_col)

        # Single-character tokens
        self._advance()

        single_char_map: dict[str, TokenType] = {
            "<": TokenType.OP_LT,
            ">": TokenType.OP_GT,
            "=": TokenType.OP_EQ,
            "-": TokenType.OP_MINUS,
            "+": TokenType.OP_PLUS,
            "*": TokenType.OP_MUL,
            "/": TokenType.OP_DIV,
            "%": TokenType.OP_MOD,
            "^": TokenType.OP_POW,
            ".": TokenType.DOT,
            ",": TokenType.COMMA,
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            "{": TokenType.LBRACE,
            "}": TokenType.RBRACE,
            ":": TokenType.COLON,
            ";": TokenType.SEMICOLON,
            "|": TokenType.PIPE,
        }

        if ch in single_char_map:
            return Token(single_char_map[ch], ch, start_pos, start_line, start_col)

        raise CypherLexerError(
            f"Unexpected character: '{ch}'",
            pos=start_pos,
            line=start_line,
            col=start_col,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _advance(self) -> None:
        """Advance one character, tracking line and column."""
        self._pos += 1
        self._col += 1
