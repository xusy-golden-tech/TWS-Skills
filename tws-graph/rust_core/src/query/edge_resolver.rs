//! Cross-file edge resolution.
//!
//! Resolves `[internal]` unresolved references by matching symbol names
//! across files. Uses suffix-based matching with a cascade strategy:
//! try full qualified name match, then 1-segment, 2-segment, 3-segment.
//! Supports cross-language grouping by common extensions (e.g. .ts <-> .tsx).

use crate::db::Database;

/// Attempt to resolve an internal reference to a symbol in another project file.
///
/// Strategy (cascade):
/// 1. Exact qualified name match
/// 2. 1-segment suffix (e.g. `MyClass` matching `foo.bar.MyClass`)
/// 3. 2-segment suffix (e.g. `module.MyClass` matching `foo.module.MyClass`)
/// 4. 3-segment suffix
///
/// Returns `Some((node_id, qualified_name))` if resolved, `None` otherwise.
pub fn resolve_internal(
    symbol_name: &str,
    source_file: &str,
    db: &Database,
) -> Option<(String, String)> {
    let conn = db.connection();

    // 1. Exact qualified name match
    if let Ok(Some(row)) = query_node_by_qualified(conn, symbol_name) {
        return Some(row);
    }

    // 2. Exact name match (simple name)
    if let Ok(Some(row)) = query_node_by_name(conn, symbol_name) {
        // If there's exactly one match, return it
        let count = count_by_name(conn, symbol_name).unwrap_or(0);
        if count == 1 {
            return Some(row);
        }
        // Multiple matches — try suffix cascade to narrow down
    }

    // 3. Suffix cascade: try matching the last N segments
    let segments: Vec<&str> = symbol_name.split('.').collect();
    let max_segments = segments.len().min(3);

    for n in 1..=max_segments {
        if n > segments.len() {
            break;
        }
        let suffix: String = segments[segments.len() - n..].join(".");
        if let Ok(Some(row)) = query_node_by_qualified_suffix(conn, &suffix) {
            return Some(row);
        }
    }

    // 4. Cross-language grouping: try matching with source file's language
    let source_lang = infer_language_from_path(source_file);
    if !source_lang.is_empty() {
        if let Ok(Some(row)) = query_node_by_name_and_lang(conn, symbol_name, &source_lang) {
            return Some(row);
        }
    }

    None
}

/// Classify a reference as `[external]` (third-party) or `[internal]` (project).
///
/// Returns `"external"` if the symbol appears to be from a standard library
/// or known third-party package, `"internal"` otherwise.
pub fn classify_reference(
    symbol_name: &str,
    source_file: &str,
    db: &Database,
) -> &'static str {
    // If resolved in the project DB, it's internal
    if resolve_internal(symbol_name, source_file, db).is_some() {
        return "internal";
    }

    // Otherwise classify by heuristics
    if is_likely_external(symbol_name) {
        return "external";
    }

    "internal"
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Infer a language identifier from a file path.
fn infer_language_from_path(file_path: &str) -> String {
    let lower = file_path.to_lowercase();
    if lower.ends_with(".py") {
        "python".to_string()
    } else if lower.ends_with(".ts") || lower.ends_with(".tsx") {
        "typescript".to_string()
    } else if lower.ends_with(".js") || lower.ends_with(".jsx") {
        "javascript".to_string()
    } else if lower.ends_with(".java") {
        "java".to_string()
    } else if lower.ends_with(".go") {
        "go".to_string()
    } else if lower.ends_with(".rs") {
        "rust".to_string()
    } else if lower.ends_with(".kt") || lower.ends_with(".kts") {
        "kotlin".to_string()
    } else if lower.ends_with(".php") {
        "php".to_string()
    } else if lower.ends_with(".rb") {
        "ruby".to_string()
    } else if lower.ends_with(".c") || lower.ends_with(".h") {
        "c".to_string()
    } else if lower.ends_with(".cpp") || lower.ends_with(".cc") || lower.ends_with(".cxx") || lower.ends_with(".hpp") {
        "cpp".to_string()
    } else if lower.ends_with(".cs") {
        "csharp".to_string()
    } else if lower.ends_with(".scala") {
        "scala".to_string()
    } else if lower.ends_with(".ex") || lower.ends_with(".exs") {
        "elixir".to_string()
    } else if lower.ends_with(".hs") {
        "haskell".to_string()
    } else if lower.ends_with(".clj") || lower.ends_with(".cljs") {
        "clojure".to_string()
    } else if lower.ends_with(".lua") {
        "lua".to_string()
    } else if lower.ends_with(".sh") || lower.ends_with(".bash") {
        "bash".to_string()
    } else {
        String::new()
    }
}

/// Guess if a symbol name looks like an external/third-party reference.
fn is_likely_external(name: &str) -> bool {
    let lower = name.to_lowercase();

    // Python stdlib + common packages
    let python_externals = [
        "os", "sys", "re", "json", "csv", "math", "random", "datetime", "time",
        "collections", "itertools", "functools", "typing", "io", "pathlib",
        "logging", "subprocess", "threading", "asyncio", "typer", "click",
        "fastapi", "flask", "django", "requests", "numpy", "pandas", "sqlalchemy",
        "pytest", "unittest", "argparse", "hashlib", "uuid", "base64", "copy",
        "enum", "abc", "dataclasses", "inspect", "warnings", "traceback",
        "tempfile", "shutil", "glob", "fnmatch", "pickle", "shelve", "marshal",
        "struct", "textwrap", "string", "pprint", "profile", "pdb",
    ];

    if python_externals.contains(&lower.as_str()) {
        return true;
    }

    // Common JS/TS external packages
    let js_externals = [
        "react", "express", "lodash", "axios", "moment", "chalk",
        "commander", "yargs", "inquirer", "ora", "winston", "bunyan",
        "node-fetch", "next", "vue", "angular", "svelte", "redux", "mobx",
        "jest", "mocha", "chai", "sinon", "cypress", "playwright",
        "webpack", "rollup", "vite", "esbuild", "babel", "typescript",
        "prettier", "eslint",
    ];

    if js_externals.contains(&lower.as_str()) {
        return true;
    }

    // Java/Kotlin external packages
    let java_like = ["java", "javax", "org", "com", "kotlin", "android"];
    for prefix in &java_like {
        if lower.starts_with(&format!("{}.", prefix)) || lower == *prefix {
            return true;
        }
    }

    // Rust std + common crates
    let rust_externals = ["std", "core", "alloc", "serde", "tokio", "actix",
        "axum", "warp", "reqwest", "clap", "anyhow", "thiserror"];
    if rust_externals.contains(&lower.as_str()) {
        return true;
    }

    // Go standard library prefixes
    let go_prefixes = ["fmt.", "net.", "os.", "io.", "sync.", "time.",
        "strings.", "strconv.", "errors.", "context.", "encoding/",
        "database/", "crypto/", "html/", "text/", "math/", "sort.",
        "path/", "runtime.", "reflect.", "regexp.", "bufio.", "log.",
        "flag.", "testing.", "archive/", "compress/", "container/",
        "image/", "mime/", "unicode/", "debug/"];
    for prefix in &go_prefixes {
        if lower.starts_with(prefix) {
            return true;
        }
    }

    // Generic external identifiers
    let generic_externals = [
        "self", "super", "this", "null", "undefined", "true", "false",
        "print", "len", "range", "int", "str", "float", "bool", "list",
        "dict", "set", "tuple", "type", "object", "any", "none",
        "console", "window", "document", "process", "global",
    ];

    if generic_externals.contains(&lower.as_str()) {
        return true;
    }

    false
}

/// Query node by exact qualified name.
fn query_node_by_qualified(
    conn: &rusqlite::Connection,
    qualified_name: &str,
) -> rusqlite::Result<Option<(String, String)>> {
    let mut stmt = conn.prepare(
        "SELECT id, qualified_name FROM nodes WHERE qualified_name = ?1 LIMIT 1",
    )?;
    let mut rows = stmt.query_map([qualified_name], |row| {
        Ok((row.get(0)?, row.get(1)?))
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        Some(Err(e)) => Err(e),
        None => Ok(None),
    }
}

/// Query node by exact simple name.
fn query_node_by_name(
    conn: &rusqlite::Connection,
    name: &str,
) -> rusqlite::Result<Option<(String, String)>> {
    let mut stmt = conn.prepare(
        "SELECT id, qualified_name FROM nodes WHERE name = ?1 LIMIT 1",
    )?;
    let mut rows = stmt.query_map([name], |row| {
        Ok((row.get(0)?, row.get(1)?))
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        Some(Err(e)) => Err(e),
        None => Ok(None),
    }
}

/// Count nodes matching a simple name.
fn count_by_name(
    conn: &rusqlite::Connection,
    name: &str,
) -> rusqlite::Result<i64> {
    conn.query_row(
        "SELECT COUNT(*) FROM nodes WHERE name = ?1",
        [name],
        |row| row.get(0),
    )
}

/// Query node by qualified_name suffix match (LIKE '%suffix').
fn query_node_by_qualified_suffix(
    conn: &rusqlite::Connection,
    suffix: &str,
) -> rusqlite::Result<Option<(String, String)>> {
    let pattern = format!("%::{}", suffix);
    let mut stmt = conn.prepare(
        "SELECT id, qualified_name FROM nodes WHERE qualified_name LIKE ?1 LIMIT 1",
    )?;
    let mut rows = stmt.query_map([&pattern], |row| {
        Ok((row.get(0)?, row.get(1)?))
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        Some(Err(e)) => Err(e),
        None => {
            // Also try with dot separator
            let dot_pattern = format!("%.{}", suffix);
            let mut stmt2 = conn.prepare(
                "SELECT id, qualified_name FROM nodes WHERE qualified_name LIKE ?1 LIMIT 1",
            )?;
            let mut rows2 = stmt2.query_map([&dot_pattern], |row| {
                Ok((row.get(0)?, row.get(1)?))
            })?;
            match rows2.next() {
                Some(Ok(r)) => Ok(Some(r)),
                Some(Err(e)) => Err(e),
                None => Ok(None),
            }
        }
    }
}

/// Query node by name + language.
fn query_node_by_name_and_lang(
    conn: &rusqlite::Connection,
    name: &str,
    lang: &str,
) -> rusqlite::Result<Option<(String, String)>> {
    let mut stmt = conn.prepare(
        "SELECT id, qualified_name FROM nodes WHERE name = ?1 AND language = ?2 LIMIT 1",
    )?;
    let mut rows = stmt.query_map(rusqlite::params![name, lang], |row| {
        Ok((row.get(0)?, row.get(1)?))
    })?;
    match rows.next() {
        Some(Ok(r)) => Ok(Some(r)),
        Some(Err(e)) => Err(e),
        None => Ok(None),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::connection::hash_id;
    use rusqlite::params;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_er_{}.db", name));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));

        let db = Database::initialize(&path).unwrap();
        (db, path)
    }

    fn now_ms() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64
    }

    fn insert_test_node(db: &Database, name: &str, qualified: &str, file_path: &str, lang: &str) -> String {
        let id = hash_id(file_path, qualified);
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, 1, 1, ?6)",
            params![id, name, qualified, file_path, lang, ts],
        )
        .unwrap();
        id
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    #[test]
    fn test_resolve_exact_qualified_name() {
        let (db, path) = setup_db("exact_qname");
        let _id = insert_test_node(&db, "my_func", "src.lib::my_func", "src/lib.py", "python");
        let result = resolve_internal("src.lib::my_func", "other.py", &db);
        assert!(result.is_some());
        let (_node_id, qn) = result.unwrap();
        assert_eq!(qn, "src.lib::my_func");
        cleanup(&path);
    }

    #[test]
    fn test_resolve_by_simple_name_single_match() {
        let (db, path) = setup_db("simple_name_single");
        let id = insert_test_node(&db, "unique_helper", "src.helpers::unique_helper", "src/helpers.py", "python");
        let result = resolve_internal("unique_helper", "other.py", &db);
        assert!(result.is_some());
        let (node_id, _qn) = result.unwrap();
        assert_eq!(node_id, id);
        cleanup(&path);
    }

    #[test]
    fn test_resolve_by_suffix_cascade() {
        let (db, path) = setup_db("suffix_cascade");
        // Two nodes with same simple name but different qualified names
        let id1 = insert_test_node(&db, "do_thing", "pkg.sub.mod_a::do_thing", "a.py", "python");
        let _id2 = insert_test_node(&db, "do_thing", "pkg.sub.mod_b::do_thing", "b.py", "python");
        // Resolve by suffix "mod_a.do_thing" — 2-segment
        let result = resolve_internal("mod_a.do_thing", "other.py", &db);
        assert!(result.is_some());
        let (node_id, _qn) = result.unwrap();
        assert_eq!(node_id, id1);
        cleanup(&path);
    }

    #[test]
    fn test_resolve_by_language_grouping() {
        let (db, path) = setup_db("lang_group");
        let id = insert_test_node(&db, "MyComponent", "src.components::MyComponent", "comp.ts", "typescript");
        // Reference from a .tsx file
        let result = resolve_internal("MyComponent", "app.tsx", &db);
        // .tsx maps to "typescript" and the node has language "typescript"
        assert!(result.is_some());
        assert_eq!(result.unwrap().0, id);
        cleanup(&path);
    }

    #[test]
    fn test_classify_external_python() {
        let (db, path) = setup_db("classify_ext_py");
        let result = classify_reference("os", "test.py", &db);
        assert_eq!(result, "external");
        cleanup(&path);
    }

    #[test]
    fn test_classify_external_react() {
        let (db, path) = setup_db("classify_ext_react");
        let result = classify_reference("react", "App.tsx", &db);
        assert_eq!(result, "external");
        cleanup(&path);
    }

    #[test]
    fn test_classify_external_java_package() {
        let (db, path) = setup_db("classify_ext_java");
        let result = classify_reference("java.util.List", "Main.java", &db);
        assert_eq!(result, "external");
        cleanup(&path);
    }

    #[test]
    fn test_classify_internal_resolvable() {
        let (db, path) = setup_db("classify_internal");
        let _id = insert_test_node(&db, "helper", "helpers::helper", "helpers.py", "python");
        let result = classify_reference("helper", "main.py", &db);
        assert_eq!(result, "internal");
        cleanup(&path);
    }

    #[test]
    fn test_classify_external_go_std() {
        let (db, path) = setup_db("classify_ext_go");
        let result = classify_reference("fmt.Println", "main.go", &db);
        assert_eq!(result, "external");
        cleanup(&path);
    }

    #[test]
    fn test_classify_external_rust_std() {
        let (db, path) = setup_db("classify_ext_rust");
        let result = classify_reference("std", "main.rs", &db);
        assert_eq!(result, "external");
        cleanup(&path);
    }

    #[test]
    fn test_infer_language_from_tsx_path() {
        let lang = infer_language_from_path("src/components/App.tsx");
        assert_eq!(lang, "typescript");
    }

    #[test]
    fn test_infer_language_from_unknown() {
        let lang = infer_language_from_path("README.md");
        assert_eq!(lang, "");
    }
}
