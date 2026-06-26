//! swift language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct SwiftExtractor;

impl Extractor for SwiftExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["swift"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["swift"]
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
