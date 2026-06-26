//! GQL lexer — tokenizes GQL query strings into a token stream.

/// Token kinds produced by the GQL lexer.
#[derive(Debug, Clone, PartialEq)]
pub enum TokenKind {
    // Keywords
    KeywordFind,
    KeywordWhere,
    KeywordReturn,
    KeywordLimit,
    KeywordImpact,
    KeywordCalls,
    KeywordTrace,
    KeywordAs,
    KeywordAnd,
    KeywordOr,
    KeywordNot,
    KeywordIn,
    KeywordOrder,
    KeywordBy,
    KeywordAsc,
    KeywordDesc,

    // Literals
    Identifier(String),
    StringLit(String),
    Number(i64),

    // Operators
    Matches,
    Eq,
    Neq,
    Lt,
    Gt,
    Lte,
    Gte,

    // Delimiters
    LParen,
    RParen,
    Comma,
    Dot,
    Colon,
    HyphenHyphen,

    // Sentinel
    Eof,
}

/// A single token with kind, source position, and raw lexeme.
#[derive(Debug, Clone)]
pub struct Token {
    pub kind: TokenKind,
    pub offset: usize,
    pub lexeme: String,
}

/// Lexer that converts a GQL input string into a stream of tokens.
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

    /// Return true if there are more characters to read.
    fn has_more(&self) -> bool {
        self.pos < self.input.len()
    }

    /// Skip whitespace characters.
    fn skip_whitespace(&mut self) {
        while self.has_more() && self.input[self.pos].is_whitespace() {
            self.pos += 1;
        }
    }

    /// Peek at the current character (None at EOF).
    fn peek(&self) -> Option<char> {
        if self.has_more() {
            Some(self.input[self.pos])
        } else {
            None
        }
    }

    /// Peek at character offset by `n` from current position.
    fn peek_ahead(&self, n: usize) -> Option<char> {
        if self.pos + n < self.input.len() {
            Some(self.input[self.pos + n])
        } else {
            None
        }
    }

    /// Consume and return the current character.
    fn advance(&mut self) -> Option<char> {
        if self.has_more() {
            let ch = self.input[self.pos];
            self.pos += 1;
            Some(ch)
        } else {
            None
        }
    }

    /// Read characters while predicate holds, return the accumulated string.
    fn read_while(&mut self, predicate: fn(char) -> bool) -> String {
        let start = self.pos;
        while self.has_more() && predicate(self.input[self.pos]) {
            self.pos += 1;
        }
        self.input[start..self.pos].iter().collect()
    }

    /// Read an identifier (or keyword), case-insensitive matching against
    /// known keyword names.
    fn read_identifier_or_keyword(&mut self) -> Token {
        let start = self.pos;
        // Include hyphens in identifiers to support names like `--inbound`
        // The hyphen-only case is handled separately in next_token.
        let ident = self.read_while(|c| c.is_alphanumeric() || c == '_');
        let upper = ident.to_uppercase();

        let kind = match upper.as_str() {
            "FIND" => TokenKind::KeywordFind,
            "WHERE" => TokenKind::KeywordWhere,
            "RETURN" => TokenKind::KeywordReturn,
            "LIMIT" => TokenKind::KeywordLimit,
            "IMPACT" => TokenKind::KeywordImpact,
            "CALLS" => TokenKind::KeywordCalls,
            "TRACE" => TokenKind::KeywordTrace,
            "AS" => TokenKind::KeywordAs,
            "AND" => TokenKind::KeywordAnd,
            "OR" => TokenKind::KeywordOr,
            "NOT" => TokenKind::KeywordNot,
            "IN" => TokenKind::KeywordIn,
            "ORDER" => TokenKind::KeywordOrder,
            "BY" => TokenKind::KeywordBy,
            "ASC" => TokenKind::KeywordAsc,
            "DESC" => TokenKind::KeywordDesc,
            "MATCHES" => TokenKind::Matches,
            _ => TokenKind::Identifier(ident.clone()),
        };

        Token {
            kind,
            offset: start,
            lexeme: ident,
        }
    }

    /// Read a single-quoted string literal.
    fn read_string(&mut self) -> Token {
        let start = self.pos;
        self.advance(); // skip opening quote
        let content = self.read_while(|c| c != '\'');
        if self.has_more() {
            self.advance(); // skip closing quote
        }
        let lexeme = format!("'{}'", content);
        Token {
            kind: TokenKind::StringLit(content),
            offset: start,
            lexeme,
        }
    }

    /// Read an integer literal.
    fn read_number(&mut self) -> Token {
        let start = self.pos;
        let num_str = self.read_while(|c| c.is_ascii_digit());
        let value: i64 = num_str.parse().unwrap_or(0);
        Token {
            kind: TokenKind::Number(value),
            offset: start,
            lexeme: num_str,
        }
    }

    /// Produce the next token from the input.
    pub fn next_token(&mut self) -> Token {
        self.skip_whitespace();

        if !self.has_more() {
            return Token {
                kind: TokenKind::Eof,
                offset: self.input.len(),
                lexeme: String::new(),
            };
        }

        let ch = self.input[self.pos];

        match ch {
            '\'' => self.read_string(),
            '0'..='9' => self.read_number(),
            '(' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::LParen,
                    offset: self.pos - 1,
                    lexeme: "(".to_string(),
                }
            }
            ')' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::RParen,
                    offset: self.pos - 1,
                    lexeme: ")".to_string(),
                }
            }
            ',' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::Comma,
                    offset: self.pos - 1,
                    lexeme: ",".to_string(),
                }
            }
            '.' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::Dot,
                    offset: self.pos - 1,
                    lexeme: ".".to_string(),
                }
            }
            ':' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::Colon,
                    offset: self.pos - 1,
                    lexeme: ":".to_string(),
                }
            }
            '=' => {
                self.pos += 1;
                Token {
                    kind: TokenKind::Eq,
                    offset: self.pos - 1,
                    lexeme: "=".to_string(),
                }
            }
            '!' => {
                if self.peek_ahead(1) == Some('=') {
                    let start = self.pos;
                    self.pos += 2;
                    Token {
                        kind: TokenKind::Neq,
                        offset: start,
                        lexeme: "!=".to_string(),
                    }
                } else {
                    // Lone '!' — treat as unknown, skip it
                    self.pos += 1;
                    self.next_token()
                }
            }
            '<' => {
                let start = self.pos;
                self.pos += 1;
                if self.peek() == Some('=') {
                    self.pos += 1;
                    Token {
                        kind: TokenKind::Lte,
                        offset: start,
                        lexeme: "<=".to_string(),
                    }
                } else {
                    Token {
                        kind: TokenKind::Lt,
                        offset: start,
                        lexeme: "<".to_string(),
                    }
                }
            }
            '>' => {
                let start = self.pos;
                self.pos += 1;
                if self.peek() == Some('=') {
                    self.pos += 1;
                    Token {
                        kind: TokenKind::Gte,
                        offset: start,
                        lexeme: ">=".to_string(),
                    }
                } else {
                    Token {
                        kind: TokenKind::Gt,
                        offset: start,
                        lexeme: ">".to_string(),
                    }
                }
            }
            '-' => {
                if self.peek_ahead(1) == Some('-') {
                    let start = self.pos;
                    self.pos += 2;
                    Token {
                        kind: TokenKind::HyphenHyphen,
                        offset: start,
                        lexeme: "--".to_string(),
                    }
                } else {
                    // Single dash — skip as unknown
                    self.pos += 1;
                    self.next_token()
                }
            }
            'a'..='z' | 'A'..='Z' | '_' => self.read_identifier_or_keyword(),
            _ => {
                // Unknown character — skip
                self.pos += 1;
                self.next_token()
            }
        }
    }

    /// Tokenize the entire input, returning all tokens including Eof.
    pub fn tokenize(&mut self) -> Vec<Token> {
        let mut tokens = Vec::new();
        loop {
            let tok = self.next_token();
            let is_eof = matches!(tok.kind, TokenKind::Eof);
            tokens.push(tok);
            if is_eof {
                break;
            }
        }
        tokens
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // Helper: extract just the TokenKind from a token vec for easy comparison.
    fn kinds(tokens: &[Token]) -> Vec<TokenKind> {
        tokens.iter().map(|t| t.kind.clone()).collect()
    }

    // -----------------------------------------------------------------------
    // Basic tokenization
    // -----------------------------------------------------------------------

    #[test]
    fn test_empty_input_returns_eof() {
        let mut lexer = Lexer::new("");
        let tokens = lexer.tokenize();
        assert_eq!(tokens.len(), 1);
        assert_eq!(tokens[0].kind, TokenKind::Eof);
    }

    #[test]
    fn test_whitespace_only_returns_eof() {
        let mut lexer = Lexer::new("   \t \n  ");
        let tokens = lexer.tokenize();
        assert_eq!(tokens.len(), 1);
        assert_eq!(tokens[0].kind, TokenKind::Eof);
    }

    #[test]
    fn test_find_keyword() {
        let mut lexer = Lexer::new("FIND");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::KeywordFind, TokenKind::Eof]);
    }

    #[test]
    fn test_find_keyword_case_insensitive() {
        let mut lexer = Lexer::new("find function");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordFind,
                TokenKind::Identifier("function".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_keywords_where_limit_return() {
        let mut lexer = Lexer::new("WHERE RETURN LIMIT");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordWhere,
                TokenKind::KeywordReturn,
                TokenKind::KeywordLimit,
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_logical_keywords_and_or_not() {
        let mut lexer = Lexer::new("AND OR NOT");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordAnd,
                TokenKind::KeywordOr,
                TokenKind::KeywordNot,
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_order_keywords() {
        let mut lexer = Lexer::new("ORDER BY ASC DESC");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordOrder,
                TokenKind::KeywordBy,
                TokenKind::KeywordAsc,
                TokenKind::KeywordDesc,
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_special_statement_keywords() {
        let mut lexer = Lexer::new("IMPACT CALLS TRACE");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordImpact,
                TokenKind::KeywordCalls,
                TokenKind::KeywordTrace,
                TokenKind::Eof,
            ]
        );
    }

    // -----------------------------------------------------------------------
    // Identifiers
    // -----------------------------------------------------------------------

    #[test]
    fn test_identifier() {
        let mut lexer = Lexer::new("my_func");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::Identifier("my_func".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_identifier_with_numbers() {
        let mut lexer = Lexer::new("func123");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::Identifier("func123".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    // -----------------------------------------------------------------------
    // String literals
    // -----------------------------------------------------------------------

    #[test]
    fn test_string_literal() {
        let mut lexer = Lexer::new("'hello'");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::StringLit("hello".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_string_literal_empty() {
        let mut lexer = Lexer::new("''");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::StringLit("".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_string_with_path() {
        let mut lexer = Lexer::new("'src/main.py'");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::StringLit("src/main.py".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    // -----------------------------------------------------------------------
    // Numbers
    // -----------------------------------------------------------------------

    #[test]
    fn test_number() {
        let mut lexer = Lexer::new("42");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![TokenKind::Number(42), TokenKind::Eof]
        );
    }

    #[test]
    fn test_number_multiple_digits() {
        let mut lexer = Lexer::new("12345");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![TokenKind::Number(12345), TokenKind::Eof]
        );
    }

    // -----------------------------------------------------------------------
    // Operators
    // -----------------------------------------------------------------------

    #[test]
    fn test_matches_keyword() {
        let mut lexer = Lexer::new("MATCHES");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Matches, TokenKind::Eof]);
    }

    #[test]
    fn test_eq_operator() {
        let mut lexer = Lexer::new("=");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Eq, TokenKind::Eof]);
    }

    #[test]
    fn test_neq_operator() {
        let mut lexer = Lexer::new("!=");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Neq, TokenKind::Eof]);
    }

    #[test]
    fn test_lt_operator() {
        let mut lexer = Lexer::new("<");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Lt, TokenKind::Eof]);
    }

    #[test]
    fn test_lte_operator() {
        let mut lexer = Lexer::new("<=");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Lte, TokenKind::Eof]);
    }

    #[test]
    fn test_gt_operator() {
        let mut lexer = Lexer::new(">");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Gt, TokenKind::Eof]);
    }

    #[test]
    fn test_gte_operator() {
        let mut lexer = Lexer::new(">=");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Gte, TokenKind::Eof]);
    }

    // -----------------------------------------------------------------------
    // Delimiters
    // -----------------------------------------------------------------------

    #[test]
    fn test_parens() {
        let mut lexer = Lexer::new("()");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![TokenKind::LParen, TokenKind::RParen, TokenKind::Eof]
        );
    }

    #[test]
    fn test_comma() {
        let mut lexer = Lexer::new("a , b");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::Identifier("a".to_string()),
                TokenKind::Comma,
                TokenKind::Identifier("b".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_dot() {
        let mut lexer = Lexer::new("a.b");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::Identifier("a".to_string()),
                TokenKind::Dot,
                TokenKind::Identifier("b".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_colon() {
        let mut lexer = Lexer::new(":");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(kinds_list, vec![TokenKind::Colon, TokenKind::Eof]);
    }

    #[test]
    fn test_hyphen_hyphen() {
        let mut lexer = Lexer::new("--inbound");
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::HyphenHyphen,
                TokenKind::Identifier("inbound".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    // -----------------------------------------------------------------------
    // Full queries
    // -----------------------------------------------------------------------

    #[test]
    fn test_full_find_query() {
        let input = "FIND function WHERE name MATCHES 'auth'";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordFind,
                TokenKind::Identifier("function".to_string()),
                TokenKind::KeywordWhere,
                TokenKind::Identifier("name".to_string()),
                TokenKind::Matches,
                TokenKind::StringLit("auth".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_full_find_query_with_return_and_limit() {
        let input = "FIND class WHERE file_path MATCHES 'src/' RETURN name, file_path LIMIT 20";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordFind,
                TokenKind::Identifier("class".to_string()),
                TokenKind::KeywordWhere,
                TokenKind::Identifier("file_path".to_string()),
                TokenKind::Matches,
                TokenKind::StringLit("src/".to_string()),
                TokenKind::KeywordReturn,
                TokenKind::Identifier("name".to_string()),
                TokenKind::Comma,
                TokenKind::Identifier("file_path".to_string()),
                TokenKind::KeywordLimit,
                TokenKind::Number(20),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_impact_query() {
        let input = "IMPACT MyClass.my_method";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordImpact,
                TokenKind::Identifier("MyClass".to_string()),
                TokenKind::Dot,
                TokenKind::Identifier("my_method".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_calls_query() {
        let input = "CALLS my_func";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordCalls,
                TokenKind::Identifier("my_func".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_calls_inbound_query() {
        let input = "CALLS my_func --inbound";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordCalls,
                TokenKind::Identifier("my_func".to_string()),
                TokenKind::HyphenHyphen,
                TokenKind::Identifier("inbound".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_trace_query() {
        let input = "TRACE main parse_config";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordTrace,
                TokenKind::Identifier("main".to_string()),
                TokenKind::Identifier("parse_config".to_string()),
                TokenKind::Eof,
            ]
        );
    }

    #[test]
    fn test_lexeme_is_correct() {
        let mut lexer = Lexer::new("FIND my_func LIMIT 10");
        let tokens = lexer.tokenize();
        assert_eq!(tokens[0].lexeme, "FIND");
        assert_eq!(tokens[1].lexeme, "my_func");
        assert_eq!(tokens[2].lexeme, "LIMIT");
        assert_eq!(tokens[3].lexeme, "10");
    }

    #[test]
    fn test_offset_is_correct() {
        let mut lexer = Lexer::new("FIND my_func");
        let tokens = lexer.tokenize();
        assert_eq!(tokens[0].offset, 0); // "FIND" starts at 0
        assert_eq!(tokens[1].offset, 5); // "my_func" starts after "FIND " (5 chars)
    }

    #[test]
    fn test_complex_qualified_name() {
        let input = "FIND function WHERE name MATCHES 'auth' AND kind MATCHES 'method' RETURN name, qualified_name LIMIT 10";
        let mut lexer = Lexer::new(input);
        let kinds_list = kinds(&lexer.tokenize());
        assert_eq!(
            kinds_list,
            vec![
                TokenKind::KeywordFind,
                TokenKind::Identifier("function".to_string()),
                TokenKind::KeywordWhere,
                TokenKind::Identifier("name".to_string()),
                TokenKind::Matches,
                TokenKind::StringLit("auth".to_string()),
                TokenKind::KeywordAnd,
                TokenKind::Identifier("kind".to_string()),
                TokenKind::Matches,
                TokenKind::StringLit("method".to_string()),
                TokenKind::KeywordReturn,
                TokenKind::Identifier("name".to_string()),
                TokenKind::Comma,
                TokenKind::Identifier("qualified_name".to_string()),
                TokenKind::KeywordLimit,
                TokenKind::Number(10),
                TokenKind::Eof,
            ]
        );
    }
}
