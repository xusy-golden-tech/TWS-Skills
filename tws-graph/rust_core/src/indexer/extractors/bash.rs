//! bash language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct BashExtractor;

impl Extractor for BashExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["sh", "bash", "zsh"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["bash"]
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
