//! Extractor registry — maps file extensions & languages to extractor instances.

use crate::traits::Extractor;

/// Holds all registered extractors and dispatches by file path or language.
pub struct Registry {
    // Registry will hold boxed trait objects; placeholder for now.
}

impl Registry {
    /// Create a new empty registry.
    pub fn new() -> Self {
        Self {}
    }

    /// Find an extractor for the given file path.
    ///
    /// Returns `None` if no extractor matches.
    pub fn find_for_file(&self, _path: &str) -> Option<&dyn Extractor> {
        // TODO: iterate registered extractors, match by extension
        None
    }

    /// Return the number of registered extractors.
    pub fn len(&self) -> usize {
        0 // TODO: return actual count
    }

    /// Returns true if no extractors are registered.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

impl Default for Registry {
    fn default() -> Self {
        Self::new()
    }
}
