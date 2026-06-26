//! csharp language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct CSharpExtractor;

impl Extractor for CSharpExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["cs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["csharp"]
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
