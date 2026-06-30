//! Language registry — maps language identifiers to their ModuleResolvers.
//!
//! Phase 0 provides a skeleton Python resolver.  Phase 2 adds TypeScript and
//! JavaScript resolvers.

use crate::resolver::ModuleResolver;
use crate::resolver::module_index::ModuleIndex;
use std::path::Path;

/// Registry that maps language names (e.g. "python", "typescript") to their
/// dedicated [`ModuleResolver`] implementations.
pub struct LanguageRegistry;

impl LanguageRegistry {
    /// Return the resolver for the given language, or `None` if the language
    /// is not yet supported.
    pub fn get(language: &str) -> Option<Box<dyn ModuleResolver>> {
        match language {
            "python" => Some(Box::new(PythonResolver)),
            "typescript" | "javascript" | "tsx" | "jsx" => Some(Box::new(TypeScriptResolver)),
            "java" => Some(Box::new(JavaResolver)),
            "kotlin" => Some(Box::new(KotlinResolver)),
            "go" => Some(Box::new(GoResolver)),
            "rust" => Some(Box::new(RustResolver)),
            "php" => Some(Box::new(PhpResolver)),
            "ruby" => Some(Box::new(RubyResolver)),
            "c" | "cpp" | "c++" => Some(Box::new(CppResolver)),
            _ => None,
        }
    }

    /// Return a list of language names that have resolvers registered.
    pub fn supported_languages() -> Vec<&'static str> {
        vec!["python", "typescript", "javascript", "java", "kotlin", "go", "rust", "php", "ruby", "c", "cpp"]
    }
}

// ---------------------------------------------------------------------------
// Python module resolver (Phase 0 — skeleton)
// ---------------------------------------------------------------------------

/// Python module resolver.
///
/// Uses Python import conventions:
/// - `from foo.bar import Baz` → look for `Baz` in `foo/bar.py` or `foo/bar/__init__.py`
/// - `import foo.bar` → treat `foo.bar` as the module
pub struct PythonResolver;

impl ModuleResolver for PythonResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        // Direct lookup: module_name → files
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // Try with common prefixes (if module_index has entries with/without src/)
        for prefix in &["", "src.", "lib."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        Vec::new()
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "python")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_python_external(module_name)
    }

    fn language(&self) -> &'static str {
        "python"
    }
}

/// Check if a module name is a known Python standard library or popular
/// third-party package.
///
/// This mirrors the heuristic in `query::edge_resolver::is_likely_external`
/// but is scoped to Python-only and used by the `ModuleResolver` trait.
pub fn is_python_external(module_name: &str) -> bool {
    let lower = module_name.to_lowercase();
    let root = lower.split('.').next().unwrap_or(&lower);

    let stdlib_and_common: &[&str] = &[
        // stdlib
        "os", "sys", "re", "json", "csv", "math", "random", "datetime", "time",
        "collections", "itertools", "functools", "typing", "io", "pathlib",
        "logging", "subprocess", "threading", "asyncio", "argparse", "hashlib",
        "uuid", "base64", "copy", "enum", "abc", "dataclasses", "inspect",
        "warnings", "traceback", "tempfile", "shutil", "glob", "fnmatch",
        "pickle", "shelve", "marshal", "struct", "textwrap", "string", "pprint",
        "profile", "pdb", "unittest",
        // common third-party
        "typer", "click", "fastapi", "flask", "django", "requests", "numpy",
        "pandas", "sqlalchemy", "pytest", "pydantic", "sqlite3", "concurrent",
        "http", "xml", "html", "urllib", "email", "socket", "ssl", "ctypes",
        "decimal", "fractions", "statistics",
    ];

    if stdlib_and_common.contains(&root) {
        return true;
    }

    // Internal Python dunder / special names
    if root.starts_with("__") || root.starts_with("_") {
        return false; // private/internal modules are not external
    }

    false
}

// ---------------------------------------------------------------------------
// TypeScript / JavaScript module resolver
// ---------------------------------------------------------------------------

/// TypeScript (and JavaScript) module resolver.
///
/// Handles the ES module system and CommonJS `require()`:
/// - Relative imports: `./foo`, `../bar` → resolved against source file
/// - Bare specifiers: `react`, `lodash` → looked up in ModuleIndex or marked external
/// - Extension resolution: tries `.ts`, `.tsx`, `.js`, `.jsx`, `/index.ts`, etc.
pub struct TypeScriptResolver;

impl ModuleResolver for TypeScriptResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        // 1. Try ModuleIndex lookup first (direct match)
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Relative path resolution
        if module_name.starts_with('.') {
            let source_dir = Path::new(source_file)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or(".");

            let resolved = if source_dir.is_empty() || source_dir == "." {
                module_name.to_string()
            } else {
                Path::new(source_dir)
                    .join(module_name)
                    .to_string_lossy()
                    .replace('\\', "/")
            };

            let normalized = simplify_ts_path(&resolved);

            // Try common extensions
            return find_ts_module_files(&normalized);
        }

        // 3. Try ModuleIndex with path-to-dotted variants
        // e.g. "src/components/Button" → "src.components.Button" or "components.Button"
        let dotted = module_name.replace('/', ".");
        if let Some(files) = module_index.lookup(&dotted) {
            return files.clone();
        }

        // 4. Try stripping common source root prefixes
        for prefix in &["src.", "lib.", "test.", "tests."] {
            if let Some(stripped) = dotted.strip_prefix(prefix) {
                if let Some(files) = module_index.lookup(stripped) {
                    return files.clone();
                }
            }
        }

        // 5. Also try adding common source prefixes (e.g. bare "components.Button" → "src.components.Button")
        for prefix in &["src.", "lib."] {
            let candidate = format!("{}{}", prefix, dotted);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        Vec::new()
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "typescript")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_ts_external(module_name)
    }

    fn language(&self) -> &'static str {
        "typescript"
    }
}

/// Check if a module name is a known npm package or external dependency.
///
/// A module is considered external if it is a bare specifier (not starting
/// with `.` or `/`) that matches a well-known npm package or does not look
/// like a project-local path.
pub fn is_ts_external(module_name: &str) -> bool {
    // Empty module names can't be external
    if module_name.is_empty() {
        return false;
    }
    // Relative paths are always local
    if module_name.starts_with('.') {
        return false;
    }
    // Absolute paths are local
    if module_name.starts_with('/') {
        return false;
    }
    // Path aliases (e.g. @/components) are local
    if module_name.starts_with('@') && module_name.contains('/') {
        // Could be an npm org package (@scope/name) or a path alias
        // For now, if it looks like a path alias (short, no @ in middle), treat as local
        let parts: Vec<&str> = module_name.split('/').collect();
        if parts.len() == 2 && !parts[1].is_empty() {
            // Check common npm org packages
            let org = parts[0];
            if is_known_npm_org(org) {
                return true;
            }
            // Otherwise, could be path alias — check if it looks local
            // If the second part looks like a project path, it might be local
            return false;
        }
    }

    let root = module_name.split('/').next().unwrap_or(module_name);

    let common_npm_packages: &[&str] = &[
        "react", "react-dom", "react-native", "vue", "vue-router", "vuex",
        "angular", "@angular",
        "lodash", "underscore", "ramda",
        "express", "koa", "fastify", "hapi",
        "axios", "node-fetch", "got", "superagent",
        "moment", "dayjs", "luxon", "date-fns",
        "redux", "mobx", "zustand", "recoil", "jotai",
        "next", "nuxt", "gatsby", "svelte", "sveltekit",
        "typescript", "tslib",
        "jest", "mocha", "chai", "sinon", "vitest",
        "webpack", "rollup", "vite", "esbuild", "parcel", "babel",
        "eslint", "prettier", "stylelint",
        "tailwindcss", "bootstrap", "sass", "less",
        "graphql", "apollo", "urql",
        "prisma", "typeorm", "sequelize", "knex", "mongoose",
        "rxjs", "immutable", "immer",
        "d3", "three", "pixi", "phaser",
        "socket.io", "ws",
        "commander", "yargs", "inquirer", "chalk", "ora",
        "uuid", "nanoid", "classnames",
        "zod", "yup", "joi",
        "ioredis", "redis", "mysql", "mysql2", "pg", "sqlite3",
        "aws-sdk", "@aws-sdk",
        "firebase", "@firebase",
        "dotenv", "cross-env",
    ];

    if common_npm_packages.contains(&root) || common_npm_packages.contains(&module_name) {
        return true;
    }

    // Check for node built-in modules
    let node_builtins: &[&str] = &[
        "fs", "path", "os", "http", "https", "http2", "net", "tls", "dns",
        "stream", "buffer", "crypto", "events", "url", "querystring", "util",
        "child_process", "cluster", "readline", "repl", "vm", "worker_threads",
        "assert", "async_hooks", "console", "module", "process", "timers",
        "tty", "v8", "zlib", "string_decoder", "perf_hooks",
    ];
    if node_builtins.contains(&root) {
        return true;
    }

    // For other bare specifiers: if we can't resolve them locally,
    // they're likely external npm packages
    // But this is only called after resolve_module returns empty,
    // so at this point it's truly unknown
    true
}

/// Known npm organization scopes that indicate external packages.
fn is_known_npm_org(scope: &str) -> bool {
    let known_scopes: &[&str] = &[
        "@angular", "@babel", "@types", "@aws-sdk", "@firebase", "@google-cloud",
        "@mui", "@emotion", "@radix-ui", "@tanstack", "@headlessui",
        "@heroicons", "@next", "@vitejs", "@playwright", "@testing-library",
        "@storybook", "@reduxjs", "@apollo", "@graphql-codegen",
        "@anthropic-ai", "@openai", "@langchain",
        "@nestjs", "@prisma", "@pmmmwh", "@swc", "@jest",
    ];
    known_scopes.contains(&scope)
}

/// Given a normalized path (without extension), find matching files by
/// trying known TypeScript/JavaScript extensions and index files.
fn find_ts_module_files(normalized_path: &str) -> Vec<String> {
    let extensions = &["ts", "tsx", "js", "jsx", "mjs", "cjs"];
    let mut candidates = Vec::new();

    // Try direct file matches
    for ext in extensions {
        candidates.push(format!("{}.{}", normalized_path, ext));
    }

    // Try index files in directory
    for ext in &["ts", "tsx", "js", "jsx"] {
        candidates.push(format!("{}/index.{}", normalized_path, ext));
    }

    candidates
}

/// Simplify a TypeScript-style path by resolving `.` and `..` segments.
fn simplify_ts_path(path: &str) -> String {
    let parts: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    let mut result: Vec<&str> = Vec::new();

    for part in parts {
        match part {
            "." => {}
            ".." => {
                if !result.is_empty() && result.last() != Some(&"..") {
                    result.pop();
                } else {
                    result.push(part);
                }
            }
            _ => result.push(part),
        }
    }
    result.join("/")
}

// ---------------------------------------------------------------------------
// Java module resolver
// ---------------------------------------------------------------------------

/// Java module resolver.
///
/// Uses Java package conventions:
/// - `import com.foo.bar.MyClass;` → module = `com.foo.bar.MyClass` → file = `com/foo/bar/MyClass.java`
/// - `import com.foo.bar.*;` → module = `com.foo.bar` → files = `com/foo/bar/*.java`
/// - ModuleIndex lookup: dotted package.declaration → file_path mapping
/// - Source root prefixes: `src/main/java/`, `src/test/java/`, `src/`
pub struct JavaResolver;

impl ModuleResolver for JavaResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        // 1. Direct ModuleIndex lookup (module_name as dotted package)
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try removing the class name (last dot-segment) to get package name
        // e.g., "com.foo.bar.MyClass" → module_name "com.foo.bar"
        if let Some(last_dot) = module_name.rfind('.') {
            let package_name = &module_name[..last_dot];
            let class_name = &module_name[last_dot + 1..];

            // Look up the package in ModuleIndex
            if let Some(files) = module_index.lookup(package_name) {
                return files.clone();
            }

            // 3. Try standard source root prefixes (Maven/Gradle conventions)
            // Convert package to path: com/foo/bar → look for files
            let package_path = package_name.replace('.', "/");
            let candidates = vec![
                format!("src/main/java/{}/{}.java", package_path, class_name),
                format!("src/test/java/{}/{}.java", package_path, class_name),
                format!("src/{}/{}.java", package_path, class_name),
            ];

            let mut result = Vec::new();
            for candidate in &candidates {
                if let Some(files) = module_index.lookup(candidate) {
                    result.extend(files.clone());
                }
            }

            // Add candidates as potential file paths even if not in index
            // (the resolver will check if they actually exist)
            for candidate in &candidates {
                let dotted = candidate.replace('/', ".").trim_end_matches(".java").to_string();
                if let Some(files) = module_index.lookup(&dotted) {
                    result.extend(files.clone());
                }
            }

            if !result.is_empty() {
                return result;
            }

            // Return candidate paths for further matching
            return candidates;
        }

        // 4. Handle package-only module_name (from wildcard imports)
        let package_path = module_name.replace('.', "/");
        let candidates = vec![
            format!("src/main/java/{}", package_path),
            format!("src/test/java/{}", package_path),
            format!("src/{}", package_path),
        ];

        // Try ModuleIndex lookup with various prefixes
        for prefix in &["src.main.java.", "src.test.java.", "src."] {
            let with_prefix = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&with_prefix) {
                return files.clone();
            }
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "java")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_java_external(module_name)
    }

    fn language(&self) -> &'static str {
        "java"
    }
}

/// Check if a module name is a known Java standard library or common
/// third-party framework.
///
/// A module is considered external if it starts with a Java stdlib prefix
/// or a known third-party framework prefix.
pub fn is_java_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Java standard library / JDK internal prefixes (full prefix matching)
    let stdlib_prefixes: &[&str] = &[
        "java.", "javax.", "jakarta.", "org.w3c.", "org.xml.", "org.omg.",
        "org.ietf.", "com.sun.", "sun.",
    ];
    for prefix in stdlib_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // Common third-party frameworks
    let third_party_prefixes: &[&str] = &[
        "org.springframework.", "org.hibernate.", "org.apache.", "org.junit.",
        "org.slf4j.", "org.mockito.", "org.assertj.", "org.testng.",
        "org.eclipse.", "org.jetbrains.", "org.jboss.", "org.glassfish.",
        "com.google.", "com.fasterxml.", "io.netty.", "io.grpc.",
        "ch.qos.logback.", "lombok.", "javax.persistence.",
        "reactor.", "reactor.core.", "kafka.", "redis.clients.",
    ];

    for prefix in third_party_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    false
}

// ---------------------------------------------------------------------------
// Kotlin module resolver
// ---------------------------------------------------------------------------

/// Kotlin module resolver.
///
/// Uses the same JVM package conventions as Java:
/// - `import com.foo.bar.MyClass` → module = `com.foo.bar.MyClass` → file = `com/foo/bar/MyClass.kt`
/// - `import com.foo.bar.*` → module = `com.foo.bar` → files = `com/foo/bar/*.kt`
/// - ModuleIndex lookup: dotted package.declaration → file_path mapping
/// - Source root prefixes: `src/main/kotlin/`, `src/main/java/`, `src/test/kotlin/`, etc.
///
/// Kotlin files can coexist with Java in the same source tree; known stdlib
/// and third-party JVM packages are classified as external.
pub struct KotlinResolver;

impl ModuleResolver for KotlinResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        // 1. Direct ModuleIndex lookup
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try removing the class/function name to get package name
        if let Some(last_dot) = module_name.rfind('.') {
            let package_name = &module_name[..last_dot];
            let class_name = &module_name[last_dot + 1..];

            // Look up the package in ModuleIndex
            if let Some(files) = module_index.lookup(package_name) {
                return files.clone();
            }

            // 3. Try standard source root prefixes (Maven/Gradle conventions)
            let package_path = package_name.replace('.', "/");
            let candidates = vec![
                format!("src/main/kotlin/{}/{}.kt", package_path, class_name),
                format!("src/test/kotlin/{}/{}.kt", package_path, class_name),
                format!("src/main/java/{}/{}.kt", package_path, class_name),
                format!("src/test/java/{}/{}.kt", package_path, class_name),
                format!("src/{}/{}.kt", package_path, class_name),
            ];

            let mut result = Vec::new();
            for candidate in &candidates {
                if let Some(files) = module_index.lookup(candidate) {
                    result.extend(files.clone());
                }
            }

            // Also try as dotted name
            for candidate in &candidates {
                let dotted = candidate.replace('/', ".").trim_end_matches(".kt").to_string();
                if let Some(files) = module_index.lookup(&dotted) {
                    result.extend(files.clone());
                }
            }

            if !result.is_empty() {
                return result;
            }

            return candidates;
        }

        // 4. Handle package-only module_name (from wildcard imports)
        let package_path = module_name.replace('.', "/");
        let candidates = vec![
            format!("src/main/kotlin/{}", package_path),
            format!("src/test/kotlin/{}", package_path),
            format!("src/main/java/{}", package_path),
            format!("src/test/java/{}", package_path),
            format!("src/{}", package_path),
        ];

        // Try ModuleIndex lookup with various prefixes
        for prefix in &["src.main.kotlin.", "src.test.kotlin.", "src.main.java.", "src.test.java.", "src."] {
            let with_prefix = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&with_prefix) {
                return files.clone();
            }
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "kotlin")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_kotlin_external(module_name)
    }

    fn language(&self) -> &'static str {
        "kotlin"
    }
}

/// Check if a module name is a known Kotlin/JVM standard library or common
/// third-party framework.  Kotlin shares the JVM ecosystem so this is
/// identical to Java's `is_java_external`, plus Kotlin stdlib prefixes.
pub fn is_kotlin_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Kotlin-specific stdlib prefixes
    let kotlin_prefixes: &[&str] = &[
        "kotlin.", "kotlinx.", "android.", "androidx.",
    ];
    for prefix in kotlin_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // All Java stdlib / third-party prefixes also apply to Kotlin
    is_java_external(module_name)
}

// ---------------------------------------------------------------------------
// Go module resolver
// ---------------------------------------------------------------------------

/// Go module resolver.
///
/// Uses Go import conventions:
/// - `import "fmt"` → standard library, marked external
/// - `import "github.com/foo/bar"` → external module path, marked external
/// - `import "mymodule/user"` → project-internal, resolved via ModuleIndex
/// - `import alias "pkg/path"` → alias tracked, last-segment used for lookup
///
/// ModuleIndex maps Go directory names (last path segment) to file paths.
/// The resolver tries the full import path first, then falls back to the
/// last segment for directory-based lookup.
pub struct GoResolver;

impl ModuleResolver for GoResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        // 1. Direct ModuleIndex lookup with full import path
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try last path segment (directory name = Go package name)
        // e.g. "mymodule/pkg/user" → look up "user"
        if module_name.contains('/') {
            // Try from right to left: "user", then "pkg/user"
            let segments: Vec<&str> = module_name.rsplit('/').collect();
            for i in 0..segments.len().min(2) {
                let candidate = if i == 0 {
                    segments[0].to_string()
                } else {
                    format!("{}/{}", segments[1], segments[0])
                };
                if let Some(files) = module_index.lookup(&candidate) {
                    return files.clone();
                }
            }
        }

        Vec::new()
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "go")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_go_external(module_name)
    }

    fn language(&self) -> &'static str {
        "go"
    }
}

/// Check if a module name is a known Go standard library package or
/// an external (third-party) module.
///
/// A module is considered external if:
/// - It is a Go standard library package (fmt, os, net/http, etc.)
/// - It contains a domain name prefix (github.com/, golang.org/, etc.)
/// - It starts with known external module prefixes
///
/// Project-internal imports (bare directory names like "mymodule/user")
/// are NOT considered external unless they match the above patterns.
pub fn is_go_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Go standard library packages
    // These are short names without dots or domain prefixes
    let go_stdlib: &[&str] = &[
        "archive", "bufio", "builtin", "bytes", "compress", "container",
        "context", "crypto", "database", "debug", "embed", "encoding",
        "errors", "expvar", "flag", "fmt", "go", "hash", "html", "image",
        "index", "io", "log", "math", "mime", "net", "os", "path", "plugin",
        "reflect", "regexp", "runtime", "sort", "strconv", "strings",
        "sync", "syscall", "testing", "text", "time", "unicode", "unsafe",
        // Extended stdlib packages (sub-packages)
        "archive/tar", "archive/zip",
        "compress/bzip2", "compress/flate", "compress/gzip", "compress/lzw", "compress/zlib",
        "crypto/aes", "crypto/cipher", "crypto/des", "crypto/dsa", "crypto/ecdsa",
        "crypto/ed25519", "crypto/elliptic", "crypto/hmac", "crypto/md5",
        "crypto/rand", "crypto/rc4", "crypto/rsa", "crypto/sha1",
        "crypto/sha256", "crypto/sha512", "crypto/subtle", "crypto/tls",
        "crypto/x509", "crypto/x509/pkix",
        "database/sql", "database/sql/driver",
        "encoding/ascii85", "encoding/asn1", "encoding/base32", "encoding/base64",
        "encoding/binary", "encoding/csv", "encoding/gob", "encoding/hex",
        "encoding/json", "encoding/pem", "encoding/xml",
        "go/ast", "go/build", "go/constant", "go/doc", "go/format",
        "go/importer", "go/parser", "go/printer", "go/scanner",
        "go/token", "go/types",
        "hash/adler32", "hash/crc32", "hash/crc64", "hash/fnv",
        "hash/maphash",
        "html/template",
        "image/color", "image/color/palette", "image/draw",
        "image/gif", "image/jpeg", "image/png",
        "index/suffixarray",
        "io/fs", "io/ioutil",
        "log/syslog",
        "math/big", "math/bits", "math/cmplx", "math/rand",
        "mime/multipart", "mime/quotedprintable",
        "net/http", "net/http/cgi", "net/http/cookiejar", "net/http/fcgi",
        "net/http/httptest", "net/http/httptrace", "net/http/httputil",
        "net/http/pprof", "net/mail", "net/rpc", "net/rpc/jsonrpc",
        "net/smtp", "net/textproto", "net/url",
        "os/exec", "os/signal", "os/user",
        "path/filepath",
        "regexp/syntax",
        "testing/fstest", "testing/iotest", "testing/quick",
    ];

    // Direct match
    if go_stdlib.contains(&module_name) {
        return true;
    }

    // Check if root package (before first /) is in stdlib
    if let Some(slash_pos) = module_name.find('/') {
        let root = &module_name[..slash_pos];
        // If root is one of the known stdlib roots
        let stdlib_roots: &[&str] = &[
            "archive", "bufio", "bytes", "compress", "container", "context",
            "crypto", "database", "debug", "embed", "encoding", "errors",
            "expvar", "flag", "fmt", "go", "hash", "html", "image", "index",
            "io", "log", "math", "mime", "net", "os", "path", "plugin",
            "reflect", "regexp", "runtime", "sort", "strconv", "strings",
            "sync", "syscall", "testing", "text", "time", "unicode", "unsafe",
        ];
        if stdlib_roots.contains(&root) {
            return true;
        }
    }

    // Known Go module hosting domains
    let external_hosts: &[&str] = &[
        "github.com/", "gitlab.com/", "bitbucket.org/",
        "golang.org/", "google.golang.org/",
        "k8s.io/", "go.uber.org/", "go.opencensus.io/",
        "gopkg.in/", "go.etcd.io/", "cloud.google.com/go/",
    ];
    for host in external_hosts {
        if module_name.starts_with(host) {
            return true;
        }
    }

    // "golang.org/x/" is the Go extended library
    if module_name.starts_with("golang.org/x/") {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Rust module resolver
// ---------------------------------------------------------------------------

/// Rust module resolver.
///
/// Handles Rust's module system:
/// - `crate::foo::bar` → absolute from crate root
/// - `super::foo` → parent module
/// - `self::foo` → current module
/// - `serde::Serialize` → external crate (no file matching)
/// - Module files: `foo.rs` or `foo/mod.rs`
///
/// The resolver converts `::`-separated paths to filesystem paths.
pub struct RustResolver;

impl ModuleResolver for RustResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        // 1. Try ModuleIndex direct lookup
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Handle crate:: prefix → absolute from crate root
        if let Some(stripped) = module_name.strip_prefix("crate::") {
            return resolve_rust_absolute_path(stripped);
        }

        // 3. Handle super:: prefix → parent module directory (ONE level up)
        if let Some(relative) = module_name.strip_prefix("super::") {
            let source_dir = Path::new(source_file)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or(".");
            // super:: goes to the PARENT module (one directory up from source file)
            let path = if source_dir.is_empty() || source_dir == "." {
                relative.replace("::", "/")
            } else {
                format!("{}/{}", source_dir, relative.replace("::", "/"))
            };
            return resolve_rust_module_files(&path);
        }

        // 4. Handle self:: prefix → current module directory
        if let Some(relative) = module_name.strip_prefix("self::") {
            let source_dir = Path::new(source_file)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or(".");
            let path = if source_dir.is_empty() || source_dir == "." {
                relative.replace("::", "/")
            } else {
                format!("{}/{}", source_dir, relative.replace("::", "/"))
            };
            return resolve_rust_module_files(&path);
        }

        // 5. No prefix — try as absolute from crate root
        // First check ModuleIndex with the full path and as-is
        let as_dots = module_name.replace("::", ".");
        if let Some(files) = module_index.lookup(&as_dots) {
            return files.clone();
        }
        // Try stripping common source root prefixes
        for prefix in &["src.", "crate."] {
            if let Some(stripped) = as_dots.strip_prefix(prefix) {
                if let Some(files) = module_index.lookup(stripped) {
                    return files.clone();
                }
            }
        }

        // Default: treat as absolute path (same as crate::...)
        resolve_rust_absolute_path(module_name)
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "rust")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_rust_external(module_name)
    }

    fn language(&self) -> &'static str {
        "rust"
    }
}

/// Convert a Rust module path (e.g. "foo::bar::Baz") to candidate file paths.
/// Each `::` segment maps to a directory level.  The final segment can be either
/// `<last>.rs` or `<last>/mod.rs`.
fn resolve_rust_absolute_path(module_path: &str) -> Vec<String> {
    let fs_path = module_path.replace("::", "/");
    resolve_rust_module_files(&fs_path)
}

/// Given a filesystem path (without extension), return candidate Rust files.
/// Path separators are normalized to `/` for cross-platform consistency.
fn resolve_rust_module_files(fs_path: &str) -> Vec<String> {
    let normalized = fs_path.replace('\\', "/");
    vec![
        format!("{}.rs", normalized),
        format!("{}/mod.rs", normalized),
    ]
}

/// Check if a Rust module path refers to an external crate.
///
/// An external crate is identified by checking the root segment against:
/// - Rust standard library: `std`, `core`, `alloc`
/// - Common third-party crates: `serde`, `tokio`, `actix`, `reqwest`, etc.
///
/// Paths starting with `crate::`, `super::`, or `self::` are always internal.
pub fn is_rust_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Paths with local scope prefixes are always internal
    if module_name.starts_with("crate::")
        || module_name.starts_with("super::")
        || module_name.starts_with("self::")
    {
        return false;
    }

    let root = module_name.split("::").next().unwrap_or(module_name);

    // Rust standard library + built-in crates
    let stdlib: &[&str] = &[
        "std", "core", "alloc", "proc_macro", "test",
    ];
    if stdlib.contains(&root) {
        return true;
    }

    // Popular third-party crates
    let third_party: &[&str] = &[
        // Serialization
        "serde", "serde_json", "serde_yaml", "serde_derive",
        "toml", "ron", "bincode", "csv",
        // Async runtime
        "tokio", "async_std", "smol", "futures", "async_trait",
        // Web frameworks
        "actix", "actix_web", "actix_rt",
        "rocket", "warp", "axum", "tide", "poem",
        "hyper", "tonic", "tower",
        // HTTP clients
        "reqwest", "ureq", "hyper",
        // Database
        "diesel", "sqlx", "rusqlite", "mysql", "postgres",
        "redis", "mongodb", "sled",
        // CLI / Config
        "clap", "structopt", "config", "dotenv", "dotenvy",
        // Error handling / Logging
        "anyhow", "thiserror", "eyre", "miette",
        "log", "env_logger", "tracing", "slog",
        // Testing
        "mockall", "proptest", "criterion", "pretty_assertions",
        // Misc utilities
        "rand", "chrono", "time",
        "regex", "lazy_static", "once_cell",
        "parking_lot", "dashmap", "crossbeam",
        "rayon", "itertools", "either",
        "bytes", "smallvec", "indexmap",
        "uuid", "base64", "hex",
        "url", "http", "mime",
        "semver", "tempfile", "dirs",
        "num", "num_cpus",
        "signal_hook", "nix", "libc",
        "gix", "git2",
        "image", "syn", "quote", "proc_macro2", "darling",
        "wasm_bindgen", "js_sys", "web_sys",
        "gloo", "yew", "leptos", "dioxus",
        "pyo3", "napi", "neon", "jni",
        "tch", "ndarray", "nalgebra", "cgmath",
        "openssl", "rustls", "native_tls",
        "ring", "hmac", "sha2", "md5", "digest",
        "flate2", "tar", "zip",
    ];

    if third_party.contains(&root) {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// PHP module resolver
// ---------------------------------------------------------------------------

/// PHP module resolver.
///
/// Uses PSR-4 autoloading conventions:
/// - `use Foo\Bar\Baz` → class `Baz` in namespace `Foo\Bar` → file `Foo/Bar/Baz.php`
/// - `use function Foo\Bar\func` → function `func` → file `Foo/Bar.php`
/// - `use const Foo\Bar\MY_CONST` → constant → file `Foo/Bar.php`
///
/// Common source roots: `src/`, `lib/`, `app/`, `includes/`
pub struct PhpResolver;

impl ModuleResolver for PhpResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        _source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        // 1. Direct ModuleIndex lookup with full module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["src.", "lib.", "app.", "includes."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Convert module name to file path and try candidate paths
        let namespace_path = module_name.replace('\\', "/");

        // Build candidate PHP file paths with common source roots
        let roots = &["src", "lib", "app", "includes", ""];
        let mut result = Vec::new();
        for root in roots {
            let candidate_file = if root.is_empty() {
                format!("{}.php", namespace_path)
            } else {
                format!("{}/{}.php", root, namespace_path)
            };
            // Check if this file exists in ModuleIndex
            if let Some(files) = module_index.lookup(&candidate_file.replace('/', ".").trim_end_matches(".php").to_string()) {
                result.extend(files.clone());
            }
            result.push(candidate_file);
        }

        result
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "php")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_php_external(module_name)
    }

    fn language(&self) -> &'static str {
        "php"
    }
}

/// Check if a module name is a known PHP built-in class or common
/// third-party package.
///
/// PHP built-in classes and functions are always available without
/// importing (PDO, Exception, DateTime, etc.).  Common frameworks
/// (Laravel/Illuminate, Symfony, Doctrine, etc.) are classified as
/// external dependencies.
pub fn is_php_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    let lower = module_name.to_lowercase();

    // PHP built-in classes and interfaces
    let builtin_classes: &[&str] = &[
        "pdo", "pdoexception", "pdostatement",
        "exception", "error", "throwable", "typeerror",
        "datetime", "datetimeimmutable", "datetimezone", "dateinterval",
        "dateperiod",
        "arrayobject", "arrayiterator", "recursivearrayiterator",
        "closure", "generator", "fiber",
        "stdclass", "splfileobject", "splfileinfo", "directoryiterator",
        "recursivedirectoryiterator", "filesystemiterator",
        "reflectionclass", "reflectionmethod", "reflectionfunction",
        "reflectionproperty", "reflectionparameter",
        "domdocument", "domxpath", "domelement", "domattr",
        "simplexmlelement", "xmlreader", "xmlwriter",
        "soapclient", "soapserver", "soapfault",
        "phar", "phardata", "pharfileinfo",
        "mysqli", "sqlite3", "sqlite3result", "sqlite3stmt",
        "json", "jsonserializable",
        "iterator", "iteratoraggregate", "countable",
        "arrayaccess", "serializable",
        "streamwrapper", "seekableiterator",
        "recursiveiterator", "outeriterator",
        "filteriterator", "callbackfilteriterator",
        "limititerator", "infiniteiterator",
        "appenditerator", "multipleiterator",
        "cachingiterator", "regexiterator",
        "weakmap", "weakreference",
        "sensitiveparametervalue",
        "stringable",
        "unitenum", "backedenum",
        "random\\randomizer", "random\\engine",
        "random\\engine\\secure", "random\\engine\\mt19937",
        "random\\engine\\pcgoneseq128xslrr64",
        "random\\engine\\xoshiro256starstar",
    ];

    // Common PHP framework / third-party prefixes
    let third_party_prefixes: &[&str] = &[
        "illuminate\\", "laravel\\",
        "symfony\\", "doctrine\\",
        "monolog\\", "phpunit\\",
        "mockery\\", "psr\\",
        "league\\", "spatie\\",
        "guzzlehttp\\", "carbon\\",
        "phpseclib\\", "swiftmailer\\",
        "twig\\", "slim\\",
        "yii\\", "cakephp\\",
        "zend\\", "laminas\\",
        "wordpress\\", "drupal\\",
        "joomla\\", "magento\\",
        "shopware\\", "composer\\",
        "ramsey\\", "vlucas\\",
        "firebase\\", "aws\\",
        "google\\cloud\\", "stripe\\",
        "predis\\", "elasticsearch\\",
        "react\\", "amphp\\",
    ];

    for prefix in third_party_prefixes {
        if lower.starts_with(prefix) {
            return true;
        }
    }

    // Direct class name match
    let root = lower.split('\\').next().unwrap_or(&lower);
    let lower_str: &str = &lower;
    if builtin_classes.contains(&root) || builtin_classes.contains(&lower_str) {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Ruby module resolver
// ---------------------------------------------------------------------------

/// Ruby module resolver.
///
/// Handles Ruby's module system:
/// - `require 'foo'` → loads `foo.rb` from $LOAD_PATH / common source roots
/// - `require_relative 'foo'` → loads relative to source file
/// - `include Foo` / `extend Foo` → constant reference (resolved as symbol)
///
/// ModuleIndex maps .rb files to module names based on file paths.
/// Source roots: `lib/`, `src/`, `app/`, `./`
pub struct RubyResolver;

impl ModuleResolver for RubyResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        // 1. Direct ModuleIndex lookup
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["lib/", "src/", "app/", ""] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Generate candidate paths based on module_name
        // `require 'foo'` → candidate: foo.rb
        // `require 'foo/bar'` → candidate: foo/bar.rb
        let mut candidates = Vec::new();

        for prefix in &["lib/", "src/", "app/", ""] {
            let file_candidate = format!("{}{}.rb", prefix, module_name);
            candidates.push(file_candidate);
        }

        // 4. For require_relative (path starts with .), resolve against source file
        if module_name.starts_with('.') {
            let source_dir = Path::new(source_file)
                .parent()
                .and_then(|p| p.to_str())
                .unwrap_or(".");

            let resolved = if source_dir == "." {
                module_name.to_string()
            } else {
                Path::new(source_dir)
                    .join(module_name)
                    .to_string_lossy()
                    .replace('\\', "/")
            };

            let normalized = simplify_ruby_path_resolver(&resolved);
            let rb_candidate = format!("{}.rb", normalized);
            // Try ModuleIndex for this specific path
            let stripped = strip_ruby_source_root(&normalized);
            if let Some(files) = module_index.lookup(&stripped) {
                candidates.extend(files.clone());
            }
            candidates.push(rb_candidate);
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "ruby")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_ruby_external(module_name)
    }

    fn language(&self) -> &'static str {
        "ruby"
    }
}

/// Check if a module name is a known Ruby standard library or common gem.
///
/// Ruby stdlib modules and popular gems are classified as external.
/// Internal project paths (starting with project directories) are NOT external.
pub fn is_ruby_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Relative paths are always local
    if module_name.starts_with('.') {
        return false;
    }

    let root = module_name.split('/').next().unwrap_or(module_name);

    // Ruby standard library (core + stdlib)
    let ruby_stdlib: &[&str] = &[
        // Core libraries
        "abbrev", "base64", "benchmark", "bigdecimal", "bundler",
        "cgi", "cmath", "csv", "date", "dbm", "debug", "delegate",
        "digest", "drb", "english", "erb", "etc", "fcntl", "fiddle",
        "fileutils", "find", "forwardable", "getoptlong", "io",
        "ipaddr", "irb", "json", "logger", "matrix", "mkmf", "monitor",
        "mutex_m", "net", "nkf", "objspace", "observer", "open-uri",
        "open3", "openssl", "optparse", "ostruct", "pathname", "pp",
        "prettyprint", "prime", "pstore", "psych", "racc", "rbconfig",
        "rdoc", "readline", "reline", "resolv", "resolv-replace",
        "rinda", "ripper", "rss", "rubygems", "securerandom", "set",
        "shellwords", "singleton", "socker", "stringio", "strscan",
        "syslog", "tempfile", "time", "timeout", "tmpdir", "tracer",
        "tsort", "un", "uri", "weakref", "yaml", "zlib",
    ];

    if ruby_stdlib.contains(&root) {
        return true;
    }

    // Common third-party gems
    let third_party_gems: &[&str] = &[
        "rails", "activerecord", "activesupport", "actionpack",
        "actionmailer", "actionview", "activejob", "actioncable",
        "activestorage", "actiontext", "actionmailbox",
        "sinatra", "rack", "puma", "unicorn", "thin", "webrick",
        "rspec", "minitest", "capybara", "factory_bot", "faker",
        "devise", "cancancan", "pundit", "omniauth",
        "sidekiq", "resque", "delayed_job",
        "nokogiri", "httparty", "faraday", "rest-client",
        "pg", "mysql2", "sqlite3", "redis", "mongo",
        "graphql", "grpc",
        "dotenv", "pry", "byebug",
        "rubocop", "reek", "flog", "simplecov",
        "sass", "webpacker", "sprockets",
        "bcrypt", "jwt",
        "aws-sdk", "google-cloud",
        "stripe", "twilio-ruby", "sendgrid-ruby",
    ];

    if third_party_gems.contains(&root) {
        return true;
    }

    // Known gem paths (e.g. active_support/core_ext/object)
    // Check if the root with underscores maps to a known gem (canonical name w/o underscores)
    // Also check hyphenated form (rest-client → rest_client gem match)
    let root_underscore = root.replace('-', "_");
    let root_no_underscore = root.replace('_', "");
    if third_party_gems.contains(&root_underscore.as_str()) {
        return true;
    }
    if third_party_gems.contains(&root_no_underscore.as_str()) {
        return true;
    }

    false
}

/// Simplify a Ruby-style path by resolving `.` and `..` segments.
fn simplify_ruby_path_resolver(path: &str) -> String {
    let parts: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    let mut result: Vec<&str> = Vec::new();

    for part in parts {
        match part {
            "." => {}
            ".." => {
                if !result.is_empty() && result.last() != Some(&"..") {
                    result.pop();
                } else {
                    result.push(part);
                }
            }
            _ => result.push(part),
        }
    }
    result.join("/")
}

/// Strip common Ruby source root prefixes.
fn strip_ruby_source_root(path: &str) -> String {
    let prefixes = &["lib/", "src/", "app/"];
    for prefix in prefixes {
        if path.starts_with(prefix) {
            return path[prefix.len()..].to_string();
        }
    }
    path.to_string()
}

// ---------------------------------------------------------------------------
// C / C++ module resolver
// ---------------------------------------------------------------------------

/// C and C++ shared module resolver.
///
/// Handles the `#include` preprocessor directive used by both C and C++:
/// - `#include "foo.h"` → project-local header, resolved via ModuleIndex or
///   by searching the source file's directory.
/// - `#include <foo.h>` / `#include <vector>` → system/standard library headers,
///   classified as external.
///
/// Module names for C/C++ are header filenames (e.g. `"foo.h"`, `"bar.hpp"`).
/// The ModuleIndex maps file paths to their basenames for lookup.
pub struct CppResolver;

impl ModuleResolver for CppResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        _project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        let mut candidates = Vec::new();

        // 1. Direct ModuleIndex lookup by header name
        if let Some(files) = module_index.lookup(module_name) {
            candidates.extend(files.clone());
        }

        // 2. Also try without extension (e.g. "foo" → "foo.h")
        if let Some(dot_pos) = module_name.rfind('.') {
            let base = &module_name[..dot_pos];
            if let Some(files) = module_index.lookup(base) {
                candidates.extend(files.clone());
            }
        }

        // 3. Search in source file's directory
        let source_dir = Path::new(source_file)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".");

        let source_candidate = if source_dir == "." {
            module_name.to_string()
        } else {
            Path::new(source_dir)
                .join(module_name)
                .to_string_lossy()
                .replace('\\', "/")
        };

        // Check if the candidate path exists in ModuleIndex
        let check_path = &source_candidate;
        // Look up by file path (module name might be the full relative path)
        if module_index.rev_lookup(check_path).is_some() {
            if !candidates.contains(check_path) {
                candidates.push(check_path.to_string());
            }
        }

        // Also try the filename-only lookup in ModuleIndex
        let basename = Path::new(&source_candidate)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("");

        if basename != module_name {
            if let Some(files) = module_index.lookup(basename) {
                for f in files {
                    if !candidates.contains(f) {
                        candidates.push(f.clone());
                    }
                }
            }
        }

        // 4. Try common prefix variations
        for prefix in &["src/", "lib/", "include/", "inc/", ""] {
            let with_prefix = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&with_prefix) {
                for f in files {
                    if !candidates.contains(f) {
                        candidates.push(f.clone());
                    }
                }
            }
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "c")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_cpp_external(module_name)
    }

    fn language(&self) -> &'static str {
        "c"
    }
}

/// Check if a header/module name is a known C/C++ standard library header or
/// a common third-party library.
///
/// A header is considered external if it matches:
/// - C standard library headers (stdio.h, stdlib.h, string.h, etc.)
/// - C++ standard library headers (iostream, vector, string, algorithm, etc.)
/// - C++ C-compatibility headers (cstdio, cstdlib, cstring, etc.)
/// - Common third-party libraries (opencv2, boost, Qt, etc.)
///
/// Project-local headers (`#include "..."` ) are not considered external
/// unless they match one of the known stdlib patterns above.
pub fn is_cpp_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // C standard library headers (with .h extension)
    let c_stdlib: &[&str] = &[
        "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
        "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h",
        "setjmp.h", "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h",
        "stdbool.h", "stddef.h", "stdint.h", "stdio.h", "stdlib.h",
        "stdnoreturn.h", "string.h", "tgmath.h", "threads.h", "time.h",
        "uchar.h", "wchar.h", "wctype.h",
    ];

    if c_stdlib.contains(&module_name) {
        return true;
    }

    // C++ standard library headers (without .h extension)
    let cpp_stdlib: &[&str] = &[
        "algorithm", "array", "atomic", "bitset", "chrono", "codecvt",
        "complex", "condition_variable", "deque", "exception", "execution",
        "filesystem", "forward_list", "fstream", "functional", "future",
        "initializer_list", "iomanip", "ios", "iosfwd", "iostream", "istream",
        "iterator", "limits", "list", "locale", "map", "memory", "memory_resource",
        "mutex", "new", "numbers", "numeric", "optional", "ostream", "queue",
        "random", "ranges", "ratio", "regex", "scoped_allocator", "set",
        "shared_mutex", "source_location", "span", "sstream", "stack",
        "stdexcept", "stop_token", "streambuf", "string", "string_view",
        "strstream", "syncstream", "system_error", "thread", "tuple",
        "type_traits", "typeindex", "typeinfo", "unordered_map",
        "unordered_set", "utility", "valarray", "variant", "vector", "version",
    ];

    if cpp_stdlib.contains(&module_name) {
        return true;
    }

    // C++ C-compatibility headers (c*)
    let c_stdlib_no_h: &[&str] = &[
        "cassert", "cctype", "cerrno", "cfenv", "cfloat", "cinttypes",
        "climits", "clocale", "cmath", "csetjmp", "csignal", "cstdarg",
        "cstdbool", "cstddef", "cstdint", "cstdio", "cstdlib", "cstring",
        "ctgmath", "ctime", "cuchar", "cwchar", "cwctype",
    ];

    if c_stdlib_no_h.contains(&module_name) {
        return true;
    }

    // Common third-party C/C++ libraries (by prefix or exact match)
    let third_party_prefixes: &[&str] = &[
        "boost/", "boost/",
        "opencv2/", "opencv/",
        "QtCore/", "QtGui/", "QtWidgets/", "Qt/",
        "glib/", "glib-2.0/",
        "gtk/", "gtk-3.0/",
        "cairo/",
        "pango/",
        "curl/",
        "openssl/",
        "zlib.h", "zconf.h",
        "png.h", "pngconf.h", "pnglibconf.h",
        "jpeglib.h", "jerror.h", "jmorecfg.h",
        "tiff.h", "tiffio.h", "tiffconf.h",
        "expat.h", "expat_external.h",
        "pcre.h", "pcre2.h",
        "sqlite3.h",
        "mysql.h", "mysqld_error.h",
        "libpq-fe.h",
        "mongo.h",
        "hdf5.h",
        "netcdf.h",
        "fftw3.h",
        "gmp.h",
        "mpfr.h",
        "mpi.h",
        "cuda.h", "cuda_runtime.h",
        "opencl.h",
        "CL/cl.h", "CL/cl2.h",
        "vulkan/vulkan.h",
        "GL/gl.h", "GL/glu.h", "GL/glew.h", "GL/glut.h",
        "SDL.h", "SDL2/SDL.h",
        "X11/Xlib.h", "X11/X.h",
        "pthread.h", "unistd.h", "fcntl.h", "sys/stat.h",
        "sys/types.h", "sys/socket.h", "netinet/in.h",
        "arpa/inet.h", "dlfcn.h", "dirent.h",
        "windows.h", "winsock2.h", "ws2tcpip.h",
        "objc/NSObject.h", "objc/runtime.h",
        "Foundation/Foundation.h", "UIKit/UIKit.h",
        "AppKit/AppKit.h", "CoreFoundation/CoreFoundation.h",
    ];

    // Check prefixes
    for prefix in third_party_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // Check for dotted paths that might be system includes
    // (e.g., sys/stat.h, netinet/in.h)
    if module_name.contains('/') {
        let root = module_name.split('/').next().unwrap_or(module_name);
        let system_roots: &[&str] = &[
            "sys", "netinet", "arpa", "net", "bits", "asm", "linux",
            "machine", "rpc", "rpcsvc", "nfs", "scsi", "sound",
            "video", "protocols", "uapi",
        ];
        if system_roots.contains(&root) {
            return true;
        }
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn test_python_resolver_is_external_stdlib() {
        let resolver = PythonResolver;
        assert!(resolver.is_external("os"));
        assert!(resolver.is_external("sys"));
        assert!(resolver.is_external("collections.abc"));
        assert!(resolver.is_external("json"));
    }

    #[test]
    fn test_python_resolver_is_external_third_party() {
        let resolver = PythonResolver;
        assert!(resolver.is_external("fastapi"));
        assert!(resolver.is_external("numpy"));
        assert!(resolver.is_external("django.db.models"));
    }

    #[test]
    fn test_python_resolver_not_external_project_module() {
        let resolver = PythonResolver;
        assert!(!resolver.is_external("myproject.utils"));
        assert!(!resolver.is_external("src.main"));
        assert!(!resolver.is_external("app.core"));
    }

    #[test]
    fn test_python_resolver_language() {
        let resolver = PythonResolver;
        assert_eq!(resolver.language(), "python");
    }

    #[test]
    fn test_python_resolver_file_to_module_name() {
        let resolver = PythonResolver;
        assert_eq!(
            resolver.file_to_module_name("src/pkg/module.py", Path::new(".")),
            Some("pkg.module".to_string())
        );
    }

    #[test]
    fn test_language_registry_get_python() {
        let r = LanguageRegistry::get("python");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "python");
    }

    #[test]
    fn test_language_registry_get_typescript() {
        let r = LanguageRegistry::get("typescript");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "typescript");
    }

    #[test]
    fn test_language_registry_get_javascript() {
        let r = LanguageRegistry::get("javascript");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "typescript");
    }

    #[test]
    fn test_language_registry_get_unsupported() {
        let r = LanguageRegistry::get("haskell");
        assert!(r.is_none());
    }

    #[test]
    fn test_supported_languages() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"python"));
        assert!(langs.contains(&"typescript"));
        assert!(langs.contains(&"javascript"));
    }

    // ------------------------------------------------------------------
    // TypeScript resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_ts_resolver_is_external_relative() {
        let resolver = TypeScriptResolver;
        assert!(!resolver.is_external("./utils"));
        assert!(!resolver.is_external("../sibling"));
    }

    #[test]
    fn test_ts_resolver_is_external_npm_packages() {
        let resolver = TypeScriptResolver;
        assert!(resolver.is_external("react"));
        assert!(resolver.is_external("lodash"));
        assert!(resolver.is_external("express"));
        assert!(resolver.is_external("axios"));
    }

    #[test]
    fn test_ts_resolver_is_external_node_builtins() {
        let resolver = TypeScriptResolver;
        assert!(resolver.is_external("fs"));
        assert!(resolver.is_external("path"));
        assert!(resolver.is_external("crypto"));
    }

    #[test]
    fn test_ts_resolver_is_external_unknown_bare_specifier() {
        let resolver = TypeScriptResolver;
        // Unknown bare specifiers are treated as external (npm packages)
        assert!(resolver.is_external("some-random-npm-pkg"));
    }

    #[test]
    fn test_ts_resolver_language() {
        let resolver = TypeScriptResolver;
        assert_eq!(resolver.language(), "typescript");
    }

    #[test]
    fn test_ts_resolver_resolve_relative_module() {
        let resolver = TypeScriptResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "./utils",
            "src/components/Button.tsx",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        // Should include: src/components/utils.ts, .tsx, .js, .jsx, /index.ts, etc.
        assert!(candidates.iter().any(|c| c == "src/components/utils.ts"));
        assert!(candidates.iter().any(|c| c == "src/components/utils/index.ts"));
    }

    #[test]
    fn test_ts_resolver_resolve_parent_import() {
        let resolver = TypeScriptResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "../shared/helpers",
            "src/components/Button.tsx",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        assert!(candidates.iter().any(|c| c == "src/shared/helpers.ts"));
    }

    #[test]
    fn test_ts_resolver_resolve_bare_specifier_empty_no_index() {
        let resolver = TypeScriptResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "myproject",
            "src/index.ts",
            Path::new("."),
            &index,
        );
        // No ModuleIndex entry and bare specifier → returns empty
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_simplify_ts_path() {
        assert_eq!(simplify_ts_path("a/b/c"), "a/b/c");
        assert_eq!(simplify_ts_path("a/./b"), "a/b");
        assert_eq!(simplify_ts_path("a/b/../c"), "a/c");
        assert_eq!(simplify_ts_path("./a"), "a");
    }

    #[test]
    fn test_find_ts_module_files() {
        let files = find_ts_module_files("src/utils");
        assert!(files.contains(&"src/utils.ts".to_string()));
        assert!(files.contains(&"src/utils/index.ts".to_string()));
        // Should be ordered with direct files first, then index files
        assert_eq!(files[0], "src/utils.ts");
    }

    // ------------------------------------------------------------------
    // Java resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_java_resolver_is_external_stdlib() {
        let resolver = JavaResolver;
        assert!(resolver.is_external("java.util.List"));
        assert!(resolver.is_external("javax.servlet.http.HttpServlet"));
        assert!(resolver.is_external("com.sun.management"));
    }

    #[test]
    fn test_java_resolver_is_external_third_party() {
        let resolver = JavaResolver;
        assert!(resolver.is_external("org.springframework.boot.Application"));
        assert!(resolver.is_external("com.google.common.collect.Lists"));
        assert!(resolver.is_external("org.apache.commons.lang3.StringUtils"));
        assert!(resolver.is_external("org.junit.jupiter.api.Test"));
    }

    #[test]
    fn test_java_resolver_not_external_project_package() {
        let resolver = JavaResolver;
        assert!(!resolver.is_external("com.mycompany.myproject.MyClass"));
        assert!(!resolver.is_external("myapp.utils.Helper"));
        assert!(!resolver.is_external("com.example.internal.Module"));
    }

    #[test]
    fn test_java_resolver_language() {
        let resolver = JavaResolver;
        assert_eq!(resolver.language(), "java");
    }

    #[test]
    fn test_java_resolver_file_to_module_name() {
        let resolver = JavaResolver;
        assert_eq!(
            resolver.file_to_module_name(
                "src/main/java/com/foo/bar/MyClass.java",
                Path::new(".")
            ),
            Some("com.foo.bar.MyClass".to_string())
        );
    }

    #[test]
    fn test_java_resolver_resolve_module_with_class() {
        let resolver = JavaResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "com.foo.bar.MyClass",
            "src/main/java/com/foo/bar/MyClass.java",
            Path::new("."),
            &index,
        );
        // Should return candidate file paths
        assert!(!candidates.is_empty());
        assert!(candidates.contains(
            &"src/main/java/com/foo/bar/MyClass.java".to_string()
        ));
    }

    #[test]
    fn test_language_registry_get_java() {
        let r = LanguageRegistry::get("java");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "java");
    }

    #[test]
    fn test_supported_languages_includes_java() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"java"));
    }

    #[test]
    fn test_java_resolver_empty_module_name() {
        let resolver = JavaResolver;
        assert!(!resolver.is_external(""));
    }

    // ------------------------------------------------------------------
    // Kotlin resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_kotlin_resolver_is_external_stdlib() {
        let resolver = KotlinResolver;
        assert!(resolver.is_external("kotlin.collections.List"));
        assert!(resolver.is_external("kotlinx.coroutines.flow.Flow"));
        assert!(resolver.is_external("java.util.ArrayList"));
        assert!(resolver.is_external("javax.servlet.http.HttpServlet"));
    }

    #[test]
    fn test_kotlin_resolver_is_external_third_party() {
        let resolver = KotlinResolver;
        assert!(resolver.is_external("org.springframework.boot.Application"));
        assert!(resolver.is_external("com.google.common.collect.Lists"));
        assert!(resolver.is_external("org.jetbrains.kotlin.idea"));
    }

    #[test]
    fn test_kotlin_resolver_is_external_android() {
        let resolver = KotlinResolver;
        assert!(resolver.is_external("android.widget.TextView"));
        assert!(resolver.is_external("androidx.compose.ui.Modifier"));
    }

    #[test]
    fn test_kotlin_resolver_not_external_project_package() {
        let resolver = KotlinResolver;
        assert!(!resolver.is_external("com.mycompany.myapp.MyClass"));
        assert!(!resolver.is_external("myapp.utils.Helper"));
        assert!(!resolver.is_external("com.example.internal.Module"));
    }

    #[test]
    fn test_kotlin_resolver_language() {
        let resolver = KotlinResolver;
        assert_eq!(resolver.language(), "kotlin");
    }

    #[test]
    fn test_kotlin_resolver_file_to_module_name() {
        let resolver = KotlinResolver;
        assert_eq!(
            resolver.file_to_module_name(
                "src/main/kotlin/com/foo/bar/MyClass.kt",
                Path::new(".")
            ),
            Some("com.foo.bar.MyClass".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name(
                "src/main/java/com/foo/bar/MyClass.kt",
                Path::new(".")
            ),
            Some("com.foo.bar.MyClass".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name(
                "src/com/example/Utils.kt",
                Path::new(".")
            ),
            Some("com.example.Utils".to_string())
        );
    }

    #[test]
    fn test_kotlin_resolver_resolve_module_with_class() {
        let resolver = KotlinResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "com.foo.bar.MyClass",
            "src/main/kotlin/com/foo/bar/MyClass.kt",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        assert!(candidates.contains(
            &"src/main/kotlin/com/foo/bar/MyClass.kt".to_string()
        ));
    }

    #[test]
    fn test_language_registry_get_kotlin() {
        let r = LanguageRegistry::get("kotlin");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "kotlin");
    }

    #[test]
    fn test_supported_languages_includes_kotlin() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"kotlin"));
    }

    #[test]
    fn test_kotlin_resolver_empty_module_name() {
        let resolver = KotlinResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_is_kotlin_external_stdlib_prefixes() {
        assert!(is_kotlin_external("kotlin.io.path.ExperimentalPathApi"));
        assert!(is_kotlin_external("kotlinx.serialization.Serializable"));
        assert!(is_kotlin_external("android.os.Bundle"));
        assert!(is_kotlin_external("androidx.lifecycle.ViewModel"));
    }

    // ------------------------------------------------------------------
    // Go resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_go_resolver_is_external_stdlib() {
        let resolver = GoResolver;
        assert!(resolver.is_external("fmt"));
        assert!(resolver.is_external("os"));
        assert!(resolver.is_external("net/http"));
        assert!(resolver.is_external("encoding/json"));
        assert!(resolver.is_external("crypto/tls"));
        assert!(resolver.is_external("strings"));
        assert!(resolver.is_external("io/ioutil"));
    }

    #[test]
    fn test_go_resolver_is_external_github() {
        let resolver = GoResolver;
        assert!(resolver.is_external("github.com/gin-gonic/gin"));
        assert!(resolver.is_external("github.com/gorilla/mux"));
        assert!(resolver.is_external("github.com/stretchr/testify"));
        assert!(resolver.is_external("github.com/sirupsen/logrus"));
    }

    #[test]
    fn test_go_resolver_is_external_golang_org() {
        let resolver = GoResolver;
        assert!(resolver.is_external("golang.org/x/net"));
        assert!(resolver.is_external("golang.org/x/tools"));
        assert!(resolver.is_external("google.golang.org/grpc"));
        assert!(resolver.is_external("go.uber.org/zap"));
    }

    #[test]
    fn test_go_resolver_is_external_other_hosts() {
        let resolver = GoResolver;
        assert!(resolver.is_external("gitlab.com/user/project"));
        assert!(resolver.is_external("k8s.io/client-go"));
        assert!(resolver.is_external("gopkg.in/yaml.v2"));
    }

    #[test]
    fn test_go_resolver_not_external_project_package() {
        let resolver = GoResolver;
        // Project-internal bare module names should not be external
        assert!(!resolver.is_external("mymodule/user"));
        assert!(!resolver.is_external("myapp/pkg/utils"));
        assert!(!resolver.is_external("internal/config"));
        assert!(!resolver.is_external("user"));
        assert!(!resolver.is_external("handler"));
    }

    #[test]
    fn test_go_resolver_empty_module_name() {
        let resolver = GoResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_go_resolver_language() {
        let resolver = GoResolver;
        assert_eq!(resolver.language(), "go");
    }

    #[test]
    fn test_go_resolver_file_to_module_name() {
        let resolver = GoResolver;
        // Go uses parent directory as module name
        assert_eq!(
            resolver.file_to_module_name("pkg/user/server.go", Path::new(".")),
            Some("user".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("internal/config/app.go", Path::new(".")),
            Some("config".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("main.go", Path::new(".")),
            Some("main".to_string())
        );
        // Non-Go files return None
        assert_eq!(
            resolver.file_to_module_name("pkg/user/server_test.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_go_resolver_resolve_module_directory_lookup() {
        let resolver = GoResolver;
        let index = ModuleIndex::empty();
        // We need to manually populate the index since empty() won't have any entries
        // For testing, create a temporary DB
        // Since ModuleIndex::empty() starts truly empty, test the path resolution logic
        let candidates = resolver.resolve_module(
            "user",
            "pkg/user/server.go",
            Path::new("."),
            &index,
        );
        // Empty index should return empty candidates
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_go_resolver_resolve_module_with_full_path() {
        let resolver = GoResolver;
        let index = ModuleIndex::empty();
        // "mymodule/user" → last segment "user" → empty index → empty result
        let candidates = resolver.resolve_module(
            "mymodule/user",
            "cmd/server/main.go",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_go() {
        let r = LanguageRegistry::get("go");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "go");
    }

    #[test]
    fn test_supported_languages_includes_go() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"go"));
    }

    #[test]
    fn test_is_go_external_stdlib_subpackage() {
        assert!(is_go_external("net/http"));
        assert!(is_go_external("crypto/tls"));
        assert!(is_go_external("encoding/json"));
        assert!(is_go_external("io/fs"));
        // Not stdlib
        assert!(!is_go_external("mypkg/subpkg"));
    }

    #[test]
    fn test_is_go_external_stdlib_direct() {
        assert!(is_go_external("fmt"));
        assert!(is_go_external("os"));
        assert!(is_go_external("strings"));
        assert!(is_go_external("sync"));
    }

    // ------------------------------------------------------------------
    // Rust resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_rust_resolver_is_external_stdlib() {
        let resolver = RustResolver;
        assert!(resolver.is_external("std::collections::HashMap"));
        assert!(resolver.is_external("core::fmt"));
        assert!(resolver.is_external("alloc::boxed::Box"));
        assert!(resolver.is_external("std"));
        assert!(resolver.is_external("proc_macro"));
    }

    #[test]
    fn test_rust_resolver_is_external_third_party() {
        let resolver = RustResolver;
        assert!(resolver.is_external("serde::Serialize"));
        assert!(resolver.is_external("serde_json::Value"));
        assert!(resolver.is_external("tokio::runtime::Runtime"));
        assert!(resolver.is_external("reqwest::Client"));
        assert!(resolver.is_external("actix_web::App"));
        assert!(resolver.is_external("axum::Router"));
        assert!(resolver.is_external("clap::Parser"));
        assert!(resolver.is_external("anyhow::Result"));
        assert!(resolver.is_external("log::info"));
    }

    #[test]
    fn test_rust_resolver_not_external_crate_paths() {
        let resolver = RustResolver;
        // crate::, super::, self:: are always internal
        assert!(!resolver.is_external("crate::foo::bar"));
        assert!(!resolver.is_external("crate::models::User"));
        assert!(!resolver.is_external("super::utils"));
        assert!(!resolver.is_external("self::inner"));
    }

    #[test]
    fn test_rust_resolver_not_external_project_modules() {
        let resolver = RustResolver;
        // Bare project-internal module paths
        assert!(!resolver.is_external("foo::bar::Baz"));
        assert!(!resolver.is_external("models::User"));
        assert!(!resolver.is_external("utils::helpers"));
        assert!(!resolver.is_external("my_module"));
    }

    #[test]
    fn test_rust_resolver_empty_module_name() {
        let resolver = RustResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_rust_resolver_language() {
        let resolver = RustResolver;
        assert_eq!(resolver.language(), "rust");
    }

    #[test]
    fn test_rust_resolver_file_to_module_name() {
        let resolver = RustResolver;
        assert_eq!(
            resolver.file_to_module_name("src/foo/bar.rs", Path::new(".")),
            Some("foo::bar".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/models/user.rs", Path::new(".")),
            Some("models::user".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/lib.rs", Path::new(".")),
            Some("".to_string())
        );
        // Non-Rust files return None
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_rust_resolver_resolve_crate_module() {
        let resolver = RustResolver;
        let index = ModuleIndex::empty();
        // parse_target_text("foo::bar::Baz") splits on last :: → module="foo::bar", symbol="Baz"
        let candidates = resolver.resolve_module(
            "crate::foo::bar",
            "src/main.rs",
            Path::new("."),
            &index,
        );
        // Should return candidate file paths
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&"foo/bar.rs".to_string()));
        assert!(candidates.contains(&"foo/bar/mod.rs".to_string()));
    }

    #[test]
    fn test_rust_resolver_resolve_crate_with_symbol_in_path() {
        let resolver = RustResolver;
        let index = ModuleIndex::empty();
        // When no parse_target_text step, full path with symbol
        let candidates = resolver.resolve_module(
            "crate::foo::bar::Baz",
            "src/main.rs",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        // The resolver doesn't know "Baz" is the symbol — it treats the full path as module
        assert!(candidates.contains(&"foo/bar/Baz.rs".to_string()));
    }

    #[test]
    fn test_rust_resolver_resolve_super_module() {
        let resolver = RustResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "super::sibling",
            "src/foo/bar.rs",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        // super from src/foo/bar.rs → src/foo/ sibling (1 level up)
        assert!(candidates.contains(&"src/foo/sibling.rs".to_string()));
    }

    #[test]
    fn test_rust_resolver_resolve_self_module() {
        let resolver = RustResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "self::inner",
            "src/foo/bar.rs",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        // self from src/foo/bar.rs → same directory
        assert!(candidates.contains(&"src/foo/inner.rs".to_string()));
    }

    #[test]
    fn test_rust_resolver_resolve_empty_module_name() {
        let resolver = RustResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "src/main.rs",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_rust() {
        let r = LanguageRegistry::get("rust");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "rust");
    }

    #[test]
    fn test_supported_languages_includes_rust() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"rust"));
    }

    #[test]
    fn test_is_rust_external_common_crates() {
        assert!(is_rust_external("serde"));
        assert!(is_rust_external("tokio::sync::Mutex"));
        assert!(is_rust_external("rayon::prelude::*"));
        assert!(is_rust_external("regex::Regex"));
        assert!(is_rust_external("chrono::Utc"));
        assert!(is_rust_external("diesel::prelude::*"));
        assert!(is_rust_external("openssl::ssl"));
        assert!(is_rust_external("rand::Rng"));
    }

    #[test]
    fn test_is_rust_external_local_paths() {
        assert!(!is_rust_external("crate::foo::bar"));
        assert!(!is_rust_external("super::utils"));
        assert!(!is_rust_external("self::helper"));
        assert!(!is_rust_external("my_crate::models"));
    }

    // ------------------------------------------------------------------
    // PHP resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_php_resolver_is_external_builtin() {
        let resolver = PhpResolver;
        assert!(resolver.is_external("PDO"));
        assert!(resolver.is_external("Exception"));
        assert!(resolver.is_external("DateTime"));
        assert!(resolver.is_external("ArrayObject"));
        assert!(resolver.is_external("Closure"));
        assert!(resolver.is_external("stdClass"));
    }

    #[test]
    fn test_php_resolver_is_external_third_party() {
        let resolver = PhpResolver;
        assert!(resolver.is_external("Illuminate\\Database\\Eloquent\\Model"));
        assert!(resolver.is_external("Symfony\\Component\\HttpFoundation\\Request"));
        assert!(resolver.is_external("Doctrine\\ORM\\EntityManager"));
        assert!(resolver.is_external("Monolog\\Logger"));
        assert!(resolver.is_external("GuzzleHttp\\Client"));
    }

    #[test]
    fn test_php_resolver_is_external_psr() {
        let resolver = PhpResolver;
        assert!(resolver.is_external("Psr\\Log\\LoggerInterface"));
        assert!(resolver.is_external("Psr\\Http\\Message\\RequestInterface"));
    }

    #[test]
    fn test_php_resolver_not_external_project() {
        let resolver = PhpResolver;
        assert!(!resolver.is_external("App\\Services\\UserService"));
        assert!(!resolver.is_external("MyApp\\Models\\User"));
        assert!(!resolver.is_external("Foo\\Bar\\Baz"));
    }

    #[test]
    fn test_php_resolver_empty_module_name() {
        let resolver = PhpResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_php_resolver_language() {
        let resolver = PhpResolver;
        assert_eq!(resolver.language(), "php");
    }

    #[test]
    fn test_php_resolver_file_to_module_name() {
        let resolver = PhpResolver;
        assert_eq!(
            resolver.file_to_module_name("src/Foo/Bar/Baz.php", Path::new(".")),
            Some("Foo\\Bar\\Baz".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("app/Models/User.php", Path::new(".")),
            Some("Models\\User".to_string())
        );
        // Non-PHP files return None
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_php_resolver_resolve_module_empty_index() {
        let resolver = PhpResolver;
        let index = ModuleIndex::empty();
        // Empty index should still return candidate file paths
        let candidates = resolver.resolve_module(
            "Foo\\Bar\\Baz",
            "src/test.php",
            Path::new("."),
            &index,
        );
        // Should return candidate paths based on PSR-4 conventions
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&"src/Foo/Bar/Baz.php".to_string()));
        assert!(candidates.contains(&"lib/Foo/Bar/Baz.php".to_string()));
    }

    #[test]
    fn test_php_resolver_resolve_module_with_index() {
        let resolver = PhpResolver;
        let index = ModuleIndex::empty();
        // ModuleIndex::empty() returns an empty index, so test the default
        // path generation without any index data
        let candidates = resolver.resolve_module(
            "App\\Services\\UserService",
            "src/test.php",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&"src/App/Services/UserService.php".to_string()));
    }

    #[test]
    fn test_php_resolver_resolve_empty_module() {
        let resolver = PhpResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "src/test.php",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_php() {
        let r = LanguageRegistry::get("php");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "php");
    }

    #[test]
    fn test_supported_languages_includes_php() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"php"));
    }

    #[test]
    fn test_is_php_external_lowercase() {
        // is_php_external is case-insensitive
        assert!(is_php_external("pdo"));
        assert!(is_php_external("datetime"));
        assert!(is_php_external("illuminate\\support\\facades\\auth"));
    }

    #[test]
    fn test_is_php_external_project_paths() {
        assert!(!is_php_external("App\\Controllers\\HomeController"));
        assert!(!is_php_external("src\\utils\\helpers"));
        assert!(!is_php_external("MyProject\\Domain\\Entity"));
    }

    // ------------------------------------------------------------------
    // Ruby resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_ruby_resolver_is_external_stdlib() {
        let resolver = RubyResolver;
        assert!(resolver.is_external("json"));
        assert!(resolver.is_external("csv"));
        assert!(resolver.is_external("yaml"));
        assert!(resolver.is_external("net/http"));
        assert!(resolver.is_external("uri"));
        assert!(resolver.is_external("set"));
        assert!(resolver.is_external("logger"));
        assert!(resolver.is_external("openssl"));
        assert!(resolver.is_external("tempfile"));
    }

    #[test]
    fn test_ruby_resolver_is_external_third_party() {
        let resolver = RubyResolver;
        assert!(resolver.is_external("rails"));
        assert!(resolver.is_external("activesupport"));
        assert!(resolver.is_external("rspec"));
        assert!(resolver.is_external("devise"));
        assert!(resolver.is_external("nokogiri"));
        assert!(resolver.is_external("sidekiq"));
        assert!(resolver.is_external("pg"));
        assert!(resolver.is_external("redis"));
    }

    #[test]
    fn test_ruby_resolver_is_external_gem_with_dashes() {
        // Gems like 'rest-client' or 'google-cloud' should be recognized
        let resolver = RubyResolver;
        assert!(resolver.is_external("rest-client"));
        // Also check same gem referenced without extension name
        assert!(is_ruby_external("rest-client/request"));
    }

    #[test]
    fn test_ruby_resolver_not_external_project() {
        let resolver = RubyResolver;
        assert!(!resolver.is_external("lib/helper"));
        assert!(!resolver.is_external("src/models/user"));
        assert!(!resolver.is_external("app/services/auth"));
        assert!(!resolver.is_external("myapp/utils"));
    }

    #[test]
    fn test_ruby_resolver_not_external_relative() {
        let resolver = RubyResolver;
        assert!(!resolver.is_external("./utils"));
        assert!(!resolver.is_external("../shared/helper"));
    }

    #[test]
    fn test_ruby_resolver_empty_module_name() {
        let resolver = RubyResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_ruby_resolver_language() {
        let resolver = RubyResolver;
        assert_eq!(resolver.language(), "ruby");
    }

    #[test]
    fn test_ruby_resolver_file_to_module_name() {
        let resolver = RubyResolver;
        assert_eq!(
            resolver.file_to_module_name("lib/foo.rb", Path::new(".")),
            Some("foo".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/models/user.rb", Path::new(".")),
            Some("models/user".to_string())
        );
        // Non-Ruby files return None
        assert_eq!(
            resolver.file_to_module_name("lib/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_ruby_resolver_resolve_module_empty_index() {
        let resolver = RubyResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "helper",
            "src/app.rb",
            Path::new("."),
            &index,
        );
        // Should generate candidate files: lib/helper.rb, src/helper.rb, app/helper.rb, helper.rb
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&"lib/helper.rb".to_string()));
        assert!(candidates.contains(&"src/helper.rb".to_string()));
        assert!(candidates.contains(&"helper.rb".to_string()));
    }

    #[test]
    fn test_ruby_resolver_resolve_module_nested_path() {
        let resolver = RubyResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "foo/bar",
            "src/main.rb",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&"lib/foo/bar.rb".to_string()));
    }

    #[test]
    fn test_ruby_resolver_resolve_module_relative() {
        let resolver = RubyResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "../shared/helper",
            "src/models/user.rb",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());
        // Resolved to src/shared/helper.rb
        assert!(candidates.iter().any(|c| c == "src/shared/helper.rb"));
    }

    #[test]
    fn test_ruby_resolver_resolve_empty_module() {
        let resolver = RubyResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "src/main.rb",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_ruby() {
        let r = LanguageRegistry::get("ruby");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "ruby");
    }

    #[test]
    fn test_supported_languages_includes_ruby() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"ruby"));
    }

    #[test]
    fn test_is_ruby_external_stdlib_variants() {
        assert!(is_ruby_external("json"));
        assert!(is_ruby_external("json/pure"));
        assert!(is_ruby_external("net/http"));
        assert!(is_ruby_external("net/smtp"));
        assert!(is_ruby_external("uri"));
        assert!(is_ruby_external("open-uri"));
        assert!(is_ruby_external("open3"));
    }

    #[test]
    fn test_is_ruby_external_gems_variants() {
        assert!(is_ruby_external("active_support/core_ext/object"));
        assert!(is_ruby_external("active_record/validations"));
        assert!(is_ruby_external("rspec/mocks"));
        assert!(is_ruby_external("devise/strategies"));
    }

    #[test]
    fn test_simplify_ruby_path_resolver() {
        assert_eq!(simplify_ruby_path_resolver("lib/foo/bar"), "lib/foo/bar");
        assert_eq!(simplify_ruby_path_resolver("src/./models"), "src/models");
        assert_eq!(simplify_ruby_path_resolver("src/../lib/helper"), "lib/helper");
        assert_eq!(simplify_ruby_path_resolver("./utils"), "utils");
    }

    #[test]
    fn test_strip_ruby_source_root() {
        assert_eq!(strip_ruby_source_root("lib/foo"), "foo");
        assert_eq!(strip_ruby_source_root("src/models/user"), "models/user");
        assert_eq!(strip_ruby_source_root("app/services"), "services");
        assert_eq!(strip_ruby_source_root("foo"), "foo");
    }

    // ------------------------------------------------------------------
    // C / C++ resolver tests
    // ------------------------------------------------------------------

    #[test]
    fn test_cpp_resolver_is_external_c_stdlib() {
        let resolver = CppResolver;
        assert!(resolver.is_external("stdio.h"));
        assert!(resolver.is_external("stdlib.h"));
        assert!(resolver.is_external("string.h"));
        assert!(resolver.is_external("math.h"));
        assert!(resolver.is_external("time.h"));
    }

    #[test]
    fn test_cpp_resolver_is_external_cpp_stdlib() {
        let resolver = CppResolver;
        assert!(resolver.is_external("iostream"));
        assert!(resolver.is_external("vector"));
        assert!(resolver.is_external("string"));
        assert!(resolver.is_external("map"));
        assert!(resolver.is_external("algorithm"));
        assert!(resolver.is_external("memory"));
        assert!(resolver.is_external("functional"));
    }

    #[test]
    fn test_cpp_resolver_is_external_c_compat() {
        let resolver = CppResolver;
        assert!(resolver.is_external("cstdio"));
        assert!(resolver.is_external("cstdlib"));
        assert!(resolver.is_external("cstring"));
        assert!(resolver.is_external("cmath"));
    }

    #[test]
    fn test_cpp_resolver_is_external_system_prefixes() {
        let resolver = CppResolver;
        assert!(resolver.is_external("sys/stat.h"));
        assert!(resolver.is_external("sys/types.h"));
        assert!(resolver.is_external("netinet/in.h"));
        assert!(resolver.is_external("arpa/inet.h"));
        assert!(resolver.is_external("bits/confname.h"));
    }

    #[test]
    fn test_cpp_resolver_is_external_third_party() {
        let resolver = CppResolver;
        assert!(resolver.is_external("boost/asio.hpp"));
        assert!(resolver.is_external("opencv2/core.hpp"));
        assert!(resolver.is_external("QtWidgets/QApplication"));
        assert!(resolver.is_external("curl/curl.h"));
        assert!(resolver.is_external("openssl/ssl.h"));
        assert!(resolver.is_external("sqlite3.h"));
        assert!(resolver.is_external("windows.h"));
        assert!(resolver.is_external("GL/gl.h"));
        assert!(resolver.is_external("pthread.h"));
        assert!(resolver.is_external("unistd.h"));
    }

    #[test]
    fn test_cpp_resolver_not_external_project_header() {
        let resolver = CppResolver;
        assert!(!resolver.is_external("myutils.h"));
        assert!(!resolver.is_external("helpers.h"));
        assert!(!resolver.is_external("internal/config.h"));
        assert!(!resolver.is_external("project_types.h"));
        assert!(!resolver.is_external("myclass.hpp"));
    }

    #[test]
    fn test_cpp_resolver_empty_module_name() {
        let resolver = CppResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_cpp_resolver_language() {
        let resolver = CppResolver;
        assert_eq!(resolver.language(), "c");
    }

    #[test]
    fn test_cpp_resolver_file_to_module_name() {
        let resolver = CppResolver;
        assert_eq!(
            resolver.file_to_module_name("src/foo.h", Path::new(".")),
            Some("foo.h".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("include/bar.hpp", Path::new(".")),
            Some("bar.hpp".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/test.c", Path::new(".")),
            Some("test.c".to_string())
        );
        // Non-C/C++ files return None
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_cpp_resolver_resolve_module_empty_index() {
        let resolver = CppResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "myutils.h",
            "src/main.c",
            Path::new("."),
            &index,
        );
        // Should return empty since ModuleIndex is empty
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_cpp_resolver_resolve_module_with_source_dir_candidate() {
        let resolver = CppResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "myutils.h",
            "src/main.c",
            Path::new("."),
            &index,
        );
        // Empty index → no candidates found
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_c() {
        let r = LanguageRegistry::get("c");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "c");
    }

    #[test]
    fn test_language_registry_get_cpp() {
        let r = LanguageRegistry::get("cpp");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "c");
    }

    #[test]
    fn test_language_registry_get_c_plus_plus() {
        let r = LanguageRegistry::get("c++");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "c");
    }

    #[test]
    fn test_supported_languages_includes_c_and_cpp() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"c"));
        assert!(langs.contains(&"cpp"));
    }

    #[test]
    fn test_is_cpp_external_mixed_case_paths() {
        // Check that various common system headers are external
        assert!(is_cpp_external("stdio.h"));
        assert!(is_cpp_external("iostream"));
        assert!(is_cpp_external("cstring"));
        assert!(is_cpp_external("sys/socket.h"));
        assert!(is_cpp_external("unistd.h"));
        assert!(is_cpp_external("fcntl.h"));
        // Project headers are NOT external
        assert!(!is_cpp_external("myapp.h"));
        assert!(!is_cpp_external("lib/helpers.h"));
    }

    #[test]
    fn test_is_cpp_external_vulkan_gl() {
        assert!(is_cpp_external("vulkan/vulkan.h"));
        assert!(is_cpp_external("GL/gl.h"));
        assert!(is_cpp_external("GL/glew.h"));
    }
}
