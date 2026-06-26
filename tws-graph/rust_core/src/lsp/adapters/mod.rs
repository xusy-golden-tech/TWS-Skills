//! LSP adapters — language-specific LSP server configurations.
//!
//! Each adapter knows the canonical LSP server command, install instructions,
//! and per-language initialization options.

pub mod c;
pub mod cpp;
pub mod csharp;
pub mod java;
pub mod php;
pub mod python;
pub mod ruby;
pub mod typescript;
