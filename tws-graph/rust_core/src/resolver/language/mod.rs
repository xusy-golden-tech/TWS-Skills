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
            "scala" => Some(Box::new(ScalaResolver)),
            "go" => Some(Box::new(GoResolver)),
            "rust" => Some(Box::new(RustResolver)),
            "php" => Some(Box::new(PhpResolver)),
            "ruby" => Some(Box::new(RubyResolver)),
            "c" | "cpp" | "c++" => Some(Box::new(CppResolver)),
            "csharp" => Some(Box::new(CSharpResolver)),
            "dart" => Some(Box::new(DartResolver)),
            "swift" => Some(Box::new(SwiftResolver)),
            "lua" => Some(Box::new(LuaResolver)),
            "bash" => Some(Box::new(BashResolver)),
            "groovy" => Some(Box::new(GroovyResolver)),
            "zig" => Some(Box::new(ZigResolver)),
            "nix" => Some(Box::new(NixResolver)),
            "elixir" => Some(Box::new(ElixirResolver)),
            _ => None,
        }
    }

    /// Return a list of language names that have resolvers registered.
    pub fn supported_languages() -> Vec<&'static str> {
        vec!["python", "typescript", "javascript", "java", "kotlin", "scala", "go", "rust", "php", "ruby", "c", "cpp", "csharp", "dart", "swift", "lua", "bash", "groovy", "zig", "nix", "elixir"]
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
// Scala module resolver
// ---------------------------------------------------------------------------

/// Scala module resolver.
///
/// Uses the same JVM package conventions as Java/Kotlin:
/// - `import com.foo.bar.MyClass` → module = `com.foo.bar.MyClass` → file = `com/foo/bar/MyClass.scala`
/// - `import com.foo.bar._` → module = `com.foo.bar` → files = `com/foo/bar/*.scala`
/// - `import com.foo.bar.{Baz, Qux}` → each symbol resolved individually
/// - ModuleIndex lookup: dotted package.name → file_path mapping
/// - Source root prefixes: `src/main/scala/`, `src/main/java/`, `src/test/scala/`, etc.
///
/// Scala files can coexist with Java/Kotlin in the same source tree; known stdlib
/// and third-party JVM packages are classified as external.
pub struct ScalaResolver;

impl ModuleResolver for ScalaResolver {
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

            // 3. Try standard source root prefixes (Maven/Gradle/SBT conventions)
            let package_path = package_name.replace('.', "/");
            let candidates = vec![
                format!("src/main/scala/{}/{}.scala", package_path, class_name),
                format!("src/test/scala/{}/{}.scala", package_path, class_name),
                format!("src/main/java/{}/{}.scala", package_path, class_name),
                format!("src/test/java/{}/{}.scala", package_path, class_name),
                format!("src/{}/{}.scala", package_path, class_name),
            ];

            let mut result = Vec::new();
            for candidate in &candidates {
                if let Some(files) = module_index.lookup(candidate) {
                    result.extend(files.clone());
                }
            }

            // Also try as dotted name
            for candidate in &candidates {
                let dotted = candidate.replace('/', ".").trim_end_matches(".scala").to_string();
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
            format!("src/main/scala/{}", package_path),
            format!("src/test/scala/{}", package_path),
            format!("src/main/java/{}", package_path),
            format!("src/test/java/{}", package_path),
            format!("src/{}", package_path),
        ];

        // Try ModuleIndex lookup with various prefixes
        for prefix in &["src.main.scala.", "src.test.scala.", "src.main.java.", "src.test.java.", "src."] {
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
        crate::resolver::module_index::infer_module_name(file_path, "scala")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_scala_external(module_name)
    }

    fn language(&self) -> &'static str {
        "scala"
    }
}

/// Check if a module name is a known Scala/JVM standard library or common
/// third-party framework.  Scala shares the JVM ecosystem so this is
/// based on `is_java_external`, plus Scala-specific stdlib and framework prefixes.
pub fn is_scala_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Scala-specific stdlib and framework prefixes
    let scala_prefixes: &[&str] = &[
        "scala.", "akka.", "play.", "zio.", "cats.",
        "shapeless.", "scalaz.", "scalatest.", "scalacheck.",
        "slick.", "doobie.", "http4s.", "fs2.",
        "com.typesafe.", "org.typelevel.",
    ];
    for prefix in scala_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // All Java stdlib / third-party prefixes also apply to Scala
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

// ---------------------------------------------------------------------------
// C# module resolver (v7.3.0)
// ---------------------------------------------------------------------------

/// C# module resolver.
///
/// Uses C# namespace/using conventions:
/// - `using Foo.Bar.Baz;` → module = `Foo.Bar.Baz` → file = `Foo/Bar/Baz.cs`
/// - ModuleIndex lookup: dotted namespace.declaration → file_path mapping
/// - Source root prefixes: `src/`, `Services/`, `Models/`
///
/// C# namespace mapping follows .NET conventions where namespace segments
/// roughly correspond to directory structure:
/// - `MyApp.Services.UserService` → `src/Services/UserService.cs`
/// - `MyApp.Models.Customer` → `src/Models/Customer.cs`
pub struct CSharpResolver;

impl ModuleResolver for CSharpResolver {
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

        // 1. Direct ModuleIndex lookup (namespace as dotted name)
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try removing the type name (last dot-segment) to get namespace
        // e.g., "MyApp.Services.UserService" → namespace "MyApp.Services"
        if let Some(last_dot) = module_name.rfind('.') {
            let namespace_name = &module_name[..last_dot];
            let type_name = &module_name[last_dot + 1..];

            // Look up the namespace in ModuleIndex
            if let Some(files) = module_index.lookup(namespace_name) {
                return files.clone();
            }

            // 3. Try common source root prefixes, converting namespace to path
            let ns_path = namespace_name.replace('.', "/");
            let candidates = vec![
                format!("{}/{}.cs", ns_path, type_name),
                format!("src/{}/{}.cs", ns_path, type_name),
            ];

            let mut result = Vec::new();
            for candidate in &candidates {
                if let Some(files) = module_index.lookup(candidate) {
                    result.extend(files.clone());
                }
            }

            if !result.is_empty() {
                return result;
            }
        } else {
            // Single-segment name: try as both namespace and type
            let type_candidate = format!("{}.cs", module_name);
            if let Some(files) = module_index.lookup(&type_candidate) {
                return files.clone();
            }

            for prefix in &["src/", ""] {
                let with_prefix = format!("{}{}.cs", prefix, module_name);
                if let Some(files) = module_index.lookup(&with_prefix) {
                    return files.clone();
                }
            }
        }

        // 4. Try ModuleIndex with namespace prefix variations
        for prefix in &["src.", ""] {
            let with_prefix = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&with_prefix) {
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
        crate::resolver::module_index::infer_module_name(file_path, "csharp")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_csharp_external(module_name)
    }

    fn language(&self) -> &'static str {
        "csharp"
    }
}

/// Check if a module/type name is from a known C# system namespace or common
/// third-party library.
///
/// A module is considered external if it starts with:
/// - System.* / System.* (BCL)
/// - Microsoft.* (nuget packages, ASP.NET, EntityFramework)
/// - Common third-party NuGet packages (Newtonsoft, Serilog, AutoMapper, etc.)
///
/// Project-local using directives (`using MyApp.Services.UserService;`) are
/// not considered external unless they match one of the known patterns above.
pub fn is_csharp_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // .NET Base Class Library and SDK prefixes
    let system_prefixes: &[&str] = &[
        "System.", "System",
        "Microsoft.", "Microsoft",
    ];

    for prefix in system_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // Common third-party NuGet packages / frameworks
    let third_party_prefixes: &[&str] = &[
        "Newtonsoft.Json",
        "Npgsql",
        "Dapper",
        "Serilog",
        "AutoMapper",
        "NLog",
        "log4net",
        "FluentValidation",
        "MediatR",
        "Polly",
        "Swashbuckle",
        "Xunit",
        "NUnit",
        "Moq",
        "FluentAssertions",
        "EntityFramework",
        "IdentityModel",
        "RestSharp",
        "StackExchange.Redis",
        "MassTransit",
        "Grpc",
        "Google.Protobuf",
        "AWSSDK",
        "Azure",
        "Amazon",
        "Hangfire",
        "Quartz",
        "Sentry",
        "OpenTelemetry",
        "YamlDotNet",
        "CsvHelper",
        "Humanizer",
        "Stateless",
        "BenchmarkDotNet",
    ];

    for prefix in third_party_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    false
}

// ---------------------------------------------------------------------------
// Dart module resolver (Stage 12)
// ---------------------------------------------------------------------------

/// Dart module resolver.
///
/// Dart module system:
/// - `package:my_app/foo/bar.dart` → resolves to `lib/foo/bar.dart`
/// - `dart:core` → Dart SDK (external)
/// - `foo/bar.dart` → relative path import
/// - `import '...' show X` → selective import
/// - `import '...' hide X` → import all except X
/// - `import '...' as prefix` → prefixed import
pub struct DartResolver;

impl ModuleResolver for DartResolver {
    fn resolve_module(
        &self,
        module_name: &str,
        source_file: &str,
        project_root: &Path,
        module_index: &ModuleIndex,
    ) -> Vec<String> {
        if module_name.is_empty() {
            return Vec::new();
        }

        // 1. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Handle package: scheme → map to lib/ directory
        //    package:my_app/foo/bar.dart → lib/foo/bar.dart
        //    (strip the package name, which is the first path segment)
        if module_name.starts_with("package:") {
            let after_scheme = &module_name["package:".len()..];
            // Strip the package name (first path segment) to get the lib-relative path
            let lib_relative = if let Some(slash_pos) = after_scheme.find('/') {
                &after_scheme[slash_pos + 1..]
            } else {
                // package:foo → lib/foo.dart (single package, no path)
                after_scheme
            };
            // The lib-relative path maps under lib/
            let lib_path = if lib_relative.is_empty() {
                "lib".to_string()
            } else {
                format!("lib/{}", lib_relative)
            };

            // Try the lib/ path
            if let Some(files) = module_index.lookup(&lib_path) {
                return files.clone();
            }

            // Try without .dart extension (ModuleIndex may store it)
            let path_without_dart = if lib_path.ends_with(".dart") {
                lib_path[..lib_path.len() - 5].to_string()
            } else {
                lib_path.clone()
            };
            let dotted = path_without_dart.replace('/', ".");
            if let Some(files) = module_index.lookup(&dotted) {
                return files.clone();
            }

            // Also try just the filename without path scheme
            let basename = if let Some(slash_pos) = after_scheme.rfind('/') {
                &after_scheme[slash_pos + 1..]
            } else {
                after_scheme
            };
            let basename_no_ext = if basename.ends_with(".dart") {
                &basename[..basename.len() - 5]
            } else {
                basename
            };

            // Look up basename in ModuleIndex
            if let Some(files) = module_index.lookup(basename_no_ext) {
                return files.clone();
            }

            // Also try dotted variations
            for prefix in &["lib.", ""] {
                let candidate = if path_without_dart.ends_with(basename_no_ext) {
                    // Try with/without directory parts
                    if let Some(dir_part_end) = path_without_dart.rfind(basename_no_ext) {
                        let dir_part = &path_without_dart[..dir_part_end];
                        format!("{}{}{}", prefix, dir_part.replace('/', "."), basename_no_ext)
                    } else {
                        format!("{}{}", prefix, basename_no_ext)
                    }
                } else {
                    format!("{}{}", prefix, path_without_dart.replace('/', "."))
                };
                if let Some(files) = module_index.lookup(&candidate) {
                    if !files.is_empty() {
                        return files.clone();
                    }
                }
            }

            // Return the lib/ path as a candidate
            return vec![lib_path];
        }

        // 3. Handle dart: scheme → external, don't resolve
        if module_name.starts_with("dart:") {
            return Vec::new();
        }

        // 4. Handle relative paths (e.g., 'foo/bar.dart')
        // Resolve relative to the source file's directory
        let source_dir = {
            let p = Path::new(source_file);
            p.parent()
                .map(|d| d.to_string_lossy().to_string())
                .unwrap_or_default()
        };

        let resolved = if !source_dir.is_empty() {
            let normalized = if module_name.starts_with("./") {
                format!("{}/{}", source_dir, &module_name[2..])
            } else if module_name.starts_with("../") {
                // Resolve parent directory
                let parent = {
                    let p = Path::new(&source_dir);
                    p.parent()
                        .map(|d| d.to_string_lossy().to_string())
                        .unwrap_or_default()
                };
                if parent.is_empty() {
                    module_name[3..].to_string()
                } else {
                    format!("{}/{}", parent, &module_name[3..])
                }
            } else {
                format!("{}/{}", source_dir, module_name)
            };
            // Simplify the path
            simplify_dart_path(&normalized)
        } else {
            module_name.to_string()
        };

        // Try the resolved path in ModuleIndex
        if let Some(files) = module_index.lookup(&resolved) {
            if !files.is_empty() {
                return files.clone();
            }
        }

        // Try without .dart extension
        let resolved_no_ext = if resolved.ends_with(".dart") {
            resolved[..resolved.len() - 5].to_string()
        } else {
            resolved.clone()
        };
        let dotted = resolved_no_ext.replace('/', ".");
        if let Some(files) = module_index.lookup(&dotted) {
            if !files.is_empty() {
                return files.clone();
            }
        }

        // Try with/without common source root prefixes
        for prefix in &["lib.", "src.", ""] {
            let candidate = format!("{}{}", prefix, dotted);
            if let Some(files) = module_index.lookup(&candidate) {
                if !files.is_empty() {
                    return files.clone();
                }
            }
        }

        // If .dart extension is present, return as candidate
        if resolved.ends_with(".dart") {
            return vec![resolved];
        }

        // Try adding .dart extension
        vec![format!("{}.dart", resolved)]
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "dart")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_dart_external(module_name)
    }

    fn language(&self) -> &'static str {
        "dart"
    }
}

/// Simplify a path by resolving ./ and ../ segments.
fn simplify_dart_path(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for segment in path.split('/') {
        match segment {
            "." => {}
            ".." => {
                parts.pop();
            }
            "" => {}
            s => parts.push(s),
        }
    }
    parts.join("/")
}

/// Check if a module name is from a known Dart/Flutter SDK or common
/// third-party package.
///
/// A module is considered external if it starts with:
/// - `dart:*` (Dart core SDK)
/// - `package:flutter/*` (Flutter framework)
/// - `package:meta/*` (meta package)
/// - Other well-known third-party packages
pub fn is_dart_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // dart: scheme is always external (Dart SDK)
    if module_name.starts_with("dart:") {
        return true;
    }

    // Check package: imports for known external packages
    if module_name.starts_with("package:") {
        let after_scheme = &module_name["package:".len()..];

        // Extract the package name (first path segment)
        let package_name = if let Some(slash_pos) = after_scheme.find('/') {
            &after_scheme[..slash_pos]
        } else {
            after_scheme
        };

        // Flutter framework packages
        let flutter_packages: &[&str] = &[
            "flutter", "flutter_test", "flutter_driver",
            "flutter_localizations", "flutter_web_plugins",
            "integration_test",
        ];
        if flutter_packages.contains(&package_name) {
            return true;
        }

        // Dart team packages
        let dart_team_packages: &[&str] = &[
            "meta", "collection", "async", "convert", "crypto",
            "html", "http", "intl", "logging", "markdown",
            "mockito", "path", "pedantic", "plugin", "quiver",
            "shelf", "source_span", "stack_trace", "stream_channel",
            "string_scanner", "term_glyph", "test", "typed_data",
            "usage", "vector_math", "watcher", "web_socket_channel",
            "yaml", "args", "characters", "clock", "fake_async",
            "file", "matcher", "platform", "process", "pub_semver",
            "pool", "glob", "json_annotation", "lints", "build",
            "source_gen", "analyzer", "front_end",
            // Additional well-known third-party
            "provider", "riverpod", "bloc", "flutter_bloc",
            "get", "dio", "retrofit", "chopper",
            "sqflite", "floor", "drift", "hive",
            "shared_preferences", "path_provider",
            "firebase_core", "firebase_auth", "firebase_firestore",
            "url_launcher", "google_fonts",
            "equatable", "freezed", "freezed_annotation",
            "json_serializable", "build_runner",
            "cached_network_image", "flutter_svg",
            "go_router", "auto_route",
            "intl", "flutter_localizations",
            "rxdart", "dartz",
            "get_it", "injectable",
            "flutter_hooks", "hooks_riverpod",
            "google_maps_flutter", "geolocator",
            "connectivity_plus", "permission_handler",
            "image_picker", "file_picker",
            "flutter_secure_storage", "encrypted_shared_preferences",
        ];

        if dart_team_packages.contains(&package_name) {
            return true;
        }

        // Check for Dart team packages (pub.dev publisher "dart.dev" or "flutter.dev")
        if package_name.starts_with("flutter_") {
            return true;
        }
    }

    false
}

// ---------------------------------------------------------------------------
// Swift module resolver (Stage 13)
// ---------------------------------------------------------------------------

/// Swift module resolver.
///
/// Handles Swift's import system:
/// - `import UIKit` → module = UIKit
/// - `import class UIKit.UIViewController` → module = UIKit, symbol = UIViewController
/// - `import struct Foundation.Data` → module = Foundation, symbol = Data
///
/// Key design decisions:
/// - Within the same Xcode target/Swift Package, all files share a namespace;
///   cross-file references within a target do NOT require imports.  The
///   `ModuleIndex` uses the directory-based module name for these references.
/// - Frameworks and other modules require `import`, and are looked up via
///   `ModuleIndex` first (for project-internal modules), then checked as
///   external.
/// - External frameworks: Foundation, UIKit, SwiftUI, AppKit, Combine, etc.
pub struct SwiftResolver;

impl ModuleResolver for SwiftResolver {
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

        // 1. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["", "Sources.", "src.", "lib."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Try splitting dotted names and look up last segment
        //    e.g., "UIKit.UIViewController" → try "UIViewController"
        if let Some(dot_pos) = module_name.rfind('.') {
            let last_segment = &module_name[dot_pos + 1..];
            if let Some(files) = module_index.lookup(last_segment) {
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
        crate::resolver::module_index::infer_module_name(file_path, "swift")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_swift_external(module_name)
    }

    fn language(&self) -> &'static str {
        "swift"
    }
}

/// Check if a module name is a known Apple system framework or common
/// Swift third-party dependency.
///
/// A module is considered external if it is:
/// - An Apple system framework (Foundation, UIKit, SwiftUI, AppKit, etc.)
/// - The Swift standard library (Swift)
/// - A common third-party Swift package (Alamofire, Kingfisher, etc.)
pub fn is_swift_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    let lower = module_name.to_lowercase();

    // Apple system frameworks + Swift standard library
    let apple_frameworks: &[&str] = &[
        // Swift core
        "swift", "dispatch", "darwin", "objectivec", "os",
        // Core frameworks
        "foundation", "uikit", "appkit", "watchkit",
        "swiftui", "combine", "swiftdata",
        // Extended frameworks
        "coregraphics", "coreanimation", "coreimage", "corevideo",
        "coremedia", "coreaudiocore", "coreaudio", "corelocation",
        "corebluetooth", "coremotion", "coreml", "corenfc",
        "coretext", "coredata", "coreservices", "corewlan",
        "avfoundation", "avkit", "arkit", "realitykit",
        "mapkit", "webkit", "scenekit", "spritekit",
        "gamekit", "gamecontroller", "metal", "metalkit",
        "storekit", "cloudkit",
        "photos", "photosui", "contacts", "contactsui",
        "eventkit", "eventkitui", "healthkit", "homekit",
        "usernotifications", "notificationcenter",
        "safariservices", "authenticationservices",
        "widgetkit", "vision", "visionkit",
        "accelerate", "cfnetwork", "fileprovider",
        "carplay", "callkit",
        "swiftcharts", "chart",
        "cryptokit", "naturallanguage", "speech",
        "network", "networkextension",
        "pdfkit", "quicklook", "quicklookthumbnailing",
        "pushkit", "iokit",
        "intents", "intentsui",
        "mediaplayer", "messages",
        "metalperformanceshaders", "metalperformanceshadersgraph",
        "metricskit",
        "multipeerconnectivity",
        "localauthentication", "devicecheck",
        "screenplay", "shareplay",
        "swiftnio",
    ];

    // Check exact matches (case-insensitive)
    if apple_frameworks.contains(&lower.as_str()) {
        return true;
    }

    // Check if the first segment (before any dot) is a known framework
    let first_segment = lower.split('.').next().unwrap_or(&lower);
    if apple_frameworks.contains(&first_segment) {
        return true;
    }

    // Common third-party Swift packages
    let third_party: &[&str] = &[
        "alamofire", "kingfisher", "snapkit", "moya", "rxswift",
        "swiftyjson", "sdwebimage", "realm", "lottie", "iglistkit",
        "rxdatasources", "promisekit", "nvactivityindicatorview",
        "hero", "pop", "chameleonframework",
        "swiftlint", "quick", "nimble",
        "firebase", "facebook", "twitter", "google",
        "socket.io", "starscream",
        "grpc-swift", "swift-protobuf",
        "apollo", "vapor", "fluent",
        "carthage", "cocoapods",
    ];

    if third_party.contains(&first_segment) {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Lua module resolver (Stage 14)
// ---------------------------------------------------------------------------

/// Lua module resolver.
///
/// Handles Lua's module system via `require`:
/// - `require("foo")` → loads `foo.lua` or `foo/init.lua` from source roots
/// - `require("foo.bar")` → loads `foo/bar.lua` or `foo/bar/init.lua`
///
/// Lua modules use `.` as a namespace separator which maps to `/` for
/// file path resolution.
pub struct LuaResolver;

impl ModuleResolver for LuaResolver {
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

        // 1. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["", "src.", "lib.", "lua."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Generate candidate file paths based on module_name
        //    require("foo") → foo.lua, foo/init.lua
        //    require("foo.bar") → foo/bar.lua, foo/bar/init.lua
        let mut candidates = Vec::new();

        let module_path = module_name.replace('.', "/");
        for prefix in &["src/", "lib/", "lua/", ""] {
            // Regular .lua file
            let file_candidate = format!("{}{}.lua", prefix, module_path);
            candidates.push(file_candidate);

            // init.lua (module directory)
            let init_candidate = format!("{}{}/init.lua", prefix, module_path);
            candidates.push(init_candidate);
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "lua")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_lua_external(module_name)
    }

    fn language(&self) -> &'static str {
        "lua"
    }
}

/// Check if a module name is a known Lua standard library.
///
/// Lua has a small standard library.  Common third-party Lua packages
/// (installed via LuaRocks) are also classified as external.
pub fn is_lua_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    let root = module_name.split('.').next().unwrap_or(module_name);

    // Lua standard library modules
    let lua_stdlib: &[&str] = &[
        "string", "table", "math", "io", "os", "coroutine",
        "debug", "utf8", "package",
    ];

    if lua_stdlib.contains(&root) {
        return true;
    }

    // Common third-party Lua packages (LuaRocks)
    let third_party: &[&str] = &[
        "luasocket", "socket", "http", "mime", "ltn12",
        "lpeg", "luafilesystem", "lfs",
        "luasec", "ssl",
        "luajson", "cjson", "dkjson",
        "lua-cjson",
        "penlight", "pl",
        "busted", "luacheck", "luacov",
        "argparse", "luaposix",
        "redis-lua", "lua-resty-redis",
        "pgmoon", "lua-resty-mysql",
        "lapis", "lua-resty-http",
        "moonscript",
        "inspect",
    ];

    if third_party.contains(&root) {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Bash module resolver (Stage 15)
// ---------------------------------------------------------------------------

/// Bash module resolver.
///
/// Bash scripts use `source file.sh` or `. file.sh` to import other scripts.
/// Sourced scripts execute in the current shell, making all functions and
/// variables available.  There is no namespace — everything is in a flat scope.
///
/// Module names are file paths (e.g. `lib/utils.sh`).
pub struct BashResolver;

impl ModuleResolver for BashResolver {
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

        // 1. Direct ModuleIndex lookup by module name (the file path)
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes (src/, lib/)
        for prefix in &["", "src/", "lib/"] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Try stripping a prefix that might already exist in module_name
        //    e.g. module_name = "src/lib/utils.sh" but index has "lib/utils.sh"
        for prefix in &["src/", "lib/"] {
            if module_name.starts_with(prefix) {
                let stripped = &module_name[prefix.len()..];
                if let Some(files) = module_index.lookup(stripped) {
                    return files.clone();
                }
            }
        }

        // 4. Resolve relative paths (./) based on source file directory
        if module_name.starts_with("./") || module_name.starts_with("../") {
            let source_dir = Path::new(source_file)
                .parent()
                .unwrap_or(Path::new("."));
            let resolved = source_dir.join(module_name);
            let resolved_str = resolved.to_string_lossy().replace('\\', "/");

            // Try the resolved absolute-like path
            if let Some(files) = module_index.lookup(&resolved_str) {
                return files.clone();
            }

            // Also try stripping src/ from the resolved path
            for prefix in &["src/", "lib/"] {
                if resolved_str.starts_with(prefix) {
                    let stripped = &resolved_str[prefix.len()..];
                    if let Some(files) = module_index.lookup(stripped) {
                        return files.clone();
                    }
                }
            }

            // Generate candidate file paths for the resolved path
            return vec![resolved_str];
        }

        // 5. Generate candidate file paths (fallback)
        let mut candidates = Vec::new();
        for prefix in &["src/", "lib/", ""] {
            candidates.push(format!("{}{}", prefix, module_name));
        }
        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "bash")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_bash_external(module_name)
    }

    fn language(&self) -> &'static str {
        "bash"
    }
}

/// Check if a module name refers to an external (system) entity.
///
/// For Bash, source paths that look like system-level shell imports
/// (e.g. `/etc/profile`, `/usr/share/...`) are external, as are known
/// common Unix commands when they appear as call targets.
///
/// Local file paths (relative or project-rooted) are internal.
pub fn is_bash_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Absolute system paths are external
    if module_name.starts_with("/etc/")
        || module_name.starts_with("/usr/")
        || module_name.starts_with("/opt/")
        || module_name.starts_with("/dev/")
        || module_name.starts_with("/proc/")
        || module_name.starts_with("/sys/")
    {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// Groovy module resolver (Stage 16)
// ---------------------------------------------------------------------------

/// Groovy module resolver.
///
/// Uses the same JVM package conventions as Java/Kotlin/Scala:
/// - `import com.foo.bar.MyClass` → module = `com.foo.bar.MyClass` → file = `com/foo/bar/MyClass.groovy`
/// - `import com.foo.bar.*` → module = `com.foo.bar` → files = `com/foo/bar/*.groovy`
/// - `import static com.foo.Bar.method` → static import
/// - `import com.foo.Bar as Alias` → alias import (Groovy-specific)
/// - ModuleIndex lookup: dotted package.declaration → file_path mapping
/// - Source root prefixes: `src/main/groovy/`, `src/main/java/`, `src/test/groovy/`, etc.
///
/// Groovy files can coexist with Java/Kotlin/Scala in the same source tree;
/// known JVM stdlib and third-party packages plus Groovy-specific prefixes
/// are classified as external.
pub struct GroovyResolver;

impl ModuleResolver for GroovyResolver {
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
                format!("src/main/groovy/{}/{}.groovy", package_path, class_name),
                format!("src/test/groovy/{}/{}.groovy", package_path, class_name),
                format!("src/main/java/{}/{}.groovy", package_path, class_name),
                format!("src/test/java/{}/{}.groovy", package_path, class_name),
                format!("src/{}/{}.groovy", package_path, class_name),
            ];

            let mut result = Vec::new();
            for candidate in &candidates {
                if let Some(files) = module_index.lookup(candidate) {
                    result.extend(files.clone());
                }
            }

            // Also try as dotted name
            for candidate in &candidates {
                let dotted = candidate.replace('/', ".").trim_end_matches(".groovy").to_string();
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
            format!("src/main/groovy/{}", package_path),
            format!("src/test/groovy/{}", package_path),
            format!("src/main/java/{}", package_path),
            format!("src/test/java/{}", package_path),
            format!("src/{}", package_path),
        ];

        // Try ModuleIndex lookup with various prefixes
        for prefix in &["src.main.groovy.", "src.test.groovy.", "src.main.java.", "src.test.java.", "src."] {
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
        crate::resolver::module_index::infer_module_name(file_path, "groovy")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_groovy_external(module_name)
    }

    fn language(&self) -> &'static str {
        "groovy"
    }
}

/// Check if a module name is a known Groovy/JVM standard library or common
/// third-party framework.  Groovy shares the JVM ecosystem so this is based
/// on `is_java_external`, plus Groovy-specific stdlib and framework prefixes.
pub fn is_groovy_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Groovy-specific stdlib and framework prefixes
    let groovy_prefixes: &[&str] = &[
        "groovy.", "groovyjarjar.", "groovyx.",
        "org.codehaus.", "org.codehaus.groovy.", "org.codehaus.gpars.",
    ];
    for prefix in groovy_prefixes {
        if module_name.starts_with(prefix) {
            return true;
        }
    }

    // All Java stdlib / third-party prefixes also apply to Groovy
    is_java_external(module_name)
}

// ---------------------------------------------------------------------------
// Zig module resolver (Stage 17)
// ---------------------------------------------------------------------------

/// Zig module resolver.
///
/// Handles the `@import` system:
/// - File imports: `@import("foo.zig")`, `@import("subdir/bar.zig")`
/// - Relative imports: `@import("../baz.zig")`
/// - Standard library: `@import("std")` — classified as external
/// - Builtins: `@import("builtin")` — classified as external
///
/// Module names are file paths relative to the project root
/// (e.g. `"utils.zig"`, `"subdir/bar.zig"`).
pub struct ZigResolver;

impl ModuleResolver for ZigResolver {
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

        // 1. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["", "src.", "lib."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Resolve relative to source file directory.
        //    Zig @import paths are relative to the importing file.
        let source_dir = Path::new(source_file)
            .parent()
            .and_then(|p| p.to_str())
            .unwrap_or(".");

        // Try the import path as-is from the source file directory
        let resolved = if source_dir.is_empty() || source_dir == "." {
            module_name.to_string()
        } else {
            Path::new(source_dir)
                .join(module_name)
                .to_string_lossy()
                .replace('\\', "/")
        };

        // Ensure .zig extension
        let resolved_with_ext = if resolved.ends_with(".zig") {
            resolved
        } else {
            format!("{}.zig", resolved)
        };

        // Normalize the path (handle .. etc.)
        let normalized = simplify_zig_path(&resolved_with_ext);

        // Check if the normalized path exists in ModuleIndex
        if let Some(files) = module_index.lookup(&normalized) {
            return files.clone();
        }

        // Try with common prefixes (src/, lib/)
        for prefix in &["src/", "lib/", ""] {
            let candidate = format!("{}{}", prefix, normalized);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // Generate candidate file paths as fallback
        let mut candidates = Vec::new();
        for prefix in &["src/", "lib/", ""] {
            candidates.push(format!("{}{}", prefix, normalized));
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "zig")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_zig_external(module_name)
    }

    fn language(&self) -> &'static str {
        "zig"
    }
}

/// Check if a module name is a known Zig standard library or builtin.
///
/// Zig has a small standard library.  Module names without `.zig` extension
/// that match known stdlib/module names are classified as external.
pub fn is_zig_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // If it has a file extension like .zig, it's a local file import
    if module_name.ends_with(".zig") || module_name.contains('/') {
        return false;
    }

    // Zig standard library / builtin modules
    let zig_stdlib: &[&str] = &[
        "std",
        "builtin",
        "root",
        "@This",
    ];

    if zig_stdlib.contains(&module_name) {
        return true;
    }

    // Zig standard library sub-modules accessed via @import
    // These always start with "std."
    if module_name.starts_with("std.") {
        return true;
    }

    false
}

/// Simplify a Zig file path by resolving `.` and `..` segments.
fn simplify_zig_path(path: &str) -> String {
    let normalized = path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    let mut stack: Vec<&str> = Vec::new();

    for part in parts {
        match part {
            "." | "" => continue,
            ".." => {
                stack.pop();
            }
            _ => {
                stack.push(part);
            }
        }
    }

    stack.join("/")
}

// ---------------------------------------------------------------------------
// Nix module resolver (Stage 18)
// ---------------------------------------------------------------------------

/// Nix module resolver.
///
/// Nix uses `import ./path.nix` for relative file imports and
/// `import <nixpkgs>` for NIX_PATH lookups.  Import paths are file paths
/// (not dotted module names).
///
/// Key import patterns:
/// - `import ./lib.nix` → relative to source file directory
/// - `import <nixpkgs>` → NIX_PATH search path (external)
/// - `import "${var}/file.nix"` → dynamic path (skipped by extractor)
/// - `callPackage ./foo.nix { }` → same as import for the path arg
/// - `with pkgs;` → scope import from attrset (external)
/// - `inherit (pkgs.stdenv) mkDerivation;` → scope import (external)
///
/// Module names are file paths with `/` separators (e.g. `pkgs/default`).
pub struct NixResolver;

impl ModuleResolver for NixResolver {
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

        // 1. Handle NIX_PATH angle-bracket syntax: <nixpkgs>, <nixpkgs/nixos>
        //    These are external and cannot be resolved to local files.
        if module_name.starts_with('<') {
            return Vec::new(); // external, no local candidates
        }

        // 2. Handle relative paths (./foo.nix, ../bar.nix)
        if module_name.starts_with('.') {
            let source_dir = Path::new(source_file)
                .parent()
                .unwrap_or(Path::new("."));
            let resolved = source_dir.join(module_name);
            let resolved_str = resolved.to_string_lossy().replace('\\', "/");

            // Normalize the path (resolve .. and .)
            let normalized = simplify_nix_path(&resolved_str);

            // Try the resolved path directly in ModuleIndex
            // First try with the .nix extension
            let with_ext = if normalized.ends_with(".nix") {
                normalized.clone()
            } else {
                format!("{}.nix", normalized)
            };

            // Check ModuleIndex for the resolved path
            if let Some(files) = module_index.lookup(&with_ext) {
                return files.clone();
            }

            // Also try without .nix extension (ModuleIndex strips extensions)
            let without_ext = if with_ext.ends_with(".nix") {
                with_ext[..with_ext.len() - 4].to_string()
            } else {
                with_ext.clone()
            };
            if let Some(files) = module_index.lookup(&without_ext) {
                return files.clone();
            }

            // Try without src/ prefix (if the index has stripped it)
            for prefix in &["src/", "lib/", "nix/"] {
                if without_ext.starts_with(prefix) {
                    let stripped = &without_ext[prefix.len()..];
                    if let Some(files) = module_index.lookup(stripped) {
                        return files.clone();
                    }
                }
            }

            // Return the resolved path as candidate file path
            return vec![with_ext];
        }

        // 3. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 4. Try with common source root prefixes
        for prefix in &["", "src/", "lib/", "nix/"] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
            // Also try with .nix extension
            let with_nix = if candidate.ends_with(".nix") {
                candidate.clone()
            } else {
                format!("{}.nix", candidate)
            };
            if let Some(files) = module_index.lookup(&with_nix) {
                return files.clone();
            }
            // Also try without .nix extension
            let without_nix = if with_nix.ends_with(".nix") {
                with_nix[..with_nix.len() - 4].to_string()
            } else {
                with_nix
            };
            if let Some(files) = module_index.lookup(&without_nix) {
                return files.clone();
            }
        }

        // 5. Generate candidate file paths as fallback
        let mut candidates = Vec::new();
        let module_path = module_name;
        for prefix in &["src/", "lib/", "nix/", ""] {
            let without_ext = if module_path.ends_with(".nix") {
                module_path[..module_path.len() - 4].to_string()
            } else {
                module_path.to_string()
            };
            candidates.push(format!("{}{}.nix", prefix, without_ext));
        }
        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "nix")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_nix_external(module_name)
    }

    fn language(&self) -> &'static str {
        "nix"
    }
}

/// Check if a module name refers to an external Nix entity.
///
/// Nix has the concept of "channels" (nixpkgs) and a handful of builtins.
/// Module names in angle brackets (`<nixpkgs>`, `<home-manager>`) are always
/// external since they refer to NIX_PATH entries.  Top-level attrset names
/// commonly used in `with` statements (like `pkgs`, `lib`, `builtins`,
/// `stdenv`) are also external because they come from the nixpkgs channel.
pub fn is_nix_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Angle-bracket NIX_PATH references are always external
    if module_name.starts_with('<') {
        return true;
    }

    // File paths (containing / or .nix extension) are internal
    // Must check this BEFORE checking against external names like "pkgs"
    if module_name.ends_with(".nix") || module_name.contains('/') {
        return false;
    }

    // Dotted paths referencing nixpkgs attrsets (pkgs.xxx, lib.xxx)
    if module_name.starts_with("pkgs.") || module_name.starts_with("lib.") {
        return true;
    }

    // Known Nix builtins and nixpkgs top-level scopes
    let nix_external: &[&str] = &[
        // Nix builtins
        "builtins",
        "derivation",
        "import",
        "abort",
        "throw",
        "toString",
        "baseNameOf",
        "dirOf",
        "fetchTarball",
        "fetchGit",
        "fetchurl",
        // nixpkgs top-level attrset names (commonly used with `with`)
        "pkgs",
        "lib",
        "stdenv",
        "nixpkgs",
        // nixpkgs package scopes
        "python3",
        "python3Packages",
        "haskellPackages",
        "nodePackages",
        "perlPackages",
        // Other common channels / flakes
        "home-manager",
        "nixos",
        "nix-darwin",
        "flake-utils",
        "flake-parts",
        // Call package helpers
        "callPackage",
        "callPackages",
    ];

    let root = module_name;
    let root_no_bracket = root.trim_start_matches('<').trim_end_matches('>');

    if nix_external.contains(&root_no_bracket) {
        return true;
    }

    false
}

/// Simplify a Nix file path by resolving `.` and `..` segments.
fn simplify_nix_path(path: &str) -> String {
    let normalized = path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    let mut stack: Vec<&str> = Vec::new();

    for part in parts {
        match part {
            "." | "" => continue,
            ".." => {
                stack.pop();
            }
            _ => {
                stack.push(part);
            }
        }
    }

    stack.join("/")
}

// ---------------------------------------------------------------------------
// Elixir module resolver (Stage 19)
// ---------------------------------------------------------------------------

/// Elixir module resolver.
///
/// Elixir uses a `.`-separated module system:
/// - `alias MyApp.Services.User` → resolve to `lib/my_app/services/user.ex`
/// - `import Enum` → external (Elixir stdlib)
/// - `use Phoenix.LiveView` → external (third-party package)
/// - `require Logger` → external (Elixir stdlib)
///
/// Module name to file path mapping:
/// `MyApp.Services.User` → `lib/my_app/services/user.ex`
/// (dots → slashes, CamelCase → snake_case)
pub struct ElixirResolver;

impl ModuleResolver for ElixirResolver {
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

        // 1. Direct ModuleIndex lookup by module name
        if let Some(files) = module_index.lookup(module_name) {
            return files.clone();
        }

        // 2. Try with common source root prefixes
        for prefix in &["", "lib.", "src.", "test."] {
            let candidate = format!("{}{}", prefix, module_name);
            if let Some(files) = module_index.lookup(&candidate) {
                return files.clone();
            }
        }

        // 3. Generate candidate file paths from module name
        //    e.g., "MyApp.Services.User" → "lib/my_app/services/user.ex"
        let module_path = elixir_module_to_path(module_name);

        let mut candidates = Vec::new();
        for prefix in &["lib/", "src/", "test/", "tests/", ""] {
            // .ex file
            let ex_file = format!("{}{}.ex", prefix, module_path);
            candidates.push(ex_file);

            // .exs file (script)
            let exs_file = format!("{}{}.exs", prefix, module_path);
            candidates.push(exs_file);
        }

        candidates
    }

    fn file_to_module_name(
        &self,
        file_path: &str,
        _project_root: &Path,
    ) -> Option<String> {
        crate::resolver::module_index::infer_module_name(file_path, "elixir")
    }

    fn is_external(&self, module_name: &str) -> bool {
        is_elixir_external(module_name)
    }

    fn language(&self) -> &'static str {
        "elixir"
    }
}

/// Convert an Elixir module name to a slash-separated file path (without extension).
///
/// `"MyApp.Services.User"` → `"my_app/services/user"`
fn elixir_module_to_path(module_name: &str) -> String {
    module_name.split('.')
        .map(camel_to_snake)
        .collect::<Vec<String>>()
        .join("/")
}

/// Convert a CamelCase segment to snake_case.
///
/// `"MyApp"` → `"my_app"`, `"User"` → `"user"`, `"HTTPClient"` → `"http_client"`
fn camel_to_snake(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }

    let mut result = String::new();
    let chars: Vec<char> = s.chars().collect();

    for (i, &c) in chars.iter().enumerate() {
        if c.is_uppercase() {
            if i > 0 {
                // Check if we need an underscore
                let prev = chars[i - 1];
                // Insert underscore before uppercase letter unless:
                // - previous is also uppercase (for acronyms like HTTPClient)
                // - but do insert if next char is lowercase (e.g., HTTPClient → http_client)
                let next_is_lower = chars.get(i + 1).map(|n| n.is_lowercase()).unwrap_or(false);
                if !prev.is_uppercase() || (prev.is_uppercase() && next_is_lower) {
                    result.push('_');
                }
            }
            result.push(c.to_ascii_lowercase());
        } else {
            result.push(c);
        }
    }

    result
}

/// Check if a module name is a known Elixir standard library, popular
/// third-party package, or framework module.
///
/// Elixir ships with a well-defined standard library.  Phoenix, Ecto, and other
/// popular packages are also treated as external.
pub fn is_elixir_external(module_name: &str) -> bool {
    if module_name.is_empty() {
        return false;
    }

    // Relative references are always local
    if module_name.starts_with('.') {
        return false;
    }

    let root = module_name.split('.').next().unwrap_or(module_name);

    // Elixir standard library (core Kernel modules + stdlib)
    let elixir_stdlib: &[&str] = &[
        // Kernel and special forms
        "Kernel", "Kernel\\SpecialForms",
        // Core modules
        "Access", "Agent", "Application", "Atom", "Base", "Behaviour",
        "Bitwise", "Calendar", "Code", "Collectable", "Compiler",
        "Date", "DateTime", "Dict", "DynamicSupervisor", "Enum",
        "Enumerable", "Exception", "File", "Float", "Function",
        "GenEvent", "GenServer", "GenStatem", "HashDict", "HashSet",
        "IO", "Inspect", "Integer", "Keyword", "List", "Logger",
        "Macro", "Map", "MapSet", "Module", "NaiveDateTime", "Node",
        "OptionParser", "Path", "Port", "Process", "Protocol",
        "Range", "Record", "Regex", "Registry", "Set", "Stream",
        "String", "StringIO", "Supervisor", "System", "Task",
        "Time", "Tuple", "URI", "Version",
        // Mix (build tool)
        "Mix",
    ];

    if elixir_stdlib.contains(&root) {
        return true;
    }

    // Popular Elixir third-party packages
    let third_party_roots: &[&str] = &[
        // Phoenix framework
        "Phoenix", "Plug",
        // Ecto (database)
        "Ecto",
        // Other popular libs
        "Eex", "ExUnit", "Earmark", "ExDoc",
        "Absinthe", "Jason", "Poison",
        "Broadway", "Flow", "Genstage",
        "NimbleCSV", "NimbleParsec", "NimbleOptions",
        "Telemetry", "TelemetryMetrics", "TelemetryPoller",
        "Finch", "Req", "HTTPoison", "Tesla",
        "Swoosh", "Bamboo",
        "Oban", "Quantum",
        "Credo", "Sobelow", "Dialyxir",
        "Wallaby", "Hound",
        "Comeonin", "Argon2", "Bcrypt",
        "Cachex", "ConCache", "Nebulex",
        "Mox", "Mimic", "ExMachina", "Faker",
        "Timex", "VegaLite", "Kino",
        "Bandit", "Cowboy", "WebSock",
        "Ash", "Oban", "Guardian",
        // GraphQL
        "Absinthe",
        // LiveView
        "LiveView", "LiveComponent", "Surface",
        // Broadway
        "Broadway",
    ];

    if third_party_roots.contains(&root) {
        return true;
    }

    // Check common two-level prefixes (e.g., "Phoenix.LiveView")
    let two_level = module_name.split('.').take(2).collect::<Vec<_>>().join(".");
    let third_party_prefixes: &[&str] = &[
        "Phoenix.LiveView", "Phoenix.HTML", "Phoenix.PubSub",
        "Phoenix.Ecto", "Phoenix.Swoosh",
        "Plug.Conn", "Plug.CSRF",
        "Ecto.SQL", "Ecto.Schema", "Ecto.Query", "Ecto.Changeset",
        "Absinthe.Schema", "Absinthe.Middleware",
    ];

    for prefix in third_party_prefixes {
        if module_name.starts_with(prefix) {
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

    // ------------------------------------------------------------------
    // C# resolver tests (v7.3.0)
    // ------------------------------------------------------------------

    #[test]
    fn test_is_csharp_external_system() {
        assert!(is_csharp_external("System"));
        assert!(is_csharp_external("System.Collections.Generic"));
        assert!(is_csharp_external("System.Threading.Tasks"));
        assert!(is_csharp_external("System.Linq"));
        assert!(is_csharp_external("System.Text.Json"));
    }

    #[test]
    fn test_is_csharp_external_microsoft() {
        assert!(is_csharp_external("Microsoft.Extensions.DependencyInjection"));
        assert!(is_csharp_external("Microsoft.Extensions.Logging"));
        assert!(is_csharp_external("Microsoft.AspNetCore.Mvc"));
        assert!(is_csharp_external("Microsoft.EntityFrameworkCore"));
    }

    #[test]
    fn test_is_csharp_external_third_party() {
        assert!(is_csharp_external("Newtonsoft.Json"));
        assert!(is_csharp_external("Dapper"));
        assert!(is_csharp_external("Serilog"));
        assert!(is_csharp_external("AutoMapper"));
        assert!(is_csharp_external("Npgsql"));
        assert!(is_csharp_external("Xunit"));
        assert!(is_csharp_external("Moq"));
        assert!(is_csharp_external("Swashbuckle.AspNetCore"));
    }

    #[test]
    fn test_is_csharp_external_project_code() {
        assert!(!is_csharp_external("MyApp.Services.UserService"));
        assert!(!is_csharp_external("MyApp.Models.Customer"));
        assert!(!is_csharp_external("MyLib.Utils.Helpers"));
        assert!(!is_csharp_external("Contoso.Core.Engine"));
    }

    #[test]
    fn test_is_csharp_external_empty() {
        assert!(!is_csharp_external(""));
    }

    #[test]
    fn test_csharp_resolver_language() {
        let resolver = CSharpResolver;
        assert_eq!(resolver.language(), "csharp");
    }

    #[test]
    fn test_csharp_resolver_file_to_module() {
        let resolver = CSharpResolver;
        assert_eq!(
            resolver.file_to_module_name("src/Services/UserService.cs", Path::new(".")),
            Some("Services.UserService".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/Models/Customer.cs", Path::new(".")),
            Some("Models.Customer".to_string())
        );
        // Non-.cs files return None
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_csharp_resolver_resolve_module_empty() {
        let resolver = CSharpResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "MyApp.Services.UserService",
            "src/Test.cs",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_csharp_resolver_resolve_module_with_index() {
        use crate::db::hash_id;
        use crate::db::Database;
        use std::time::{SystemTime, UNIX_EPOCH};

        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let db_path = std::env::temp_dir().join("tws_csharp_resolver_test.db");
        let _ = std::fs::remove_file(&db_path);
        let db = Database::initialize(&db_path).unwrap();

        // Insert nodes for C# files
        let paths = vec![
            ("src/Services/UserService.cs", "Services.UserService"),
            ("src/Models/Customer.cs", "Models.Customer"),
            ("src/Utils/Helpers.cs", "Utils.Helpers"),
        ];
        for (file_path, module_name) in &paths {
            let id = hash_id(file_path, &format!("{}::{}", file_path, module_name));
            db.connection().execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, updated_at) VALUES (?1, 'class', ?2, ?3, ?4, 'csharp', 1, 1, ?5)",
                rusqlite::params![id, module_name, format!("{}::{}", file_path, module_name), file_path, ts],
            ).unwrap();
        }

        let index = ModuleIndex::build(&db).unwrap();

        let resolver = CSharpResolver;
        // Direct lookup by module name
        let candidates = resolver.resolve_module(
            "Services.UserService",
            "src/Test.cs",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty());

        let _ = std::fs::remove_file(&db_path);
    }

    #[test]
    fn test_language_registry_get_csharp() {
        let r = LanguageRegistry::get("csharp");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "csharp");
    }

    #[test]
    fn test_supported_languages_includes_csharp() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"csharp"));
    }

    // ------------------------------------------------------------------
    // Scala resolver tests (Stage 11)
    // ------------------------------------------------------------------

    #[test]
    fn test_scala_resolver_is_external_stdlib() {
        let resolver = ScalaResolver;
        assert!(resolver.is_external("scala.collection.immutable.List"));
        assert!(resolver.is_external("scala.concurrent.Future"));
        assert!(resolver.is_external("scala.io.Source"));
        assert!(resolver.is_external("scala.math.BigDecimal"));
        // Java stdlib also available in Scala
        assert!(resolver.is_external("java.util.ArrayList"));
        assert!(resolver.is_external("javax.inject.Inject"));
    }

    #[test]
    fn test_scala_resolver_is_external_third_party() {
        let resolver = ScalaResolver;
        // Akka ecosystem
        assert!(resolver.is_external("akka.actor.ActorSystem"));
        assert!(resolver.is_external("akka.http.scaladsl.server.Directives"));
        // Play framework
        assert!(resolver.is_external("play.api.mvc.Controller"));
        // ZIO
        assert!(resolver.is_external("zio.ZIO"));
        // Cats
        assert!(resolver.is_external("cats.effect.IO"));
        // Typelevel
        assert!(resolver.is_external("org.typelevel.cats.effect.IO"));
        // Typesafe config
        assert!(resolver.is_external("com.typesafe.config.ConfigFactory"));
        // Slick (database)
        assert!(resolver.is_external("slick.jdbc.PostgresProfile"));
        // http4s
        assert!(resolver.is_external("http4s.HttpRoutes"));
        // Spring (Scala can use Java frameworks)
        assert!(resolver.is_external("org.springframework.stereotype.Service"));
    }

    #[test]
    fn test_scala_resolver_not_external_project_package() {
        let resolver = ScalaResolver;
        // Project-internal packages should NOT be external
        assert!(!resolver.is_external("com.mycompany.app.MyClass"));
        assert!(!resolver.is_external("com.example.service.UserService"));
        assert!(!resolver.is_external("models.Customer"));
        assert!(!resolver.is_external("services.AuthService"));
    }

    #[test]
    fn test_scala_resolver_empty_module_name() {
        let resolver = ScalaResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_scala_resolver_language() {
        let resolver = ScalaResolver;
        assert_eq!(resolver.language(), "scala");
    }

    #[test]
    fn test_scala_resolver_file_to_module_name() {
        let resolver = ScalaResolver;
        // Scala files under Maven/Gradle/SBT layout
        assert_eq!(
            resolver.file_to_module_name("src/main/scala/com/foo/bar/MyClass.scala", Path::new(".")),
            Some("com.foo.bar.MyClass".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/test/scala/com/foo/bar/MySpec.scala", Path::new(".")),
            Some("com.foo.bar.MySpec".to_string())
        );
        // Simple src/ layout
        assert_eq!(
            resolver.file_to_module_name("src/com/example/Utils.scala", Path::new(".")),
            Some("com.example.Utils".to_string())
        );
        // Non-Scala files return None
        assert_eq!(
            resolver.file_to_module_name("src/com/example/Utils.java", Path::new(".")),
            None
        );
        assert_eq!(
            resolver.file_to_module_name("src/com/example/Utils.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_scala_resolver_resolve_module_empty_index() {
        let resolver = ScalaResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "com.foo.bar.MyClass",
            "src/test.scala",
            Path::new("."),
            &index,
        );
        // Should return candidate paths even without index data
        assert!(!candidates.is_empty(), "Expected candidate paths from empty ModuleIndex");
        assert!(candidates.contains(
            &"src/main/scala/com/foo/bar/MyClass.scala".to_string()
        ));
    }

    #[test]
    fn test_scala_resolver_resolve_module_with_index() {
        use crate::db::hash_id;
        use crate::db::Database;
        use std::time::{SystemTime, UNIX_EPOCH};

        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let db_path = std::env::temp_dir().join("tws_scala_resolver_test.db");
        let _ = std::fs::remove_file(&db_path);
        let db = Database::initialize(&db_path).unwrap();

        // Insert nodes for Scala files
        let paths = vec![
            ("src/main/scala/com/foo/bar/MyClass.scala", "com.foo.bar.MyClass"),
            ("src/main/scala/com/foo/baz/Other.scala", "com.foo.baz.Other"),
            ("src/main/scala/com/example/App.scala", "com.example.App"),
        ];
        for (file_path, module_name) in &paths {
            let id = hash_id(file_path, &format!("{}::{}", file_path, module_name));
            db.connection().execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, updated_at) VALUES (?1, 'class', ?2, ?3, ?4, 'scala', 1, 1, ?5)",
                rusqlite::params![id, module_name, format!("{}::{}", file_path, module_name), file_path, ts],
            ).unwrap();
        }

        let index = ModuleIndex::build(&db).unwrap();

        let resolver = ScalaResolver;
        // Direct lookup by module name
        let candidates = resolver.resolve_module(
            "com.foo.bar.MyClass",
            "src/test.scala",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty(), "Expected resolved candidates for known module");
        assert!(
            candidates.contains(&"src/main/scala/com/foo/bar/MyClass.scala".to_string()),
            "Expected direct match: {:?}", candidates
        );

        let _ = std::fs::remove_file(&db_path);
    }

    #[test]
    fn test_language_registry_get_scala() {
        let r = LanguageRegistry::get("scala");
        assert!(r.is_some());
        assert_eq!(r.unwrap().language(), "scala");
    }

    #[test]
    fn test_supported_languages_includes_scala() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"scala"));
    }

    // ------------------------------------------------------------------
    // Dart resolver tests (Stage 12)
    // ------------------------------------------------------------------

    #[test]
    fn test_dart_resolver_is_external_dart_sdk() {
        let resolver = DartResolver;
        assert!(resolver.is_external("dart:core"));
        assert!(resolver.is_external("dart:convert"));
        assert!(resolver.is_external("dart:async"));
        assert!(resolver.is_external("dart:io"));
        assert!(resolver.is_external("dart:math"));
        assert!(resolver.is_external("dart:collection"));
    }

    #[test]
    fn test_dart_resolver_is_external_flutter() {
        let resolver = DartResolver;
        assert!(resolver.is_external("package:flutter/material.dart"));
        assert!(resolver.is_external("package:flutter/widgets.dart"));
        assert!(resolver.is_external("package:flutter_test/flutter_test.dart"));
    }

    #[test]
    fn test_dart_resolver_is_external_known_packages() {
        let resolver = DartResolver;
        assert!(resolver.is_external("package:provider/provider.dart"));
        assert!(resolver.is_external("package:http/http.dart"));
        assert!(resolver.is_external("package:sqflite/sqflite.dart"));
        assert!(resolver.is_external("package:freezed_annotation/freezed_annotation.dart"));
    }

    #[test]
    fn test_dart_resolver_not_external_project_package() {
        let resolver = DartResolver;
        // Project's own package imports
        assert!(!resolver.is_external("package:my_app/models/user.dart"));
        assert!(!resolver.is_external("package:my_app/utils/helpers.dart"));
        assert!(!resolver.is_external("package:my_app/main.dart"));
        // Relative imports
        assert!(!resolver.is_external("models/user.dart"));
        assert!(!resolver.is_external("utils/helpers.dart"));
        assert!(!resolver.is_external("lib/src/core.dart"));
    }

    #[test]
    fn test_dart_resolver_empty_module_name() {
        let resolver = DartResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_dart_resolver_language() {
        let resolver = DartResolver;
        assert_eq!(resolver.language(), "dart");
    }

    #[test]
    fn test_dart_resolver_file_to_module_name() {
        let resolver = DartResolver;
        assert_eq!(
            resolver.file_to_module_name("lib/src/models/user.dart", Path::new(".")),
            Some("src.models.user".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("lib/widgets/button.dart", Path::new(".")),
            Some("widgets.button".to_string())
        );
        // Non-Dart files return None
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
        assert_eq!(
            resolver.file_to_module_name("src/foo.java", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_dart_resolver_resolve_package_module() {
        let resolver = DartResolver;
        let index = ModuleIndex::empty();
        // Even with empty ModuleIndex, should generate candidate lib/ paths
        let candidates = resolver.resolve_module(
            "package:my_app/models/user.dart",
            "lib/main.dart",
            Path::new("."),
            &index,
        );
        assert!(!candidates.is_empty(), "Expected candidate paths for package import");
        assert!(candidates.contains(&"lib/models/user.dart".to_string()),
            "Expected lib/models/user.dart, got: {:?}", candidates);
    }

    #[test]
    fn test_dart_resolver_resolve_dart_sdk_empty() {
        let resolver = DartResolver;
        let index = ModuleIndex::empty();
        // dart: imports should return empty (external)
        let candidates = resolver.resolve_module(
            "dart:core",
            "lib/main.dart",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty(), "dart: imports should return no candidates");
    }

    #[test]
    fn test_dart_resolver_resolve_relative_import() {
        let resolver = DartResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "models/user.dart",
            "lib/main.dart",
            Path::new("."),
            &index,
        );
        // Should resolve relative to source file directory
        assert!(!candidates.is_empty(), "Expected candidate paths for relative import");
        assert!(candidates.contains(&"lib/models/user.dart".to_string()),
            "Expected lib/models/user.dart, got: {:?}", candidates);
    }

    #[test]
    fn test_dart_resolver_resolve_relative_with_parent() {
        let resolver = DartResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "../utils/helpers.dart",
            "lib/src/main.dart",
            Path::new("."),
            &index,
        );
        // Should resolve to lib/utils/helpers.dart (one level up from lib/src/)
        assert!(!candidates.is_empty(), "Expected candidates for parent-relative import");
        assert!(candidates.contains(&"lib/utils/helpers.dart".to_string()),
            "Expected lib/utils/helpers.dart, got: {:?}", candidates);
    }

    #[test]
    fn test_dart_resolver_resolve_empty_module() {
        let resolver = DartResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "lib/main.dart",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_language_registry_get_dart() {
        let r = LanguageRegistry::get("dart");
        assert!(r.is_some(), "Expected Dart resolver in registry");
        assert_eq!(r.unwrap().language(), "dart");
    }

    #[test]
    fn test_supported_languages_includes_dart() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"dart"), "Expected 'dart' in supported languages");
    }

    #[test]
    fn test_simplify_dart_path() {
        assert_eq!(simplify_dart_path("lib/models/user.dart"), "lib/models/user.dart");
        assert_eq!(simplify_dart_path("lib/./models/user.dart"), "lib/models/user.dart");
        assert_eq!(simplify_dart_path("lib/src/../models/user.dart"), "lib/models/user.dart");
        assert_eq!(simplify_dart_path("./utils.dart"), "utils.dart");
    }

    // ------------------------------------------------------------------
    // Swift resolver tests (Stage 13)
    // ------------------------------------------------------------------

    #[test]
    fn test_swift_resolver_is_external_apple_frameworks() {
        let resolver = SwiftResolver;
        assert!(resolver.is_external("UIKit"));
        assert!(resolver.is_external("Foundation"));
        assert!(resolver.is_external("SwiftUI"));
        assert!(resolver.is_external("AppKit"));
        assert!(resolver.is_external("Combine"));
        assert!(resolver.is_external("Swift"));
        assert!(resolver.is_external("CoreData"));
        assert!(resolver.is_external("AVFoundation"));
        assert!(resolver.is_external("MapKit"));
        assert!(resolver.is_external("Metal"));
        assert!(resolver.is_external("WebKit"));
        assert!(resolver.is_external("SceneKit"));
    }

    #[test]
    fn test_swift_resolver_is_external_third_party() {
        let resolver = SwiftResolver;
        assert!(resolver.is_external("Alamofire"));
        assert!(resolver.is_external("Kingfisher"));
        assert!(resolver.is_external("SnapKit"));
        assert!(resolver.is_external("RxSwift"));
        assert!(resolver.is_external("Lottie"));
        assert!(resolver.is_external("Firebase"));
    }

    #[test]
    fn test_swift_resolver_not_external_project_module() {
        let resolver = SwiftResolver;
        assert!(!resolver.is_external("MyApp"));
        assert!(!resolver.is_external("Models.User"));
        assert!(!resolver.is_external("Services.AuthManager"));
        assert!(!resolver.is_external("AppDelegate"));
    }

    #[test]
    fn test_swift_resolver_empty_module_name() {
        let resolver = SwiftResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_swift_resolver_language() {
        let resolver = SwiftResolver;
        assert_eq!(resolver.language(), "swift");
    }

    #[test]
    fn test_swift_resolver_file_to_module_name() {
        let resolver = SwiftResolver;
        let result = resolver.file_to_module_name("Sources/Models/User.swift", Path::new("."));
        assert_eq!(result, Some("Models.User".to_string()));
        let result2 = resolver.file_to_module_name("src/main.swift", Path::new("."));
        assert_eq!(result2, Some("main".to_string()));
    }

    #[test]
    fn test_swift_resolver_resolve_module_empty_index() {
        let index = ModuleIndex::empty();
        let resolver = SwiftResolver;
        // Module not in index → no candidate files
        let result = resolver.resolve_module("Models.User", "src/main.swift", Path::new("."), &index);
        assert!(result.is_empty());
    }

    #[test]
    fn test_swift_resolver_resolve_module_with_index() {
        use crate::db::Database;
        use std::time::{SystemTime, UNIX_EPOCH};
        use crate::db::hash_id;

        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let db_path = std::env::temp_dir().join("tws_swift_resolver_test.db");
        let _ = std::fs::remove_file(&db_path);
        let db = Database::initialize(&db_path).unwrap();

        // Insert Swift nodes
        let paths = vec![
            ("Sources/Models/User.swift", "Models.User"),
            ("Sources/Services/AuthManager.swift", "Services.AuthManager"),
        ];
        for (file_path, module_name) in &paths {
            let id = hash_id(file_path, &format!("{}::{}", file_path, module_name));
            db.connection().execute(
                "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, updated_at) VALUES (?1, 'class', ?2, ?3, ?4, 'swift', 1, 1, ?5)",
                rusqlite::params![id, module_name, format!("{}::{}", file_path, module_name), file_path, ts],
            ).unwrap();
        }

        let index = ModuleIndex::build(&db).unwrap();
        let resolver = SwiftResolver;

        // Look up by module name
        let result = resolver.resolve_module("Models.User", "src/main.swift", Path::new("."), &index);
        assert!(!result.is_empty(), "Should find Sources/Models/User.swift");
        assert!(result.iter().any(|f| f.contains("User.swift")));

        let _ = std::fs::remove_file(&db_path);
    }

    #[test]
    fn test_swift_resolver_resolve_module_with_prefix_variants() {
        use crate::db::Database;
        use std::time::{SystemTime, UNIX_EPOCH};
        use crate::db::hash_id;

        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let db_path = std::env::temp_dir().join("tws_swift_prefix_test.db");
        let _ = std::fs::remove_file(&db_path);
        let db = Database::initialize(&db_path).unwrap();

        // Insert node
        let file_path = "Sources/Features/Auth/Login.swift";
        let module_name = "Features.Auth.Login";
        let id = hash_id(file_path, &format!("{}::{}", file_path, module_name));
        db.connection().execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, updated_at) VALUES (?1, 'class', ?2, ?3, ?4, 'swift', 1, 1, ?5)",
            rusqlite::params![id, module_name, format!("{}::{}", file_path, module_name), file_path, ts],
        ).unwrap();

        let index = ModuleIndex::build(&db).unwrap();
        let resolver = SwiftResolver;

        // Try without Sources prefix
        let result = resolver.resolve_module("Features.Auth.Login", "src/main.swift", Path::new("."), &index);
        assert!(!result.is_empty(), "Should find with Sources prefix");
        assert!(result.iter().any(|f| f.contains("Login.swift")));

        let _ = std::fs::remove_file(&db_path);
    }

    #[test]
    fn test_swift_resolver_resolve_empty_module() {
        let index = ModuleIndex::empty();
        let resolver = SwiftResolver;
        let result = resolver.resolve_module("", "src/main.swift", Path::new("."), &index);
        assert!(result.is_empty());
    }

    #[test]
    fn test_swift_resolver_apple_framework_module_segments() {
        // Test first-segment matching for dotted module names
        let resolver = SwiftResolver;
        assert!(resolver.is_external("UIKit.UIViewController"));
        assert!(resolver.is_external("Foundation.NSObject"));
        assert!(resolver.is_external("SwiftUI.View"));
        assert!(resolver.is_external("Combine.Publisher"));
    }

    #[test]
    fn test_language_registry_get_swift() {
        let r = LanguageRegistry::get("swift");
        assert!(r.is_some(), "Expected Swift resolver to be registered");
        assert_eq!(r.unwrap().language(), "swift");
    }

    #[test]
    fn test_supported_languages_includes_swift() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"swift"), "Expected 'swift' in supported languages");
    }

    // ------------------------------------------------------------------
    // Lua resolver tests (Stage 14)
    // ------------------------------------------------------------------

    #[test]
    fn test_lua_resolver_is_external_stdlib() {
        let resolver = LuaResolver;
        assert!(resolver.is_external("string"));
        assert!(resolver.is_external("table"));
        assert!(resolver.is_external("math"));
        assert!(resolver.is_external("io"));
        assert!(resolver.is_external("os"));
        assert!(resolver.is_external("coroutine"));
        assert!(resolver.is_external("debug"));
        assert!(resolver.is_external("package"));
        assert!(resolver.is_external("utf8"));
    }

    #[test]
    fn test_lua_resolver_is_external_nested() {
        let resolver = LuaResolver;
        // "socket.http" → root "socket" is third-party
        assert!(resolver.is_external("socket.http"));
        // "pl.stringx" → root "pl" is third-party (Penlight)
        assert!(resolver.is_external("pl.stringx"));
    }

    #[test]
    fn test_lua_resolver_not_external_project_module() {
        let resolver = LuaResolver;
        assert!(!resolver.is_external("myproject.utils"));
        assert!(!resolver.is_external("src.main"));
        assert!(!resolver.is_external("app.core"));
        assert!(!resolver.is_external("mymodule"));
    }

    #[test]
    fn test_lua_resolver_empty_module_name() {
        let resolver = LuaResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_lua_resolver_language() {
        let resolver = LuaResolver;
        assert_eq!(resolver.language(), "lua");
    }

    #[test]
    fn test_lua_resolver_file_to_module_name() {
        let resolver = LuaResolver;
        assert_eq!(
            resolver.file_to_module_name("src/mymod.lua", Path::new(".")),
            Some("mymod".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("lib/http/request.lua", Path::new(".")),
            Some("http.request".to_string())
        );
    }

    #[test]
    fn test_lua_resolver_resolve_module_empty_index() {
        let resolver = LuaResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "mymod",
            "src/main.lua",
            Path::new("."),
            &index,
        );
        // Should generate candidate file paths
        assert!(!candidates.is_empty(), "Expected candidate paths for Lua module");
        assert!(candidates.contains(&"src/mymod.lua".to_string()));
        assert!(candidates.contains(&"src/mymod/init.lua".to_string()));
    }

    #[test]
    fn test_lua_resolver_resolve_module_generates_candidates_with_empty_index() {
        let resolver = LuaResolver;
        let index = ModuleIndex::empty();

        // For "http", the resolver should generate candidate paths
        let candidates = resolver.resolve_module(
            "http",
            "src/main.lua",
            Path::new("."),
            &index,
        );
        // Should generate src/http.lua, lib/http.lua, lua/http.lua, http.lua,
        // and init.lua variants for each
        assert!(!candidates.is_empty(), "Expected candidate paths for Lua module");
        assert!(candidates.contains(&"src/http.lua".to_string()), "Expected src/http.lua candidate");
        assert!(candidates.contains(&"src/http/init.lua".to_string()), "Expected src/http/init.lua candidate");
        assert!(candidates.contains(&"http.lua".to_string()), "Expected http.lua candidate");
    }

    #[test]
    fn test_lua_resolver_resolve_nested_module_candidates() {
        let resolver = LuaResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "foo.bar.baz",
            "src/main.lua",
            Path::new("."),
            &index,
        );
        // Should generate foo/bar/baz.lua and foo/bar/baz/init.lua candidates
        assert!(candidates.contains(&"src/foo/bar/baz.lua".to_string()));
        assert!(candidates.contains(&"src/foo/bar/baz/init.lua".to_string()));
        // Also without src prefix
        assert!(candidates.contains(&"foo/bar/baz.lua".to_string()));
        assert!(candidates.contains(&"foo/bar/baz/init.lua".to_string()));
    }

    #[test]
    fn test_lua_resolver_resolve_empty_module() {
        let resolver = LuaResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "src/main.lua",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty(), "Empty module should return no candidates");
    }

    #[test]
    fn test_language_registry_get_lua() {
        let r = LanguageRegistry::get("lua");
        assert!(r.is_some(), "Expected Lua resolver to be registered");
        assert_eq!(r.unwrap().language(), "lua");
    }

    #[test]
    fn test_supported_languages_includes_lua() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"lua"), "Expected 'lua' in supported languages");
    }

    #[test]
    fn test_is_lua_external_third_party_luarocks() {
        assert!(is_lua_external("luasocket"));
        assert!(is_lua_external("lpeg"));
        assert!(is_lua_external("luafilesystem"));
        assert!(is_lua_external("penlight"));
        assert!(is_lua_external("busted"));
    }

    // ------------------------------------------------------------------
    // Bash resolver tests (Stage 15)
    // ------------------------------------------------------------------

    #[test]
    fn test_bash_resolver_is_external_system_paths() {
        let resolver = BashResolver;
        assert!(resolver.is_external("/etc/profile"));
        assert!(resolver.is_external("/usr/share/bash-completion"));
        assert!(resolver.is_external("/opt/custom/script.sh"));
    }

    #[test]
    fn test_bash_resolver_not_external_local_files() {
        let resolver = BashResolver;
        assert!(!resolver.is_external("lib/utils.sh"));
        assert!(!resolver.is_external("src/config.sh"));
        assert!(!resolver.is_external("helpers.sh"));
        assert!(!resolver.is_external("dir/sub/script.sh"));
    }

    #[test]
    fn test_bash_resolver_not_external_relative_paths() {
        let resolver = BashResolver;
        assert!(!resolver.is_external("./config.sh"));
        assert!(!resolver.is_external("../lib/utils.sh"));
    }

    #[test]
    fn test_bash_resolver_empty_module_name() {
        let resolver = BashResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_bash_resolver_language() {
        let resolver = BashResolver;
        assert_eq!(resolver.language(), "bash");
    }

    #[test]
    fn test_bash_resolver_file_to_module_name() {
        let resolver = BashResolver;
        // Should delegate to infer_module_name("bash")
        let result = resolver.file_to_module_name("src/lib/utils.sh", Path::new("."));
        assert_eq!(result, Some("lib/utils.sh".to_string()));
        let result2 = resolver.file_to_module_name("config.sh", Path::new("."));
        assert_eq!(result2, Some("config.sh".to_string()));
    }

    #[test]
    fn test_bash_resolver_resolve_module_empty_index() {
        let resolver = BashResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "lib/utils.sh",
            "src/test.sh",
            Path::new("."),
            &index,
        );
        // Should generate candidate file paths
        assert!(!candidates.is_empty(), "Expected candidate paths for Bash module");
        assert!(candidates.contains(&"src/lib/utils.sh".to_string()),
            "Expected src/lib/utils.sh candidate, got: {:?}", candidates);
        assert!(candidates.contains(&"lib/utils.sh".to_string()),
            "Expected lib/utils.sh candidate, got: {:?}", candidates);
    }

    #[test]
    fn test_bash_resolver_resolve_module_with_index() {
        let resolver = BashResolver;
        let mut index = ModuleIndex::empty();
        // Simulate adding a file to the index
        index.insert("lib/utils.sh", "src/lib/utils.sh".to_string());

        let candidates = resolver.resolve_module(
            "lib/utils.sh",
            "src/test.sh",
            Path::new("."),
            &index,
        );
        assert_eq!(candidates.len(), 1, "Expected 1 candidate from index");
        assert_eq!(candidates[0], "src/lib/utils.sh");
    }

    #[test]
    fn test_bash_resolver_resolve_relative_path() {
        let resolver = BashResolver;
        let mut index = ModuleIndex::empty();
        // source ./config.sh from src/test.sh → src/config.sh
        index.insert("src/config.sh", "src/config.sh".to_string());

        let candidates = resolver.resolve_module(
            "./config.sh",
            "src/test.sh",
            Path::new("."),
            &index,
        );
        // Should try to resolve ./config.sh → src/config.sh
        assert!(!candidates.is_empty(), "Expected candidates for relative path");
        // The resolved path should contain config.sh
        assert!(
            candidates.iter().any(|c| c.contains("config.sh")),
            "Expected candidates containing config.sh, got: {:?}", candidates
        );
    }

    #[test]
    fn test_bash_resolver_resolve_empty_module() {
        let resolver = BashResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module(
            "",
            "src/test.sh",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty(), "Empty module should return no candidates");
    }

    #[test]
    fn test_bash_resolver_resolve_strips_prefix() {
        let resolver = BashResolver;
        let mut index = ModuleIndex::empty();
        // Index has stripped path, module_name already has src/ prefix
        index.insert("lib/utils.sh", "src/lib/utils.sh".to_string());

        let candidates = resolver.resolve_module(
            "src/lib/utils.sh",
            "src/test.sh",
            Path::new("."),
            &index,
        );
        // Should strip src/ and find the entry
        assert!(!candidates.is_empty(), "Expected candidates after prefix stripping");
        assert!(
            candidates.iter().any(|c| c.contains("utils.sh")),
            "Expected candidates containing utils.sh, got: {:?}", candidates
        );
    }

    #[test]
    fn test_language_registry_get_bash() {
        let r = LanguageRegistry::get("bash");
        assert!(r.is_some(), "Expected Bash resolver to be registered");
        assert_eq!(r.unwrap().language(), "bash");
    }

    #[test]
    fn test_supported_languages_includes_bash() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"bash"), "Expected 'bash' in supported languages");
    }

    #[test]
    fn test_is_bash_external_function() {
        assert!(is_bash_external("/etc/profile"));
        assert!(is_bash_external("/usr/local/bin/script"));
        assert!(!is_bash_external("lib/utils.sh"));
        assert!(!is_bash_external(""));
    }

    // ------------------------------------------------------------------
    // Groovy resolver tests (Stage 16)
    // ------------------------------------------------------------------

    #[test]
    fn test_groovy_resolver_is_external_groovy_stdlib() {
        let resolver = GroovyResolver;
        assert!(resolver.is_external("groovy.json.JsonSlurper"));
        assert!(resolver.is_external("groovy.xml.MarkupBuilder"));
        assert!(resolver.is_external("org.codehaus.groovy.runtime"));
    }

    #[test]
    fn test_groovy_resolver_is_external_java_stdlib() {
        let resolver = GroovyResolver;
        assert!(resolver.is_external("java.util.List"));
        assert!(resolver.is_external("javax.servlet.http.HttpServlet"));
        assert!(resolver.is_external("jakarta.ws.rs.GET"));
    }

    #[test]
    fn test_groovy_resolver_is_external_third_party() {
        let resolver = GroovyResolver;
        assert!(resolver.is_external("org.springframework.beans.factory"));
        assert!(resolver.is_external("com.google.common.collect"));
        assert!(resolver.is_external("org.apache.commons.lang3"));
    }

    #[test]
    fn test_groovy_resolver_not_external_project_package() {
        let resolver = GroovyResolver;
        assert!(!resolver.is_external("com.myproject.Service"));
        assert!(!resolver.is_external("myapp.Utils"));
        assert!(!resolver.is_external("scripts.deploy"));
    }

    #[test]
    fn test_groovy_resolver_language() {
        let resolver = GroovyResolver;
        assert_eq!(resolver.language(), "groovy");
    }

    #[test]
    fn test_groovy_resolver_file_to_module_name() {
        let resolver = GroovyResolver;
        // Should delegate to infer_module_name("groovy")
        assert_eq!(
            resolver.file_to_module_name("src/main/groovy/com/foo/Bar.groovy", Path::new(".")),
            Some("com.foo.Bar".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/com/example/Utils.groovy", Path::new(".")),
            Some("com.example.Utils".to_string())
        );
    }

    #[test]
    fn test_groovy_resolver_empty_module_name() {
        let resolver = GroovyResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module("", "src/App.groovy", Path::new("."), &index);
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_is_groovy_external_function() {
        assert!(is_groovy_external("groovy.lang.Closure"));
        assert!(is_groovy_external("groovyjarjar.asm.Type"));
        assert!(is_groovy_external("java.lang.String"));
        assert!(is_groovy_external("org.codehaus.groovy"));
        assert!(!is_groovy_external("com.mycompany.app"));
        assert!(!is_groovy_external(""));
    }

    #[test]
    fn test_language_registry_get_groovy() {
        let r = LanguageRegistry::get("groovy");
        assert!(r.is_some(), "Expected GroovyResolver to be registered");
        assert_eq!(r.unwrap().language(), "groovy");
    }

    #[test]
    fn test_supported_languages_includes_groovy() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"groovy"), "Expected 'groovy' in supported languages, got: {:?}", langs);
    }

    // ------------------------------------------------------------------
    // Zig resolver tests (Stage 17)
    // ------------------------------------------------------------------

    #[test]
    fn test_zig_resolver_language() {
        let resolver = ZigResolver;
        assert_eq!(resolver.language(), "zig");
    }

    #[test]
    fn test_zig_resolver_is_external_stdlib() {
        let resolver = ZigResolver;
        assert!(resolver.is_external("std"));
        assert!(resolver.is_external("builtin"));
        assert!(resolver.is_external("root"));
        assert!(resolver.is_external("std.mem"));
        assert!(resolver.is_external("std.fs"));
    }

    #[test]
    fn test_zig_resolver_not_external_file_imports() {
        let resolver = ZigResolver;
        // File-based imports are not external
        assert!(!resolver.is_external("utils.zig"));
        assert!(!resolver.is_external("subdir/bar.zig"));
        assert!(!resolver.is_external("foo/bar.zig"));
        assert!(!resolver.is_external("../baz.zig"));
    }

    #[test]
    fn test_zig_resolver_is_external_empty() {
        let resolver = ZigResolver;
        assert!(!resolver.is_external(""));
    }

    #[test]
    fn test_zig_resolver_resolve_relative_to_source_file() {
        // ModuleIndex: src/utils.zig → "utils"
        let mut index = ModuleIndex::empty();
        index.insert("utils", "src/utils.zig".to_string());

        let resolver = ZigResolver;
        // When importing "utils.zig" from src/main.zig,
        // the source_dir is "src", so resolved becomes "src/utils.zig"
        let candidates = resolver.resolve_module(
            "utils.zig",
            "src/main.zig",
            Path::new("."),
            &index,
        );
        // Should find via ModuleIndex lookup of "utils"
        assert!(
            candidates.contains(&"src/utils.zig".to_string()),
            "Expected to find src/utils.zig, got: {:?}", candidates
        );
    }

    #[test]
    fn test_zig_resolver_resolve_module_by_name() {
        // ModuleIndex maps "foo.bar" → "src/foo/bar.zig"
        let mut index = ModuleIndex::empty();
        index.insert("foo.bar", "src/foo/bar.zig".to_string());

        let resolver = ZigResolver;
        let candidates = resolver.resolve_module(
            "foo.bar",
            "src/main.zig",
            Path::new("."),
            &index,
        );
        assert!(
            candidates.contains(&"src/foo/bar.zig".to_string()),
            "Expected to find src/foo/bar.zig, got: {:?}", candidates
        );
    }

    #[test]
    fn test_zig_resolver_resolve_empty_module() {
        let index = ModuleIndex::empty();
        let resolver = ZigResolver;
        let candidates = resolver.resolve_module(
            "",
            "src/main.zig",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_zig_resolver_resolve_subdir_import() {
        // When importing "subdir/bar.zig" from src/main.zig
        let mut index = ModuleIndex::empty();
        index.insert("subdir.bar", "src/subdir/bar.zig".to_string());

        let resolver = ZigResolver;
        let candidates = resolver.resolve_module(
            "subdir/bar.zig",
            "src/main.zig",
            Path::new("."),
            &index,
        );
        // Should find via source_dir resolution: src/subdir/bar.zig
        // Then ModuleIndex lookup of "subdir.bar" should find it
        assert!(
            !candidates.is_empty(),
            "Expected to find subdir/bar.zig, got: {:?}", candidates
        );
    }

    #[test]
    fn test_zig_resolver_file_to_module_name() {
        let resolver = ZigResolver;
        assert_eq!(
            resolver.file_to_module_name("src/main.zig", Path::new(".")),
            Some("main".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/utils.zig", Path::new(".")),
            Some("utils".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/foo/bar.zig", Path::new(".")),
            Some("foo.bar".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/main.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_language_registry_get_zig() {
        let r = LanguageRegistry::get("zig");
        assert!(r.is_some(), "Expected ZigResolver to be registered");
        assert_eq!(r.unwrap().language(), "zig");
    }

    #[test]
    fn test_supported_languages_includes_zig() {
        let langs = LanguageRegistry::supported_languages();
        assert!(langs.contains(&"zig"), "Expected 'zig' in supported languages, got: {:?}", langs);
    }

    #[test]
    fn test_is_zig_external_function() {
        assert!(is_zig_external("std"));
        assert!(is_zig_external("builtin"));
        assert!(is_zig_external("root"));
        assert!(is_zig_external("std.mem"));
        assert!(is_zig_external("std.fs.path"));
        assert!(!is_zig_external("utils.zig"));
        assert!(!is_zig_external("foo/bar.zig"));
        assert!(!is_zig_external("my_project"));
        assert!(!is_zig_external(""));
    }

    #[test]
    fn test_simplify_zig_path_basic() {
        assert_eq!(simplify_zig_path("src/utils.zig"), "src/utils.zig");
        assert_eq!(simplify_zig_path("src/foo/bar.zig"), "src/foo/bar.zig");
    }

    #[test]
    fn test_simplify_zig_path_with_dot_dot() {
        assert_eq!(simplify_zig_path("src/foo/../utils.zig"), "src/utils.zig");
        assert_eq!(simplify_zig_path("src/foo/bar/../../baz.zig"), "src/baz.zig");
    }

    #[test]
    fn test_simplify_zig_path_with_dot() {
        assert_eq!(simplify_zig_path("src/./utils.zig"), "src/utils.zig");
        assert_eq!(simplify_zig_path("./utils.zig"), "utils.zig");
    }

    // ------------------------------------------------------------------
    // Nix resolver tests (Stage 18)
    // ------------------------------------------------------------------

    #[test]
    fn test_nix_resolver_language() {
        let resolver = NixResolver;
        assert_eq!(resolver.language(), "nix");
    }

    #[test]
    fn test_nix_resolver_is_external_angle_bracket() {
        assert!(is_nix_external("<nixpkgs>"));
        assert!(is_nix_external("<nixpkgs/nixos>"));
        assert!(is_nix_external("<home-manager>"));
        assert!(is_nix_external("<nix-darwin>"));
    }

    #[test]
    fn test_nix_resolver_is_external_builtins() {
        assert!(is_nix_external("builtins"));
        assert!(is_nix_external("pkgs"));
        assert!(is_nix_external("lib"));
        assert!(is_nix_external("stdenv"));
        assert!(is_nix_external("nixpkgs"));
    }

    #[test]
    fn test_nix_resolver_is_external_dotted_scopes() {
        assert!(is_nix_external("pkgs.stdenv"));
        assert!(is_nix_external("pkgs.python3"));
        assert!(is_nix_external("lib.lists"));
        assert!(is_nix_external("lib.attrsets"));
    }

    #[test]
    fn test_nix_resolver_is_external_file_paths() {
        // File paths (with .nix extension or /) are internal
        assert!(!is_nix_external("./lib.nix"));
        assert!(!is_nix_external("../utils.nix"));
        assert!(!is_nix_external("pkgs/default.nix"));
        assert!(!is_nix_external("modules/services/nginx.nix"));
    }

    #[test]
    fn test_nix_resolver_is_external_empty() {
        assert!(!is_nix_external(""));
    }

    #[test]
    fn test_nix_resolver_file_to_module_name() {
        let resolver = NixResolver;
        assert_eq!(
            resolver.file_to_module_name("src/pkgs/default.nix", Path::new(".")),
            Some("pkgs/default".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("lib/helpers.nix", Path::new(".")),
            Some("helpers".to_string())
        );
    }

    #[test]
    fn test_nix_resolver_resolve_relative_path() {
        let resolver = NixResolver;
        let mut index = ModuleIndex::empty();

        // Registration: "src/lib.nix" → module "lib"
        index.insert("lib", "src/lib.nix".to_string());

        // resolve_module("./lib.nix") from "src/default.nix"
        let candidates = resolver.resolve_module(
            "./lib.nix",
            "src/default.nix",
            Path::new("."),
            &index,
        );
        // Should find src/lib.nix
        assert!(candidates.contains(&"src/lib.nix".to_string()),
            "Expected src/lib.nix in candidates, got {:?}", candidates);
    }

    #[test]
    fn test_nix_resolver_resolve_angle_bracket_external() {
        let resolver = NixResolver;
        let index = ModuleIndex::empty();

        // <nixpkgs> style imports return empty candidates (external)
        let candidates = resolver.resolve_module(
            "<nixpkgs>",
            "src/default.nix",
            Path::new("."),
            &index,
        );
        assert!(candidates.is_empty(),
            "Angle-bracket imports should have no local candidates");
    }

    #[test]
    fn test_nix_resolver_resolve_direct_module_name() {
        let resolver = NixResolver;
        let mut index = ModuleIndex::empty();

        // Registration: "pkgs/default.nix" → module "pkgs/default"
        index.insert("pkgs/default", "pkgs/default.nix".to_string());

        // resolve_module("pkgs/default") directly
        let candidates = resolver.resolve_module(
            "pkgs/default",
            "src/default.nix",
            Path::new("."),
            &index,
        );
        assert!(candidates.contains(&"pkgs/default.nix".to_string()),
            "Expected pkgs/default.nix in candidates, got {:?}", candidates);
    }

    #[test]
    fn test_nix_resolver_resolve_with_prefix_fallback() {
        let resolver = NixResolver;
        let mut index = ModuleIndex::empty();

        // Registration: "src/modules/test.nix" → module "modules/test"
        index.insert("modules/test", "src/modules/test.nix".to_string());

        // resolve_module("src/modules/test") should find it via prefix fallback
        let candidates = resolver.resolve_module(
            "src/modules/test",
            "src/default.nix",
            Path::new("."),
            &index,
        );
        assert!(candidates.contains(&"src/modules/test.nix".to_string()) ||
                candidates.contains(&"modules/test.nix".to_string()),
            "Expected to find modules/test, got {:?}", candidates);
    }

    #[test]
    fn test_language_registry_get_nix() {
        let resolver = LanguageRegistry::get("nix");
        assert!(resolver.is_some(), "Expected NixResolver in LanguageRegistry");
        assert_eq!(resolver.unwrap().language(), "nix");
    }

    #[test]
    fn test_simplify_nix_path_basic() {
        assert_eq!(simplify_nix_path("src/./utils.nix"), "src/utils.nix");
        assert_eq!(simplify_nix_path("src/../lib/utils.nix"), "lib/utils.nix");
        assert_eq!(simplify_nix_path("./utils.nix"), "utils.nix");
        assert_eq!(simplify_nix_path("src/lib/../default.nix"), "src/default.nix");
    }

    // ------------------------------------------------------------------
    // Elixir resolver tests (Stage 19)
    // ------------------------------------------------------------------

    #[test]
    fn test_elixir_resolver_language() {
        let resolver = ElixirResolver;
        assert_eq!(resolver.language(), "elixir");
    }

    #[test]
    fn test_is_elixir_external_stdlib() {
        assert!(is_elixir_external("Enum"));
        assert!(is_elixir_external("String"));
        assert!(is_elixir_external("List"));
        assert!(is_elixir_external("Map"));
        assert!(is_elixir_external("Logger"));
        assert!(is_elixir_external("GenServer"));
        assert!(is_elixir_external("Mix"));
    }

    #[test]
    fn test_is_elixir_external_third_party() {
        assert!(is_elixir_external("Phoenix"));
        assert!(is_elixir_external("Ecto"));
        assert!(is_elixir_external("Plug"));
        assert!(is_elixir_external("Jason"));
        assert!(is_elixir_external("Absinthe"));
    }

    #[test]
    fn test_is_elixir_external_two_level() {
        assert!(is_elixir_external("Phoenix.LiveView"));
        assert!(is_elixir_external("Phoenix.LiveView.Engine"));
        assert!(is_elixir_external("Ecto.Schema"));
        assert!(is_elixir_external("Ecto.Query"));
        assert!(is_elixir_external("Plug.Conn"));
    }

    #[test]
    fn test_is_elixir_external_not_external() {
        // Project-local modules should NOT be external
        assert!(!is_elixir_external("MyApp"));
        assert!(!is_elixir_external("MyApp.Services.User"));
        assert!(!is_elixir_external("Internal"));
    }

    #[test]
    fn test_is_elixir_external_empty() {
        assert!(!is_elixir_external(""));
    }

    #[test]
    fn test_elixir_resolver_file_to_module_name() {
        let resolver = ElixirResolver;
        assert_eq!(
            resolver.file_to_module_name("lib/my_app/services/user.ex", Path::new(".")),
            Some("MyApp.Services.User".to_string())
        );
        assert_eq!(
            resolver.file_to_module_name("src/worker.ex", Path::new(".")),
            Some("Worker".to_string())
        );
        // Non-Elixir files
        assert_eq!(
            resolver.file_to_module_name("src/foo.py", Path::new(".")),
            None
        );
    }

    #[test]
    fn test_elixir_resolver_resolve_module_direct() {
        let resolver = ElixirResolver;
        let mut index = ModuleIndex::empty();

        // Register: lib/my_app/services/user.ex → module "MyApp.Services.User"
        index.insert(
            "MyApp.Services.User",
            "lib/my_app/services/user.ex".to_string(),
        );

        let candidates = resolver.resolve_module(
            "MyApp.Services.User",
            "lib/my_app/services/user.ex",
            Path::new("."),
            &index,
        );
        assert!(
            candidates.contains(&"lib/my_app/services/user.ex".to_string()),
            "Expected lib/my_app/services/user.ex in candidates, got {:?}",
            candidates
        );
    }

    #[test]
    fn test_elixir_resolver_resolve_module_nested() {
        let resolver = ElixirResolver;
        let mut index = ModuleIndex::empty();

        index.insert(
            "MyApp.Services.Helper",
            "lib/my_app/services/helper.ex".to_string(),
        );

        let candidates = resolver.resolve_module(
            "MyApp.Services.Helper",
            "lib/my_app/worker.ex",
            Path::new("."),
            &index,
        );
        assert!(
            candidates.contains(&"lib/my_app/services/helper.ex".to_string()),
            "Expected module to be resolved, got {:?}",
            candidates
        );
    }

    #[test]
    fn test_elixir_resolver_resolve_module_prefix_fallback() {
        let resolver = ElixirResolver;
        let mut index = ModuleIndex::empty();

        // Index has stripped prefix: "Services.Worker" → "src/services/worker.ex"
        index.insert(
            "Services.Worker",
            "src/services/worker.ex".to_string(),
        );

        let candidates = resolver.resolve_module(
            "Services.Worker",
            "lib/my_app.ex",
            Path::new("."),
            &index,
        );
        assert!(
            candidates.contains(&"src/services/worker.ex".to_string()),
            "Expected src/services/worker.ex via prefix fallback, got {:?}",
            candidates
        );
    }

    #[test]
    fn test_elixir_resolver_resolve_module_no_index() {
        let resolver = ElixirResolver;
        let index = ModuleIndex::empty();

        let candidates = resolver.resolve_module(
            "MyApp.Services.User",
            "lib/my_app.ex",
            Path::new("."),
            &index,
        );
        // Should generate candidate paths as fallback
        assert!(!candidates.is_empty(), "Should generate candidate paths");
        // Should include common prefixes
        assert!(
            candidates.iter().any(|c| c.contains("my_app/services/user")),
            "Should contain snake_case path, got {:?}",
            candidates
        );
    }

    #[test]
    fn test_elixir_resolver_resolve_module_empty() {
        let resolver = ElixirResolver;
        let index = ModuleIndex::empty();
        let candidates = resolver.resolve_module("", "lib/test.ex", Path::new("."), &index);
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_camel_to_snake() {
        assert_eq!(camel_to_snake("MyApp"), "my_app");
        assert_eq!(camel_to_snake("User"), "user");
        assert_eq!(camel_to_snake("Services"), "services");
        assert_eq!(camel_to_snake("HTTPClient"), "http_client");
        assert_eq!(camel_to_snake("MyAppServices"), "my_app_services");
        assert_eq!(camel_to_snake(""), "");
    }

    #[test]
    fn test_language_registry_get_elixir() {
        let resolver = LanguageRegistry::get("elixir");
        assert!(resolver.is_some(), "Expected ElixirResolver in LanguageRegistry");
        assert_eq!(resolver.unwrap().language(), "elixir");
    }
}
