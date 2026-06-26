//! rust language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct RustExtractor;

impl Extractor for RustExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["rs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["rust"]
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
