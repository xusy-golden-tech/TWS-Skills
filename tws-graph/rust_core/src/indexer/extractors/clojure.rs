//! clojure language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct ClojureExtractor;

impl Extractor for ClojureExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["clj", "cljs", "cljc", "edn"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["clojure"]
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
