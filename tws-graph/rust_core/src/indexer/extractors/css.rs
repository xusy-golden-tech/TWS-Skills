//! css language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct CssExtractor;

impl Extractor for CssExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["css"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["css"]
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
