//! GQL planner — converts a GQL AST into an execution plan.

use super::ast::Query;

/// An execution plan that can be run against the database.
#[derive(Debug, Default)]
pub struct Plan {
    pub sql: String,
    pub params: Vec<String>,
}

/// Generate an execution plan from a parsed GQL query.
pub fn plan(_query: &Query) -> anyhow::Result<Plan> {
    // TODO: translate GQL AST to SQL + FTS5 queries
    Ok(Plan::default())
}
