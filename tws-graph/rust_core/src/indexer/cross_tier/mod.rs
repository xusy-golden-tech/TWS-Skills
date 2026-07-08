//! Cross-tier tracing — scan source files for HTTP calls and route definitions,
//! normalize URLs, and match front-end callers to back-end handlers.
//!
//! # Modules
//!
//! | Module | Phase | Purpose |
//! |--------|-------|---------|
//! | [`patterns`]   | B1a | Static tree-sitter Query pattern definitions per framework |
//! | [`normalizer`] | B1b | Unify heterogeneous URL parameter syntax into `{param}` form |
//! | [`matcher`]    | B1c | Three-level URL matching pipeline (exact → template → fuzzy) |

pub mod patterns;
pub mod normalizer;
pub mod matcher;
