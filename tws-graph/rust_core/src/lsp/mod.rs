//! LSP (Language Server Protocol) integration.
//!
//! Provides client wrappers for external language servers to obtain
//! semantic tokens, references, and hover information.

pub mod adapters;
pub mod client;
pub mod manager;
pub mod protocol;
