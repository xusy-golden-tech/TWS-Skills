//! Parallel file extraction using rayon.
//!
//! Each worker thread processes a chunk of files independently:
//! - Creates its own Registry (extractors are stateless unit structs)
//! - Creates its own ParserPool (tree_sitter::Parser is not Send)
//! - Only does extraction (tree-sitter parsing + AST walking)
//! - Returns `FileResult`s to the main thread for DB insertion
//!
//! SQLite writes are intentionally single-threaded in the main thread,
//! which is the key design constraint: extract in parallel, write sequentially.

use std::path::{Path, PathBuf};

use crate::db::models::{EdgeRecord, NodeRecord};
use crate::indexer::context::ExtractionContext;
use crate::indexer::parser_pool::ParserPool;
use crate::indexer::registry::Registry;
use crate::traits::Extractor;

// ---------------------------------------------------------------------------
// FileResult
// ---------------------------------------------------------------------------

/// Accumulated extraction output for a single file.
///
/// Contains only Send-safe data (Strings, i64, i32) so it can be safely
/// transferred across rayon thread boundaries.
#[derive(Debug, Default)]
pub struct FileResult {
    /// Symbol nodes discovered in the file.
    pub nodes: Vec<NodeRecord>,

    /// Relationship edges discovered in the file.
    pub edges: Vec<EdgeRecord>,
}

// ---------------------------------------------------------------------------
// process_chunk
// ---------------------------------------------------------------------------

/// Process a chunk of files in the current (worker) thread.
///
/// Each thread builds its own `Registry` (via `registry_setup`) and `ParserPool`.
/// The `registry_setup` closure registers all available extractors — typically
/// the same `register_all_extractors` function used in the serial path.
///
/// # Safety
///
/// `tree_sitter::Parser` is `!Send`, so each thread must have its own pool.
/// The `registry_setup` closure is `Send + Sync` and only touches the
/// thread-local `Registry`.
pub fn process_chunk(
    root_path: &Path,
    file_paths: &[PathBuf],
    registry_setup: &(dyn Fn(&mut Registry) + Send + Sync),
) -> Vec<FileResult> {
    let mut registry = Registry::new();
    registry_setup(&mut registry);
    let mut parser_pool = ParserPool::new();
    let mut results = Vec::with_capacity(file_paths.len());

    for file_path in file_paths {
        let result = process_one_file(root_path, file_path, &registry, &mut parser_pool);
        results.push(result);
    }

    results
}

// ---------------------------------------------------------------------------
// process_one_file
// ---------------------------------------------------------------------------

/// Process a single file: detect language, find extractor, parse, extract.
///
/// Returns `FileResult::default()` (empty vectors) for files that cannot be
/// processed — unsupported extension, parse error, I/O error, etc.
/// This matches the behaviour of `index_one_file()` in the serial path,
/// which returns `None` for unprocessable files.
fn process_one_file(
    root_path: &Path,
    file_path: &Path,
    registry: &Registry,
    parser_pool: &mut ParserPool,
) -> FileResult {
    let file_path_str = file_path.to_string_lossy();

    // 1. Detect language from file extension
    let lang = match crate::indexer::language::detect(&file_path_str) {
        Some(l) => l,
        None => return FileResult::default(),
    };

    // 2. Find the appropriate extractor
    let ext_str = match file_path.extension().and_then(|e| e.to_str()) {
        Some(e) => e,
        None => return FileResult::default(),
    };

    let extractor = match registry.find_by_extension(ext_str) {
        Some(e) => e,
        None => return FileResult::default(),
    };

    // 3. Acquire a parser from the thread-local pool
    let parser = match parser_pool.get_or_create(lang) {
        Some(p) => p,
        None => return FileResult::default(),
    };

    // 4. Read source bytes
    let abs_path = root_path.join(file_path);
    let source_bytes = match std::fs::read(&abs_path) {
        Ok(b) => b,
        Err(_) => return FileResult::default(),
    };

    // 5. Compute project-relative path (normalise Windows backslashes)
    let rel_path = file_path_str.replace('\\', "/");

    // 6. Create extraction context
    let mut ctx = ExtractionContext::new(rel_path, lang.to_string());

    // 7. Parse with tree-sitter
    let tree = match parser.parse(&source_bytes, None) {
        Some(t) => t,
        None => return FileResult::default(),
    };

    // 8. Extract symbols and edges
    if extractor.extract(&source_bytes, &tree, &mut ctx).is_err() {
        return FileResult::default();
    }

    // 9. Take accumulated results (no DB writes in worker threads)
    let result = std::mem::take(&mut ctx.result);

    FileResult {
        nodes: result.nodes,
        edges: result.edges,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// A minimal test extractor that handles Python files.
    struct TestExtractor;

    impl Extractor for TestExtractor {
        fn extensions(&self) -> Vec<&'static str> {
            vec!["py"]
        }

        fn languages(&self) -> Vec<&'static str> {
            vec!["python"]
        }

        fn extract(
            &self,
            _source: &[u8],
            _tree: &tree_sitter::Tree,
            _ctx: &mut ExtractionContext,
        ) -> anyhow::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn test_file_result_default_is_empty() {
        let r = FileResult::default();
        assert!(r.nodes.is_empty());
        assert!(r.edges.is_empty());
    }

    #[test]
    fn test_process_one_file_unknown_extension_returns_empty() {
        let root = Path::new(".");
        let file = PathBuf::from("test.xyzzy");
        let registry = Registry::new();
        let mut pool = ParserPool::new();

        let result = process_one_file(root, &file, &registry, &mut pool);
        assert!(result.nodes.is_empty());
        assert!(result.edges.is_empty());
    }

    #[test]
    fn test_process_one_file_missing_file_returns_empty() {
        let root = Path::new(".");
        let file = PathBuf::from("nonexistent_file_42.py");
        let mut registry = Registry::new();
        registry.register(Box::new(TestExtractor));
        let mut pool = ParserPool::new();

        let result = process_one_file(root, &file, &registry, &mut pool);
        assert!(result.nodes.is_empty());
        assert!(result.edges.is_empty());
    }

    #[test]
    fn test_process_chunk_empty_list() {
        let root = Path::new(".");
        let files: Vec<PathBuf> = vec![];

        let results = process_chunk(root, &files, &|_| {});
        assert!(results.is_empty());
    }

    #[test]
    fn test_process_chunk_with_unknown_files() {
        let root = Path::new(".");
        let files: Vec<PathBuf> = vec![
            PathBuf::from("a.xyz"),
            PathBuf::from("b.xyz"),
        ];

        let results = process_chunk(root, &files, &|_| {});
        assert_eq!(results.len(), 2);
        for r in &results {
            assert!(r.nodes.is_empty());
            assert!(r.edges.is_empty());
        }
    }

    #[test]
    fn test_process_one_file_no_extension_returns_empty() {
        let root = Path::new(".");
        let file = PathBuf::from("Dockerfile"); // no extension
        let registry = Registry::new();
        let mut pool = ParserPool::new();

        let result = process_one_file(root, &file, &registry, &mut pool);
        assert!(result.nodes.is_empty());
        assert!(result.edges.is_empty());
    }

    #[test]
    fn test_process_chunk_registry_setup_is_called() {
        use std::sync::atomic::{AtomicBool, Ordering};
        static CALLED: AtomicBool = AtomicBool::new(false);

        let root = Path::new(".");
        let files: Vec<PathBuf> = vec![];

        let _ = process_chunk(root, &files, &|_reg| {
            CALLED.store(true, Ordering::SeqCst);
        });

        assert!(CALLED.load(Ordering::SeqCst));
    }
}
