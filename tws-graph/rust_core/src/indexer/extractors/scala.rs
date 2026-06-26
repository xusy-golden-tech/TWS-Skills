//! scala language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct ScalaExtractor;

impl Extractor for ScalaExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["scala", "sc"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["scala"]
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
