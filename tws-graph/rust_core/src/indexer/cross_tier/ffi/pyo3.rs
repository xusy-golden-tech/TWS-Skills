// TODO: PyO3 FFI import/export extraction.
// Phase B3 will implement:
//   - extract_pyo3_exports(): detect #[pyfunction], #[pymethods] in Rust
//   - extract_pyo3_imports(): detect `from ._core import X` in Python
