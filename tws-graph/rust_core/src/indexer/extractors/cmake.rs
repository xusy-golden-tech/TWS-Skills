//! cmake language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct CmakeExtractor;

impl Extractor for CmakeExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cmake"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["cmake"]
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
