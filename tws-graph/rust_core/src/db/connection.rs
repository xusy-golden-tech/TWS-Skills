//! SQLite connection management.
//!
//! Opens (or creates) the index database with WAL journal mode and
//! a 256 MB page cache for high-throughput query workloads.

use rusqlite::{Connection, Result};
use std::path::Path;

/// Open a connection to the code-graph SQLite database.
///
/// Enables:
/// - WAL journal mode (better concurrent read performance)
/// - 256 MB cache (`cache_size = -256000`)
/// - foreign keys
/// - `mmap_size` = 256 MB
pub fn open(db_path: &Path) -> Result<Connection> {
    let conn = Connection::open(db_path)?;

    conn.execute_batch(
        "
        PRAGMA journal_mode = WAL;
        PRAGMA cache_size = -256000;
        PRAGMA foreign_keys = ON;
        PRAGMA mmap_size = 268435456;
        PRAGMA synchronous = NORMAL;
        PRAGMA temp_store = MEMORY;
        ",
    )?;

    Ok(conn)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_open_in_memory() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA journal_mode = WAL;").unwrap();
        // Should succeed
    }
}
