//! URL Normalizer — unify heterogeneous framework URL patterns into {param} format.
//!
//! Each web framework has its own URL parameter syntax:
//! - Flask: `<type:name>`
//! - Express/Gin: `:name`
//! - Spring Boot: `{name:regex}`
//! - Django: `<type:name>` or re_path `(?P<name>pattern)`
//! - ASP.NET: `{name:type}`
//!
//! The normalizer converts all of them into the canonical `{name}` form so that
//! cross-tier URL matching can compare URLs without framework-specific noise.

use regex::Regex;
use std::sync::LazyLock;

// ---------------------------------------------------------------------------
// Precompiled regexes (compiled once, reused across calls)
// ---------------------------------------------------------------------------

static FLASK_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<(int|string|uuid|float|path|any):(\w+)>").unwrap());

static EXPRESS_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r":(\w+)").unwrap());

static SPRING_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\{(\w+):[^}]+\}").unwrap());

static DJANGO_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<(\w+):(\w+)>").unwrap());

static DJANGO_RE_PATH: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\(\?P<(\w+)>[^)]+\)").unwrap());

static ASPNET_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\{(\w+):\w+\}").unwrap());

static GIN_WILDCARD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\*(\w+)").unwrap());

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Normalize a framework-specific URL pattern into canonical `{param_name}` form.
///
/// This is the primary public API. It applies framework-specific normalization
/// rules and then strips trailing slashes (except for root "/").
///
/// # Arguments
/// * `url` - The original URL pattern (e.g. `/users/<int:user_id>`)
/// * `framework` - The framework name (e.g. `"flask"`, `"express"`, `"spring"`)
///
/// # Returns
/// A normalized URL string with all parameter placeholders in `{name}` format
/// and trailing slash removed.
///
/// # Examples
/// ```
/// assert_eq!(normalize("/users/<int:user_id>", "flask"), "/users/{user_id}");
/// assert_eq!(normalize("/users/:user_id", "express"), "/users/{user_id}");
/// assert_eq!(normalize("/users/{user_id}", "fastapi"), "/users/{user_id}");
/// ```
pub fn normalize(url: &str, framework: &str) -> String {
    let raw = normalize_url(url, framework);
    normalize_trailing_slash(&raw)
}

/// Apply framework-specific normalization without trailing-slash cleanup.
/// Useful internally and for testing the raw normalization step.
pub fn normalize_url(url: &str, framework: &str) -> String {
    match framework {
        "flask" => {
            // <int:user_id> → {user_id}
            let s = FLASK_RE.replace_all(url, "{${2}}").to_string();
            // Also handle Gin-style wildcards in Flask (rare but possible)
            GIN_WILDCARD_RE.replace_all(&s, "{$1}").to_string()
        }
        "express" | "gin" | "echo" | "rails" => {
            // :user_id → {user_id}  (but do NOT catch *wildcard, handled next)
            let s = EXPRESS_RE.replace_all(url, "{$1}").to_string();
            // *filepath → {filepath}
            GIN_WILDCARD_RE.replace_all(&s, "{$1}").to_string()
        }
        "spring" | "spring_boot" => {
            // {id:[0-9]+} → {id}, but {id} stays {id}
            SPRING_RE.replace_all(url, "{$1}").to_string()
        }
        "django" => {
            // Strip re_path regex anchors: ^url/$ → url/
            let cleaned = url
                .trim_start_matches('^')
                .trim_end_matches('$');
            // <int:pk> → {pk}
            let s = DJANGO_RE.replace_all(cleaned, "{$2}").to_string();
            // Also handle re_path (?P<id>\d+) → {id}
            DJANGO_RE_PATH.replace_all(&s, "{$1}").to_string()
        }
        "django_re_path" => {
            // (?P<id>\d+) → {id}
            DJANGO_RE_PATH.replace_all(url, "{$1}").to_string()
        }
        "aspnet" | "asp_net" | "asp.net" => {
            // {userId:guid} → {userId}
            ASPNET_RE.replace_all(url, "{$1}").to_string()
        }
        "fastapi" | "laravel" => {
            // Already canonical {param} form — no change.
            url.to_string()
        }
        _ => {
            // Unknown framework: try all rules in priority order.
            normalize_unknown(url)
        }
    }
}

/// Try all normalization rules in a safe priority order for an unknown framework.
///
/// Rules are applied from most specific (framework-unique syntax) to most
/// general (e.g. `:param` which could be part of a URL path). This prevents
/// false-positive matches.
pub fn normalize_unknown(url: &str) -> String {
    let mut result = url.to_string();

    // Apply in priority order: most specific → least specific
    // 1. Flask-style <type:name>  — very specific
    result = FLASK_RE.replace_all(&result, "{${2}}").to_string();
    // 2. Django re_path (?P<name>pattern)  — very specific
    result = DJANGO_RE_PATH.replace_all(&result, "{$1}").to_string();
    // 3. Spring {name:regex|type}  — specific to braces
    result = SPRING_RE.replace_all(&result, "{$1}").to_string();
    // 4. Django <type:name>  — less restrictive than Flask but still angle
    result = DJANGO_RE.replace_all(&result, "{$2}").to_string();
    // 5. ASP.NET {name:type}
    result = ASPNET_RE.replace_all(&result, "{$1}").to_string();
    // 6. Gin wildcard *name
    result = GIN_WILDCARD_RE.replace_all(&result, "{$1}").to_string();
    // 7. Express/Gin :param  — most general, apply last
    result = EXPRESS_RE.replace_all(&result, "{$1}").to_string();

    // Apply trailing-slash cleanup
    normalize_trailing_slash(&result)
}

/// Remove a trailing slash from a URL for consistency, unless the URL is just "/".
///
/// # Examples
/// ```
/// assert_eq!(normalize_trailing_slash("/users/"), "/users");
/// assert_eq!(normalize_trailing_slash("/users"), "/users");
/// assert_eq!(normalize_trailing_slash("/"), "/");
/// ```
pub fn normalize_trailing_slash(url: &str) -> String {
    if url.len() > 1 && url.ends_with('/') {
        url[..url.len() - 1].to_string()
    } else {
        url.to_string()
    }
}

/// Extract the path component from a potentially full HTTP URL.
///
/// If the URL starts with `http://` or `https://`, the scheme and authority
/// (host:port) are stripped, leaving only the path (and query string, if any).
/// URLs that are already relative paths are returned unchanged.
///
/// # Examples
/// ```
/// assert_eq!(extract_url_path("http://127.0.0.1:8000/tasks/"), "/tasks/");
/// assert_eq!(extract_url_path("https://api.example.com/v1/users"), "/v1/users");
/// assert_eq!(extract_url_path("/api/tasks"), "/api/tasks");
/// assert_eq!(extract_url_path("http://localhost"), "/");
/// ```
pub fn extract_url_path(url: &str) -> String {
    let path = if let Some(rest) = url.strip_prefix("http://") {
        if let Some(pos) = rest.find('/') {
            rest[pos..].to_string()
        } else {
            "/".to_string()
        }
    } else if let Some(rest) = url.strip_prefix("https://") {
        if let Some(pos) = rest.find('/') {
            rest[pos..].to_string()
        } else {
            "/".to_string()
        }
    } else {
        url.to_string()
    };
    // Strip query string
    if let Some(pos) = path.find('?') {
        path[..pos].to_string()
    } else {
        path
    }
}

/// Heuristically detect a web framework based on file path and source code content.
///
/// First inspects import/dependency patterns in the source code (most reliable),
/// then falls back to file extension heuristics.
///
/// # Returns
/// The detected framework name, or `None` if it cannot be reliably determined.
pub fn detect_framework(file_path: &str, source: &str) -> Option<&'static str> {
    let code_lower = source.to_lowercase();
    let path_lower = file_path.to_lowercase();

    // ── Source-code-based detection (most reliable) ──

    // FastAPI: from fastapi import ...
    if source.contains("from fastapi import")
        || source.contains("from fastapi.")
        || code_lower.contains("fastapi")
    {
        return Some("fastapi");
    }

    // Flask: from flask import ...
    if source.contains("from flask import")
        || source.contains("from flask.")
        || source.contains("import flask")
    {
        return Some("flask");
    }

    // Django: django.urls or urlpatterns
    if source.contains("django.urls") || source.contains("urlpatterns") {
        return Some("django");
    }

    // Spring Boot: @GetMapping, @PostMapping, Spring annotations
    if code_lower.contains("@getmapping")
        || code_lower.contains("@postmapping")
        || code_lower.contains("@requestmapping")
        || code_lower.contains("@putmapping")
        || code_lower.contains("@deletemapping")
        || code_lower.contains("@patchmapping")
        || code_lower.contains("org.springframework")
    {
        return Some("spring_boot");
    }

    // Express: require('express') or express()
    if source.contains("require('express')") || source.contains("express()") {
        return Some("express");
    }

    // Gin: gin.Default(), gin.New()
    if code_lower.contains("gin.default()") || code_lower.contains("gin.new()") {
        return Some("gin");
    }

    // ASP.NET: [Route], [HttpGet], [HttpPost]
    if code_lower.contains("[httpget")
        || code_lower.contains("[httppost")
        || code_lower.contains("[httpput")
        || code_lower.contains("[httpdelete")
        || code_lower.contains("[route(")
    {
        return Some("aspnet");
    }

    // Laravel: Route::get, Route::post
    if code_lower.contains("route::get")
        || code_lower.contains("route::post")
        || code_lower.contains("route::put")
        || code_lower.contains("route::delete")
    {
        return Some("laravel");
    }

    // ── File-extension-based fallback ──

    if path_lower.ends_with(".py") {
        // Python — could be Flask, FastAPI, or Django. Without imports, can't tell.
        None
    } else if path_lower.ends_with(".go") {
        Some("gin") // most common Go web framework
    } else if path_lower.ends_with(".java") || path_lower.ends_with(".kt") {
        Some("spring_boot")
    } else if path_lower.ends_with(".cs") {
        Some("aspnet")
    } else if path_lower.ends_with(".php") {
        Some("laravel")
    } else {
        None
    }
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // Flask — normalize_url (raw normalization, no trailing-slash cleanup)
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_flask_int_param() {
        let result = normalize_url("/users/<int:user_id>", "flask");
        assert_eq!(result, "/users/{user_id}");
    }

    #[test]
    fn test_normalize_flask_string_param() {
        let result = normalize_url("/users/<string:username>", "flask");
        assert_eq!(result, "/users/{username}");
    }

    #[test]
    fn test_normalize_flask_uuid_param() {
        let result = normalize_url("/users/<uuid:id>", "flask");
        assert_eq!(result, "/users/{id}");
    }

    #[test]
    fn test_normalize_flask_path_param() {
        let result = normalize_url("/users/<path:filepath>", "flask");
        assert_eq!(result, "/users/{filepath}");
    }

    #[test]
    fn test_normalize_flask_float_param() {
        let result = normalize_url("/users/<float:price>", "flask");
        assert_eq!(result, "/users/{price}");
    }

    #[test]
    fn test_normalize_flask_any_param() {
        let result = normalize_url("/multi/<any:value>", "flask");
        assert_eq!(result, "/multi/{value}");
    }

    #[test]
    fn test_normalize_flask_multiple_params() {
        let result = normalize_url("/org/<int:org_id>/user/<string:name>", "flask");
        assert_eq!(result, "/org/{org_id}/user/{name}");
    }

    // -----------------------------------------------------------------------
    // Express / Gin / Echo (shared :param syntax) — normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_express_param() {
        let result = normalize_url("/users/:user_id", "express");
        assert_eq!(result, "/users/{user_id}");
    }

    #[test]
    fn test_normalize_gin_param() {
        let result = normalize_url("/users/:user_id", "gin");
        assert_eq!(result, "/users/{user_id}");
    }

    #[test]
    fn test_normalize_echo_param() {
        let result = normalize_url("/users/:user_id", "echo");
        assert_eq!(result, "/users/{user_id}");
    }

    #[test]
    fn test_normalize_express_multiple_params() {
        let result = normalize_url("/org/:org_id/user/:user_id", "express");
        assert_eq!(result, "/org/{org_id}/user/{user_id}");
    }

    #[test]
    fn test_normalize_gin_wildcard() {
        let result = normalize_url("/files/*filepath", "gin");
        assert_eq!(result, "/files/{filepath}");
    }

    #[test]
    fn test_normalize_gin_colon_and_wildcard() {
        let result = normalize_url("/users/:user_id/files/*filepath", "gin");
        assert_eq!(result, "/users/{user_id}/files/{filepath}");
    }

    // -----------------------------------------------------------------------
    // Spring Boot — normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_spring_boot_regex() {
        let result = normalize_url("/users/{id:[0-9]+}", "spring");
        assert_eq!(result, "/users/{id}");
    }

    #[test]
    fn test_normalize_spring_boot_multiple_regex() {
        let result = normalize_url(
            "/org/{orgId:[0-9]+}/user/{userId:[a-z]+}",
            "spring_boot",
        );
        assert_eq!(result, "/org/{orgId}/user/{userId}");
    }

    #[test]
    fn test_normalize_spring_boot_no_change() {
        let result = normalize_url("/users/{user_id}", "spring");
        assert_eq!(result, "/users/{user_id}");
    }

    // -----------------------------------------------------------------------
    // Django — normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_django_int() {
        let result = normalize_url("users/<int:pk>/", "django");
        assert_eq!(result, "users/{pk}/");
    }

    #[test]
    fn test_normalize_django_slug() {
        let result = normalize_url("articles/<slug:title_slug>/", "django");
        assert_eq!(result, "articles/{title_slug}/");
    }

    #[test]
    fn test_normalize_django_re_path() {
        let result = normalize_url("users/(?P<id>\\d+)/", "django_re_path");
        assert_eq!(result, "users/{id}/");
    }

    #[test]
    fn test_normalize_django_handles_both_styles() {
        // "django" framework should handle re_path patterns too
        let result = normalize_url("items/(?P<cat>\\w+)/(?P<id>\\d+)/", "django");
        assert_eq!(result, "items/{cat}/{id}/");
    }

    // -----------------------------------------------------------------------
    // ASP.NET — normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_aspnet_guid() {
        let result = normalize_url("/users/{userId:guid}", "aspnet");
        assert_eq!(result, "/users/{userId}");
    }

    #[test]
    fn test_normalize_aspnet_int_constraint() {
        let result = normalize_url("/api/{id:int}", "aspnet");
        assert_eq!(result, "/api/{id}");
    }

    #[test]
    fn test_normalize_aspnet_constraint() {
        let result = normalize_url("/api/{id:int}", "asp_net");
        assert_eq!(result, "/api/{id}");
    }

    // -----------------------------------------------------------------------
    // FastAPI / Laravel (already canonical — no change) — normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_no_change_fastapi() {
        let result = normalize_url("/users/{user_id}", "fastapi");
        assert_eq!(result, "/users/{user_id}");
    }

    #[test]
    fn test_normalize_no_change_laravel() {
        let result = normalize_url("/users/{userId}", "laravel");
        assert_eq!(result, "/users/{userId}");
    }

    // -----------------------------------------------------------------------
    // normalize() — primary public API with trailing-slash cleanup
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_flask_int_param_with_trailing_slash() {
        // normalize() strips trailing slash
        assert_eq!(
            normalize("/users/<int:user_id>/", "flask"),
            "/users/{user_id}"
        );
    }

    #[test]
    fn test_normalize_flask_string_param_public() {
        assert_eq!(
            normalize("/users/<string:username>", "flask"),
            "/users/{username}"
        );
    }

    #[test]
    fn test_normalize_flask_uuid_param_public() {
        assert_eq!(normalize("/items/<uuid:id>", "flask"), "/items/{id}");
    }

    #[test]
    fn test_normalize_flask_path_param_public() {
        assert_eq!(
            normalize("/files/<path:filepath>", "flask"),
            "/files/{filepath}"
        );
    }

    #[test]
    fn test_normalize_express_param_public() {
        assert_eq!(
            normalize("/users/:user_id", "express"),
            "/users/{user_id}"
        );
    }

    #[test]
    fn test_normalize_spring_boot_regex_public() {
        assert_eq!(
            normalize("/users/{id:[0-9]+}", "spring_boot"),
            "/users/{id}"
        );
    }

    #[test]
    fn test_normalize_django_int_public() {
        assert_eq!(normalize("users/<int:pk>/", "django"), "users/{pk}");
    }

    #[test]
    fn test_normalize_django_re_path_public() {
        assert_eq!(
            normalize("users/(?P<id>\\d+)/", "django"),
            "users/{id}"
        );
    }

    #[test]
    fn test_normalize_aspnet_guid_public() {
        assert_eq!(
            normalize("/users/{userId:guid}", "aspnet"),
            "/users/{userId}"
        );
    }

    #[test]
    fn test_normalize_gin_wildcard_public() {
        assert_eq!(normalize("/files/*filepath", "gin"), "/files/{filepath}");
    }

    #[test]
    fn test_normalize_trailing_slash_public() {
        // normalize() applies trailing slash cleanup
        assert_eq!(
            normalize("/users/{user_id}/", "fastapi"),
            "/users/{user_id}"
        );
    }

    #[test]
    fn test_normalize_no_change_fastapi_public() {
        assert_eq!(
            normalize("/users/{user_id}", "fastapi"),
            "/users/{user_id}"
        );
    }

    #[test]
    fn test_normalize_unknown_framework_public() {
        assert_eq!(
            normalize("/users/:user_id", "unknown_framework"),
            "/users/{user_id}"
        );
    }

    // -----------------------------------------------------------------------
    // Trailing slash consistency — normalize_trailing_slash
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_trailing_slash_removes() {
        let result = normalize_trailing_slash("/users/{id}/");
        assert_eq!(result, "/users/{id}");
    }

    #[test]
    fn test_normalize_trailing_slash_no_effect() {
        let result = normalize_trailing_slash("/users/{id}");
        assert_eq!(result, "/users/{id}");
    }

    #[test]
    fn test_normalize_trailing_slash_root() {
        // "/" stays "/"
        let result = normalize_trailing_slash("/");
        assert_eq!(result, "/");
    }

    #[test]
    fn test_normalize_trailing_slash_flask() {
        assert_eq!(
            normalize("/users/<int:id>/", "flask"),
            "/users/{id}"
        );
    }

    #[test]
    fn test_normalize_root_preserved() {
        assert_eq!(normalize("/", "fastapi"), "/");
    }

    // -----------------------------------------------------------------------
    // Unknown framework — normalize_unknown
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_unknown_flask_style() {
        assert_eq!(
            normalize_unknown("/users/<int:user_id>"),
            "/users/{user_id}"
        );
    }

    #[test]
    fn test_normalize_unknown_express_style() {
        assert_eq!(normalize_unknown("/users/:user_id"), "/users/{user_id}");
    }

    #[test]
    fn test_normalize_unknown_django_re_path_style() {
        assert_eq!(
            normalize_unknown("users/(?P<id>\\d+)/"),
            "users/{id}"
        );
    }

    #[test]
    fn test_normalize_unknown_spring_boot_style() {
        assert_eq!(
            normalize_unknown("/users/{id:[0-9]+}"),
            "/users/{id}"
        );
    }

    #[test]
    fn test_normalize_unknown_aspnet_style() {
        assert_eq!(
            normalize_unknown("/users/{userId:guid}"),
            "/users/{userId}"
        );
    }

    #[test]
    fn test_normalize_unknown_gin_wildcard_style() {
        assert_eq!(
            normalize_unknown("/files/*filepath"),
            "/files/{filepath}"
        );
    }

    #[test]
    fn test_normalize_unknown_no_match() {
        // Already canonical — trailing slash removed
        assert_eq!(
            normalize_unknown("/users/{id}/items/{item_id}/"),
            "/users/{id}/items/{item_id}"
        );
    }

    #[test]
    fn test_normalize_unknown_plain_url() {
        assert_eq!(normalize_unknown("/api/health"), "/api/health");
    }

    // -----------------------------------------------------------------------
    // Unknown framework fallback in normalize_url
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_unknown_framework_flask_style_via_normalize_url() {
        let result = normalize_url("/data/<int:record_id>", "some_unknown_fw");
        assert_eq!(result, "/data/{record_id}");
    }

    #[test]
    fn test_normalize_unknown_framework_express_style_via_normalize_url() {
        let result = normalize_url("/data/:record_id", "some_unknown_fw");
        assert_eq!(result, "/data/{record_id}");
    }

    #[test]
    fn test_normalize_unknown_framework_no_params_via_normalize_url() {
        let result = normalize_url("/health", "some_unknown_fw");
        assert_eq!(result, "/health");
    }

    // -----------------------------------------------------------------------
    // Framework detection (source-code-based)
    // -----------------------------------------------------------------------

    #[test]
    fn test_detect_framework_fastapi() {
        let code = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')";
        assert_eq!(detect_framework("main.py", code), Some("fastapi"));
    }

    #[test]
    fn test_detect_framework_flask() {
        let code = "from flask import Flask\napp = Flask(__name__)\n@app.route('/')";
        assert_eq!(detect_framework("app.py", code), Some("flask"));
    }

    #[test]
    fn test_detect_framework_django() {
        let code = "from django.urls import path\nurlpatterns = [\n    path('', views.index),\n]";
        assert_eq!(detect_framework("urls.py", code), Some("django"));
    }

    #[test]
    fn test_detect_framework_spring_boot() {
        let code = "@GetMapping(\"/users\")\npublic List<User> getUsers() {";
        assert_eq!(
            detect_framework("UserController.java", code),
            Some("spring_boot")
        );
    }

    #[test]
    fn test_detect_framework_express() {
        let code = "const express = require('express');\nconst app = express();";
        assert_eq!(detect_framework("server.js", code), Some("express"));
    }

    #[test]
    fn test_detect_framework_gin() {
        let code = "r := gin.Default()\nr.GET(\"/ping\", func(c *gin.Context) {";
        assert_eq!(detect_framework("main.go", code), Some("gin"));
    }

    #[test]
    fn test_detect_framework_aspnet() {
        let code = "[HttpGet]\npublic IActionResult GetUsers() {";
        assert_eq!(
            detect_framework("UsersController.cs", code),
            Some("aspnet")
        );
    }

    #[test]
    fn test_detect_framework_laravel() {
        let code = "Route::get('/users', [UserController::class, 'index']);";
        assert_eq!(detect_framework("web.php", code), Some("laravel"));
    }

    #[test]
    fn test_detect_framework_unknown() {
        assert_eq!(detect_framework("script.rb", ""), None);
    }

    #[test]
    fn test_detect_framework_python_no_imports() {
        // Python file without recognisable framework imports — fallback returns None
        let code = "print('hello world')";
        assert_eq!(detect_framework("script.py", code), None);
    }

    // -----------------------------------------------------------------------
    // Framework detection (file-extension-based fallback)
    // -----------------------------------------------------------------------

    #[test]
    fn test_detect_framework_go_fallback() {
        // Go file without gin imports — still guessed as gin
        let code = "package main\nfunc main() {}";
        assert_eq!(detect_framework("main.go", code), Some("gin"));
    }

    #[test]
    fn test_detect_framework_java_fallback() {
        let code = "public class Foo {}";
        assert_eq!(detect_framework("Foo.java", code), Some("spring_boot"));
    }

    #[test]
    fn test_detect_framework_cs_fallback() {
        let code = "public class Foo {}";
        assert_eq!(detect_framework("Foo.cs", code), Some("aspnet"));
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_normalize_empty_url() {
        assert_eq!(normalize("", "fastapi"), "");
    }

    #[test]
    fn test_normalize_root_only() {
        assert_eq!(normalize("/", "flask"), "/");
    }

    #[test]
    fn test_normalize_flask_multiple_converters() {
        assert_eq!(
            normalize("/org/<int:org_id>/user/<uuid:user_id>", "flask"),
            "/org/{org_id}/user/{user_id}"
        );
    }

    #[test]
    fn test_normalize_django_re_path_multiple() {
        assert_eq!(
            normalize("items/(?P<cat>\\w+)/(?P<id>\\d+)/", "django"),
            "items/{cat}/{id}"
        );
    }

    // -----------------------------------------------------------------------
    // extract_url_path
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_url_path_http_full() {
        assert_eq!(
            extract_url_path("http://127.0.0.1:8000/tasks/"),
            "/tasks/"
        );
    }

    #[test]
    fn test_extract_url_path_https_full() {
        assert_eq!(
            extract_url_path("https://api.example.com/v1/users"),
            "/v1/users"
        );
    }

    #[test]
    fn test_extract_url_path_already_relative() {
        assert_eq!(extract_url_path("/api/tasks"), "/api/tasks");
    }

    #[test]
    fn test_extract_url_path_root_only() {
        assert_eq!(extract_url_path("/"), "/");
    }

    #[test]
    fn test_extract_url_path_no_path() {
        assert_eq!(extract_url_path("http://localhost"), "/");
    }

    #[test]
    fn test_extract_url_path_https_no_path() {
        assert_eq!(extract_url_path("https://example.com"), "/");
    }

    #[test]
    fn test_extract_url_path_strips_query_string() {
        assert_eq!(
            extract_url_path("http://127.0.0.1:8000/api/tasks?page=1&limit=10"),
            "/api/tasks"
        );
    }

    #[test]
    fn test_extract_url_path_https_with_port() {
        assert_eq!(
            extract_url_path("https://api.example.com:443/v2/users/123/profile"),
            "/v2/users/123/profile"
        );
    }
}
