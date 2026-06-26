//! sql language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct SqlExtractor;

impl Extractor for SqlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["sql"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["sql"]
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
