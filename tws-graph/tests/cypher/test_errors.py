"""Tests for cypher/errors.py."""

import pytest
from tws_graph.cypher.errors import (
    CypherError,
    CypherLexerError,
    CypherSyntaxError,
    CypherSemanticError,
    CypherExecutionError,
)


class TestCypherError:

    def test_instantiate_minimal(self):
        e = CypherError('test message')
        assert str(e) == 'test message'
        assert e.pos is None
        assert e.line is None
        assert e.col is None

    def test_instantiate_with_position(self):
        e = CypherError('test', pos=5, line=2, col=3)
        assert str(e) == 'test'
        assert e.pos == 5
        assert e.line == 2
        assert e.col == 3

    def test_inherits_from_exception(self):
        assert issubclass(CypherError, Exception)
        assert isinstance(CypherError('msg'), BaseException)


class TestCypherLexerError:

    def test_inherits_from_cypher_error(self):
        assert issubclass(CypherLexerError, CypherError)

    def test_instantiate(self):
        e = CypherLexerError('unexpected char', pos=10, line=1, col=11)
        assert str(e) == 'unexpected char'
        assert e.pos == 10
        assert e.line == 1
        assert e.col == 11

    def test_is_exception(self):
        assert isinstance(CypherLexerError('x'), Exception)


class TestCypherSyntaxError:

    def test_inherits_from_cypher_error(self):
        assert issubclass(CypherSyntaxError, CypherError)

    def test_instantiate_formats_message(self):
        e = CypherSyntaxError(
            'unexpected token',
            expected='RETURN',
            actual='WHERE',
            line=2,
            col=8,
        )
        assert 'line 2' in str(e)
        assert 'col 8' in str(e)
        assert 'RETURN' in str(e)
        assert 'WHERE' in str(e)
        assert e.expected == 'RETURN'
        assert e.actual == 'WHERE'
        assert e.line == 2
        assert e.col == 8

    def test_is_exception(self):
        assert isinstance(
            CypherSyntaxError('msg', 'X', 'Y', 1, 1), Exception
        )


class TestCypherSemanticError:

    def test_inherits_from_cypher_error(self):
        assert issubclass(CypherSemanticError, CypherError)

    def test_instantiate(self):
        e = CypherSemanticError('variable x not defined', line=3, col=5)
        assert str(e) == 'variable x not defined'
        assert e.line == 3
        assert e.col == 5

    def test_is_exception(self):
        assert isinstance(CypherSemanticError('x'), Exception)


class TestCypherExecutionError:

    def test_inherits_from_cypher_error(self):
        assert issubclass(CypherExecutionError, CypherError)

    def test_instantiate(self):
        e = CypherExecutionError('store is locked', pos=0, line=1, col=0)
        assert str(e) == 'store is locked'
        assert e.pos == 0
        assert e.line == 1
        assert e.col == 0

    def test_is_exception(self):
        assert isinstance(CypherExecutionError('x'), Exception)


class TestExceptionHierarchy:

    def test_full_chain(self):
        assert issubclass(CypherLexerError, CypherError)
        assert issubclass(CypherSyntaxError, CypherError)
        assert issubclass(CypherSemanticError, CypherError)
        assert issubclass(CypherExecutionError, CypherError)
        assert issubclass(CypherError, Exception)
        assert issubclass(Exception, BaseException)

    def test_can_catch_by_base(self):
        # Subclasses with compatible __init__ to CypherError
        for err_cls in [CypherLexerError, CypherSemanticError, CypherExecutionError]:
            try:
                raise err_cls("test")
            except CypherError:
                pass
            else:
                pytest.fail(f"{err_cls.__name__} not caught by CypherError")

    def test_syntax_error_caught_by_base(self):
        """CypherSyntaxError has extra __init__ args but still is-a CypherError."""
        try:
            raise CypherSyntaxError("test", "RETURN", "WHERE", 1, 1)
        except CypherError:
            pass
        else:
            pytest.fail("CypherSyntaxError not caught by CypherError")
