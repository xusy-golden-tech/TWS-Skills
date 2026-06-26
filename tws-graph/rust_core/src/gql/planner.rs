//! GQL planner — converts a GQL AST into an execution plan.
//!
//! The planner produces a logical plan consisting of operators that can be
//! executed against the database. Each operator represents a data-flow
//! transformation (scan, filter, expand, project, sort, limit).

use anyhow::Result;

use super::ast::Statement;

/// A single operator in the execution plan.
#[derive(Debug, Clone)]
pub enum Operator {
    /// Scan nodes from the nodes table, optionally filtered by kind.
    Scan {
        /// Optional node kind filter (e.g., "function", "class").
        kind: Option<String>,
    },
    /// Filter rows using a SQL WHERE expression string.
    Filter {
        /// SQL WHERE clause fragment (without the WHERE keyword).
        sql: String,
        /// Bind parameters for the filter.
        params: Vec<String>,
    },
    /// Expand edges: traverse outbound or inbound edges.
    EdgeExpand {
        /// Direction: "outbound" or "inbound".
        direction: String,
        /// Edge kinds to follow (empty = all).
        edge_kinds: Vec<String>,
        /// Maximum traversal depth (1 = direct neighbours).
        depth: usize,
    },
    /// Project specific columns.
    Project {
        /// Column names to return.
        fields: Vec<String>,
    },
    /// Sort results.
    Sort {
        /// Column to sort by.
        field: String,
        /// true = ascending, false = descending.
        asc: bool,
    },
    /// Limit the number of result rows.
    Limit {
        count: usize,
    },
}

/// An execution plan — a sequence of operators.
#[derive(Debug, Clone)]
pub struct Plan {
    /// Operators to execute in order.
    pub operators: Vec<Operator>,
    /// Original statement (used for special statement types like IMPACT/CALLS/TRACE).
    pub statement: Option<Statement>,
}

impl Default for Plan {
    fn default() -> Self {
        Self {
            operators: Vec::new(),
            statement: None,
        }
    }
}

/// Generate an execution plan from a parsed GQL statement.
pub fn plan(statement: &Statement) -> Result<Plan> {
    match statement {
        Statement::Find(q) => plan_find(q),
        _ => {
            // For IMPACT, CALLS, TRACE — pass through the statement directly
            // so the executor can use Database methods for traversal.
            Ok(Plan {
                operators: Vec::new(),
                statement: Some(statement.clone()),
            })
        }
    }
}

fn plan_find(q: &super::ast::FindQuery) -> Result<Plan> {
    use super::ast::{ComparisonOp, FilterExpr, Value};

    let mut operators = Vec::new();

    // 1. Scan operator with optional kind filter
    operators.push(Operator::Scan {
        kind: q.kind.clone(),
    });

    // 2. Filter operator from WHERE clause
    if let Some(ref filter) = q.filter {
        let (sql, params) = filter_to_sql(filter);
        operators.push(Operator::Filter { sql, params });
    }

    // 3. Project operator from RETURN clause
    if !q.return_fields.is_empty() {
        operators.push(Operator::Project {
            fields: q.return_fields.clone(),
        });
    }

    // 4. Sort operators from ORDER BY
    for item in &q.order_by {
        operators.push(Operator::Sort {
            field: item.field.clone(),
            asc: matches!(item.direction, super::ast::OrderDirection::Asc),
        });
    }

    // 5. Limit operator
    if let Some(limit) = q.limit {
        operators.push(Operator::Limit { count: limit });
    }

    Ok(Plan {
        operators,
        statement: None,
    })
}

/// Convert a FilterExpr AST node to SQL text and bind parameters.
fn filter_to_sql(filter: &super::ast::FilterExpr) -> (String, Vec<String>) {
    use super::ast::{ComparisonOp, FilterExpr, Value};
    match filter {
        FilterExpr::Comparison { field, op, value } => {
            let sql_op = match op {
                ComparisonOp::Matches => "LIKE",
                ComparisonOp::Eq => "=",
                ComparisonOp::Neq => "!=",
                ComparisonOp::Lt => "<",
                ComparisonOp::Gt => ">",
                ComparisonOp::Lte => "<=",
                ComparisonOp::Gte => ">=",
            };
            match value {
                Value::String(s) => {
                    // For MATCHES/LIKE, wrap in % wildcards
                    let pattern = if matches!(op, ComparisonOp::Matches) {
                        format!("%{}%", s)
                    } else {
                        s.clone()
                    };
                    (format!("{} {} ?1", field, sql_op), vec![pattern])
                }
                Value::Number(n) => (
                    format!("{} {} ?1", field, sql_op),
                    vec![n.to_string()],
                ),
            }
        }
        FilterExpr::And(left, right) => {
            let (l_sql, l_params) = filter_to_sql(left);
            let (r_sql, mut r_params) = filter_to_sql(right);
            let mut params = l_params;
            params.append(&mut r_params);
            (format!("({} AND {})", l_sql, r_sql), params)
        }
        FilterExpr::Or(left, right) => {
            let (l_sql, l_params) = filter_to_sql(left);
            let (r_sql, mut r_params) = filter_to_sql(right);
            let mut params = l_params;
            params.append(&mut r_params);
            (format!("({} OR {})", l_sql, r_sql), params)
        }
        FilterExpr::Not(inner) => {
            let (inner_sql, params) = filter_to_sql(inner);
            (format!("NOT ({})", inner_sql), params)
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::super::ast::*;
    use super::*;

    #[test]
    fn test_plan_find_simple() {
        let q = FindQuery {
            kind: Some("function".to_string()),
            filter: None,
            return_fields: vec![],
            order_by: vec![],
            limit: None,
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        assert_eq!(plan.operators.len(), 1);
        match &plan.operators[0] {
            Operator::Scan { kind } => assert_eq!(kind.as_deref().unwrap(), "function"),
            _ => panic!("expected Scan"),
        }
    }

    #[test]
    fn test_plan_find_with_filter() {
        let q = FindQuery {
            kind: Some("function".to_string()),
            filter: Some(FilterExpr::Comparison {
                field: "name".to_string(),
                op: ComparisonOp::Matches,
                value: Value::String("auth".to_string()),
            }),
            return_fields: vec![],
            order_by: vec![],
            limit: None,
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        assert_eq!(plan.operators.len(), 2);
        match &plan.operators[0] {
            Operator::Scan { kind } => assert_eq!(kind.as_deref().unwrap(), "function"),
            _ => panic!("expected Scan"),
        }
        match &plan.operators[1] {
            Operator::Filter { sql, params } => {
                assert!(sql.contains("LIKE"));
                assert_eq!(params[0], "%auth%");
            }
            _ => panic!("expected Filter"),
        }
    }

    #[test]
    fn test_plan_find_with_return() {
        let q = FindQuery {
            kind: Some("class".to_string()),
            filter: None,
            return_fields: vec!["name".to_string(), "file_path".to_string()],
            order_by: vec![],
            limit: None,
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        assert_eq!(plan.operators.len(), 2);
        match &plan.operators[1] {
            Operator::Project { fields } => assert_eq!(fields, &vec!["name", "file_path"]),
            _ => panic!("expected Project"),
        }
    }

    #[test]
    fn test_plan_find_with_sort() {
        let q = FindQuery {
            kind: None,
            filter: None,
            return_fields: vec![],
            order_by: vec![OrderItem {
                field: "name".to_string(),
                direction: OrderDirection::Desc,
            }],
            limit: None,
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        assert_eq!(plan.operators.len(), 2);
        match &plan.operators[1] {
            Operator::Sort { field, asc } => {
                assert_eq!(field, "name");
                assert!(!asc);
            }
            _ => panic!("expected Sort"),
        }
    }

    #[test]
    fn test_plan_find_with_limit() {
        let q = FindQuery {
            kind: None,
            filter: None,
            return_fields: vec![],
            order_by: vec![],
            limit: Some(20),
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        assert_eq!(plan.operators.len(), 2);
        match &plan.operators[1] {
            Operator::Limit { count } => assert_eq!(*count, 20),
            _ => panic!("expected Limit"),
        }
    }

    #[test]
    fn test_plan_find_full() {
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
            limit: Some(10),
        };
        let plan = plan(&Statement::Find(q)).unwrap();
        // Scan, Filter, Project, Sort, Limit = 5 operators
        assert_eq!(plan.operators.len(), 5);
    }

    #[test]
    fn test_plan_impact() {
        let stmt = Statement::Impact {
            target: "MyClass.my_method".to_string(),
        };
        let plan = plan(&stmt).unwrap();
        assert!(plan.operators.is_empty());
        assert!(plan.statement.is_some());
        match plan.statement.as_ref().unwrap() {
            Statement::Impact { target } => assert_eq!(target, "MyClass.my_method"),
            _ => panic!("expected Impact"),
        }
    }

    #[test]
    fn test_plan_calls() {
        let stmt = Statement::Calls {
            target: "my_func".to_string(),
            inbound: false,
        };
        let plan = plan(&stmt).unwrap();
        assert!(plan.operators.is_empty());
        assert!(plan.statement.is_some());
        match plan.statement.as_ref().unwrap() {
            Statement::Calls { target, inbound } => {
                assert_eq!(target, "my_func");
                assert!(!inbound);
            }
            _ => panic!("expected Calls"),
        }
    }

    #[test]
    fn test_plan_trace() {
        let stmt = Statement::Trace {
            source: "main".to_string(),
            target: "parse_config".to_string(),
        };
        let plan = plan(&stmt).unwrap();
        assert!(plan.operators.is_empty());
        assert!(plan.statement.is_some());
        match plan.statement.as_ref().unwrap() {
            Statement::Trace { source, target } => {
                assert_eq!(source, "main");
                assert_eq!(target, "parse_config");
            }
            _ => panic!("expected Trace"),
        }
    }

    #[test]
    fn test_filter_and_to_sql() {
        let filter = FilterExpr::And(
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
        let (sql, params) = filter_to_sql(&filter);
        assert!(sql.contains("AND"));
        assert!(sql.contains("LIKE"));
        assert_eq!(params.len(), 2);
    }

    #[test]
    fn test_filter_or_to_sql() {
        let filter = FilterExpr::Or(
            Box::new(FilterExpr::Comparison {
                field: "name".to_string(),
                op: ComparisonOp::Matches,
                value: Value::String("foo".to_string()),
            }),
            Box::new(FilterExpr::Comparison {
                field: "name".to_string(),
                op: ComparisonOp::Matches,
                value: Value::String("bar".to_string()),
            }),
        );
        let (sql, params) = filter_to_sql(&filter);
        assert!(sql.contains("OR"));
        assert_eq!(params.len(), 2);
    }

    #[test]
    fn test_filter_not_to_sql() {
        let filter = FilterExpr::Not(Box::new(FilterExpr::Comparison {
            field: "name".to_string(),
            op: ComparisonOp::Matches,
            value: Value::String("test".to_string()),
        }));
        let (sql, params) = filter_to_sql(&filter);
        assert!(sql.contains("NOT"));
        assert_eq!(params.len(), 1);
    }

    #[test]
    fn test_filter_eq_number() {
        let filter = FilterExpr::Comparison {
            field: "start_line".to_string(),
            op: ComparisonOp::Eq,
            value: Value::Number(42),
        };
        let (sql, params) = filter_to_sql(&filter);
        assert!(sql.contains("= ?1"));
        assert_eq!(params[0], "42");
    }
}
