//! dockerfile language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct DockerfileExtractor;

impl Extractor for DockerfileExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["dockerfile"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["dockerfile"]
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
