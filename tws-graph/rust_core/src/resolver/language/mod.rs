//! Language registry — maps language identifiers to their ModuleResolvers.
//!
//! Phase 0 provides a skeleton Python resolver. Future phases will add
//! TypeScript, Java, Go, Rust, and other language-specific resolvers.

use crate::resolver::ModuleResolver;
use crate::resolver::module_index::ModuleIndex;
use std::path::Path;

/// Registry that maps language names (e.g. "python", "typescript") to their
/// dedicated [`ModuleResolver`] implementations.
pub struct LanguageRegistry;

impl LanguageRegistry {
    /// Return the resolver for the given language, or `None` if the language
    /// is not yet supported.
    pub fn get(language: &str) -> Option<Box<dyn ModuleResolver>> {
        match language {
            "python" => Some(Box::new(PythonResolver)),
            _ => None,
        }
    }

    /// Return a list of language names that have resolvers registered.
    pub fn supported_languages() -> Vec<&'static str> {
        vec!["python"]
    }
}

// ---------------------------------------------------------------------------
// Python module resolver (Phase 0 — skeleton)
// ---------------------------------------------------------------------------

/// Python module resolver.
///
/// Uses Python import conventions:
/// - `from foo.bar import Baz` → look for `Baz` in `foo/bar.py` or `foo/bar/__init__.py`
/// - `import foo.bar` → treat `foo.bar` as the module
pub struct PythonResolver;

impl ModuleResolver for PythonResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        // Direct lookup: module_name → files
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // Try with common prefixes (if module_index has entries with/without src/)
        for prefix in &["", "src.", "lib."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        Vec::new()
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "python")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_python_external(module_name)
    }

    fn language(&self) -> &'static str {
        "python"
    }
}

/// Check if a module name is a known Python standard library or popular
/// third-party package.
///
/// This mirrors the heuristic in `query::edge_resolver::is_likely_external`
/// but is scoped to Python-only and used by the `ModuleResolver` trait.
pub fn is_python_external(module_name: &str) -> bool {
    let lower = module_name.to_lowercase();
    let root = lower.split('.').next().unwrap_or(&lower);

    let stdlib_and_common: &[&str] = &[
        // stdlib
        "os", "sys", "re", "json", "csv", "math", "random", "datetime", "time",
        "collections", "itertools", "functools", "typing", "io", "pathlib",
        "logging", "subprocess", "threading", "asyncio", "argparse", "hashlib",
        "uuid", "base64", "copy", "enum", "abc", "dataclasses", "inspect",
        "warnings", "traceback", "tempfile", "shutil", "glob", "fnmatch",
        "pickle", "shelve", "marshal", "struct", "textwrap", "string", "pprint",
        "profile", "pdb", "unittest",
        // common third-party
        "typer", "click", "fastapi", "flask", "django", "requests", "numpy",
        "pandas", "sqlalchemy", "pytest", "pydantic", "sqlite3", "concurrent",
        "http", "xml", "html", "urllib", "email", "socket", "ssl", "ctypes",
        "decimal", "fractions", "statistics",
    ];

    if stdlib_and_common.contains(&root) {
        return true;
    }

    // Internal Python dunder / special names
    if root.starts_with("__") || root.starts_with("_") {
        return false; // private/internal modules are not external
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn test_python_resolver_is_external_stdlib() {
        let resolver = PythonResolver;
        assert!(resolver.is_external("os"));
        assert!(resolver.is_external("sys"));
        assert!(resolver.is_external("collections.abc"));
        assert!(resolver.is_external("json"));
    }

    #[test]
    fn test_python_resolver_is_external_third_party() {
        let resolver = PythonResolver;
        assert!(resolver.is_external("fastapi"));
        assert!(resolver.is_external("numpy"));
        assert!(resolver.is_external("django.db.models"));
    }

    #[test]
    fn test_python_resolver_not_external_project_module() {
        let resolver = PythonResolver;
        assert!(!resolver.is_external("myproject.utils"));
        assert!(!resolver.is_external("src.main"));
        assert!(!resolver.is_external("app.core"));
    }

    #[test]
    fn test_python_resolver_language() {
        let resolver = PythonResolver;
        assert_eq!(resolver.language(), "python");
    }

    #[test]
    fn test_python_resolver_file_to_module_name() {
        let resolver = PythonResolver;
        assert_eq!(
            resolver.file_to_module_name("src/pkg/module.py", Path::new(".")),
            Some("pkg.module".to_string())
        );
    }

    #[test]
    fn test_language_registry_get_python() {
        let r = LanguageRegistry::get("python");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "python");
    }

    #[test]
    fn test_language_registry_get_unsupported() {
        let r = LanguageRegistry::get("typescript");
        assert!(r.is_none());
    }

    #[test]
    fn test_supported_languages() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"python"));
    }
}
