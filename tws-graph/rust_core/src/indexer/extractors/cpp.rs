//! cpp language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct CppExtractor;

impl Extractor for CppExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cpp", "cc", "cxx", "hpp", "hh", "hxx"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["cpp"]
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
