//! go language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct GoExtractor;

impl Extractor for GoExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["go"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["go"]
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
