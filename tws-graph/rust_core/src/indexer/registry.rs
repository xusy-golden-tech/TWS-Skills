//! Extractor registry — maps file extensions & languages to extractor instances.
//!
//! The registry maintains two indexes for O(1) lookup:
//! - By file extension (e.g. `"py"` → `PythonExtractor`)
//! - By language name (e.g. `"python"` → `PythonExtractor`)
//!
//! Both maps share ownership of the extractor instances via `Rc`.

use crate::traits::Extractor;
use std::collections::HashMap;
use std::rc::Rc;

/// Holds all registered extractors and dispatches by file extension or language.
pub struct Registry {
    extractors_by_ext: HashMap<String, Rc<Box<dyn Extractor>>>,
    extractors_by_lang: HashMap<String, Rc<Box<dyn Extractor>>>,
}

impl Registry {
    /// Create a new empty registry.
    pub fn new() -> Self {
        Self {
            extractors_by_ext: HashMap::new(),
            extractors_by_lang: HashMap::new(),
        }
    }

    /// Register an extractor, inserting it into both extension and language maps.
    ///
    /// The same extractor instance is shared across all extensions and languages
    /// it claims to handle.
    pub fn register(&mut self, extractor: Box<dyn Extractor>) {
        let rc: Rc<Box<dyn Extractor>> = Rc::new(extractor);

        for ext in rc.extensions() {
            self.extractors_by_ext
                .insert(ext.to_lowercase(), Rc::clone(&rc));
        }
        for lang in rc.languages() {
            self.extractors_by_lang
                .insert(lang.to_lowercase(), Rc::clone(&rc));
        }
    }

    /// Find an extractor by file extension (case-insensitive).
    ///
    /// Returns a reference to the [`Extractor`] trait object, or `None` if no
    /// extractor handles the given extension.
    pub fn find_by_extension(&self, ext: &str) -> Option<&dyn Extractor> {
        self.extractors_by_ext
            .get(&ext.to_lowercase())
            .map(|rc| rc.as_ref().as_ref())
    }

    /// Find an extractor by language name (case-insensitive).
    ///
    /// Returns a reference to the [`Extractor`] trait object, or `None` if no
    /// extractor handles the given language.
    pub fn find_by_language(&self, lang: &str) -> Option<&dyn Extractor> {
        self.extractors_by_lang
            .get(&lang.to_lowercase())
            .map(|rc| rc.as_ref().as_ref())
    }

    /// Return the number of registered extractor instances.
    pub fn len(&self) -> usize {
        // Count unique Rc pointers by address (both maps share the same pointers).
        use std::collections::HashSet;
        let mut seen: HashSet<*const ()> = HashSet::new();
        for rc in self.extractors_by_ext.values() {
            let ptr = Rc::as_ptr(rc) as *const ();
            seen.insert(ptr);
        }
        seen.len()
    }

    /// Returns true if no extractors are registered.
    pub fn is_empty(&self) -> bool {
        self.extractors_by_ext.is_empty()
    }
}

impl Default for Registry {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::indexer::context::ExtractionContext;
    use tree_sitter::Tree;

    /// A minimal test extractor — handles Rust and Python.
    struct TestExtractor {
        extensions: Vec<&'static str>,
        languages: Vec<&'static str>,
    }

    impl Extractor for TestExtractor {
        fn extensions(&self) -> Vec<&'static str> {
            self.extensions.clone()
        }

        fn languages(&self) -> Vec<&'static str> {
            self.languages.clone()
        }

        fn extract(
            &self,
            _source: &[u8],
            _tree: &Tree,
            _ctx: &mut ExtractionContext,
        ) -> anyhow::Result<()> {
            Ok(())
        }
    }

    fn make_test_extractor() -> Box<dyn Extractor> {
        Box::new(TestExtractor {
            extensions: vec!["py", "pyi"],
            languages: vec!["python"],
        })
    }

    #[test]
    fn test_new_registry_is_empty() {
        let r = Registry::new();
        assert!(r.is_empty());
        assert_eq!(r.len(), 0);
    }

    #[test]
    fn test_default_is_empty() {
        let r = Registry::default();
        assert!(r.is_empty());
    }

    #[test]
    fn test_register_and_find_by_extension() {
        let mut r = Registry::new();
        r.register(make_test_extractor());

        assert!(!r.is_empty());
        assert_eq!(r.len(), 1);

        let ext = r.find_by_extension("py");
        assert!(ext.is_some(), "should find extractor for .py");

        let ext = r.find_by_extension("pyi");
        assert!(ext.is_some(), "should find extractor for .pyi");

        let ext = r.find_by_extension("rs");
        assert!(ext.is_none(), "should NOT find extractor for .rs");
    }

    #[test]
    fn test_register_and_find_by_language() {
        let mut r = Registry::new();
        r.register(make_test_extractor());

        let ext = r.find_by_language("python");
        assert!(ext.is_some(), "should find extractor for python");

        let ext = r.find_by_language("rust");
        assert!(ext.is_none(), "should NOT find extractor for rust");
    }

    #[test]
    fn test_find_is_case_insensitive() {
        let mut r = Registry::new();
        r.register(make_test_extractor());

        assert!(r.find_by_extension("PY").is_some());
        assert!(r.find_by_extension("Py").is_some());
        assert!(r.find_by_language("PYTHON").is_some());
        assert!(r.find_by_language("Python").is_some());
    }

    #[test]
    fn test_register_multiple_extractors() {
        let mut r = Registry::new();
        r.register(make_test_extractor());

        // Register a second extractor for Rust
        let rust_ext = Box::new(TestExtractor {
            extensions: vec!["rs"],
            languages: vec!["rust"],
        });
        r.register(rust_ext);

        assert!(!r.is_empty());
        assert_eq!(r.len(), 2);

        assert!(r.find_by_extension("py").is_some());
        assert!(r.find_by_extension("rs").is_some());
        assert!(r.find_by_language("python").is_some());
        assert!(r.find_by_language("rust").is_some());
    }

    #[test]
    fn test_register_extractor_with_multiple_languages() {
        let mut r = Registry::new();
        let ts_ext = Box::new(TestExtractor {
            extensions: vec!["ts", "tsx", "js", "jsx"],
            languages: vec!["typescript", "javascript"],
        });
        r.register(ts_ext);

        assert_eq!(r.len(), 1);

        // All four extensions should be findable
        for ext in &["ts", "tsx", "js", "jsx"] {
            assert!(
                r.find_by_extension(ext).is_some(),
                "should find extractor for .{}",
                ext
            );
        }

        // Both languages should be findable
        for lang in &["typescript", "javascript"] {
            assert!(
                r.find_by_language(lang).is_some(),
                "should find extractor for language {}",
                lang
            );
        }
    }

    #[test]
    fn test_len_counts_unique_extractors() {
        let mut r = Registry::new();
        // Register same extractor twice (should only count once since it
        // registers under "py" and "python" but it's the same instance).
        r.register(make_test_extractor());

        // A single extractor that handles two extensions should count as 1.
        assert_eq!(r.len(), 1);
    }
}
