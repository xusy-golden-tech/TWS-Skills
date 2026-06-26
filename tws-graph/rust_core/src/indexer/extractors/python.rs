//! python language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct PythonExtractor;

impl Extractor for PythonExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["py", "pyi"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["python"]
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
