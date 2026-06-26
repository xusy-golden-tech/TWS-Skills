//! proto language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct ProtoExtractor;

impl Extractor for ProtoExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["proto"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["proto"]
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
