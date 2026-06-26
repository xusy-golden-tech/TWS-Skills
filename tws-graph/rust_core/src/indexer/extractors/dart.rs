//! dart language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct DartExtractor;

impl Extractor for DartExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["dart"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["dart"]
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
