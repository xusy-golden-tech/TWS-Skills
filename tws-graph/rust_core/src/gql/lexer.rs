//! GQL lexer — tokenizes GQL query strings.

/// Token kinds produced by the GQL lexer.
#[derive(Debug, Clone, PartialEq)]
pub enum TokenKind {
    KeywordFind,
    KeywordWhere,
    KeywordReturn,
    KeywordLimit,
    KeywordImpact,
    Identifier(String),
    StringLit(String),
    Number(i64),
    Matches,
    Eq,
    Comma,
    LParen,
    RParen,
    Eof,
}

/// A single token with kind and source position.
#[derive(Debug, Clone)]
pub struct Token {
    pub kind: TokenKind,
    pub offset: usize,
}

/// Lexer state.
pub struct Lexer {
    input: Vec<char>,
    pos: usize,
}

impl Lexer {
    pub fn new(input: &str) -> Self {
        Self {
            input: input.chars().collect(),
            pos: 0,
        }
    }

    /// Return the next token (placeholder — always returns Eof).
    pub fn next_token(&mut self) -> Token {
        // TODO: implement full GQL lexer
        Token {
            kind: TokenKind::Eof,
            offset: self.input.len(),
        }
    }
}
