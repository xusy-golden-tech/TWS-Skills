//! html language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct HtmlExtractor;

impl Extractor for HtmlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["html", "htm"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["html"]
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
