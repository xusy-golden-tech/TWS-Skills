//! json language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct JsonExtractor;

impl Extractor for JsonExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["json"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["json"]
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
