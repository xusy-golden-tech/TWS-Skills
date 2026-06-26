//! nix language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct NixExtractor;

impl Extractor for NixExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["nix"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["nix"]
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
