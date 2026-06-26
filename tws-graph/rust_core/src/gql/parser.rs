//! GQL parser — recursive-descent parser that builds an AST from a token stream.

use anyhow::{bail, Result};

use super::ast::{
    ComparisonOp, FilterExpr, FindQuery, OrderDirection, OrderItem, Statement, Value,
};
use super::lexer::{Lexer, Token, TokenKind};

/// A recursive-descent parser for the GQL query language.
pub struct Parser {
    lexer: Lexer,
    current: Token,
    peek: Token,
}

impl Parser {
    /// Create a new parser for the given GQL input string.
    pub fn new(input: &str) -> Self {
        let mut lexer = Lexer::new(input);
        let current = lexer.next_token();
        let peek = lexer.next_token();
        Self {
            lexer,
            current,
            peek,
        }
    }

    /// Advance to the next token.
    fn advance(&mut self) {
        self.current = self.peek.clone();
        self.peek = self.lexer.next_token();
    }

    /// Check if current token matches a given keyword.
    fn check(&self, kind: &TokenKind) -> bool {
        std::mem::discriminant(&self.current.kind) == std::mem::discriminant(kind)
    }

    /// Check and advance if current token matches the expected kind,
    /// returning an error otherwise.
    fn expect(&mut self, kind: TokenKind) -> Result<Token> {
        if std::mem::discriminant(&self.current.kind) == std::mem::discriminant(&kind) {
            let tok = self.current.clone();
            self.advance();
            Ok(tok)
        } else {
            bail!(
                "expected {:?} but found {:?} at offset {}",
                kind,
                self.current.kind,
                self.current.offset
            );
        }
    }

    /// Expect and consume an identifier, returning its string value.
    fn expect_identifier(&mut self) -> Result<String> {
        match &self.current.kind {
            TokenKind::Identifier(name) => {
                let name = name.clone();
                self.advance();
                Ok(name)
            }
            kind => bail!(
                "expected identifier but found {:?} at offset {}",
                kind,
                self.current.offset
            ),
        }
    }

    /// Parse a dot-separated qualified name (e.g. `Module.Class.method`).
    fn parse_qualified_name(&mut self) -> Result<String> {
        let mut name = self.expect_identifier()?;
        while matches!(&self.current.kind, TokenKind::Dot) {
            self.advance(); // consume '.'
            let part = self.expect_identifier()?;
            name.push('.');
            name.push_str(&part);
        }
        Ok(name)
    }

    /// Parse a comma-separated list of identifiers.
    fn parse_identifier_list(&mut self) -> Result<Vec<String>> {
        let mut fields = vec![self.expect_identifier()?];
        while matches!(&self.current.kind, TokenKind::Comma) {
            self.advance(); // consume ','
            fields.push(self.expect_identifier()?);
        }
        Ok(fields)
    }

    /// Parse a literal value (string or number).
    fn parse_value(&mut self) -> Result<Value> {
        match &self.current.kind {
            TokenKind::StringLit(s) => {
                let v = Value::String(s.clone());
                self.advance();
                Ok(v)
            }
            TokenKind::Number(n) => {
                let v = Value::Number(*n);
                self.advance();
                Ok(v)
            }
            kind => bail!(
                "expected string or number but found {:?} at offset {}",
                kind,
                self.current.offset
            ),
        }
    }

    /// Parse a comparison operator.
    fn parse_comparison_op(&mut self) -> Result<ComparisonOp> {
        let op = match &self.current.kind {
            TokenKind::Matches => ComparisonOp::Matches,
            TokenKind::Eq => ComparisonOp::Eq,
            TokenKind::Neq => ComparisonOp::Neq,
            TokenKind::Lt => ComparisonOp::Lt,
            TokenKind::Gt => ComparisonOp::Gt,
            TokenKind::Lte => ComparisonOp::Lte,
            TokenKind::Gte => ComparisonOp::Gte,
            kind => bail!(
                "expected comparison operator but found {:?} at offset {}",
                kind,
                self.current.offset
            ),
        };
        self.advance();
        Ok(op)
    }

    /// Parse a comparison: `field OP value`
    fn parse_comparison(&mut self) -> Result<FilterExpr> {
        let field = self.expect_identifier()?;
        let op = self.parse_comparison_op()?;
        let value = self.parse_value()?;
        Ok(FilterExpr::Comparison { field, op, value })
    }

    /// Parse a filter expression (may include AND/OR/NOT).
    fn parse_filter_expr(&mut self) -> Result<FilterExpr> {
        if matches!(self.current.kind, TokenKind::KeywordNot) {
            self.advance(); // consume NOT
            let inner = self.parse_filter_term()?;
            return Ok(FilterExpr::Not(Box::new(inner)));
        }
        self.parse_filter_or()
    }

    /// Parse OR-connected terms.
    fn parse_filter_or(&mut self) -> Result<FilterExpr> {
        let mut left = self.parse_filter_and()?;
        while matches!(self.current.kind, TokenKind::KeywordOr) {
            self.advance(); // consume OR
            let right = self.parse_filter_and()?;
            left = FilterExpr::Or(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    /// Parse AND-connected terms.
    fn parse_filter_and(&mut self) -> Result<FilterExpr> {
        let mut left = self.parse_filter_term()?;
        while matches!(self.current.kind, TokenKind::KeywordAnd) {
            self.advance(); // consume AND
            let right = self.parse_filter_term()?;
            left = FilterExpr::And(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    /// Parse a single filter term (comparison or parenthesized).
    fn parse_filter_term(&mut self) -> Result<FilterExpr> {
        if matches!(self.current.kind, TokenKind::LParen) {
            self.advance(); // consume '('
            let expr = self.parse_filter_expr()?;
            self.expect(TokenKind::RParen)?;
            return Ok(expr);
        }
        self.parse_comparison()
    }

    /// Check if the current token can start a filter expression.
    fn is_start_of_filter(&self) -> bool {
        !matches!(
            &self.current.kind,
            TokenKind::Eof
                | TokenKind::KeywordReturn
                | TokenKind::KeywordOrder
                | TokenKind::KeywordLimit
        )
    }

    /// Parse an ORDER BY clause: `ORDER BY field [ASC|DESC], field [ASC|DESC], ...`
    fn parse_order_by(&mut self) -> Result<Vec<OrderItem>> {
        self.advance(); // consume ORDER
        self.expect(TokenKind::KeywordBy)?; // consume BY

        let mut items = Vec::new();
        let field = self.expect_identifier()?;
        let direction = if matches!(&self.current.kind, TokenKind::KeywordAsc) {
            self.advance();
            OrderDirection::Asc
        } else if matches!(&self.current.kind, TokenKind::KeywordDesc) {
            self.advance();
            OrderDirection::Desc
        } else {
            OrderDirection::Asc // default
        };
        items.push(OrderItem { field, direction });

        while matches!(&self.current.kind, TokenKind::Comma) {
            self.advance(); // consume ','
            let field = self.expect_identifier()?;
            let direction = if matches!(&self.current.kind, TokenKind::KeywordAsc) {
                self.advance();
                OrderDirection::Asc
            } else if matches!(&self.current.kind, TokenKind::KeywordDesc) {
                self.advance();
                OrderDirection::Desc
            } else {
                OrderDirection::Asc
            };
            items.push(OrderItem { field, direction });
        }
        Ok(items)
    }

    // -----------------------------------------------------------------------
    // Statement-level parsing
    // -----------------------------------------------------------------------

    /// Parse a FIND statement:
    /// `FIND kind [WHERE filter] [RETURN fields] [ORDER BY ...] [LIMIT n]`
    fn parse_find(&mut self) -> Result<Statement> {
        self.advance(); // consume FIND

        // Parse the node kind (optional — could be "FIND WHERE ...")
        let kind = if matches!(&self.current.kind, TokenKind::Identifier(_)) {
            Some(self.expect_identifier()?)
        } else {
            None
        };

        // Optional WHERE clause
        let filter = if matches!(&self.current.kind, TokenKind::KeywordWhere) {
            self.advance(); // consume WHERE
            Some(self.parse_filter_expr()?)
        } else {
            None
        };

        // Optional RETURN clause
        let return_fields = if matches!(&self.current.kind, TokenKind::KeywordReturn) {
            self.advance(); // consume RETURN
            self.parse_identifier_list()?
        } else {
            Vec::new()
        };

        // Optional ORDER BY clause
        let order_by = if matches!(&self.current.kind, TokenKind::KeywordOrder) {
            self.parse_order_by()?
        } else {
            Vec::new()
        };

        // Optional LIMIT clause
        let limit = if matches!(&self.current.kind, TokenKind::KeywordLimit) {
            self.advance(); // consume LIMIT
            match &self.current.kind {
                TokenKind::Number(n) => {
                    let v = *n as usize;
                    self.advance();
                    Some(v)
                }
                kind => bail!(
                    "expected number after LIMIT but found {:?} at offset {}",
                    kind,
                    self.current.offset
                ),
            }
        } else {
            None
        };

        Ok(Statement::Find(FindQuery {
            kind,
            filter,
            return_fields,
            order_by,
            limit,
        }))
    }

    /// Parse an IMPACT statement: `IMPACT qualified.name`
    fn parse_impact(&mut self) -> Result<Statement> {
        self.advance(); // consume IMPACT
        let target = self.parse_qualified_name()?;
        Ok(Statement::Impact { target })
    }

    /// Parse a CALLS statement: `CALLS qualified.name [--inbound]`
    fn parse_calls(&mut self) -> Result<Statement> {
        self.advance(); // consume CALLS
        let target = self.parse_qualified_name()?;

        let inbound = if matches!(&self.current.kind, TokenKind::HyphenHyphen) {
            self.advance(); // consume --
            let flag = self.expect_identifier()?;
            if flag.to_lowercase() == "inbound" {
                true
            } else {
                bail!(
                    "unknown flag --{} at offset {}",
                    flag,
                    self.current.offset
                );
            }
        } else {
            false
        };

        Ok(Statement::Calls { target, inbound })
    }

    /// Parse a TRACE statement: `TRACE source target`
    fn parse_trace(&mut self) -> Result<Statement> {
        self.advance(); // consume TRACE
        let source = self.parse_qualified_name()?;
        let target = self.parse_qualified_name()?;
        Ok(Statement::Trace { source, target })
    }

    /// Parse a complete GQL statement.
    pub fn parse(&mut self) -> Result<Statement> {
        let stmt = match &self.current.kind {
            TokenKind::KeywordFind => self.parse_find()?,
            TokenKind::KeywordImpact => self.parse_impact()?,
            TokenKind::KeywordCalls => self.parse_calls()?,
            TokenKind::KeywordTrace => self.parse_trace()?,
            TokenKind::Eof => bail!("empty query"),
            kind => bail!(
                "unexpected token {:?} at offset {} — expected FIND, IMPACT, CALLS, or TRACE",
                kind,
                self.current.offset
            ),
        };

        // After parsing the statement, expect EOF
        if !matches!(&self.current.kind, TokenKind::Eof) {
            bail!(
                "unexpected token {:?} after end of statement at offset {}",
                self.current.kind,
                self.current.offset
            );
        }

        Ok(stmt)
    }
}

/// Convenience: parse a GQL query string into a `Statement`.
pub fn parse(input: &str) -> Result<Statement> {
    let mut parser = Parser::new(input);
    parser.parse()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // FIND parsing
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_find_simple() {
        let stmt = parse("FIND function").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.kind.unwrap(), "function");
                assert!(q.filter.is_none());
                assert!(q.return_fields.is_empty());
                assert!(q.limit.is_none());
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_where() {
        let stmt = parse("FIND function WHERE name MATCHES 'auth'").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.kind.unwrap(), "function");
                match q.filter.unwrap() {
                    FilterExpr::Comparison { field, op, value } => {
                        assert_eq!(field, "name");
                        match op {
                            ComparisonOp::Matches => {}
                            _ => panic!("expected Matches"),
                        }
                        match value {
                            Value::String(s) => assert_eq!(s, "auth"),
                            _ => panic!("expected String"),
                        }
                    }
                    _ => panic!("expected Comparison"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_return() {
        let stmt = parse("FIND class RETURN name, file_path").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.kind.unwrap(), "class");
                assert_eq!(q.return_fields, vec!["name", "file_path"]);
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_limit() {
        let stmt = parse("FIND function LIMIT 20").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.limit.unwrap(), 20);
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_full() {
        let stmt =
            parse("FIND class WHERE file_path MATCHES 'src/' RETURN name, file_path LIMIT 20")
                .unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.kind.unwrap(), "class");
                assert!(q.filter.is_some());
                assert_eq!(q.return_fields.len(), 2);
                assert_eq!(q.limit.unwrap(), 20);
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_and() {
        let stmt = parse("FIND function WHERE name MATCHES 'auth' AND kind MATCHES 'method'")
            .unwrap();
        match stmt {
            Statement::Find(q) => {
                match q.filter.unwrap() {
                    FilterExpr::And(_, _) => {} // OK
                    _ => panic!("expected And"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_or() {
        let stmt = parse("FIND function WHERE name MATCHES 'auth' OR name MATCHES 'login'")
            .unwrap();
        match stmt {
            Statement::Find(q) => {
                match q.filter.unwrap() {
                    FilterExpr::Or(_, _) => {} // OK
                    _ => panic!("expected Or"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_not() {
        let stmt = parse("FIND function WHERE NOT name MATCHES 'test'").unwrap();
        match stmt {
            Statement::Find(q) => {
                match q.filter.unwrap() {
                    FilterExpr::Not(_) => {} // OK
                    _ => panic!("expected Not"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_eq() {
        let stmt = parse("FIND function WHERE name = 'main'").unwrap();
        match stmt {
            Statement::Find(q) => {
                match q.filter.unwrap() {
                    FilterExpr::Comparison { op, .. } => {
                        match op {
                            ComparisonOp::Eq => {}
                            _ => panic!("expected Eq"),
                        }
                    }
                    _ => panic!("expected Comparison"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_order_by() {
        let stmt = parse("FIND function ORDER BY name ASC").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.order_by.len(), 1);
                assert_eq!(q.order_by[0].field, "name");
                match q.order_by[0].direction {
                    OrderDirection::Asc => {}
                    _ => panic!("expected Asc"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_with_order_by_desc() {
        let stmt = parse("FIND function ORDER BY name DESC").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.order_by.len(), 1);
                match q.order_by[0].direction {
                    OrderDirection::Desc => {}
                    _ => panic!("expected Desc"),
                }
            }
            _ => panic!("expected Find"),
        }
    }

    #[test]
    fn test_parse_find_order_by_multi() {
        let stmt = parse("FIND function ORDER BY name ASC, file_path DESC").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.order_by.len(), 2);
            }
            _ => panic!("expected Find"),
        }
    }

    // -----------------------------------------------------------------------
    // IMPACT parsing
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_impact() {
        let stmt = parse("IMPACT MyClass.my_method").unwrap();
        match stmt {
            Statement::Impact { target } => {
                assert_eq!(target, "MyClass.my_method");
            }
            _ => panic!("expected Impact"),
        }
    }

    #[test]
    fn test_parse_impact_simple_name() {
        let stmt = parse("IMPACT my_func").unwrap();
        match stmt {
            Statement::Impact { target } => {
                assert_eq!(target, "my_func");
            }
            _ => panic!("expected Impact"),
        }
    }

    // -----------------------------------------------------------------------
    // CALLS parsing
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_calls() {
        let stmt = parse("CALLS my_func").unwrap();
        match stmt {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "my_func");
                assert!(!inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    #[test]
    fn test_parse_calls_qualified() {
        let stmt = parse("CALLS MyClass.my_method").unwrap();
        match stmt {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "MyClass.my_method");
                assert!(!inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    #[test]
    fn test_parse_calls_inbound() {
        let stmt = parse("CALLS my_func --inbound").unwrap();
        match stmt {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "my_func");
                assert!(inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    // -----------------------------------------------------------------------
    // TRACE parsing
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_trace() {
        let stmt = parse("TRACE main parse_config").unwrap();
        match stmt {
            Statement::Trace { source, target } => {
                assert_eq!(source, "main");
                assert_eq!(target, "parse_config");
            }
            _ => panic!("expected Trace"),
        }
    }

    #[test]
    fn test_parse_trace_qualified() {
        let stmt = parse("TRACE MyClass.main MyClass.parse").unwrap();
        match stmt {
            Statement::Trace { source, target } => {
                assert_eq!(source, "MyClass.main");
                assert_eq!(target, "MyClass.parse");
            }
            _ => panic!("expected Trace"),
        }
    }

    // -----------------------------------------------------------------------
    // Error cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_empty_input_error() {
        let result = parse("");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_trailing_garbage() {
        let result = parse("FIND function garbage");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_unknown_keyword() {
        let result = parse("UNKNOWN");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_case_insensitive_find() {
        let stmt = parse("find function").unwrap();
        match stmt {
            Statement::Find(q) => {
                assert_eq!(q.kind.unwrap(), "function");
            }
            _ => panic!("expected Find"),
        }
    }
}
