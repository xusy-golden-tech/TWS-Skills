//! ruby language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct RubyExtractor;

impl Extractor for RubyExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["rb"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["ruby"]
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
