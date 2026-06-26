//! GQL AST node definitions.

/// Top-level GQL query.
#[derive(Debug, Default, Clone)]
pub struct Query {
    pub clauses: Vec<Clause>,
}

/// A single clause in a GQL query (FIND, WHERE, RETURN, LIMIT, etc.).
#[derive(Debug, Clone)]
pub enum Clause {
    Find {
        kind: String,
    },
    Where {
        field: String,
        op: MatchOp,
        value: String,
    },
    Return {
        fields: Vec<String>,
    },
    Limit {
        count: usize,
    },
    Impact {
        target: String,
    },
}

/// Match operators.
#[derive(Debug, Clone)]
pub enum MatchOp {
    Matches,
    Equals,
}
