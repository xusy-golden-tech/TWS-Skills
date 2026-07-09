//! CGo FFI import/export extraction.
//!
//! This module provides extractors for CGo FFI patterns:
//! - `extract_cgo_exports()`: detect `//export FuncName` in C source files
//! - `extract_cgo_imports()`: detect `C.funcname()` calls in Go source files
//!
//! # Architecture
//!
//! ```text
//! extract_cgo_exports(source, file_path)
//!   → tree-sitter-c parse → walk AST
//!     → find comment nodes matching `//export <name>`
//!     → verify next function_definition name matches
//!   → Vec<FfiExportRecord>
//!
//! extract_cgo_imports(source, file_path)
//!   → check for `import "C"` (quick reject)
//!   → tree-sitter-go parse → walk AST
//!     → find selector_expression nodes with operand "C"
//!     → deduplicate by symbol name
//!   → Vec<FfiImportRecord>
//! ```

use crate::db::models::{FfiExportRecord, FfiImportRecord};
use std::collections::HashSet;
use tree_sitter::Node;

// ============================================================================
// CGo Export Extraction (C //export comments)
// ============================================================================

/// Extract CGo exports from a C source file.
///
/// Scans for `//export <name>` comments in C files. Each such comment marks
/// the following function definition as a cgo export.
pub fn extract_cgo_exports(
    parser: &mut tree_sitter::Parser,
    source: &str,
    file_path: &str,
) -> Result<Vec<FfiExportRecord>, String> {
    let ts_lang: tree_sitter::Language = tree_sitter_c::LANGUAGE.into();
    parser
        .set_language(&ts_lang)
        .map_err(|e| format!("cgo exports: set_language(c) failed: {}", e))?;

    let tree = parser
        .parse(source, None)
        .ok_or_else(|| format!("cgo exports: parse failed: {}", file_path))?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();
    let mut exports = Vec::new();

    collect_c_exports(&root, source_bytes, file_path, &mut exports);

    Ok(exports)
}

/// Recursively walk the C AST collecting `//export`-annotated functions.
///
/// At each level, iterates through named children. When a `comment` node
/// matches `//export <name>`, looks for the next `function_definition`
/// sibling and records an export.
fn collect_c_exports(
    node: &Node,
    source: &[u8],
    file_path: &str,
    exports: &mut Vec<FfiExportRecord>,
) {
    let count = node.named_child_count();
    for i in 0..count {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "comment" {
                let comment_text = node_text(source, child);
                if let Some(symbol_name) = extract_export_name(&comment_text) {
                    // Try to find the next function_definition sibling
                    if let Some(func_node) = next_function_sibling(node, i) {
                        let func_name = extract_c_func_name(func_node, source);
                        if !func_name.is_empty() && func_name != symbol_name {
                            log::warn!(
                                "cgo export: name mismatch in {} — comment says '{}' but next function is '{}'",
                                file_path,
                                symbol_name,
                                func_name
                            );
                        }
                        let start = func_node.start_position();
                        exports.push(FfiExportRecord {
                            id: None,
                            symbol_name,
                            symbol_name_raw: None,
                            func_node_id: 0,
                            ffi_framework: "cgo".to_string(),
                            source_lang: "c".to_string(),
                            file_path: file_path.to_string(),
                            line: (start.row + 1) as i64,
                            column: (start.column + 1) as i64,
                            raw_snippet: Some(snippet(source, func_node)),
                        });
                    } else {
                        // No function found after the comment — still record the export
                        let start = child.start_position();
                        exports.push(FfiExportRecord {
                            id: None,
                            symbol_name,
                            symbol_name_raw: None,
                            func_node_id: 0,
                            ffi_framework: "cgo".to_string(),
                            source_lang: "c".to_string(),
                            file_path: file_path.to_string(),
                            line: (start.row + 1) as i64,
                            column: (start.column + 1) as i64,
                            raw_snippet: Some(snippet(source, child)),
                        });
                    }
                }
                // Don't recurse into comment nodes
            } else {
                // Recurse into all other nodes to find nested items
                collect_c_exports(&child, source, file_path, exports);
            }
        }
    }
}

/// Extract the export symbol name from a `//export <name>` comment.
///
/// Handles variations:
/// - `//export do_work`
/// - `// export do_work`
/// - `//export  do_work` (extra whitespace)
/// - `//export do_work  // inline comment`
fn extract_export_name(comment: &str) -> Option<String> {
    let after_slashes = comment.trim_start_matches("//");
    let trimmed = after_slashes.trim();
    if let Some(rest) = trimmed.strip_prefix("export") {
        let rest = rest.trim();
        // Take the first identifier-like token (alphanumeric + underscore)
        rest.split(|c: char| !c.is_alphanumeric() && c != '_')
            .next()
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
    } else {
        None
    }
}

/// Find the next named sibling that is a `function_definition`.
fn next_function_sibling<'a>(parent: &Node<'a>, idx: usize) -> Option<Node<'a>> {
    for j in (idx + 1)..parent.named_child_count() {
        if let Some(sibling) = parent.named_child(j) {
            if sibling.kind() == "function_definition" {
                return Some(sibling);
            }
        }
    }
    None
}

/// Extract the function name from a C `function_definition` node.
///
/// Walks the `declarator` field to find the inner identifier node.
fn extract_c_func_name(func_node: Node, source: &[u8]) -> String {
    if let Some(declarator) = func_node.child_by_field_name("declarator") {
        return find_first_identifier(declarator, source);
    }
    String::new()
}

/// Find the first `identifier` node in a subtree.
fn find_first_identifier(node: Node, source: &[u8]) -> String {
    if node.kind() == "identifier" {
        return node_text(source, node);
    }
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            let result = find_first_identifier(child, source);
            if !result.is_empty() {
                return result;
            }
        }
    }
    String::new()
}

// ============================================================================
// CGo Import Extraction (Go C.xxx() calls)
// ============================================================================

/// CGo pseudo-package identifiers that are always available in cgo-enabled
/// Go files but do NOT correspond to user-defined C exports. These are
/// generated by the cgo toolchain and should not be treated as FFI symbols.
///
/// See: https://pkg.go.dev/cmd/cgo
const CGO_PSEUDO_IDENTIFIERS: &[&str] = &[
    "CString", "GoString", "GoStringN", "GoBytes", "CBytes",
];

/// Extract CGo imports from a Go source file.
///
/// First checks whether the file contains `import "C"`. If not, returns
/// empty immediately. Otherwise, scans for `C.<func>()` selector expressions
/// and extracts the called symbol names, deduplicated by name.
pub fn extract_cgo_imports(
    parser: &mut tree_sitter::Parser,
    source: &str,
    file_path: &str,
) -> Result<Vec<FfiImportRecord>, String> {
    let ts_lang: tree_sitter::Language = tree_sitter_go::LANGUAGE.into();
    parser
        .set_language(&ts_lang)
        .map_err(|e| format!("cgo imports: set_language(go) failed: {}", e))?;

    let tree = parser
        .parse(source, None)
        .ok_or_else(|| format!("cgo imports: parse failed: {}", file_path))?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();

    // Quick reject: file must contain `import "C"`
    if !has_c_import(&root, source_bytes) {
        return Ok(Vec::new());
    }

    // Scan for C.xxx() calls
    let mut seen_symbols: HashSet<String> = HashSet::new();
    let mut imports = Vec::new();
    collect_cgo_calls(
        &root,
        source_bytes,
        file_path,
        &mut seen_symbols,
        &mut imports,
    );

    Ok(imports)
}

/// Check whether the Go AST contains `import "C"`.
///
/// Searches for an `import_spec` node whose `path` field is the
/// `interpreted_string_literal` `"C"`.
fn has_c_import(node: &Node, source: &[u8]) -> bool {
    if node.kind() == "import_spec" {
        if let Some(path_node) = node.child_by_field_name("path") {
            let text = node_text(source, path_node);
            if text == "\"C\"" {
                return true;
            }
        }
    }
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            if has_c_import(&child, source) {
                return true;
            }
        }
    }
    false
}

/// Recursively walk the Go AST looking for `C.xxx()` selector expressions.
///
/// When a `selector_expression` node is found with operand `C` (identifier),
/// the field name is extracted as the imported symbol. Duplicates are
/// silently skipped via `seen_symbols`.
fn collect_cgo_calls(
    node: &Node,
    source: &[u8],
    file_path: &str,
    seen_symbols: &mut HashSet<String>,
    imports: &mut Vec<FfiImportRecord>,
) {
    if node.kind() == "selector_expression" {
        // Check if operand is the identifier "C"
        if let Some(operand) = node.child_by_field_name("operand") {
            if operand.kind() == "identifier" && node_text(source, operand) == "C" {
                if let Some(field) = node.child_by_field_name("field") {
                    let symbol_name = node_text(source, field);
                    // Skip cgo pseudo-package identifiers and empty names
                    if !symbol_name.is_empty()
                        && !CGO_PSEUDO_IDENTIFIERS.contains(&symbol_name.as_str())
                        && seen_symbols.insert(symbol_name.clone())
                    {
                        let start = node.start_position();
                        imports.push(FfiImportRecord {
                            id: None,
                            symbol_name,
                            call_node_id: 0,
                            import_stmt: None,
                            ffi_framework: "cgo".to_string(),
                            source_lang: "go".to_string(),
                            file_path: file_path.to_string(),
                            line: (start.row + 1) as i64,
                            column: (start.column + 1) as i64,
                            raw_snippet: Some(snippet(source, *node)),
                        });
                    }
                }
            }
        }
    }

    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            collect_cgo_calls(&child, source, file_path, seen_symbols, imports);
        }
    }
}

// ============================================================================
// Helpers
// ============================================================================

/// Get the source text of a tree-sitter node.
fn node_text(source: &[u8], node: Node) -> String {
    node.utf8_text(source)
        .map(|c| c.to_string())
        .unwrap_or_default()
}

/// Truncate a source snippet for debugging (max 200 chars).
fn snippet(source: &[u8], node: Node) -> String {
    let text = node_text(source, node);
    if text.len() > 200 {
        format!("{}...", &text[..200])
    } else {
        text
    }
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: parse with fresh C parser.
    fn parse_c(source: &str) -> (tree_sitter::Parser, String) {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c::LANGUAGE.into())
            .unwrap();
        (parser, source.to_string())
    }

    /// Helper: parse with fresh Go parser.
    fn parse_go(source: &str) -> (tree_sitter::Parser, String) {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_go::LANGUAGE.into())
            .unwrap();
        (parser, source.to_string())
    }

    // -----------------------------------------------------------------------
    // Export tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_cgo_exports_simple() {
        let src = r#"
//export do_work
void do_work(const char* input) { }
//export init_library
int init_library() { return 0; }
"#;
        let (mut parser, source) = parse_c(src);
        let result =
            extract_cgo_exports(&mut parser, &source, "test.c").unwrap();
        assert_eq!(result.len(), 2);
        let names: Vec<&str> =
            result.iter().map(|r| r.symbol_name.as_str()).collect();
        assert!(names.contains(&"do_work"));
        assert!(names.contains(&"init_library"));
        for record in &result {
            assert_eq!(record.ffi_framework, "cgo");
            assert_eq!(record.source_lang, "c");
        }
    }

    #[test]
    fn test_extract_cgo_exports_no_export() {
        let src = r#"
int main() { return 0; }
void helper() {}
"#;
        let (mut parser, source) = parse_c(src);
        let result =
            extract_cgo_exports(&mut parser, &source, "test.c").unwrap();
        assert_eq!(result.len(), 0);
    }

    #[test]
    fn test_extract_cgo_exports_extra_whitespace() {
        let src = r#"
//export  do_work
void do_work() {}
// export init_library
int init_library() { return 0; }
"#;
        let (mut parser, source) = parse_c(src);
        let result =
            extract_cgo_exports(&mut parser, &source, "test.c").unwrap();
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_extract_cgo_exports_inline_comment() {
        let src = r#"
//export do_work // this is the worker
void do_work() {}
"#;
        let (mut parser, source) = parse_c(src);
        let result =
            extract_cgo_exports(&mut parser, &source, "test.c").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "do_work");
    }

    // -----------------------------------------------------------------------
    // Import tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_cgo_imports_simple() {
        let src = r#"package main
/*
#include "lib.h"
*/
import "C"

func ProcessWork() {
    C.do_work(C.CString("hello"))
    C.init_library()
}
"#;
        let (mut parser, source) = parse_go(src);
        let result =
            extract_cgo_imports(&mut parser, &source, "test.go").unwrap();
        assert_eq!(result.len(), 2);
        let names: Vec<&str> =
            result.iter().map(|r| r.symbol_name.as_str()).collect();
        assert!(names.contains(&"do_work"));
        assert!(names.contains(&"init_library"));
        for record in &result {
            assert_eq!(record.ffi_framework, "cgo");
            assert_eq!(record.source_lang, "go");
        }
    }

    #[test]
    fn test_extract_cgo_imports_no_cgo() {
        let src = r#"package main

import "fmt"

func main() {
    fmt.Println("hello")
}
"#;
        let (mut parser, source) = parse_go(src);
        let result =
            extract_cgo_imports(&mut parser, &source, "test.go").unwrap();
        assert_eq!(result.len(), 0);
    }

    #[test]
    fn test_extract_cgo_imports_dedup() {
        let src = r#"package main
/*
#include "lib.h"
*/
import "C"

func ProcessWork() {
    C.do_work(C.CString("hello"))
    C.do_work(C.CString("world"))
    C.do_work(C.CString("again"))
}
"#;
        let (mut parser, source) = parse_go(src);
        let result =
            extract_cgo_imports(&mut parser, &source, "test.go").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "do_work");
    }

    #[test]
    fn test_extract_cgo_imports_multiple_operations() {
        // Test that selector_expression outside call_expression context also works
        let src = r#"package main
import "C"

func GetFunc() {
    var fn = C.do_work
    _ = fn
}
"#;
        let (mut parser, source) = parse_go(src);
        let result =
            extract_cgo_imports(&mut parser, &source, "test.go").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "do_work");
    }
}
