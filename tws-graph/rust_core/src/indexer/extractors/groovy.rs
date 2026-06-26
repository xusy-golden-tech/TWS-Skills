//! groovy language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct GroovyExtractor;

impl Extractor for GroovyExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["groovy"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["groovy"]
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
