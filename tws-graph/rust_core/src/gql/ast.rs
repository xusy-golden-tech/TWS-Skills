//! GQL AST node definitions.
//!
//! Defines the intermediate representation produced by the parser and
//! consumed by the planner.

/// Top-level GQL statement — one of FIND, IMPACT, CALLS, or TRACE.
#[derive(Debug, Clone)]
pub enum Statement {
    /// A FIND query: `FIND kind [WHERE filter] [RETURN fields] [ORDER BY ...] [LIMIT n]`
    Find(FindQuery),
    /// Impact analysis: `IMPACT qualified.name`
    Impact {
        target: String,
    },
    /// Call analysis: `CALLS qualified.name [--inbound]`
    Calls {
        target: String,
        inbound: bool,
    },
    /// Path trace: `TRACE source target`
    Trace {
        source: String,
        target: String,
    },
}

/// A parsed FIND query.
#[derive(Debug, Clone)]
pub struct FindQuery {
    /// The node kind to scan (e.g. "function", "class").
    pub kind: Option<String>,
    /// Optional WHERE filter expression.
    pub filter: Option<FilterExpr>,
    /// Fields to return (empty means all fields).
    pub return_fields: Vec<String>,
    /// ORDER BY clauses.
    pub order_by: Vec<OrderItem>,
    /// Optional LIMIT.
    pub limit: Option<usize>,
}

/// A filter expression in a WHERE clause.
#[derive(Debug, Clone)]
pub enum FilterExpr {
    /// A comparison: `field OP value`
    Comparison {
        field: String,
        op: ComparisonOp,
        value: Value,
    },
    /// Logical AND: `expr AND expr`
    And(Box<FilterExpr>, Box<FilterExpr>),
    /// Logical OR: `expr OR expr`
    Or(Box<FilterExpr>, Box<FilterExpr>),
    /// Logical NOT: `NOT expr`
    Not(Box<FilterExpr>),
}

/// Comparison operators.
#[derive(Debug, Clone)]
pub enum ComparisonOp {
    Matches,
    Eq,
    Neq,
    Lt,
    Gt,
    Lte,
    Gte,
}

impl ComparisonOp {
    /// SQL-friendly representation.
    pub fn as_sql(&self) -> &'static str {
        match self {
            ComparisonOp::Matches => "LIKE",
            ComparisonOp::Eq => "=",
            ComparisonOp::Neq => "!=",
            ComparisonOp::Lt => "<",
            ComparisonOp::Gt => ">",
            ComparisonOp::Lte => "<=",
            ComparisonOp::Gte => ">=",
        }
    }
}

/// A literal value.
#[derive(Debug, Clone)]
pub enum Value {
    String(String),
    Number(i64),
}

/// An ORDER BY item.
#[derive(Debug, Clone)]
pub struct OrderItem {
    pub field: String,
    pub direction: OrderDirection,
}

/// Sort direction.
#[derive(Debug, Clone)]
pub enum OrderDirection {
    Asc,
    Desc,
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_query_builder() {
        let q = FindQuery {
            kind: Some("function".to_string()),
            filter: Some(FilterExpr::Comparison {
                field: "name".to_string(),
                op: ComparisonOp::Matches,
                value: Value::String("auth".to_string()),
            }),
            return_fields: vec!["name".to_string(), "file_path".to_string()],
            order_by: vec![OrderItem {
                field: "name".to_string(),
                direction: OrderDirection::Asc,
            }],
            limit: Some(20),
        };
        assert_eq!(q.kind.unwrap(), "function");
        assert_eq!(q.return_fields.len(), 2);
        assert_eq!(q.limit.unwrap(), 20);
    }

    #[test]
    fn test_statement_impact() {
        let s = Statement::Impact {
            target: "MyClass.my_method".to_string(),
        };
        match s {
            Statement::Impact { target } => assert_eq!(target, "MyClass.my_method"),
            _ => panic!("expected Impact"),
        }
    }

    #[test]
    fn test_statement_calls() {
        let s = Statement::Calls {
            target: "my_func".to_string(),
            inbound: false,
        };
        match s {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "my_func");
                assert!(!inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    #[test]
    fn test_statement_calls_inbound() {
        let s = Statement::Calls {
            target: "parse_config".to_string(),
            inbound: true,
        };
        match s {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "parse_config");
                assert!(inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    #[test]
    fn test_statement_trace() {
        let s = Statement::Trace {
            source: "main".to_string(),
            target: "parse_config".to_string(),
        };
        match s {
            Statement::Trace { source, target } => {
                assert_eq!(source, "main");
                assert_eq!(target, "parse_config");
            }
            _ => panic!("expected Trace"),
        }
    }

    #[test]
    fn test_filter_expr_and() {
        let expr = FilterExpr::And(
            Box::new(FilterExpr::Comparison {
                field: "name".to_string(),
                op: ComparisonOp::Matches,
                value: Value::String("foo".to_string()),
            }),
            Box::new(FilterExpr::Comparison {
                field: "kind".to_string(),
                op: ComparisonOp::Eq,
                value: Value::String("function".to_string()),
            }),
        );
        match &expr {
            FilterExpr::And(left, _) => match left.as_ref() {
                FilterExpr::Comparison { field, .. } => assert_eq!(field, "name"),
                _ => panic!("expected Comparison"),
            },
            _ => panic!("expected And"),
        }
    }

    #[test]
    fn test_comparison_op_as_sql() {
        assert_eq!(ComparisonOp::Matches.as_sql(), "LIKE");
        assert_eq!(ComparisonOp::Eq.as_sql(), "=");
        assert_eq!(ComparisonOp::Neq.as_sql(), "!=");
        assert_eq!(ComparisonOp::Lt.as_sql(), "<");
        assert_eq!(ComparisonOp::Gt.as_sql(), ">");
        assert_eq!(ComparisonOp::Lte.as_sql(), "<=");
        assert_eq!(ComparisonOp::Gte.as_sql(), ">=");
    }

    #[test]
    fn test_order_direction() {
        let asc = OrderDirection::Asc;
        let desc = OrderDirection::Desc;
        // Just verify construction works
        match asc {
            OrderDirection::Asc => {}
            _ => panic!("expected Asc"),
        }
        match desc {
            OrderDirection::Desc => {}
            _ => panic!("expected Desc"),
        }
    }

    #[test]
    fn test_value_variants() {
        let s = Value::String("hello".to_string());
        let n = Value::Number(42);
        match s {
            Value::String(v) => assert_eq!(v, "hello"),
            _ => panic!("expected String"),
        }
        match n {
            Value::Number(v) => assert_eq!(v, 42),
            _ => panic!("expected Number"),
        }
    }
}
