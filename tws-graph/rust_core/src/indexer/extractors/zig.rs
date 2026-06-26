//! zig language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct ZigExtractor;

impl Extractor for ZigExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["zig"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["zig"]
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
