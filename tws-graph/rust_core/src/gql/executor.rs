//! GQL executor — runs a plan against the SQLite database.

use rusqlite::Connection;

use super::planner::Plan;

/// Execute a GQL plan and return results as JSON strings.
pub fn execute(_conn: &Connection, _plan: &Plan) -> anyhow::Result<Vec<String>> {
    // TODO: bind params, run SQL, convert rows to JSON
    Ok(Vec::new())
}
