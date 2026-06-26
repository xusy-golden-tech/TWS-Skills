//! toml language extractor.

use crate::indexer::context::ExtractionContext;
use crate::traits::Extractor;
use tree_sitter::Tree;

pub struct TomlExtractor;

impl Extractor for TomlExtractor {
    fn extensions(&self) -> Vec<&'static str> {
        vec!["toml"]
    }
    fn languages(&self) -> Vec<&'static str> {
        vec!["toml"]
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
