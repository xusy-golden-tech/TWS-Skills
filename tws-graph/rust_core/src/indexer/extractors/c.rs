//! c language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct CExtractor;

impl Extractor for CExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["c", "h"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["c"]
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
