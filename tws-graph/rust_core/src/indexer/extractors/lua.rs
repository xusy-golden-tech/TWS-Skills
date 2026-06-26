//! lua language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct LuaExtractor;

impl Extractor for LuaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["lua"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["lua"]
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
