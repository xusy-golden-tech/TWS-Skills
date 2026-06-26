//! java language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct JavaExtractor;

impl Extractor for JavaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["java"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["java"]
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
