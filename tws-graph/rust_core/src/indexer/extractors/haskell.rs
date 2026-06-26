//! haskell language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct HaskellExtractor;

impl Extractor for HaskellExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["hs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["haskell"]
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
