//! PyO3 FFI import/export extraction.
//!
//! This module provides extractors for PyO3 FFI patterns:
//! - `extract_pyo3_exports()`: detect `#[pyfunction]`, `#[pymethods]` in Rust
//! - `extract_pyo3_imports()`: detect `from ._core import X` in Python
//!
//! # Architecture
//!
//! ```text
//! extract_pyo3_exports(source, file_path)
//!   → tree-sitter-rust parse → walk AST
//!     → find function_item with #[pyfunction] attribute
//!     → find impl_item with #[pymethods] attribute → extract methods
//!   → Vec<FfiExportRecord>
//!
//! extract_pyo3_imports(source, file_path)
//!   → tree-sitter-python parse → walk AST
//!     → find import_from_statement with native module name
//!   → Vec<FfiImportRecord>
//! ```

use crate::db::models::{FfiExportRecord, FfiImportRecord};
use tree_sitter::Node;

// ============================================================================
// PyO3 Export Extraction
// ============================================================================

/// Extract PyO3 exports from a Rust source file.
///
/// Detects:
/// - `#[pyfunction] fn name()` — exports a function by its original name
/// - `#[pyfunction(name = "X")] fn internal()` — exports with a custom name override
/// - `#[pymethods] impl MyClass { fn method1() {} fn method2() {} }` — exports
///   all methods in the impl block (excluding `#[new]`, `#[getter]`, `#[setter]`)
pub fn extract_pyo3_exports(
    parser: &mut tree_sitter::Parser,
    source: &str,
    file_path: &str,
) -> Result<Vec<FfiExportRecord>, String> {
    let ts_lang: tree_sitter::Language = tree_sitter_rust::LANGUAGE.into();
    parser
        .set_language(&ts_lang)
        .map_err(|e| format!("pyo3 exports: set_language(rust) failed: {}", e))?;

    let tree = parser
        .parse(source, None)
        .ok_or_else(|| format!("pyo3 exports: parse failed: {}", file_path))?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();
    let mut exports = Vec::new();

    collect_rust_exports(&root, source_bytes, file_path, &mut exports);

    Ok(exports)
}

/// Recursively walk the Rust AST collecting PyO3 exports.
///
/// `attribute_item` is a **sibling** of the item it decorates in tree-sitter-rust,
/// not a child. So we look for `attribute_item` → next-sibling-is-`function_item`
/// or `attribute_item` → next-sibling-is-`impl_item` pairs at each level.
fn collect_rust_exports(
    node: &Node,
    source: &[u8],
    file_path: &str,
    exports: &mut Vec<FfiExportRecord>,
) {
    let count = node.named_child_count();
    for i in 0..count {
        if let Some(child) = node.named_child(i) {
            if child.kind() == "attribute_item" {
                let attr_name = get_attribute_name(child, source);
                // Check if the next named sibling is the item this attribute decorates
                if let Some(next) = next_named_sibling_skip_attrs(node, i) {
                    match (attr_name.as_deref(), next.kind()) {
                        (Some("pyfunction"), "function_item") => {
                            let func_name = extract_rust_func_name(next, source);
                            let (symbol_name, symbol_name_raw) =
                                resolve_pyo3_name_override(child, &func_name, source);
                            let start = next.start_position();
                            exports.push(FfiExportRecord {
                                id: None,
                                symbol_name,
                                symbol_name_raw,
                                func_node_id: 0,
                                ffi_framework: "pyo3".to_string(),
                                source_lang: "rust".to_string(),
                                file_path: file_path.to_string(),
                                line: (start.row + 1) as i64,
                                column: (start.column + 1) as i64,
                                raw_snippet: Some(snippet(source, next)),
                            });
                        }
                        (Some("pymethods"), "impl_item") => {
                            if let Some(body) = next.child_by_field_name("body") {
                                extract_impl_methods(body, source, file_path, exports);
                            }
                            // Recurse into impl_item for nested items
                            collect_rust_exports(&next, source, file_path, exports);
                        }
                        _ => {}
                    }
                }
                // Don't recurse into attribute_item itself (it only contains
                // attribute tokens, not nested items).
            } else {
                // Recurse into all other nodes to find nested items
                // (e.g. inside modules, mod blocks, etc.)
                collect_rust_exports(&child, source, file_path, exports);
            }
        }
    }
}

/// Get the next named sibling at the same level, skipping over other
/// attribute_items. This handles cases like:
/// ```
/// #[pyfunction]
/// #[pyo3(signature = (...))]
/// fn my_func() { ... }
/// ```
/// where a second `attribute_item` sits between `#[pyfunction]` and the
/// function/item that it decorates.
fn next_named_sibling_skip_attrs<'a>(parent: &Node<'a>, idx: usize) -> Option<Node<'a>> {
    for j in (idx + 1)..parent.named_child_count() {
        if let Some(sibling) = parent.named_child(j) {
            if sibling.kind() == "attribute_item" {
                continue;
            }
            return Some(sibling);
        }
    }
    None
}

/// Extract the attribute name from an `attribute_item` node.
///
/// Tree structure:
/// ```text
/// attribute_item
///   attribute
///     identifier: pyfunction  (or pymethods)
/// ```
fn get_attribute_name(attr_item: Node, source: &[u8]) -> Option<String> {
    for i in 0..attr_item.named_child_count() {
        if let Some(attr) = attr_item.named_child(i) {
            if attr.kind() == "attribute" {
                for j in 0..attr.named_child_count() {
                    if let Some(id) = attr.named_child(j) {
                        if id.kind() == "identifier" {
                            return Some(node_text(source, id));
                        }
                    }
                }
            }
        }
    }
    None
}

/// Check if a node is an identifier-like node.
fn is_identifier(node: Node) -> bool {
    node.kind() == "identifier" || node.kind() == "simple_identifier"
}

/// Extract the function name from a Rust `function_item` node.
fn extract_rust_func_name(func_node: Node, source: &[u8]) -> String {
    if let Some(name_node) = func_node.child_by_field_name("name") {
        return node_text(source, name_node);
    }
    String::new()
}

/// Resolve the export symbol name, handling `name="X"` overrides.
///
/// Returns `(symbol_name, symbol_name_raw)`:
/// - If a `name=` override is found, `symbol_name` is the override value
///   and `symbol_name_raw` is `Some(original_func_name)`.
/// - Otherwise, both use the original function name.
fn resolve_pyo3_name_override(
    attr_item: Node,
    func_name: &str,
    source: &[u8],
) -> (String, Option<String>) {
    let override_name = extract_name_override_from_attribute(attr_item, source);
    match override_name {
        Some(override_val) => (override_val, Some(func_name.to_string())),
        None => (func_name.to_string(), None),
    }
}

/// Extract the `name="X"` value from a `#[pyfunction(name = "X")]` attribute node.
fn extract_name_override_from_attribute(attr_item: Node, source: &[u8]) -> Option<String> {
    // Walk into: attribute_item → attribute → token_tree → (name = "value")
    let mut cursor = attr_item.walk();
    for child in attr_item.children(&mut cursor) {
        if child.kind() == "attribute" {
            if let Some(val) = find_name_override_in_attribute(child, source) {
                return Some(val);
            }
        }
    }
    None
}

/// Search within an `attribute` node for the `name = "X"` override.
fn find_name_override_in_attribute(attr: Node, source: &[u8]) -> Option<String> {
    let mut cursor = attr.walk();
    for child in attr.children(&mut cursor) {
        if child.kind() == "token_tree" {
            if let Some(val) = find_name_pair_in_token_tree(child, source) {
                return Some(val);
            }
        }
    }
    None
}

/// Walk a token_tree to find `name = "value"` and extract the string value.
///
/// For `#[pyfunction(name = "custom_name")]`, the token_tree contains:
///   ( identifier:name  =  string_literal:"custom_name" )
fn find_name_pair_in_token_tree(node: Node, source: &[u8]) -> Option<String> {
    let mut cursor = node.walk();
    let children: Vec<Node> = node.children(&mut cursor).collect();

    for (i, child) in children.iter().enumerate() {
        if is_identifier(*child) && node_text(source, *child) == "name" {
            // Look ahead for '=' followed by string_literal
            if let Some(val) = find_string_after_equals(&children, i, source) {
                return Some(val);
            }
        }
        // Recurse into nested token_tree
        if child.kind() == "token_tree" {
            if let Some(val) = find_name_pair_in_token_tree(*child, source) {
                return Some(val);
            }
        }
    }
    None
}

/// Given `children` and the index of the `name` identifier, find the string_literal after `=`.
fn find_string_after_equals(
    children: &[Node],
    name_idx: usize,
    source: &[u8],
) -> Option<String> {
    let mut found_equals = false;
    for child in children.iter().skip(name_idx + 1) {
        let text = node_text(source, *child);
        if text == "=" {
            found_equals = true;
        } else if found_equals && child.kind() == "string_literal" {
            let raw = node_text(source, *child);
            // Strip surrounding quotes
            let inner = raw.trim_matches('"').trim_matches('\'');
            if !inner.is_empty() {
                return Some(inner.to_string());
            }
        }
    }
    None
}

/// Extract all method names from an `impl_item` body (declaration_list),
/// excluding methods with `#[new]`, `#[getter]`, or `#[setter]` attributes.
///
/// Inside a `declaration_list`, attributes are siblings of the function_item
/// they decorate, just like at the module level.
fn extract_impl_methods(
    body: Node,
    source: &[u8],
    file_path: &str,
    exports: &mut Vec<FfiExportRecord>,
) {
    let count = body.named_child_count();
    for i in 0..count {
        if let Some(child) = body.named_child(i) {
            if child.kind() == "function_item" {
                // Check if the previous sibling is a special attribute
                let has_special = if i > 0 {
                    body.named_child(i - 1)
                        .map(|prev| {
                            prev.kind() == "attribute_item"
                                && is_special_pyo3_attr(prev, source)
                        })
                        .unwrap_or(false)
                } else {
                    false
                };

                if !has_special {
                    let method_name = extract_rust_func_name(child, source);
                    if !method_name.is_empty() {
                        let start = child.start_position();
                        exports.push(FfiExportRecord {
                            id: None,
                            symbol_name: method_name,
                            symbol_name_raw: None,
                            func_node_id: 0,
                            ffi_framework: "pyo3".to_string(),
                            source_lang: "rust".to_string(),
                            file_path: file_path.to_string(),
                            line: (start.row + 1) as i64,
                            column: (start.column + 1) as i64,
                            raw_snippet: Some(snippet(source, child)),
                        });
                    }
                }
            }
            // Recurse into other container nodes (but not attributes or functions)
            if child.kind() != "function_item" && child.kind() != "attribute_item" {
                extract_impl_methods(child, source, file_path, exports);
            }
        }
    }
}

/// Check if an `attribute_item` node has `#[new]`, `#[getter]`, or `#[setter]`.
fn is_special_pyo3_attr(attr_item: Node, source: &[u8]) -> bool {
    let name = get_attribute_name(attr_item, source);
    matches!(name.as_deref(), Some("new" | "getter" | "setter"))
}

// ============================================================================
// PyO3 Import Extraction
// ============================================================================

/// Native module name patterns to detect PyO3/CFFI imports.
const NATIVE_MODULE_MARKERS: &[&str] = &["_core", ".so", ".pyd", "_native"];

/// Extract PyO3 imports from a Python source file.
///
/// Detects `from X._core import Y` and similar native-module import patterns.
/// Does NOT handle `import X._core as alias` in this phase.
pub fn extract_pyo3_imports(
    parser: &mut tree_sitter::Parser,
    source: &str,
    file_path: &str,
) -> Result<Vec<FfiImportRecord>, String> {
    let ts_lang: tree_sitter::Language = tree_sitter_python::LANGUAGE.into();
    parser
        .set_language(&ts_lang)
        .map_err(|e| format!("pyo3 imports: set_language(python) failed: {}", e))?;

    let tree = parser
        .parse(source, None)
        .ok_or_else(|| format!("pyo3 imports: parse failed: {}", file_path))?;

    let root = tree.root_node();
    let source_bytes = source.as_bytes();
    let mut imports = Vec::new();

    collect_python_imports(&root, source_bytes, file_path, &mut imports);

    Ok(imports)
}

/// Recursively walk the Python AST looking for `import_from_statement` nodes.
fn collect_python_imports(
    node: &Node,
    source: &[u8],
    file_path: &str,
    imports: &mut Vec<FfiImportRecord>,
) {
    if node.kind() == "import_from_statement" {
        let module_text = get_import_module_name(*node, source);
        if is_native_module(&module_text) {
            let symbols = extract_imported_symbols(*node, source);
            let start = node.start_position();
            let stmt_text = node_text(source, *node);
            for symbol_name in symbols {
                imports.push(FfiImportRecord {
                    id: None,
                    symbol_name,
                    call_node_id: 0,
                    import_stmt: Some(stmt_text.clone()),
                    ffi_framework: "pyo3".to_string(),
                    source_lang: "python".to_string(),
                    file_path: file_path.to_string(),
                    line: (start.row + 1) as i64,
                    column: (start.column + 1) as i64,
                    raw_snippet: Some(snippet(source, *node)),
                });
            }
        }
    }

    // Recurse into children
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            collect_python_imports(&child, source, file_path, imports);
        }
    }
}

/// Get the module name text from an `import_from_statement` node.
///
/// tree-sitter-python AST structure:
/// - `from tws_graph._core import X`:
///     import_from_statement → dotted_name (module), dotted_name (symbol), ...
/// - `from ._core import X`:
///     import_from_statement → relative_import (module), dotted_name (symbol), ...
fn get_import_module_name(node: Node, source: &[u8]) -> String {
    // Try the module_name field first (works in some tree-sitter-python versions)
    if let Some(module) = node.child_by_field_name("module_name") {
        let text = node_text(source, module);
        if !text.is_empty() {
            return text;
        }
    }
    // The first named child is either a dotted_name or relative_import → the module
    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "dotted_name" | "relative_import" => {
                    return node_text(source, child);
                }
                _ => {}
            }
        }
    }
    String::new()
}

/// Check if a module name matches native extension patterns.
fn is_native_module(module_name: &str) -> bool {
    NATIVE_MODULE_MARKERS
        .iter()
        .any(|marker| module_name.contains(marker))
}

/// Extract the imported symbol names from an `import_from_statement` node.
///
/// tree-sitter-python AST structure:
/// - `from X import a, b, c`:
///     import_from_statement → dotted_name(module), dotted_name(a), dotted_name(b), dotted_name(c)
/// - `from X import (a, b)`:
///     import_from_statement → dotted_name(module), dotted_name(a), dotted_name(b)
/// - `from .X import a`:
///     import_from_statement → relative_import(module), dotted_name(a)
///
/// The FIRST dotted_name or relative_import is always the module; subsequent
/// named children are the imported symbols.
fn extract_imported_symbols(node: Node, source: &[u8]) -> Vec<String> {
    let mut symbols = Vec::new();
    let mut seen_module = false;

    for i in 0..node.named_child_count() {
        if let Some(child) = node.named_child(i) {
            match child.kind() {
                "dotted_name" => {
                    if !seen_module {
                        // First dotted_name is the module name
                        seen_module = true;
                        continue;
                    }
                    let text = node_text(source, child);
                    // For "a.b" dotted names, take the base name
                    let base = text.split('.').next().unwrap_or(&text);
                    symbols.push(base.to_string());
                }
                "relative_import" => {
                    // relative_import is the module name
                    seen_module = true;
                }
                "aliased_import" => {
                    let text = node_text(source, child);
                    // `import a as b` → take the alias "b"
                    if let Some(alias) = text.split(" as ").nth(1) {
                        symbols.push(alias.trim().to_string());
                    } else if let Some(name) = text.split(" as ").next() {
                        symbols.push(name.trim().to_string());
                    }
                }
                _ => {}
            }
        }
    }

    symbols
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

    /// Helper: parse with fresh Rust parser.
    fn parse_rust(source: &str) -> (tree_sitter::Parser, String) {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_rust::LANGUAGE.into())
            .unwrap();
        (parser, source.to_string())
    }

    /// Helper: parse with fresh Python parser.
    fn parse_python(source: &str) -> (tree_sitter::Parser, String) {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_python::LANGUAGE.into())
            .unwrap();
        (parser, source.to_string())
    }

    // -----------------------------------------------------------------------
    // Export tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_pyo3_exports_simple() {
        let src = r#"
#[pyfunction]
fn simple_export() {}
"#;
        let (mut parser, source) = parse_rust(src);
        let result =
            extract_pyo3_exports(&mut parser, &source, "test.rs").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "simple_export");
        assert_eq!(result[0].symbol_name_raw, None);
        assert_eq!(result[0].ffi_framework, "pyo3");
        assert_eq!(result[0].source_lang, "rust");
    }

    #[test]
    fn test_extract_pyo3_exports_custom_name() {
        let src = r#"
#[pyfunction(name = "custom_name")]
fn internal_name() {}
"#;
        let (mut parser, source) = parse_rust(src);
        let result =
            extract_pyo3_exports(&mut parser, &source, "test.rs").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "custom_name");
        assert_eq!(
            result[0].symbol_name_raw,
            Some("internal_name".to_string())
        );
    }

    #[test]
    fn test_extract_pyo3_exports_pymethods() {
        let src = r#"
#[pymethods]
impl MyClass {
    fn method1(&self) {}
    fn method2(&self, x: i32) {}
}
"#;
        let (mut parser, source) = parse_rust(src);
        let result =
            extract_pyo3_exports(&mut parser, &source, "test.rs").unwrap();
        assert_eq!(result.len(), 2);
        let names: Vec<&str> =
            result.iter().map(|r| r.symbol_name.as_str()).collect();
        assert!(names.contains(&"method1"));
        assert!(names.contains(&"method2"));
    }

    #[test]
    fn test_extract_pyo3_exports_pymethods_exclude_special() {
        let src = r#"
#[pymethods]
impl MyClass {
    #[new]
    fn new() -> Self { Self {} }
    #[getter]
    fn get_x(&self) -> i32 { 0 }
    fn normal_method(&self) {}
}
"#;
        let (mut parser, source) = parse_rust(src);
        let result =
            extract_pyo3_exports(&mut parser, &source, "test.rs").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "normal_method");
    }

    #[test]
    fn test_extract_pyo3_exports_no_pyo3() {
        let src = r#"
fn regular_function() {}
struct Foo {}
"#;
        let (mut parser, source) = parse_rust(src);
        let result =
            extract_pyo3_exports(&mut parser, &source, "test.rs").unwrap();
        assert_eq!(result.len(), 0);
    }

    // -----------------------------------------------------------------------
    // Import tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_pyo3_imports_from_import() {
        let src = "from tws_graph._core import rust_index, rust_search\n";
        let (mut parser, source) = parse_python(src);
        let result =
            extract_pyo3_imports(&mut parser, &source, "test.py").unwrap();
        assert_eq!(result.len(), 2);
        let names: Vec<&str> =
            result.iter().map(|r| r.symbol_name.as_str()).collect();
        assert!(names.contains(&"rust_index"));
        assert!(names.contains(&"rust_search"));
        for record in &result {
            assert_eq!(record.ffi_framework, "pyo3");
            assert_eq!(record.source_lang, "python");
        }
    }

    #[test]
    fn test_extract_pyo3_imports_relative_import() {
        let src = "from ._core import another_func\n";
        let (mut parser, source) = parse_python(src);
        let result =
            extract_pyo3_imports(&mut parser, &source, "test.py").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "another_func");
    }

    #[test]
    fn test_extract_pyo3_imports_non_native_skip() {
        let src = "from os import path\nfrom typing import List\n";
        let (mut parser, source) = parse_python(src);
        let result =
            extract_pyo3_imports(&mut parser, &source, "test.py").unwrap();
        assert_eq!(result.len(), 0);
    }

    #[test]
    fn test_extract_pyo3_imports_pyd_marker() {
        let src = "from mylib.pyd import fast_func\n";
        let (mut parser, source) = parse_python(src);
        let result =
            extract_pyo3_imports(&mut parser, &source, "test.py").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "fast_func");
    }

    #[test]
    fn test_extract_pyo3_imports_native_marker() {
        let src = "from tws_graph._native import process\n";
        let (mut parser, source) = parse_python(src);
        let result =
            extract_pyo3_imports(&mut parser, &source, "test.py").unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].symbol_name, "process");
    }
}
