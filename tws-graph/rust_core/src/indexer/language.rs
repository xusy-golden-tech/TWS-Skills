//! Language detection — maps file extensions to language names.

/// Return the canonical language name for a file extension (lowercase).
///
/// Returns `None` for unsupported extensions.
pub fn detect_by_extension(ext: &str) -> Option<&'static str> {
    match ext {
        "py" | "pyi" => Some("python"),
        "ts" => Some("typescript"),
        "tsx" => Some("typescript"),
        "js" => Some("javascript"),
        "jsx" => Some("javascript"),
        "java" => Some("java"),
        "go" => Some("go"),
        "rs" => Some("rust"),
        "kt" | "kts" => Some("kotlin"),
        "php" => Some("php"),
        "rb" => Some("ruby"),
        "c" => Some("c"),
        "h" => Some("c"),
        "cpp" | "cc" | "cxx" => Some("cpp"),
        "hpp" | "hh" | "hxx" => Some("cpp"),
        "cs" => Some("csharp"),
        "scala" | "sc" => Some("scala"),
        "ex" | "exs" => Some("elixir"),
        "hs" => Some("haskell"),
        "clj" | "cljs" | "cljc" | "edn" => Some("clojure"),
        "lua" => Some("lua"),
        "sh" | "bash" | "zsh" => Some("bash"),
        "dart" => Some("dart"),
        "swift" => Some("swift"),
        "html" | "htm" => Some("html"),
        "css" => Some("css"),
        "md" | "mdx" => Some("markdown"),
        "toml" => Some("toml"),
        "sql" => Some("sql"),
        "dockerfile" => Some("dockerfile"),
        "yaml" | "yml" => Some("yaml"),
        "hcl" | "tf" | "tfvars" => Some("hcl"),
        "json" => Some("json"),
        "proto" => Some("proto"),
        // Additional extensions without dedicated extractors yet
        "groovy" => Some("groovy"),
        "zig" => Some("zig"),
        "nix" => Some("nix"),
        "cmake" | "cmake.in" => Some("cmake"),
        _ => None,
    }
}

/// Detect language from a full file path.
pub fn detect(path: &str) -> Option<&'static str> {
    let ext = std::path::Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase())?;

    detect_by_extension(&ext)
}
