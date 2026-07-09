//! FFI cross-language tracing — detect and match FFI import/export symbols.
//!
//! This module provides extraction engines for various FFI frameworks (PyO3, CGo,
//! JNA, etc.) and a matching engine that links imports to exports across
//! language boundaries.
//!
//! # Architecture
//!
//! ```text
//! scan_ffi(root, files)
//!   → pyo3::extract_pyo3_exports()  — detect #[pyfunction], #[pymethods] in Rust
//!   → pyo3::extract_pyo3_imports()  — detect `from ._core import X` in Python
//!   → cgo::extract_cgo_exports()    — detect `//export FuncName` in Go
//!   → cgo::extract_cgo_imports()    — detect `import "C"` usage in Go
//!   → matcher::match_pyo3()         — exact symbol-name matching
//!   → matcher::match_cgo()          — exact symbol-name matching
//! ```

pub mod pyo3;
pub mod cgo;
pub mod matcher;
