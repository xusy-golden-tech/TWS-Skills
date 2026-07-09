//! FFI import/export matching engine.
//!
//! Links FFI imports to their corresponding exports across language boundaries
//! using exact symbol-name matching. Dispatches to framework-specific matchers
//! based on the `ffi_framework` field.
//!
//! # Architecture
//!
//! ```text
//! match_all(imports, exports, db)
//!   → group imports by ffi_framework
//!   → group exports by ffi_framework
//!     → for each framework:
//!         match_pyo3()  — exact symbol-name matching for PyO3
//!         match_cgo()   — exact symbol-name matching for CGo (Phase B4)
//!   → write FfiCrossEdgeRecord entries to DB
//!   → return FfiMatchResult { pyo3_count, cgo_count, total }
//! ```

use crate::db::connection::Database;
use crate::db::models::{FfiCrossEdgeRecord, FfiExportRecord, FfiImportRecord};

// ============================================================================
// FfiMatchResult
// ============================================================================

/// Result of running all FFI matchers.
pub struct FfiMatchResult {
    /// Number of PyO3 cross-edges created.
    pub pyo3_count: usize,
    /// Number of CGo cross-edges created (0 in Phase B3).
    pub cgo_count: usize,
    /// Total number of cross-edges created across all frameworks.
    pub total: usize,
}

// ============================================================================
// Public API
// ============================================================================

/// Run all FFI matchers grouped by `ffi_framework`.
///
/// Groups imports and exports by their `ffi_framework` field, then dispatches
/// to the appropriate framework-specific matcher. Each matcher creates
/// `FfiCrossEdgeRecord` entries in the database.
///
/// # Returns
/// `FfiMatchResult` with per-framework and total edge counts.
pub fn match_all(
    imports: &[FfiImportRecord],
    exports: &[FfiExportRecord],
    db: &Database,
) -> Result<FfiMatchResult, String> {
    let mut pyo3_count = 0usize;
    let mut cgo_count = 0usize;

    // Group imports by framework
    let pyo3_imports: Vec<&FfiImportRecord> = imports
        .iter()
        .filter(|r| r.ffi_framework == "pyo3")
        .collect();

    if !pyo3_imports.is_empty() {
        let pyo3_exports: Vec<&FfiExportRecord> = exports
            .iter()
            .filter(|r| r.ffi_framework == "pyo3")
            .collect();

        if !pyo3_exports.is_empty() {
            pyo3_count = match_pyo3(&pyo3_imports, &pyo3_exports, db)?;
        }
    }

    // CGo matching — Phase B4, placeholder for now
    let cgo_imports: Vec<&FfiImportRecord> = imports
        .iter()
        .filter(|r| r.ffi_framework == "cgo")
        .collect();

    if !cgo_imports.is_empty() {
        let cgo_exports: Vec<&FfiExportRecord> = exports
            .iter()
            .filter(|r| r.ffi_framework == "cgo")
            .collect();

        if !cgo_exports.is_empty() {
            cgo_count = match_cgo(&cgo_imports, &cgo_exports, db)?;
        }
    }

    Ok(FfiMatchResult {
        pyo3_count,
        cgo_count,
        total: pyo3_count + cgo_count,
    })
}

// ============================================================================
// Framework-specific matchers
// ============================================================================

/// Match PyO3 imports to exports using exact symbol-name matching.
///
/// For each import, searches exports for a matching `symbol_name`.
/// Successful matches create `FfiCrossEdgeRecord` entries in the database.
/// Mismatches are silently skipped (the export may be in a different project).
///
/// When multiple exports share the same symbol name (e.g., same function in
/// different files), all matches are recorded.
///
/// # Returns
/// Number of cross-edges created.
pub fn match_pyo3(
    imports: &[&FfiImportRecord],
    exports: &[&FfiExportRecord],
    db: &Database,
) -> Result<usize, String> {
    let mut count = 0usize;

    for import in imports {
        for export in exports {
            if export.symbol_name == import.symbol_name {
                let record = FfiCrossEdgeRecord {
                    id: None,
                    from_node_id: import.call_node_id,
                    to_node_id: export.func_node_id,
                    ffi_import_id: import.id.unwrap_or(0),
                    ffi_export_id: export.id.unwrap_or(0),
                    edge_kind: "CROSS_FFI".to_string(),
                    symbol_name: import.symbol_name.clone(),
                    ffi_framework: "pyo3".to_string(),
                    created_at: String::new(),
                };
                db.insert_ffi_cross_edge(&record)
                    .map_err(|e| format!("insert_ffi_cross_edge failed: {}", e))?;
                count += 1;
            }
        }
    }

    Ok(count)
}

/// Match CGo imports to exports using exact symbol-name matching.
///
/// Placeholder for Phase B4 — always returns 0.
pub fn match_cgo(
    _imports: &[&FfiImportRecord],
    _exports: &[&FfiExportRecord],
    _db: &Database,
) -> Result<usize, String> {
    // Phase B4 will implement CGo matching logic.
    Ok(0)
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::connection::Database;

    /// Create a temp-file test database.
    fn setup_test_db(name: &str) -> (Database, std::path::PathBuf) {
        let path = std::env::temp_dir().join(format!("tws_test_ffi_matcher_{}.db", name));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
        (Database::initialize(&path).expect("initialize test DB"), path)
    }

    /// Clean up temp test database files.
    fn cleanup(path: &std::path::Path) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
    }

    fn make_import(
        id: i64,
        symbol: &str,
        framework: &str,
    ) -> FfiImportRecord {
        FfiImportRecord {
            id: Some(id),
            symbol_name: symbol.to_string(),
            call_node_id: id,
            import_stmt: None,
            ffi_framework: framework.to_string(),
            source_lang: "python".to_string(),
            file_path: "test.py".to_string(),
            line: 1,
            column: 1,
            raw_snippet: None,
        }
    }

    fn make_export(
        id: i64,
        symbol: &str,
        raw: Option<&str>,
        framework: &str,
    ) -> FfiExportRecord {
        FfiExportRecord {
            id: Some(id),
            symbol_name: symbol.to_string(),
            symbol_name_raw: raw.map(|s| s.to_string()),
            func_node_id: id,
            ffi_framework: framework.to_string(),
            source_lang: "rust".to_string(),
            file_path: "test.rs".to_string(),
            line: 1,
            column: 1,
            raw_snippet: None,
        }
    }

    #[test]
    fn test_match_pyo3_exact() {
        let (db, _path) = setup_test_db("exact_match");
        let imports = vec![make_import(1, "rust_index", "pyo3")];
        let exports = vec![make_export(1, "rust_index", None, "pyo3")];
        let import_refs: Vec<&FfiImportRecord> = imports.iter().collect();
        let export_refs: Vec<&FfiExportRecord> = exports.iter().collect();
        let count = match_pyo3(&import_refs, &export_refs, &db).unwrap();
        assert_eq!(count, 1);

        let edges = db.get_ffi_cross_edges(None, None).unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].symbol_name, "rust_index");
        assert_eq!(edges[0].edge_kind, "CROSS_FFI");
        cleanup(&_path);
    }

    #[test]
    fn test_match_pyo3_no_match() {
        let (db, _path) = setup_test_db("no_match");
        let imports = vec![make_import(1, "unknown_func", "pyo3")];
        let exports = vec![make_export(1, "rust_index", None, "pyo3")];
        let import_refs: Vec<&FfiImportRecord> = imports.iter().collect();
        let export_refs: Vec<&FfiExportRecord> = exports.iter().collect();
        let count = match_pyo3(&import_refs, &export_refs, &db).unwrap();
        assert_eq!(count, 0);

        let edges = db.get_ffi_cross_edges(None, None).unwrap();
        assert_eq!(edges.len(), 0);
        cleanup(&_path);
    }

    #[test]
    fn test_match_pyo3_name_override() {
        let (db, _path) = setup_test_db("name_override");
        let imports = vec![make_import(1, "custom_name", "pyo3")];
        let exports = vec![make_export(1, "custom_name", Some("internal_name"), "pyo3")];
        let import_refs: Vec<&FfiImportRecord> = imports.iter().collect();
        let export_refs: Vec<&FfiExportRecord> = exports.iter().collect();
        let count = match_pyo3(&import_refs, &export_refs, &db).unwrap();
        assert_eq!(count, 1);
        cleanup(&_path);
    }

    #[test]
    fn test_match_pyo3_multiple_exports() {
        let (db, _path) = setup_test_db("multi_export");
        let imports = vec![make_import(1, "shared_name", "pyo3")];
        let exports = vec![
            make_export(1, "shared_name", None, "pyo3"),
            make_export(2, "shared_name", None, "pyo3"),
            make_export(3, "other_func", None, "pyo3"),
        ];
        let import_refs: Vec<&FfiImportRecord> = imports.iter().collect();
        let export_refs: Vec<&FfiExportRecord> = exports.iter().collect();
        let count = match_pyo3(&import_refs, &export_refs, &db).unwrap();
        assert_eq!(count, 2);
        cleanup(&_path);
    }

    #[test]
    fn test_match_all_pyo3() {
        let (db, _path) = setup_test_db("match_all_pyo3");
        let imports = vec![
            make_import(1, "func_a", "pyo3"),
            make_import(2, "func_b", "pyo3"),
        ];
        let exports = vec![
            make_export(1, "func_a", None, "pyo3"),
            make_export(2, "other", None, "pyo3"),
        ];
        let result = match_all(&imports, &exports, &db).unwrap();
        assert_eq!(result.pyo3_count, 1);
        assert_eq!(result.cgo_count, 0);
        assert_eq!(result.total, 1);
        cleanup(&_path);
    }

    #[test]
    fn test_match_all_empty() {
        let (db, _path) = setup_test_db("match_all_empty");
        let imports: Vec<FfiImportRecord> = Vec::new();
        let exports: Vec<FfiExportRecord> = Vec::new();
        let result = match_all(&imports, &exports, &db).unwrap();
        assert_eq!(result.pyo3_count, 0);
        assert_eq!(result.cgo_count, 0);
        assert_eq!(result.total, 0);
        cleanup(&_path);
    }
}
