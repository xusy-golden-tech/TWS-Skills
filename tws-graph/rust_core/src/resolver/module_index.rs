//! ModuleIndex — reverse index from module name to file paths.
//!
//! Scans the `nodes` table for all distinct file paths, infers module names
//! from file paths using language-specific rules, and builds a bidirectional
//! mapping: module name → file paths (forward) and file path → module name
//! (reverse).
//!
//! Phase 0 only implements Python module name inference rules. All other
//! languages return an empty mapping, gracefully degrading.

use crate::db::Database;
use std::collections::HashMap;

/// Bidirectional index mapping module names to file paths and vice versa.
#[derive(Debug, Clone, Default)]
pub struct ModuleIndex {
    /// module_name → Vec<file_path> (one module may span multiple files,
    /// e.g. `__init__.py` + regular modules).
    mapping: HashMap<String, Vec<String>>,
    /// file_path → module_name (reverse lookup).
    rev_mapping: HashMap<String, String>,
}

impl ModuleIndex {
    /// Build the module index from the `nodes` table.
    ///
    /// Queries distinct (file_path, language) pairs then infers a module
    /// name for each file.  For languages without inference rules (yet),
    /// the file is silently skipped.
    pub fn build(db: &Database) -> rusqlite::Result<Self> {
        let conn = db.connection();
        let mut stmt = conn.prepare("SELECT DISTINCT file_path, language FROM nodes")?;
        let rows: Vec<(String, String)> = stmt
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .filter_map(|r| r.ok())
            .collect();

        let mut mapping: HashMap<String, Vec<String>> = HashMap::new();
        let mut rev_mapping: HashMap<String, String> = HashMap::new();

        for (file_path, language) in &rows {
            let module_name = infer_module_name(file_path, language);
            if let Some(name) = module_name {
                mapping.entry(name.clone()).or_default().push(file_path.clone());
                rev_mapping.entry(file_path.clone()).or_insert(name);
            }
        }

        Ok(Self { mapping, rev_mapping })
    }

    /// Build an empty ModuleIndex (useful for testing).
    pub fn empty() -> Self {
        Self::default()
    }

    /// Look up files that belong to the given module name.
    pub fn lookup(&self, module_name: &str) -> Option<&Vec<String>> {
        self.mapping.get(module_name)
    }

    /// Reverse-lookup: given a file path, return its module name.
    pub fn rev_lookup(&self, file_path: &str) -> Option<&str> {
        self.rev_mapping.get(file_path).map(|s| s.as_str())
    }

    /// Returns the number of module entries in the index.
    pub fn len(&self) -> usize {
        self.mapping.len()
    }

    /// Returns true if the index is empty.
    pub fn is_empty(&self) -> bool {
        self.mapping.is_empty()
    }
}

// ---------------------------------------------------------------------------
// Module name inference (language-specific rules)
// ---------------------------------------------------------------------------

/// Infer a module name from a file path and language.
///
/// Each language has its own module system conventions:
///
/// | Language     | Rule                                                         |
/// |--------------|--------------------------------------------------------------|
/// | Python       | `src/foo/bar.py` → `foo.bar`                                 |
/// |              | `src/foo/bar/__init__.py` → `foo.bar`                        |
/// | TypeScript   | `src/foo/bar.ts` → `src/foo/bar` (relative path, stripped ext)|
/// | Java         | `src/com/foo/Bar.java` → `com.foo.Bar`                       |
/// | Go           | `pkg/foo/bar.go` → `foo` (parent directory = package name)  |
/// | Rust         | crate-based, not implemented yet                             |
pub fn infer_module_name(file_path: &str, language: &str) -> Option<String> {
    match language {
        "python" => infer_python_module(file_path),
        "typescript" | "javascript" | "tsx" | "jsx" => infer_typescript_module(file_path),
        "java" => infer_java_module(file_path),
        "kotlin" => infer_kotlin_module(file_path),
        "go" => infer_go_module(file_path),
        _ => None, // not yet implemented, graceful degradation
    }
}

/// Python module name inference.
///
/// Rules:
/// 1. Strip `.py` extension.
/// 2. If the filename is `__init__`, use the parent directory as the module.
/// 3. Replace path separators with dots.
/// 4. Strip common source root prefixes (`src/`, `lib/`).
fn infer_python_module(file_path: &str) -> Option<String> {
    let path = file_path.trim_end_matches('/');

    // Only handle .py files
    if !path.ends_with(".py") {
        return None;
    }

    // Strip extension
    let without_ext = &path[..path.len() - 3];

    // Handle __init__.py
    if without_ext.ends_with("__init__") {
        // Strip the trailing /__init__ or __init__
        let dir_part = if without_ext.ends_with("/__init__") {
            &without_ext[..without_ext.len() - 9]
        } else {
            // bare __init__.py — root module
            ""
        };
        if dir_part.is_empty() {
            return Some("".to_string()); // root-level __init__.py
        }
        let module = dir_part.replace('/', ".");
        return Some(strip_src_prefix(&module));
    }

    // Regular .py file: replace / with .
    let module = without_ext.replace('/', ".");
    Some(strip_src_prefix(&module))
}

/// TypeScript/JavaScript module name inference.
///
/// Rules:
/// 1. Strip known extensions (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`).
/// 2. If the filename is `index`, use the parent directory as the module.
/// 3. Replace path separators with dots.
/// 4. Strip common source root prefixes (`src/`, `lib/`).
fn infer_typescript_module(file_path: &str) -> Option<String> {
    let path = file_path.trim_end_matches('/');

    let ts_extensions = &[".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs"];
    let mut without_ext = path;

    // Find the matching extension (longest first since .tsx > .ts)
    let mut found_ext = false;
    for ext in ts_extensions {
        if path.ends_with(ext) {
            without_ext = &path[..path.len() - ext.len()];
            found_ext = true;
            break;
        }
    }

    if !found_ext {
        return None;
    }

    // Handle index files: `src/components/index.ts` → module = "components"
    if without_ext.ends_with("/index") {
        let dir_part = &without_ext[..without_ext.len() - 6]; // strip "/index"
        if dir_part.is_empty() {
            return Some(String::new()); // root-level index.ts
        }
        let module = dir_part.replace('/', ".");
        return Some(strip_src_prefix(&module));
    }

    // Handle bare "index" at root: "index.ts" → root module
    if without_ext == "index" {
        return Some(String::new());
    }

    // Regular file: replace / with .
    let module = without_ext.replace('/', ".");
    Some(strip_src_prefix(&module))
}

/// Strip common source root prefixes (`src.`, `lib.`, `test.`, `tests.`).
fn strip_src_prefix(module: &str) -> String {
    for prefix in &["src.", "lib.", "test.", "tests."] {
        if module.starts_with(prefix) {
            return module[prefix.len()..].to_string();
        }
    }
    module.to_string()
}

/// Java module name inference.
///
/// Rules:
/// 1. Strip `.java` extension.
/// 2. Strip common source root prefixes: `src/main/java/`, `src/test/java/`, `src/`.
/// 3. Replace path separators with dots.
///
/// Examples:
/// - `src/main/java/com/foo/bar/MyClass.java` → `com.foo.bar.MyClass`
/// - `src/com/example/Utils.java` → `com.example.Utils`
fn infer_java_module(file_path: &str) -> Option<String> {
    let path = file_path.trim_end_matches('/');

    // Only handle .java files
    if !path.ends_with(".java") {
        return None;
    }

    // Strip .java extension
    let without_ext = &path[..path.len() - 5];

    // Strip common Java source root prefixes
    let stripped = strip_java_source_root(without_ext);

    // Replace / with . to get dotted package notation
    let module = stripped.replace('/', ".");
    Some(module)
}

/// Strip Java source root prefixes like `src/main/java/`, `src/test/java/`, `src/`.
fn strip_java_source_root(path: &str) -> String {
    // Try specific Java Maven/Gradle conventions first (longest match)
    let prefixes = &[
        ("src/main/java/", "src/main/java/"),
        ("src/test/java/", "src/test/java/"),
        ("src/main/", "src/main/"),
        ("src/test/", "src/test/"),
        ("src/", "src/"),
        ("lib/", "lib/"),
    ];

    for (_name, prefix) in prefixes {
        if path.starts_with(prefix) {
            return path[prefix.len()..].to_string();
        }
    }

    // No prefix matched, return as-is
    path.to_string()
}

/// Kotlin module name inference.
///
/// Rules:
/// 1. Strip `.kt` or `.kts` extension.
/// 2. Strip common source root prefixes (same as Java, plus `src/main/kotlin/`).
/// 3. Replace path separators with dots.
///
/// Examples:
/// - `src/main/kotlin/com/foo/bar/MyClass.kt` → `com.foo.bar.MyClass`
/// - `src/main/java/com/foo/bar/Utils.kt` → `com.foo.bar.Utils`
/// - `src/com/example/App.kt` → `com.example.App`
fn infer_kotlin_module(file_path: &str) -> Option<String> {
    let path = file_path.trim_end_matches('/');

    // Handle .kt and .kts files
    let without_ext = if path.ends_with(".kt") && !path.ends_with(".kts") {
        &path[..path.len() - 3]
    } else if path.ends_with(".kts") {
        &path[..path.len() - 4]
    } else {
        return None;
    };

    // Strip common Kotlin/Java source root prefixes
    let stripped = strip_kotlin_source_root(without_ext);

    // Replace / with . to get dotted package notation
    let module = stripped.replace('/', ".");
    Some(module)
}

/// Strip Kotlin source root prefixes.  Kotlin can live in standard Maven/Gradle
/// directory layouts for either `kotlin` or `java` source sets.
fn strip_kotlin_source_root(path: &str) -> String {
    let prefixes = &[
        ("src/main/kotlin/", "src/main/kotlin/"),
        ("src/test/kotlin/", "src/test/kotlin/"),
        ("src/main/java/", "src/main/java/"),
        ("src/test/java/", "src/test/java/"),
        ("src/main/", "src/main/"),
        ("src/test/", "src/test/"),
        ("src/", "src/"),
        ("lib/", "lib/"),
    ];

    for (_name, prefix) in prefixes {
        if path.starts_with(prefix) {
            return path[prefix.len()..].to_string();
        }
    }

    path.to_string()
}

/// Go module name inference.
///
/// Rules:
/// 1. Only handle `.go` files.
/// 2. Use the parent directory name as the module name (Go convention:
///    directory name = package name).
/// 3. For root-level `.go` files, use `main` (the common top-level package).
///
/// Examples:
/// - `pkg/user/server.go` → `user`
/// - `internal/config/app.go` → `config`
/// - `main.go` → `main`
/// - `cmd/server/main.go` → `server`
fn infer_go_module(file_path: &str) -> Option<String> {
    let path = file_path.trim_end_matches('/');

    // Only handle .go files
    if !path.ends_with(".go") {
        return None;
    }

    // Strip .go extension → "pkg/user/server"
    let without_ext = &path[..path.len() - 3];

    // Get the directory part (strip filename)
    // For "pkg/user/server" → directory = "pkg/user"
    // For "main" → no directory (root level)
    let dir_path = if let Some(slash_pos) = without_ext.rfind('/') {
        &without_ext[..slash_pos]
    } else {
        // Root-level .go file (no parent directory)
        return Some("main".to_string());
    };

    // Get the parent directory name (last segment of directory path)
    // For "pkg/user" → "user"
    // For "cmd/server" → "server"
    let module_name = if let Some(last_slash) = dir_path.rfind('/') {
        dir_path[last_slash + 1..].to_string()
    } else {
        dir_path.to_string()
    };

    Some(module_name)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::connection::hash_id;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn setup_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_mi_{}.db", name));
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

    fn insert_node(db: &Database, name: &str, file_path: &str, lang: &str) {
        let id = hash_id(file_path, &format!("{}::{}", file_path, name));
        let ts = now_ms();
        let conn = db.connection();
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, \
             start_line, end_line, updated_at) \
             VALUES (?1, 'function', ?2, ?3, ?4, ?5, 1, 1, ?6)",
            rusqlite::params![id, name, format!("{}::{}", file_path, name), file_path, lang, ts],
        )
        .unwrap();
    }

    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    // ------------------------------------------------------------------
    // Python module name inference
    // ------------------------------------------------------------------

    #[test]
    fn test_infer_python_module_regular() {
        assert_eq!(infer_python_module("src/foo/bar.py"), Some("foo.bar".to_string()));
    }

    #[test]
    fn test_infer_python_module_init() {
        assert_eq!(
            infer_python_module("src/foo/bar/__init__.py"),
            Some("foo.bar".to_string())
        );
    }

    #[test]
    fn test_infer_python_module_root() {
        // No src/ prefix, top-level
        assert_eq!(infer_python_module("mylib.py"), Some("mylib".to_string()));
    }

    #[test]
    fn test_infer_python_module_root_init() {
        assert_eq!(infer_python_module("__init__.py"), Some("".to_string()));
    }

    #[test]
    fn test_infer_python_module_non_python() {
        assert_eq!(infer_python_module("foo.ts"), None);
    }

    #[test]
    fn test_infer_module_python_via_lang() {
        assert_eq!(
            infer_module_name("src/foo/bar.py", "python"),
            Some("foo.bar".to_string())
        );
    }

    #[test]
    fn test_infer_module_unknown_lang_graceful() {
        // Go is now supported — should return the parent directory
        assert_eq!(infer_module_name("src/foo/bar.go", "go"), Some("foo".to_string()));
        // Still unknown languages should return None
        assert_eq!(infer_module_name("src/foo/bar.rs", "rust"), None);
    }

    // ------------------------------------------------------------------
    // Java module name inference
    // ------------------------------------------------------------------

    #[test]
    fn test_infer_java_module_maven_src() {
        assert_eq!(
            infer_module_name("src/main/java/com/foo/bar/MyClass.java", "java"),
            Some("com.foo.bar.MyClass".to_string())
        );
    }

    #[test]
    fn test_infer_java_module_simple_src() {
        assert_eq!(
            infer_module_name("src/com/example/Utils.java", "java"),
            Some("com.example.Utils".to_string())
        );
    }

    #[test]
    fn test_infer_java_module_test_src() {
        assert_eq!(
            infer_module_name("src/test/java/com/foo/BarTest.java", "java"),
            Some("com.foo.BarTest".to_string())
        );
    }

    #[test]
    fn test_infer_java_module_non_java() {
        assert_eq!(infer_module_name("src/foo/bar.py", "java"), None);
        assert_eq!(infer_module_name("src/foo/bar.kt", "java"), None);
    }

    // ------------------------------------------------------------------
    // TypeScript module name inference
    // ------------------------------------------------------------------

    #[test]
    fn test_infer_typescript_module_regular() {
        assert_eq!(
            infer_module_name("src/components/Button.ts", "typescript"),
            Some("components.Button".to_string())
        );
    }

    #[test]
    fn test_infer_typescript_module_index() {
        assert_eq!(
            infer_module_name("src/components/index.ts", "typescript"),
            Some("components".to_string())
        );
    }

    #[test]
    fn test_infer_typescript_module_tsx() {
        assert_eq!(
            infer_module_name("src/pages/Home.tsx", "typescript"),
            Some("pages.Home".to_string())
        );
    }

    #[test]
    fn test_infer_typescript_module_js() {
        assert_eq!(
            infer_module_name("src/utils/helpers.js", "javascript"),
            Some("utils.helpers".to_string())
        );
    }

    #[test]
    fn test_infer_typescript_module_non_ts() {
        assert_eq!(infer_module_name("src/foo.py", "typescript"), None);
    }

    // ------------------------------------------------------------------
    // Kotlin module name inference
    // ------------------------------------------------------------------

    #[test]
    fn test_infer_kotlin_module_maven_src() {
        assert_eq!(
            infer_module_name("src/main/kotlin/com/foo/bar/MyClass.kt", "kotlin"),
            Some("com.foo.bar.MyClass".to_string())
        );
    }

    #[test]
    fn test_infer_kotlin_module_simple_src() {
        assert_eq!(
            infer_module_name("src/com/example/Utils.kt", "kotlin"),
            Some("com.example.Utils".to_string())
        );
    }

    #[test]
    fn test_infer_kotlin_module_test_src() {
        assert_eq!(
            infer_module_name("src/test/kotlin/com/foo/BarTest.kt", "kotlin"),
            Some("com.foo.BarTest".to_string())
        );
    }

    #[test]
    fn test_infer_kotlin_module_script_file() {
        assert_eq!(
            infer_module_name("src/main/kotlin/com/example/script.kts", "kotlin"),
            Some("com.example.script".to_string())
        );
    }

    #[test]
    fn test_infer_kotlin_module_non_kotlin() {
        assert_eq!(infer_module_name("src/foo/bar.java", "kotlin"), None);
        assert_eq!(infer_module_name("src/foo/bar.py", "kotlin"), None);
    }

    #[test]
    fn test_infer_kotlin_module_in_java_src_tree() {
        // Kotlin files can coexist in src/main/java/
        assert_eq!(
            infer_module_name("src/main/java/com/foo/bar/Utils.kt", "kotlin"),
            Some("com.foo.bar.Utils".to_string())
        );
    }

    #[test]
    fn test_infer_typescript_module_root_index() {
        assert_eq!(
            infer_module_name("index.ts", "typescript"),
            Some("".to_string())
        );
    }

    #[test]
    fn test_infer_module_from_javascript_variants() {
        // JavaScript uses the same resolver as TypeScript
        assert_eq!(
            infer_module_name("lib/core.jsx", "javascript"),
            Some("core".to_string())
        );
        assert_eq!(
            infer_module_name("lib/core.mjs", "javascript"),
            Some("core".to_string())
        );
    }

    // ------------------------------------------------------------------
    // ModuleIndex construction
    // ------------------------------------------------------------------

    #[test]
    fn test_build_module_index_from_db() {
        let (db, path) = setup_db("build_index");
        insert_node(&db, "foo", "src/pkg/module_a.py", "python");
        insert_node(&db, "bar", "src/pkg/module_a.py", "python");
        insert_node(&db, "baz", "src/pkg/module_b.py", "python");
        insert_node(&db, "qux", "src/utils/helpers.py", "python");

        // Also insert a TypeScript file (now supported)
        insert_node(&db, "comp", "src/components/App.tsx", "typescript");

        let idx = ModuleIndex::build(&db).unwrap();
        // We should have 4 modules (3 Python + 1 TypeScript)
        assert_eq!(idx.len(), 4);
        assert!(idx.lookup("pkg.module_a").is_some());
        assert!(idx.lookup("pkg.module_b").is_some());
        assert!(idx.lookup("utils.helpers").is_some());
        // TypeScript module should now be inferred
        assert!(idx.lookup("components.App").is_some());

        cleanup(&path);
    }

    #[test]
    fn test_rev_lookup() {
        let (db, path) = setup_db("rev_lookup");
        insert_node(&db, "func", "src/mypkg/core.py", "python");

        let idx = ModuleIndex::build(&db).unwrap();
        assert_eq!(idx.rev_lookup("src/mypkg/core.py"), Some("mypkg.core"));

        cleanup(&path);
    }

    #[test]
    fn test_empty_index() {
        let idx = ModuleIndex::empty();
        assert!(idx.is_empty());
        assert_eq!(idx.len(), 0);
        assert!(idx.lookup("anything").is_none());
        assert!(idx.rev_lookup("anything.py").is_none());
    }

    #[test]
    fn test_multiple_files_same_module() {
        // __init__.py and other .py files in same directory share module name
        let (db, path) = setup_db("multi_files");
        insert_node(&db, "a", "src/pkg/__init__.py", "python");
        insert_node(&db, "b", "src/pkg/utils.py", "python");

        let idx = ModuleIndex::build(&db).unwrap();
        assert_eq!(idx.len(), 2);
        // pkg module → files
        let pkg_files = idx.lookup("pkg").unwrap();
        assert!(pkg_files.contains(&"src/pkg/__init__.py".to_string()));
        let utils_files = idx.lookup("pkg.utils").unwrap();
        assert!(utils_files.contains(&"src/pkg/utils.py".to_string()));

        cleanup(&path);
    }

    // ------------------------------------------------------------------
    // Go module name inference
    // ------------------------------------------------------------------

    #[test]
    fn test_infer_go_module_basic() {
        assert_eq!(
            infer_module_name("pkg/user/server.go", "go"),
            Some("user".to_string())
        );
        assert_eq!(
            infer_module_name("internal/config/app.go", "go"),
            Some("config".to_string())
        );
        assert_eq!(
            infer_module_name("cmd/server/main.go", "go"),
            Some("server".to_string())
        );
    }

    #[test]
    fn test_infer_go_module_root_file() {
        assert_eq!(
            infer_module_name("main.go", "go"),
            Some("main".to_string())
        );
    }

    #[test]
    fn test_infer_go_module_non_go() {
        assert_eq!(infer_module_name("pkg/user/server.py", "go"), None);
        assert_eq!(infer_module_name("pkg/user/server.java", "go"), None);
        assert_eq!(infer_module_name("pkg/user/server.ts", "go"), None);
    }

    #[test]
    fn test_infer_go_module_nested() {
        assert_eq!(
            infer_module_name("services/api/handler/health.go", "go"),
            Some("handler".to_string())
        );
    }
}
