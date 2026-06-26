//! elixir language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct ElixirExtractor;

impl Extractor for ElixirExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["ex", "exs"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["elixir"]
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
