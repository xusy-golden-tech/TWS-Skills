//! GQL parser — builds an AST from a token stream.

use super::ast::Query;
use super::lexer::Lexer;

/// Parse a GQL query string into a `Query` AST node.
pub fn parse(_input: &str) -> anyhow::Result<Query> {
    // TODO: implement recursive-descent GQL parser
    Ok(Query::default())
}

/// Internal parser state.
pub struct Parser {
    _lexer: Lexer,
}

impl Parser {
    pub fn new(input: &str) -> Self {
        Self {
            _lexer: Lexer::new(input),
        }
    }

    pub fn parse_query(&mut self) -> anyhow::Result<Query> {
        // TODO: implement GQL parsing
        Ok(Query::default())
    }
}
