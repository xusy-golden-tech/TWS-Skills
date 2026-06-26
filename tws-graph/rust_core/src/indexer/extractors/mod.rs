//! Language extractors — one module per supported language / config format.
//!
//! Each extractor implements the `Extractor` trait and uses a tree-sitter
//! parser to produce nodes and edges.

pub mod bash;
pub mod c;
pub mod clojure;
pub mod cmake;
pub mod cpp;
pub mod csharp;
pub mod css;
pub mod dart;
pub mod dockerfile;
pub mod elixir;
pub mod go;
pub mod groovy;
pub mod haskell;
pub mod hcl;
pub mod html;
pub mod java;
pub mod json;
pub mod kotlin;
pub mod kustomize;
pub mod kubernetes;
pub mod lua;
pub mod markdown;
pub mod nix;
pub mod php;
pub mod proto;
pub mod python;
pub mod ruby;
pub mod rust;
pub mod scala;
pub mod sql;
pub mod swift;
pub mod toml_extractor;
pub mod typescript;
pub mod yaml;
pub mod zig;
