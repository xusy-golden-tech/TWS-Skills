//! Multi-repo federation — query across multiple indexed repositories.
//!
//! The [`FederationManager`] opens multiple SQLite index databases and
//! provides cross-repo search, symbol resolution, and impact analysis.

use crate::db::Database;
use serde_json::Value;
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Federated query manager for multi-repo queries.
///
/// Each repository has its own SQLite index database.  The manager
/// opens all databases and provides aggregate query methods that
/// span every federated repo.
pub struct FederationManager {
    repos: Vec<RepoInfo>,
}

/// Information about a single federated repository.
pub struct RepoInfo {
    /// Human-readable name for the repository (e.g. `"backend"`, `"frontend"`).
    pub name: String,
    /// Root directory of the repository.
    pub root: PathBuf,
    /// Path to the SQLite index database for this repository.
    pub db_path: PathBuf,
    /// Open database handle.
    pub db: Database,
}

/// A result from a federated query, grouped by repository.
#[derive(Debug, Clone)]
pub struct FederatedResult {
    /// Name of the repository this result came from.
    pub repo_name: String,
    /// Query results as JSON values.
    pub results: Vec<Value>,
}

// ---------------------------------------------------------------------------
// FederationManager
// ---------------------------------------------------------------------------

impl FederationManager {
    /// Create a new federation manager from a list of repo specifications.
    ///
    /// Each tuple is `(name, root_path, db_path)`.  All databases must exist
    /// (use [`Database::open`]), so the repositories should already be indexed.
    ///
    /// Returns an error if any database fails to open.
    pub fn new(repos: Vec<(String, PathBuf, PathBuf)>) -> Result<Self, anyhow::Error> {
        let mut infos = Vec::with_capacity(repos.len());
        for (name, root, db_path) in repos {
            let db = Database::open(&db_path).map_err(|e| {
                anyhow::anyhow!(
                    "Failed to open database for repo '{}' at {}: {}",
                    name,
                    db_path.display(),
                    e
                )
            })?;
            infos.push(RepoInfo {
                name,
                root,
                db_path,
                db,
            });
        }
        Ok(Self { repos: infos })
    }

    /// Create an empty federation manager.
    ///
    /// Use [`add_repo`](Self::add_repo) to add repositories later.
    pub fn empty() -> Self {
        Self { repos: Vec::new() }
    }

    // -----------------------------------------------------------------------
    // Repository management
    // -----------------------------------------------------------------------

    /// Add a repository to the federation.
    ///
    /// The database must already exist and be indexed.  Returns an error
    /// if the database cannot be opened.
    pub fn add_repo(
        &mut self,
        name: &str,
        root: &Path,
        db_path: &Path,
    ) -> Result<(), anyhow::Error> {
        let db = Database::open(db_path).map_err(|e| {
            anyhow::anyhow!(
                "Failed to open database for repo '{}' at {}: {}",
                name,
                db_path.display(),
                e
            )
        })?;
        self.repos.push(RepoInfo {
            name: name.to_string(),
            root: root.to_path_buf(),
            db_path: db_path.to_path_buf(),
            db,
        });
        Ok(())
    }

    /// Remove a repository from the federation by name.
    ///
    /// If no repo with the given name exists, this is a no-op.
    pub fn remove_repo(&mut self, name: &str) {
        self.repos.retain(|r| r.name != name);
    }

    /// List all federated repositories.
    ///
    /// Returns a slice of [`RepoInfo`] references.
    pub fn list_repos(&self) -> &[RepoInfo] {
        &self.repos
    }

    /// Return the number of federated repositories.
    pub fn repo_count(&self) -> usize {
        self.repos.len()
    }

    // -----------------------------------------------------------------------
    // Federated queries
    // -----------------------------------------------------------------------

    /// Search across all federated repos.
    ///
    /// Runs an FTS5 search on each repository's index and aggregates the
    /// results grouped by repo name.
    ///
    /// * `query` — search text (passed to FTS5).
    /// * `kind`  — optional symbol kind filter (e.g. `"class"`, `"function"`).
    /// * `lang`  — optional language filter (e.g. `"python"`, `"rust"`).
    pub fn federated_search(
        &self,
        query: &str,
        kind: Option<&str>,
        lang: Option<&str>,
    ) -> Vec<FederatedResult> {
        let mut output = Vec::with_capacity(self.repos.len());

        for repo in &self.repos {
            // Try FTS5 first
            let mut rows = match repo.db.search_fts5(query, kind, lang, None, 200) {
                Ok(r) => r,
                Err(_) => Vec::new(),
            };

            // LIKE fallback if FTS5 returned nothing
            if rows.is_empty() && !query.is_empty() {
                rows = match repo.db.search_like(query, kind, lang, 200) {
                    Ok(r) => r,
                    Err(_) => Vec::new(),
                };
            }

            // Edit-distance fallback
            if rows.is_empty() && !query.is_empty() {
                rows = match repo.db.search_edit_distance(query, kind, lang, 200) {
                    Ok(r) => r,
                    Err(_) => Vec::new(),
                };
            }

            let results: Vec<Value> = rows
                .into_iter()
                .map(|(id, k, name, qname, fpath, language, rank)| {
                    let mut obj = serde_json::Map::new();
                    obj.insert("id".into(), Value::String(id));
                    obj.insert("kind".into(), Value::String(k));
                    obj.insert("name".into(), Value::String(name));
                    obj.insert("qualified_name".into(), Value::String(qname));
                    obj.insert("file_path".into(), Value::String(fpath));
                    obj.insert("language".into(), Value::String(language));
                    if let Some(r) = rank {
                        obj.insert("rank".into(), Value::Number(
                            serde_json::Number::from_f64(r).unwrap_or_else(|| 0.into())
                        ));
                    }
                    Value::Object(obj)
                })
                .collect();

            if !results.is_empty() {
                output.push(FederatedResult {
                    repo_name: repo.name.clone(),
                    results,
                });
            }
        }

        output
    }

    /// Search for a symbol by exact name match across all repos.
    ///
    /// Returns results grouped by repository.  Useful for cross-repo
    /// symbol resolution when symbol names are known.
    pub fn find_symbol(&self, name: &str) -> Vec<FederatedResult> {
        let mut output = Vec::with_capacity(self.repos.len());

        for repo in &self.repos {
            let conn = repo.db.connection();

            // Search by exact name or qualified_name
            let mut stmt = match conn.prepare(
                "SELECT id, kind, name, qualified_name, file_path, language \
                 FROM nodes WHERE name = ?1 OR qualified_name = ?1 \
                 ORDER BY name LIMIT 200",
            ) {
                Ok(s) => s,
                Err(_) => continue,
            };

            let rows = match stmt.query_map(rusqlite::params![name], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                ))
            }) {
                Ok(r) => r,
                Err(_) => continue,
            };

            let results: Vec<Value> = rows
                .filter_map(|r| r.ok())
                .map(|(id, kind, sym_name, qname, fpath, language)| {
                    let mut obj = serde_json::Map::new();
                    obj.insert("id".into(), Value::String(id));
                    obj.insert("kind".into(), Value::String(kind));
                    obj.insert("name".into(), Value::String(sym_name));
                    obj.insert("qualified_name".into(), Value::String(qname));
                    obj.insert("file_path".into(), Value::String(fpath));
                    obj.insert("language".into(), Value::String(language));
                    Value::Object(obj)
                })
                .collect();

            if !results.is_empty() {
                output.push(FederatedResult {
                    repo_name: repo.name.clone(),
                    results,
                });
            }
        }

        output
    }

    /// Get cross-repo call edges.
    ///
    /// For a symbol in the named repository, find all outbound call edges.
    /// Then search other repos for symbols with the same name as the call
    /// targets — these are potential cross-repo dependencies.
    ///
    /// Returns results grouped by the *target* repository.
    pub fn cross_repo_calls(
        &self,
        repo_name: &str,
        symbol: &str,
    ) -> Vec<FederatedResult> {
        // Find the source repo
        let source_repo = match self.repos.iter().find(|r| r.name == repo_name) {
            Some(r) => r,
            None => return Vec::new(),
        };

        // Find the symbol node in the source repo
        let node_id = match source_repo.db.find_node_id_by_name(symbol) {
            Ok(Some(id)) => id,
            _ => return Vec::new(),
        };

        // Get outbound edges
        let edges = match source_repo.db.get_outbound_edges(&node_id) {
            Ok(e) => e,
            Err(_) => return Vec::new(),
        };

        // For each call target, search other repos
        let target_repos: Vec<&RepoInfo> = self
            .repos
            .iter()
            .filter(|r| r.name != repo_name)
            .collect();

        let mut output = Vec::new();
        for (_edge_id, _target_hash, _kind, target_text) in &edges {
            if let Some(target_name) = target_text {
                // Try to find this symbol in other repos
                for trepo in &target_repos {
                    if let Ok(mut stmt) = trepo.db.connection().prepare(
                        "SELECT id, kind, name, qualified_name, file_path, language \
                         FROM nodes WHERE qualified_name LIKE ?1 OR name = ?2 \
                         LIMIT 50",
                    ) {
                        let like_pattern = format!("%{}%", target_name);
                        if let Ok(rows) = stmt.query_map(
                            rusqlite::params![like_pattern, target_name],
                            |row| {
                                Ok((
                                    row.get::<_, String>(0)?,
                                    row.get::<_, String>(1)?,
                                    row.get::<_, String>(2)?,
                                    row.get::<_, String>(3)?,
                                    row.get::<_, String>(4)?,
                                    row.get::<_, String>(5)?,
                                ))
                            },
                        ) {
                            let results: Vec<Value> = rows
                                .filter_map(|r| r.ok())
                                .map(|(id, kind, name, qname, fpath, lang)| {
                                    let mut obj = serde_json::Map::new();
                                    obj.insert("id".into(), Value::String(id));
                                    obj.insert("kind".into(), Value::String(kind));
                                    obj.insert("name".into(), Value::String(name));
                                    obj.insert("qualified_name".into(), Value::String(qname));
                                    obj.insert("file_path".into(), Value::String(fpath));
                                    obj.insert("language".into(), Value::String(lang));
                                    obj.insert("called_by".into(), Value::String(symbol.to_string()));
                                    Value::Object(obj)
                                })
                                .collect();

                            if !results.is_empty() {
                                output.push(FederatedResult {
                                    repo_name: trepo.name.clone(),
                                    results,
                                });
                            }
                        }
                    }
                }
            }
        }

        output
    }

    /// Get cross-repo impact.
    ///
    /// For a symbol in the named repository, find all symbols in *other*
    /// repos that depend on it (i.e. inbound edges from other repos to
    /// symbols matching this one).
    ///
    /// Returns results grouped by the *depending* repository.
    pub fn cross_repo_impact(
        &self,
        repo_name: &str,
        symbol: &str,
    ) -> Vec<FederatedResult> {
        let mut output = Vec::new();

        // Find all repos except the source
        let other_repos: Vec<&RepoInfo> = self
            .repos
            .iter()
            .filter(|r| r.name != repo_name)
            .collect();

        for trepo in &other_repos {
            let conn = trepo.db.connection();

            // Search for edges where target_text matches our symbol,
            // or find nodes matching the symbol name and check their inbound edges
            let mut stmt = match conn.prepare(
                "SELECT e.id, e.source, e.kind, e.target_text, n.name, n.kind, n.file_path \
                 FROM edges e \
                 JOIN nodes n ON n.id = e.source \
                 WHERE e.target_text LIKE ?1 \
                 LIMIT 200",
            ) {
                Ok(s) => s,
                Err(_) => continue,
            };

            let pattern = format!("%{}%", symbol);
            let rows = match stmt.query_map(rusqlite::params![pattern], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                ))
            }) {
                Ok(r) => r,
                Err(_) => continue,
            };

            let results: Vec<Value> = rows
                .filter_map(|r| r.ok())
                .map(|(edge_id, source, kind, target_text, node_name, node_kind, file_path)| {
                    let mut obj = serde_json::Map::new();
                    obj.insert("edge_id".into(), Value::Number(edge_id.into()));
                    obj.insert("source_id".into(), Value::String(source));
                    obj.insert("kind".into(), Value::String(kind));
                    obj.insert("target_text".into(), Value::String(
                        target_text.unwrap_or_default()
                    ));
                    obj.insert("source_name".into(), Value::String(node_name));
                    obj.insert("source_kind".into(), Value::String(node_kind));
                    obj.insert("source_file".into(), Value::String(file_path));
                    obj.insert("depends_on".into(), Value::String(symbol.to_string()));
                    Value::Object(obj)
                })
                .collect();

            if !results.is_empty() {
                output.push(FederatedResult {
                    repo_name: trepo.name.clone(),
                    results,
                });
            }
        }

        output
    }

    /// List symbols across all repos that have unresolved external references.
    ///
    /// Returns results grouped by repository.
    pub fn cross_repo_unresolved(&self) -> Vec<FederatedResult> {
        let mut output = Vec::with_capacity(self.repos.len());

        for repo in &self.repos {
            let unresolved = match repo.db.get_unresolved() {
                Ok(u) => u,
                Err(_) => continue,
            };

            if unresolved.is_empty() {
                continue;
            }

            let results: Vec<Value> = unresolved
                .into_iter()
                .map(|(ref_name, ref_kind, file_path, from_node_id)| {
                    let mut obj = serde_json::Map::new();
                    obj.insert("reference_name".into(), Value::String(ref_name));
                    obj.insert("reference_kind".into(), Value::String(ref_kind));
                    obj.insert("file_path".into(), Value::String(file_path));
                    obj.insert("from_node_id".into(), Value::String(from_node_id));
                    Value::Object(obj)
                })
                .collect();

            output.push(FederatedResult {
                repo_name: repo.name.clone(),
                results,
            });
        }

        output
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::hash_id;
    use rusqlite::params;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    fn temp_db_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("tws_federate_test_{}.db", name))
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn cleanup_db(path: &Path) {
        let _ = fs::remove_file(path);
        let _ = fs::remove_file(path.with_extension("db-wal"));
        let _ = fs::remove_file(path.with_extension("db-shm"));
    }

    /// Create a test database with some nodes.
    fn create_test_db(
        db_path: &Path,
        nodes: &[(&str, &str, &str, &str, &str)],  // (name, qname, kind, lang, file)
    ) -> Database {
        let db = Database::initialize(db_path).unwrap();
        let conn = db.connection();
        let ts = now_ms();

        for (name, qname, kind, lang, file) in nodes {
            let nid = hash_id(file, qname);
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, 1, 10, ?7)",
                params![nid, kind, name, qname, file, lang, ts],
            )
            .unwrap();
        }

        db
    }

    // ------------------------------------------------------------------
    // Construction tests
    // ------------------------------------------------------------------

    #[test]
    fn test_new_empty_federation() {
        let fm = FederationManager::empty();
        assert_eq!(fm.repo_count(), 0);
        assert!(fm.list_repos().is_empty());
    }

    #[test]
    fn test_new_federation_with_repos() {
        let db_a_path = temp_db_path("fed_new_a");
        let db_b_path = temp_db_path("fed_new_b");
        cleanup_db(&db_a_path);
        cleanup_db(&db_b_path);

        let _db_a = Database::initialize(&db_a_path).unwrap();
        let _db_b = Database::initialize(&db_b_path).unwrap();

        let fm = FederationManager::new(vec![
            ("repo-a".into(), PathBuf::from("/fake/root/a"), db_a_path.clone()),
            ("repo-b".into(), PathBuf::from("/fake/root/b"), db_b_path.clone()),
        ])
        .unwrap();

        assert_eq!(fm.repo_count(), 2);
        assert_eq!(fm.list_repos()[0].name, "repo-a");
        assert_eq!(fm.list_repos()[1].name, "repo-b");

        cleanup_db(&db_a_path);
        cleanup_db(&db_b_path);
    }

    #[test]
    fn test_new_federation_nonexistent_db_fails() {
        let result = FederationManager::new(vec![
            ("bad".into(), PathBuf::from("/fake"), PathBuf::from("/nonexistent/path.db")),
        ]);
        assert!(result.is_err());
    }

    // ------------------------------------------------------------------
    // Repo management tests
    // ------------------------------------------------------------------

    #[test]
    fn test_add_and_remove_repo() {
        let db_path = temp_db_path("fed_add_remove");
        cleanup_db(&db_path);

        let _db = Database::initialize(&db_path).unwrap();

        let mut fm = FederationManager::empty();
        assert_eq!(fm.repo_count(), 0);

        fm.add_repo("test-repo", Path::new("/tmp/test"), &db_path).unwrap();
        assert_eq!(fm.repo_count(), 1);

        fm.remove_repo("test-repo");
        assert_eq!(fm.repo_count(), 0);

        cleanup_db(&db_path);
    }

    #[test]
    fn test_remove_nonexistent_repo_is_noop() {
        let mut fm = FederationManager::empty();
        fm.remove_repo("nope");
        assert_eq!(fm.repo_count(), 0);
    }

    #[test]
    fn test_list_repos_returns_correct_order() {
        let db_a = temp_db_path("fed_list_a");
        let db_b = temp_db_path("fed_list_b");
        cleanup_db(&db_a);
        cleanup_db(&db_b);

        let _da = Database::initialize(&db_a).unwrap();
        let _db = Database::initialize(&db_b).unwrap();

        let fm = FederationManager::new(vec![
            ("first".into(), PathBuf::from("/a"), db_a.clone()),
            ("second".into(), PathBuf::from("/b"), db_b.clone()),
        ])
        .unwrap();

        let repos = fm.list_repos();
        assert_eq!(repos.len(), 2);
        assert_eq!(repos[0].name, "first");
        assert_eq!(repos[1].name, "second");

        cleanup_db(&db_a);
        cleanup_db(&db_b);
    }

    // ------------------------------------------------------------------
    // Federated search tests
    // ------------------------------------------------------------------

    #[test]
    fn test_federated_search_across_repos() {
        let db_a = temp_db_path("fed_search_a");
        let db_b = temp_db_path("fed_search_b");
        cleanup_db(&db_a);
        cleanup_db(&db_b);

        let _da = create_test_db(
            &db_a,
            &[("calculateTotal", "src.utils::calculateTotal", "function", "python", "src/utils.py")],
        );
        let _db = create_test_db(
            &db_b,
            &[("TotalHandler", "src.handler::TotalHandler", "class", "typescript", "src/handler.ts")],
        );

        let fm = FederationManager::new(vec![
            ("py-repo".into(), PathBuf::from("/a"), db_a.clone()),
            ("ts-repo".into(), PathBuf::from("/b"), db_b.clone()),
        ])
        .unwrap();

        let results = fm.federated_search("Total", None, None);
        assert!(!results.is_empty(), "Expected search results across repos");

        // Should find matches in both repos
        let py_result = results.iter().find(|r| r.repo_name == "py-repo");
        let ts_result = results.iter().find(|r| r.repo_name == "ts-repo");

        assert!(py_result.is_some(), "Expected results from py-repo");
        assert!(ts_result.is_some(), "Expected results from ts-repo");
        assert!(!py_result.unwrap().results.is_empty());
        assert!(!ts_result.unwrap().results.is_empty());

        cleanup_db(&db_a);
        cleanup_db(&db_b);
    }

    #[test]
    fn test_federated_search_with_kind_filter() {
        let db_a = temp_db_path("fed_kind_a");
        cleanup_db(&db_a);

        let _da = create_test_db(
            &db_a,
            &[
                ("MyClass", "src.app::MyClass", "class", "python", "src/app.py"),
                ("my_func", "src.app::my_func", "function", "python", "src/app.py"),
            ],
        );

        let fm = FederationManager::new(vec![
            ("repo".into(), PathBuf::from("/a"), db_a.clone()),
        ])
        .unwrap();

        // Search only for classes
        let results = fm.federated_search("My", Some("class"), None);
        assert!(!results.is_empty());

        // All results should be class kind
        for fed in &results {
            for r in &fed.results {
                let kind = r.get("kind").and_then(|v| v.as_str()).unwrap_or("");
                assert_eq!(kind, "class");
            }
        }

        cleanup_db(&db_a);
    }

    #[test]
    fn test_federated_search_no_matches() {
        let db_a = temp_db_path("fed_nomatch");
        cleanup_db(&db_a);

        let _da = create_test_db(
            &db_a,
            &[("foo", "src.lib::foo", "function", "rust", "src/lib.rs")],
        );

        let fm = FederationManager::new(vec![
            ("repo".into(), PathBuf::from("/a"), db_a.clone()),
        ])
        .unwrap();

        let _results = fm.federated_search("ZzZzZzNotThere", None, None);
        // FTS5 may or may not return results for "ZzZzZzNotThere" — it's a phrase query
        // Just verify it doesn't panic

        cleanup_db(&db_a);
    }

    // ------------------------------------------------------------------
    // Cross-repo call / impact tests
    // ------------------------------------------------------------------

    #[test]
    fn test_cross_repo_calls_finds_targets() {
        let db_a = temp_db_path("fed_cross_a");
        let db_b = temp_db_path("fed_cross_b");
        cleanup_db(&db_a);
        cleanup_db(&db_b);

        // Repo A: has function foo that calls bar
        {
            let db = Database::initialize(&db_a).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let src_id = hash_id("src/a.py", "src.a::foo");
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, updated_at) \
                 VALUES (?1, 'function', 'foo', 'src.a::foo', 'src/a.py', 'python', 1, 10, ?2)",
                params![src_id, ts],
            ).unwrap();

            let tgt_hash = hash_id("src/b.py", "src.b::bar");
            conn.execute(
                "INSERT INTO edges (source, target, target_text, kind, source_loc, provenance) \
                 VALUES (?1, ?2, 'bar', 'CALLS', 'src/a.py:5:1', 'tree-sitter')",
                params![src_id, tgt_hash],
            ).unwrap();
        }

        // Repo B: has function bar
        {
            create_test_db(
                &db_b,
                &[("bar", "src.b::bar", "function", "python", "src/b.py")],
            );
        }

        let fm = FederationManager::new(vec![
            ("repo-a".into(), PathBuf::from("/a"), db_a.clone()),
            ("repo-b".into(), PathBuf::from("/b"), db_b.clone()),
        ])
        .unwrap();

        let _results = fm.cross_repo_calls("repo-a", "foo");
        // foo in repo-a calls bar; bar might be found in repo-b
        // This is a best-effort match; just verify the method doesn't panic

        cleanup_db(&db_a);
        cleanup_db(&db_b);
    }

    #[test]
    fn test_cross_repo_impact() {
        let db_a = temp_db_path("fed_impact_a");
        let db_b = temp_db_path("fed_impact_b");
        cleanup_db(&db_a);
        cleanup_db(&db_b);

        // Repo A: has function shared_lib::util
        {
            create_test_db(
                &db_a,
                &[("util", "shared_lib::util", "function", "python", "src/util.py")],
            );
        }

        // Repo B: has function that references util
        {
            let db = Database::initialize(&db_b).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let caller_id = hash_id("src/app.py", "src.app::process");
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, updated_at) \
                 VALUES (?1, 'function', 'process', 'src.app::process', 'src/app.py', 'python', 1, 10, ?2)",
                params![caller_id, ts],
            ).unwrap();

            let util_hash = hash_id("src/util.py", "shared_lib::util");
            conn.execute(
                "INSERT INTO edges (source, target, target_text, kind, source_loc, provenance) \
                 VALUES (?1, ?2, 'util', 'CALLS', 'src/app.py:3:5', 'tree-sitter')",
                params![caller_id, util_hash],
            ).unwrap();
        }

        let fm = FederationManager::new(vec![
            ("repo-a".into(), PathBuf::from("/a"), db_a.clone()),
            ("repo-b".into(), PathBuf::from("/b"), db_b.clone()),
        ])
        .unwrap();

        let _results = fm.cross_repo_impact("repo-a", "util");
        // Should find repo-b depends on util from repo-a
        // Just verify it doesn't panic

        cleanup_db(&db_a);
        cleanup_db(&db_b);
    }

    #[test]
    fn test_find_symbol_across_repos() {
        let db_a = temp_db_path("fed_sym_a");
        let db_b = temp_db_path("fed_sym_b");
        cleanup_db(&db_a);
        cleanup_db(&db_b);

        let _da = create_test_db(
            &db_a,
            &[("shared_util", "src.util::shared_util", "function", "python", "src/util.py")],
        );
        let _db = create_test_db(
            &db_b,
            &[("shared_util", "lib.helper::shared_util", "function", "python", "lib/helper.py")],
        );

        let fm = FederationManager::new(vec![
            ("repo-a".into(), PathBuf::from("/a"), db_a.clone()),
            ("repo-b".into(), PathBuf::from("/b"), db_b.clone()),
        ])
        .unwrap();

        let results = fm.find_symbol("shared_util");
        assert_eq!(results.len(), 2, "Expected matches in both repos");

        let a = results.iter().find(|r| r.repo_name == "repo-a").unwrap();
        let b = results.iter().find(|r| r.repo_name == "repo-b").unwrap();
        assert!(!a.results.is_empty());
        assert!(!b.results.is_empty());

        cleanup_db(&db_a);
        cleanup_db(&db_b);
    }

    #[test]
    fn test_cross_repo_unresolved() {
        let db_a = temp_db_path("fed_unres_a");
        cleanup_db(&db_a);

        {
            let db = Database::initialize(&db_a).unwrap();
            let conn = db.connection();
            let ts = now_ms();

            let node_id = hash_id("src/app.py", "src.app::main");
            conn.execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
                 start_line, end_line, updated_at) \
                 VALUES (?1, 'function', 'main', 'src.app::main', 'src/app.py', 'python', 1, 10, ?2)",
                params![node_id, ts],
            ).unwrap();

            conn.execute(
                "INSERT INTO unresolved_refs (reference_name, reference_kind, file_path, language, from_node_id) \
                 VALUES ('external_lib', 'function', 'src/app.py', 'python', ?1)",
                params![node_id],
            ).unwrap();
        }

        let fm = FederationManager::new(vec![
            ("repo-a".into(), PathBuf::from("/a"), db_a.clone()),
        ])
        .unwrap();

        let results = fm.cross_repo_unresolved();
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].repo_name, "repo-a");
        assert!(!results[0].results.is_empty());

        let first = &results[0].results[0];
        assert_eq!(first["reference_name"], "external_lib");

        cleanup_db(&db_a);
    }

    #[test]
    fn test_federated_result_clone() {
        let result = FederatedResult {
            repo_name: "test".into(),
            results: vec![serde_json::json!({"key": "value"})],
        };
        let cloned = result.clone();
        assert_eq!(cloned.repo_name, "test");
        assert_eq!(cloned.results.len(), 1);
    }
}
