//! kustomize language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct KustomizeExtractor;

impl Extractor for KustomizeExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["yaml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["kustomize"]
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
