//! yaml language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct YamlExtractor;

impl Extractor for YamlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["yaml", "yml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["yaml"]
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
