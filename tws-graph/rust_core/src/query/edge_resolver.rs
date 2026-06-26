//! Cross-file edge resolution.
//!
//! Resolves `[internal]` unresolved references by matching symbol names
//! across files.

/// Attempt to resolve an internal reference to a symbol in another file.
///
/// Returns `None` if the reference cannot be resolved.
pub fn resolve_internal(
    _symbol_name: &str,
    _source_file: &str,
) -> Option<String> {
    // TODO: query DB for matching symbols in other project files
    None
}

/// Classify a reference as `[external]` (third-party) or `[internal]` (project).
pub fn classify_reference(_symbol_name: &str, _source_file: &str) -> &'static str {
    // TODO: check if symbol resolves to project file or external library
    "internal"
}
