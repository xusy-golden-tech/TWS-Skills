//! Language detection — maps file extensions to language names.
//!
//! Covers 30+ languages with full extension-to-language mapping.

/// Return the canonical language name for a file extension (lowercase).
///
/// Returns `None` for unsupported extensions.
///
/// Supports programming languages, markup/styles, configuration formats,
/// data languages, and IaC formats.
pub fn detect_by_extension(ext: &str) -> Option<&'static str> {
    match ext {
        // --- programming languages ---
        "py" | "pyi" | "pyx" | "pxd" => Some("python"),
        "ts" => Some("typescript"),
        "tsx" => Some("typescript"),
        "js" => Some("javascript"),
        "jsx" => Some("javascript"),
        "mjs" => Some("javascript"),
        "cjs" => Some("javascript"),
        "java" => Some("java"),
        "go" => Some("go"),
        "rs" => Some("rust"),
        "kt" | "kts" => Some("kotlin"),
        "php" | "phtml" | "php3" | "php4" | "php5" | "phps" => Some("php"),
        "rb" => Some("ruby"),
        "c" => Some("c"),
        "h" => Some("c"),
        "cpp" | "cc" | "cxx" | "c++" => Some("cpp"),
        "hpp" | "hh" | "hxx" | "h++" => Some("cpp"),
        "cs" => Some("csharp"),
        "scala" | "sc" => Some("scala"),
        "ex" | "exs" => Some("elixir"),
        "hs" | "lhs" => Some("haskell"),
        "clj" | "cljs" | "cljc" | "edn" => Some("clojure"),
        "lua" => Some("lua"),
        "sh" | "bash" | "zsh" | "fish" => Some("bash"),
        "dart" => Some("dart"),
        "swift" => Some("swift"),
        "groovy" | "gvy" | "gy" | "gsh" => Some("groovy"),
        "zig" | "zir" => Some("zig"),
        "nix" => Some("nix"),
        "cmake" | "cmake.in" => Some("cmake"),
        "r" => Some("r"),
        "pl" | "pm" => Some("perl"),

        // --- markup / styles ---
        "html" | "htm" | "shtml" => Some("html"),
        "css" | "scss" | "sass" | "less" => Some("css"),
        "md" | "mdx" | "markdown" => Some("markdown"),

        // --- configuration / IaC ---
        "toml" => Some("toml"),
        "yaml" | "yml" => Some("yaml"),
        "hcl" | "tf" | "tfvars" => Some("hcl"),
        "json" | "jsonc" | "json5" => Some("json"),
        "proto" | "protobuf" => Some("proto"),

        // --- data ---
        "sql" | "psql" | "mysql" => Some("sql"),

        // --- docker ---
        "dockerfile" => Some("dockerfile"),

        _ => None,
    }
}

/// Detect language from a full file path.
///
/// Examines the file extension and maps it to the canonical language name.
/// Returns `None` if the extension is unsupported or missing.
pub fn detect_language(file_path: &str) -> Option<&'static str> {
    let ext = std::path::Path::new(file_path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase())?;

    detect_by_extension(&ext)
}

/// Legacy alias — delegates to [`detect_language`].
#[inline]
pub fn detect(path: &str) -> Option<&'static str> {
    detect_language(path)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // --- detect_by_extension ---

    #[test]
    fn test_python_extensions() {
        assert_eq!(detect_by_extension("py"), Some("python"));
        assert_eq!(detect_by_extension("pyi"), Some("python"));
        assert_eq!(detect_by_extension("pyx"), Some("python"));
        assert_eq!(detect_by_extension("pxd"), Some("python"));
    }

    #[test]
    fn test_typescript_extensions() {
        assert_eq!(detect_by_extension("ts"), Some("typescript"));
        assert_eq!(detect_by_extension("tsx"), Some("typescript"));
    }

    #[test]
    fn test_javascript_extensions() {
        assert_eq!(detect_by_extension("js"), Some("javascript"));
        assert_eq!(detect_by_extension("jsx"), Some("javascript"));
        assert_eq!(detect_by_extension("mjs"), Some("javascript"));
        assert_eq!(detect_by_extension("cjs"), Some("javascript"));
    }

    #[test]
    fn test_java() {
        assert_eq!(detect_by_extension("java"), Some("java"));
    }

    #[test]
    fn test_go() {
        assert_eq!(detect_by_extension("go"), Some("go"));
    }

    #[test]
    fn test_rust() {
        assert_eq!(detect_by_extension("rs"), Some("rust"));
    }

    #[test]
    fn test_kotlin() {
        assert_eq!(detect_by_extension("kt"), Some("kotlin"));
        assert_eq!(detect_by_extension("kts"), Some("kotlin"));
    }

    #[test]
    fn test_php() {
        assert_eq!(detect_by_extension("php"), Some("php"));
        assert_eq!(detect_by_extension("phtml"), Some("php"));
    }

    #[test]
    fn test_ruby() {
        assert_eq!(detect_by_extension("rb"), Some("ruby"));
    }

    #[test]
    fn test_c() {
        assert_eq!(detect_by_extension("c"), Some("c"));
        assert_eq!(detect_by_extension("h"), Some("c"));
    }

    #[test]
    fn test_cpp() {
        assert_eq!(detect_by_extension("cpp"), Some("cpp"));
        assert_eq!(detect_by_extension("cc"), Some("cpp"));
        assert_eq!(detect_by_extension("cxx"), Some("cpp"));
        assert_eq!(detect_by_extension("hpp"), Some("cpp"));
        assert_eq!(detect_by_extension("hh"), Some("cpp"));
        assert_eq!(detect_by_extension("hxx"), Some("cpp"));
    }

    #[test]
    fn test_csharp() {
        assert_eq!(detect_by_extension("cs"), Some("csharp"));
    }

    #[test]
    fn test_scala() {
        assert_eq!(detect_by_extension("scala"), Some("scala"));
        assert_eq!(detect_by_extension("sc"), Some("scala"));
    }

    #[test]
    fn test_elixir() {
        assert_eq!(detect_by_extension("ex"), Some("elixir"));
        assert_eq!(detect_by_extension("exs"), Some("elixir"));
    }

    #[test]
    fn test_haskell() {
        assert_eq!(detect_by_extension("hs"), Some("haskell"));
        assert_eq!(detect_by_extension("lhs"), Some("haskell"));
    }

    #[test]
    fn test_clojure() {
        assert_eq!(detect_by_extension("clj"), Some("clojure"));
        assert_eq!(detect_by_extension("cljs"), Some("clojure"));
        assert_eq!(detect_by_extension("cljc"), Some("clojure"));
        assert_eq!(detect_by_extension("edn"), Some("clojure"));
    }

    #[test]
    fn test_lua() {
        assert_eq!(detect_by_extension("lua"), Some("lua"));
    }

    #[test]
    fn test_bash() {
        assert_eq!(detect_by_extension("sh"), Some("bash"));
        assert_eq!(detect_by_extension("bash"), Some("bash"));
        assert_eq!(detect_by_extension("zsh"), Some("bash"));
    }

    #[test]
    fn test_dart() {
        assert_eq!(detect_by_extension("dart"), Some("dart"));
    }

    #[test]
    fn test_swift() {
        assert_eq!(detect_by_extension("swift"), Some("swift"));
    }

    #[test]
    fn test_html() {
        assert_eq!(detect_by_extension("html"), Some("html"));
        assert_eq!(detect_by_extension("htm"), Some("html"));
    }

    #[test]
    fn test_css() {
        assert_eq!(detect_by_extension("css"), Some("css"));
    }

    #[test]
    fn test_markdown() {
        assert_eq!(detect_by_extension("md"), Some("markdown"));
        assert_eq!(detect_by_extension("mdx"), Some("markdown"));
    }

    #[test]
    fn test_toml() {
        assert_eq!(detect_by_extension("toml"), Some("toml"));
    }

    #[test]
    fn test_yaml() {
        assert_eq!(detect_by_extension("yaml"), Some("yaml"));
        assert_eq!(detect_by_extension("yml"), Some("yaml"));
    }

    #[test]
    fn test_hcl() {
        assert_eq!(detect_by_extension("hcl"), Some("hcl"));
        assert_eq!(detect_by_extension("tf"), Some("hcl"));
        assert_eq!(detect_by_extension("tfvars"), Some("hcl"));
    }

    #[test]
    fn test_json() {
        assert_eq!(detect_by_extension("json"), Some("json"));
    }

    #[test]
    fn test_proto() {
        assert_eq!(detect_by_extension("proto"), Some("proto"));
    }

    #[test]
    fn test_sql() {
        assert_eq!(detect_by_extension("sql"), Some("sql"));
    }

    #[test]
    fn test_dockerfile() {
        assert_eq!(detect_by_extension("dockerfile"), Some("dockerfile"));
    }

    #[test]
    fn test_groovy() {
        assert_eq!(detect_by_extension("groovy"), Some("groovy"));
    }

    #[test]
    fn test_zig() {
        assert_eq!(detect_by_extension("zig"), Some("zig"));
    }

    #[test]
    fn test_nix() {
        assert_eq!(detect_by_extension("nix"), Some("nix"));
    }

    #[test]
    fn test_cmake() {
        assert_eq!(detect_by_extension("cmake"), Some("cmake"));
        assert_eq!(detect_by_extension("cmake.in"), Some("cmake"));
    }

    #[test]
    fn test_unknown_extension() {
        assert_eq!(detect_by_extension("xyzzy"), None);
        assert_eq!(detect_by_extension(""), None);
    }

    // --- detect_language ---

    #[test]
    fn test_detect_language_from_path() {
        assert_eq!(detect_language("src/main.rs"), Some("rust"));
        assert_eq!(detect_language("src/lib.py"), Some("python"));
        assert_eq!(detect_language("src/App.tsx"), Some("typescript"));
        assert_eq!(detect_language("src/index.js"), Some("javascript"));
        assert_eq!(detect_language("styles.css"), Some("css"));
        assert_eq!(detect_language("Dockerfile"), None); // Dockerfile not dockerfile extension
        assert_eq!(detect_language("path/to/Dockerfile.dockerfile"), Some("dockerfile")); // handled as "dockerfile" ext
        assert_eq!(detect_language("no_extension"), None);
    }

    #[test]
    fn test_detect_language_delegates_to_detect_by_extension() {
        // Verify detect_language and detect_by_extension agree
        for ext in &[
            "py", "rs", "ts", "js", "go", "java", "rb", "php", "kt", "cs",
            "scala", "ex", "hs", "clj", "lua", "sh", "dart", "swift",
            "html", "css", "md", "toml", "yaml", "hcl", "json", "proto", "sql",
        ] {
            let path = format!("file.{}", ext);
            let from_path = detect_language(&path);
            let from_ext = detect_by_extension(ext);
            assert_eq!(
                from_path, from_ext,
                "mismatch for extension .{}: detect_language={:?}, detect_by_extension={:?}",
                ext, from_path, from_ext
            );
        }
    }

    #[test]
    fn test_detect_language_case_insensitive() {
        assert_eq!(detect_language("file.PY"), Some("python"));
        assert_eq!(detect_language("file.RS"), Some("rust"));
        assert_eq!(detect_language("file.TSX"), Some("typescript"));
    }

    // --- detect (legacy) ---

    #[test]
    fn test_detect_legacy_delegates() {
        assert_eq!(detect("src/main.rs"), Some("rust"));
        assert_eq!(detect("src/lib.py"), Some("python"));
        assert_eq!(detect("unknown.xyz"), None);
    }
}
