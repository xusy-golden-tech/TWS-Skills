//! Parser pool — reuses tree-sitter Parsers by language to avoid repeated
//! creation and `set_language()` calls during indexing.
//!
//! tree-sitter `Parser` is not `Send`, so this pool is designed for single-thread
//! use. Phase 3 parallel indexing will add per-thread pools.

use std::collections::HashMap;

use crate::lang_to_tree_sitter;

pub struct ParserPool {
    parsers: HashMap<String, tree_sitter::Parser>,
}

impl ParserPool {
    pub fn new() -> Self {
        ParserPool {
            parsers: HashMap::new(),
        }
    }

    /// Get or create a parser for the given language.
    /// Returns `None` if the language is unknown or if setting the language fails.
    pub fn get_or_create(&mut self, lang: &str) -> Option<&mut tree_sitter::Parser> {
        if !self.parsers.contains_key(lang) {
            let ts_lang = lang_to_tree_sitter(lang)?;
            let mut parser = tree_sitter::Parser::new();
            parser.set_language(&ts_lang).ok()?;
            self.parsers.insert(lang.to_string(), parser);
        }
        self.parsers.get_mut(lang)
    }
}
