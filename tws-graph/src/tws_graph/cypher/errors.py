"""Cypher 引擎错误类型定义.

Per design-cypher-engine.md section 10.1.
"""

from typing import Optional


class CypherError(Exception):
    """Cypher 引擎异常基类.

    All Cypher-specific errors inherit from this class. Carries optional
    source-position information so that CLI callers can render error
    locations with caret indicators.
    """

    def __init__(
        self,
        message: str,
        pos: Optional[int] = None,
        line: Optional[int] = None,
        col: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.pos = pos
        self.line = line
        self.col = col


class CypherLexerError(CypherError):
    """词法错误.

    Raised when the lexer encounters an unrecognised character, an
    unterminated string literal, or an unclosed block comment.
    """


class CypherSyntaxError(CypherError):
    """语法错误.

    Raised when the parser encounters an unexpected token, a missing
    clause keyword, or unbalanced delimiters. Includes expected and
    actual token descriptions to aid error reporting.
    """

    def __init__(
        self,
        message: str,
        expected: str,
        actual: str,
        line: int,
        col: int,
    ) -> None:
        full_msg = (
            f"Syntax error at line {line}, col {col}: {message}. "
            f"Expected: {expected}, got: {actual}"
        )
        super().__init__(full_msg, line=line, col=col)
        self.expected = expected
        self.actual = actual


class CypherSemanticError(CypherError):
    """语义错误.

    Raised when the planner detects undefined variables, type
    mismatches, or misuse of aggregate vs. non-aggregate expressions.
    """


class CypherExecutionError(CypherError):
    """运行时错误.

    Raised by the executor when a runtime condition prevents query
    completion — for example, store access failures, division by zero,
    or calls to unknown functions.
    """
