//! hcl language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct HclExtractor;

impl Extractor for HclExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["hcl", "tf", "tfvars"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["hcl"]
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
